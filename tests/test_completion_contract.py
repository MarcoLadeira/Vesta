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
    ProviderBlockedReason,
    completion_state_from_legacy,
    evaluate_completion,
    legacy_status_for_completion,
    objective_from_request,
    result_is_completed,
)


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


@pytest.mark.parametrize(
    ("payload", "verdict", "reason_code"),
    [
        (
            {"status": "capability_mismatch", "reason": "No edit adapter."},
            CompletionVerdict.BLOCKED,
            "capability_mismatch",
        ),
        (
            {"status": "cancelled", "stopped_reason": "cancel_requested"},
            CompletionVerdict.CANCELLED,
            "cancel_requested",
        ),
        (
            {"status": "retryable_provider_error", "stopped_reason": "timeout"},
            CompletionVerdict.TIMEOUT,
            "timeout",
        ),
        (
            {"status": "failed", "error": "provider crashed"},
            CompletionVerdict.FAILED,
            "provider_failed",
        ),
        (
            {"status": "needs_edit_approval", "answer": "approve this"},
            CompletionVerdict.BLOCKED,
            "permission_required",
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


class _EvidenceRunner(FakeStreamingRunner):
    def __init__(self, result: dict[str, object]) -> None:
        super().__init__()
        self._result = result

    def stream(self, prompt: str, **kwargs: object) -> dict[str, object]:
        project_root = kwargs.get("project_root")
        if isinstance(project_root, Path):
            (project_root / "parser.py").write_text("value = 2\n", encoding="utf-8")
        return dict(self._result)


def test_pipeline_persists_one_objective_verdict_and_receipt_contract() -> None:
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
    assert verdict["verdict"] == "completed", verdict
    assert verdict["reason_code"] == "objective_verified"
    assert result["objective"]["acceptance"] == ["expected_edit", "tests_pass"]
    receipt_verdict = result["receipt"]["completion_verdict"]
    workflow_verdict = result["workflow"]["completion_verdict"]
    assert receipt_verdict["verdict"] == verdict["verdict"]
    assert workflow_verdict["reason_code"] == verdict["reason_code"]
    assert "objective_text" not in receipt_verdict["objective"]
    assert "objective_text" not in workflow_verdict["objective"]
    assert checkpoint.completion_verdict["verdict"] == "completed"
    assert workflow.completion_verdict["reason_code"] == "objective_verified"
    assert receipt is not None
    assert receipt["completion_verdict"]["verdict"] == "completed"


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
        is CompletionState.FAILED
    )
    assert completion_state_from_legacy({}) is CompletionState.FAILED


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
        ("unclassified stop", CompletionState.FAILED),
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
