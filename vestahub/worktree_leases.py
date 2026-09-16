"""Durable, reconciled leases for Vesta-owned isolated Git worktrees (#537)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell execution
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Callable, Iterable

from . import shadow_journal
from .atomic_io import atomic_write_text, interprocess_transaction
from .command_runner import redact
from .repository_safety import (
    RepositoryHandle,
    RepositorySafetyError,
    capture_repository_handle,
    revalidate_repository_handle,
    require_mutation_permitted,
)
from .state import state_dir
from .boundary_errors import safe_detail


LEASE_SCHEMA_VERSION = 1
LEASE_STATES = frozenset(
    {
        "planned",
        "creating",
        "active",
        "needs_review",
        "completed",
        "cleaning",
        "cleanup_failed",
        "released",
    }
)

GitRun = Callable[..., subprocess.CompletedProcess[str]]


class WorktreeLeaseError(RuntimeError):
    """A lease action cannot safely create, alter, or remove a worktree."""


@dataclass(frozen=True)
class WorktreeLease:
    lease_id: str
    task_id: str
    run_id: str
    owner: str
    repository_id: str
    common_git_dir: str
    path: str
    filesystem_id: tuple[int, int] | None
    branch: str
    requested_base: str
    base_sha: str
    state: str
    created_at: str
    heartbeat_at: str
    expires_at: str
    evidence: dict[str, Any]
    schema_version: int = LEASE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["filesystem_id"] = (
            list(self.filesystem_id) if self.filesystem_id else None
        )
        return value


@dataclass(frozen=True)
class LeaseDecision:
    """A read-only integration preview; it cannot grant apply authority."""

    allowed: bool
    reason: str
    paths: tuple[str, ...]
    recommended_actions: tuple[str, ...]
    lease: WorktreeLease

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "paths": list(self.paths),
            "recommended_actions": list(self.recommended_actions),
            "lease": self.lease.to_dict(),
        }


@dataclass(frozen=True)
class WorktreeRecovery:
    """Observed recovery state and non-destructive actions for one lease."""

    lease: WorktreeLease
    recommended_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "lease": self.lease.to_dict(),
            "recommended_actions": list(self.recommended_actions),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_id(value: str) -> str:
    clean = "".join(char for char in str(value) if char.isalnum() or char in "-_")
    if not clean or clean != str(value) or len(clean) > 96:
        raise WorktreeLeaseError("Invalid worktree lease id")
    return clean


def _filesystem_id(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return int(stat.st_dev), int(stat.st_ino)


def _valid_branch(value: str) -> str:
    branch = str(value or "").strip()
    forbidden = ("..", "@{", "//", "\\", " ", "~", "^", ":", "?", "*", "[")
    if (
        not branch.startswith("codex/")
        or branch.endswith("/")
        or branch.startswith("/")
        or any(token in branch for token in forbidden)
    ):
        raise WorktreeLeaseError("Worktree branch must be a safe codex/ branch")
    return branch


# --- #613 Stage 2: shadow journal --------------------------------------
#
# Stage 1's inventory names this module JOURNAL_OWNED -- "leases: worktree
# ownership". Every lease save is already a whole-record overwrite (``_save``
# always writes the complete ``lease.to_dict()``), which is exactly the shape
# vestahub.shadow_journal mirrors: the latest event's payload *is* the
# projection, so no field-level reduce logic is needed.
#
# ``_save`` is the single place every state transition in this module reaches
# disk through (``create``, ``_reconcile_loaded``, ``heartbeat``, ``release``
# and ``cleanup`` all funnel through it), so mirroring there covers all of
# them without touching each call site.


def _valid_lease_record(record: Mapping[str, Any]) -> bool:
    """A mirrored lease must carry the identity and state a reader needs."""

    return (
        record.get("schema_version") == LEASE_SCHEMA_VERSION
        and record.get("state") in LEASE_STATES
        and isinstance(record.get("lease_id"), str)
        and bool(record.get("lease_id"))
    )


class WorktreeManager:
    """Create and reconcile Vesta-owned worktrees without touching user ones."""

    def __init__(
        self,
        repo_root: Path,
        *,
        git_run: GitRun = subprocess.run,
        disk_usage: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
        min_free_bytes: int = 512 * 1024 * 1024,
        max_active_leases: int = 16,
        lease_ttl_seconds: float = 24 * 3600.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self._git_run = git_run
        self._disk_usage = disk_usage
        self.min_free_bytes = max(0, int(min_free_bytes))
        self.max_active_leases = max(1, int(max_active_leases))
        self.lease_ttl_seconds = max(1.0, float(lease_ttl_seconds))
        self._now = now

    def _directory(self) -> Path:
        return state_dir(self.repo_root) / "repository" / "worktrees"

    def _lease_path(self, lease_id: str) -> Path:
        return self._directory() / f"{_safe_id(lease_id)}.json"

    def _lock_path(self) -> Path:
        return self._directory() / "leases.index"

    def _git(
        self, args: list[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith("GIT_CONFIG_")
        }
        kwargs: dict[str, Any] = {
            "cwd": str(cwd or self.repo_root),
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 30.0,
            "check": False,
            "env": env,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            return self._git_run(["git", *args], **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorktreeLeaseError(
                f"Git worktree command unavailable: {redact(str(exc))[:240]}"
            ) from exc

    def _text(self, args: list[str], *, cwd: Path | None = None) -> str:
        result = self._git(args, cwd=cwd)
        if result.returncode != 0:
            detail = str(result.stderr or result.stdout or "Git command failed")
            raise WorktreeLeaseError(redact(detail)[:400])
        return str(result.stdout or "").strip()

    def _save(self, lease: WorktreeLease) -> WorktreeLease:
        if lease.state not in LEASE_STATES:
            raise WorktreeLeaseError(f"Invalid worktree lease state: {lease.state}")
        path = self._lease_path(lease.lease_id)
        try:
            with interprocess_transaction(path):
                atomic_write_text(
                    path, json.dumps(lease.to_dict(), sort_keys=True, indent=2) + "\n"
                )
                # #613 Stage 3: mirrored while still holding the file's own
                # lock, so the journal can never observe saves in a different
                # order than the file did.
                shadow_journal.record_snapshot(
                    path, lease.to_dict(), is_valid_record=_valid_lease_record
                )
        except (OSError, TimeoutError, ValueError) as exc:
            raise WorktreeLeaseError(
                f"Could not persist worktree lease: {redact(str(exc))[:240]}"
            ) from exc
        return lease

    def load(self, lease_id: str) -> WorktreeLease:
        path = self._lease_path(lease_id)
        try:
            with interprocess_transaction(path):
                data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise WorktreeLeaseError(
                f"Could not read worktree lease: {redact(str(exc))[:240]}"
            ) from exc
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != LEASE_SCHEMA_VERSION
        ):
            raise WorktreeLeaseError("Unsupported worktree lease schema")
        filesystem = data.get("filesystem_id")
        if filesystem is not None and (
            not isinstance(filesystem, list)
            or len(filesystem) != 2
            or not all(isinstance(item, int) for item in filesystem)
        ):
            raise WorktreeLeaseError("Invalid worktree lease filesystem identity")
        evidence = data.get("evidence")
        if not isinstance(evidence, dict):
            raise WorktreeLeaseError("Invalid worktree lease evidence")
        try:
            lease = WorktreeLease(
                lease_id=_safe_id(str(data["lease_id"])),
                task_id=str(data.get("task_id") or ""),
                run_id=str(data.get("run_id") or ""),
                owner=str(data.get("owner") or ""),
                repository_id=str(data["repository_id"]),
                common_git_dir=str(data["common_git_dir"]),
                path=str(data["path"]),
                filesystem_id=tuple(filesystem) if filesystem is not None else None,
                branch=_valid_branch(str(data["branch"])),
                requested_base=str(data["requested_base"]),
                base_sha=str(data["base_sha"]),
                state=str(data["state"]),
                created_at=str(data["created_at"]),
                heartbeat_at=str(data["heartbeat_at"]),
                expires_at=str(data["expires_at"]),
                evidence=dict(evidence),
                schema_version=int(data["schema_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WorktreeLeaseError("Malformed worktree lease") from exc
        if lease.state not in LEASE_STATES:
            raise WorktreeLeaseError("Unknown worktree lease state")
        return lease

    def _raw_legacy_snapshot(self, lease_id: str) -> dict[str, Any]:
        """The on-disk lease dict without :func:`load`'s strict validation.

        :func:`load` raises on anything invalid, by design -- every other
        caller in this module needs a lease it can trust or an explicit
        error, never a silent guess. The comparator needs the opposite: read
        whatever is actually there, valid or not, so a real divergence can be
        reported rather than raised past.
        """
        try:
            data = json.loads(self._lease_path(lease_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def shadow_journal_projection(self, lease_id: str) -> dict[str, Any]:
        """The lease state the shadow journal alone would reconstruct."""
        return shadow_journal.projection(
            self._lease_path(lease_id), is_valid_record=_valid_lease_record
        )

    def lease_contradiction_report(self, lease_id: str) -> dict[str, Any] | None:
        """``None`` when the file and its shadow agree; otherwise, what disagrees.

        The dual-read #613 asks for. Both sides are read under the lease
        file's own lock -- the same one :func:`_save` holds while writing file
        and shadow together -- so they are one consistent snapshot rather than
        a mix of before-and-after an in-flight save.
        """
        return shadow_journal.contradiction_report(
            self._lease_path(lease_id),
            lambda: self._raw_legacy_snapshot(lease_id),
            is_valid_record=_valid_lease_record,
            identity={"lease_id": lease_id},
        )

    def list(self) -> list[WorktreeLease]:
        directory = self._directory()
        if not directory.is_dir():
            return []
        leases = [self.load(path.stem) for path in directory.glob("*.json")]
        return sorted(leases, key=lambda item: (item.created_at, item.lease_id))

    def _registry(self) -> dict[Path, dict[str, str]]:
        output = self._text(["worktree", "list", "--porcelain"])
        entries: dict[Path, dict[str, str]] = {}
        current: dict[str, str] = {}
        for line in output.splitlines() + [""]:
            if not line:
                raw_path = current.get("worktree")
                if raw_path:
                    entries[Path(raw_path).resolve(strict=False)] = current
                current = {}
                continue
            key, _, value = line.partition(" ")
            if key in {"worktree", "HEAD", "branch"}:
                current[key] = value
        return entries

    def _resolve_base(self, base: str) -> str:
        return self._text(["rev-parse", "--verify", f"{base}^{{commit}}"])

    def _check_destination(self, target: Path) -> Path:
        destination = target.expanduser().resolve(strict=False)
        if destination.exists():
            raise WorktreeLeaseError(
                f"Worktree destination already exists: {destination}"
            )
        try:
            destination.relative_to(self.repo_root)
        except ValueError:
            pass
        else:
            raise WorktreeLeaseError(
                "Worktree destination must be outside the authoritative worktree"
            )
        parent = destination.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        try:
            free = int(self._disk_usage(parent).free)
        except OSError as exc:
            raise WorktreeLeaseError(
                f"Could not inspect available disk space: {safe_detail(exc)}"
            ) from exc
        if free < self.min_free_bytes:
            raise WorktreeLeaseError("Insufficient disk space for isolated worktree")
        return destination

    def _active_leases(self) -> list[WorktreeLease]:
        return [
            lease
            for lease in self.list()
            if lease.state in {"planned", "creating", "active", "cleaning", "completed"}
        ]

    def create(
        self,
        handle: RepositoryHandle,
        *,
        task_id: str,
        run_id: str,
        owner: str,
        branch: str,
        target: Path,
        base: str,
        planned_paths: Iterable[str],
    ) -> WorktreeLease:
        """Create one lease after fresh identity, quota, and collision checks."""

        if (
            not str(task_id).strip()
            or not str(run_id).strip()
            or not str(owner).strip()
        ):
            raise WorktreeLeaseError(
                "Task, run, and owner are required for a worktree lease"
            )
        safe_branch = _valid_branch(branch)
        destination = self._check_destination(Path(target))
        with interprocess_transaction(self._lock_path()):
            try:
                decision = require_mutation_permitted(
                    handle,
                    planned_paths=planned_paths,
                    operation="worktree_create",
                    allow_isolation=True,
                )
            except RepositorySafetyError as exc:
                raise WorktreeLeaseError(safe_detail(exc)) from exc
            if not decision.allowed:
                raise WorktreeLeaseError("Repository safety denied worktree creation")
            current = decision.validation.current
            if current is None:
                raise WorktreeLeaseError(
                    "Repository safety did not return a fresh handle"
                )
            active = self._active_leases()
            if len(active) >= self.max_active_leases:
                raise WorktreeLeaseError("Active worktree lease quota reached")
            if any(
                item.branch == safe_branch or Path(item.path) == destination
                for item in active
            ):
                raise WorktreeLeaseError(
                    "Worktree branch or destination is already leased"
                )
            registry = self._registry()
            if destination in registry or any(
                item.get("branch") == f"refs/heads/{safe_branch}"
                for item in registry.values()
            ):
                raise WorktreeLeaseError(
                    "Git already has this worktree branch or destination"
                )
            base_sha = self._resolve_base(str(base))
            now = float(self._now())
            stamp = _now_iso()
            lease = WorktreeLease(
                lease_id=uuid.uuid4().hex,
                task_id=str(task_id),
                run_id=str(run_id),
                owner=str(owner),
                repository_id=current.identity.repository_id,
                common_git_dir=str(current.identity.common_git_dir),
                path=str(destination),
                filesystem_id=None,
                branch=safe_branch,
                requested_base=str(base),
                base_sha=base_sha,
                state="creating",
                created_at=stamp,
                heartbeat_at=stamp,
                expires_at=datetime.fromtimestamp(
                    now + self.lease_ttl_seconds, tz=timezone.utc
                )
                .replace(microsecond=0)
                .isoformat(),
                evidence={
                    "registry_state": "creating",
                    "repository_id": current.identity.repository_id,
                    "base_sha": base_sha,
                    "command": [
                        "git",
                        "worktree",
                        "add",
                        str(destination),
                        "-b",
                        safe_branch,
                        base_sha,
                    ],
                    "safety": decision.to_dict(),
                },
            )
            self._save(lease)
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                failed = replace(
                    lease,
                    state="cleanup_failed",
                    heartbeat_at=_now_iso(),
                    evidence={
                        **lease.evidence,
                        "registry_state": "destination_failed",
                        "reasons": ["destination_create_failed"],
                    },
                )
                self._save(failed)
                raise WorktreeLeaseError(
                    f"Could not create worktree destination: {redact(str(exc))[:240]}"
                ) from exc
            result = self._git(
                ["worktree", "add", str(destination), "-b", safe_branch, base_sha]
            )
            if result.returncode != 0:
                failed = replace(
                    lease,
                    state="cleanup_failed",
                    heartbeat_at=_now_iso(),
                    evidence={
                        **lease.evidence,
                        "registry_state": "add_failed",
                        "reasons": ["worktree_add_failed"],
                    },
                )
                self._save(failed)
                detail = str(
                    result.stderr or result.stdout or "git worktree add failed"
                )
                raise WorktreeLeaseError(redact(detail)[:400])
            return self._reconcile_loaded(lease)

    def _reconcile_loaded(self, lease: WorktreeLease) -> WorktreeLease:
        if lease.state == "released":
            return lease
        destination = Path(lease.path).resolve(strict=False)
        registry = self._registry()
        reasons: list[str] = []
        entry = registry.get(destination)
        if entry is None:
            reasons.append("registry_missing")
        if not destination.is_dir():
            reasons.append("worktree_missing")
        current = None
        if not reasons:
            try:
                current = capture_repository_handle(
                    destination, task_id=lease.task_id, run_id=lease.run_id
                )
            except Exception:  # noqa: BLE001 - degraded state must be visible
                reasons.append("worktree_probe_unavailable")
        if current is not None:
            if str(current.identity.common_git_dir) != lease.common_git_dir:
                reasons.append("common_git_directory_changed")
            if current.identity.branch != lease.branch:
                reasons.append("branch_changed")
            if (
                lease.filesystem_id is not None
                and current.identity.filesystem_id != lease.filesystem_id
            ):
                reasons.append("worktree_replaced")
            if current.dirty_state.changed_paths or current.dirty_state.conflicted:
                reasons.append("dirty_worktree")
            try:
                ahead = self._text(
                    ["rev-list", "--max-count=1", f"{lease.base_sha}..HEAD"],
                    cwd=destination,
                )
            except WorktreeLeaseError:
                ahead = "unknown"
            if ahead:
                reasons.append(
                    "unpushed_commits" if ahead != "unknown" else "history_unavailable"
                )
        if entry is not None and entry.get("branch") != f"refs/heads/{lease.branch}":
            reasons.append("registry_branch_changed")
        if reasons:
            reviewed = replace(
                lease,
                state="needs_review",
                heartbeat_at=_now_iso(),
                evidence={
                    **lease.evidence,
                    "registry_state": "needs_review",
                    "reasons": sorted(set(reasons)),
                },
            )
            return self._save(reviewed)
        active = replace(
            lease,
            filesystem_id=current.identity.filesystem_id
            if current is not None
            else None,
            state="active",
            heartbeat_at=_now_iso(),
            evidence={
                **lease.evidence,
                "registry_state": "registered",
                "observed_head": current.identity.head_sha
                if current is not None
                else "",
                "reasons": [],
            },
        )
        return self._save(active)

    def reconcile(self, lease_id: str) -> WorktreeLease:
        with interprocess_transaction(self._lock_path()):
            return self._reconcile_loaded(self.load(lease_id))

    def _diff_paths(self, root: Path, revision: str) -> tuple[str, ...]:
        result = self._git(["diff", "--name-only", "-z", revision], cwd=root)
        if result.returncode != 0:
            detail = str(result.stderr or result.stdout or "Git diff failed")
            raise WorktreeLeaseError(redact(detail)[:400])
        return tuple(
            dict.fromkeys(
                value.replace("\\", "/")
                for value in str(result.stdout or "").split("\0")
                if value
            )
        )

    def preview_apply(
        self, lease_id: str, target_handle: RepositoryHandle
    ) -> LeaseDecision:
        """Preview source/target divergence without changing either worktree."""

        lease = self.load(lease_id)
        if lease.state == "released":
            return LeaseDecision(False, "lease_released", (), ("inspect",), lease)
        validation = revalidate_repository_handle(target_handle)
        if not validation.fresh or validation.current is None:
            return LeaseDecision(
                False,
                "target_stale",
                (),
                ("inspect_target", "recapture_target"),
                lease,
            )
        source = Path(lease.path).resolve(strict=False)
        if source not in self._registry() or not source.is_dir():
            reviewed = self._reconcile_loaded(lease)
            return LeaseDecision(
                False,
                "source_unavailable",
                (),
                ("inspect", "recover"),
                reviewed,
            )
        try:
            source_paths = self._diff_paths(source, f"{lease.base_sha}..HEAD")
            target_paths = self._diff_paths(
                validation.current.identity.worktree_root,
                f"{lease.base_sha}..{validation.current.identity.head_sha}",
            )
        except WorktreeLeaseError:
            return LeaseDecision(
                False,
                "preview_unavailable",
                (),
                ("inspect", "retry_preview"),
                lease,
            )
        overlap = tuple(sorted(set(source_paths) & set(target_paths)))
        target_moved = validation.current.identity.head_sha != lease.base_sha
        if target_moved and overlap:
            return LeaseDecision(
                False,
                "target_diverged_overlap",
                overlap,
                ("inspect_conflicts", "rebase_or_merge_manually"),
                lease,
            )
        if target_moved:
            return LeaseDecision(
                True,
                "target_diverged_nonoverlap",
                tuple(sorted(source_paths)),
                ("review_preview", "request_apply_authority"),
                lease,
            )
        return LeaseDecision(
            True,
            "safe_preview",
            tuple(sorted(source_paths)),
            ("review_preview", "request_apply_authority"),
            lease,
        )

    def heartbeat(self, lease_id: str, *, owner: str) -> WorktreeLease:
        """Renew an active owned lease without changing its worktree."""

        with interprocess_transaction(self._lock_path()):
            lease = self.load(lease_id)
            if lease.owner != str(owner) or lease.state not in {"creating", "active"}:
                raise WorktreeLeaseError(
                    "Only an active lease owner may renew its lease"
                )
            now = float(self._now())
            return self._save(
                replace(
                    lease,
                    heartbeat_at=_now_iso(),
                    expires_at=datetime.fromtimestamp(
                        now + self.lease_ttl_seconds, tz=timezone.utc
                    )
                    .replace(microsecond=0)
                    .isoformat(),
                )
            )

    def release(self, lease_id: str, *, owner: str) -> WorktreeLease:
        """Mark completed work for review; this does not remove a worktree."""

        with interprocess_transaction(self._lock_path()):
            lease = self.load(lease_id)
            if lease.owner != str(owner):
                raise WorktreeLeaseError("Only the lease owner may release it")
            if lease.state not in {"active", "needs_review"}:
                raise WorktreeLeaseError(
                    "Only active or reviewable leases may be released"
                )
            return self._save(
                replace(lease, state="completed", heartbeat_at=_now_iso())
            )

    def recover(self) -> list[WorktreeRecovery]:
        """Reconcile every durable lease and recommend only non-destructive actions."""

        with interprocess_transaction(self._lock_path()):
            recovered: list[WorktreeRecovery] = []
            for lease in self.list():
                observed = self._reconcile_loaded(lease)
                if observed.state == "active":
                    actions = ("resume", "inspect")
                elif observed.state in {"needs_review", "cleanup_failed"}:
                    actions = ("inspect", "cleanup_after_review")
                elif observed.state == "released":
                    actions = ("inspect_receipt",)
                else:
                    actions = ("inspect",)
                recovered.append(WorktreeRecovery(observed, actions))
            return recovered

    def cleanup(self, lease_id: str, *, owner: str) -> WorktreeLease:
        """Remove only a pristine, reconciled worktree owned by this caller."""

        with interprocess_transaction(self._lock_path()):
            lease = self.load(lease_id)
            if lease.owner != str(owner):
                raise WorktreeLeaseError("Only the lease owner may request cleanup")
            current = self._reconcile_loaded(lease)
            if current.state != "active":
                return current
            cleaning = self._save(
                replace(
                    current,
                    state="cleaning",
                    heartbeat_at=_now_iso(),
                    evidence={**current.evidence, "cleanup": "started"},
                )
            )
            result = self._git(["worktree", "remove", cleaning.path])
            if result.returncode != 0:
                review = replace(
                    cleaning,
                    state="needs_review",
                    heartbeat_at=_now_iso(),
                    evidence={
                        **cleaning.evidence,
                        "cleanup": "preserved",
                        "reasons": ["cleanup_refused"],
                    },
                )
                return self._save(review)
            released = replace(
                cleaning,
                state="released",
                heartbeat_at=_now_iso(),
                evidence={**cleaning.evidence, "cleanup": "removed", "reasons": []},
            )
            return self._save(released)


def list_worktree_leases(project_root: Path) -> list[WorktreeLease]:
    """Read persisted leases through the same strict manager contract."""

    return WorktreeManager(project_root).list()
