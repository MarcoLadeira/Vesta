"""Malformed audit entries degrade honestly, never vanish (#474)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from opaihub.audit import (
    audit_path,
    read_audit,
    record_audit_event,
    summarize_audit,
)


class AuditDegradationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _corrupt_middle_record(self, text: str = "{ torn middle record") -> None:
        with audit_path(self.root).open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")

    def test_clean_audit_summary_is_complete(self):
        record_audit_event(self.root, "policy_allow", note="a")
        summary = summarize_audit(self.root)
        self.assertTrue(summary["complete"])
        self.assertFalse(summary["degraded"])
        self.assertEqual(summary["skipped_events"], 0)

    def test_malformed_middle_record_marks_the_summary_degraded(self):
        record_audit_event(self.root, "policy_allow", note="a")
        self._corrupt_middle_record()
        record_audit_event(self.root, "policy_deny", note="b")

        summary = summarize_audit(self.root)
        # Both valid events are still counted; the damage is surfaced, not hidden.
        self.assertEqual(summary["event_count"], 2)
        self.assertFalse(summary["complete"])
        self.assertTrue(summary["degraded"])
        self.assertEqual(summary["skipped_events"], 1)

    def test_non_object_json_line_counts_as_malformed(self):
        record_audit_event(self.root, "policy_allow")
        with audit_path(self.root).open("a", encoding="utf-8") as handle:
            handle.write("42\n")  # valid JSON, but not an audit object
            handle.write('"just a string"\n')
        summary = summarize_audit(self.root)
        self.assertEqual(summary["skipped_events"], 2)
        self.assertTrue(summary["degraded"])
        # read_audit still yields only the well-formed object events.
        self.assertEqual(len(read_audit(self.root)), 1)

    def test_summary_survives_events_missing_event_type(self):
        record_audit_event(self.root, "policy_allow")
        with audit_path(self.root).open("a", encoding="utf-8") as handle:
            handle.write('{"seq": 99, "note": "no event_type here"}\n')
        # Must not KeyError; the typeless event buckets under "unknown".
        summary = summarize_audit(self.root)
        self.assertIn("unknown", summary["by_type"])


if __name__ == "__main__":
    unittest.main()
