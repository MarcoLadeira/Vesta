"""Silence is not proof that an external effect did not happen (#616).

OPai retries the identical request on a transport error. Three of the codes
that trigger it — ``PROVIDER_TIMEOUT``, ``STREAM_ABORTED``, ``NO_RESPONSE`` —
are precisely the ones #616 says prove nothing:

    A timeout, exception, process crash or missing response is not proof
    that an external action did not start or finish.

These tests pin the judgement that closes that gap: an operation is repeated
automatically only when repeating it is harmless, or when the failure proves
nothing left the machine.
"""

from __future__ import annotations

import unittest

from opaihub.operation_class import (
    UNKNOWN_KIND_CLASS,
    DispatchProof,
    OperationClass,
    classify_operation,
    dispatch_proof,
    model_call_kind,
    retry_decision,
)


class DispatchProofTests(unittest.TestCase):
    """What a failure actually establishes about delivery."""

    def test_a_refused_connection_proves_nothing_was_sent(self) -> None:
        self.assertIs(
            dispatch_proof("PROVIDER_UNAVAILABLE"), DispatchProof.NOT_DISPATCHED
        )

    def test_an_aborted_stream_proves_the_provider_started_working(self) -> None:
        # Tokens were already coming back, so the provider generated — and
        # bills for — real output. This is the strongest case against retry.
        self.assertIs(dispatch_proof("STREAM_ABORTED"), DispatchProof.DISPATCHED)

    def test_the_ambiguous_codes_stay_ambiguous(self) -> None:
        # The heart of #616: none of these are evidence of non-delivery.
        for code in ("PROVIDER_TIMEOUT", "NO_RESPONSE", "NETWORK_ERROR"):
            self.assertIs(dispatch_proof(code), DispatchProof.UNKNOWN, code)

    def test_an_unclassified_code_is_not_mistaken_for_safety(self) -> None:
        self.assertIs(dispatch_proof("WHO_KNOWS"), DispatchProof.UNKNOWN)
        self.assertIs(dispatch_proof(""), DispatchProof.UNKNOWN)

    def test_codes_are_matched_regardless_of_case_or_padding(self) -> None:
        self.assertIs(
            dispatch_proof("  provider_unavailable  "), DispatchProof.NOT_DISPATCHED
        )


class ClassificationTests(unittest.TestCase):
    def test_an_unregistered_operation_gets_the_most_restrictive_class(self) -> None:
        # #616: "Unknown defaults to the restrictive class for paid, mutating
        # or irreversible work." A new outward effect must not fail open just
        # because someone forgot to register it.
        self.assertIs(classify_operation("some_new_tool"), UNKNOWN_KIND_CLASS)
        self.assertIs(UNKNOWN_KIND_CLASS, OperationClass.CONTINUITY_UNCERTAIN)

    def test_reads_are_safely_repeatable(self) -> None:
        for kind in ("read_file", "git_status", "github_get_issue"):
            self.assertIs(
                classify_operation(kind), OperationClass.SAFELY_REPEATABLE, kind
            )

    def test_arbitrary_commands_are_never_auto_repeated(self) -> None:
        # `run_command` can be `npm publish`. Nothing can classify it safely.
        self.assertIs(
            classify_operation("run_command"), OperationClass.CONTINUITY_UNCERTAIN
        )

    def test_free_and_paid_inference_are_different_operations(self) -> None:
        self.assertEqual(model_call_kind(is_free=True), "model_call_free")
        self.assertEqual(model_call_kind(is_free=False), "model_call_paid")
        self.assertIsNot(
            classify_operation("model_call_free"),
            classify_operation("model_call_paid"),
        )

    def test_outward_github_writes_are_not_plain_reconcilable(self) -> None:
        # A duplicate comment or PR is visible to everyone and not undoable by
        # OPai, so it must rely on a dedupe key rather than on observation.
        for kind in ("github_comment", "open_pr"):
            self.assertIs(
                classify_operation(kind), OperationClass.PROVIDER_IDEMPOTENT, kind
            )


class PaidInferenceRetryTests(unittest.TestCase):
    """The defect #616 exists to prevent: a second charge nobody authorised."""

    def test_a_timed_out_paid_call_is_not_repeated(self) -> None:
        decision = retry_decision("model_call_paid", error_code="PROVIDER_TIMEOUT")
        self.assertFalse(decision.allowed)
        self.assertIn("unknown", decision.reason)

    def test_an_aborted_paid_stream_is_not_repeated(self) -> None:
        decision = retry_decision("model_call_paid", error_code="STREAM_ABORTED")
        self.assertFalse(decision.allowed)
        self.assertIn("already dispatched", decision.reason)

    def test_a_missing_response_on_a_paid_call_is_not_repeated(self) -> None:
        self.assertFalse(retry_decision("model_call_paid", error_code="NO_RESPONSE"))

    def test_a_paid_call_refused_before_dispatch_may_be_repeated(self) -> None:
        # The rule is not "never retry paid work" — it is "retry only on
        # evidence". A 503 refused at the door is that evidence.
        decision = retry_decision("model_call_paid", error_code="PROVIDER_UNAVAILABLE")
        self.assertTrue(decision.allowed)
        self.assertIs(decision.proof, DispatchProof.NOT_DISPATCHED)


class FreeInferenceRetryTests(unittest.TestCase):
    """The flaky-endpoint retry that makes free models usable must survive."""

    def test_a_timed_out_free_call_is_still_repeated(self) -> None:
        # Free inference costs nothing and leaves no outward trace, so the
        # ambiguity that blocks a paid retry is harmless here. Losing this
        # would regress the intermittent-503 recovery it was built for.
        self.assertTrue(
            retry_decision("model_call_free", error_code="PROVIDER_TIMEOUT")
        )

    def test_even_an_aborted_free_stream_is_repeated(self) -> None:
        self.assertTrue(retry_decision("model_call_free", error_code="STREAM_ABORTED"))


class BudgetTests(unittest.TestCase):
    def test_the_budget_is_checked_before_any_class_reasoning(self) -> None:
        # Otherwise a SAFELY_REPEATABLE operation would retry forever.
        self.assertFalse(
            retry_decision("read_file", error_code="NETWORK_ERROR", attempts=1)
        )

    def test_an_exhausted_budget_says_so_rather_than_blaming_the_operation(
        self,
    ) -> None:
        decision = retry_decision("model_call_free", attempts=1, max_attempts=1)
        self.assertFalse(decision.allowed)
        self.assertIn("budget", decision.reason)

    def test_a_larger_budget_permits_a_further_attempt(self) -> None:
        self.assertTrue(retry_decision("model_call_free", attempts=1, max_attempts=2))


class ContractTests(unittest.TestCase):
    def test_a_decision_is_truthy_exactly_when_it_allows(self) -> None:
        # Call sites read `if retry_decision(...)`, so the bool must not lie.
        self.assertTrue(bool(retry_decision("read_file")))
        self.assertFalse(bool(retry_decision("run_command")))

    def test_every_decision_explains_itself(self) -> None:
        # These reasons surface to the user when a retry is withheld; an empty
        # one would turn a deliberate refusal into an unexplained stall.
        for kind in ("read_file", "run_command", "model_call_paid", "unknown_thing"):
            for code in ("", "PROVIDER_TIMEOUT", "STREAM_ABORTED"):
                decision = retry_decision(kind, error_code=code)
                self.assertTrue(decision.reason.strip(), f"{kind}/{code}")

    def test_a_denied_decision_still_reports_what_it_knew(self) -> None:
        decision = retry_decision("model_call_paid", error_code="STREAM_ABORTED")
        self.assertIs(
            decision.operation_class, OperationClass.NEEDS_PROOF_OF_NO_DISPATCH
        )
        self.assertIs(decision.proof, DispatchProof.DISPATCHED)

    def test_no_registered_operation_is_left_unclassified(self) -> None:
        # Guards against a typo'd enum value silently becoming a string.
        from opaihub.operation_class import _REGISTRY

        for kind, value in _REGISTRY.items():
            self.assertIsInstance(value, OperationClass, kind)


if __name__ == "__main__":
    unittest.main()
