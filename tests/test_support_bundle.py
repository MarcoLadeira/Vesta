"""Bounded, previewable diagnostic bundle for support requests (#551).

build_support_bundle only composes sources that already redact themselves
at write time (audit.record_audit_event, ledger.record_event) -- these
tests pin the composition, the hard event-count and byte-size caps, and
the CLI's preview-by-default / --out-to-write behavior.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from vesta.cli import main
from vestahub import support_bundle as sb
from vestahub.audit import GUARD_DENY, record_audit_event
from vestahub.ledger import record_route_decision


class BuildSupportBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_an_empty_project_still_produces_a_valid_bundle(self) -> None:
        bundle = sb.build_support_bundle(self.root)
        self.assertEqual(bundle["report"], "vesta-support-bundle")
        self.assertEqual(bundle["audit"]["events"], [])
        self.assertEqual(bundle["ledger_events"], [])
        self.assertFalse(bundle["truncated"]["audit_events"])
        self.assertFalse(bundle["truncated"]["ledger_events"])

    def test_recorded_audit_and_ledger_events_are_both_included(self) -> None:
        record_audit_event(
            self.root, GUARD_DENY, actor="agent", operation="write_file", reason="x"
        )
        record_route_decision(self.root, "a task", model_tier="L1")

        bundle = sb.build_support_bundle(self.root)

        self.assertEqual(len(bundle["audit"]["events"]), 1)
        self.assertEqual(bundle["audit"]["events"][0]["event_type"], GUARD_DENY)
        self.assertEqual(len(bundle["ledger_events"]), 1)
        self.assertEqual(bundle["ledger_events"][0]["model_tier"], "L1")

    def test_a_secret_in_an_audit_reason_never_reaches_the_bundle(self) -> None:
        # record_audit_event redacts string fields at write time; the bundle
        # must not somehow route around that.
        record_audit_event(
            self.root,
            GUARD_DENY,
            actor="agent",
            operation="run_command",
            reason="blocked token=sk-abcdef1234567890abcd",
        )
        bundle = sb.build_support_bundle(self.root)
        self.assertNotIn("sk-abcdef1234567890abcd", json.dumps(bundle))

    def test_audit_events_beyond_the_cap_are_truncated_to_the_most_recent(
        self,
    ) -> None:
        with mock.patch.object(sb, "MAX_AUDIT_EVENTS", 3):
            for i in range(5):
                record_audit_event(
                    self.root, GUARD_DENY, actor="agent", operation=f"op-{i}"
                )
            bundle = sb.build_support_bundle(self.root)
        kept = [e["operation"] for e in bundle["audit"]["events"]]
        self.assertEqual(kept, ["op-2", "op-3", "op-4"])
        self.assertTrue(bundle["truncated"]["audit_events"])

    def test_ledger_events_beyond_the_cap_are_truncated_and_flagged(self) -> None:
        with mock.patch.object(sb, "MAX_LEDGER_EVENTS", 2):
            for _ in range(4):
                record_route_decision(self.root, "task", model_tier="L1")
            bundle = sb.build_support_bundle(self.root)
        self.assertEqual(len(bundle["ledger_events"]), 2)
        self.assertTrue(bundle["truncated"]["ledger_events"])

    def test_the_bundle_is_hard_capped_by_serialized_size(self) -> None:
        for i in range(10):
            record_audit_event(
                self.root, GUARD_DENY, actor="agent", operation=f"op-{i}" * 20
            )
        uncapped = sb.build_support_bundle(self.root)
        uncapped_size = sb._bundle_size(uncapped)
        cap = uncapped_size // 2

        # A cap well below the natural size forces real shrinking -- proves
        # the byte budget is enforced, not just documented.
        with mock.patch.object(sb, "MAX_BUNDLE_BYTES", cap):
            bundle = sb.build_support_bundle(self.root)

        self.assertLessEqual(sb._bundle_size(bundle), cap)
        self.assertTrue(bundle["truncated"]["audit_events"])
        self.assertLess(
            len(bundle["audit"]["events"]), len(uncapped["audit"]["events"])
        )


class SupportBundleCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_out_flag_prints_the_bundle_instead_of_writing_a_file(self) -> None:
        with mock.patch("vesta.cli.print_json") as fake_print:
            rc = main(["support-bundle", "--project", str(self.root)])
        self.assertEqual(rc, 0)
        fake_print.assert_called_once()
        printed = fake_print.call_args.args[0]
        self.assertEqual(printed["report"], "vesta-support-bundle")

    def test_out_flag_writes_a_readable_json_bundle(self) -> None:
        record_audit_event(self.root, GUARD_DENY, actor="agent", operation="x")
        out = Path(self._tmp.name) / "bundle.json"
        rc = main(["support-bundle", "--project", str(self.root), "--out", str(out)])
        self.assertEqual(rc, 0)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["report"], "vesta-support-bundle")
        self.assertEqual(len(written["audit"]["events"]), 1)
        self.assertIn("version", written)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
