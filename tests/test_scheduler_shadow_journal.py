"""#613 Stage 2: schedules mirror into the shadow journal, and are written safely.

``vestahub/scheduler.py`` is Stage 1's "runs: scheduled work". It is the first
migrated module that keeps *one document* rather than one file per record, so
the thing being journalled is the document itself.

It also carried a real durability bug, which the migration fixes:

* ``_write`` used a bare ``path.write_text`` -- not atomic, no lock. A crash
  or a concurrent writer mid-write leaves a truncated ``schedules.json``.
* ``_read`` turns any ``JSONDecodeError`` into ``[]``.

Together those mean a torn write does not surface as an error; it reports
"you have no schedules". That is exactly the "corruption becomes
empty/default permissive state" #613 forbids, and it is silent, which is
worse than loud.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from vestahub import scheduler


class ScheduleShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_written_document_is_mirrored_and_agrees(self):
        scheduler._write(self.root, [{"id": "nightly", "enabled": True}])

        self.assertEqual(
            scheduler.shadow_journal_projection(self.root),
            {"schedules": [{"id": "nightly", "enabled": True}]},
        )
        self.assertIsNone(scheduler.schedule_contradiction_report(self.root))

    def test_each_rewrite_is_mirrored(self):
        scheduler._write(self.root, [{"id": "a"}])
        scheduler._write(self.root, [{"id": "a"}, {"id": "b"}])

        projection = scheduler.shadow_journal_projection(self.root)

        self.assertEqual([item["id"] for item in projection["schedules"]], ["a", "b"])
        self.assertIsNone(scheduler.schedule_contradiction_report(self.root))

    def test_an_empty_document_is_mirrored_rather_than_skipped(self):
        """Deleting the last schedule is a state change worth recording.

        A validator that required a non-empty list would drop it, leaving the
        shadow claiming schedules that no longer exist.
        """
        scheduler._write(self.root, [{"id": "a"}])
        scheduler._write(self.root, [])

        self.assertEqual(
            scheduler.shadow_journal_projection(self.root), {"schedules": []}
        )
        self.assertIsNone(scheduler.schedule_contradiction_report(self.root))

    def test_the_write_is_atomic_and_leaves_no_partial_document(self):
        """The bug this migration fixes: a bare write_text is not atomic."""
        scheduler._write(self.root, [{"id": "a"}])
        path = scheduler._path(self.root)

        # A complete, parseable document, and no temp debris beside it.
        self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), list)
        strays = sorted(
            p.name for p in path.parent.iterdir() if p.suffix in {".tmp", ".partial"}
        )
        self.assertEqual(strays, [])

    def test_a_truncated_file_is_reported_rather_than_read_as_empty(self):
        """`_read` returns [] for unreadable content -- the divergence must show.

        This is the failure mode that made the old unlocked write dangerous:
        losing every schedule looked identical to never having had any. The
        contradiction report is what makes the difference visible.
        """
        scheduler._write(self.root, [{"id": "a"}, {"id": "b"}])
        scheduler._path(self.root).write_text('[{"id": "a"', encoding="utf-8")

        # The legacy reader still says "no schedules"...
        self.assertEqual(scheduler.list_schedules(self.root), [])
        # ...but the shadow knows better, and the report says so.
        report = scheduler.schedule_contradiction_report(self.root)
        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})
        self.assertEqual(len(report["shadow"]["schedules"]), 2)

    def test_an_out_of_band_write_is_reported(self):
        scheduler._write(self.root, [{"id": "a"}])
        scheduler._path(self.root).write_text(
            json.dumps([{"id": "tampered"}]), encoding="utf-8"
        )

        report = scheduler.schedule_contradiction_report(self.root)

        self.assertIsNotNone(report)
        self.assertIn("schedules", report["mismatched_fields"])

    def test_a_never_written_document_agrees_as_both_empty(self):
        self.assertIsNone(scheduler.schedule_contradiction_report(self.root))
        self.assertEqual(scheduler.shadow_journal_projection(self.root), {})

    def test_concurrent_writes_leave_file_and_shadow_agreeing(self):
        """The lock this migration added is what makes this deterministic."""

        def write(index: int) -> None:
            scheduler._write(self.root, [{"id": f"s-{index}"}])

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(write, range(12)))

        self.assertIsNone(scheduler.schedule_contradiction_report(self.root))
        self.assertEqual(
            scheduler.shadow_journal_projection(self.root)["schedules"],
            scheduler.list_schedules(self.root),
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
