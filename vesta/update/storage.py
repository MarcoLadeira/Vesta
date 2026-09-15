"""Application-wide updater persistence with atomic cross-process writes."""

from __future__ import annotations

import json
import secrets
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from vestahub import shadow_journal
from vestahub.atomic_io import atomic_write_text, interprocess_transaction
from vestahub.owner_lease import acquire as acquire_lease
from vestahub.owner_lease import is_current as lease_is_current

from .models import UpdateOperation, UpdatePolicy


@dataclass(frozen=True)
class UpdaterPaths:
    root: Path
    policy: Path
    operation: Path
    trust: Path
    downloads: Path
    staging: Path
    lease: Path
    mutex: Path
    health: Path

    @classmethod
    def for_home(cls, home: Path) -> "UpdaterPaths":
        base = home.expanduser().resolve(strict=False) / ".vesta" / "updater"
        return cls(
            root=base,
            policy=base / "policy.json",
            operation=base / "operation.json",
            trust=base / "trust.json",
            downloads=base / "downloads",
            staging=base / "staging",
            lease=base / "operation-owner.json",
            mutex=base / "operation.mutex",
            health=base / "health.json",
        )


def _valid_update_record(record):
    """A mirrored updater record must at least be a readable object.

    Deliberately permissive: this module keeps four distinct documents
    (policy, operation, trust floors, lease) whose schemas differ, and each
    already validates itself on read. The mirror's job is to preserve what
    was written, not to re-litigate four schemas -- a stricter validator here
    would silently drop a record the module itself considers valid, which is
    the failure mode #613 is trying to remove rather than add.
    """

    return isinstance(record, Mapping)


def _read_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class UpdateStore:
    def __init__(
        self,
        paths: UpdaterPaths | None = None,
        *,
        managed_policy: Mapping[str, object] | None = None,
    ) -> None:
        self.paths = paths or UpdaterPaths.for_home(Path.home())
        self._managed_policy = dict(managed_policy or {})

    def _effective_policy(self, policy: UpdatePolicy) -> UpdatePolicy:
        """Apply trusted machine policy without persisting it as user consent."""

        managed = self._managed_policy
        if not managed:
            return policy
        changes: dict[str, Any] = {}
        managed_fields: list[str] = []

        def set_managed(field: str, value: object) -> None:
            changes[field] = value
            managed_fields.append(field)

        if managed.get("disable_update_checks") is True:
            set_managed("check_for_updates", False)
        if managed.get("disable_auto_updates") is True:
            set_managed("automatic_downloads", False)
            set_managed("automatic_install_on_quit", False)
        if "automatic_downloads" in managed:
            set_managed("automatic_downloads", bool(managed["automatic_downloads"]))
        if "automatic_install_on_quit" in managed:
            set_managed(
                "automatic_install_on_quit",
                bool(managed["automatic_install_on_quit"]),
            )
        if managed.get("channel") in {"stable", "beta", "alpha"}:
            set_managed("channel", str(managed["channel"]))
        if managed.get("owner") in {"vesta", "mdm", "store", "manual"}:
            set_managed("owner", str(managed["owner"]))
        if isinstance(managed.get("maximum_deferral_hours"), int):
            set_managed("maximum_deferral_hours", managed["maximum_deferral_hours"])
        if isinstance(managed.get("mandatory_install_after"), str):
            set_managed("mandatory_install_after", managed["mandatory_install_after"])
        changes["management_source"] = str(
            managed.get("management_source") or "machine"
        )
        changes["managed_fields"] = tuple(sorted(set(managed_fields)))
        return replace(policy, **changes)

    def _prepare(self) -> None:
        for path in (self.paths.root.parent, self.paths.root):
            if path.exists() and (
                path.is_symlink()
                or (hasattr(path, "is_junction") and path.is_junction())
            ):
                raise OSError("unsafe updater state directory link")
        self.paths.root.mkdir(parents=True, exist_ok=True)

    def load_policy(self, *, legacy_workspaces: Iterable[Path] = ()) -> UpdatePolicy:
        self._prepare()
        with interprocess_transaction(self.paths.policy):
            raw = _read_object(self.paths.policy)
            if raw:
                try:
                    return self._effective_policy(UpdatePolicy.from_dict(raw))
                except (TypeError, ValueError):
                    return self._effective_policy(UpdatePolicy())
            legacy_enabled = False
            for workspace in legacy_workspaces:
                legacy_path = (
                    workspace.expanduser().resolve(strict=False)
                    / ".vestahub"
                    / "gui"
                    / "preferences.json"
                )
                value = _read_object(legacy_path)
                legacy_enabled = legacy_enabled or value.get("auto_update") is True
            policy = UpdatePolicy(
                automatic_downloads=legacy_enabled,
                automatic_install_on_quit=legacy_enabled,
                rollout_cohort=secrets.randbelow(100),
                legacy_auto_update_migrated=True,
            )
            self._write_policy(policy)
            return self._effective_policy(policy)

    def _write_policy(self, policy: UpdatePolicy) -> None:
        atomic_write_text(
            self.paths.policy,
            json.dumps(policy.to_dict(), indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )
        # #613 Stage 2: every caller already holds
        # interprocess_transaction(self.paths.policy) before reaching here, so
        # the journal observes writes in the order the file took them.
        shadow_journal.record_snapshot(
            self.paths.policy, policy.to_dict(), is_valid_record=_valid_update_record
        )

    def save_policy(self, policy: UpdatePolicy) -> UpdatePolicy:
        self._prepare()
        with interprocess_transaction(self.paths.policy):
            raw = _read_object(self.paths.policy)
            persisted = UpdatePolicy.from_dict(raw) if raw else UpdatePolicy()
            requested = policy.to_dict()
            protected = set(policy.managed_fields)
            values = persisted.to_dict()
            for key, value in requested.items():
                if key not in protected and key not in {
                    "managed_fields",
                    "management_source",
                }:
                    values[key] = value
            clean = UpdatePolicy.from_dict(values)
            self._write_policy(clean)
        return self._effective_policy(clean)

    def update_policy(self, **changes: object) -> UpdatePolicy:
        self._prepare()
        with interprocess_transaction(self.paths.policy):
            raw = _read_object(self.paths.policy)
            current = UpdatePolicy.from_dict(raw) if raw else UpdatePolicy()
            protected = set(self._effective_policy(current).managed_fields)
            updated = replace(
                current,
                **{
                    key: value for key, value in changes.items() if key not in protected
                },
            )
            self._write_policy(updated)
        return self._effective_policy(updated)

    def load_operation(self) -> UpdateOperation:
        self._prepare()
        raw = _read_object(self.paths.operation)
        try:
            return UpdateOperation.from_dict(raw) if raw else UpdateOperation()
        except (TypeError, ValueError):
            return UpdateOperation()

    def save_operation(self, operation: UpdateOperation) -> UpdateOperation:
        self._prepare()
        with interprocess_transaction(self.paths.operation):
            atomic_write_text(
                self.paths.operation,
                json.dumps(operation.to_dict(), indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )
            shadow_journal.record_snapshot(
                self.paths.operation,
                operation.to_dict(),
                is_valid_record=_valid_update_record,
            )
        return operation

    def state_schema_ok(self) -> bool:
        """Strictly validate persisted updater state for post-update health."""

        policy = _read_object(self.paths.policy)
        operation = _read_object(self.paths.operation)
        if not policy or not operation:
            return False
        try:
            UpdatePolicy.from_dict(policy)
            UpdateOperation.from_dict(operation)
        except (TypeError, ValueError):
            return False
        return True

    def load_metadata_floor(self, channel: str) -> int:
        """Return the durable signed-metadata high-water mark for a channel."""

        raw = _read_object(self.paths.trust)
        versions = (
            raw.get("metadata_versions") if raw.get("schema_version") == 1 else {}
        )
        if not isinstance(versions, Mapping):
            return 0
        value = versions.get(channel, 0)
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else 0
        )

    def record_metadata_version(self, channel: str, version: int) -> int:
        """Monotonically persist a version only after signature verification."""

        if channel not in {"stable", "beta", "alpha"} or version < 1:
            raise ValueError("invalid metadata high-water mark")
        self._prepare()
        with interprocess_transaction(self.paths.trust):
            raw = _read_object(self.paths.trust)
            versions = (
                raw.get("metadata_versions") if raw.get("schema_version") == 1 else {}
            )
            clean = dict(versions) if isinstance(versions, Mapping) else {}
            clean[channel] = max(self.load_metadata_floor(channel), version)
            trust = {"schema_version": 1, "metadata_versions": clean}
            atomic_write_text(
                self.paths.trust,
                json.dumps(trust, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )
            # A metadata floor only ever moves forward; mirroring it keeps the
            # anti-rollback high-water mark reconstructible if the file is
            # lost, which is the one value here an attacker would want reset.
            shadow_journal.record_snapshot(
                self.paths.trust, trust, is_valid_record=_valid_update_record
            )
        return int(clean[channel])

    def document_projection(self, name: str) -> dict[str, object]:
        """Rebuild one updater document from its shadow journal.

        ``name`` is one of ``policy``, ``operation`` or ``trust`` -- the three
        documents that are mirrored. The lease is deliberately not: it is
        fencing state owned by :func:`operation_guard`, rebuilt from scratch on
        every acquisition, and a journal of superseded fences would be a record
        of things that are true only until the next process starts.
        """

        path = self._document_path(name)
        return shadow_journal.projection(path, is_valid_record=_valid_update_record)

    def document_contradiction_report(self, name: str) -> dict[str, object] | None:
        """``None`` when one updater document and its shadow agree, else what differs."""

        path = self._document_path(name)
        return shadow_journal.contradiction_report(
            path,
            lambda: _read_object(path),
            is_valid_record=_valid_update_record,
            identity={"document": name},
        )

    def _document_path(self, name: str) -> Path:
        try:
            return {
                "policy": self.paths.policy,
                "operation": self.paths.operation,
                "trust": self.paths.trust,
            }[name]
        except KeyError:
            raise ValueError(f"unknown updater document: {name!r}") from None

    @contextmanager
    def operation_guard(self, *, timeout_seconds: float = 0.25) -> Iterator[int]:
        """Hold one cross-process operation lock and issue a fencing token."""
        self._prepare()
        with interprocess_transaction(
            self.paths.mutex, timeout_seconds=timeout_seconds
        ):
            lease = acquire_lease(self.paths.lease)
            fence = int(lease["fence"])
            try:
                yield fence
            finally:
                if lease_is_current(self.paths.lease, fence):
                    released = {
                        "fence": fence,
                        "pid": lease.get("pid"),
                        "boot": lease.get("boot"),
                        "acquired_at": lease.get("acquired_at"),
                        "heartbeat_at": 0.0,
                        "released": True,
                    }
                    with interprocess_transaction(self.paths.lease):
                        atomic_write_text(
                            self.paths.lease,
                            json.dumps(released, sort_keys=True) + "\n",
                            mode=0o600,
                        )
