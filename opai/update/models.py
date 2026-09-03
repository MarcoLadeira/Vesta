"""Versioned provider-neutral update models and state transitions."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


UPDATE_SCHEMA_VERSION = 1
UPDATER_PROTOCOL_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InstallType(str, Enum):
    WINDOWS_MSIX = "windows_msix"
    MACOS_SPARKLE = "macos_sparkle"
    QTIFW = "qtifw"
    PORTABLE = "portable"
    SOURCE_CHECKOUT = "source_checkout"
    UNKNOWN = "unknown"


class UpdateOwner(str, Enum):
    OPAI = "opai"
    MDM = "mdm"
    STORE = "store"
    MANUAL = "manual"


class UpdateState(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED_INSTALL = "unsupported_install"
    POLICY_BLOCKED = "policy_blocked"
    DOWNLOADING = "downloading"
    DEFERRED = "deferred"
    SKIPPED = "skipped"
    VERIFYING = "verifying"
    PAUSED = "paused"
    FAILED_RETRIABLE = "failed_retriable"
    CANCELLED = "cancelled"
    READY_TO_INSTALL = "ready_to_install"
    FAILED_TERMINAL = "failed_terminal"
    WAITING_FOR_IDLE = "waiting_for_idle"
    INSTALL_ON_QUIT = "install_on_quit"
    INSTALLING = "installing"
    RESTARTING = "restarting"
    HEALTH_CHECKING = "health_checking"
    COMPLETED = "completed"
    ROLLBACK_PENDING = "rollback_pending"
    NEEDS_ATTENTION = "needs_attention"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


_TRANSITIONS: dict[UpdateState, frozenset[UpdateState]] = {
    UpdateState.IDLE: frozenset({UpdateState.CHECKING}),
    UpdateState.CHECKING: frozenset(
        {
            UpdateState.UP_TO_DATE,
            UpdateState.AVAILABLE,
            UpdateState.UNAVAILABLE,
            UpdateState.UNSUPPORTED_INSTALL,
            UpdateState.POLICY_BLOCKED,
            # A source checkout has no download/stage/install pipeline: the
            # check itself fast-forwards the working tree, so discovery and
            # completion are the same step. Packaged installs still reach
            # COMPLETED only through INSTALLING -> RESTARTING -> health.
            UpdateState.COMPLETED,
        }
    ),
    UpdateState.UP_TO_DATE: frozenset({UpdateState.CHECKING}),
    UpdateState.AVAILABLE: frozenset(
        {
            UpdateState.DOWNLOADING,
            UpdateState.DEFERRED,
            UpdateState.SKIPPED,
            UpdateState.CHECKING,
            UpdateState.POLICY_BLOCKED,
        }
    ),
    UpdateState.UNAVAILABLE: frozenset({UpdateState.CHECKING}),
    UpdateState.UNSUPPORTED_INSTALL: frozenset({UpdateState.CHECKING}),
    UpdateState.POLICY_BLOCKED: frozenset({UpdateState.CHECKING}),
    UpdateState.DOWNLOADING: frozenset(
        {
            UpdateState.VERIFYING,
            UpdateState.PAUSED,
            UpdateState.FAILED_RETRIABLE,
            UpdateState.CANCELLED,
        }
    ),
    UpdateState.DEFERRED: frozenset(
        {UpdateState.AVAILABLE, UpdateState.READY_TO_INSTALL, UpdateState.CHECKING}
    ),
    UpdateState.SKIPPED: frozenset({UpdateState.CHECKING}),
    UpdateState.VERIFYING: frozenset(
        {UpdateState.READY_TO_INSTALL, UpdateState.FAILED_TERMINAL}
    ),
    UpdateState.PAUSED: frozenset({UpdateState.DOWNLOADING, UpdateState.CANCELLED}),
    UpdateState.FAILED_RETRIABLE: frozenset(
        {UpdateState.CHECKING, UpdateState.DOWNLOADING, UpdateState.INSTALLING}
    ),
    UpdateState.CANCELLED: frozenset({UpdateState.AVAILABLE, UpdateState.CHECKING}),
    UpdateState.READY_TO_INSTALL: frozenset(
        {
            UpdateState.WAITING_FOR_IDLE,
            UpdateState.INSTALL_ON_QUIT,
            UpdateState.INSTALLING,
            UpdateState.DEFERRED,
            UpdateState.POLICY_BLOCKED,
        }
    ),
    UpdateState.FAILED_TERMINAL: frozenset({UpdateState.CHECKING}),
    UpdateState.WAITING_FOR_IDLE: frozenset(
        {
            UpdateState.INSTALLING,
            UpdateState.INSTALL_ON_QUIT,
            UpdateState.DEFERRED,
            UpdateState.POLICY_BLOCKED,
        }
    ),
    UpdateState.INSTALL_ON_QUIT: frozenset(
        {UpdateState.INSTALLING, UpdateState.DEFERRED, UpdateState.POLICY_BLOCKED}
    ),
    UpdateState.INSTALLING: frozenset(
        {
            UpdateState.RESTARTING,
            UpdateState.FAILED_RETRIABLE,
            UpdateState.FAILED_TERMINAL,
        }
    ),
    UpdateState.RESTARTING: frozenset(
        {UpdateState.HEALTH_CHECKING, UpdateState.FAILED_RETRIABLE}
    ),
    UpdateState.HEALTH_CHECKING: frozenset(
        {
            UpdateState.COMPLETED,
            UpdateState.ROLLBACK_PENDING,
            UpdateState.NEEDS_ATTENTION,
        }
    ),
    UpdateState.COMPLETED: frozenset({UpdateState.CHECKING}),
    UpdateState.ROLLBACK_PENDING: frozenset({UpdateState.ROLLING_BACK}),
    UpdateState.NEEDS_ATTENTION: frozenset(
        {UpdateState.CHECKING, UpdateState.ROLLING_BACK}
    ),
    UpdateState.ROLLING_BACK: frozenset(
        {UpdateState.ROLLED_BACK, UpdateState.NEEDS_ATTENTION}
    ),
    UpdateState.ROLLED_BACK: frozenset({UpdateState.CHECKING}),
}


def can_transition(current: UpdateState, target: UpdateState) -> bool:
    return target in _TRANSITIONS.get(UpdateState(current), frozenset())


@dataclass(frozen=True)
class UpdatePolicy:
    schema_version: int = UPDATE_SCHEMA_VERSION
    check_for_updates: bool = True
    automatic_downloads: bool = False
    automatic_install_on_quit: bool = False
    channel: str = "stable"
    minimum_check_interval_seconds: int = 4 * 60 * 60
    owner: UpdateOwner = UpdateOwner.OPAI
    critical_update_enforcement_hours: int | None = None
    maximum_deferral_hours: int | None = None
    mandatory_install_after: str = ""
    management_source: str = ""
    managed_fields: tuple[str, ...] = ()
    remind_later_until: str = ""
    skipped_version: str = ""
    last_user_decision: str = ""
    last_user_decision_at: str = ""
    rollout_cohort: int = -1
    legacy_auto_update_migrated: bool = False
    # Which revision of the cadence table this policy was written under. A
    # persisted policy predating the table carries 0, which is what lets the
    # store migrate an untouched legacy interval exactly once without ever
    # overwriting an interval the user chose for themselves.
    cadence_policy_version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", UpdateOwner(self.owner))
        object.__setattr__(self, "managed_fields", tuple(self.managed_fields))
        if self.schema_version != UPDATE_SCHEMA_VERSION:
            raise ValueError("unsupported update policy schema")
        if self.channel not in {"stable", "beta", "alpha"}:
            raise ValueError("unsupported update channel")
        if self.minimum_check_interval_seconds < 60:
            raise ValueError("update check interval is too small")
        if self.rollout_cohort not in {-1, *range(100)}:
            raise ValueError("rollout cohort must be between 0 and 99")
        if self.maximum_deferral_hours is not None and self.maximum_deferral_hours < 0:
            raise ValueError("maximum deferral must not be negative")
        if self.owner is not UpdateOwner.OPAI:
            object.__setattr__(self, "automatic_downloads", False)
            object.__setattr__(self, "automatic_install_on_quit", False)
        if not self.automatic_downloads:
            object.__setattr__(self, "automatic_install_on_quit", False)

    @property
    def discovery_allowed(self) -> bool:
        return self.check_for_updates

    @property
    def installation_allowed(self) -> bool:
        return self.owner is UpdateOwner.OPAI

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["owner"] = self.owner.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UpdatePolicy":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: raw for key, raw in value.items() if key in allowed})


@dataclass(frozen=True)
class InstalledBuild:
    version: str
    build_id: str
    channel: str
    platform: str
    architecture: str
    install_type: InstallType
    package_identity: str = ""
    publisher_identity: str = ""
    updater_protocol_version: int = UPDATER_PROTOCOL_VERSION
    artifact_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "install_type", InstallType(self.install_type))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["install_type"] = self.install_type.value
        return value


@dataclass(frozen=True)
class UpdateCandidate:
    version: str
    build_id: str
    channel: str
    release_id: str
    published_at: str
    platform: str
    architecture: str
    install_type: InstallType
    artifact_url: str
    artifact_sha256: str
    artifact_size: int
    publisher_identity: str
    metadata_key_ids: tuple[str, ...] = ()
    release_title: str = ""
    release_notes: str = ""
    release_notes_url: str = ""
    minimum_current_version: str = ""
    maximum_current_version: str = ""
    minimum_os_version: str = ""
    criticality: str = "normal"
    required_after: str = ""
    rollout_percentage: int = 100
    cohort_start: int = 0
    minimum_updater_protocol: int = 1
    rollback_compatible: bool = False
    native: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "install_type", InstallType(self.install_type))
        object.__setattr__(self, "metadata_key_ids", tuple(self.metadata_key_ids))
        object.__setattr__(self, "native", dict(self.native))
        if len(self.artifact_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.artifact_sha256
        ):
            raise ValueError("candidate artifact digest is not SHA-256")
        if self.artifact_size <= 0:
            raise ValueError("candidate artifact size must be positive")
        if not 0 <= self.rollout_percentage <= 100 or not 0 <= self.cohort_start < 100:
            raise ValueError("candidate rollout is invalid")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["install_type"] = self.install_type.value
        value["metadata_key_ids"] = list(self.metadata_key_ids)
        value["native"] = dict(self.native)
        return value

    def to_public_dict(self) -> dict[str, Any]:
        """Candidate metadata safe for GUI/CLI (no signed or native URLs)."""
        value = self.to_dict()
        value.pop("artifact_url", None)
        native = value.pop("native", {})
        value["verification"] = {
            "metadata_key_ids": list(self.metadata_key_ids),
            "publisher_identity": self.publisher_identity,
            "native_mechanism": (
                "msix_app_installer"
                if self.install_type is InstallType.WINDOWS_MSIX
                else "sparkle_2"
                if self.install_type is InstallType.MACOS_SPARKLE
                else "manual"
            ),
            "feed_configured": bool(native),
        }
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UpdateCandidate":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: raw for key, raw in value.items() if key in allowed})


@dataclass(frozen=True)
class UpdateOperation:
    schema_version: int = UPDATE_SCHEMA_VERSION
    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: UpdateState = UpdateState.IDLE
    desired_state: str = ""
    installed_build: Mapping[str, Any] = field(default_factory=dict)
    candidate: Mapping[str, Any] = field(default_factory=dict)
    # Units of work, not always bytes: a packaged download counts bytes, a
    # source fast-forward counts named stages. Every consumer renders the
    # fraction, never the raw numbers, and ``progress_label`` says which unit
    # is being counted in words the user reads.
    downloaded_bytes: int = 0
    total_bytes: int = 0
    progress_label: str = ""
    staged_artifact: str = ""
    staged_sha256: str = ""
    native_transaction: str = ""
    native_result_path: str = ""
    native_action: str = ""
    health_deadline_at: str = ""
    retry_count: int = 0
    next_retry_at: str = ""
    error_category: str = ""
    safe_diagnostic: str = ""
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    last_check_at: str = ""
    last_successful_check_at: str = ""
    highest_metadata_version: int = 0
    last_known_good: Mapping[str, Any] = field(default_factory=dict)
    quarantined_versions: tuple[str, ...] = ()
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", UpdateState(self.state))
        object.__setattr__(self, "installed_build", dict(self.installed_build))
        object.__setattr__(self, "candidate", dict(self.candidate))
        object.__setattr__(self, "last_known_good", dict(self.last_known_good))
        object.__setattr__(
            self, "quarantined_versions", tuple(self.quarantined_versions)
        )
        if self.schema_version != UPDATE_SCHEMA_VERSION:
            raise ValueError("unsupported update operation schema")

    def transition(self, target: UpdateState, **changes: Any) -> "UpdateOperation":
        target = UpdateState(target)
        if not can_transition(self.state, target):
            raise ValueError(
                f"illegal update transition: {self.state.value}->{target.value}"
            )
        return replace(self, state=target, updated_at=_now(), **changes)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        value["quarantined_versions"] = list(self.quarantined_versions)
        return value

    def to_public_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        installed_build = dict(value.get("installed_build") or {})
        installed_build.pop("artifact_path", None)
        value["installed_build"] = installed_build
        value["candidate"] = (
            UpdateCandidate.from_dict(self.candidate).to_public_dict()
            if self.candidate
            else {}
        )
        value["artifact_staged"] = bool(self.staged_artifact and self.staged_sha256)
        value["rollback_available"] = bool(
            self.last_known_good.get("artifact_path")
            and self.last_known_good.get("artifact_sha256")
        )
        for sensitive in (
            "staged_artifact",
            "staged_sha256",
            "last_known_good",
            "native_result_path",
        ):
            value.pop(sensitive, None)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "UpdateOperation":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: raw for key, raw in value.items() if key in allowed})
