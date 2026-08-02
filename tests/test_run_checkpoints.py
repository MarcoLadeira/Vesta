"""Recoverable run checkpoints for edit-capable runs (#75)."""

from __future__ import annotations

import json
import multiprocessing
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opaihub.checkpoints import (
    COMPLETION_STATES,
    create_run_checkpoint,
    finalize_run_checkpoint,
    list_run_checkpoints,
    load_run_checkpoint,
    recover_interrupted_checkpoints,
)

from tests._helpers import FakeAccountRunner, make_repo


def _finalize_checkpoint_in_child(
    project_root: str,
    checkpoint_id: str,
    completion_state: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    """Race worker kept at module scope so Windows ``spawn`` can import it."""
    start.wait(timeout=15)
    try:
        checkpoint = finalize_run_checkpoint(
            Path(project_root),
            checkpoint_id,
            completion_state=completion_state,
            outcome=f"finalized as {completion_state}",
        )
        results.put(("ok", checkpoint.completion_state))
    except Exception as exc:  # pragma: no cover - asserted in parent process
        results.put(("error", repr(exc)))


def _recover_stale_checkpoint_in_child(
    project_root: str,
    checkpoint_id: str,
    snapshot_taken: multiprocessing.synchronize.Event,
    continue_recovery: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    """Model recovery after it has already observed a pending checkpoint."""
    try:
        stale = load_run_checkpoint(Path(project_root), checkpoint_id)
        if stale.completion_state != "pending":
            results.put(("error", "recovery worker did not observe pending state"))
            return
        snapshot_taken.set()
        continue_recovery.wait(timeout=15)
        checkpoint = finalize_run_checkpoint(
            Path(project_root),
            checkpoint_id,
            completion_state="interrupted",
            outcome="Run interrupted before finalizing; review before reuse",
            changed_files=stale.baseline_changed_files,
            recovery_actions=("inspect the working tree against the recorded git head",),
        )
        results.put(("ok", checkpoint.completion_state))
    except Exception as exc:  # pragma: no cover - asserted in parent process
        results.put(("error", repr(exc)))


def _replay_checkpoint_creation_in_child(
    project_root: str,
    checkpoint_id: str,
    results: multiprocessing.queues.Queue,
) -> None:
    """Replay a same-id create as a separate process after a prior attempt."""
    try:
        checkpoint = create_run_checkpoint(
            Path(project_root),
            task="replayed start request",
            task_id="task-race",
            edit_capable=True,
            mode="implement",
            model="hermetic",
            checkpoint_id=checkpoint_id,
            read_budget=False,
        )
        results.put(("ok", checkpoint.completion_state, checkpoint.outcome))
    except Exception as exc:  # pragma: no cover - asserted in parent process
        results.put(("error", repr(exc)))


class CheckpointContractTests(unittest.TestCase):
    def test_checkpoint_records_git_mode_model_policy_and_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            (root / "dirty.py").write_text("x = 1\n", encoding="utf-8")
            checkpoint = create_run_checkpoint(
                root,
                task="fix the bug",
                task_id="task-1",
                edit_capable=True,
                mode="implement",
                model="account:claude:sonnet",
                policy={"mode": "implement", "capabilities": ["edit_files"]},
            )
            self.assertTrue(checkpoint.checkpoint_id)
            self.assertTrue(checkpoint.edit_capable)
            self.assertTrue(checkpoint.git["is_repo"])
            self.assertTrue(checkpoint.git["head"])
            self.assertIn("dirty.py", checkpoint.baseline_changed_files)
            self.assertEqual(checkpoint.policy["mode"], "implement")
            self.assertIn("panic", checkpoint.budget)
            self.assertEqual(checkpoint.completion_state, "pending")

    def test_checkpoint_never_stores_prompt_text_or_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            secret = "sk-abcdefghijklmnopqrstuv"
            checkpoint = create_run_checkpoint(
                root,
                task=f"deploy with token={secret}",
                task_id="task-1",
                edit_capable=True,
                mode="implement",
                model="m",
            )
            raw = (
                root
                / ".opaihub"
                / "agent"
                / "checkpoints"
                / f"{checkpoint.checkpoint_id}.json"
            ).read_text(encoding="utf-8")
            self.assertNotIn(secret, raw)
            self.assertNotIn("deploy with token", raw)
            self.assertEqual(
                checkpoint.task_hash,
                load_run_checkpoint(root, checkpoint.checkpoint_id).task_hash,
            )

    def test_no_git_project_is_represented_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)  # not a git repo
            checkpoint = create_run_checkpoint(
                root,
                task="task",
                task_id="task-1",
                edit_capable=False,
                mode="explain",
                model="auto",
                read_budget=False,
            )
            self.assertFalse(checkpoint.git["is_repo"])
            self.assertEqual(checkpoint.baseline_changed_files, ())

    def test_finalize_separates_files_changed_before_versus_during(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            (root / "pre_existing.py").write_text("x = 1\n", encoding="utf-8")
            checkpoint = create_run_checkpoint(
                root,
                task="task",
                task_id="task-1",
                edit_capable=True,
                mode="implement",
                model="m",
            )
            finalized = finalize_run_checkpoint(
                root,
                checkpoint.checkpoint_id,
                completion_state="answered",
                outcome="answered",
                changed_files=["pre_existing.py", "new_file.py"],
                diff_summary={"files": 2, "additions": 10},
            )
            self.assertEqual(finalized.completion_state, "answered")
            self.assertIn("pre_existing.py", finalized.baseline_changed_files)
            # Only the file created during the run is "changed during".
            self.assertEqual(finalized.changed_during_run, ("new_file.py",))
            self.assertEqual(finalized.diff_summary["files"], 2)

    def test_finalize_rejects_unknown_completion_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            checkpoint = create_run_checkpoint(
                root, task="t", task_id="t", edit_capable=False, mode="ask", model="m"
            )
            with self.assertRaises(ValueError):
                finalize_run_checkpoint(
                    root, checkpoint.checkpoint_id, completion_state="whatever"
                )

    def test_interrupted_pending_checkpoints_are_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            create_run_checkpoint(
                root,
                task="t",
                task_id="t1",
                edit_capable=True,
                mode="implement",
                model="m",
            )
            recovered = recover_interrupted_checkpoints(root)
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0].completion_state, "interrupted")
            self.assertTrue(recovered[0].recovery_actions)
            # A second sweep is idempotent (nothing pending remains).
            self.assertEqual(recover_interrupted_checkpoints(root), [])

    def test_stale_recovery_cannot_overwrite_a_checkpoint_that_just_finished(self):
        """Recovery's stale pending snapshot must lose to a real finalization."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            pending = create_run_checkpoint(
                root,
                task="finish safely",
                task_id="task-race",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                read_budget=False,
            )
            finalized = finalize_run_checkpoint(
                root,
                pending.checkpoint_id,
                completion_state="answered",
                outcome="completed normally",
                changed_files=["finished.py"],
            )
            path = root / ".opaihub" / "agent" / "checkpoints" / (
                f"{pending.checkpoint_id}.json"
            )
            before_recovery = path.read_bytes()

            # This models a recovery process which enumerated the checkpoint
            # while it was pending, then raced a normal finalizer to commit.
            with mock.patch(
                "opaihub.checkpoints.list_run_checkpoints", return_value=[pending]
            ):
                self.assertEqual(recover_interrupted_checkpoints(root), [])

            self.assertEqual(load_run_checkpoint(root, pending.checkpoint_id), finalized)
            self.assertEqual(path.read_bytes(), before_recovery)

    def test_recovery_process_cannot_overwrite_a_checkpoint_finalized_mid_recovery(self):
        """The recovery race is protected even when the contenders are processes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = create_run_checkpoint(
                root,
                task="finish safely",
                task_id="task-race",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="recovery-race",
                read_budget=False,
            )
            context = multiprocessing.get_context("spawn")
            snapshot_taken = context.Event()
            continue_recovery = context.Event()
            results = context.Queue()
            recovery = context.Process(
                target=_recover_stale_checkpoint_in_child,
                args=(
                    str(root),
                    pending.checkpoint_id,
                    snapshot_taken,
                    continue_recovery,
                    results,
                ),
            )
            recovery.start()
            self.assertTrue(snapshot_taken.wait(timeout=15))
            finalized = finalize_run_checkpoint(
                root,
                pending.checkpoint_id,
                completion_state="answered",
                outcome="completed normally",
                changed_files=["finished.py"],
            )
            continue_recovery.set()
            recovery.join(timeout=20)

            self.assertFalse(recovery.is_alive(), "recovery process timed out")
            self.assertEqual(recovery.exitcode, 0)
            self.assertEqual(results.get(timeout=5), ("ok", "answered"))
            self.assertEqual(load_run_checkpoint(root, pending.checkpoint_id), finalized)

    def test_concurrent_finalizers_leave_one_readable_terminal_checkpoint(self):
        """Unique temporary files and a per-checkpoint lock survive real processes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = create_run_checkpoint(
                root,
                task="finish once",
                task_id="task-race",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="finalize-race",
                read_budget=False,
            )
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            results = context.Queue()
            states = ("answered", "partial", "failed", "interrupted")
            workers = [
                context.Process(
                    target=_finalize_checkpoint_in_child,
                    args=(str(root), pending.checkpoint_id, state, start, results),
                )
                for state in states
            ]
            for worker in workers:
                worker.start()
            start.set()
            for worker in workers:
                worker.join(timeout=20)
                self.assertFalse(worker.is_alive(), "checkpoint finalizer timed out")
                self.assertEqual(worker.exitcode, 0)

            outcomes = [results.get(timeout=5) for _ in workers]
            self.assertTrue(all(outcome[0] == "ok" for outcome in outcomes), outcomes)
            persisted = load_run_checkpoint(root, pending.checkpoint_id)
            self.assertIn(persisted.completion_state, states)
            self.assertEqual(
                {outcome[1] for outcome in outcomes}, {persisted.completion_state}
            )
            self.assertEqual(
                list(
                    (root / ".opaihub" / "agent" / "checkpoints").glob(
                        f".{pending.checkpoint_id}.json.*.tmp"
                    )
                ),
                [],
            )

    def test_replayed_creator_cannot_reset_a_terminal_checkpoint(self):
        """A duplicate start request cannot turn terminal evidence back to pending."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = create_run_checkpoint(
                root,
                task="finish once",
                task_id="task-race",
                edit_capable=True,
                mode="implement",
                model="hermetic",
                checkpoint_id="create-race",
                read_budget=False,
            )
            finalized = finalize_run_checkpoint(
                root,
                checkpoint.checkpoint_id,
                completion_state="answered",
                outcome="completed normally",
                changed_files=["finished.py"],
            )
            context = multiprocessing.get_context("spawn")
            results = context.Queue()
            replay = context.Process(
                target=_replay_checkpoint_creation_in_child,
                args=(str(root), checkpoint.checkpoint_id, results),
            )
            replay.start()
            replay.join(timeout=20)

            self.assertFalse(replay.is_alive(), "duplicate creator timed out")
            self.assertEqual(replay.exitcode, 0)
            self.assertEqual(results.get(timeout=5), ("ok", "answered", "completed normally"))
            self.assertEqual(load_run_checkpoint(root, checkpoint.checkpoint_id), finalized)

    def test_invalid_checkpoint_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                create_run_checkpoint(
                    root,
                    task="t",
                    task_id="t",
                    edit_capable=False,
                    mode="ask",
                    model="m",
                    checkpoint_id="../escape",
                    read_budget=False,
                )

    def test_git_runner_is_injectable_for_hermetic_tests(self):
        calls: list[list[str]] = []

        def fake_git(argv, **kwargs):
            calls.append(argv)
            mapping = {
                "rev-parse": "deadbeef",
                "branch": "main",
                "remote": "https://example.test/repo.git",
                "status": "",
            }
            key = argv[1]
            return subprocess.CompletedProcess(argv, 0, mapping.get(key, ""), "")

        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = create_run_checkpoint(
                Path(tmp),
                task="t",
                task_id="t",
                edit_capable=True,
                mode="implement",
                model="m",
                git_runner=fake_git,
                read_budget=False,
            )
        self.assertTrue(checkpoint.git["is_repo"])
        self.assertEqual(checkpoint.git["branch"], "main")
        self.assertTrue(calls)

    def test_completion_states_are_the_documented_set(self):
        self.assertIn("read_only", COMPLETION_STATES)
        self.assertIn("cancelled_before_edit", COMPLETION_STATES)
        self.assertIn("interrupted", COMPLETION_STATES)


class PipelineCheckpointTests(unittest.TestCase):
    """The cross-surface guarantee: no edit-capable route bypasses a checkpoint."""

    def test_edit_capable_run_always_has_a_checkpoint_before_execution(self):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "fix issue #7 and make a PR",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(text="done", cost=0.01),
            )
            self.assertTrue(result["checkpoint_id"])
            self.assertTrue(result["checkpoint"]["edit_capable"])
            checkpoint = load_run_checkpoint(root, result["checkpoint_id"])
            self.assertEqual(checkpoint.mode, "implement")
            self.assertEqual(checkpoint.completion_state, "partial")
            self.assertEqual(checkpoint.completion_verdict["verdict"], "partial")
            # The checkpoint was created before the run and finalized after.
            self.assertTrue(checkpoint.created_at)
            self.assertTrue(checkpoint.finalized_at)

    def test_read_only_run_is_checkpointed_honestly_as_non_edit(self):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "explain how routing works",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(text="here", cost=0.01),
            )
            checkpoint = load_run_checkpoint(root, result["checkpoint_id"])
            self.assertFalse(checkpoint.edit_capable)
            self.assertEqual(checkpoint.completion_state, "read_only")

    def test_cancelled_before_edit_is_recorded_honestly(self):
        from opaihub.gui_pipeline import handle_gui_message

        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "fix the bug and commit",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(text="done", cost=0.01),
                cancel=cancel,
            )
            self.assertEqual(result["status"], "cancelled")
            checkpoint = load_run_checkpoint(root, result["checkpoint_id"])
            self.assertEqual(checkpoint.completion_state, "cancelled_before_edit")

    def test_every_pipeline_run_creates_exactly_one_checkpoint(self):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            handle_gui_message(
                root,
                "explain the code",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(text="ok", cost=0.01),
            )
            self.assertEqual(len(list_run_checkpoints(root)), 1)

    def test_checkpoint_json_from_a_real_run_has_no_prompt_or_secret(self):
        from opaihub.gui_pipeline import handle_gui_message

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
            result = handle_gui_message(
                root,
                f"rotate {secret} and fix the bug",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(text="done", cost=0.01),
            )
            path = (
                root
                / ".opaihub"
                / "agent"
                / "checkpoints"
                / f"{result['checkpoint_id']}.json"
            )
            raw = path.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertNotIn("rotate", raw)
        parsed = json.loads(raw)
        self.assertTrue(parsed["task_hash"])


if __name__ == "__main__":
    unittest.main()
