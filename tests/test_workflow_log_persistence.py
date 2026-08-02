"""Race-safe per-run workflow evidence persistence (#445)."""

from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opaihub import run_journal, workflow_runner
from opaihub.run_state import TERMINAL_STATES, RunState, exit_code_for
from opaihub.workflow_runner import read_workflow_log, run_workflow


class _CompletedCommand:
    def __init__(self, output: str) -> None:
        self.returncode = 0
        self.combined_output = output


class _TimedOutCommand:
    returncode = 124
    combined_output = "command exceeded its deadline"
    timed_out = True


class _FailedCommand:
    returncode = 2
    combined_output = "command failed"
    timed_out = False


def _run_competing_workflow(
    project_root: str,
    run_id: str,
    old_started: object,
    old_release: object,
    outcomes: object,
) -> None:
    """Run a synthetic workflow; the older run pauses after reserving its revision."""

    try:
        workflow_runner.workflow_plan = lambda _root, _workflow_id: {
            "ok": True,
            "workflow": {"id": "synthetic"},
            "steps": [{"step": "synthetic", "safe_command": ["synthetic"]}],
        }

        def fake_command(_command: list[str], _root: Path, *, timeout: int):
            del timeout
            if run_id == "older-run":
                old_started.set()
                if not old_release.wait(timeout=20):
                    raise TimeoutError("timed out waiting to finish the older workflow")
            return _CompletedCommand(f"output from {run_id}")

        workflow_runner.run_policy_command = fake_command
        result = run_workflow(
            Path(project_root), "synthetic", execute=True, run_id=run_id
        )
        outcomes.put({"ok": True, "run_id": run_id, "result": result})
    except BaseException as exc:
        outcomes.put({"ok": False, "run_id": run_id, "error": repr(exc)})


def _synthetic_plan() -> dict[str, object]:
    return {
        "ok": True,
        "workflow": {"id": "synthetic"},
        "steps": [{"step": "synthetic", "safe_command": ["synthetic"]}],
    }


class WorkflowLogPersistenceTests(unittest.TestCase):
    def _run_synthetic(
        self, root: Path, run_id: str, **kwargs: object
    ) -> dict[str, object]:
        with (
            mock.patch.object(
                workflow_runner, "workflow_plan", return_value=_synthetic_plan()
            ),
            mock.patch.object(
                workflow_runner,
                "run_policy_command",
                return_value=_CompletedCommand(f"output from {run_id}"),
            ),
        ):
            return run_workflow(
                root, "synthetic", execute=True, run_id=run_id, **kwargs
            )

    def test_slower_older_run_cannot_replace_newer_current_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = multiprocessing.get_context("spawn")
            old_started = context.Event()
            old_release = context.Event()
            outcomes = context.Queue()
            older = context.Process(
                target=_run_competing_workflow,
                args=(str(root), "older-run", old_started, old_release, outcomes),
            )
            newer = context.Process(
                target=_run_competing_workflow,
                args=(str(root), "newer-run", old_started, old_release, outcomes),
            )
            try:
                older.start()
                self.assertTrue(old_started.wait(timeout=30))
                newer.start()
                newer.join(timeout=30)
                self.assertFalse(newer.is_alive())
                newer_outcome = outcomes.get(timeout=30)
                old_release.set()
                older.join(timeout=30)
                self.assertFalse(older.is_alive())
                older_outcome = outcomes.get(timeout=30)
            finally:
                old_release.set()
                for process in (older, newer):
                    process.join(timeout=30)

            self.assertTrue(newer_outcome["ok"], newer_outcome)
            self.assertTrue(older_outcome["ok"], older_outcome)
            self.assertEqual(newer_outcome["result"]["evidence"]["state"], "current")
            self.assertEqual(older_outcome["result"]["evidence"]["state"], "previous")
            current = read_workflow_log(root)
            older_evidence = read_workflow_log(root, "older-run")
            self.assertEqual(current["state"], "current")
            self.assertEqual(current["run"]["run_id"], "newer-run")
            self.assertEqual(
                current["run"]["results"][0]["output_tail"], "output from newer-run"
            )
            self.assertEqual(older_evidence["state"], "previous")
            self.assertEqual(older_evidence["run"]["run_id"], "older-run")

    def test_reader_observes_only_complete_snapshots_while_a_run_log_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run_synthetic(root, "reader-run")
            initial = read_workflow_log(root)["run"]
            done = threading.Event()

            def write_snapshots() -> None:
                try:
                    for cycle in range(30):
                        snapshot = {
                            **initial,
                            "updated_at": f"2026-01-01T00:00:{cycle:02d}+00:00",
                            "results": [
                                {
                                    **initial["results"][0],
                                    "output_tail": f"cycle-{cycle}:" + "x" * 40_000,
                                }
                            ],
                        }
                        with workflow_runner.interprocess_transaction(
                            workflow_runner.workflow_last_path(root)
                        ):
                            workflow_runner._write_workflow_snapshot(root, snapshot)
                finally:
                    done.set()

            writer = threading.Thread(target=write_snapshots)
            writer.start()
            observed: list[dict[str, object]] = []
            try:
                while not done.is_set():
                    observed.append(read_workflow_log(root))
            finally:
                writer.join(timeout=30)
            self.assertFalse(writer.is_alive())
            self.assertTrue(observed)
            unexpected = [
                {"state": item["state"], "reason": item.get("reason")}
                for item in observed
                if item["state"] != "current"
            ]
            self.assertTrue(not unexpected, unexpected)
            self.assertTrue(
                all(evidence["run"]["run_id"] == "reader-run" for evidence in observed)
            )
            self.assertTrue(
                all(
                    evidence["run"]["results"][0]["output_tail"]
                    for evidence in observed
                )
            )

    def test_interrupted_final_snapshot_never_becomes_completed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run_synthetic(root, "stable-run")
            real_write = workflow_runner._write_workflow_snapshot

            def interrupt_final_snapshot(
                project_root: Path, snapshot: dict[str, object]
            ) -> None:
                if snapshot["run_id"] == "interrupted-run" and snapshot.get(
                    "run_state"
                ) in {state.value for state in TERMINAL_STATES}:
                    raise OSError("simulated interrupted workflow snapshot")
                real_write(project_root, snapshot)

            with mock.patch.object(
                workflow_runner,
                "_write_workflow_snapshot",
                side_effect=interrupt_final_snapshot,
            ):
                with self.assertRaisesRegex(OSError, "interrupted workflow snapshot"):
                    self._run_synthetic(root, "interrupted-run")

            evidence = read_workflow_log(root)
            self.assertEqual(evidence["state"], "current")
            self.assertEqual(evidence["run"]["run_id"], "interrupted-run")
            self.assertEqual(evidence["run"]["run_state"], "verifying")
            self.assertEqual(evidence["run"]["results"][0]["status"], "ok")

    def test_corrupt_current_pointer_is_reported_as_degraded_not_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run_synthetic(root, "known-run")
            workflow_runner.workflow_last_path(root).write_text(
                "{ interrupted pointer", encoding="utf-8"
            )

            evidence = read_workflow_log(root)

            self.assertEqual(evidence["state"], "degraded")
            self.assertIn("pointer", evidence["reason"])
            self.assertNotIn("run", evidence)

    def test_executed_workflow_persists_canonical_task_run_and_step_facts(self):
        """The durable workflow record is a #379 run, not a parallel status."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run_synthetic(root, "canonical-run")
            snapshot = read_workflow_log(root)["run"]

        self.assertEqual(result["run_state"], RunState.COMPLETED.value)
        self.assertEqual(result["reason_code"], "workflow_completed")
        self.assertEqual(
            result["completion_verdict"],
            {"verdict": "completed", "reason_code": "workflow_completed"},
        )
        self.assertEqual(result["steps"][0]["run_state"], RunState.COMPLETED.value)
        self.assertEqual(result["plan_steps"][0]["safe_command"], ["synthetic"])
        self.assertTrue(snapshot["task_id"])
        self.assertEqual(snapshot["run_state"], RunState.COMPLETED.value)
        self.assertEqual(snapshot["reason_code"], "workflow_completed")
        self.assertEqual(
            [entry["state"] for entry in snapshot["state_history"]],
            ["queued", "preparing", "running", "verifying", "completed"],
        )
        self.assertEqual(snapshot["steps"][0]["run_state"], RunState.COMPLETED.value)
        self.assertEqual(
            [entry["state"] for entry in snapshot["steps"][0]["state_history"]],
            ["queued", "preparing", "running", "completed"],
        )
        self.assertEqual(snapshot["results"][0]["run_state"], RunState.COMPLETED.value)

    def test_unmapped_step_makes_an_executed_workflow_partial_not_completed(self):
        """A declared step that did not run is evidence of incomplete work."""

        plan = {
            "ok": True,
            "workflow": {"id": "mixed"},
            "steps": [
                {"step": "mapped", "safe_command": ["mapped"]},
                {"step": "unmapped", "safe_command": None},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(workflow_runner, "workflow_plan", return_value=plan),
                mock.patch.object(
                    workflow_runner,
                    "run_policy_command",
                    return_value=_CompletedCommand("mapped output"),
                ),
            ):
                result = run_workflow(root, "mixed", execute=True, run_id="mixed-run")
            snapshot = read_workflow_log(root)["run"]

        self.assertEqual(result["run_state"], RunState.PARTIAL.value)
        self.assertEqual(result["reason_code"], "workflow_steps_unmapped")
        self.assertEqual(
            result["completion_verdict"],
            {"verdict": "partial", "reason_code": "workflow_steps_unmapped"},
        )
        self.assertEqual(snapshot["steps"][0]["run_state"], RunState.COMPLETED.value)
        self.assertEqual(snapshot["steps"][1]["run_state"], RunState.BLOCKED.value)
        self.assertEqual(snapshot["steps"][1]["reason_code"], "no_safe_command_mapping")

    def test_read_migrates_legacy_workflow_snapshots_to_canonical_facts(self):
        """Existing #445 evidence remains readable without inventing success."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = {
                "schema_version": 1,
                "run_id": "legacy-run",
                "revision": 1,
                "workflow_id": "legacy-workflow",
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:01+00:00",
                "state": "failed",
                "results": [
                    {
                        "step": "legacy-step",
                        "status": "failed",
                        "returncode": 2,
                    }
                ],
            }
            with workflow_runner.interprocess_transaction(
                workflow_runner.workflow_last_path(root)
            ):
                workflow_runner._write_workflow_snapshot(root, snapshot)
                workflow_runner._write_workflow_pointer(root, snapshot)
            restored = read_workflow_log(root)["run"]

        self.assertEqual(restored["schema_version"], 2)
        self.assertEqual(restored["migrated_from_schema_version"], 1)
        self.assertEqual(restored["run_state"], RunState.FAILED.value)
        self.assertEqual(restored["reason_code"], "workflow_failed")
        self.assertEqual(restored["results"][0]["run_state"], RunState.FAILED.value)
        self.assertEqual(restored["results"][0]["reason_code"], "command_failed")

    def test_retry_uses_a_new_run_attempt_linked_to_the_same_task(self):
        """A retry cannot overwrite its predecessor's terminal evidence."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._run_synthetic(root, "first-run", task_id="task-379")
            second = self._run_synthetic(root, "second-run", task_id="task-379")
            first_evidence = read_workflow_log(root, "first-run")["run"]
            second_evidence = read_workflow_log(root)["run"]

        self.assertEqual(first["attempt"], 1)
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(second["predecessor_run_id"], "first-run")
        self.assertEqual(first_evidence["run_state"], RunState.COMPLETED.value)
        self.assertEqual(second_evidence["run_id"], "second-run")
        self.assertEqual(second_evidence["task_id"], "task-379")

    def test_timeout_keeps_its_canonical_terminal_meaning(self):
        plan = {
            "ok": True,
            "workflow": {"id": "timed"},
            "steps": [{"step": "slow", "safe_command": ["slow"]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(workflow_runner, "workflow_plan", return_value=plan),
                mock.patch.object(
                    workflow_runner,
                    "run_policy_command",
                    return_value=_TimedOutCommand(),
                ),
            ):
                result = run_workflow(root, "timed", execute=True, run_id="timeout-run")
            snapshot = read_workflow_log(root)["run"]

        self.assertEqual(result["run_state"], RunState.TIMEOUT.value)
        self.assertEqual(result["reason_code"], "workflow_step_timeout")
        self.assertEqual(snapshot["steps"][0]["run_state"], RunState.TIMEOUT.value)

    def test_failed_workflow_terminalizes_each_unstarted_step(self):
        """A terminal run cannot leave later declared steps ambiguously queued."""

        plan = {
            "ok": True,
            "workflow": {"id": "failure"},
            "steps": [
                {"step": "fails", "safe_command": ["fails"]},
                {"step": "never-started", "safe_command": ["never-started"]},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(workflow_runner, "workflow_plan", return_value=plan),
                mock.patch.object(
                    workflow_runner,
                    "run_policy_command",
                    return_value=_FailedCommand(),
                ),
            ):
                result = run_workflow(
                    root, "failure", execute=True, run_id="failure-run"
                )
            snapshot = read_workflow_log(root)["run"]

        self.assertEqual(result["run_state"], RunState.FAILED.value)
        self.assertEqual(snapshot["steps"][0]["run_state"], RunState.FAILED.value)
        self.assertEqual(snapshot["steps"][1]["run_state"], RunState.BLOCKED.value)
        self.assertEqual(snapshot["steps"][1]["reason_code"], "workflow_terminated")

    def test_empty_executable_workflow_is_blocked_not_completed(self):
        """No declared executable work is a configuration block, not success."""

        plan = {
            "ok": True,
            "workflow": {"id": "empty"},
            "steps": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(workflow_runner, "workflow_plan", return_value=plan):
                result = run_workflow(root, "empty", execute=True, run_id="empty-run")

        self.assertEqual(result["run_state"], RunState.BLOCKED.value)
        self.assertEqual(result["reason_code"], "no_executable_steps")

    def test_invalid_canonical_history_is_reported_as_degraded(self):
        """Recovery names corrupt state facts; it must never substitute success."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run_synthetic(root, "corrupt-run")
            path = workflow_runner.workflow_log_path(root, "corrupt-run")
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["state_history"].append(
                {
                    "state": "running",
                    "at": raw["updated_at"],
                    "reason_code": "illegal_resurrection",
                }
            )
            path.write_text(json.dumps(raw), encoding="utf-8")
            evidence = read_workflow_log(root)

        self.assertEqual(evidence["state"], "degraded")
        self.assertIn("illegal workflow state-history transition", evidence["reason"])

    def test_invalid_canonical_step_history_is_reported_as_degraded(self):
        """A valid run cannot make corrupt step evidence look trustworthy."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run_synthetic(root, "step-corrupt-run")
            path = workflow_runner.workflow_log_path(root, "step-corrupt-run")
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["steps"][0]["state_history"].append(
                {
                    "state": "running",
                    "at": raw["updated_at"],
                    "reason_code": "illegal_step_resurrection",
                }
            )
            path.write_text(json.dumps(raw), encoding="utf-8")
            evidence = read_workflow_log(root)

        self.assertEqual(evidence["state"], "degraded")
        self.assertIn("illegal workflow step-history transition", evidence["reason"])

    def test_contradictory_completion_verdict_is_reported_as_degraded(self):
        """The stored terminal verdict cannot disagree with its run state."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run_synthetic(root, "verdict-corrupt-run")
            path = workflow_runner.workflow_log_path(root, "verdict-corrupt-run")
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["completion_verdict"] = {
                "verdict": "failed",
                "reason_code": "workflow_step_failed",
            }
            path.write_text(json.dumps(raw), encoding="utf-8")
            evidence = read_workflow_log(root)

        self.assertEqual(evidence["state"], "degraded")
        self.assertIn("completion verdict contradicts run state", evidence["reason"])


class WorkflowCliParityTests(unittest.TestCase):
    def test_workflow_cli_uses_the_canonical_terminal_exit_code(self) -> None:
        """Scripts can distinguish a partial workflow from an execution failure."""

        from opaihub.cli import cmd_workflow

        args = type(
            "Args",
            (),
            {"project": ".", "id": "workflow", "execute": True, "timeout": 120},
        )()
        with mock.patch("opaihub.cli.run_workflow") as run:
            run.return_value = {
                "executed": True,
                "run_state": RunState.PARTIAL.value,
                "reason_code": "workflow_steps_unmapped",
            }
            self.assertEqual(cmd_workflow(args), exit_code_for(RunState.PARTIAL))


class RunJournalReplayTests(unittest.TestCase):
    """The journal (#517) must independently reconstruct what the snapshot says.

    Every existing test above exercises the snapshot file exactly as before —
    the journal is additive evidence, not a replacement, so none of that
    coverage should have needed to change. These prove the new evidence is
    trustworthy on its own terms: replay determinism, restart-safety lease
    visibility, and quarantine of corruption without stalling the pipeline.
    """

    def _run_synthetic(self, root: Path, run_id: str) -> dict[str, object]:
        with (
            mock.patch.object(
                workflow_runner, "workflow_plan", return_value=_synthetic_plan()
            ),
            mock.patch.object(
                workflow_runner,
                "run_policy_command",
                return_value=_CompletedCommand(f"output from {run_id}"),
            ),
        ):
            return run_workflow(root, "synthetic", execute=True, run_id=run_id)

    def test_replay_matches_the_snapshots_run_state_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run_synthetic(root, "replay-run")
            snapshot = read_workflow_log(root, "replay-run")["run"]
            projection = workflow_runner.replay_workflow_run(root, "replay-run")

            self.assertEqual(projection["run_state"], snapshot["run_state"])
            self.assertEqual(projection["reason_code"], snapshot["reason_code"])
            self.assertEqual(projection["state_history"], snapshot["state_history"])
            for index, step in enumerate(snapshot["steps"]):
                replayed_step = projection["steps"][str(index)]
                self.assertEqual(replayed_step["run_state"], step["run_state"])
                self.assertEqual(replayed_step["reason_code"], step["reason_code"])
                self.assertEqual(replayed_step["state_history"], step["state_history"])

    def test_replay_matches_the_snapshot_for_a_failed_run_too(self) -> None:
        """Determinism must hold on the unhappy path, not just a clean success."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(
                    workflow_runner, "workflow_plan", return_value=_synthetic_plan()
                ),
                mock.patch.object(
                    workflow_runner, "run_policy_command", return_value=_FailedCommand()
                ),
            ):
                run_workflow(root, "synthetic", execute=True, run_id="failed-run")
            snapshot = read_workflow_log(root, "failed-run")["run"]
            self.assertEqual(snapshot["run_state"], RunState.FAILED.value)
            projection = workflow_runner.replay_workflow_run(root, "failed-run")
            self.assertEqual(projection["run_state"], RunState.FAILED.value)
            self.assertEqual(projection["state_history"], snapshot["state_history"])

    def test_repeated_replay_and_load_always_converge_to_the_same_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run_synthetic(root, "converge-run")
            journal_path = workflow_runner.workflow_journal_path(root, "converge-run")

            first = workflow_runner.replay_workflow_run(root, "converge-run")
            second = workflow_runner.replay_workflow_run(root, "converge-run")
            loaded = run_journal.load(
                journal_path,
                reduce=workflow_runner._reduce_workflow_journal,
                empty=workflow_runner._empty_workflow_projection,
                validate=workflow_runner._validate_workflow_journal_event,
            )
            self.assertEqual(first, second)
            self.assertEqual(first, loaded.projection)

    def test_the_lease_is_acquired_and_observable_through_read_workflow_log(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run_synthetic(root, "lease-run")
            evidence = read_workflow_log(root, "lease-run")
            self.assertIn("lease", evidence)
            self.assertTrue(evidence["lease"]["ownerIsThisProcess"])
            self.assertFalse(evidence["lease"]["stale"])
            self.assertEqual(evidence["lease"]["reason"], "owned_here")

    def test_a_corrupted_journal_is_quarantined_and_the_pipeline_still_advances(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = workflow_runner._reserve_workflow_run(
                root,
                "synthetic",
                "quarantine-run",
                task_id="task-quarantine",
                plan_steps=[{"step": "one"}],
            )
            snapshot, _ = workflow_runner._advance_workflow_run(
                root, snapshot, RunState.PREPARING, reason_code="preparing_execution"
            )
            snapshot, _ = workflow_runner._advance_workflow_run(
                root, snapshot, RunState.RUNNING, reason_code="execution_started"
            )

            journal_path = workflow_runner.workflow_journal_path(root, "quarantine-run")
            lines = journal_path.read_text(encoding="utf-8").splitlines()
            self.assertGreaterEqual(len(lines), 2)
            lines[0] = "not valid json at all, injected corruption"
            journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.assertRaises(run_journal.JournalCorruption):
                workflow_runner.replay_workflow_run(root, "quarantine-run")

            # The corruption is in evidence, not the run: the very next
            # transition succeeds, quarantining the bad journal instead of
            # blocking the workflow that depends on it.
            snapshot, _ = workflow_runner._advance_workflow_run(
                root, snapshot, RunState.VERIFYING, reason_code="verifying_workflow"
            )

            quarantine_dir = journal_path.parent / "quarantine"
            self.assertTrue(quarantine_dir.is_dir())
            quarantined = list(quarantine_dir.glob("*.manifest.json"))
            self.assertEqual(len(quarantined), 1)
            manifest = json.loads(quarantined[0].read_text(encoding="utf-8"))
            self.assertEqual(manifest["original_path"], str(journal_path))

            # And the fresh journal that replaced it is healthy again.
            projection = workflow_runner.replay_workflow_run(root, "quarantine-run")
            self.assertEqual(projection["run_state"], RunState.VERIFYING.value)

    def test_snapshot_and_journal_are_never_written_as_only_one_of_the_two(
        self,
    ) -> None:
        """A caller must never observe durable-snapshot evidence with no
        corresponding journal entry, or vice versa, for any transition."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run_synthetic(root, "paired-run")
            snapshot = read_workflow_log(root, "paired-run")["run"]
            projection = workflow_runner.replay_workflow_run(root, "paired-run")
            self.assertEqual(
                len(projection["state_history"]), len(snapshot["state_history"])
            )
            for index, step in enumerate(snapshot["steps"]):
                self.assertEqual(
                    len(projection["steps"][str(index)]["state_history"]),
                    len(step["state_history"]),
                )
