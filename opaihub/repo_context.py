"""Canonical active-repository context and dirty-worktree safety helpers."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed git argv, never a shell command
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from .atomic_io import atomic_write_text, interprocess_transaction
from .command_runner import redact
from .repository_safety import (
    DirtyState as CanonicalDirtyState,
    RepositoryProbeError,
    build_repository_safety_receipt,
    capture_repository_handle,
    classify_dirty_state,
)
from .state import state_dir


class DirtyConflictError(RuntimeError):
    """Raised when requested work overlaps changes OPai does not own."""


def _sanitize_remote(value: str) -> str:
    remote = str(value or "").strip()
    try:
        parsed = urlsplit(remote)
    except ValueError:
        return ""
    if parsed.scheme and "@" in parsed.netloc:
        parsed = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[1])
        return redact(urlunsplit(parsed))
    return redact(remote)


@dataclass(frozen=True)
class RepoContext:
    path: Path
    branch: str = ""
    remote: str = ""
    dirty_paths: tuple[str, ...] = ()
    is_git: bool = True
    handle_id: str = ""
    safety: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "branch": self.branch,
            "remote": _sanitize_remote(self.remote),
            "dirty_paths": list(self.dirty_paths),
            "dirty": bool(self.dirty_paths),
            "is_git": self.is_git,
            "handle_id": self.handle_id,
            "safety": dict(self.safety),
        }


@dataclass(frozen=True)
class DirtyAssessment:
    status: str
    can_proceed: bool
    conflicting_paths: tuple[str, ...] = ()
    unrelated_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 12.0,
        "check": False,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    # Fixed executable plus argv list; no shell or user-built command string.
    return subprocess.run(["git", *args], **kwargs)  # nosec B603 B607


def _git_text(root: Path, args: list[str]) -> str:
    result = _run_git(root, args)
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def _dirty_paths(root: Path) -> tuple[str, ...]:
    try:
        return capture_repository_handle(
            root, task_id="active-repository", run_id="context"
        ).dirty_state.changed_paths
    except RepositoryProbeError:
        return ()


def _context_from_handle(handle: Any) -> RepoContext:
    identity = handle.identity
    assessment = classify_dirty_state(handle.dirty_state, planned_paths=None)
    remote = dict(identity.remotes).get("origin", "")
    safety = {
        "schema_version": handle.schema_version,
        "handle": {
            "handle_id": handle.handle_id,
            "captured_at": handle.captured_at,
            "max_age_seconds": handle.max_age_seconds,
        },
        "identity": identity.to_dict(),
        "dirty_state": handle.dirty_state.to_dict(),
        "assessment": assessment.to_dict(),
    }
    return RepoContext(
        path=identity.worktree_root,
        branch=identity.branch,
        remote=remote,
        dirty_paths=handle.dirty_state.changed_paths,
        is_git=True,
        handle_id=handle.handle_id,
        safety=safety,
    )


def context_from_repository_handle(handle: Any) -> RepoContext:
    """Project a previously task-bound canonical handle into legacy context."""

    return _context_from_handle(handle)


def resolve_repo_context(path: str | Path) -> RepoContext:
    """Resolve a selected folder to its enclosing Git worktree when possible."""

    selected = Path(path).expanduser().resolve(strict=False)
    start = selected.parent if selected.is_file() else selected
    try:
        return _context_from_handle(
            capture_repository_handle(
                start, task_id="active-repository", run_id="context"
            )
        )
    except RepositoryProbeError as exc:
        return RepoContext(
            path=start,
            is_git=False,
            safety={
                "schema_version": 1,
                "status": "unavailable",
                "reason": exc.reason,
            },
        )


def repository_safety_surface(
    path: str | Path,
) -> tuple[RepoContext, dict[str, Any], list[dict[str, Any]]]:
    """Build the shared, read-only repository-safety projection for a surface."""

    context = resolve_repo_context(path)
    leases: list[dict[str, Any]] = []
    lease_error = ""
    if context.is_git:
        try:
            from .worktree_leases import list_worktree_leases

            leases = [lease.to_dict() for lease in list_worktree_leases(context.path)]
        except Exception as exc:  # noqa: BLE001 - corrupted leases fail visibly, never open cleanup
            lease_error = redact(str(exc))[:240]
    source = context.safety if isinstance(context.safety, dict) else {}
    identity = source.get("identity")
    assessment = source.get("assessment")
    dirty_state = source.get("dirty_state")
    handle = source.get("handle")
    safety: dict[str, Any] = {
        "schema_version": int(source.get("schema_version") or 1),
        "status": str(source.get("status") or "available"),
        "handle": dict(handle) if isinstance(handle, dict) else {},
        "identity": dict(identity) if isinstance(identity, dict) else {},
        "dirty_state": dict(dirty_state) if isinstance(dirty_state, dict) else {},
        "assessment": dict(assessment) if isinstance(assessment, dict) else {},
    }
    if not context.is_git:
        safety["status"] = "unavailable"
        safety["reason"] = str(source.get("reason") or "probe_unavailable")
    if lease_error:
        safety["lease_error"] = lease_error
    safety["receipt"] = build_repository_safety_receipt(
        source,
        safety["assessment"],
        leases,
    )
    return context, safety, leases


def _active_repo_path(project_root: Path) -> Path:
    return state_dir(project_root) / "gui" / "active_repo.json"


def save_active_repo(project_root: Path, context: RepoContext) -> Path:
    target = _active_repo_path(project_root.expanduser().resolve())
    payload = json.dumps(context.to_dict(), indent=2, sort_keys=True) + "\n"
    with interprocess_transaction(target):
        atomic_write_text(target, payload)
    return target


def load_active_repo(project_root: Path) -> RepoContext | None:
    try:
        data = json.loads(_active_repo_path(project_root).read_text(encoding="utf-8"))
        path = Path(str(data["path"])).expanduser().resolve()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not path.is_dir():
        return None
    return RepoContext(
        path=path,
        branch=str(data.get("branch") or ""),
        remote=_sanitize_remote(str(data.get("remote") or "")),
        dirty_paths=tuple(str(item) for item in data.get("dirty_paths") or []),
        is_git=bool(data.get("is_git", True)),
        handle_id=str(data.get("handle_id") or ""),
        safety=dict(data.get("safety") or {})
        if isinstance(data.get("safety"), dict)
        else {},
    )


def active_repo_context(project_root: Path) -> RepoContext:
    """Restore a valid persisted repo, or detect and persist a new one."""

    workspace = project_root.expanduser().resolve()
    saved = load_active_repo(workspace)
    context = resolve_repo_context(saved.path if saved is not None else workspace)
    save_active_repo(workspace, context)
    return context


def _normal(path: str) -> PurePosixPath:
    return PurePosixPath(str(path).replace("\\", "/").strip("/"))


def _overlaps(left: PurePosixPath, right: PurePosixPath) -> bool:
    return left == right or left in right.parents or right in left.parents


def classify_dirty_paths(
    dirty_paths: Iterable[str], intended_paths: Iterable[str] | None
) -> DirtyAssessment:
    dirty = tuple(dict.fromkeys(str(_normal(path)) for path in dirty_paths if str(path)))
    canonical = classify_dirty_state(
        CanonicalDirtyState(unstaged=dirty), planned_paths=intended_paths
    )
    if canonical.classification == "clean":
        return DirtyAssessment("clean", True)
    if canonical.classification in {"overlapping", "unsafe"}:
        unrelated = tuple(
            path for path in dirty if path not in canonical.overlapping_paths
        )
        return DirtyAssessment(
            "conflicting", False, canonical.overlapping_paths, unrelated
        )
    if canonical.classification == "unknown":
        return DirtyAssessment("needs_inspection", False, unrelated_paths=dirty)
    if canonical.classification == "compatible":
        return DirtyAssessment("compatible", True, unrelated_paths=dirty)
    # An unrelated user change is safe only for the caller to *isolate*; this
    # compatibility API preserves that legacy routing signal. Mutation paths use
    # require_mutation_permitted(), which blocks direct writes in this state.
    return DirtyAssessment("unrelated", True, unrelated_paths=dirty)


def prepare_isolated_worktree(
    repo_root: Path,
    target: Path,
    *,
    branch: str,
    base: str = "origin/main",
    dirty_paths: Iterable[str] = (),
    intended_paths: Iterable[str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    """Create a non-destructive worktree after checking dirty-path overlap."""

    root = repo_root.expanduser().resolve()
    destination = target.expanduser().resolve()
    if not branch.startswith("codex/"):
        raise ValueError("Isolated OPai branches must use the codex/ prefix")
    if destination.exists():
        raise FileExistsError(destination)
    assessment = classify_dirty_paths(dirty_paths, intended_paths)
    if not assessment.can_proceed:
        joined = ", ".join(assessment.conflicting_paths)
        raise DirtyConflictError(f"User changes overlap requested files: {joined}")
    command = [
        "git",
        "worktree",
        "add",
        str(destination),
        "-b",
        branch,
        base,
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "check": True,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    run(command, **kwargs)
    return command
