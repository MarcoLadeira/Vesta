import json
from decimal import Decimal
import threading
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from _helpers import make_repo
from opaihub.agent_objectives import ObjectiveStore
from opaihub.journal_store import StaleWriterError
from opaihub.objective_execution import ObjectiveExecutor, worker_prompt
from opaihub.objective_worker import authorize_request
from opaihub.receipt import verify_receipt
from opaihub.state import state_dir


def task(name, **extra):
    return {
        "name": name,
        "objective": f"Update {name}",
        "intended_paths": [name + ".txt"],
        **extra,
    }


def test_receipts_reconcile_exact_costs_and_detect_tampering(tmp_path):
    store = ObjectiveStore(tmp_path)
    obj = store.create("Update", [task("a"), task("b")])
    oid = obj["objective_id"]
    first = store.claim_next(oid, "owner-a")
    second = store.claim_next(oid, "owner-b")
    store.record_cost(oid, None, "planner", "0.000000000000000001")
    store.record_cost(oid, first["assignment_id"], "first", "0.1")
    store.record_cost(oid, second["assignment_id"], "second", None)
    receipt = store.snapshot(oid)["receipt"]
    assert receipt["cost_usd"] == "0.100000000000000001"
    assert Decimal(receipt["cost_components"]["coordinator_usd"]) == Decimal(
        "0.000000000000000001"
    )
    assert receipt["cost_complete"] is False
    assert verify_receipt(tmp_path, receipt)["content_verified"]
    store.record_cost(oid, second["assignment_id"], "second", "0.2")
    for row, owner in [(first, "owner-a"), (second, "owner-b")]:
        store.finish_assignment(
            oid, row["assignment_id"], owner, row["fence"], status="completed"
        )
    snapshot = store.snapshot(oid)
    assert snapshot["receipt"]["cost_usd"] == "0.300000000000000001"
    assert snapshot["receipt"]["cost_complete"] is True
    assert snapshot["receipt"]["provisional"] is True
    assert all(
        verify_receipt(tmp_path, row["receipt"])["content_verified"]
        for row in snapshot["assignments"]
    )
    snapshot["receipt"]["cost_usd"] = "0"
    assert verify_receipt(tmp_path, snapshot["receipt"])["status"] == "TAMPERED"


def test_interrupted_owner_acknowledges_without_claiming_success(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [task("a")])["objective_id"]
    row = store.claim_next(oid, "original")
    with pytest.raises(StaleWriterError):
        store.acknowledge_interrupted(
            oid, "original", row["fence"], assignment_id=row["assignment_id"]
        )
    store.recover_expired(oid, now="9999-01-01T00:00:00+00:00")
    with pytest.raises(StaleWriterError):
        store.acknowledge_interrupted(
            oid, "other", row["fence"], assignment_id=row["assignment_id"]
        )
    result = store.acknowledge_interrupted(
        oid,
        "original",
        row["fence"],
        assignment_id=row["assignment_id"],
        changed_files=["a.txt"],
    )
    assert result["status"] == "needs-attention"
    assert result["assignments"][0]["owner"] == ""
    assert result["assignments"][0]["changed_files"] == ["a.txt"]
    assert result["cost_complete"] is False


def test_sequential_eligibility_blocks_disjoint_workers(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [task("a", parallel_eligible=False), task("b")])[
        "objective_id"
    ]
    store.claim_next(oid, "a")
    assert store.claim_next(oid, "b") is None
    waiting = store.snapshot(oid)["assignments"][1]["admission"]
    assert waiting["waiting_for_owners"] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"owner": "impostor"},
        {"fence": 999},
        {"task_id": "unrelated"},
        {"worktree": "elsewhere"},
    ],
)
def test_worker_rejects_changed_launch_identity(tmp_path, changes):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [task("a")], mode="plan", model="local:chosen")[
        "objective_id"
    ]
    row = store.claim_next(oid, "owner")
    target = tmp_path / "worker"
    store.attach_worktree(
        oid,
        row["assignment_id"],
        "owner",
        row["fence"],
        worktree=str(target),
        branch="codex/worker",
        lease_id="lease",
        base_sha="abc",
    )
    packet = {
        "authority_root": str(tmp_path),
        "objective_id": oid,
        "run_id": row["run_id"],
        "task_id": row["task_id"],
        "owner": "owner",
        "fence": row["fence"],
        "worktree": str(target),
        "prompt": "Implement",
        "mode": "full-auto",
        "allow_cloud": True,
        "model_id": "paid:other",
    }
    directory = state_dir(tmp_path) / "objectives" / "workers" / row["run_id"]
    lease = SimpleNamespace(
        run_id=row["run_id"],
        task_id=row["task_id"],
        path=str(target),
        owner="owner",
        state="active",
        lease_id="lease",
    )
    with mock.patch(
        "opaihub.worktree_leases.WorktreeManager.list", return_value=[lease]
    ):
        authorized = authorize_request(
            packet, directory / "request.json", directory / "response.json"
        )
        assert (
            authorized["mode"],
            authorized["model_id"],
            authorized["allow_cloud"],
        ) == ("plan", "local:chosen", False)
        with pytest.raises(ValueError):
            authorize_request(
                {**packet, **changes},
                directory / "request.json",
                directory / "response.json",
            )


def test_dependency_reports_are_bounded_and_exclude_unrelated_answers():
    objective = {
        "objective": "Update",
        "history": ["PRIVATE HISTORY"],
        "assignments": [
            {
                "name": "a",
                "assignment_id": "a-id",
                "run_id": "a-run",
                "status": "completed",
                "result": {"handoff": {"summary": "Useful finding " * 3000}},
            },
            {
                "name": "b",
                "status": "completed",
                "result": {"handoff": {"summary": "UNRELATED SECRET"}},
            },
        ],
    }
    prompt = worker_prompt(objective, task("c", depends_on=["a"]))
    assert "Useful finding" in prompt
    assert "UNRELATED SECRET" not in prompt and "PRIVATE HISTORY" not in prompt
    packet = json.loads(prompt.split("Task data follows:\n", 1)[1])
    assert packet["dependency_reports"][0]["summary_truncated"] is True
    assert len(json.dumps(packet, ensure_ascii=False)) <= 22000


def test_incremental_cost_reader_handles_partial_lines_and_late_start_events(tmp_path):
    from opaihub.ledger import ledger_path

    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [task("a")])["objective_id"]
    row = store.claim_next(oid, "owner")
    path = ledger_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "assignment_run_id": row["run_id"],
        "call_id": "call-a",
        "event_type": "model_call_started",
    }
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    executor = ObjectiveExecutor(tmp_path, store=store)
    executor._sync_costs(store.snapshot(oid))
    assert store.snapshot(oid)["cost_complete"] is False
    final = {
        **event,
        "event_type": "model_call_finalized",
        "cost_usd": "0.123456789012345678",
        "cost_usd_provenance": "actual",
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(final))
    executor._sync_costs(store.snapshot(oid))
    assert store.snapshot(oid)["cost_usd"] == "0"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + json.dumps(event) + "\n")
    executor._sync_costs(store.snapshot(oid))
    assert store.snapshot(oid)["cost_usd"] == "0.123456789012345678"
    store.finish_assignment(
        oid, row["assignment_id"], "owner", row["fence"], status="completed"
    )
    assert store.snapshot(oid)["cost_complete"] is True


def test_invalid_planner_contract_releases_owner(tmp_path):
    make_repo(tmp_path, files={"a.txt": "old"}, commit=True)
    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [])["objective_id"]

    def worker(*args):
        return {
            "status": "completed",
            "answer": json.dumps(
                {"assignments": [task("a", dependencies=["missing"])]}
            ),
            "cost_usd": "0",
            "measurement_kind": "actual",
        }

    result = ObjectiveExecutor(tmp_path, store=store, worker=worker).plan(oid)
    assert result["status"] == "needs-attention"
    assert result["planning"]["owner"] == ""
    assert result["assignments"] == []


def test_objective_waiting_behind_another_continues_without_manual_restart(tmp_path):
    make_repo(tmp_path, files={"a.txt": "old", "b.txt": "old"}, commit=True)
    store = ObjectiveStore(tmp_path)
    first = store.create("First", [task("a")], project_limit=1)["objective_id"]
    second = store.create("Second", [task("b")], project_limit=1)["objective_id"]
    started, waiting, release = threading.Event(), threading.Event(), threading.Event()

    def worker(packet, cancel, activity):
        name = packet["assignment"]["name"]
        if name == "a":
            started.set()
            assert release.wait(30)
        (Path(packet["worktree"]) / (name + ".txt")).write_text("changed")
        return {"status": "completed", "cost_usd": "0", "measurement_kind": "actual"}

    one, two = (
        ObjectiveExecutor(tmp_path, worker=worker),
        ObjectiveExecutor(tmp_path, worker=worker),
    )
    original_claim = two.store.claim_next

    def claim(*args):
        result = original_claim(*args)
        if result is None:
            waiting.set()
        return result

    with (
        mock.patch.object(
            one, "reconcile", side_effect=lambda oid, cancel: store.snapshot(oid)
        ),
        mock.patch.object(
            two, "reconcile", side_effect=lambda oid, cancel: store.snapshot(oid)
        ),
        mock.patch.object(two.store, "claim_next", side_effect=claim),
        ThreadPoolExecutor(2) as pool,
    ):
        a = pool.submit(one.run, first)
        try:
            assert started.wait(30)
            b = pool.submit(two.run, second)
            assert waiting.wait(30)
        finally:
            release.set()
        assert a.result(40)["assignments"][0]["status"] == "completed"
        assert b.result(40)["assignments"][0]["status"] == "completed"


def test_worker_process_validates_real_lease_and_returns_attributable_evidence(
    tmp_path,
):
    from opai.agents_bridge import objective_worktree_path
    from opaihub.objective_execution import run_worker_process

    make_repo(tmp_path, files={"a.txt": "old"}, commit=True)
    store = ObjectiveStore(tmp_path)
    oid = store.create("Update", [task("a")], mode="plan")["objective_id"]
    script = (
        "import sys; from opaihub import gui_pipeline, objective_worker, objective_routing; "
        "objective_routing.select_worker_route=lambda *a,**kw: "
        "{'allowed':True,'model_id':'fixture:local','provider':'fixture','endpoint':None}; "
        "gui_pipeline.handle_gui_message=lambda root,prompt,**kw: "
        "{'status':'completed','answer':'Local isolated response',"
        "'objective_cost_events':[{'operation_key':kw['run_id']+'-local',"
        "'amount_usd':'0','measurement_kind':'actual'}]}; "
        "raise SystemExit(objective_worker.main(sys.argv[1:]))"
    )

    def worker(packet, cancel, activity):
        directory = state_dir(tmp_path) / "objectives" / "workers" / packet["run_id"]
        return run_worker_process(
            packet,
            directory,
            cancel,
            activity,
            argv=[
                sys.executable,
                "-c",
                script,
                str(directory / "request.json"),
                str(directory / "response.json"),
            ],
        )

    executor = ObjectiveExecutor(tmp_path, store=store, worker=worker)
    row = store.claim_next(oid, executor.owner)
    executor._assignment(oid, row, threading.Event())
    result = store.snapshot(oid)
    assert result["assignments"][0]["status"] == "completed", result["assignments"][0][
        "result"
    ]
    assert result["assignments"][0]["result"]["answer"] == "Local isolated response"
    assert result["cost_usd"] == "0" and result["cost_complete"]
    assert (
        objective_worktree_path(
            tmp_path, {"objective_id": oid, "assignment_id": row["assignment_id"]}
        )
        == Path(result["assignments"][0]["worktree"]).resolve()
    )
