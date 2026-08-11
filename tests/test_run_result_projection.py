"""The boundary projector: one place turns a verdict into a RunResult (#612, #618).

Every execution surface used to decide completion truth from its own slice of
evidence. These tests pin the contract the projector gives instead: the same
verdict plus the same evidence reference always yields the same canonical
result, missing evidence degrades honestly rather than fabricating a claim,
and a mutating task can never be marked completed without the verdict itself
having verified it.
"""

from __future__ import annotations

import unittest

from opaihub.completion import (
    AcceptanceRequirement,
    CompletionVerdict,
    CompletionVerdictResult,
    ObjectiveRecord,
)
from opaihub.run_result_projection import (
    project_run_result,
    project_run_result_for_background_run,
)


def _objective(*, edit_intent: bool = False) -> ObjectiveRecord:
    acceptance = (
        (AcceptanceRequirement.EXPECTED_EDIT,)
        if edit_intent
        else (AcceptanceRequirement.ANSWER_PRESENT,)
    )
    return ObjectiveRecord(
        objective_text="do the thing", mode="ask", acceptance=acceptance
    )


def _verdict(
    verdict: CompletionVerdict,
    *,
    reason_code: str = "test_reason",
    reason: str = "Because the test says so.",
    edit_intent: bool = False,
) -> CompletionVerdictResult:
    return CompletionVerdictResult(
        verdict=verdict,
        reason_code=reason_code,
        reason=reason,
        objective=_objective(edit_intent=edit_intent),
    )


class NonMutatingCompletionTests(unittest.TestCase):
    def test_a_completed_answer_only_verdict_has_not_applicable_verification(
        self,
    ) -> None:
        result = project_run_result(
            verdict=_verdict(CompletionVerdict.COMPLETED),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=False,
            delivery_ref=("receipt", "abc123"),
            economics_ref=("ledger", "evt-1"),
        )
        self.assertEqual(result.lifecycle["state"], "completed")
        self.assertEqual(result.verification["applicable"], False)
        self.assertEqual(result.verification["verdict"], "not_applicable")

    def test_a_completed_answer_degrades_without_delivery_or_economics_refs(
        self,
    ) -> None:
        # Honest, not a bug: RunResult itself requires delivery/economics
        # evidence for *any* completed state. No ref supplied means no claim
        # made — the projector must not invent one to make the state stick.
        result = project_run_result(
            verdict=_verdict(CompletionVerdict.COMPLETED),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=False,
        )
        self.assertEqual(result.lifecycle["state"], "needs_attention")

    def test_supplying_both_refs_lets_a_completed_answer_stand(self) -> None:
        result = project_run_result(
            verdict=_verdict(CompletionVerdict.COMPLETED),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=False,
            delivery_ref=("receipt", "abc123"),
            economics_ref=("ledger", "evt-1"),
        )
        self.assertEqual(result.lifecycle["state"], "completed")
        self.assertEqual(
            result.delivery["record_ref"], {"kind": "receipt", "id": "abc123"}
        )
        self.assertEqual(
            result.economics["record_ref"], {"kind": "ledger", "id": "evt-1"}
        )


class MutatingCompletionTests(unittest.TestCase):
    def test_a_completed_mutating_verdict_references_the_verdict_as_verification(
        self,
    ) -> None:
        result = project_run_result(
            verdict=_verdict(
                CompletionVerdict.COMPLETED, reason_code="tests_pass", edit_intent=True
            ),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=True,
            delivery_ref=("receipt", "abc123"),
            economics_ref=("ledger", "evt-1"),
        )
        self.assertEqual(result.lifecycle["state"], "completed")
        self.assertEqual(result.verification["verdict"], "verified")
        self.assertEqual(result.verification["record_ref"]["id"], "tests_pass")

    def test_the_verdict_is_the_only_verification_authority_never_fabricated_elsewhere(
        self,
    ) -> None:
        # The projector cannot be handed a "verified" claim directly - it only
        # ever derives verification from the verdict object itself.
        import inspect

        signature = inspect.signature(project_run_result)
        self.assertNotIn("verification", signature.parameters)


class NonCompletedTerminalTests(unittest.TestCase):
    def test_cancelled_requires_observed_teardown_evidence(self) -> None:
        result = project_run_result(
            verdict=_verdict(
                CompletionVerdict.CANCELLED, reason_code="cancelled_by_user"
            ),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=True,
        )
        self.assertEqual(result.lifecycle["state"], "needs_attention")
        self.assertEqual(result.recovery["reason"], "manual_review")

    def test_observed_teardown_preserves_cancelled_and_references_its_journal(
        self,
    ) -> None:
        result = project_run_result(
            verdict=_verdict(
                CompletionVerdict.CANCELLED, reason_code="cancelled_by_user"
            ),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=True,
            cancellation={"scope_id": "run-7", "phase": "terminated"},
        )
        self.assertEqual(result.lifecycle["state"], "cancelled")
        self.assertEqual(result.authority["cancellation"]["phase"], "terminated")
        self.assertEqual(
            result.authority["cancellation"]["record_ref"],
            {"kind": "cancellation_journal", "id": "run-7"},
        )

    def test_failed_never_requires_delivery_or_economics_evidence(self) -> None:
        result = project_run_result(
            verdict=_verdict(CompletionVerdict.FAILED, reason_code="provider_error"),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=True,
        )
        self.assertEqual(result.lifecycle["state"], "failed")

    def test_timeout_can_carry_an_automatic_retry_classification(self) -> None:
        result = project_run_result(
            verdict=_verdict(CompletionVerdict.TIMEOUT, reason_code="provider_timeout"),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=False,
            automatic_retry=True,
            retry_reason="timeout",
        )
        self.assertEqual(result.lifecycle["state"], "timeout")
        self.assertTrue(result.recovery["automatic_retry"])
        self.assertEqual(result.recovery["reason"], "timeout")

    def test_task_deadline_references_provenance_and_forbids_automatic_retry(
        self,
    ) -> None:
        result = project_run_result(
            verdict=_verdict(
                CompletionVerdict.TIMEOUT,
                reason_code="task_deadline",
                reason="Task deadline reached while work was active.",
            ),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=True,
            automatic_retry=True,
            retry_reason="timeout",
            timeout={
                "timeout_origin": "task_deadline",
                "owner": "account_runner",
                "provider_condition": "responsive",
                "retry_safety": "reconcile_before_retry",
            },
            timeout_ref=("usage_ledger", "17:taskhash"),
        )

        self.assertEqual(result.lifecycle["state"], "timeout")
        self.assertFalse(result.recovery["automatic_retry"])
        self.assertEqual(result.recovery["reason"], "manual_review")
        self.assertEqual(result.authority["timeout"]["origin"], "task_deadline")
        self.assertEqual(
            result.authority["timeout"]["record_ref"],
            {"kind": "usage_ledger", "id": "17:taskhash"},
        )
        self.assertIn("task_deadline", result.diagnostics["codes"])

    def test_completed_can_never_carry_automatic_retry(self) -> None:
        # RunResult itself refuses this combination; the projector's own
        # contract is to never raise it at the caller, only degrade to an
        # honest needs_attention — exactly like a missing evidence reference.
        result = project_run_result(
            verdict=_verdict(CompletionVerdict.COMPLETED),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=False,
            delivery_ref=("receipt", "abc"),
            economics_ref=("ledger", "evt"),
            automatic_retry=True,
            retry_reason="timeout",
        )
        self.assertEqual(result.lifecycle["state"], "needs_attention")


class DeterminismTests(unittest.TestCase):
    def test_the_same_inputs_always_project_the_same_result(self) -> None:
        kwargs = dict(
            verdict=_verdict(CompletionVerdict.PARTIAL, reason_code="workflow_partial"),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=True,
            task_id="task-1",
            run_id="run-1",
        )
        first = project_run_result(**kwargs)
        second = project_run_result(**kwargs)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_project_run_result_rejects_a_non_verdict_result(self) -> None:
        with self.assertRaises(TypeError):
            project_run_result(
                verdict={"verdict": "completed"},  # type: ignore[arg-type]
                final_transition_at="2026-08-03T10:00:00+00:00",
                mutating=False,
            )


class BackgroundRunProjectionTests(unittest.TestCase):
    def test_a_completed_background_run_references_its_own_persisted_record(
        self,
    ) -> None:
        result = project_run_result_for_background_run(
            verdict=_verdict(CompletionVerdict.COMPLETED, edit_intent=True),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=True,
            task_id="task-7",
            run_id="run-7",
        )
        self.assertEqual(result.lifecycle["state"], "completed")
        self.assertEqual(
            result.delivery["record_ref"], {"kind": "background_run", "id": "run-7"}
        )
        self.assertEqual(
            result.economics["record_ref"], {"kind": "background_run", "id": "run-7"}
        )
        self.assertEqual(result.identity["run_id"], "run-7")

    def test_a_failed_background_run_can_be_marked_retry_safe(self) -> None:
        result = project_run_result_for_background_run(
            verdict=_verdict(
                CompletionVerdict.FAILED, reason_code="provider_unavailable"
            ),
            final_transition_at="2026-08-03T10:00:00+00:00",
            mutating=False,
            task_id="task-8",
            run_id="run-8",
            automatic_retry=True,
            retry_reason="provider_transient",
        )
        self.assertEqual(result.lifecycle["state"], "failed")
        self.assertTrue(result.recovery["automatic_retry"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
