import pytest

from vestahub.agent_objectives import ObjectiveStore
from vestahub.objective_execution import worker_prompt


def team(tmp_path):
    store = ObjectiveStore(tmp_path)
    obj = store.create(
        "Build authentication",
        [
            {
                "name": "auth",
                "objective": "Implement tokens",
                "intended_paths": ["src/auth.py"],
            }
        ],
        allow_cloud=False,
    )
    return store, obj["objective_id"], obj["assignments"][0]["assignment_id"]


def change(store, oid, action, aid=None, **value):
    return store.control(
        oid, action, aid, {"revision": store.snapshot(oid)["team_revision"], **value}
    )


def test_messages_retain_identity_context_and_active_model(tmp_path):
    store, oid, aid = team(tmp_path)
    running = store.claim_next(oid, "worker")
    change(
        store,
        oid,
        "agent_settings",
        aid,
        model="account:claude:opus",
        group="Authentication",
    )
    current = store.snapshot(oid)["assignments"][0]
    assert current["model"] == running["model"] == "auto"
    obj = change(
        store, oid, "agent_message", aid, message="Explain the token expiry decision"
    )
    followup = obj["assignments"][-1]
    assert followup["agent_id"] == aid
    assert followup["display_name"] == current["display_name"]
    assert followup["depends_on"] == [running["name"]]
    assert followup["intended_paths"] == running["intended_paths"]
    assert followup["model"] == "account:claude:opus"
    assert obj["allow_cloud"] is False
    assert store.claim_next(oid, "other") is None
    obj = store.finish_assignment(
        oid,
        aid,
        "worker",
        running["fence"],
        status="completed",
        result={"handoff": {"summary": "Tokens expire after ten minutes"}},
    )
    assert "Tokens expire after ten minutes" in worker_prompt(obj, followup)
    change(store, oid, "agent_message", aid, message="Now check refresh tokens")
    last = store.snapshot(oid)["assignments"][-1]
    assert last["depends_on"] == [followup["name"]]
    store.control(oid, "rename", last["assignment_id"], "Ada")
    reopened = ObjectiveStore(tmp_path).snapshot(oid)
    assert {a["display_name"] for a in reopened["assignments"]} == {"Ada"}
    assert {a["group"] for a in reopened["assignments"]} == {"Authentication"}
    assert (
        reopened["assignments"][1]["user_message"]
        == "Explain the token expiry decision"
    )
    assert reopened["assignments"][0]["finished_at"]


def test_held_agent_can_be_connected_then_started_with_real_handoff(tmp_path):
    store, oid, aid = team(tmp_path)
    obj = change(
        store,
        oid,
        "add_agent",
        name="Sam",
        objective="Review tokens",
        start=False,
        group="Security",
    )
    reviewer = obj["assignments"][-1]
    rid = reviewer["assignment_id"]
    assert reviewer["held"] and reviewer["team_controls"]["can_start"]
    obj = change(store, oid, "connect_agents", rid, source_id=aid, connected=True)
    assert obj["assignments"][-1]["depends_on"] == ["auth"]
    running = store.claim_next(oid, "worker")
    change(store, oid, "start_agent", rid)
    assert store.claim_next(oid, "second") is None
    store.finish_assignment(
        oid,
        aid,
        "worker",
        running["fence"],
        status="completed",
        result={"handoff": {"summary": "Implemented expiration validation"}},
    )
    next_task = store.claim_next(oid, "second")
    assert next_task["assignment_id"] == rid
    assert "Implemented expiration validation" in worker_prompt(
        store.snapshot(oid), next_task
    )
    with pytest.raises(ValueError, match="before"):
        change(store, oid, "connect_agents", rid, source_id=aid, connected=False)


def test_stale_changes_cycles_and_cross_objective_edges_are_atomic(tmp_path):
    store, oid, aid = team(tmp_path)
    obj = change(store, oid, "add_agent", objective="Review tokens", start=False)
    rid = obj["assignments"][-1]["assignment_id"]
    obj = change(store, oid, "connect_agents", rid, source_id=aid, connected=True)
    with pytest.raises(ValueError, match="cycle"):
        change(store, oid, "connect_agents", aid, source_id=rid, connected=True)
    other = store.create(
        "Other objective", [{"name": "other", "objective": "Other work"}]
    )
    with pytest.raises(ValueError):
        change(
            store,
            oid,
            "connect_agents",
            rid,
            source_id=other["assignments"][0]["assignment_id"],
            connected=True,
        )
    with pytest.raises(ValueError, match="changed"):
        store.control(
            oid,
            "add_agent",
            value={"revision": obj["team_revision"] - 1, "objective": "Duplicate"},
        )
    assert len(store.snapshot(oid)["assignments"]) == 2
    assert store.snapshot(oid)["team_revision"] == obj["team_revision"]


def test_live_activity_does_not_invalidate_user_team_revision(tmp_path):
    store, oid, aid = team(tmp_path)
    obj = store.snapshot(oid)
    running = store.claim_next(oid, "worker")
    store.update_activity(oid, aid, "worker", running["fence"], activity="Read auth.py")
    result = store.control(
        oid,
        "agent_message",
        aid,
        {"revision": obj["team_revision"], "message": "Check edge cases"},
    )
    with pytest.raises(ValueError, match="changed"):
        store.control(
            oid,
            "agent_message",
            aid,
            {"revision": obj["team_revision"], "message": "Check edge cases"},
        )
    assert len(result["assignments"]) == 2


def test_groups_and_layout_are_durable_and_do_not_change_execution(tmp_path):
    store, oid, aid = team(tmp_path)
    running = store.claim_next(oid, "worker")
    obj = change(store, oid, "add_agent", objective="Review tokens", start=False)
    rid = obj["assignments"][-1]["assignment_id"]
    change(store, oid, "group_agents", assignment_ids=[aid, rid], group="Sign in")
    points = {aid: {"x": 42, "y": 80}, rid: {"x": 400, "y": 80}}
    change(store, oid, "team_layout", positions=points)
    reopened = ObjectiveStore(tmp_path).snapshot(oid)
    assert reopened["team_layout"] == points
    assert {a["group"] for a in reopened["assignments"]} == {"Sign in"}
    active = reopened["assignments"][0]
    assert (active["run_id"], active["fence"], active["owner"], active["model"]) == (
        running["run_id"],
        running["fence"],
        running["owner"],
        running["model"],
    )
    for bad in (
        {"foreign": {"x": 1, "y": 2}},
        {aid: {"x": float("nan"), "y": 2}},
        {aid: {"x": True, "y": 2}},
    ):
        with pytest.raises(ValueError):
            change(store, oid, "team_layout", positions=bad)
    with pytest.raises(ValueError):
        change(store, oid, "group_agents", assignment_ids=[aid, "foreign"], group="Bad")
    assert ObjectiveStore(tmp_path).snapshot(oid)["team_layout"] == points


def test_integration_ownership_and_task_limit_block_new_work(tmp_path):
    store, oid, aid = team(tmp_path)
    with store._db(True) as db:
        obj = store._load(db, oid)
        obj["integration"]["owner"] = "integrator"
        store._save_obj(db, obj)
    with pytest.raises(ValueError):
        change(store, oid, "agent_message", aid, message="Must not race integration")
    assert not store.snapshot(oid)["team_controls"]["can_add"]
    full = store.create(
        "Full team", [{"name": f"task-{i}", "objective": "Work"} for i in range(32)]
    )
    assert not full["team_controls"]["can_add"]
    with pytest.raises(ValueError, match="32"):
        change(store, full["objective_id"], "add_agent", objective="Too many")


def test_unstarted_blocked_receiver_can_disconnect_without_rewriting_executed_work(
    tmp_path,
):
    store, oid, aid = team(tmp_path)
    obj = change(store, oid, "add_agent", objective="Review tokens", start=False)
    rid = obj["assignments"][-1]["assignment_id"]
    change(store, oid, "connect_agents", rid, source_id=aid, connected=True)
    running = store.claim_next(oid, "worker")
    store.finish_assignment(oid, aid, "worker", running["fence"], status="failed")
    blocked = store.snapshot(oid)["assignments"][-1]
    assert blocked["status"] == "blocked" and blocked["team_controls"]["can_connect"]
    obj = change(store, oid, "connect_agents", rid, source_id=aid, connected=False)
    assert obj["assignments"][-1]["status"] == "pending"
    assert obj["assignments"][-1]["held"]
    assert obj["assignments"][0]["status"] == "failed"


def test_missing_historical_message_timestamp_is_not_replaced_with_now():
    from vesta.gui_recents import _clean_messages

    assert (
        _clean_messages([{"role": "user", "text": "An old question"}])[0]["timestamp"]
        == ""
    )
