from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from vesta.update.identity import detect_install_type
from vesta.update.manifest import ManifestError, verify_manifest
from vesta.update.models import (
    InstallType,
    InstalledBuild,
    UpdateCandidate,
    UpdateOwner,
    UpdatePolicy,
    UpdateState,
    can_transition,
)
from vesta.update.storage import UpdateStore, UpdaterPaths


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("idle", "checking"),
        ("checking", "up_to_date"),
        ("checking", "available"),
        ("checking", "unavailable"),
        ("checking", "unsupported_install"),
        ("checking", "policy_blocked"),
        ("available", "downloading"),
        ("available", "deferred"),
        ("available", "skipped"),
        ("downloading", "verifying"),
        ("downloading", "paused"),
        ("downloading", "failed_retriable"),
        ("downloading", "cancelled"),
        ("verifying", "ready_to_install"),
        ("verifying", "failed_terminal"),
        ("ready_to_install", "waiting_for_idle"),
        ("ready_to_install", "install_on_quit"),
        ("ready_to_install", "installing"),
        ("waiting_for_idle", "installing"),
        ("install_on_quit", "installing"),
        ("installing", "restarting"),
        ("installing", "failed_retriable"),
        ("installing", "failed_terminal"),
        ("restarting", "health_checking"),
        ("health_checking", "completed"),
        # A source checkout has no install pipeline: its check *is* the
        # fast-forward, so discovery and completion are one step.
        ("checking", "completed"),
        ("health_checking", "rollback_pending"),
        ("health_checking", "needs_attention"),
        ("rollback_pending", "rolling_back"),
        ("rolling_back", "rolled_back"),
        ("rolling_back", "needs_attention"),
        ("failed_retriable", "checking"),
        ("deferred", "available"),
        ("cancelled", "available"),
    ],
    ids=lambda value: value,
)
def test_update_state_machine_accepts_required_transition(current: str, target: str):
    assert can_transition(UpdateState(current), UpdateState(target))


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("idle", "up_to_date"),
        ("checking", "ready_to_install"),
        ("available", "installing"),
        ("downloading", "ready_to_install"),
        ("verifying", "installing"),
        ("ready_to_install", "completed"),
        ("installing", "completed"),
        ("restarting", "completed"),
        ("completed", "installing"),
        ("rolled_back", "completed"),
        ("failed_terminal", "downloading"),
        ("policy_blocked", "installing"),
    ],
    ids=lambda value: value,
)
def test_update_state_machine_rejects_unsafe_transition(current: str, target: str):
    assert not can_transition(UpdateState(current), UpdateState(target))


def test_update_policy_defaults_discovery_on_and_install_consent_off():
    policy = UpdatePolicy()

    assert policy.check_for_updates is True
    assert policy.automatic_downloads is False
    assert policy.automatic_install_on_quit is False
    assert policy.owner is UpdateOwner.VESTA
    assert policy.channel == "stable"


def test_managed_owner_blocks_vesta_installation_without_disabling_status():
    policy = UpdatePolicy(owner=UpdateOwner.MDM, automatic_downloads=True)

    assert policy.discovery_allowed is True
    assert policy.installation_allowed is False
    assert policy.automatic_downloads is False


@pytest.mark.parametrize(
    ("platform_name", "package_identity", "executable", "git_checkout", "expected"),
    [
        (
            "windows",
            True,
            "C:/Program Files/WindowsApps/Vesta/Vesta.exe",
            False,
            "windows_msix",
        ),
        (
            "darwin",
            False,
            "/Applications/Vesta.app/Contents/MacOS/Vesta",
            False,
            "macos_sparkle",
        ),
        ("windows", False, "C:/Tools/Vesta/Vesta.exe", False, "portable"),
        ("darwin", False, "/tmp/Vesta", False, "portable"),
        ("linux", False, "/src/.venv/bin/python", True, "source_checkout"),
        ("linux", False, "/opt/vesta/vesta", False, "unknown"),
    ],
)
def test_install_type_detection_is_explicit(
    platform_name: str,
    package_identity: bool,
    executable: str,
    git_checkout: bool,
    expected: str,
):
    assert (
        detect_install_type(
            platform_name=platform_name,
            package_identity=package_identity,
            executable=Path(executable),
            git_checkout=git_checkout,
        ).value
        == expected
    )


def _installed(**overrides: object) -> InstalledBuild:
    values: dict[str, object] = {
        "version": "0.2.1a1",
        "build_id": "a" * 40,
        "channel": "stable",
        "platform": "windows",
        "architecture": "x86_64",
        "install_type": InstallType.WINDOWS_MSIX,
        "package_identity": "Vesta.Desktop",
        "publisher_identity": "CN=Vesta",
    }
    values.update(overrides)
    return InstalledBuild(**values)


def _candidate(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "version": "0.3.0",
        "build_id": "b" * 40,
        "channel": "stable",
        "release_id": "v0.3.0",
        "published_at": "2026-08-14T10:00:00Z",
        "platform": "windows",
        "architecture": "x86_64",
        "install_type": "windows_msix",
        "artifact_url": "https://updates.example.test/releases/v0.3.0/Vesta-0.3.0.msix",
        "artifact_sha256": "c" * 64,
        "artifact_size": 123456,
        "publisher_identity": "CN=Vesta",
        "metadata_key_ids": ["root-1"],
        "release_title": "Vesta 0.3.0",
        "release_notes": "Security and reliability improvements.",
        "release_notes_url": "https://updates.example.test/releases/v0.3.0/notes",
        "minimum_current_version": "0.2.0",
        "maximum_current_version": "0.2.9",
        "minimum_os_version": "10.0.17763",
        "criticality": "normal",
        "rollout_percentage": 100,
        "cohort_start": 0,
        "minimum_updater_protocol": 1,
        "rollback_compatible": True,
        "native": {
            "appinstaller_url": "https://updates.example.test/stable/Vesta.appinstaller",
            "package_name": "Vesta.Desktop",
        },
    }
    values.update(overrides)
    return values


def _signed_manifest(
    *,
    candidate: dict[str, object] | None = None,
    metadata_version: int = 7,
    expires_at: datetime | None = None,
    channel: str = "stable",
    key_id: str = "root-1",
    private_key: Ed25519PrivateKey | None = None,
) -> tuple[bytes, dict[str, object]]:
    key = private_key or Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    signed = {
        "schema_version": 1,
        "metadata_version": metadata_version,
        "generated_at": "2026-08-14T10:00:00Z",
        "expires_at": (expires_at or NOW + timedelta(days=7)).isoformat(),
        "channel": channel,
        "releases": [candidate or _candidate()],
    }
    canonical = json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
    envelope = {
        "signed": signed,
        "signatures": [
            {
                "key_id": key_id,
                "signature": base64.b64encode(key.sign(canonical)).decode(),
            }
        ],
    }
    trust = {
        "schema_version": 1,
        "threshold": 1,
        "keys": [
            {
                "key_id": key_id,
                "public_key": base64.b64encode(public).decode(),
                "revoked": False,
            }
        ],
    }
    return json.dumps(envelope).encode(), trust


def test_valid_signed_manifest_selects_matching_newer_candidate():
    payload, trust = _signed_manifest()

    result = verify_manifest(
        payload,
        trust=trust,
        installed=_installed(),
        cohort=42,
        prior_metadata_version=6,
        now=NOW,
    )

    assert result.metadata_version == 7
    assert result.candidate == UpdateCandidate.from_dict(_candidate())
    assert result.signing_key_ids == ("root-1",)


def test_signed_manifest_reports_exact_installed_release_as_current():
    candidate = _candidate(version="0.2.1a1", build_id="a" * 40)
    payload, trust = _signed_manifest(candidate=candidate)

    result = verify_manifest(
        payload,
        trust=trust,
        installed=_installed(),
        cohort=42,
        prior_metadata_version=6,
        now=NOW,
    )

    assert result.candidate is None
    assert result.metadata_version == 7


def test_authenticated_empty_release_set_is_current_not_unavailable():
    payload, _ = _signed_manifest()
    envelope = json.loads(payload)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    envelope["signed"]["releases"] = []
    canonical = json.dumps(
        envelope["signed"], sort_keys=True, separators=(",", ":")
    ).encode()
    envelope["signatures"] = [
        {
            "key_id": "empty-root",
            "signature": base64.b64encode(key.sign(canonical)).decode(),
        }
    ]
    trust = {
        "schema_version": 1,
        "threshold": 1,
        "keys": [
            {
                "key_id": "empty-root",
                "public_key": base64.b64encode(public).decode(),
                "revoked": False,
            }
        ],
    }

    result = verify_manifest(
        json.dumps(envelope).encode(),
        trust=trust,
        installed=_installed(),
        cohort=42,
        prior_metadata_version=0,
        now=NOW,
    )

    assert result.candidate is None


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("bad_signature", "metadata_signature_invalid"),
        ("unknown_key", "metadata_key_unknown"),
        ("revoked_key", "metadata_key_revoked"),
        ("expired", "metadata_expired"),
        ("metadata_rollback", "metadata_rollback"),
        ("wrong_channel", "channel_mismatch"),
        ("wrong_platform", "platform_mismatch"),
        ("wrong_architecture", "architecture_mismatch"),
        ("wrong_install_type", "install_type_mismatch"),
        ("publisher_mismatch", "publisher_mismatch"),
        ("version_downgrade", "target_rollback"),
        ("insecure_artifact_url", "insecure_artifact_url"),
        ("cohort_excluded", "rollout_excluded"),
        ("incompatible_current", "current_version_unsupported"),
    ],
)
def test_signed_manifest_rejects_untrusted_or_mixed_candidate(mutation: str, code: str):
    candidate = _candidate()
    metadata_version = 7
    expires = NOW + timedelta(days=7)
    channel = "stable"
    key_id = "root-1"
    installed = _installed()
    cohort = 42
    prior = 6
    if mutation == "wrong_channel":
        candidate["channel"] = "beta"
    elif mutation == "wrong_platform":
        candidate["platform"] = "darwin"
    elif mutation == "wrong_architecture":
        candidate["architecture"] = "arm64"
    elif mutation == "wrong_install_type":
        candidate["install_type"] = "portable"
    elif mutation == "publisher_mismatch":
        candidate["publisher_identity"] = "CN=Attacker"
    elif mutation == "version_downgrade":
        candidate["version"] = "0.1.0"
    elif mutation == "insecure_artifact_url":
        candidate["artifact_url"] = "http://updates.example.test/vesta.msix"
    elif mutation == "cohort_excluded":
        candidate["rollout_percentage"] = 10
    elif mutation == "incompatible_current":
        candidate["minimum_current_version"] = "0.2.2"
    elif mutation == "expired":
        expires = NOW - timedelta(seconds=1)
    elif mutation == "metadata_rollback":
        prior = 8
    elif mutation == "wrong_channel":
        channel = "beta"
    if mutation == "unknown_key":
        key_id = "other"
    payload, trust = _signed_manifest(
        candidate=candidate,
        metadata_version=metadata_version,
        expires_at=expires,
        channel=channel,
        key_id=key_id,
    )
    if mutation == "bad_signature":
        envelope = json.loads(payload)
        envelope["signatures"][0]["signature"] = base64.b64encode(b"x" * 64).decode()
        payload = json.dumps(envelope).encode()
    elif mutation == "unknown_key":
        trust["keys"][0]["key_id"] = "root-1"
    elif mutation == "revoked_key":
        trust["keys"][0]["revoked"] = True

    with pytest.raises(ManifestError, match=code):
        verify_manifest(
            payload,
            trust=trust,
            installed=installed,
            cohort=cohort,
            prior_metadata_version=prior,
            now=NOW,
        )


def test_app_wide_policy_migrates_legacy_workspace_consent_once(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    legacy = workspace / ".vestahub" / "gui" / "preferences.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"schema_version": 3, "auto_update": True}))
    store = UpdateStore(UpdaterPaths.for_home(home))

    first = store.load_policy(legacy_workspaces=[workspace])
    legacy.write_text(json.dumps({"schema_version": 3, "auto_update": False}))
    second = store.load_policy(legacy_workspaces=[workspace])

    assert first.automatic_downloads is True
    assert first.automatic_install_on_quit is True
    assert second == first
    assert store.paths.policy.is_relative_to(home)


def test_operation_state_round_trip_is_atomic_and_app_wide(tmp_path: Path):
    store = UpdateStore(UpdaterPaths.for_home(tmp_path))
    operation = store.load_operation().transition(UpdateState.CHECKING)

    store.save_operation(operation)

    loaded = store.load_operation()
    assert loaded.state is UpdateState.CHECKING
    assert loaded.operation_id == operation.operation_id
    assert loaded.schema_version == 1


def test_machine_policy_overrides_user_consent_without_becoming_user_state(
    tmp_path: Path,
):
    store = UpdateStore(
        UpdaterPaths.for_home(tmp_path),
        managed_policy={
            "disable_auto_updates": True,
            "channel": "beta",
            "owner": "mdm",
            "maximum_deferral_hours": 24,
            "management_source": "Intune",
        },
    )
    store.save_policy(UpdatePolicy(automatic_downloads=True))

    effective = store.load_policy()
    persisted = json.loads(store.paths.policy.read_text(encoding="utf-8"))

    assert effective.owner is UpdateOwner.MDM
    assert effective.channel == "beta"
    assert effective.automatic_downloads is False
    assert effective.maximum_deferral_hours == 24
    assert effective.management_source == "Intune"
    assert "owner" in effective.managed_fields
    assert persisted["owner"] == "vesta"
    assert persisted["automatic_downloads"] is True


def test_managed_fields_cannot_be_changed_through_user_policy_api(tmp_path: Path):
    store = UpdateStore(
        UpdaterPaths.for_home(tmp_path),
        managed_policy={"disable_update_checks": True, "channel": "stable"},
    )

    result = store.update_policy(check_for_updates=True, channel="alpha")

    assert result.check_for_updates is False
    assert result.channel == "stable"
