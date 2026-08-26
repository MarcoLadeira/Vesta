from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from opai.update.adapters import (
    AdapterInstallResult,
    AdapterVerification,
    DeveloperGitUpdateAdapter,
)
from opai.update.errors import UpdateError
from opai.update.models import (
    InstallType,
    InstalledBuild,
    UpdateCandidate,
    UpdateOwner,
    UpdatePolicy,
    UpdateState,
)
from opai.update.runtime import ActiveWorkStatus
from opai.update.service import UpdateService
from opai.update.service import _jittered_check_interval
from opai.update.storage import UpdateStore, UpdaterPaths


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
ARTIFACT = b"signed-msix-bytes"


def _installed(**overrides: object) -> InstalledBuild:
    values: dict[str, object] = {
        "version": "0.2.1a1",
        "build_id": "a" * 40,
        "channel": "stable",
        "platform": "windows",
        "architecture": "x86_64",
        "install_type": InstallType.WINDOWS_MSIX,
        "package_identity": "OPai.Desktop",
        "publisher_identity": "CN=OPai",
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
        "artifact_url": "https://updates.example.test/releases/v0.3.0/OPai.msix",
        "artifact_sha256": hashlib.sha256(ARTIFACT).hexdigest(),
        "artifact_size": len(ARTIFACT),
        "publisher_identity": "CN=OPai",
        "metadata_key_ids": ["root-1"],
        "release_title": "OPai 0.3.0",
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
            "appinstaller_url": "https://updates.example.test/stable/OPai.appinstaller",
            "recovery": {
                "version": "0.2.1a1",
                "build_id": "a" * 40,
                "channel": "stable",
                "release_id": "v0.2.1a1",
                "published_at": "2026-08-01T10:00:00Z",
                "platform": "windows",
                "architecture": "x86_64",
                "install_type": "windows_msix",
                "artifact_url": "https://updates.example.test/releases/v0.2.1/OPai.msix",
                "artifact_sha256": hashlib.sha256(ARTIFACT).hexdigest(),
                "artifact_size": len(ARTIFACT),
                "publisher_identity": "CN=OPai",
                "metadata_key_ids": ["root-1"],
                "rollback_compatible": False,
                "native": {
                    "appinstaller_url": "https://updates.example.test/v0.2.1/OPai.appinstaller"
                },
            },
        },
    }
    values.update(overrides)
    return values


def _manifest(
    candidate: dict[str, object] | None = None,
    *,
    metadata_version: int = 7,
    signing_key: Ed25519PrivateKey | None = None,
) -> tuple[bytes, dict[str, object]]:
    key = signing_key or Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    signed = {
        "schema_version": 1,
        "metadata_version": metadata_version,
        "generated_at": "2026-08-14T10:00:00Z",
        "expires_at": (NOW + timedelta(days=7)).isoformat(),
        "channel": "stable",
        "releases": [candidate or _candidate()],
    }
    body = json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
    envelope = {
        "signed": signed,
        "signatures": [
            {"key_id": "root-1", "signature": base64.b64encode(key.sign(body)).decode()}
        ],
    }
    trust = {
        "schema_version": 1,
        "threshold": 1,
        "feed_url": "https://updates.example.test/stable/manifest.json",
        "keys": [
            {
                "key_id": "root-1",
                "public_key": base64.b64encode(public).decode(),
                "revoked": False,
            }
        ],
    }
    return json.dumps(envelope).encode(), trust


class Fetcher:
    def __init__(self, payload: bytes, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.payload


class Downloader:
    def __init__(self, body: bytes = ARTIFACT) -> None:
        self.body = body
        self.calls = 0

    def download(
        self,
        candidate: UpdateCandidate,
        root: Path,
        operation_id: str,
        progress: Any,
    ) -> Path:
        self.calls += 1
        root.mkdir(parents=True, exist_ok=True)
        target = root / operation_id / "OPai.msix"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.body)
        progress(len(self.body), len(self.body))
        return target

    def stage(
        self,
        source: Path,
        candidate: UpdateCandidate,
        root: Path,
        operation_id: str,
    ) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        target = root / operation_id / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        return target


class Adapter:
    name = "fake-msix"

    def __init__(
        self,
        *,
        publisher: str = "CN=OPai",
        install_ok: bool = True,
        rollback_ok: bool = True,
    ):
        self.publisher = publisher
        self.install_ok = install_ok
        self.rollback_ok = rollback_ok
        self.installs: list[tuple[Path, str]] = []
        self.rollbacks = 0

    def doctor(self, installed: InstalledBuild) -> dict[str, object]:
        return {"available": True, "name": self.name, "publisher": self.publisher}

    def verify(self, artifact: Path, candidate: UpdateCandidate) -> AdapterVerification:
        return AdapterVerification(
            verified=True, publisher_identity=self.publisher, evidence="native"
        )

    def install(
        self, artifact: Path, candidate: UpdateCandidate, *, mode: str
    ) -> AdapterInstallResult:
        self.installs.append((artifact, mode))
        return AdapterInstallResult(
            started=self.install_ok,
            transaction_id="tx-1" if self.install_ok else "",
            error_category="installer_failed" if not self.install_ok else "",
        )

    def rollback(self, last_known_good: dict[str, object]) -> AdapterInstallResult:
        self.rollbacks += 1
        return AdapterInstallResult(
            started=self.rollback_ok,
            transaction_id="rollback-1" if self.rollback_ok else "",
            error_category="rollback_failed" if not self.rollback_ok else "",
        )


def _service(
    tmp_path: Path,
    *,
    policy: UpdatePolicy | None = None,
    installed: InstalledBuild | None = None,
    fetch_error: Exception | None = None,
    body: bytes = ARTIFACT,
    adapter: Adapter | None = None,
    active: bool = False,
) -> tuple[UpdateService, Fetcher, Downloader, Adapter]:
    payload, trust = _manifest(
        _candidate(
            platform=(installed or _installed()).platform,
            architecture=(installed or _installed()).architecture,
            install_type=(installed or _installed()).install_type.value,
            publisher_identity=(installed or _installed()).publisher_identity,
        )
    )
    store = UpdateStore(UpdaterPaths.for_home(tmp_path))
    store.save_policy(policy or UpdatePolicy(rollout_cohort=42))
    fetcher = Fetcher(payload, error=fetch_error)
    downloader = Downloader(body)
    native = adapter or Adapter()
    service = UpdateService(
        store=store,
        installed=installed or _installed(),
        trust=trust,
        manifest_fetcher=fetcher,
        downloader=downloader,
        adapter=native,
        runtime_probe=lambda: ActiveWorkStatus(
            safe_to_install=not active,
            reasons=("active_run",) if active else (),
            active_workspaces=("fixture",) if active else (),
        ),
        now=lambda: NOW,
    )
    return service, fetcher, downloader, native


def test_discovery_runs_when_automatic_install_is_off(tmp_path: Path):
    service, fetcher, _, _ = _service(tmp_path)

    operation = service.check(force=False)

    assert operation.state is UpdateState.AVAILABLE
    assert len(fetcher.calls) == 1
    assert service.policy().automatic_downloads is False


def test_exact_installed_release_is_up_to_date_not_unavailable(tmp_path: Path):
    installed = _installed()
    payload, trust = _manifest(
        _candidate(version=installed.version, build_id=installed.build_id)
    )
    store = UpdateStore(UpdaterPaths.for_home(tmp_path))
    store.save_policy(UpdatePolicy(rollout_cohort=42))
    service = UpdateService(
        store=store,
        installed=installed,
        trust=trust,
        manifest_fetcher=Fetcher(payload),
        downloader=Downloader(),
        adapter=Adapter(),
        runtime_probe=lambda: ActiveWorkStatus(True),
        now=lambda: NOW,
    )

    operation = service.check(force=True)

    assert operation.state is UpdateState.UP_TO_DATE
    assert operation.error_category == ""


def test_manual_check_bypasses_freshness_cache(tmp_path: Path):
    service, fetcher, _, _ = _service(tmp_path)
    service.check(force=False)

    cached = service.check(force=False)
    fresh = service.check(force=True)

    assert cached.state is UpdateState.AVAILABLE
    assert fresh.state is UpdateState.AVAILABLE
    assert len(fetcher.calls) == 2


def test_new_discovery_cannot_reuse_a_previous_candidates_staged_artifact(
    tmp_path: Path,
):
    service, _, _, _ = _service(tmp_path)
    stale = service.store.load_operation()
    service.store.save_operation(
        replace(
            stale,
            state=UpdateState.COMPLETED,
            candidate=_candidate(version="0.2.9", build_id="c" * 40),
            staged_artifact=str(service.store.paths.staging / "old.msix"),
            staged_sha256="c" * 64,
            downloaded_bytes=10,
            total_bytes=10,
        )
    )

    discovered = service.check(force=True)

    assert discovered.state is UpdateState.AVAILABLE
    assert discovered.candidate["version"] == "0.3.0"
    assert discovered.staged_artifact == ""
    assert discovered.staged_sha256 == ""
    assert discovered.downloaded_bytes == 0


def test_signed_metadata_floor_survives_operation_state_loss(tmp_path: Path):
    key = Ed25519PrivateKey.generate()
    payload8, trust = _manifest(metadata_version=8, signing_key=key)
    store = UpdateStore(UpdaterPaths.for_home(tmp_path))
    store.save_policy(UpdatePolicy(rollout_cohort=42))

    def service(payload: bytes) -> UpdateService:
        return UpdateService(
            store=store,
            installed=_installed(),
            trust=trust,
            manifest_fetcher=Fetcher(payload),
            downloader=Downloader(),
            adapter=Adapter(),
            runtime_probe=lambda: ActiveWorkStatus(True),
            now=lambda: NOW,
        )

    assert service(payload8).check(force=True).state is UpdateState.AVAILABLE
    store.paths.operation.unlink()
    payload7, _ = _manifest(metadata_version=7, signing_key=key)

    rejected = service(payload7).check(force=True)

    assert rejected.state is UpdateState.UNAVAILABLE
    assert rejected.error_category == "metadata_rollback"
    assert store.load_metadata_floor("stable") == 8


def test_disabled_discovery_is_policy_blocked_not_current(tmp_path: Path):
    service, fetcher, _, _ = _service(
        tmp_path, policy=UpdatePolicy(check_for_updates=False, rollout_cohort=4)
    )

    operation = service.check()

    assert operation.state is UpdateState.POLICY_BLOCKED
    assert fetcher.calls == []


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (OSError("token=super-secret"), "offline"),
        (TimeoutError("https://signed.example/?sig=secret"), "offline"),
        (ValueError("broken remote payload"), "feed_unavailable"),
    ],
)
def test_failed_check_is_unavailable_and_redacted(
    tmp_path: Path, error: Exception, category: str
):
    service, _, _, _ = _service(tmp_path, fetch_error=error)

    operation = service.check(force=True)

    assert operation.state is UpdateState.UNAVAILABLE
    assert operation.error_category == category
    assert "secret" not in operation.safe_diagnostic
    assert "http" not in operation.safe_diagnostic


def test_managed_install_owner_discovers_but_blocks_in_app_install(tmp_path: Path):
    service, _, _, _ = _service(
        tmp_path,
        policy=UpdatePolicy(owner=UpdateOwner.MDM, rollout_cohort=9),
    )

    operation = service.check(force=True)

    assert operation.state is UpdateState.POLICY_BLOCKED
    assert operation.candidate["version"] == "0.3.0"


@pytest.mark.parametrize(
    "install_type",
    [InstallType.PORTABLE, InstallType.SOURCE_CHECKOUT, InstallType.UNKNOWN],
)
def test_unsupported_install_discovers_without_fake_install_button(
    tmp_path: Path, install_type: InstallType
):
    payload, trust = _manifest(_candidate())
    store = UpdateStore(UpdaterPaths.for_home(tmp_path))
    store.save_policy(UpdatePolicy(rollout_cohort=42))
    service = UpdateService(
        store=store,
        installed=_installed(
            install_type=install_type,
            platform="linux"
            if install_type is InstallType.SOURCE_CHECKOUT
            else "windows",
            publisher_identity="",
        ),
        trust=trust,
        manifest_fetcher=Fetcher(payload),
        downloader=Downloader(),
        adapter=Adapter(),
        runtime_probe=lambda: ActiveWorkStatus(True),
        now=lambda: NOW,
    )

    operation = service.check(force=True)

    assert operation.state is UpdateState.UNSUPPORTED_INSTALL
    assert operation.error_category == "manual_update_required"


@pytest.mark.parametrize(
    "install_type",
    [InstallType.PORTABLE, InstallType.SOURCE_CHECKOUT, InstallType.UNKNOWN],
)
def test_feedless_unsupported_install_reports_manual_update_not_feed_error(
    tmp_path: Path, install_type: InstallType
):
    """Non-packaged installs ship no signed feed (the trust store only exists
    beside a packaged executable), so a check must not fabricate a "feed could
    not be read" network failure — the deterministic truth is manual update."""
    store = UpdateStore(UpdaterPaths.for_home(tmp_path))
    store.save_policy(UpdatePolicy(rollout_cohort=42))
    fetcher = Fetcher(b"{}")
    service = UpdateService(
        store=store,
        installed=_installed(install_type=install_type, publisher_identity=""),
        trust={},
        manifest_fetcher=fetcher,
        downloader=Downloader(),
        adapter=Adapter(),
        runtime_probe=lambda: ActiveWorkStatus(True),
        now=lambda: NOW,
    )

    operation = service.check(force=True)

    assert operation.state is UpdateState.UNSUPPORTED_INSTALL
    assert operation.error_category == "manual_update_required"
    assert "feed" not in operation.safe_diagnostic.casefold()
    assert fetcher.calls == []
    assert operation.last_successful_check_at
    assert operation.retry_count == 0
    assert operation.next_retry_at == ""


def _developer_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_result: dict[str, object],
) -> tuple[UpdateService, Fetcher, list[bool]]:
    """A source-checkout service whose git check is stubbed at the boundary."""
    import opai.updater as legacy_updater

    forces: list[bool] = []
    monkeypatch.setattr(
        legacy_updater,
        "check_for_update",
        lambda root, branch="main", force=False: (
            forces.append(bool(force)) or dict(git_result)
        ),
    )
    store = UpdateStore(UpdaterPaths.for_home(tmp_path))
    store.save_policy(UpdatePolicy(rollout_cohort=42))
    fetcher = Fetcher(b"{}")
    service = UpdateService(
        store=store,
        installed=_installed(
            install_type=InstallType.SOURCE_CHECKOUT,
            platform="linux",
            publisher_identity="",
        ),
        trust={},
        manifest_fetcher=fetcher,
        downloader=Downloader(),
        adapter=DeveloperGitUpdateAdapter(tmp_path),
        runtime_probe=lambda: ActiveWorkStatus(True),
        now=lambda: NOW,
    )
    return service, fetcher, forces


def test_source_checkout_behind_remote_reports_commit_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, fetcher, forces = _developer_service(
        tmp_path,
        monkeypatch,
        {"checked": True, "up_to_date": False, "commits_behind": 3, "reason": None},
    )

    operation = service.check(force=True)

    assert operation.state is UpdateState.UNSUPPORTED_INSTALL
    assert operation.error_category == "manual_update_required"
    assert "3 commits behind origin/main" in operation.safe_diagnostic
    assert fetcher.calls == []
    assert forces == [True]
    assert operation.last_successful_check_at
    assert operation.next_retry_at == ""


def test_source_checkout_one_commit_behind_uses_singular(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, _, _ = _developer_service(
        tmp_path,
        monkeypatch,
        {"checked": True, "up_to_date": False, "commits_behind": 1, "reason": None},
    )

    operation = service.check(force=True)

    assert "1 commit behind origin/main" in operation.safe_diagnostic


def test_source_checkout_current_with_remote_is_up_to_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, _, _ = _developer_service(
        tmp_path,
        monkeypatch,
        {"checked": True, "up_to_date": True, "commits_behind": 0, "reason": None},
    )

    operation = service.check(force=True)

    assert operation.state is UpdateState.UP_TO_DATE
    assert operation.error_category == ""
    assert operation.last_successful_check_at


def test_source_checkout_offline_git_check_is_retried_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, _, _ = _developer_service(
        tmp_path,
        monkeypatch,
        {"checked": False, "reason": "Update check timed out — you may be offline."},
    )

    operation = service.check(force=True)

    assert operation.state is UpdateState.UNAVAILABLE
    assert operation.error_category == "offline"
    assert operation.next_retry_at


@pytest.mark.parametrize(
    "reason",
    [
        "No 'origin' remote is configured.",
        "OPai isn't running from a git checkout, so it can't check for updates itself.",
    ],
)
def test_source_checkout_without_remote_is_manual_not_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
):
    service, _, _ = _developer_service(
        tmp_path, monkeypatch, {"checked": False, "reason": reason}
    )

    operation = service.check(force=True)

    assert operation.state is UpdateState.UNSUPPORTED_INSTALL
    assert operation.error_category == "manual_update_required"
    assert operation.next_retry_at == ""


def test_automatic_policy_downloads_and_verifies_in_background(tmp_path: Path):
    policy = UpdatePolicy(
        automatic_downloads=True,
        automatic_install_on_quit=True,
        rollout_cohort=10,
    )
    service, _, downloader, _ = _service(tmp_path, policy=policy)

    operation = service.check(force=True, allow_automatic_download=True)

    assert downloader.calls == 2
    assert operation.state is UpdateState.INSTALL_ON_QUIT
    assert operation.staged_sha256 == hashlib.sha256(ARTIFACT).hexdigest()
    assert Path(operation.staged_artifact).is_relative_to(service.store.paths.staging)


def test_digest_mismatch_never_becomes_ready_to_install(tmp_path: Path):
    service, _, _, _ = _service(tmp_path, body=b"x" * len(ARTIFACT))
    available = service.check(force=True)

    operation = service.download(available.operation_id)

    assert operation.state is UpdateState.FAILED_TERMINAL
    assert operation.error_category == "artifact_hash_mismatch"
    assert not operation.staged_artifact


def test_native_publisher_mismatch_never_becomes_ready_to_install(tmp_path: Path):
    service, _, _, _ = _service(tmp_path, adapter=Adapter(publisher="CN=Attacker"))
    available = service.check(force=True)

    operation = service.download(available.operation_id)

    assert operation.state is UpdateState.FAILED_TERMINAL
    assert operation.error_category == "publisher_mismatch"


def test_management_takeover_blocks_download_at_the_final_boundary(tmp_path: Path):
    service, _, downloader, _ = _service(tmp_path)
    available = service.check(force=True)
    service.store._managed_policy = {"owner": "mdm", "management_source": "fixture"}

    blocked = service.download(available.operation_id)

    assert blocked.state is UpdateState.POLICY_BLOCKED
    assert blocked.error_category == "installation_owned_elsewhere"
    assert downloader.calls == 0


def test_management_takeover_blocks_install_at_the_final_boundary(tmp_path: Path):
    service, _, _, adapter = _service(tmp_path)
    ready = service.download(service.check(force=True).operation_id)
    service.store._managed_policy = {"owner": "store", "management_source": "fixture"}

    blocked = service.install(ready.operation_id, mode="now")

    assert blocked.state is UpdateState.POLICY_BLOCKED
    assert blocked.error_category == "installation_owned_elsewhere"
    assert adapter.installs == []


def test_active_work_defers_immediate_install_without_invoking_adapter(tmp_path: Path):
    service, _, _, adapter = _service(tmp_path, active=True)
    ready = service.download(service.check(force=True).operation_id)

    operation = service.install(ready.operation_id, mode="now")

    assert operation.state is UpdateState.WAITING_FOR_IDLE
    assert operation.error_category == "active_work_blocked"
    assert adapter.installs == []


def test_consequence_accepted_does_not_bypass_unreconciled_active_work(tmp_path: Path):
    service, _, _, adapter = _service(tmp_path, active=True)
    ready = service.download(service.check(force=True).operation_id)

    operation = service.install(
        ready.operation_id,
        mode="now",
        consequence_accepted=True,
    )

    assert operation.state is UpdateState.WAITING_FOR_IDLE
    assert adapter.installs == []


def test_safe_install_records_last_known_good_and_native_transaction(tmp_path: Path):
    service, _, _, adapter = _service(tmp_path)
    ready = service.download(service.check(force=True).operation_id)

    operation = service.install(ready.operation_id, mode="now")

    assert operation.state is UpdateState.RESTARTING
    assert operation.native_transaction == "tx-1"
    assert operation.last_known_good["version"] == "0.2.1a1"
    assert adapter.installs[0][1] == "now"
    assert operation.health_deadline_at
    assert operation.native_action == "install"


def test_packaged_download_fails_closed_without_a_signed_recovery_candidate(
    tmp_path: Path,
):
    service, _, _, adapter = _service(tmp_path)
    available = service.check(force=True)
    candidate = dict(available.candidate)
    candidate["rollback_compatible"] = False
    candidate["native"] = {"appinstaller_url": candidate["native"]["appinstaller_url"]}
    service.store.save_operation(replace(available, candidate=candidate))

    blocked = service.download(available.operation_id)

    assert blocked.state is UpdateState.FAILED_TERMINAL
    assert blocked.error_category == "recovery_unavailable"
    assert adapter.installs == []


def test_install_on_quit_is_durable_until_the_quit_boundary(tmp_path: Path):
    service, _, _, adapter = _service(tmp_path)
    ready = service.download(service.check(force=True).operation_id)

    scheduled = service.install(ready.operation_id, mode="on_quit")
    launched = service.install_on_quit()

    assert scheduled.state is UpdateState.INSTALL_ON_QUIT
    assert launched.state is UpdateState.RESTARTING
    assert adapter.installs[0][1] == "on_quit"


def test_waiting_for_idle_is_re_evaluated_by_background_maintenance(tmp_path: Path):
    service, _, _, adapter = _service(tmp_path, active=True)
    ready = service.download(service.check(force=True).operation_id)
    waiting = service.install(ready.operation_id, mode="when_idle")
    service.runtime_probe = lambda: ActiveWorkStatus(True)

    progressed = service.maintain()

    assert waiting.state is UpdateState.WAITING_FOR_IDLE
    assert progressed.state is UpdateState.RESTARTING
    assert adapter.installs == [(Path(ready.staged_artifact), "when_idle")]


def test_periodic_maintenance_runs_a_due_background_check(tmp_path: Path):
    service, fetcher, _, _ = _service(tmp_path)
    first = service.check(force=True)
    service.store.save_operation(
        replace(first, last_successful_check_at=(NOW - timedelta(hours=5)).isoformat())
    )

    refreshed = service.maintain()

    assert refreshed.state is UpdateState.AVAILABLE
    assert len(fetcher.calls) == 2


def test_periodic_cadence_uses_bounded_stable_local_jitter():
    intervals = {
        _jittered_check_interval(UpdatePolicy(rollout_cohort=cohort))
        for cohort in range(100)
    }

    assert min(intervals) == pytest.approx(4 * 60 * 60 * 0.90)
    assert max(intervals) == pytest.approx(4 * 60 * 60 * 1.10)
    assert _jittered_check_interval(UpdatePolicy(rollout_cohort=42)) in intervals


def test_offline_periodic_check_backs_off_then_recovers_when_network_returns(
    tmp_path: Path,
):
    service, fetcher, _, _ = _service(tmp_path, fetch_error=OSError("offline"))
    failed = service.check(force=True)

    throttled = service.maintain()
    fetcher.error = None
    service._now = lambda: NOW + timedelta(seconds=61)
    recovered = service.maintain()

    assert failed.state is UpdateState.UNAVAILABLE
    assert throttled.operation_id == failed.operation_id
    assert len(fetcher.calls) == 2
    assert recovered.state is UpdateState.AVAILABLE


def test_install_rejects_a_deferred_artifact_outside_the_staging_root(tmp_path: Path):
    service, _, _, adapter = _service(tmp_path)
    ready = service.download(service.check(force=True).operation_id)
    outside = tmp_path / "substituted.msix"
    outside.write_bytes(ARTIFACT)
    service.store.save_operation(
        replace(
            ready,
            staged_artifact=str(outside),
            staged_sha256=hashlib.sha256(ARTIFACT).hexdigest(),
        )
    )

    rejected = service.install(ready.operation_id, mode="now")

    assert rejected.state is UpdateState.FAILED_TERMINAL
    assert rejected.error_category == "staged_artifact_substituted"
    assert adapter.installs == []


def test_new_application_confirms_health_only_for_exact_target(tmp_path: Path):
    service, _, _, _ = _service(tmp_path)
    restarting = service.install(
        service.download(service.check(force=True).operation_id).operation_id,
        mode="now",
    )
    target = _installed(version="0.3.0", build_id="b" * 40)

    operation = service.confirm_health(
        restarting.operation_id,
        running=target,
        interactive=True,
        assets_ok=True,
        state_schema_ok=True,
        doctor_ok=True,
    )

    assert operation.state is UpdateState.COMPLETED
    assert operation.error_category == ""
    assert Path(operation.installed_build["artifact_path"]).is_relative_to(
        service.store.paths.staging
    )
    assert "artifact_path" not in operation.to_public_dict()["installed_build"]


def test_native_helper_failure_is_reconciled_without_claiming_restart(tmp_path: Path):
    result_path = tmp_path / ".opai" / "updater" / "staging" / "native-result.json"
    result_path.parent.mkdir(parents=True)

    class ResultAdapter(Adapter):
        def install(self, artifact, candidate, *, mode):
            result_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "success": False,
                        "error_category": "installer_failed",
                    }
                ),
                encoding="utf-8",
            )
            return AdapterInstallResult(
                True, transaction_id="helper-1", result_path=str(result_path)
            )

    service, _, _, _ = _service(tmp_path, adapter=ResultAdapter())
    restarting = service.install(
        service.download(service.check(force=True).operation_id).operation_id,
        mode="now",
    )

    reconciled = service.maintain()

    assert restarting.state is UpdateState.RESTARTING
    assert reconciled.state is UpdateState.FAILED_RETRIABLE
    assert reconciled.error_category == "installer_failed"


def test_startup_health_reads_real_persisted_schema_and_asset_files(tmp_path: Path):
    service, _, _, _ = _service(tmp_path)
    service.check(force=True)
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("index.html", "app.js", "settings.js", "styles.css"):
        (assets / name).write_text("ok", encoding="utf-8")

    healthy = service.startup_health(assets)
    service.store.paths.policy.write_text("not-json", encoding="utf-8")
    broken = service.startup_health(assets)

    assert healthy == {
        "assets_ok": True,
        "state_schema_ok": True,
        "doctor_ok": True,
    }
    assert broken["state_schema_ok"] is False


@pytest.mark.parametrize(
    ("changes", "category"),
    [
        ({"running": _installed(version="0.2.1a1")}, "health_build_mismatch"),
        ({"interactive": False}, "health_not_interactive"),
        ({"assets_ok": False}, "health_assets_failed"),
        ({"state_schema_ok": False}, "health_state_failed"),
        ({"doctor_ok": False}, "health_doctor_failed"),
    ],
)
def test_failed_post_update_health_quarantines_and_requests_rollback(
    tmp_path: Path, changes: dict[str, object], category: str
):
    service, _, _, _ = _service(tmp_path)
    restarting = service.install(
        service.download(service.check(force=True).operation_id).operation_id,
        mode="now",
    )
    arguments: dict[str, object] = {
        "running": _installed(version="0.3.0", build_id="b" * 40),
        "interactive": True,
        "assets_ok": True,
        "state_schema_ok": True,
        "doctor_ok": True,
    }
    arguments.update(changes)

    operation = service.confirm_health(restarting.operation_id, **arguments)

    assert operation.state is UpdateState.ROLLBACK_PENDING
    assert operation.error_category == category
    assert "0.3.0" in operation.quarantined_versions


def test_rollback_is_not_claimed_until_the_restored_build_confirms_health(
    tmp_path: Path,
):
    service, _, _, adapter = _service(tmp_path)
    restarting = service.install(
        service.download(service.check(force=True).operation_id).operation_id,
        mode="now",
    )
    pending = service.confirm_health(
        restarting.operation_id,
        running=_installed(version="0.3.0", build_id="wrong"),
        interactive=True,
        assets_ok=True,
        state_schema_ok=True,
        doctor_ok=True,
    )

    rolling = service.rollback(pending.operation_id)
    operation = service.confirm_recovery(
        pending.operation_id,
        running=_installed(),
        interactive=True,
        assets_ok=True,
        state_schema_ok=True,
        doctor_ok=True,
    )

    assert rolling.state is UpdateState.ROLLING_BACK
    assert operation.state is UpdateState.ROLLED_BACK
    assert adapter.rollbacks == 1


def test_rollback_refuses_recovery_artifact_outside_staging_root(tmp_path: Path):
    service, _, _, adapter = _service(tmp_path)
    restarting = service.install(
        service.download(service.check(force=True).operation_id).operation_id,
        mode="now",
    )
    pending = service.confirm_health(
        restarting.operation_id,
        running=_installed(version="0.3.0", build_id="b" * 40),
        interactive=False,
        assets_ok=True,
        state_schema_ok=True,
        doctor_ok=True,
    )
    outside = tmp_path / "outside.msix"
    outside.write_bytes(ARTIFACT)
    service.store.save_operation(
        replace(
            pending,
            last_known_good={
                **pending.last_known_good,
                "artifact_path": str(outside),
                "artifact_sha256": hashlib.sha256(ARTIFACT).hexdigest(),
            },
        )
    )

    operation = service.rollback(pending.operation_id)

    assert operation.state is UpdateState.NEEDS_ATTENTION
    assert operation.error_category == "rollback_artifact_invalid"
    assert adapter.rollbacks == 0


def test_rollback_failure_never_claims_the_old_version_is_restored(tmp_path: Path):
    service, _, _, _ = _service(tmp_path, adapter=Adapter(rollback_ok=False))
    restarting = service.install(
        service.download(service.check(force=True).operation_id).operation_id,
        mode="now",
    )
    pending = service.confirm_health(
        restarting.operation_id,
        running=_installed(version="0.3.0", build_id="wrong"),
        interactive=True,
        assets_ok=True,
        state_schema_ok=True,
        doctor_ok=True,
    )

    operation = service.rollback(pending.operation_id)

    assert operation.state is UpdateState.NEEDS_ATTENTION
    assert operation.error_category == "rollback_failed"


def test_commands_are_idempotent_for_the_same_ready_operation(tmp_path: Path):
    service, _, downloader, _ = _service(tmp_path)
    available = service.check(force=True)
    first = service.download(available.operation_id)

    second = service.download(available.operation_id)

    assert second == first
    assert downloader.calls == 2


def test_stale_operation_id_cannot_mutate_current_candidate(tmp_path: Path):
    service, _, _, _ = _service(tmp_path)
    service.check(force=True)

    with pytest.raises(UpdateError, match="stale_operation"):
        service.download("not-the-current-operation")


def test_doctor_and_status_are_views_of_the_same_persisted_operation(tmp_path: Path):
    service, _, _, _ = _service(tmp_path)
    operation = service.check(force=True)

    status = service.status()
    doctor = service.doctor()

    assert status["operation"]["operation_id"] == operation.operation_id
    assert doctor["operation"]["operation_id"] == operation.operation_id
    assert doctor["native"]["name"] == "fake-msix"


def test_public_status_never_exposes_staging_paths_or_signed_urls(tmp_path: Path):
    service, _, _, _ = _service(tmp_path)
    ready = service.download(service.check(force=True).operation_id)

    payload = json.dumps(service.status())

    assert ready.staged_artifact
    assert ready.staged_artifact not in payload
    assert "OPai.msix" not in payload


def test_later_keeps_verified_artifact_and_can_be_resumed(tmp_path: Path):
    service, _, _, _ = _service(tmp_path)
    ready = service.download(service.check(force=True).operation_id)

    deferred = service.defer(ready.operation_id)
    resumed = service.resume(deferred.operation_id)

    assert deferred.state is UpdateState.DEFERRED
    assert deferred.staged_artifact == ready.staged_artifact
    assert resumed.state is UpdateState.READY_TO_INSTALL


def test_skip_is_version_specific_and_persisted_in_app_policy(tmp_path: Path):
    service, _, _, _ = _service(tmp_path)
    available = service.check(force=True)

    skipped = service.skip(available.operation_id)

    assert skipped.state is UpdateState.SKIPPED
    assert service.policy().skipped_version == "0.3.0"


def test_retriable_download_can_retry_same_candidate(tmp_path: Path):
    class Interrupted(Downloader):
        def download(self, *args, **kwargs):
            from opai.update.download import DownloadError

            if self.calls == 0:
                self.calls += 1
                raise DownloadError("download_interrupted", retriable=True)
            return super().download(*args, **kwargs)

    service, _, _, _ = _service(tmp_path)
    downloader = Interrupted()
    service.downloader = downloader
    failed = service.download(service.check(force=True).operation_id)

    recovered = service.download(failed.operation_id)

    assert failed.state is UpdateState.FAILED_RETRIABLE
    assert recovered.state is UpdateState.READY_TO_INSTALL


def test_policy_update_is_application_wide_and_enforces_consent_dependency(
    tmp_path: Path,
):
    service, _, _, _ = _service(tmp_path)

    enabled = service.set_policy(
        automatic_downloads=True, automatic_install_on_quit=True
    )
    disabled = service.set_policy(automatic_downloads=False)

    assert enabled.automatic_install_on_quit is True
    assert disabled.automatic_downloads is False
    assert disabled.automatic_install_on_quit is False
    with pytest.raises(UpdateError, match="automatic_download_required"):
        service.set_policy(automatic_install_on_quit=True)
