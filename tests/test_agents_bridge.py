from __future__ import annotations

from unittest import mock

import pytest

from vestahub.gui_preferences import load_gui_preferences, save_gui_preferences


def test_team_timeline_is_ordered_durable_bounded_and_objective_scoped(tmp_path):
    from vestahub.agent_objectives import ObjectiveStore

    store = ObjectiveStore(tmp_path)
    obj = store.create("Repair API", [{"name": "api", "objective": "Repair auth"}])
    oid = obj["objective_id"]
    worker = store.claim_next(oid, "owner")
    aid, fence = worker["assignment_id"], worker["fence"]
    for message in ("Edited auth.ts", "Fixed token expiry"):
        store.update_activity(oid, aid, "owner", fence, activity=message)
    store.update_activity(
        oid,
        aid,
        "owner",
        fence,
        activity='{"title":"Streaming response","channel":"status"}',
    )
    other = store.create("Other task", [{"name": "other", "objective": "Private task"}])
    store.claim_next(other["objective_id"], "other-owner")
    timeline = ObjectiveStore(tmp_path).snapshot(oid)["timeline"]
    assert [e.get("activity") for e in timeline] == [
        None,
        "Edited auth.ts",
        "Fixed token expiry",
    ]
    assert all(e["assignment_id"] == aid and e["occurred_at"] for e in timeline)
    assert [e["sequence"] for e in timeline] == sorted(e["sequence"] for e in timeline)
    for index in range(81):
        store.update_activity(
            oid, aid, "owner", fence, activity=f"Recorded check {index}"
        )
    store.finish_assignment(
        oid,
        aid,
        "owner",
        fence,
        status="completed",
        verification={"summary": "28/28 tests passed"},
        result={"handoff": {"summary": "Fixed expiry handling"}},
    )
    snapshot = ObjectiveStore(tmp_path).snapshot(oid)
    assert snapshot["timeline_truncated"] is True
    assert len(snapshot["timeline"]) == 80
    assert snapshot["timeline"][-1]["verification_summary"] == "28/28 tests passed"
    assert snapshot["timeline"][-1]["summary"] == "Fixed expiry handling"


def test_unsupported_host_rejects_submission_before_creating_an_objective(tmp_path):
    from vesta.agents_bridge import create_objective_payload

    with (
        mock.patch("vestahub.process_tree.sys.platform", "darwin"),
        mock.patch("vesta.agents_bridge.ObjectiveStore") as store,
    ):
        with pytest.raises(ValueError, match="unavailable on this host"):
            create_objective_payload(
                tmp_path,
                {"text": "Repair parser", "agentsRuntime": {"supported": True}},
            )
        store.assert_not_called()


def test_agent_rename_is_durable_metadata_and_preserves_execution_identity(tmp_path):
    from vesta.agents_bridge import control_objective_payload
    from vestahub.agent_objectives import ObjectiveStore

    store = ObjectiveStore(tmp_path)
    created = store.create(
        "Repair modules",
        [{"name": "api", "objective": "Repair API", "intended_paths": ["api/"]}],
    )
    original = created["assignments"][0]
    result = control_objective_payload(
        tmp_path,
        {
            "objective_id": created["objective_id"],
            "assignment_id": original["assignment_id"],
            "action": "rename",
            "value": "Ada",
        },
    )
    renamed = ObjectiveStore(tmp_path).snapshot(created["objective_id"])["assignments"][
        0
    ]
    assert renamed["display_name"] == "Ada"
    assert result["objective"]["revision"] > created["revision"]
    for field in (
        "assignment_id",
        "task_id",
        "run_id",
        "name",
        "objective",
        "status",
        "model",
        "avatar_index",
    ):
        assert renamed[field] == original[field]
    with pytest.raises(ValueError):
        store.control(
            created["objective_id"], "rename", original["assignment_id"], "x" * 41
        )
    with pytest.raises(ValueError):
        store.control(created["objective_id"], "rename", "foreign-assignment", "Ada")


def test_multi_agent_preference_is_explicit_boolean_and_independent(tmp_path):
    save_gui_preferences(
        tmp_path,
        {"multi_agent_enabled": True, "default_mode": "plan", "default_model": "test"},
    )
    prefs = load_gui_preferences(tmp_path)
    assert prefs["multi_agent_enabled"] is True
    assert prefs["default_mode"] == "plan"
    assert prefs["default_model"] == "test"
    for value in ("true", "false", 1, [], {}):
        save_gui_preferences(tmp_path, {"multi_agent_enabled": value})
        assert load_gui_preferences(tmp_path)["multi_agent_enabled"] is False


def test_objective_submission_uses_stable_identity_and_bounded_user_authority(tmp_path):
    from vesta.agents_bridge import create_objective_payload

    store = mock.Mock()
    store.create.return_value = {"objective_id": "o"}
    payload = {
        "requestId": "request-1",
        "text": "Repair parser",
        "mode": "plan",
        "model": "local:coder",
        "allowCloud": "true",
        "history": ["private"],
    }
    with mock.patch("vesta.agents_bridge.ObjectiveStore", return_value=store):
        create_objective_payload(tmp_path, payload)
        first = store.create.call_args
        create_objective_payload(tmp_path, payload)
        assert store.create.call_args == first
    assert first.kwargs["mode"] == "plan"
    assert first.kwargs["allow_cloud"] is False
    assert "history" not in first.kwargs
    assert first.kwargs["shared_context"] == ""


def test_control_targets_canonical_ids_and_never_grants_cloud_implicitly(tmp_path):
    from vesta.agents_bridge import control_objective_payload

    executor = mock.Mock()
    executor.control.return_value = {"objective_id": "o", "status": "stopping"}
    with mock.patch("vesta.agents_bridge.ObjectiveExecutor", return_value=executor):
        result = control_objective_payload(
            tmp_path, {"objective_id": "o", "assignment_id": "a", "action": "cancel"}
        )
        assert result["ok"] is True
        executor.control.assert_called_once_with(
            "o", "stop", assignment_id="a", value=None
        )
        with pytest.raises(ValueError):
            control_objective_payload(
                tmp_path, {"objective_id": "o", "action": "allow_cloud"}
            )


def test_invalid_submission_never_reaches_runtime(tmp_path):
    from vesta.agents_bridge import create_objective_payload

    with mock.patch("vesta.agents_bridge.ObjectiveStore") as store:
        for payload in (
            [],
            {},
            {"text": "x", "mode": "do-anything"},
            {"text": "x", "maxParallel": True},
        ):
            with pytest.raises(ValueError):
                create_objective_payload(tmp_path, payload)
        store.assert_not_called()


def test_objective_listing_recovers_expired_owners_before_projection(tmp_path):
    from vesta.agents_bridge import objectives_payload

    store = mock.Mock()
    store.list_objectives.return_value = [
        {"objective_id": "o", "status": "needs_attention"}
    ]
    with mock.patch("vesta.agents_bridge.ObjectiveStore", return_value=store):
        result = objectives_payload(tmp_path)
    assert store.method_calls == [
        mock.call.recover_expired(),
        mock.call.list_objectives(),
    ]
    assert result["objectives"] == store.list_objectives.return_value


def test_objective_creation_captures_bypass_authority(tmp_path):
    from vesta.agents_bridge import create_objective_payload
    from vestahub.agent_objectives import ObjectiveStore

    created = create_objective_payload(
        tmp_path, {"text": "Update independent modules", "bypassPermissions": True}
    )
    assert created["bypass_permissions"] is True
    assert (
        ObjectiveStore(tmp_path).snapshot(created["objective_id"])["bypass_permissions"]
        is True
    )
