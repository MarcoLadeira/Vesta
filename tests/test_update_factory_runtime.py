from __future__ import annotations

import json
from pathlib import Path

from opai.update.factory import (
    create_update_service,
    load_installed_build,
    load_managed_update_configuration,
    load_trust_store,
)
from opai.update.models import InstallType
from opai.update.native import WindowsMsixAdapter
from opai.update.runtime import probe_active_work
from opai.release_identity import packaged_metadata_paths
from opaihub.session_registry import SessionRegistry


def test_packaged_identity_comes_from_release_evidence_not_handwritten_version(
    tmp_path: Path,
):
    identity = tmp_path / "release-identity.json"
    identity.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.3.0",
                "build_id": "b" * 40,
                "channel": "beta",
                "platform": "windows",
                "architecture": "x86_64",
                "install_type": "windows_msix",
                "package_identity": "OPai.Desktop",
                "publisher_identity": "CN=Vesta",
            }
        ),
        encoding="utf-8",
    )

    installed = load_installed_build(identity_paths=[identity])

    assert installed.version == "0.3.0"
    assert installed.build_id == "b" * 40
    assert installed.install_type.value == "windows_msix"


def test_trust_store_is_loaded_only_from_packaged_or_admin_paths(tmp_path: Path):
    trust_path = tmp_path / "update-trust.json"
    trust_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "threshold": 1,
                "feed_url": "https://updates.example.test/stable/manifest.json",
                "keys": [{"key_id": "root-1", "public_key": "eA==", "revoked": False}],
            }
        ),
        encoding="utf-8",
    )

    trust = load_trust_store(paths=[trust_path])

    assert trust["feed_url"].startswith("https://")
    assert trust["keys"][0]["key_id"] == "root-1"


def test_update_trust_never_falls_back_to_an_ancestor_file(tmp_path: Path):
    bundle = tmp_path / "parent" / "bundle"
    executable = bundle / "cli" / "opai.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"native")
    ancestor = tmp_path / "parent" / "update-trust.json"
    ancestor.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "threshold": 1,
                "feed_url": "https://attacker.example.test/manifest.json",
                "keys": [{"key_id": "wrong", "public_key": "eA==", "revoked": False}],
            }
        ),
        encoding="utf-8",
    )

    paths = packaged_metadata_paths("update-trust.json", executable_path=executable)

    assert paths == (bundle / "update-trust.json",)
    assert ancestor not in paths
    assert load_trust_store(paths=paths) == {}


def test_runtime_probe_uses_canonical_thread_lease_and_background_run_truth(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    thread = workspace / ".opaihub" / "gui" / "thread.json"
    thread.parent.mkdir(parents=True)
    thread.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state": "running",
                "lease": {"pid": 1, "boot": "other", "heartbeat_at": 100.0},
            }
        ),
        encoding="utf-8",
    )

    active = probe_active_work([workspace], now=110.0)
    stale = probe_active_work([workspace], now=1000.0)

    assert active.safe_to_install is False
    assert "active_run" in active.reasons
    assert str(workspace.resolve()) in active.active_workspaces
    assert stale.safe_to_install is True


def test_runtime_probe_observes_canonical_sessions_from_another_process_scope(
    tmp_path: Path,
):
    durable = tmp_path / "active-sessions"
    registry = SessionRegistry(durable_root=durable, process_id=4242)
    registry.start("remote-run", "pipeline")

    active = probe_active_work(
        [], active_session_root=durable, is_pid_alive=lambda pid: pid == 4242
    )
    registry.finish("remote-run")
    idle = probe_active_work(
        [], active_session_root=durable, is_pid_alive=lambda pid: pid == 4242
    )

    assert active.safe_to_install is False
    assert "owned_process" in active.reasons
    assert idle.safe_to_install is True


def test_composition_root_selects_native_adapter_and_migrates_app_wide_policy(
    tmp_path: Path,
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    preferences = workspace / ".opaihub" / "gui" / "preferences.json"
    preferences.parent.mkdir(parents=True)
    preferences.write_text(json.dumps({"auto_update": True}), encoding="utf-8")
    installed = load_installed_build(identity_paths=[tmp_path / "missing.json"])
    installed = installed.__class__(
        **{
            **installed.to_dict(),
            "install_type": InstallType.WINDOWS_MSIX,
            "platform": "windows",
            "package_identity": "OPai.Desktop",
            "publisher_identity": "CN=Vesta",
        }
    )

    service = create_update_service(
        workspaces=[workspace], home=home, installed=installed, trust={}
    )

    assert isinstance(service.adapter, WindowsMsixAdapter)
    assert service.policy().automatic_downloads is True
    assert service.store.paths.policy.is_relative_to(home)


def test_admin_policy_accepts_documented_aliases_and_https_feed_override(
    tmp_path: Path,
):
    policy_path = tmp_path / "update-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "disableAutoUpdates": True,
                "allowedReleaseChannel": "beta",
                "updateOwner": "mdm",
                "maximumDeferralHours": 12,
                "mandatoryInstallAfter": "2026-08-20T12:00:00Z",
                "feedOverride": "https://managed.example.test/beta/manifest.json",
                "management_source": "Jamf",
            }
        ),
        encoding="utf-8",
    )

    policy = load_managed_update_configuration(paths=[policy_path])

    assert policy["disable_auto_updates"] is True
    assert policy["channel"] == "beta"
    assert policy["owner"] == "mdm"
    assert policy["maximum_deferral_hours"] == 12
    assert policy["feed_url"].startswith("https://")
    assert policy["management_source"] == "Jamf"


def test_admin_policy_rejects_untrusted_link_and_insecure_feed(tmp_path: Path):
    real = tmp_path / "real.json"
    real.write_text(
        json.dumps({"schema_version": 1, "feedOverride": "http://unsafe.test/feed"}),
        encoding="utf-8",
    )
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(real)
    except OSError:
        link = real

    policy = load_managed_update_configuration(paths=[link])

    if link != real:
        assert policy == {}
    else:
        assert "feed_url" not in policy
