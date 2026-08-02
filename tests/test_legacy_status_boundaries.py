"""Legacy status strings remain observable boundary compatibility only."""

from __future__ import annotations

import unittest

from opaihub.completion import (
    CompletionState,
    completion_state_from_legacy,
    legacy_status_for_completion,
)
from opaihub.legacy_status import (
    legacy_status_for_completion_state,
    legacy_status_to_result,
    legacy_status_usage,
)


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

    def test_unknown_schema_version_precedes_answered_and_cancelled_strings(self) -> None:
        result = legacy_status_to_result(
            {
                "schema_version": 999,
                "status": "answered",
                "stopped_reason": "cancelled",
                "verification": {
                    "applicable": False,
                    "verdict": "not_applicable",
                },
                "delivery": {
                    "applicable": True,
                    "verdict": "delivered",
                    "record_ref": {"kind": "delivery", "id": "delivery-1"},
                },
                "economics": {
                    "integrity": "reconciled",
                    "record_ref": {"kind": "economics", "id": "economics-1"},
                },
            }
        )

        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.compatibility["state"], "incompatible")
        self.assertEqual(result.compatibility["source_schema_version"], 999)
        self.assertFalse(result.recovery["automatic_retry"])

    def test_cancelled_stop_reason_precedes_answered_status(self) -> None:
        result = legacy_status_to_result(
            {
                "schema_version": 0,
                "status": "answered",
                "stopped_reason": "cancelled",
                "finished_at": "2026-08-02T12:34:56Z",
            }
        )

        self.assertEqual(result.schema_version, 0)
        self.assertEqual(result.lifecycle["state"], "cancelled")
        self.assertEqual(result.compatibility["state"], "legacy_import")
        self.assertEqual(result.compatibility["source_schema_version"], 0)

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

    def test_unknown_legacy_input_and_output_are_explicitly_incompatible(self) -> None:
        imported = completion_state_from_legacy({"status": "future_vendor_state"})
        exported = legacy_status_for_completion_state("future_terminal_state")
        public_export = legacy_status_for_completion("future_terminal_state")

        self.assertIs(imported, CompletionState.NEEDS_ATTENTION)
        self.assertEqual(exported, "needs_attention")
        self.assertEqual(public_export, "needs_attention")

    def test_canonical_compatibility_values_are_total_and_truth_preserving(self) -> None:
        expected = {
            "awaiting_input": CompletionState.AWAITING_INPUT,
            "blocked": CompletionState.BLOCKED,
            "cancelled": CompletionState.CANCELLED,
            "completed": CompletionState.COMPLETED,
            "failed": CompletionState.FAILED,
            "needs_attention": CompletionState.NEEDS_ATTENTION,
            "partial": CompletionState.PARTIAL,
            "timeout": CompletionState.TIMEOUT,
        }

        for state, completion_state in expected.items():
            with self.subTest(state=state):
                self.assertIs(
                    completion_state_from_legacy({"completion_state": state}),
                    completion_state,
                )

        outputs = {
            "awaiting_input": "needs_user_input",
            "blocked": "provider_blocked",
            "cancelled": "cancelled",
            "completed": "answered",
            "failed": "failed",
            "needs_attention": "needs_attention",
            "partial": "incomplete",
            "timeout": "timeout",
        }
        for state, status in outputs.items():
            with self.subTest(output_state=state):
                self.assertEqual(legacy_status_for_completion_state(state), status)

    def test_active_canonical_values_degrade_without_crashing(self) -> None:
        for state in (
            "queued",
            "preparing",
            "running",
            "verifying",
            "cancel_requested",
        ):
            with self.subTest(state=state):
                self.assertIs(
                    completion_state_from_legacy({"completion_state": state}),
                    CompletionState.NEEDS_ATTENTION,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
