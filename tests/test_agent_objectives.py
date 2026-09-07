from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal
import subprocess
import sys

import pytest

from opaihub.agent_objectives import ObjectiveStore
from opaihub.journal_store import StaleWriterError, open_store


def plan(name, paths=None, **kwargs):
    return dict(
        name=name, objective=f"Implement {name}", intended_paths=paths or [], **kwargs
    )


def claim_in_process(root, objective_id, owner):
    return ObjectiveStore(root).claim_next(objective_id, owner)


def verified_integration(store, oid, root):
    from opaihub.verification_execution import (
        VerificationExecutionContext,
        execute_policy,
        persist_verification_manifest,
    )
    from opaihub.verification_policy import (
        PolicyCheck,
        PolicySource,
        VerificationPolicy,
    )

    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Tests",
            "-c",
            "user.email=tests@example.test",
            "-c",
            "core.hooksPath=",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    obj = store.snapshot(oid)
    policy = VerificationPolicy(
        status="ready",
        classification={"mode": "implement", "edit_capable": True},
        checks=(
            PolicyCheck(
                "unit",
                "unit",
                "required",
                "Verify integration",
                command=(sys.executable, "-c", "print('verified')"),
            ),
        ),
        sources=(PolicySource("builtin", "Test policy"),),
    )
    context = VerificationExecutionContext(
        task_id=obj["task_id"],
        run_id=obj["run_id"] + "-integration",
        worktree=root,
        repository_id="test",
        head_sha=sha,
    )
    manifest = execute_policy(policy, context)
    ref = persist_verification_manifest(root, manifest)
    return dict(
        worktree=str(root),
        verification={"passed": True, "manifest": ref.to_dict()},
        result={"head_sha": sha},
    )


def test_empty_objective_waits_for_validated_plan(tmp_path):
    store = ObjectiveStore(tmp_path)
    obj = store.create("Ship isolated tasks")
    assert obj["status"] == "planning"
    assert store.claim_next(obj["objective_id"], "worker") is None
    obj = store.set_plan(obj["objective_id"], [plan("api", ["src/api.py"])])
    assert obj["status"] == "ready"
    assert len(store.list_objectives()) == 1
    with open_store(tmp_path) as db:
        assert (
            db.execute(
                "SELECT requested_outcome FROM tasks WHERE task_id=?", (obj["task_id"],)
            ).fetchone()[0]
            == "Ship isolated tasks"
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM events WHERE run_id=?", (obj["run_id"],)
            ).fetchone()[0]
            >= 2
        )


@pytest.mark.parametrize(
    "assignments",
    [
        [plan("a", ["../escape"])],
        [plan("a", ["C:\\escape"])],
        [plan("a", ["/escape"])],
        [plan("a", ["src/../../escape"])],
        [plan("a", [".git/config"])],
        [plan("a", ["src/a"], depends_on=["missing"])],
        [plan("a", depends_on=["b"]), plan("b", depends_on=["a"])],
        [plan("a"), plan("a")],
        [plan("a", risk="impossible")],
        [plan("a", depends_on="b")],
        [plan("a", capabilities="shell")],
        [plan("a", estimated_cost_usd="NaN")],
        [plan("a", budget_usd="-1")],
        [dict(name="a", objective="")],
    ],
)
def test_invalid_plans_never_persist(tmp_path, assignments):
    store = ObjectiveStore(tmp_path)
    with pytest.raises(ValueError):
        store.create("Task", assignments)
    assert store.list_objectives() == []


def test_dependencies_overlap_and_failure_isolation(tmp_path):
    store = ObjectiveStore(tmp_path)
    obj = store.create(
        "Task",
        [
            plan("a", ["src"]),
            plan("b", ["src/b.py"]),
            plan("c", ["tests"], depends_on=["a"]),
            plan("d", ["docs"]),
        ],
    )
    oid = obj["objective_id"]
    a = store.claim_next(oid, "a")
    d = store.claim_next(oid, "d")
    assert [a["name"], d["name"]] == ["a", "d"]
    store.finish_assignment(oid, a["assignment_id"], "a", a["fence"], status="failed")
    b = store.claim_next(oid, "b")
    assert b["name"] == "b"
    assert (
        next(x for x in store.snapshot(oid)["assignments"] if x["name"] == "c")[
            "status"
        ]
        == "blocked"
    )


def test_unknown_scopes_serialize_across_objectives(tmp_path):
    store = ObjectiveStore(tmp_path)
    first = store.create("First", [plan("a")])
    second = store.create("Second", [plan("b", ["src/b"])])
    assert store.claim_next(first["objective_id"], "a")
    assert store.claim_next(second["objective_id"], "b") is None


def test_project_admission_is_atomic_across_processes(tmp_path):
    store = ObjectiveStore(tmp_path)
    objectives = [
        store.create(str(i), [plan(str(i), [f"src/{i}"])], project_limit=1)
        for i in range(4)
    ]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(claim_in_process, str(tmp_path), obj["objective_id"], str(i))
            for i, obj in enumerate(objectives)
        ]
        assert sum(f.result() is not None for f in futures) == 1


def test_budget_reserves_concurrent_work_and_unknown_cost_blocks_admission(tmp_path):
    store = ObjectiveStore(tmp_path)
    obj = store.create(
        "Task",
        [
            plan("a", ["a"], estimated_cost_usd="0.6"),
            plan("b", ["b"], estimated_cost_usd="0.6"),
        ],
        budget_usd="1",
    )
    oid = obj["objective_id"]
    a = store.claim_next(oid, "a")
    assert store.claim_next(oid, "b") is None
    store.record_cost(oid, a["assignment_id"], "cost-a", None)
    store.finish_assignment(
        oid, a["assignment_id"], "a", a["fence"], status="completed"
    )
    assert store.claim_next(oid, "b") is None
    assert store.snapshot(oid)["cost_complete"] is False


def test_exact_idempotent_costs_include_failed_and_parent_operations(tmp_path):
    store = ObjectiveStore(tmp_path)
    obj = store.create("Task", [plan("a", ["a"])])
    oid, aid = obj["objective_id"], obj["assignments"][0]["assignment_id"]
    store.record_cost(oid, aid, "one", "0.10000000000000000001")
    store.record_cost(oid, None, "two", "0.2")
    store.record_cost(oid, aid, "one", "0.10000000000000000001")
    assert Decimal(store.snapshot(oid)["cost_usd"]) == Decimal("0.30000000000000000001")
    with pytest.raises(ValueError):
        store.record_cost(oid, aid, "one", "2")


def test_stop_and_expiry_hold_capacity_until_confirmed_termination(tmp_path):
    store = ObjectiveStore(tmp_path)
    obj = store.create("Task", [plan("a", ["a"]), plan("b", ["b"])], max_parallel=1)
    oid = obj["objective_id"]
    a = store.claim_next(oid, "worker", lease_seconds=1)
    stopped = store.control(oid, "stop", a["assignment_id"])
    assert stopped["assignments"][0]["status"] == "stopping"
    assert store.claim_next(oid, "other") is None
    store.finish_assignment(
        oid, a["assignment_id"], "worker", a["fence"], status="cancelled"
    )
    b = store.claim_next(oid, "other", lease_seconds=1)
    recovered = store.recover_expired(now="2999-01-01T00:00:00+00:00")
    assert recovered
    snap = store.snapshot(oid)
    assert snap["status"] == "needs-attention"
    with pytest.raises(StaleWriterError):
        store.heartbeat(oid, b["assignment_id"], "other", b["fence"])
    assert store.claim_next(oid, "third") is None


def test_controls_and_integration_require_verified_completion(tmp_path):
    store = ObjectiveStore(tmp_path)
    obj = store.create("Task", [plan("a", ["a"]), plan("b", ["b"])])
    oid = obj["objective_id"]
    store.control(oid, "pause")
    assert store.claim_next(oid, "worker") is None
    store.control(oid, "prioritize", obj["assignments"][1]["assignment_id"])
    store.control(oid, "resume")
    b = store.claim_next(oid, "worker")
    assert b["name"] == "b"
    store.attach_worktree(
        oid,
        b["assignment_id"],
        "worker",
        b["fence"],
        worktree="tree",
        branch="branch",
        lease_id="lease",
        base_sha="sha",
    )
    store.update_activity(
        oid, b["assignment_id"], "worker", b["fence"], activity="testing"
    )
    assert store.begin_integration(oid, "integrator") is None
    store.finish_assignment(
        oid,
        b["assignment_id"],
        "worker",
        b["fence"],
        status="completed",
        changed_files=["b"],
    )
    a = store.claim_next(oid, "worker")
    store.finish_assignment(
        oid, a["assignment_id"], "worker", a["fence"], status="completed"
    )
    integration = store.begin_integration(oid, "integrator")
    assert integration["fence"] > 0
    with pytest.raises(ValueError):
        store.finish_integration(
            oid, "integrator", integration["fence"], status="completed"
        )
    evidence = verified_integration(store, oid, tmp_path / "integration")
    completed = store.finish_integration(
        oid, "integrator", integration["fence"], status="completed", **evidence
    )
    assert completed["status"] == "completed"


def test_create_replay_is_idempotent_and_rejects_changed_contract(tmp_path):
    store = ObjectiveStore(tmp_path)
    first = store.create("Task", task_id="task", run_id="run")
    second = store.create("Task", task_id="task", run_id="run")
    assert first["objective_id"] == second["objective_id"]
    with pytest.raises(ValueError):
        store.create("Changed", task_id="task", run_id="run")


def test_planner_ownership_survives_restart_without_replay(tmp_path):
    store = ObjectiveStore(tmp_path)
    obj = store.create("Task")
    oid = obj["objective_id"]
    owned = store.begin_plan(oid, "planner")
    assert owned["owner"] == "planner"
    assert ObjectiveStore(tmp_path).begin_plan(oid, "other") is None
    store.recover_expired(now="2999-01-01T00:00:00+00:00")
    assert store.snapshot(oid)["status"] == "needs-attention"
    with pytest.raises(StaleWriterError):
        store.finish_plan(oid, "planner", owned["fence"], assignments=[plan("a")])


def test_planner_finishes_bounded_plan_with_fence(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Task")["objective_id"]
    owned = store.begin_plan(oid, "planner")
    with pytest.raises(ValueError):
        store.set_plan(oid, [plan("b")])
    ready = store.finish_plan(oid, "planner", owned["fence"], assignments=[plan("a")])
    assert ready["status"] == "ready"
    assert ready["planning"]["owner"] == ""


def test_stopping_integration_never_completes_after_cancellation(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Task", [plan("a")])["objective_id"]
    a = store.claim_next(oid, "worker")
    store.finish_assignment(
        oid, a["assignment_id"], "worker", a["fence"], status="completed"
    )
    integration = store.begin_integration(oid, "worker")
    assert store.control(oid, "stop")["status"] == "stopping"
    finished = store.finish_integration(
        oid,
        "worker",
        integration["fence"],
        status="completed",
        verification={"passed": True},
    )
    assert finished["status"] == "cancelled"


def test_active_cost_is_unknown_then_reconciles_exactly(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Task", [plan("a")])["objective_id"]
    a = store.claim_next(oid, "worker")
    assert store.snapshot(oid)["cost_complete"] is False
    assert store.snapshot(oid)["assignments"][0]["cost_complete"] is False
    store.record_cost(oid, a["assignment_id"], "operation", None)
    store.record_cost(oid, a["assignment_id"], "operation", "0.25")
    store.finish_assignment(
        oid, a["assignment_id"], "worker", a["fence"], status="failed"
    )
    final = store.snapshot(oid)
    assert final["cost_usd"] == "0.25"
    assert final["cost_complete"] is True
    assert final["assignments"][0]["last_owner"] == "worker"
    with open_store(tmp_path) as db:
        assert (
            db.execute(
                "SELECT terminal_verdict FROM runs WHERE run_id=?", (a["run_id"],)
            ).fetchone()[0]
            == "failed"
        )


@pytest.mark.parametrize("amount", ["1e999999999", "1e-999999999"])
def test_cost_exponent_is_bounded(tmp_path, amount):
    with pytest.raises(ValueError):
        ObjectiveStore(tmp_path).create("Task", budget_usd=amount)


def test_pause_survives_worker_and_planner_completion(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Task")["objective_id"]
    owned = store.begin_plan(oid, "planner")
    store.control(oid, "pause")
    ready = store.finish_plan(oid, "planner", owned["fence"], assignments=[plan("a")])
    assert ready["status"] == "paused"
    assert store.control(oid, "resume")["status"] == "ready"
    a = store.claim_next(oid, "worker")
    store.control(oid, "pause")
    finished = store.finish_assignment(
        oid, a["assignment_id"], "worker", a["fence"], status="completed"
    )
    assert finished["status"] == "paused"
    assert finished["paused_from"] == "ready-to-integrate"
    assert store.control(oid, "resume")["status"] == "ready-to-integrate"


def test_phase_heartbeat_fences_stale_planner(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Task")["objective_id"]
    phase = store.begin_plan(oid, "planner", lease_seconds=1)
    updated = store.heartbeat_phase(
        oid, "planning", "planner", phase["fence"], lease_seconds=300
    )
    assert updated["expires_at"] > phase["expires_at"]
    with pytest.raises(StaleWriterError):
        store.heartbeat_phase(oid, "planning", "other", phase["fence"])


def test_integration_rejects_fabricated_verification(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Task", [plan("a")])["objective_id"]
    a = store.claim_next(oid, "worker")
    store.finish_assignment(
        oid, a["assignment_id"], "worker", a["fence"], status="completed"
    )
    owned = store.begin_integration(oid, "integrator")
    with pytest.raises(ValueError):
        store.finish_integration(
            oid,
            "integrator",
            owned["fence"],
            status="completed",
            verification={"passed": True},
        )


def test_read_snapshot_does_not_mix_concurrent_commits(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Task")["objective_id"]
    with store._db() as reader:
        assert (
            reader.execute("SELECT COUNT(*) FROM objective_cost_events").fetchone()[0]
            == 0
        )
        store.record_cost(oid, None, "concurrent", "0.3")
        assert (
            reader.execute("SELECT COUNT(*) FROM objective_cost_events").fetchone()[0]
            == 0
        )
    assert store.snapshot(oid)["cost_usd"] == "0.3"


def test_root_scope_serializes_and_normalizes_windows_protected_paths(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create("Task", [plan("a", ["."]), plan("b", ["src/b"])])["objective_id"]
    assert store.claim_next(oid, "one")
    assert store.claim_next(oid, "two") is None
    with pytest.raises(ValueError):
        store.create("Other", [plan("a", [".GIT/config"])])


def test_missing_final_cost_does_not_restore_budget_headroom(tmp_path):
    store = ObjectiveStore(tmp_path)
    oid = store.create(
        "Task",
        [
            plan("a", ["a"], estimated_cost_usd="0.6"),
            plan("b", ["b"], estimated_cost_usd="0.6"),
        ],
        budget_usd="1",
    )["objective_id"]
    a = store.claim_next(oid, "worker")
    store.finish_assignment(
        oid, a["assignment_id"], "worker", a["fence"], status="failed"
    )
    assert store.snapshot(oid)["cost_complete"] is False
    assert store.claim_next(oid, "other") is None
