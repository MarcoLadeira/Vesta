"""Legacy status strings remain observable boundary compatibility only."""

from __future__ import annotations

import unittest

from opaihub.completion import (
    CompletionState,
    completion_state_from_legacy,
    legacy_status_for_completion,
)
from opaihub.legacy_status import legacy_status_to_result, legacy_status_usage


class LegacyStatusBoundaryTests(unittest.TestCase):
    def test_unknown_legacy_status_degrades_to_needs_attention(self) -> None:
        result = legacy_status_to_result({"status": "future_vendor_state"})

        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.compatibility["state"], "incompatible")
        self.assertFalse(result.recovery["automatic_retry"])

    def test_unknown_legacy_status_ignores_malformed_timestamp(self) -> None:
        result = legacy_status_to_result(
            {"status": "future_vendor_state", "finished_at": "eventually"}
        )

        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(
            result.lifecycle["final_transition_at"], "1970-01-01T00:00:00Z"
        )

    def test_unknown_legacy_status_discards_non_json_incidental_metadata(self) -> None:
        result = legacy_status_to_result(
            {
                "status": "future_vendor_state",
                "identity": {"unsafe": object()},
                "schema_version": {"future": 2},
            }
        )

        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.identity, {})
        self.assertEqual(result.compatibility["source_schema_version"], "dict")

    def test_known_legacy_failure_maps_at_the_import_boundary(self) -> None:
        result = legacy_status_to_result(
            {
                "status": "provider_unavailable",
                "run_id": "legacy-run-1",
                "attempt_id": "legacy-attempt-1",
                "finished_at": "2026-08-02T12:34:56Z",
                "error": {"record_ref": {"kind": "error", "id": "error-1"}},
            }
        )

        self.assertEqual(result.lifecycle["state"], "failed")
        self.assertEqual(result.compatibility["legacy_status"], "provider_unavailable")
        self.assertEqual(result.compatibility["state"], "legacy_import")

    def test_legacy_completed_status_cannot_bypass_terminal_evidence(self) -> None:
        result = legacy_status_to_result({"status": "answered"})

        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.compatibility["state"], "degraded")
        self.assertIn("evidence", result.lifecycle["reason_detail"])

    def test_malformed_known_legacy_payload_degrades_instead_of_raising(self) -> None:
        result = legacy_status_to_result(
            {"status": "failed", "provider": {"snapshot": {"mutable": True}}}
        )

        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.compatibility["state"], "degraded")

    def test_boundary_usage_reports_removal_gate_and_returns_a_copy(self) -> None:
        before = legacy_status_usage()
        legacy_status_to_result({"status": "failed"})
        completion_state_from_legacy({"status": "answered"})
        legacy_status_for_completion(CompletionState.FAILED)
        after = legacy_status_usage()

        self.assertGreaterEqual(after["imports"], before["imports"] + 2)
        self.assertGreaterEqual(after["exports"], before["exports"] + 1)
        self.assertEqual(
            after["removal_gate"],
            "zero authoritative legacy reads and writes for one supported release",
        )
        after["imports"] = -1
        self.assertNotEqual(legacy_status_usage()["imports"], -1)

    def test_completion_compatibility_functions_delegate_to_boundary_adapter(self) -> None:
        before = legacy_status_usage()

        state = completion_state_from_legacy({"status": "needs_confirmation"})
        status = legacy_status_for_completion(CompletionState.COMPLETED)

        after = legacy_status_usage()
        self.assertIs(state, CompletionState.NEEDS_CONSENT)
        self.assertEqual(status, "answered")
        self.assertGreaterEqual(after["imports"], before["imports"] + 1)
        self.assertGreaterEqual(after["exports"], before["exports"] + 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
