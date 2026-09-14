"""Canonical completion truth for runners, pipelines, and the UI."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile

import pytest

from _helpers import FakeStreamingRunner, make_repo

from opaihub.completion import (
    AcceptanceRequirement,
    CompletionResult,
    CompletionState,
    CompletionVerdict,
    FailureReason,
    ProviderBlockedReason,
    answer_contradicts_verdict,
    classify_failure_reason,
    completion_state_from_legacy,
    evaluate_completion,
    legacy_status_for_completion,
    objective_from_request,
    result_is_completed,
    result_meets_objective,
)


def test_result_meets_objective_is_stricter_than_canonical_completion() -> None:
    # #381: a run can be canonically completed (answered, no stop reason) yet
    # not meet its objective (no diff evidence) — a PARTIAL verdict. Savings are
    # gated on meeting the objective, so this run must not claim them.
    objective = objective_from_request("Fix the bug.", mode="implement")
    answered_no_diff = {"status": "answered", "answer": "I looked at it."}

    assert result_is_completed(answered_no_diff) is True
    assert result_meets_objective(objective, answered_no_diff) is False

    verified = {
        "status": "answered",
        "answer": "Fixed.",
        "changed_files": ["bug.py"],
    }
    assert result_meets_objective(objective, verified) is True


def test_edit_objective_only_completes_with_diff_and_test_evidence() -> None:
    objective = objective_from_request(
        "Fix the parser bug and run the tests.", mode="implement"
    )

    partial = evaluate_completion(
        objective,
        {
            "status": "answered",
            "answer": "I changed it.",
            "changed_files": ["parser.py"],
        },
    )
    completed = evaluate_completion(
        objective,
        {
            "status": "answered",
            "answer": "I changed it and tests passed.",
            "changed_files": ["parser.py"],
            "tool_trace": [{"tool": "run_tests", "ok": True, "detail": "12 passed"}],
        },
    )

    assert objective.acceptance == (
        AcceptanceRequirement.EXPECTED_EDIT,
        AcceptanceRequirement.TESTS_PASS,
    )
    assert partial.verdict is CompletionVerdict.PARTIAL
    assert partial.reason_code == "tests_not_verified"
    assert completed.verdict is CompletionVerdict.COMPLETED
    assert [evidence.kind for evidence in completed.evidence] == ["diff", "tests"]


def test_answer_objective_requires_a_real_answer_before_completion() -> None:
    objective = objective_from_request("Explain the router.", mode="explain")

    partial = evaluate_completion(objective, {"status": "answered", "answer": ""})
    completed = evaluate_completion(
        objective, {"status": "answered", "answer": "The router selects a tier."}
    )

    assert objective.acceptance == (AcceptanceRequirement.ANSWER_PRESENT,)
    assert partial.verdict is CompletionVerdict.PARTIAL
    assert partial.reason_code == "answer_missing"
    assert completed.verdict is CompletionVerdict.COMPLETED


def test_answer_delivery_does_not_claim_independent_objective_verification() -> None:
    objective = objective_from_request("What is 2+2?", mode="explain")

    result = evaluate_completion(
        objective,
        {"status": "answered", "answer": "Four", "completion_state": "completed"},
    )

    assert result.verdict is CompletionVerdict.COMPLETED
    assert result.reason_code == "answer_delivered"
    assert "not independently verified" in result.reason.lower()
    assert "objective verified" not in result.reason.lower()


def test_typed_provider_failure_preserves_its_actionable_user_message() -> None:
    objective = objective_from_request("Explain the repository.", mode="explain")
    message = (
        "Claude says you've hit your monthly spend limit. Wait for it to reset, "
        "raise it at https://claude.ai/settings/usage, or switch model."
    )

    result = evaluate_completion(
        objective,
        {
            "status": "failed",
            "error": {
                "code": "PROVIDER_QUOTA_EXHAUSTED",
                "userMessage": message,
            },
        },
    )

    assert result.verdict is CompletionVerdict.FAILED
    assert result.reason == message


@pytest.mark.parametrize(
    ("payload", "verdict", "reason_code"),
    [
        (
            {"status": "capability_mismatch", "reason": "No edit adapter."},
            CompletionVerdict.BLOCKED,
            "capability_mismatch",
        ),
        (
            {"status": "cancelled", "stopped_reason": "cancelled"},
            CompletionVerdict.CANCELLED,
            "cancelled",
        ),
        (
            {"status": "retryable_provider_error", "stopped_reason": "timeout"},
            CompletionVerdict.TIMEOUT,
            "timeout",
        ),
        (
            # #402: the tool-loop controller's wall-clock timeout keeps STUCK as
            # its canonical state but must surface a TIMEOUT verdict end-to-end.
            {"status": "incomplete", "stopped_reason": "controller_timeout"},
            CompletionVerdict.TIMEOUT,
            "timeout",
        ),
        (
            # #656: a bare failure carries no typed error code, so it has no
            # provider evidence and cannot be given a provider reason code.
            # Note the payload: "provider crashed" is untyped *prose*. Reading
            # a cause out of it is exactly what the issue forbids -- "frontend
            # /CLI must not infer failure class from raw timeline text, exit
            # code or stderr" -- and it is why this case now reads "unknown".
            {"status": "failed", "error": "provider crashed"},
            CompletionVerdict.FAILED,
            "unknown",
        ),
        (
            # #380: a typed provider error code drives the failure class so the
            # user is sent to re-connect, not offered a generic retry.
            {"status": "failed", "error": {"code": "AUTH_EXPIRED"}},
            CompletionVerdict.FAILED,
            "auth",
        ),
        (
            {"status": "needs_edit_approval", "answer": "approve this"},
            CompletionVerdict.BLOCKED,
            "permission_required",
        ),
        (
            {
                "status": "needs_attention",
                "completion_state": "needs_attention",
                "stopped_reason": "cancellation_unconfirmed",
            },
            CompletionVerdict.NEEDS_ATTENTION,
            "cancellation_unconfirmed",
        ),
    ],
)
def test_terminal_verdicts_have_typed_reason_codes(
    payload: dict[str, str], verdict: CompletionVerdict, reason_code: str
) -> None:
    result = evaluate_completion(
        objective_from_request("Fix it.", mode="implement"), payload
    )

    assert result.verdict is verdict
    assert result.reason_code == reason_code
    assert result.reason


@pytest.mark.parametrize(
    ("error_code", "expected"),
    [
        ("AUTH_MISSING", FailureReason.AUTH),
        ("AUTH_INVALID", FailureReason.AUTH),
        ("AUTH_EXPIRED", FailureReason.AUTH),
        ("PROVIDER_RATE_LIMITED", FailureReason.RATE_LIMIT),
        ("PROVIDER_QUOTA_EXHAUSTED", FailureReason.RATE_LIMIT),
        ("NETWORK_ERROR", FailureReason.NETWORK),
        ("PROVIDER_UNAVAILABLE", FailureReason.PROVIDER),
        ("MODEL_UNAVAILABLE", FailureReason.PROVIDER),
        ("NO_RESPONSE", FailureReason.PROVIDER),
        ("STREAM_ABORTED", FailureReason.PROVIDER),
        ("CONTEXT_TOO_LARGE", FailureReason.PROVIDER),
        ("CONFIG_INVALID", FailureReason.INTERNAL),
        ("PROVIDER_CLI_OUTDATED", FailureReason.PROVIDER),
    ],
)
def test_failure_reason_maps_every_provider_error_code(
    error_code: str, expected: FailureReason
) -> None:
    assert (
        classify_failure_reason({"status": "failed", "error": {"code": error_code}})
        is expected
    )


def test_failure_reason_falls_back_honestly_without_a_typed_code() -> None:
    # The test name still fits; what counts as honest changed.
    #
    # A codeless failure used to default to "provider", described here as the
    # honest option because the alternative was a fabricated internal blame.
    # #656 opens by describing what that default actually produced: a run the
    # no-progress guard stopped, presented to the user as "the provider
    # failed". Blaming a component that produced no evidence of failing is not
    # a lesser fabrication than blaming Vesta itself.
    #
    # So an absent cause is now UNKNOWN. Evidence still classifies: a runner
    # error is internal because the status says so.
    assert classify_failure_reason({"status": "failed"}) is FailureReason.UNKNOWN
    assert (
        classify_failure_reason({"status": "failed", "error": "raw text"})
        is FailureReason.UNKNOWN
    )
    assert classify_failure_reason({"status": "runner_error"}) is FailureReason.INTERNAL
    assert classify_failure_reason(None) is FailureReason.UNKNOWN


def test_every_canonical_provider_error_code_is_explicitly_classified() -> None:
    """A ratchet, added because removing the default broke five codes at once.

    While ``classify_failure_reason`` ended in ``return PROVIDER``, any code
    missing from the map still landed somewhere plausible. Removing that
    default reclassified all five unmapped codes as UNKNOWN in one commit --
    including PROVIDER_CLI_OUTDATED, which the parametrised test above pins to
    PROVIDER.

    With the default gone there is no safety net, so the map has to be
    complete, and completeness has to be enforced rather than remembered: a
    provider code added tomorrow would otherwise become UNKNOWN silently.
    """

    from opaihub.completion import _FAILURE_BY_ERROR_CODE
    from opaihub.provider_protocol import ERROR_CODES

    unmapped = sorted(set(ERROR_CODES) - set(_FAILURE_BY_ERROR_CODE))

    assert unmapped == [], (
        "these canonical provider error codes have no typed failure class and "
        "would silently classify as UNKNOWN: " + ", ".join(unmapped)
    )


def test_a_stopped_run_is_never_blamed_on_the_provider() -> None:
    """#656's motivating defect, at the classification layer.

    Cancellation and a reached deadline are both known exactly. Neither is a
    provider fault, and neither is unknown.
    """

    cancelled = classify_failure_reason({"error": {"code": "USER_CANCELLED"}})
    deadline = classify_failure_reason({"error": {"code": "TASK_DEADLINE"}})

    assert cancelled is FailureReason.CANCELLED
    assert deadline is FailureReason.DEADLINE
    for reason in (cancelled, deadline):
        assert reason is not FailureReason.PROVIDER
        assert reason is not FailureReason.UNKNOWN


def test_typed_failure_offers_a_class_specific_next_action() -> None:
    # #380: a failure names its class AND the next safe action — auth failures
    # send the user to re-connect, not a generic "retry the run".
    objective = objective_from_request("Fix it.", mode="implement")
    auth = evaluate_completion(
        objective, {"status": "failed", "error": {"code": "AUTH_MISSING"}}
    )
    rate = evaluate_completion(
        objective, {"status": "failed", "error": {"code": "PROVIDER_RATE_LIMITED"}}
    )

    assert auth.verdict is CompletionVerdict.FAILED
    assert auth.reason_code == "auth"
    assert "re-connect" in auth.next_action.lower()

    assert rate.reason_code == "rate_limit"
    assert "retry" in rate.next_action.lower()
    # Different classes give genuinely different guidance.
    assert auth.next_action != rate.next_action


class _EvidenceRunner(FakeStreamingRunner):
    def __init__(self, result: dict[str, object]) -> None:
        super().__init__()
        self._result = result

    def stream(self, prompt: str, **kwargs: object) -> dict[str, object]:
        project_root = kwargs.get("project_root")
        if isinstance(project_root, Path):
            (project_root / "parser.py").write_text("value = 2\n", encoding="utf-8")
        return dict(self._result)


def test_pipeline_persists_one_manifest_verdict_and_receipt_contract() -> None:
    from opaihub.checkpoints import load_run_checkpoint
    from opaihub.gui_pipeline import handle_gui_message, last_savings_receipt
    from opaihub.workflow_state import load_workflow_state

    runner = _EvidenceRunner(
        {
            "text": "Parser corrected.",
            "cost": 0.01,
            "changed_files": ["parser.py"],
            "tool_trace": [{"tool": "run_tests", "ok": True, "detail": "12 passed"}],
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(Path(tmp), files={"parser.py": "value = 1\n"}, commit=True)
        result = handle_gui_message(
            root,
            "Fix parser.py and run tests.",
            model_id="account:claude:sonnet",
            mode="safe-auto",
            account_runner=runner,
            on_text=lambda _chunk: None,
        )
        checkpoint = load_run_checkpoint(root, result["checkpoint_id"])
        workflow = load_workflow_state(root)
        receipt = last_savings_receipt(root)

    verdict = result["completion_verdict"]
    assert verdict["verdict"] == "partial", verdict
    assert verdict["reason_code"] == "verification_unverified"
    assert result["verification_manifest"]["digest"]
    assert (
        verdict["verification_manifest"]["digest"]
        == result["verification_manifest"]["digest"]
    )
    assert result["objective"]["acceptance"] == ["expected_edit", "tests_pass"]
    receipt_verdict = result["receipt"]["completion_verdict"]
    workflow_verdict = result["workflow"]["completion_verdict"]
    assert receipt_verdict["verdict"] == verdict["verdict"]
    assert workflow_verdict["reason_code"] == verdict["reason_code"]
    assert "objective_text" not in receipt_verdict["objective"]
    assert "objective_text" not in workflow_verdict["objective"]
    assert checkpoint.completion_verdict["verdict"] == "partial"
    assert (
        checkpoint.completion_verdict["verification_manifest"]["digest"]
        == result["verification_manifest"]["digest"]
    )
    assert workflow.completion_verdict["reason_code"] == "verification_unverified"
    assert (
        workflow.completion_verdict["verification_manifest"]["digest"]
        == result["verification_manifest"]["digest"]
    )
    assert receipt is not None
    assert receipt["completion_verdict"]["verdict"] == "partial"
    assert (
        receipt["completion_verdict"]["verification_manifest"]["digest"]
        == result["verification_manifest"]["digest"]
    )
    assert result["verification_manifest"]["digest"][:12] in result["run_summary"]


def test_verification_receives_the_live_cancel_signal() -> None:
    """#614/#666: verification runs real subprocesses -- the "verifying" state
    Vesta refuses to let jump straight to "cancelled" for exactly that reason
    (see test_cancel_two_phase.py). Before this, ``execute_policy`` was always
    called with ``cancel=None``: pressing Stop mid-verification changed
    nothing here, so a check kept running to its own timeout regardless, and
    cost or repository changes could keep accruing after the user was told
    Vesta was stopping. This proves the same Event that stops everything else
    in the turn now reaches verification too.
    """
    import threading
    from unittest import mock

    import opaihub.gui_pipeline as gui_pipeline_module
    from opaihub.gui_pipeline import handle_gui_message

    runner = _EvidenceRunner(
        {
            "text": "Parser corrected.",
            "cost": 0.01,
            "changed_files": ["parser.py"],
            "tool_trace": [{"tool": "run_tests", "ok": True, "detail": "12 passed"}],
        }
    )
    cancel_event = threading.Event()
    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(Path(tmp), files={"parser.py": "value = 1\n"}, commit=True)
        with mock.patch.object(
            gui_pipeline_module,
            "execute_policy",
            wraps=gui_pipeline_module.execute_policy,
        ) as spy:
            handle_gui_message(
                root,
                "Fix parser.py and run tests.",
                model_id="account:claude:sonnet",
                mode="safe-auto",
                account_runner=runner,
                on_text=lambda _chunk: None,
                cancel=cancel_event,
            )

    assert spy.called, "an edit-intent turn must run verification"
    cancel_arg = spy.call_args.kwargs.get("cancel")
    assert cancel_arg is not None, "verification must observe the turn's Stop signal"
    assert cancel_arg() is False, "the signal must reflect the real event, unset here"
    cancel_event.set()
    assert cancel_arg() is True, "and flip to true the moment the user presses Stop"


def test_partial_verdict_is_persisted_as_non_completed_checkpoint_state() -> None:
    from opaihub.checkpoints import load_run_checkpoint
    from opaihub.gui_pipeline import handle_gui_message

    runner = _EvidenceRunner(
        {
            "text": "Parser corrected.",
            "cost": 0.01,
            "changed_files": ["parser.py"],
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(Path(tmp), files={"parser.py": "value = 1\n"}, commit=True)
        result = handle_gui_message(
            root,
            "Fix parser.py and run tests.",
            model_id="account:claude:sonnet",
            mode="safe-auto",
            account_runner=runner,
            on_text=lambda _chunk: None,
        )
        checkpoint = load_run_checkpoint(root, result["checkpoint_id"])

    assert result["completion_verdict"]["verdict"] == "partial"
    assert result["checkpoint"]["completion_state"] != "answered"
    assert checkpoint.completion_state != "answered"
    assert checkpoint.completion_verdict["verdict"] == "partial"


def test_gui_provider_trace_cannot_bypass_the_persisted_verification_manifest() -> None:
    from opaihub.gui_pipeline import handle_gui_message

    runner = _EvidenceRunner(
        {
            "text": "Parser corrected.",
            "cost": 0.01,
            "changed_files": ["parser.py"],
            "tool_trace": [{"tool": "run_tests", "ok": True, "detail": "12 passed"}],
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(Path(tmp), files={"parser.py": "value = 1\n"}, commit=True)
        result = handle_gui_message(
            root,
            "Fix parser.py and run tests.",
            model_id="account:claude:sonnet",
            mode="safe-auto",
            account_runner=runner,
            on_text=lambda _chunk: None,
        )

    assert result["verification_manifest"]["digest"]
    assert result["completion_verdict"]["verdict"] == "partial"
    assert result["completion_verdict"]["reason_code"] == "verification_unverified"


def test_provider_diagnostics_never_become_persisted_verdict_reason() -> None:
    secret = "api_key=sk-supersecret9876543210abcdef"
    result = evaluate_completion(
        objective_from_request("Fix parser.py.", mode="implement"),
        {"status": "failed", "error": f"provider crashed: {secret}"},
    )

    assert result.verdict is CompletionVerdict.FAILED
    assert secret not in result.reason


def test_pipeline_timeout_preserves_the_timeout_verdict() -> None:
    from opaihub.gui_pipeline import handle_gui_message

    runner = _EvidenceRunner({"text": "", "cost": None, "timed_out": True})
    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
        result = handle_gui_message(
            root,
            "Fix app.py.",
            model_id="account:claude:sonnet",
            mode="safe-auto",
            account_runner=runner,
            on_text=lambda _chunk: None,
        )

    assert result["completion_verdict"]["verdict"] == "timeout"
    assert result["completion_verdict"]["reason_code"] == "timeout"


def test_active_task_deadline_has_cause_specific_completion_guidance() -> None:
    result = evaluate_completion(
        objective_from_request("Implement the feature.", mode="implement"),
        {
            "status": "failed",
            "completion_state": "timeout",
            "stopped_reason": "task_deadline",
            "changed_files": ["feature.py"],
        },
    )

    assert result.verdict is CompletionVerdict.TIMEOUT
    assert result.reason_code == "task_deadline"
    assert "provider" not in result.reason.lower()
    assert "retained" in result.reason.lower()
    assert "smaller" not in result.next_action.lower()
    assert "reconcile" in result.next_action.lower()


def test_blocked_terminal_run_persists_a_verdict_even_without_a_task_outcome() -> None:
    from opaihub.gui_pipeline import handle_gui_message
    from opaihub.ledger import read_events

    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
        result = handle_gui_message(
            root,
            "Delete the repository.",
            model_id="auto",
            mode="safe-auto",
        )
        terminal_events = [
            event
            for event in read_events(root)
            if event.get("event_type") == "completion_verdict"
        ]

    assert result["completion_verdict"]["verdict"] == "blocked"
    assert len(terminal_events) == 1
    assert terminal_events[0]["reason_code"]


def test_result_is_completed_only_for_a_genuine_completion() -> None:
    assert result_is_completed({"status": "answered_by_free_api"})
    assert result_is_completed({"completion_state": "completed"})
    # A run that streamed text but ended stuck/blocked is not a completion,
    # even though a legacy layer left status as "answered".
    assert not result_is_completed(
        {"status": "answered_by_free_api", "stopped_reason": "no_progress"}
    )
    assert not result_is_completed(
        {"status": "answered", "completion_state": "provider_blocked"}
    )
    assert not result_is_completed({"status": "cancelled"})
    assert not result_is_completed(None)


def test_legacy_stop_text_can_never_map_to_completed() -> None:
    state = completion_state_from_legacy(
        {
            "status": "answered_by_free_api",
            "stopped_reason": "tool_budget_exhausted",
        }
    )

    assert state is CompletionState.STUCK_NO_PROGRESS


@pytest.mark.parametrize("state", list(CompletionState))
def test_explicit_completion_states_round_trip(state: CompletionState) -> None:
    assert completion_state_from_legacy({"completion_state": state.value}) is state


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"status": "answered"}, CompletionState.COMPLETED),
        ({"status": "answered_by_account"}, CompletionState.COMPLETED),
        ({"status": "fail_open"}, CompletionState.FAILED),
        ({"status": "cache_hit"}, CompletionState.COMPLETED),
        ({"status": "cancelled"}, CompletionState.CANCELLED),
        ({"status": "needs_user_input"}, CompletionState.NEEDS_USER_INPUT),
        ({"status": "needs_confirmation"}, CompletionState.NEEDS_CONSENT),
        ({"status": "needs_paid_confirmation"}, CompletionState.NEEDS_CONSENT),
        (
            {"status": "retryable_provider_error"},
            CompletionState.RETRYABLE_PROVIDER_ERROR,
        ),
        ({"status": "provider_blocked"}, CompletionState.PROVIDER_BLOCKED),
        ({"status": "incomplete"}, CompletionState.STUCK_NO_PROGRESS),
        ({"status": "failed"}, CompletionState.FAILED),
    ],
)
def test_legacy_statuses_map_explicitly(
    payload: dict[str, str], expected: CompletionState
) -> None:
    assert completion_state_from_legacy(payload) is expected


def test_unknown_legacy_status_fails_closed() -> None:
    assert (
        completion_state_from_legacy({"status": "probably fine"})
        is CompletionState.NEEDS_ATTENTION
    )
    assert completion_state_from_legacy({}) is CompletionState.NEEDS_ATTENTION


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("cancelled", CompletionState.CANCELLED),
        ("needs_user_input", CompletionState.NEEDS_USER_INPUT),
        ("needs_consent", CompletionState.NEEDS_CONSENT),
        ("rate_limit", CompletionState.PROVIDER_BLOCKED),
        ("quota", CompletionState.PROVIDER_BLOCKED),
        ("timeout", CompletionState.RETRYABLE_PROVIDER_ERROR),
        ("approval_required", CompletionState.NEEDS_CONSENT),
        ("repeated_failure", CompletionState.STUCK_NO_PROGRESS),
        ("repeated_success", CompletionState.STUCK_NO_PROGRESS),
        ("unclassified stop", CompletionState.NEEDS_ATTENTION),
    ],
)
def test_stopped_reason_takes_precedence_over_answered_status(
    reason: str, expected: CompletionState
) -> None:
    assert (
        completion_state_from_legacy(
            {"status": "answered_by_free_api", "stopped_reason": reason}
        )
        is expected
    )


def test_only_completed_maps_to_an_answered_compatibility_status() -> None:
    assert (
        legacy_status_for_completion(
            CompletionState.COMPLETED,
            completed_status="answered_by_free_api",
        )
        == "answered_by_free_api"
    )
    for state in CompletionState:
        if state is not CompletionState.COMPLETED:
            assert not legacy_status_for_completion(state).startswith("answered")


def test_provider_block_reasons_are_typed_and_complete() -> None:
    assert {reason.value for reason in ProviderBlockedReason} == {
        "auth",
        "rate_limit",
        "quota",
        "billing",
        "panic",
        "daily_cap",
        "monthly_cap",
        "task_cap",
    }


def test_completion_result_is_immutable_and_serializes_enum_values() -> None:
    result = CompletionResult(
        state=CompletionState.PROVIDER_BLOCKED,
        blocked_reason=ProviderBlockedReason.QUOTA,
        recovery_action="Wait for the provider quota to reset.",
    )

    assert result.to_dict()["completion_state"] == "provider_blocked"
    assert result.to_dict()["provider_blocked_reason"] == "quota"
    with pytest.raises(FrozenInstanceError):
        result.state = CompletionState.COMPLETED  # type: ignore[misc]


def test_provider_blocked_result_requires_a_typed_reason() -> None:
    with pytest.raises(ValueError, match="blocked_reason"):
        CompletionResult(state=CompletionState.PROVIDER_BLOCKED)


def test_blocked_reason_is_forbidden_on_non_blocked_results() -> None:
    with pytest.raises(ValueError, match="only valid"):
        CompletionResult(
            state=CompletionState.COMPLETED,
            blocked_reason=ProviderBlockedReason.QUOTA,
        )


@pytest.mark.parametrize("version", [True, 1.9, 0, -1])
def test_completion_schema_version_must_be_a_positive_integer(version: object) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        CompletionResult(
            state=CompletionState.COMPLETED,
            schema_version=version,  # type: ignore[arg-type]
        )


def test_checkpoint_is_deeply_immutable_and_serializes_as_plain_data() -> None:
    source = {"subgoals": ["first"], "nested": {"turn": 2}}
    result = CompletionResult(
        state=CompletionState.STUCK_NO_PROGRESS,
        checkpoint=source,
    )
    source["subgoals"].append("mutated")
    source["nested"]["turn"] = 99

    assert result.to_dict()["checkpoint"] == {
        "subgoals": ["first"],
        "nested": {"turn": 2},
    }
    with pytest.raises(TypeError):
        result.checkpoint["new"] = "value"  # type: ignore[index]


@pytest.mark.parametrize("mutable", [{"value"}, bytearray(b"value")])
def test_checkpoint_rejects_non_json_mutable_leaves(mutable: object) -> None:
    with pytest.raises(TypeError, match="JSON-compatible"):
        CompletionResult(
            state=CompletionState.STUCK_NO_PROGRESS,
            checkpoint={"unsafe": mutable},
        )


# ---------------------------------------------------------------------------
# Round 2 (2026-07-24) status-honesty fixes: the verdict was wrong in BOTH
# directions — a real commit read "Partial", and a refusal read "Completed".
# ---------------------------------------------------------------------------
def test_commit_made_outside_opais_tools_counts_as_change_evidence() -> None:
    # A provider CLI commits through its own shell, and committing CLEARS the
    # dirty paths the run created — so a genuinely successful commit arrived
    # with no changed_files and no diff_review, and was stamped PARTIAL. The
    # pipeline measures the repository across the run and reports it here.
    objective = objective_from_request(
        "Stage and commit the two leftover files.", mode="implement"
    )
    committed = {
        "status": "answered",
        "answer": "Committed the two files.",
        "changed_files": [],
        "repo_change": {
            "changed": True,
            "kind": "commit",
            "detail": "New commit on this branch (530766d)",
        },
    }

    verdict = evaluate_completion(objective, committed)

    assert verdict.verdict is CompletionVerdict.COMPLETED
    assert any(item.kind == "diff" for item in verdict.evidence)


def test_unchanged_repository_still_fails_the_edit_evidence_gate() -> None:
    # The honesty gate must not be weakened: no repo movement, no evidence.
    objective = objective_from_request("Fix the parser bug.", mode="implement")

    verdict = evaluate_completion(
        objective,
        {
            "status": "answered",
            "answer": "Done!",
            "changed_files": [],
            "repo_change": {"changed": False},
        },
    )

    assert verdict.verdict is CompletionVerdict.PARTIAL
    assert verdict.reason_code == "change_not_verified"


def test_answer_that_declines_the_request_is_not_completed() -> None:
    # The other direction: an Explain-mode turn whose whole answer is "that is
    # outside my capabilities" satisfied ANSWER_PRESENT and was stamped
    # Completed. A declined request is not a met objective.
    objective = objective_from_request("Show me the current git state.", mode="explain")

    verdict = evaluate_completion(
        objective,
        {
            "status": "answered",
            "answer": "I noted that git log execution is outside of my tool capabilities.",
        },
    )

    assert verdict.verdict is CompletionVerdict.PARTIAL
    assert verdict.reason_code == "provider_declined"


def test_gemini_capability_refusal_is_not_completed() -> None:
    objective = objective_from_request(
        "Run git fetch and compare this branch with origin.", mode="explain"
    )

    verdict = evaluate_completion(
        objective,
        {
            "status": "answered",
            "answer": (
                "I do not have the capability to execute git commands directly. "
                "My authorized capabilities are limited to inspect_git, read_files, "
                "and search_code."
            ),
        },
    )

    assert verdict.verdict is CompletionVerdict.PARTIAL
    assert verdict.reason_code == "provider_declined"


def test_declining_prose_alongside_real_tool_work_still_completes() -> None:
    # Prose alone never decides this. A run that actually called a tool and
    # merely narrated a limitation is a completed answer, not a refusal.
    objective = objective_from_request("Show me the current git state.", mode="explain")

    verdict = evaluate_completion(
        objective,
        {
            "status": "answered",
            "answer": (
                "HEAD is 530766d. I cannot execute arbitrary shell commands, "
                "but git_status covered what you asked for."
            ),
            "tool_trace": [{"tool": "git_status", "ok": True}],
        },
    )

    assert verdict.verdict is CompletionVerdict.COMPLETED


def test_ordinary_answer_is_never_mistaken_for_a_refusal() -> None:
    objective = objective_from_request("Explain what this module does.", mode="explain")

    verdict = evaluate_completion(
        objective,
        {
            "status": "answered",
            "answer": "It parses the config file and validates each key in turn.",
        },
    )

    assert verdict.verdict is CompletionVerdict.COMPLETED


# --- Round 5 finding 3: a claim of retrieval standing in for the data ---------


@pytest.mark.parametrize(
    "answer",
    [
        "Retrieved and printed the requested git status and git log output.",
        "Retrieved details for PR 511 via GitHub API.",
        "I have fetched the raw API response for the pull request details.",
        "Printed the requested log output above.",
    ],
)
def test_claiming_output_without_showing_it_is_not_completed(answer: str) -> None:
    # The live failure: asked for raw `git status`/`git log` and for PR details,
    # the model twice answered with only a claim of success and zero data, and the
    # run was stamped Completed because the claim itself is non-empty text.
    objective = objective_from_request(
        "Print the raw output of git status and git log.", mode="explain"
    )

    verdict = evaluate_completion(objective, {"status": "answered", "answer": answer})

    assert verdict.verdict is CompletionVerdict.PARTIAL
    assert verdict.reason_code == "claimed_output_missing"
    assert "raw output" in verdict.next_action


def test_a_claim_that_actually_carries_the_output_completes() -> None:
    objective = objective_from_request("Print the raw git status.", mode="explain")

    verdict = evaluate_completion(
        objective,
        {
            "status": "answered",
            "answer": (
                "Retrieved the requested git status output:\n\n"
                "```\nOn branch main\nnothing to commit, working tree clean\n```"
            ),
        },
    )

    assert verdict.verdict is CompletionVerdict.COMPLETED


@pytest.mark.parametrize(
    "answer",
    [
        "Ran the command; it returned no output.",
        "The requested git log produced no commits for that range.",
        "Nothing to show — the status output was empty.",
    ],
)
def test_reporting_an_empty_result_is_a_complete_answer(answer: str) -> None:
    # Saying "there was no output" is honest reporting, not a hidden deliverable —
    # it must not be downgraded just because it matches the claim shape.
    objective = objective_from_request("Print the raw git log.", mode="explain")

    verdict = evaluate_completion(objective, {"status": "answered", "answer": answer})

    assert verdict.verdict is CompletionVerdict.COMPLETED


def test_a_real_explanation_is_never_flagged_as_a_bare_claim() -> None:
    # The false-positive direction. A substantive answer that happens to mention
    # reading something is an answer, not a bare claim, and must not be downgraded.
    objective = objective_from_request("What does the router do?", mode="explain")

    verdict = evaluate_completion(
        objective,
        {
            "status": "answered",
            "answer": (
                "I read the routing module. It scores each candidate model on "
                "capability, then cost, then recent reliability, and returns the "
                "first that can run the task locally. When nothing local qualifies "
                "it walks the same ordering across configured free providers, and "
                "only then does it consider a paid account, which always needs "
                "explicit confirmation before the call is made."
            ),
        },
    )

    assert verdict.verdict is CompletionVerdict.COMPLETED


# --- Round 5 finding 2: pill and prose must not say opposite things ----------


def test_success_prose_under_a_failed_verdict_is_flagged_as_conflicting() -> None:
    # The live failure: a red "Failed" pill directly above "The current branch has
    # been successfully pushed to the origin remote."
    assert answer_contradicts_verdict(
        "The current branch has been successfully pushed to the origin remote.",
        CompletionVerdict.FAILED,
    )
    assert answer_contradicts_verdict(
        "I pushed the branch and opened a PR.", CompletionVerdict.PARTIAL
    )
    assert answer_contradicts_verdict(
        "The commit was successful.", CompletionVerdict.BLOCKED
    )


def test_a_verified_run_is_never_annotated_as_conflicting() -> None:
    # The flag exists to reconcile disagreement. A completed (or user-cancelled)
    # run has nothing to reconcile, so the same prose must not be marked.
    for verdict in (CompletionVerdict.COMPLETED, CompletionVerdict.CANCELLED):
        assert not answer_contradicts_verdict(
            "The current branch has been successfully pushed to origin.", verdict
        )


def test_prose_without_a_success_claim_is_not_flagged() -> None:
    for answer in (
        "The push is awaiting your approval.",
        "I could not push: the remote rejected the credentials.",
        "Here is what the diff would change.",
        "",
    ):
        assert not answer_contradicts_verdict(answer, CompletionVerdict.FAILED)
