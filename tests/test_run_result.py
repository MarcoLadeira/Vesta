"""Canonical terminal RunResult contract (#612, #618)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import unittest

from opaihub.run_result import RunResult


FINAL_AT = "2026-08-02T12:34:56Z"


def _reference(kind: str) -> dict[str, str]:
    return {"kind": kind, "id": f"{kind}-record-1"}


def _completed_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "state": "completed",
        "reason": "terminal_resolution",
        "reason_detail": "Objective evidence reconciled.",
        "final_transition_at": FINAL_AT,
        "reconciled": True,
        "mutating": True,
        "identity": {"run_id": "run-1", "attempt_id": "attempt-1"},
        "provider": {"record_ref": _reference("provider")},
        "recovery": {"automatic_retry": False, "reason": "none"},
        "verification": {
            "applicable": True,
            "verdict": "verified",
            "record_ref": _reference("verification"),
        },
        "delivery": {
            "applicable": True,
            "verdict": "delivered",
            "record_ref": _reference("delivery"),
        },
        "economics": {
            "integrity": "reconciled",
            "record_ref": _reference("economics"),
        },
        "authority": {
            "mutating": True,
            "record_ref": _reference("authority"),
        },
        "diagnostics": {"record_refs": [_reference("diagnostic")]},
    }
    payload.update(overrides)
    return payload


class RunResultTests(unittest.TestCase):
    def test_completed_mutation_requires_reconciled_verification(self) -> None:
        payload = _completed_payload(
            verification={
                "applicable": True,
                "verdict": "failed",
                "record_ref": _reference("verification"),
            }
        )

        with self.assertRaisesRegex(ValueError, "verification"):
            RunResult.from_payload(**payload)

    def test_completed_mutation_requires_reconciled_delivery(self) -> None:
        payload = _completed_payload(
            delivery={
                "applicable": True,
                "verdict": "pending",
                "record_ref": _reference("delivery"),
            }
        )

        with self.assertRaisesRegex(ValueError, "delivery"):
            RunResult.from_payload(**payload)

    def test_completed_requires_cost_integrity_evidence(self) -> None:
        payload = _completed_payload(
            economics={
                "integrity": "estimated",
                "record_ref": _reference("economics"),
            }
        )

        with self.assertRaisesRegex(ValueError, "cost integrity"):
            RunResult.from_payload(**payload)

    def test_terminal_result_requires_reason_timestamp_and_reconciliation(self) -> None:
        cases = (
            ({"reason_detail": ""}, "reason detail"),
            ({"final_transition_at": ""}, "final transition timestamp"),
            ({"reconciled": False}, "reconciliation"),
        )
        for replacement, message in cases:
            with self.subTest(replacement=replacement):
                payload = _completed_payload(**replacement)
                with self.assertRaisesRegex(ValueError, message):
                    RunResult.from_payload(**payload)

    def test_payload_boolean_contract_fields_are_not_truthy_coerced(self) -> None:
        with self.assertRaisesRegex(TypeError, "mutating"):
            RunResult.from_payload(**_completed_payload(mutating="yes"))
        with self.assertRaisesRegex(TypeError, "reconciled"):
            RunResult.from_payload(**_completed_payload(reconciled="yes"))

    def test_from_dict_rejects_integer_mutating_as_a_boolean_impostor(self) -> None:
        payload = RunResult.from_payload(**_completed_payload()).to_dict()
        payload["authority"]["mutating"] = 1
        payload["verification"] = {}

        with self.assertRaisesRegex(TypeError, "mutating"):
            RunResult.from_dict(payload)

    def test_from_payload_rejects_authority_boolean_impostor(self) -> None:
        payload = _completed_payload(authority={"mutating": 1})

        with self.assertRaisesRegex(TypeError, "mutating"):
            RunResult.from_payload(**payload)

    def test_result_envelope_is_terminal_only(self) -> None:
        payload = _completed_payload(state="cancel_requested")

        with self.assertRaisesRegex(ValueError, "terminal"):
            RunResult.from_payload(**payload)

    def test_cancelled_requires_reconciliation(self) -> None:
        payload = _completed_payload(
            state="cancelled",
            reconciled=False,
            verification={},
            delivery={},
            economics={},
        )

        with self.assertRaisesRegex(ValueError, "reconciliation"):
            RunResult.from_payload(**payload)

    def test_unknown_retry_reason_cannot_enable_automatic_retry(self) -> None:
        payload = _completed_payload(
            state="failed",
            recovery={"automatic_retry": True, "reason": "future_vendor_retry"},
            verification={},
            delivery={},
            economics={},
        )

        with self.assertRaisesRegex(ValueError, "retry reason"):
            RunResult.from_payload(**payload)

    def test_unknown_disabled_retry_reason_is_normalized_to_manual_review(self) -> None:
        payload = _completed_payload(
            state="failed",
            recovery={"automatic_retry": False, "reason": "future_vendor_retry"},
            verification={},
            delivery={},
            economics={},
        )

        result = RunResult.from_payload(**payload)

        self.assertFalse(result.recovery["automatic_retry"])
        self.assertEqual(result.recovery["reason"], "manual_review")

    def test_automatic_retry_requires_a_real_boolean(self) -> None:
        payload = _completed_payload(
            state="failed",
            recovery={"automatic_retry": "false", "reason": "network"},
            verification={},
            delivery={},
            economics={},
        )

        with self.assertRaisesRegex(TypeError, "automatic_retry"):
            RunResult.from_payload(**payload)

    def test_completed_result_cannot_schedule_a_terminal_timeout_retry(self) -> None:
        payload = _completed_payload(
            recovery={"automatic_retry": True, "reason": "timeout"}
        )

        with self.assertRaisesRegex(ValueError, "automatic retry"):
            RunResult.from_payload(**payload)

    def test_incompatible_terminal_result_cannot_schedule_automatic_retry(self) -> None:
        payload = RunResult.from_payload(
            state="needs_attention",
            reason_detail="Unknown input.",
            final_transition_at=FINAL_AT,
            recovery={"automatic_retry": False, "reason": "manual_review"},
            compatibility={
                "state": "incompatible",
                "source_schema_version": 99,
                "automatic_retry": False,
            },
        ).to_dict()
        payload["recovery"] = {"automatic_retry": True, "reason": "network"}

        with self.assertRaisesRegex(ValueError, "automatic retry"):
            RunResult.from_dict(payload)

    def test_incompatible_metadata_cannot_claim_automatic_retry(self) -> None:
        payload = RunResult.from_payload(
            state="needs_attention",
            reason_detail="Unknown input.",
            final_transition_at=FINAL_AT,
            compatibility={
                "state": "incompatible",
                "source_schema_version": 99,
                "automatic_retry": False,
            },
        ).to_dict()
        payload["compatibility"]["automatic_retry"] = True

        with self.assertRaisesRegex(ValueError, "automatic retry"):
            RunResult.from_dict(payload)

    def test_failed_result_may_reference_a_safe_new_attempt_retry(self) -> None:
        result = RunResult.from_payload(
            **_completed_payload(
                state="failed",
                recovery={"automatic_retry": True, "reason": "network"},
                verification={},
                delivery={},
                economics={},
            )
        )

        self.assertTrue(result.recovery["automatic_retry"])
        self.assertEqual(result.recovery["reason"], "network")

    def test_provider_evidence_is_a_reference_not_a_mutable_snapshot(self) -> None:
        payload = _completed_payload(
            provider={
                "record_ref": _reference("provider"),
                "snapshot": {"status": "completed", "tokens": 42},
            }
        )

        with self.assertRaisesRegex(ValueError, "snapshot"):
            RunResult.from_payload(**payload)

    def test_provider_metadata_requires_a_stable_record_reference(self) -> None:
        payload = _completed_payload(provider={"provider_id": "vendor-model"})

        with self.assertRaisesRegex(ValueError, "record reference"):
            RunResult.from_payload(**payload)

    def test_reference_values_must_be_nonempty_scalars(self) -> None:
        payload = _completed_payload(
            provider={"record_ref": {"kind": "provider", "id": {"nested": "x"}}}
        )

        with self.assertRaisesRegex(ValueError, "scalar"):
            RunResult.from_payload(**payload)

    def test_reference_shape_rejects_unlisted_nested_metadata(self) -> None:
        payload = _completed_payload(
            verification={
                "applicable": True,
                "verdict": "verified",
                "record_ref": {
                    "kind": "verification",
                    "id": "verify-1",
                    "metadata": {"output": "raw provider output"},
                },
            }
        )

        with self.assertRaisesRegex(ValueError, "reference"):
            RunResult.from_payload(**payload)

    def test_digest_without_record_id_path_or_uri_is_not_a_reference(self) -> None:
        payload = _completed_payload(
            provider={"record_ref": {"digest": "a" * 64}}
        )

        with self.assertRaisesRegex(ValueError, "reference"):
            RunResult.from_payload(**payload)

    def test_provider_rejects_raw_output_payload_and_metadata_at_any_depth(self) -> None:
        raw_values = (
            {"record_ref": _reference("provider"), "output": "raw"},
            {"record_ref": _reference("provider"), "payload": {"text": "raw"}},
            {
                "record_ref": _reference("provider"),
                "metadata": {"nested": {"response": "raw"}},
            },
        )
        for provider in raw_values:
            with self.subTest(provider=provider):
                with self.assertRaisesRegex(ValueError, "provider"):
                    RunResult.from_payload(
                        **_completed_payload(provider=provider)
                    )

    def test_terminal_timestamp_must_be_timezone_qualified_iso_8601(self) -> None:
        payload = _completed_payload(final_transition_at="yesterday")

        with self.assertRaisesRegex(ValueError, "timestamp"):
            RunResult.from_payload(**payload)

    def test_non_mutating_completion_records_verification_not_applicable(self) -> None:
        payload = _completed_payload(
            mutating=False,
            authority={"mutating": False},
            verification={},
        )

        with self.assertRaisesRegex(ValueError, "verification"):
            RunResult.from_payload(**payload)

    def test_valid_result_is_deeply_immutable_and_serializes_plain_data(self) -> None:
        payload = _completed_payload()
        result = RunResult.from_payload(**payload)
        payload["identity"]["run_id"] = "changed"  # type: ignore[index]

        self.assertEqual(result.identity["run_id"], "run-1")
        self.assertEqual(result.lifecycle["state"], "completed")
        self.assertEqual(result.presentation["category"], "success")
        self.assertEqual(result.to_dict()["identity"]["run_id"], "run-1")
        self.assertEqual(result.to_json(), result.to_json())
        with self.assertRaises(TypeError):
            result.identity["run_id"] = "changed"  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            result.schema_version = 99  # type: ignore[misc]

    def test_unknown_state_degrades_to_typed_incompatible_result(self) -> None:
        payload = _completed_payload(state="future_terminal")

        result = RunResult.from_payload(**payload)

        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.compatibility["state"], "incompatible")
        self.assertFalse(result.recovery["automatic_retry"])

    def test_unknown_state_with_malformed_incidental_data_still_degrades(self) -> None:
        payload = _completed_payload(
            state="future_terminal",
            final_transition_at="not-a-time",
            identity={"unsafe": object()},
        )

        result = RunResult.from_payload(**payload)

        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.identity, {})

    def test_unknown_schema_version_degrades_without_preserving_success(self) -> None:
        payload = RunResult.from_payload(**_completed_payload()).to_dict()
        payload["schema_version"] = 99

        result = RunResult.from_dict(payload)

        self.assertEqual(result.schema_version, 1)
        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.compatibility["state"], "incompatible")
        self.assertEqual(result.compatibility["source_schema_version"], 99)
        self.assertFalse(result.recovery["automatic_retry"])

    def test_structured_future_schema_identifier_also_degrades_safely(self) -> None:
        payload = RunResult.from_payload(**_completed_payload()).to_dict()
        payload["schema_version"] = {"future": 2}

        result = RunResult.from_dict(payload)

        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.compatibility["state"], "incompatible")
        self.assertEqual(result.compatibility["source_schema_version"], "dict")

    def test_non_finite_future_schema_identifier_degrades_safely(self) -> None:
        payload = RunResult.from_payload(**_completed_payload()).to_dict()
        payload["schema_version"] = float("inf")

        result = RunResult.from_dict(payload)

        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.compatibility["source_schema_version"], "float")

    def test_previous_schema_round_trip_is_byte_stable(self) -> None:
        previous_fixture = {
            "schema_version": 0,
            "identity": {"attempt_id": "attempt-v0", "run_id": "run-v0"},
            "lifecycle": {
                "final_transition_at": "2026-07-01T00:00:00Z",
                "reason": "terminal_resolution",
                "reason_detail": "Provider failure was reconciled.",
                "reconciliation": "reconciled",
                "state": "failed",
            },
            "provider": {"record_ref": {"id": "provider-v0", "kind": "provider"}},
            "recovery": {"automatic_retry": False, "reason": "none"},
            "verification": {},
            "delivery": {},
            "economics": {},
            "authority": {"mutating": False},
            "diagnostics": {"record_refs": []},
            "presentation": {"category": "error", "label": "Failed"},
            "compatibility": {
                "automatic_retry": False,
                "source_schema_version": 0,
                "state": "compatible_previous",
            },
        }
        expected_json = json.dumps(
            previous_fixture,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

        self.assertEqual(RunResult.from_dict(previous_fixture).to_json(), expected_json)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
