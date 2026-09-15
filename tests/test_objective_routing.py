from unittest import mock

import pytest

from vestahub import objective_routing as routing


def model(model_id, kind, **extra):
    return {"id": model_id, "kind": kind, "available": True, **extra}


def select(tmp_path, models, *, objective=None, assignment=None, usage=None, **kwargs):
    with mock.patch.object(
        routing.provider_usage, "usage_overview", return_value=usage or []
    ) as read:
        result = routing.select_worker_route(
            tmp_path,
            {
                "model": "auto",
                "mode": "safe-auto",
                "allow_cloud": True,
                **(objective or {}),
            },
            {"role": "implementer", **(assignment or {})},
            catalog={"models": models},
            now=100,
            **kwargs,
        )
        assert read.call_args.kwargs["probe"] is False
        return result


def test_no_cloud_authorization_excludes_free_and_paid(tmp_path):
    result = select(
        tmp_path,
        [model("free:groq:test", "free"), model("account:codex:test", "account")],
        objective={"allow_cloud": False},
    )
    assert not result["allowed"]
    assert len(result["blockers"]) == 2


def test_truthy_string_is_not_cloud_authorization(tmp_path):
    assert not select(
        tmp_path,
        [model("account:codex:test", "account")],
        objective={"allow_cloud": "false"},
    )["allowed"]


def test_planner_suggestion_does_not_override_user_model(tmp_path):
    result = select(
        tmp_path,
        [model("free:groq:test", "free"), model("account:codex:test", "account")],
        objective={"model": "account:codex:test"},
        assignment={"model": "free:groq:test", "route": "free"},
    )
    assert result["model_id"] == "account:codex:test"
    assert result["eligible_candidates"] == ["account:codex:test"]


def test_authorized_reroute_pins_exact_assignment_model(tmp_path):
    result = select(
        tmp_path,
        [model("free:groq:test", "free"), model("account:codex:test", "account")],
        objective={"model": "account:codex:test"},
        assignment={"model": "free:groq:test", "model_authorized": True},
    )
    assert result["model_id"] == "free:groq:test"


def test_unknown_explicit_model_never_falls_back(tmp_path):
    result = select(
        tmp_path,
        [model("free:groq:test", "free")],
        objective={"model": "account:codex:missing"},
    )
    assert not result["allowed"]
    assert result["model_id"] == "account:codex:missing"
    assert result["blockers"]


def test_assignment_capabilities_exclude_unsupported_and_partial(tmp_path):
    result = select(
        tmp_path,
        [model("account:claude:test", "account"), model("free:groq:test", "free")],
        assignment={"capabilities": ["structured_output"]},
    )
    assert not result["allowed"]
    assert len(result["blockers"]) == 2
    assert result["blockers"][0]["model_id"] == "account:claude:test"


def test_local_chat_route_is_concrete_and_cannot_claim_edit_tools(tmp_path):
    local = model(
        "ollama:test",
        "local",
        endpoint="http://127.0.0.1:11434",
        capabilities={"repo_editing": "supported"},
    )
    assert not select(tmp_path, [local], objective={"allow_cloud": False})["allowed"]
    result = select(tmp_path, [local], objective={"allow_cloud": False}, planning=True)
    assert result["allowed"]
    assert result["model_id"] == "ollama:test"
    assert result["capabilities"] == ["chat"]


def test_concrete_local_route_precedes_cloud_for_readonly(tmp_path):
    result = select(
        tmp_path,
        [
            model("free:groq:test", "free"),
            model("openai:test", "local", endpoint="http://127.0.0.1:1234/v1"),
        ],
        planning=True,
    )
    assert result["model_id"] == "openai:test"
    assert result["provider"] == "openai-compatible"
    assert "auto" not in result["eligible_candidates"]


@pytest.mark.parametrize(
    "official,allowed",
    [
        ({"available": True, "stale": False, "remaining": 0}, False),
        ({"available": True, "stale": True, "remaining": 0}, True),
        ({"available": False, "remaining": 0}, True),
        ({"available": True, "remaining": None}, True),
        ({"available": True, "remaining": "NaN"}, True),
    ],
)
def test_only_fresh_observed_exhaustion_blocks(tmp_path, official, allowed):
    usage = [
        {"provider": "groq", "official": official, "vestaTracked": {"calls": 999999}}
    ]
    result = select(tmp_path, [model("free:groq:test", "free")], usage=usage)
    assert result["allowed"] is allowed


def test_recent_provider_block_is_honored(tmp_path):
    with mock.patch.object(routing.provider_blocks, "is_blocked", return_value=True):
        result = select(tmp_path, [model("free:groq:test", "free")])
    assert not result["allowed"]
    assert "blocked" in str(result["blockers"])


def test_no_catalog_discovery_probes_cloud_accounts(tmp_path):
    with (
        mock.patch(
            "vesta.app_state.available_models", return_value={"models": []}
        ) as catalog,
        mock.patch.object(routing.provider_usage, "usage_overview", return_value=[]),
    ):
        routing.select_worker_route(tmp_path, {"model": "auto"}, {})
    assert catalog.call_args.kwargs == {
        "discover_local": True,
        "discover_accounts": False,
    }


def test_paid_direct_api_is_blocked_until_pipeline_supports_it(tmp_path):
    result = select(tmp_path, [model("paid:deepseek:deepseek-v4-flash", "paid")])
    assert not result["allowed"]
    assert "dispatch is not supported" in str(result["blockers"])


def test_dynamic_model_evidence_can_narrow_edit_capability(tmp_path):
    result = select(
        tmp_path,
        [
            model(
                "account:codex:test",
                "account",
                capabilities={"repo_editing": "unsupported"},
            ),
            model("free:groq:test", "free"),
        ],
    )
    assert result["model_id"] == "free:groq:test"


def test_free_is_cheaper_than_account_and_edit_restrictions_are_per_assignment(
    tmp_path,
):
    models = [
        model("account:codex:test", "account"),
        model("free:groq:test", "free", repo_editing=False),
    ]
    assert (
        select(tmp_path, models, assignment={"role": "reviewer"})["model_id"]
        == "free:groq:test"
    )
    assert select(tmp_path, models)["model_id"] == "account:codex:test"


@pytest.mark.parametrize("endpoint", ["", "https://remote.example/v1"])
def test_unknown_or_public_local_endpoint_cannot_enter_local_dispatch(
    tmp_path, endpoint
):
    assert not select(
        tmp_path, [model("openai:test", "local", endpoint=endpoint)], planning=True
    )["allowed"]


def test_quota_is_read_from_existing_ledger_and_expires_at_reset(tmp_path):
    from datetime import datetime, timezone

    now = 1_784_800_000.0
    events = [
        {
            "event_type": routing.provider_usage.EVENT_MODEL_CALL,
            "provider_id": "groq",
            "created_at": datetime.fromtimestamp(now - 10, timezone.utc).isoformat(),
            "quota_snapshot": {
                "metric": "requests",
                "limit": 10,
                "remaining": 0,
                "resetsAt": "30s",
            },
        }
    ]
    catalog = {
        "models": [
            model("free:groq:test", "free"),
            model("account:codex:test", "account"),
        ]
    }
    with (
        mock.patch.object(
            routing.provider_usage, "_model_call_events", return_value=events
        ),
        mock.patch.object(
            routing.provider_usage,
            "probe_usage",
            side_effect=AssertionError("No provider probe"),
        ),
    ):
        first = routing.select_worker_route(
            tmp_path, {"allow_cloud": True}, {}, catalog=catalog, now=now
        )
        later = routing.select_worker_route(
            tmp_path, {"allow_cloud": True}, {}, catalog=catalog, now=now + 30
        )
    assert first["model_id"] == "account:codex:test"
    assert later["model_id"] == "free:groq:test"
    assert later["quota"]["official"]["stale"] is True


def test_worker_route_uses_canonical_assignment_not_packet_suggestion(tmp_path):
    from types import SimpleNamespace
    from vestahub import objective_worker
    from vestahub.state import state_dir

    worktree = tmp_path / "isolated"
    assignment = {
        "run_id": "assignment-run",
        "task_id": "assignment-task",
        "owner": "owner",
        "fence": 1,
        "status": "running",
        "lease_id": "lease",
        "worktree": str(worktree),
        "role": "implementer",
        "model": "account:codex:planner-suggested",
        "model_authorized": False,
    }
    objective = {
        "run_id": "parent-run",
        "assignments": [assignment],
        "status": "running",
        "model": "free:groq:authorized",
        "mode": "safe-auto",
        "allow_cloud": True,
    }
    packet = {
        "authority_root": str(tmp_path),
        "objective_id": "objective",
        "run_id": "assignment-run",
        "task_id": "assignment-task",
        "owner": "owner",
        "fence": 1,
        "worktree": str(worktree),
        "prompt": "Fix this function",
        "model_id": "account:codex:packet-forged",
        "allow_cloud": False,
        "capabilities": ["unknown"],
        "routing": {"allowed": True},
        "bypass_permissions": True,
    }
    lease = SimpleNamespace(
        run_id="assignment-run",
        task_id="assignment-task",
        path=str(worktree),
        owner="owner",
        state="active",
        lease_id="lease",
    )
    directory = state_dir(tmp_path) / "objectives" / "workers" / "assignment-run"
    with (
        mock.patch(
            "vestahub.agent_objectives.ObjectiveStore.snapshot", return_value=objective
        ),
        mock.patch(
            "vestahub.worktree_leases.WorktreeManager.list", return_value=[lease]
        ),
        mock.patch(
            "vesta.app_state.available_models",
            return_value={"models": [model("free:groq:authorized", "free")]},
        ),
        mock.patch.object(routing.provider_usage, "usage_overview", return_value=[]),
    ):
        sanitized = objective_worker.authorize_request(
            packet, directory / "request.json", directory / "response.json"
        )
    assert sanitized["model_id"] == "free:groq:authorized"
    assert sanitized["allow_cloud"] is True
    assert sanitized["bypass_permissions"] is False
    assert sanitized["routing"]["capabilities"] == ["chat", "repo_editing"]
    assert sanitized["routing"]["eligible_candidates"] == ["free:groq:authorized"]


def test_auto_reuses_canonical_provider_reliability_order(tmp_path):
    from vestahub import provider_reliability

    provider_reliability.record_provider_outcome(
        tmp_path, "gemini", False, reason="timeout", now=95
    )
    result = select(
        tmp_path, [model("free:gemini:test", "free"), model("free:groq:test", "free")]
    )
    assert result["eligible_candidates"] == ["free:groq:test", "free:gemini:test"]


def test_local_route_retains_exact_discovered_endpoint(tmp_path):
    result = select(
        tmp_path,
        [model("openai:test", "local", endpoint="http://127.0.0.1:1234/v1")],
        planning=True,
    )
    assert result["endpoint"] == "http://127.0.0.1:1234/v1"
    with mock.patch.dict("os.environ", {"LOCAL_MODEL_URL": "https://remote.example"}):
        runner = routing.managed_local_runner(result["model_id"], result["endpoint"])
    assert runner.base_url == "http://127.0.0.1:1234/v1"
    assert runner.model == "test"


@pytest.mark.parametrize(
    "model_id,endpoint",
    [
        ("auto", "http://127.0.0.1:1234/v1"),
        ("unknown:test", "http://127.0.0.1:1234/v1"),
        ("ollama:", "http://127.0.0.1:11434"),
        ("ollama:test", ""),
        ("openai:test", "https://remote.example/v1"),
    ],
)
def test_managed_local_dispatch_cannot_fallback_or_use_public_endpoint(
    model_id, endpoint
):
    with pytest.raises(ValueError, match="concrete local model"):
        routing.managed_local_runner(model_id, endpoint)


@pytest.mark.parametrize("model_id", ["auto", "ollama:", "unknown:test"])
def test_local_catalog_cannot_admit_nonconcrete_dispatch_id(tmp_path, model_id):
    result = select(
        tmp_path,
        [
            model(
                model_id, "local", provider="ollama", endpoint="http://127.0.0.1:11434"
            )
        ],
        planning=True,
    )
    assert not result["allowed"]


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:1234/v1", None])
def test_real_managed_pipeline_binds_local_route_without_rediscovery(
    tmp_path, endpoint
):
    from _helpers import make_repo
    from vestahub.agent_objectives import ObjectiveStore
    from vestahub.gui_pipeline import handle_gui_message

    root = make_repo(tmp_path)
    objective = ObjectiveStore(root).create(
        "Explain this project", [], mode="plan", model="openai:test"
    )
    with (
        mock.patch("vestahub.local_runner.runner_for_model") as rediscover,
        mock.patch(
            "vestahub.ask.run_ask",
            return_value={"status": "answered_locally", "answer": "Project summary"},
        ) as ask,
    ):
        kwargs = dict(
            model_id="openai:test",
            mode="plan",
            authority_root=root,
            task_id=objective["task_id"],
            run_id=objective["run_id"] + "-plan",
            local_model_endpoint=endpoint,
        )
        if endpoint is None:
            with pytest.raises(ValueError, match="concrete local model"):
                handle_gui_message(root, "Explain this project", **kwargs)
            ask.assert_not_called()
        else:
            handle_gui_message(root, "Explain this project", **kwargs)
            assert ask.call_args.kwargs["runner"].base_url == endpoint
            assert ask.call_args.kwargs["selected_model_id"] == "openai:test"
        rediscover.assert_not_called()
