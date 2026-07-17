"""Canonical completion truth for runners, pipelines, and the UI."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from opaihub.completion import (
    CompletionResult,
    CompletionState,
    ProviderBlockedReason,
    completion_state_from_legacy,
    legacy_status_for_completion,
    result_is_completed,
)


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
