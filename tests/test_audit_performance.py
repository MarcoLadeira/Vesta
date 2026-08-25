"""Bounded audit append and summary hot paths."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import audit


class AuditPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        clear = getattr(audit, "clear_audit_summary_cache", None)
        if clear is not None:
            clear()

    def tearDown(self) -> None:
        clear = getattr(audit, "clear_audit_summary_cache", None)
        if clear is not None:
            clear()

    def test_current_checkpoint_avoids_full_log_scan_on_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit.record_audit_event(root, "policy_allow", note="first")

            with mock.patch.object(
                audit,
                "_last_entry",
                wraps=audit._last_entry,
            ) as scan:
                audit.record_audit_event(root, "policy_allow", note="second")
                audit.record_audit_event(root, "policy_allow", note="third")

            self.assertEqual(scan.call_count, 0)
            self.assertEqual(
                [event["seq"] for event in audit.read_audit(root)],
                [1, 2, 3],
            )

    def test_legacy_checkpoint_scans_once_then_records_log_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit.record_audit_event(root, "policy_allow", note="first")
            checkpoint = json.loads(
                audit.checkpoint_path(root).read_text(encoding="utf-8")
            )
            checkpoint.pop("log_size", None)
            audit.checkpoint_path(root).write_text(
                json.dumps(checkpoint), encoding="utf-8"
            )

            with mock.patch.object(
                audit,
                "_last_entry",
                wraps=audit._last_entry,
            ) as scan:
                audit.record_audit_event(root, "policy_allow", note="second")

            self.assertEqual(scan.call_count, 1)
            upgraded = json.loads(
                audit.checkpoint_path(root).read_text(encoding="utf-8")
            )
            self.assertEqual(
                upgraded["log_size"], audit.audit_path(root).stat().st_size
            )

    def test_corrupted_checkpoint_hash_falls_back_to_durable_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = audit.record_audit_event(root, "policy_allow", note="first")
            checkpoint_file = audit.checkpoint_path(root)
            checkpoint = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            checkpoint["head_hash"] = (
                "1" if checkpoint["head_hash"][0] != "1" else "2"
            ) + checkpoint["head_hash"][1:]
            checkpoint_file.write_text(json.dumps(checkpoint), encoding="utf-8")

            second = audit.record_audit_event(root, "policy_allow", note="second")

            self.assertEqual(second["prev_hash"], first["entry_hash"])
            self.assertTrue(audit.verify_chain(root)["ok"])

    def test_repeated_summaries_parse_and_verify_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit.record_audit_event(root, "policy_deny", note="first")

            with (
                mock.patch.object(
                    audit, "_read_audit", wraps=audit._read_audit
                ) as read,
                mock.patch.object(
                    audit, "verify_chain", wraps=audit.verify_chain
                ) as verify,
            ):
                first = audit.summarize_audit(root)
                second = audit.summarize_audit(root)
                third = audit.summarize_audit(root)

            self.assertEqual(read.call_count, 1)
            self.assertEqual(verify.call_count, 1)
            self.assertEqual(first, second)
            self.assertEqual(second, third)

    def test_summary_cache_invalidates_after_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit.record_audit_event(root, "policy_allow")
            self.assertEqual(audit.summarize_audit(root)["event_count"], 1)

            audit.record_audit_event(root, "policy_deny")

            self.assertEqual(audit.summarize_audit(root)["event_count"], 2)

    def test_summary_cache_detects_same_metadata_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit.record_audit_event(root, "policy_deny")
            self.assertTrue(audit.summarize_audit(root)["chain"]["ok"])
            path = audit.audit_path(root)
            before = path.stat()
            damaged = path.read_text(encoding="utf-8").replace(
                "policy_deny", "policy_xeny"
            )
            path.write_text(damaged, encoding="utf-8")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

            summary = audit.summarize_audit(root)

            self.assertFalse(summary["chain"]["ok"])
            self.assertEqual(summary["by_type"], {"policy_xeny": 1})

    def test_cached_summary_is_a_defensive_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit.record_audit_event(root, "policy_allow")
            first = audit.summarize_audit(root)
            first["by_type"]["forged"] = 99

            second = audit.summarize_audit(root)

            self.assertNotIn("forged", second["by_type"])

    def test_cached_summary_does_not_reopen_the_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit.record_audit_event(root, "policy_allow")
            first = audit.summarize_audit(root)

            with mock.patch.object(
                Path,
                "open",
                side_effect=AssertionError("cache hit reopened the audit log"),
            ):
                second = audit.summarize_audit(root)

            self.assertEqual(second, first)


if __name__ == "__main__":
    unittest.main()
