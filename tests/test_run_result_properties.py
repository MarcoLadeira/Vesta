"""#618: properties of the canonical result, over generated evidence.

The 34-case matrix pins the endings a reader can name. These properties cover
the ones nobody thought to name -- the combinations that arise when a provider
half-answers while a deadline expires and cost never arrives. Every defect this
issue fixed was of exactly that shape: not a scenario anyone designed, but a
fall-through nobody noticed.

Each property is stated as an invariant over arbitrary evidence rather than a
worked example, so a future change that breaks it fails here even if it breaks
it in a combination no fixture anticipated.

Executed at 10_000 examples per property (see PROFILE below).
"""

from __future__ import annotations

import unittest

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vestahub.generated_lifecycle import (
    DEGRADED_INPUTS,
    STATE_SPECS,
    TERMINAL_STATE_IDS,
)
from vestahub.legacy_status import legacy_status_to_result
from vestahub.run_result import RunResult

#: #618 requires >=10,000 executed examples. Deadline is disabled because the
#: builder does real schema validation; a slow machine must not turn a
#: correctness suite into a flaky one.
PROFILE = settings(
    max_examples=10_000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

TERMINAL = sorted(TERMINAL_STATE_IDS)
RETRY_REASONS = ("network", "provider_transient", "rate_limit", "timeout")
VERIFICATION_VERDICTS = (
    "verified",
    "passed",
    "failed",
    "blocked",
    "cancelled",
    "unavailable",
    "not_applicable",
)
DELIVERY_VERDICTS = (
    "delivered",
    "verified",
    "failed",
    "partially_delivered",
    "unknown",
)
COST_INTEGRITY = ("reconciled", "verified", "unavailable", "estimated", "pending")

LEGACY_STATUSES = (
    "answered",
    "answered_by_account",
    "answered_by_free_api",
    "answered_locally",
    "applied",
    "no_edits",
    "cache_hit",
    "error",
    "failed",
    "cancelled",
    "blocked",
    "timeout",
)


def _reference(kind: str, ident: str) -> dict:
    return {"kind": kind, "id": ident}


@st.composite
def evidence(draw) -> dict:
    """Arbitrary, mostly-invalid result evidence.

    Deliberately not constrained to buildable inputs: the point is that the
    builder either produces a schema-valid result or refuses, and never yields
    a result that quietly overstates what happened.
    """

    state = draw(st.sampled_from(TERMINAL))
    mutating = draw(st.booleans())
    payload: dict = {
        "state": state,
        "reason_detail": draw(
            st.text(min_size=1, max_size=40).filter(lambda t: t.strip())
        ),
        "final_transition_at": "2026-08-11T12:00:00+00:00",
        "mutating": mutating,
    }
    if draw(st.booleans()):
        payload["verification"] = {
            "applicable": draw(st.booleans()),
            "verdict": draw(st.sampled_from(VERIFICATION_VERDICTS)),
            "record_ref": _reference("completion_verdict", "v"),
        }
    if draw(st.booleans()):
        payload["delivery"] = {
            "applicable": draw(st.booleans()),
            "verdict": draw(st.sampled_from(DELIVERY_VERDICTS)),
            "record_ref": _reference("pull_request", "1"),
        }
    if draw(st.booleans()):
        payload["economics"] = {
            "integrity": draw(st.sampled_from(COST_INTEGRITY)),
            "record_ref": _reference("ledger_event", "e"),
        }
    if draw(st.booleans()):
        payload["recovery"] = {
            "automatic_retry": draw(st.booleans()),
            "reason": draw(st.sampled_from((*RETRY_REASONS, "none", "manual_review"))),
        }
    return payload


def _build(payload: dict) -> RunResult | None:
    try:
        return RunResult.from_payload(**payload)
    except (TypeError, ValueError):
        return None


class CanonicalResultProperties(unittest.TestCase):
    """Invariants R1-R20 as properties over generated evidence."""

    @PROFILE
    @given(evidence())
    def test_p1_a_mutating_completed_result_always_has_passing_verification(
        self, payload: dict
    ) -> None:
        built = _build(payload)
        if built is None:
            return
        data = built.to_dict()
        if data["lifecycle"]["state"] != "completed":
            return
        verification = data.get("verification") or {}
        verdict = verification.get("verdict")
        if data["authority"]["mutating"]:
            # R1. The builder enforces this ("completed mutating result
            # requires reconciled verification evidence"), so a mutating run
            # cannot be recorded as complete on the strength of the model
            # having replied.
            self.assertIn(
                verdict,
                ("verified", "passed"),
                "a mutating run completed without verification passing",
            )
        else:
            # An answer-only run may legitimately declare verification
            # not_applicable *or* run checks anyway and pass them. Hypothesis
            # found this: an earlier version of this property demanded
            # not_applicable and was simply wrong about the contract. What must
            # never appear is a *failing* verdict on a completed run.
            self.assertIn(
                verdict,
                ("not_applicable", "verified", "passed"),
                "a completed run carries a verification verdict that "
                "contradicts its own completion",
            )

    @PROFILE
    @given(evidence())
    def test_p6_and_p10_a_completed_result_never_carries_unknown_cost(
        self, payload: dict
    ) -> None:
        built = _build(payload)
        if built is None:
            return
        data = built.to_dict()
        if data["lifecycle"]["state"] != "completed":
            return
        economics = data.get("economics") or {}
        self.assertIn(
            economics.get("integrity"),
            ("reconciled", "verified"),
            "a completed run reported spend it could not account for",
        )

    @PROFILE
    @given(evidence())
    def test_p15_and_p16_a_completed_result_never_overstates_delivery(
        self, payload: dict
    ) -> None:
        built = _build(payload)
        if built is None:
            return
        data = built.to_dict()
        if data["lifecycle"]["state"] != "completed":
            return
        delivery = data.get("delivery") or {}
        self.assertIn(
            delivery.get("verdict"),
            ("delivered", "verified"),
            "a completed run claimed a delivery it did not achieve",
        )

    @PROFILE
    @given(evidence())
    def test_p5_and_p13_automatic_retry_requires_a_known_safe_reason(
        self, payload: dict
    ) -> None:
        built = _build(payload)
        if built is None:
            return
        data = built.to_dict()
        recovery = data["recovery"]
        if not recovery["automatic_retry"]:
            return
        self.assertIn(
            recovery["reason"],
            RETRY_REASONS,
            "an unknown reason enabled an automatic retry",
        )
        self.assertTrue(
            STATE_SPECS[data["lifecycle"]["state"]]["automatic_retry_eligible"],
            "a state the schema forbids retrying was retried automatically",
        )

    @PROFILE
    @given(evidence())
    def test_p11_serialization_round_trips_exactly(self, payload: dict) -> None:
        built = _build(payload)
        if built is None:
            return
        once = built.to_dict()
        twice = RunResult.from_dict(once).to_dict()
        self.assertEqual(once, twice)

    @PROFILE
    @given(evidence())
    def test_every_built_result_is_terminal_and_named(self, payload: dict) -> None:
        built = _build(payload)
        if built is None:
            return
        data = built.to_dict()
        state = data["lifecycle"]["state"]
        self.assertIn(state, TERMINAL_STATE_IDS)
        self.assertTrue(STATE_SPECS[state]["terminal"])
        self.assertTrue(str(data["lifecycle"]["reason_detail"]).strip())


class LegacyImportProperties(unittest.TestCase):
    """P9/P12/P13: route, vintage and unknown values cannot buy success."""

    @PROFILE
    @given(
        st.fixed_dictionaries(
            {
                "status": st.one_of(
                    st.sampled_from(LEGACY_STATUSES),
                    st.text(max_size=24),
                ),
            }
        ),
        st.sampled_from(("claude", "codex", "gemini", "deepseek", "local", "")),
    )
    def test_p9_and_p13_no_legacy_status_or_provider_yields_completion(
        self, payload: dict, provider: str
    ) -> None:
        """A legacy record may describe a finished run. It cannot certify one.

        The provider is varied alongside because #618 requires identity to ride
        in the result without deciding it: the same legacy status must import
        the same way whether Claude, a local model, or nothing produced it.
        """

        enriched = dict(payload)
        if provider:
            enriched["provider"] = provider
        result = legacy_status_to_result(enriched)
        state = result.to_dict()["lifecycle"]["state"]
        self.assertIn(state, TERMINAL_STATE_IDS)
        self.assertNotEqual(
            state,
            "completed",
            "a legacy status alone certified engineering completion",
        )

    @PROFILE
    @given(st.integers(min_value=-5, max_value=99))
    def test_p12_an_unknown_schema_version_never_becomes_success(
        self, version: int
    ) -> None:
        result = legacy_status_to_result(
            {"status": "answered", "schema_version": version}
        )
        data = result.to_dict()
        self.assertNotEqual(data["lifecycle"]["state"], "completed")
        self.assertFalse(data["recovery"]["automatic_retry"])

    def test_the_degraded_inputs_contract_agrees_with_the_schema(self) -> None:
        """#612 declares how each degraded input must resolve; honour it."""

        for name, expected in DEGRADED_INPUTS.items():
            with self.subTest(degraded=name):
                self.assertNotEqual(expected["state"], "completed")
                self.assertFalse(expected["automatic_retry"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
