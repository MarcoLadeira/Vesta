from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

from opaihub import budget, gui_pipeline, ledger


def test_assignment_finances_use_parent_while_workspace_state_stays_local(tmp_path):
    from opaihub.execution_scope import assignment_scope, financial_root
    from opaihub.state import state_dir

    parent, child = tmp_path / "parent", tmp_path / "worker"
    with assignment_scope(child, parent, task_id="task-1", run_id="run-1"):
        assert financial_root(child) == parent.resolve()
        assert state_dir(child) == child / ".opaihub"
        event = ledger.record_event(child, "test", task="bounded task")
        assert event["assignment_run_id"] == "run-1"
        assert ledger.ledger_path(child) == parent / ".opaihub/ledger/usage.jsonl"
    assert financial_root(child) == child.resolve()
    assert not ledger.ledger_path(child).exists()
    assert ledger.read_events(parent)[0]["assignment_task_id"] == "task-1"


def test_execution_scope_does_not_leak_between_threads_or_exceptions(tmp_path):
    from opaihub.execution_scope import assignment_scope, financial_root

    child = tmp_path / "worker"
    with pytest.raises(RuntimeError):
        with assignment_scope(child, tmp_path, task_id="t", run_id="r"):
            with ThreadPoolExecutor(1) as pool:
                assert pool.submit(financial_root, child).result() == child.resolve()
            raise RuntimeError("worker failed")
    assert financial_root(child) == child.resolve()


def test_worker_budget_gate_uses_authority_root(tmp_path):
    from opaihub.execution_scope import assignment_scope

    child = tmp_path / "worker"
    with assignment_scope(child, tmp_path, task_id="t", run_id="r"):
        with mock.patch.object(
            budget, "load_budget", side_effect=RuntimeError("stop")
        ) as load:
            with pytest.raises(RuntimeError, match="stop"):
                budget.budget_gate(child)
            load.assert_called_once_with(tmp_path.resolve())


def test_managed_pipeline_restores_context_and_returns_all_attempt_costs(tmp_path):
    from opaihub.execution_scope import financial_root

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
