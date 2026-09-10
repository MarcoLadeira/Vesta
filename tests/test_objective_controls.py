from pathlib import Path

import pytest

from _helpers import make_repo
from opaihub.agent_objectives import ObjectiveStore
from opaihub.journal_store import StaleWriterError
from opaihub.objective_execution import ObjectiveExecutor


def assignment(name, **extra):
    return {
        "name": name,
        "objective": "Update " + name,
        "intended_paths": [name + ".txt"],
        **extra,
    }


def test_predispatch_retry_is_fenced_and_preserves_history(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [assignment("a"), assignment("b", depends_on=["a"])])[
        "objective_id"
    ]
    row = store.claim_next(oid, "worker")
    store.record_cost(oid, row["assignment_id"], row["run_id"] + "-provider", "0")
    blocked = store.finish_assignment(
        oid,
        row["assignment_id"],
        "worker",
        row["fence"],
        status="needs-attention",
        result={
            "status": "blocked",
            "dispatch_state": "not-dispatched",
            "answer": "No eligible route",
        },
    )["assignments"][0]
    assert {"retry", "reroute", "budget"} <= set(blocked["allowed_actions"])
    store.control(oid, "reroute", row["assignment_id"], {"model": "free:groq:test"})
    assert store.claim_next(oid, "other") is None
    with pytest.raises(ValueError, match="stale"):
        store.control(oid, "retry", row["assignment_id"], {"run_id": "stale"})
    retried = store.control(
        oid, "retry", row["assignment_id"], {"run_id": row["run_id"]}
    )
    current = retried["assignments"][0]
    assert current["run_id"] != row["run_id"]
    assert current["attempts"][0]["run_id"] == row["run_id"]
    assert current["attempts"][0]["result"] == blocked["result"]
    assert current["cost_usd"] == "0"
    assert current["model"] == "free:groq:test"
    assert retried["assignments"][1]["status"] == "pending"
    with pytest.raises(ValueError):
        store.control(oid, "retry", row["assignment_id"], {"run_id": row["run_id"]})


@pytest.mark.parametrize(
    "marker,amount,changed",
    [
        (None, "0", []),
        ("dispatched", "0", []),
        ("not-dispatched", None, []),
        ("not-dispatched", "0.1", []),
        ("not-dispatched", "0", ["a.txt"]),
    ],
)
def test_retry_rejects_uncertain_dispatched_or_changed_attempts(
    tmp_path, marker, amount, changed
):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [assignment("a")])["objective_id"]
    row = store.claim_next(oid, "worker")
    store.record_cost(oid, row["assignment_id"], row["run_id"] + "-provider", amount)
    blocked = store.finish_assignment(
        oid,
        row["assignment_id"],
        "worker",
        row["fence"],
        status="needs-attention",
        changed_files=changed,
        result={"status": "blocked", "dispatch_state": marker},
    )["assignments"][0]
    assert "retry" not in blocked["allowed_actions"]
    with pytest.raises(ValueError):
        store.control(oid, "retry", row["assignment_id"], {"run_id": row["run_id"]})


@pytest.mark.parametrize("route_allowed", [False, True])
def test_worker_marks_only_preflight_denials_retryable(
    tmp_path, monkeypatch, route_allowed
):
    import json
    from unittest.mock import Mock
    from opaihub import objective_worker

    store = ObjectiveStore(tmp_path)
    obj = store.create("Update", [assignment("a", budget_usd="1")])
    row = store.claim_next(obj["objective_id"], "worker")
    packet = {
        "run_id": row["run_id"],
        "task_id": row["task_id"],
        "authority_root": str(tmp_path),
        "worktree": str(tmp_path / "worker"),
        "model_id": "account:codex:test",
        "routing": {"allowed": route_allowed, "reason": "No route", "blockers": []},
    }
    request, response = tmp_path / "request.json", tmp_path / "response.json"
    request.write_text("{}")
    monkeypatch.setattr(objective_worker, "authorize_request", lambda *args: packet)
    provider = Mock(side_effect=AssertionError("Provider dispatch is forbidden"))
    monkeypatch.setattr("opaihub.gui_pipeline.handle_gui_message", provider)
    assert objective_worker.main([str(request), str(response)]) == 0
    result = json.loads(response.read_text())
    assert result["dispatch_state"] == "not-dispatched"
    assert result["status"] == "blocked"
    assert result["objective_cost_events"][0]["amount_usd"] == "0"
    provider.assert_not_called()


def block(store, oid, row):
    return store.finish_assignment(
        oid,
        row["assignment_id"],
        "worker",
        row["fence"],
        status="failed",
        result={
            "status": "needs_command_approval",
            "command_approval": {
                "command": "git status",
                "reason": "Confirm operation",
            },
        },
    )["assignments"][0]


def test_pipeline_cannot_promote_its_blocked_result_to_predispatch_retry(
    tmp_path, monkeypatch
):
    import json
    from unittest.mock import Mock
    from opaihub import objective_worker

    store = ObjectiveStore(tmp_path)
    obj = store.create("Update", [assignment("a")])
    row = store.claim_next(obj["objective_id"], "worker")
    packet = {
        "run_id": row["run_id"],
        "task_id": row["task_id"],
        "authority_root": str(tmp_path),
        "worktree": str(tmp_path / "worker"),
        "model_id": "account:codex:test",
        "mode": "safe-auto",
        "prompt": "Update a",
        "routing": {"allowed": True},
    }
    request, response = tmp_path / "request.json", tmp_path / "response.json"
    request.write_text("{}")
    monkeypatch.setattr(objective_worker, "authorize_request", lambda *args: packet)
    monkeypatch.setattr(objective_worker.sys, "stdin", None)
    provider = Mock(
        return_value={"status": "blocked", "dispatch_state": "not-dispatched"}
    )
    monkeypatch.setattr("opaihub.gui_pipeline.handle_gui_message", provider)
    assert objective_worker.main([str(request), str(response)]) == 0
    result = json.loads(response.read_text())
    assert result["status"] == "blocked"
    assert "dispatch_state" not in result
    provider.assert_called_once()


def test_approval_is_request_bound_and_preserves_attempt_costs(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [assignment("a"), assignment("b", depends_on=["a"])])[
        "objective_id"
    ]
    first = store.claim_next(oid, "worker")
    store.record_cost(oid, first["assignment_id"], "first-operation", "0.125")
    row = block(store, oid, first)
    request = row["pending_approval"]["request_id"]
    assert "approve" in row["allowed_actions"]
    with pytest.raises(ValueError):
        store.control(oid, "approve", first["assignment_id"], {"request_id": "stale"})
    with pytest.raises(ValueError):
        store.control(
            oid,
            "approve",
            first["assignment_id"],
            {"request_id": request, "command": "other"},
        )
    approved = store.control(
        oid, "approve", first["assignment_id"], {"request_id": request}
    )
    current = approved["assignments"][0]
    assert current["run_id"] != first["run_id"]
    assert current["attempts"][0]["run_id"] == first["run_id"]
    assert current["cost_usd"] == "0.125"
    assert current["approval_grant"]["run_id"] == current["run_id"]
    assert current["approval_grant"]["command"] == "git status"
    assert approved["assignments"][1]["status"] == "pending"
    with pytest.raises(ValueError):
        store.control(oid, "approve", first["assignment_id"], {"request_id": request})


def test_review_uses_fenced_revision_and_preserves_previous_receipt(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Review", [assignment("a")])["objective_id"]
    row = store.claim_next(oid, "worker")
    store.record_cost(oid, row["assignment_id"], "one", "0")
    previous = store.finish_assignment(
        oid, row["assignment_id"], "worker", row["fence"], status="completed"
    )
    assert "request_review" in previous["allowed_actions"]
    requested = store.control(
        oid, "request_review", value={"revision": previous["revision"]}
    )
    assert requested["status"] == "ready"
    assert (
        requested["assignments"][0]["receipt"] == previous["assignments"][0]["receipt"]
    )
    review = requested["assignments"][1]
    assert review["role"] == "reviewer" and review["depends_on"] == ["a"]
    assert review["run_id"] != row["run_id"]
    assert len(requested["integration_history"]) == 1
    with pytest.raises(ValueError):
        store.control(oid, "request_review", value={"revision": previous["revision"]})


@pytest.mark.parametrize("tamper", [False, True])
def test_approval_continuation_transfers_partial_work_before_new_invocation(
    tmp_path, tamper
):
    root = tmp_path / "repository"
    root.mkdir()
    make_repo(
        root, files={"a.txt": "original", ".gitignore": ".opaihub/\n"}, commit=True
    )
    store = ObjectiveStore(root)
    oid = store.create("Update a", [assignment("a")])["objective_id"]
    paths = []

    def worker(packet, cancel, activity):
        worktree = Path(packet["worktree"])
        paths.append(worktree)
        if len(paths) == 1:
            (worktree / "a.txt").write_text("partial")
            return {
                "status": "needs_command_approval",
                "cost_usd": "0.1",
                "measurement_kind": "actual",
                "command_approval": {"command": "git status"},
            }
        assert (worktree / "a.txt").read_text() == "partial"
        (worktree / "a.txt").write_text("finished")
        return {"status": "completed", "cost_usd": "0.2", "measurement_kind": "actual"}

    executor = ObjectiveExecutor(
        root, worker=worker, worktree_root=tmp_path / "workers"
    )
    first = executor.run(oid)["assignments"][0]
    if tamper:
        (paths[0] / "a.txt").write_text("changed after approval request")
    store.control(
        oid,
        "approve",
        first["assignment_id"],
        {"request_id": first["pending_approval"]["request_id"]},
    )
    final = executor.run(oid)
    if tamper:
        assert len(paths) == 1
        assert final["assignments"][0]["status"] == "failed"
        assert (
            "Retained changes require review"
            in final["assignments"][0]["result"]["error"]
        )
        return
    assert len(paths) == 2 and paths[0] != paths[1]
    row = final["assignments"][0]
    assert row["status"] == "completed", row["result"]
    assert row["changed_files"] == ["a.txt"]
    assert row["cost_usd"] == "0.3"
    assert (paths[0] / "a.txt").read_text() == "partial"
    assert "approval_grant" not in row


def test_custody_prevents_finalization_until_exact_proof(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [assignment("a")])["objective_id"]
    row = store.claim_next(oid, "worker")
    identity = dict(assignment_id=row["assignment_id"])
    store.record_execution_custody(
        oid,
        "worker",
        row["fence"],
        "execution",
        **identity,
        guardian_pid=100,
        worker_pid=101,
        tree_kind="windows-job",
    )
    with pytest.raises(StaleWriterError):
        store.finish_assignment(
            oid, row["assignment_id"], "worker", row["fence"], status="completed"
        )
    store.interrupt_execution(oid, "worker", row["fence"], **identity)
    assert store.snapshot(oid)["assignments"][0]["owner"] == "worker"
    proof = {
        "execution_id": "wrong",
        "owner": "worker",
        "dispatch_fence": row["fence"],
        "tree_kind": "windows-job",
        "tree_terminated": True,
    }
    with pytest.raises(StaleWriterError):
        store.record_execution_termination(
            oid, "worker", row["fence"], "execution", **identity, proof=proof
        )
    proof["execution_id"] = "execution"
    store.record_execution_termination(
        oid, "worker", row["fence"], "execution", **identity, proof=proof
    )
    recovered = store.snapshot(oid)
    assert recovered["assignments"][0]["owner"] == ""
    assert recovered["assignments"][0]["status"] == "needs-attention"
    assert not recovered["cost_complete"]
    assert store.claim_next(oid, "new-worker") is None


def test_proof_before_expiration_is_consumed_only_after_owner_loss(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [assignment("a")])["objective_id"]
    row = store.claim_next(oid, "worker")
    identity = dict(assignment_id=row["assignment_id"])
    store.record_execution_custody(
        oid,
        "worker",
        row["fence"],
        "execution",
        **identity,
        guardian_pid=100,
        worker_pid=101,
        tree_kind="posix-group",
    )
    proof = {
        "execution_id": "execution",
        "owner": "worker",
        "dispatch_fence": row["fence"],
        "tree_kind": "posix-group",
        "tree_terminated": True,
    }
    store.record_execution_termination(
        oid, "worker", row["fence"], "execution", **identity, proof=proof
    )
    assert store.snapshot(oid)["assignments"][0]["owner"] == "worker"
    store.recover_expired(oid, now="9999-01-01T00:00:00+00:00")
    assert store.snapshot(oid)["assignments"][0]["owner"] == ""
