"""Canonical active-repository context and dirty-worktree safety helpers."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed git argv, never a shell command
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from .command_runner import redact
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

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "path": str(self.path),
            "remote": _sanitize_remote(self.remote),
            "dirty_paths": list(self.dirty_paths),
            "dirty": bool(self.dirty_paths),
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
    result = _run_git(root, ["status", "--porcelain=v1", "--untracked-files=all"])
    output = result.stdout or "" if result.returncode == 0 else ""
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        normalized = path.replace("\\", "/")
        if normalized.startswith((".opaihub/", ".opcoding/")):
            continue
        if normalized and normalized not in paths:
            paths.append(normalized)
    return tuple(paths)


def resolve_repo_context(path: str | Path) -> RepoContext:
    """Resolve a selected folder to its enclosing Git worktree when possible."""

    selected = Path(path).expanduser().resolve()
    start = selected.parent if selected.is_file() else selected
    top = _git_text(start, ["rev-parse", "--show-toplevel"])
    if not top:
        return RepoContext(path=start, is_git=False)
    root = Path(top).resolve()
    return RepoContext(
        path=root,
        branch=_git_text(root, ["branch", "--show-current"]),
        remote=_sanitize_remote(_git_text(root, ["remote", "get-url", "origin"])),
        dirty_paths=_dirty_paths(root),
        is_git=True,
    )


def _active_repo_path(project_root: Path) -> Path:
    return state_dir(project_root) / "gui" / "active_repo.json"


def save_active_repo(project_root: Path, context: RepoContext) -> Path:
    target = _active_repo_path(project_root.expanduser().resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(context.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
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
    if not dirty:
        return DirtyAssessment("clean", True)
    if intended_paths is None:
        return DirtyAssessment("needs_inspection", True, unrelated_paths=dirty)
    intended = tuple(_normal(path) for path in intended_paths if str(path))
    conflicts = tuple(
        path for path in dirty if any(_overlaps(_normal(path), target) for target in intended)
    )
    unrelated = tuple(path for path in dirty if path not in conflicts)
    if conflicts:
        return DirtyAssessment("conflicting", False, conflicts, unrelated)
    return DirtyAssessment("unrelated", True, unrelated_paths=unrelated)


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
