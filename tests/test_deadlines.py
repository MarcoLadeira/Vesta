"""Versioned deadline policy and timeout provenance (#683)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vesta.provider_contract import normalize_provider_error
from vestahub.deadlines import (
    RECONCILE_BEFORE_RETRY,
    TASK_DEADLINE,
    UNKNOWN_TIMEOUT,
    DeadlineBudget,
    DeadlineClocks,
    enrich_timeout_event,
    timeout_event,
)


def test_issue_683_active_account_incident_replays_as_task_deadline() -> None:
    fixture_path = (
        Path(__file__).parent / "fixtures" / "issue_683_active_account_deadline.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    clocks = DeadlineClocks(
        started_at=0.0,
        task_deadline_seconds=fixture["task_deadline_seconds"],
        provider_idle_timeout_seconds=fixture["provider_idle_timeout_seconds"],
    )

    origin = None
    for item in fixture["events"]:
        at = float(item["at"])
        if item["provider_activity"]:
            clocks.note_provider_activity(at)
        origin = clocks.expired_origin(at)
        if item["kind"] != "deadline_poll":
            assert origin is None, item

    assert origin == TASK_DEADLINE
    assert clocks.provider_responsive_at(1200.0)
    event = timeout_event(
        origin=origin,
        owner="account_runner",
        configured_seconds=clocks.task_deadline_seconds,
        elapsed_seconds=clocks.elapsed_at(1200.0),
        provider_responsive=clocks.provider_responsive_at(1200.0),
        last_activity_seconds_ago=clocks.last_activity_age_at(1200.0),
        phase="verification",
        progress_observed=True,
        external_effect_possible=True,
        teardown_state="requested",
        verification_state="incomplete",
    )
    assert event["timeout_origin"] == TASK_DEADLINE
    assert event["provider_condition"] == "responsive"
    assert event["verification_state"] == "incomplete"
    assert event["retry_safety"] == "reconcile_before_retry"
    error = normalize_provider_error(
        fixture["provider"],
        "",
        timed_out=True,
        timeout_origin=event["timeout_origin"],
    )
    assert error["code"] == "TASK_DEADLINE"
    user_copy = f"{error['title']} {error['userMessage']}".lower()
    assert "did not receive a response" not in user_copy
    assert "smaller request" not in user_copy


def _budget() -> DeadlineBudget:
    return DeadlineBudget(
        task_deadline_seconds=1200,
        provider_idle_timeout_seconds=300,
        lane="long_horizon",
    )


def test_deadline_budget_is_deterministic_and_carries_both_clocks() -> None:
    first = _budget()
    second = _budget()

    assert first.to_dict() == second.to_dict()
    assert first.budget_id == second.budget_id
    assert first.to_dict()["task_deadline_seconds"] == 1200.0
    assert first.to_dict()["provider_idle_timeout_seconds"] == 300.0


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan")])
def test_deadline_budget_rejects_invalid_task_clocks(value: object) -> None:
    with pytest.raises(ValueError):
        DeadlineBudget(
            task_deadline_seconds=value,  # type: ignore[arg-type]
            provider_idle_timeout_seconds=300,
        )


def test_active_task_deadline_has_stable_reconcile_first_provenance() -> None:
    budget = _budget()
    kwargs = dict(
        origin=TASK_DEADLINE,
        owner="account_runner",
        configured_seconds=1200,
        elapsed_seconds=1200.2,
        provider_responsive=True,
        last_activity_seconds_ago=32,
        budget=budget,
        operation_id="op-7",
        progress_observed=True,
        external_effect_possible=True,
        teardown_state="terminated",
        cost_state="observed",
        verification_state="incomplete",
    )

    first = timeout_event(**kwargs)
    second = timeout_event(**kwargs)

    assert first == second
    assert first["timeout_id"] == second["timeout_id"]
    assert first["timeout_origin"] == TASK_DEADLINE
    assert first["provider_condition"] == "responsive"
    assert first["retry_safety"] == RECONCILE_BEFORE_RETRY
    assert first["deadline_budget_id"] == budget.budget_id
    assert first["progress_observed"] is True
    assert first["external_effect_possible"] is True


def test_unknown_origin_degrades_to_closed_vocabulary() -> None:
    event = timeout_event(
        origin="future_vendor_timeout",
        owner="adapter",
        configured_seconds=10,
    )
    assert event["timeout_origin"] == UNKNOWN_TIMEOUT


def test_durable_context_changes_the_event_id_without_storing_output() -> None:
    event = timeout_event(
        origin=TASK_DEADLINE,
        owner="account_runner",
        configured_seconds=1200,
    )
    enriched = enrich_timeout_event(
        event,
        task_id="task-1",
        run_id="run-1",
        checkpoint_id="checkpoint-1",
    )

    assert enriched["timeout_id"] != event["timeout_id"]
    assert enriched["checkpoint_id"] == "checkpoint-1"
    assert "output" not in enriched
    assert "response" not in enriched
