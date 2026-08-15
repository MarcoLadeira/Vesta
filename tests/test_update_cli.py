from __future__ import annotations

import json

from opai.cli import build_parser, cmd_update
from opai.update.models import UpdateState


def test_update_parser_exposes_canonical_lifecycle_commands():
    parser = build_parser()

    for command in (
        "status",
        "check",
        "download",
        "install",
        "rollback",
        "doctor",
        "developer-git",
    ):
        args = parser.parse_args(["update", command])
        assert args.update_command == command
        assert args.func is cmd_update


def test_update_install_modes_are_mutually_exclusive():
    parser = build_parser()
    args = parser.parse_args(["update", "install", "--when-idle"])
    assert args.when_idle is True
    assert args.on_quit is False


def test_developer_git_update_is_explicit_and_source_only(
    monkeypatch, capsys, tmp_path
):
    from opai import cli
    from opai.update.adapters import DeveloperGitUpdateAdapter
    from opai.update.models import InstallType, InstalledBuild

    adapter = DeveloperGitUpdateAdapter(tmp_path)
    monkeypatch.setattr(
        adapter,
        "check_source",
        lambda **_kwargs: {"status": "update_available", "branch": "main"},
    )
    service = type(
        "Service",
        (),
        {
            "adapter": adapter,
            "installed": InstalledBuild(
                version="0.2.1a1",
                build_id="a" * 40,
                channel="stable",
                platform="windows",
                architecture="x86_64",
                install_type=InstallType.SOURCE_CHECKOUT,
            ),
        },
    )()
    monkeypatch.setattr(
        "opai.update.factory.create_update_service", lambda **_kwargs: service
    )

    assert cli.main(["update", "developer-git", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["developer_source_update"] is True
    assert payload["status"] == "update_available"


def test_main_doctor_payload_contains_same_canonical_updater_state(monkeypatch, capsys):
    from opai import cli

    update = {
        "schema_version": 1,
        "operation": {"state": UpdateState.AVAILABLE.value, "operation_id": "op-1"},
    }

    class Service:
        def doctor(self):
            return update

    monkeypatch.setattr(
        "opai.update.factory.create_update_service", lambda **_kwargs: Service()
    )
    monkeypatch.setattr(
        cli,
        "project_status",
        lambda _root: {
            "client_integrations": {"summary": {"broken": 0, "missing": 0}},
            "stale_paths": {"ok": True},
            "superpowers": {},
        },
    )
    monkeypatch.setattr("opaihub.validator.validate_all", lambda _root: {"ok": True})
    monkeypatch.setattr("opaihub.loader.registry_items", lambda *_args: [])
    monkeypatch.setattr("opaihub.local_models.discover_local_models", lambda _root: [])
    monkeypatch.setattr("opai.model_registry.catalog", lambda: {})

    assert cli.main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["updater"] == update
