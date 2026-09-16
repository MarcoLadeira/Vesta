"""Regression coverage for bounded ledger and audit tail reads."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vestahub import (
    audit,
    background_runs,
    benchmark,
    cost_telemetry,
    health,
    ledger,
    vestabench,
    runs,
)
from vestahub.state import state_dir
from vestahub.workflow_ledger import WorkflowLedger


class JsonlTailReadTests(unittest.TestCase):
    def _write_rows(self, path: Path, count: int = 2_000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [json.dumps({"row": index}) for index in range(count)]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def test_zero_limit_returns_no_ledger_or_audit_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_rows(ledger.ledger_path(root), count=3)
            self._write_rows(audit.audit_path(root), count=3)

            self.assertEqual(ledger.read_events(root, limit=0), [])
            self.assertEqual(audit.read_audit(root, limit=0), [])

    def test_limited_reads_do_not_materialize_the_complete_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_rows(ledger.ledger_path(root))
            self._write_rows(audit.audit_path(root))

            with mock.patch.object(
                Path,
                "read_text",
                side_effect=AssertionError("bounded read loaded the complete log"),
            ):
                ledger_rows = ledger.read_events(root, limit=2)
                audit_rows = audit.read_audit(root, limit=2)

            self.assertEqual([row["row"] for row in ledger_rows], [1998, 1999])
            self.assertEqual([row["row"] for row in audit_rows], [1998, 1999])

    def test_tail_limit_keeps_physical_line_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = (
                json.dumps({"row": 1})
                + "\n"
                + "malformed\n"
                + json.dumps({"row": 2})
                + "\n\n"
            )
            ledger_path = ledger.ledger_path(root)
            audit_path = audit.audit_path(root)
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.write_text(content, encoding="utf-8")
            audit_path.write_text(content, encoding="utf-8")

            self.assertEqual(ledger.read_events(root, limit=3), [{"row": 2}])
            self.assertEqual(audit.read_audit(root, limit=3), [{"row": 2}])

    def test_other_limited_history_readers_do_not_load_complete_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = WorkflowLedger(root, task_id="task-1")
            cases = (
                (
                    background_runs._notifications_path(root),
                    lambda: background_runs.read_notifications(root, limit=2),
                ),
                (
                    benchmark.benchmark_history_path(root),
                    lambda: benchmark.read_benchmark_history(root, limit=2),
                ),
                (
                    state_dir(root) / "agent" / "events.jsonl",
                    lambda: cost_telemetry.read_cost_events(root, limit=2),
                ),
                (
                    state_dir(root) / "health" / "history.jsonl",
                    lambda: health.health_history(root, limit=2),
                ),
                (
                    vestabench.vestabench_history_path(root),
                    lambda: vestabench.read_vestabench_history(root, limit=2),
                ),
                (runs.runs_path(root), lambda: runs.recent_runs(root, limit=2)),
                (workflow.path, lambda: workflow.read(limit=2)),
            )
            for path, _reader in cases:
                event_type = "cost_telemetry" if path == workflow.path else "history"
                self._write_event_rows(path, event_type=event_type)

            with mock.patch.object(
                Path,
                "read_text",
                side_effect=AssertionError("bounded read loaded the complete log"),
            ):
                for _path, reader in cases:
                    self.assertEqual(len(reader()), 2)

    def test_zero_limit_is_empty_for_other_history_readers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = WorkflowLedger(root, task_id="task-1")
            paths_and_readers = (
                (
                    background_runs._notifications_path(root),
                    lambda: background_runs.read_notifications(root, limit=0),
                ),
                (
                    benchmark.benchmark_history_path(root),
                    lambda: benchmark.read_benchmark_history(root, limit=0),
                ),
                (
                    state_dir(root) / "agent" / "events.jsonl",
                    lambda: cost_telemetry.read_cost_events(root, limit=0),
                ),
                (
                    state_dir(root) / "health" / "history.jsonl",
                    lambda: health.health_history(root, limit=0),
                ),
                (
                    vestabench.vestabench_history_path(root),
                    lambda: vestabench.read_vestabench_history(root, limit=0),
                ),
                (runs.runs_path(root), lambda: runs.recent_runs(root, limit=0)),
                (workflow.path, lambda: workflow.read(limit=0)),
            )
            for path, _reader in paths_and_readers:
                event_type = "cost_telemetry" if path == workflow.path else "history"
                self._write_event_rows(path, count=3, event_type=event_type)

            for _path, reader in paths_and_readers:
                self.assertEqual(reader(), [])

    def test_workflow_limit_counts_matching_task_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = WorkflowLedger(root, task_id="task-1")
            workflow.append("step", sequence=1)
            workflow.append("step", sequence=2)
            for sequence in range(20):
                WorkflowLedger(root, task_id="task-2").append("step", sequence=sequence)

            events = workflow.read(limit=2)

        self.assertEqual([event["sequence"] for event in events], [1, 2])

    def test_cost_limit_counts_cost_events_not_interleaved_workflow_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = WorkflowLedger(root, task_id="task-1")
            workflow.append("cost_telemetry", sequence=1)
            workflow.append("cost_telemetry", sequence=2)
            for sequence in range(20):
                workflow.append("step", sequence=sequence)

            events = cost_telemetry.read_cost_events(root, limit=2)

        self.assertEqual([event["sequence"] for event in events], [1, 2])

    def test_workflow_reader_skips_valid_json_that_is_not_an_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = WorkflowLedger(root, task_id="task-1")
            workflow.append("step", sequence=1)
            with workflow.path.open("a", encoding="utf-8") as handle:
                handle.write("null\n")
                handle.write('"not an event"\n')
                handle.write("[]\n")

            events = workflow.read(limit=1)

        self.assertEqual([event["sequence"] for event in events], [1])

    def test_history_readers_recover_valid_objects_before_a_corrupt_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = (
                (
                    background_runs._notifications_path(root),
                    lambda: background_runs.read_notifications(root, limit=2),
                ),
                (
                    benchmark.benchmark_history_path(root),
                    lambda: benchmark.read_benchmark_history(root, limit=2),
                ),
                (
                    state_dir(root) / "health" / "history.jsonl",
                    lambda: health.health_history(root, limit=2),
                ),
                (
                    vestabench.vestabench_history_path(root),
                    lambda: vestabench.read_vestabench_history(root, limit=2),
                ),
                (runs.runs_path(root), lambda: runs.recent_runs(root, limit=2)),
            )
            for path, _reader in cases:
                path.parent.mkdir(parents=True, exist_ok=True)
                lines = [json.dumps({"row": 1}), json.dumps({"row": 2})]
                lines.extend("null" if index % 2 else "not-json" for index in range(20))
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            for _path, reader in cases:
                self.assertEqual([item["row"] for item in reader()], [1, 2])

    def _write_event_rows(
        self,
        path: Path,
        *,
        count: int = 2_000,
        event_type: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            json.dumps({"row": index, "event_type": event_type, "task_id": "task-1"})
            for index in range(count)
        ]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
