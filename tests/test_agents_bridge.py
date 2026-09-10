from __future__ import annotations

from unittest import mock

import pytest

from opaihub.gui_preferences import load_gui_preferences, save_gui_preferences


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
    from opai.agents_bridge import create_objective_payload

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
    with mock.patch("opai.agents_bridge.ObjectiveStore", return_value=store):
        create_objective_payload(tmp_path, payload)
        first = store.create.call_args
        create_objective_payload(tmp_path, payload)
        assert store.create.call_args == first
    assert first.kwargs["mode"] == "plan"
    assert first.kwargs["allow_cloud"] is False
    assert "history" not in first.kwargs
    assert first.kwargs["shared_context"] == ""


def test_control_targets_canonical_ids_and_never_grants_cloud_implicitly(tmp_path):
    from opai.agents_bridge import control_objective_payload

    executor = mock.Mock()
    executor.control.return_value = {"objective_id": "o", "status": "stopping"}
    with mock.patch("opai.agents_bridge.ObjectiveExecutor", return_value=executor):
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
    from opai.agents_bridge import create_objective_payload

    with mock.patch("opai.agents_bridge.ObjectiveStore") as store:
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
    from opai.agents_bridge import objectives_payload

    store = mock.Mock()
    store.list_objectives.return_value = [
        {"objective_id": "o", "status": "needs_attention"}
    ]
    with mock.patch("opai.agents_bridge.ObjectiveStore", return_value=store):
        result = objectives_payload(tmp_path)
    assert store.method_calls == [
        mock.call.recover_expired(),
        mock.call.list_objectives(),
    ]
    assert result["objectives"] == store.list_objectives.return_value
