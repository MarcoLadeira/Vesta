from __future__ import annotations

import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path
from queue import Empty
from typing import Any

from opaihub import scheduler


def _create_schedule_slowly(
    root: str,
    cadence: str,
    ready: Any,
    start: Any,
    results: Any,
) -> None:
    original_read = scheduler._read

    def delayed_read(project_root: Path) -> list[dict[str, Any]]:
        schedules = original_read(project_root)
        time.sleep(0.3)
        return schedules

    scheduler._read = delayed_read
    ready.put(cadence)
    start.wait()
    try:
        result = scheduler.create_schedule(Path(root), "bug_fix", cadence)
        results.put(("ok", cadence, result["status"]))
    except BaseException as exc:  # pragma: no cover - reported to the parent
        results.put(("error", cadence, type(exc).__name__, str(exc)))


class ScheduleTransactionTests(unittest.TestCase):
    def test_concurrent_create_and_update_preserve_both_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scheduler._write(
                root,
                [
                    {
                        "id": "bug_fix:daily",
                        "workflow_id": "bug_fix",
                        "cadence": "daily",
                        "enabled": False,
                        "generation": "stale",
                    }
                ],
            )
            context = multiprocessing.get_context("spawn")
            ready = context.Queue()
            start = context.Event()
            results = context.Queue()
            workers = [
                context.Process(
                    target=_create_schedule_slowly,
                    args=(str(root), cadence, ready, start, results),
                )
                for cadence in ("daily", "weekly")
            ]

            for worker in workers:
                worker.start()
            try:
                self.assertEqual(
                    {ready.get(timeout=10), ready.get(timeout=10)},
                    {"daily", "weekly"},
                )
                start.set()
                outcomes = [results.get(timeout=20) for _ in workers]
            except Empty as exc:  # pragma: no cover - timeout is the assertion
                self.fail(f"concurrent scheduler worker timed out: {exc}")
            finally:
                start.set()
                for worker in workers:
                    worker.join(timeout=20)

            schedules = scheduler.list_schedules(root)
            by_id = {item["id"]: item for item in schedules}

        self.assertEqual([worker.exitcode for worker in workers], [0, 0])
        self.assertTrue(all(outcome[0] == "ok" for outcome in outcomes))
        self.assertEqual(set(by_id), {"bug_fix:daily", "bug_fix:weekly"})
        self.assertTrue(by_id["bug_fix:daily"]["enabled"])
        self.assertNotIn("generation", by_id["bug_fix:daily"])
        for schedule in by_id.values():
            self.assertEqual(schedule["runner"], "manual-cli")
            self.assertIn("No background daemon", schedule["notes"])


if __name__ == "__main__":
    unittest.main()
