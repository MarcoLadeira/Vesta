"""Race-safe per-run workflow evidence persistence (#445)."""

from __future__ import annotations

import multiprocessing
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opaihub import workflow_runner
from opaihub.workflow_runner import read_workflow_log, run_workflow


class _CompletedCommand:
    def __init__(self, output: str) -> None:
        self.returncode = 0
        self.combined_output = output


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
                                    "step": "synthetic",
                                    "status": "ok",
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
                if (
                    snapshot["run_id"] == "interrupted-run"
                    and snapshot["state"] != "running"
                ):
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
            self.assertEqual(evidence["run"]["state"], "running")
            self.assertEqual(evidence["run"]["results"], [])

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
