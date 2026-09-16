from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

from vestahub import budget, gui_pipeline, ledger


def test_assignment_finances_use_parent_while_workspace_state_stays_local(tmp_path):
    from vestahub.execution_scope import assignment_scope, financial_root
    from vestahub.state import state_dir

    parent, child = tmp_path / "parent", tmp_path / "worker"
    with assignment_scope(child, parent, task_id="task-1", run_id="run-1"):
        assert financial_root(child) == parent.resolve()
        assert state_dir(child) == child / ".vestahub"
        event = ledger.record_event(child, "test", task="bounded task")
        assert event["assignment_run_id"] == "run-1"
        assert ledger.ledger_path(child) == parent / ".vestahub/ledger/usage.jsonl"
    assert financial_root(child) == child.resolve()
    assert not ledger.ledger_path(child).exists()
    assert ledger.read_events(parent)[0]["assignment_task_id"] == "task-1"


def test_execution_scope_does_not_leak_between_threads_or_exceptions(tmp_path):
    from vestahub.execution_scope import assignment_scope, financial_root

    child = tmp_path / "worker"
    with pytest.raises(RuntimeError):
        with assignment_scope(child, tmp_path, task_id="t", run_id="r"):
            with ThreadPoolExecutor(1) as pool:
                assert pool.submit(financial_root, child).result() == child.resolve()
            raise RuntimeError("worker failed")
    assert financial_root(child) == child.resolve()


def test_worker_budget_gate_uses_authority_root(tmp_path):
    from vestahub.execution_scope import assignment_scope

    child = tmp_path / "worker"
    with assignment_scope(child, tmp_path, task_id="t", run_id="r"):
        with mock.patch.object(
            budget, "load_budget", side_effect=RuntimeError("stop")
        ) as load:
            with pytest.raises(RuntimeError, match="stop"):
                budget.budget_gate(child)
            load.assert_called_once_with(tmp_path.resolve())


def test_managed_pipeline_restores_context_and_returns_all_attempt_costs(tmp_path):
    from vestahub.execution_scope import financial_root

    child = tmp_path / "worker"

    def run(*args, **kwargs):
        assert financial_root(child) == tmp_path.resolve()
        ledger.record_event(
            child,
            "model_call",
            task="work",
            **{
                "call_id": "attempt-a",
                "cost_usd": 0.1,
                "cost_usd_provenance": "actual",
            },
        )
        ledger.record_event(
            child, "model_call_started", task="work", **{"call_id": "attempt-b"}
        )
        return {"status": "failed"}

    with mock.patch.object(gui_pipeline, "_handle_gui_message", run):
        result = gui_pipeline.handle_gui_message(
            child, "work", task_id="t", run_id="r", authority_root=tmp_path
        )
    assert financial_root(child) == child.resolve()
    assert result["objective_cost_events"] == [
        {
            "operation_key": "attempt-a",
            "amount_usd": "0.1",
            "measurement_kind": "actual",
        },
        {
            "operation_key": "attempt-b",
            "amount_usd": None,
            "measurement_kind": "unavailable",
        },
    ]


def test_managed_pipeline_requires_both_canonical_identifiers(tmp_path):
    with mock.patch.object(gui_pipeline, "_handle_gui_message") as run:
        with pytest.raises(ValueError):
            gui_pipeline.handle_gui_message(tmp_path, "work", authority_root=tmp_path)
        run.assert_not_called()


def _budget_objective(tmp_path, *, objective_cap="10", assignment_cap="1"):
    from vestahub.agent_objectives import ObjectiveStore

    store = ObjectiveStore(tmp_path)
    obj = store.create(
        "Bounded edits",
        assignments=[
            {
                "name": "first",
                "objective": "First edit",
                "intended_paths": ["a"],
                "budget_usd": assignment_cap,
            },
            {
                "name": "second",
                "objective": "Second edit",
                "intended_paths": ["b"],
                "budget_usd": "2",
            },
        ],
        budget_usd=objective_cap,
    )
    first = store.claim_next(obj["objective_id"], "owner-first")
    return store, obj, first


def test_assignment_cap_applies_to_each_call_and_counts_failed_attempts(tmp_path):
    from vestahub.execution_scope import assignment_scope, managed_budget_gate

    store, obj, first = _budget_objective(tmp_path)
    child = tmp_path / "worker"
    with assignment_scope(
        child, tmp_path, task_id=first["task_id"], run_id=first["run_id"]
    ):
        ledger.record_event(
            child,
            "model_call",
            task="failed attempt",
            **{
                "call_id": "failed-cost",
                "cost_usd": 0.75,
                "cost_usd_provenance": "actual",
                "status": "failed",
            },
        )
        gate = managed_budget_gate(child, next_cost_usd="0.30")
    assert gate["denied"]
    assert any("Assignment budget" in reason for reason in gate["reasons"])
    assert store.snapshot(obj["objective_id"])["cost_usd"] == "0"


def test_objective_cap_includes_other_reservations_but_not_own_twice(tmp_path):
    from vestahub.execution_scope import assignment_scope, managed_budget_gate

    store, obj, first = _budget_objective(
        tmp_path, objective_cap="3", assignment_cap="1"
    )
    second = store.claim_next(obj["objective_id"], "owner-second")
    assert second is not None
    child = tmp_path / "worker"
    with assignment_scope(
        child, tmp_path, task_id=first["task_id"], run_id=first["run_id"]
    ):
        assert managed_budget_gate(child, next_cost_usd="1")["allowed"]
        store.control(obj["objective_id"], "budget", value="2.9")
        assert managed_budget_gate(child, next_cost_usd="1")["denied"]


def test_unknown_and_estimated_prior_costs_fail_closed_under_assignment_cap(tmp_path):
    from vestahub.execution_scope import assignment_scope, managed_budget_gate

    _, _, first = _budget_objective(tmp_path)
    child = tmp_path / "worker"
    with assignment_scope(
        child, tmp_path, task_id=first["task_id"], run_id=first["run_id"]
    ):
        ledger.record_event(
            child,
            "model_call",
            task="unpriced",
            **{
                "call_id": "unknown-cost",
                "cost_usd": None,
                "cost_usd_provenance": "unavailable",
            },
        )
        assert managed_budget_gate(child, next_cost_usd="0.01")["denied"]


def test_planner_obeys_parent_cap_and_unknown_next_call_fails_closed(tmp_path):
    from vestahub.execution_scope import assignment_scope, managed_budget_gate
    from vestahub.agent_objectives import ObjectiveStore

    obj = ObjectiveStore(tmp_path).create("Plan bounded edits", budget_usd="0.5")
    with assignment_scope(
        tmp_path / "planner",
        tmp_path,
        task_id=obj["task_id"],
        run_id=obj["run_id"] + "-plan",
    ):
        assert managed_budget_gate(tmp_path / "planner", next_cost_usd="0.6")["denied"]
        assert managed_budget_gate(tmp_path / "planner", next_cost_usd="0.4")["allowed"]
        assert managed_budget_gate(tmp_path / "planner", next_cost_usd=None)["denied"]


def test_paid_transport_refuses_opaque_spend_under_objective_cap(tmp_path):
    from vestahub.execution_scope import assignment_scope
    from vestahub.local_runner import PaidAPIRunner

    _, _, first = _budget_objective(tmp_path)
    runner = PaidAPIRunner(
        "https://example.invalid",
        "model",
        "unused-test-value",
        pricing_model_id="model",
    )
    with assignment_scope(
        tmp_path / "worker", tmp_path, task_id=first["task_id"], run_id=first["run_id"]
    ):
        with pytest.raises(RuntimeError, match="cannot enforce"):
            runner._auth_headers()
