"""Canonical repository identity and mutation-safety primitives (#536).

This module owns the read-only facts which must be captured before OPai can
mutate a repository.  A path alone is deliberately insufficient: the handle
binds a canonical worktree, Git metadata, remotes, filesystem identity, HEAD,
and null-delimited status snapshot.  Callers revalidate that handle immediately
before a write; later tasks layer policy and worktree leases on this contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess  # nosec B404 - every call below uses fixed argv, never a shell
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from .command_runner import redact


SCHEMA_VERSION = 1
DEFAULT_MAX_HANDLE_AGE_SECONDS = 300.0

GitRun = Callable[..., subprocess.CompletedProcess[Any]]


class RepositoryProbeError(RuntimeError):
    """A read-only repository probe could not establish trustworthy identity."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = redact(detail)[:400]
        super().__init__(self.detail or reason)


@dataclass(frozen=True)
class DirtyState:
    """All status categories from porcelain v2, preserving safe path bytes."""

    staged: tuple[str, ...] = ()
    unstaged: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()
    conflicted: tuple[str, ...] = ()
    malformed_records: tuple[str, ...] = ()

    @property
    def changed_paths(self) -> tuple[str, ...]:
        """Every relevant mutable path, de-duplicated in observation order."""

        values = (*self.staged, *self.unstaged, *self.untracked, *self.conflicted)
        return tuple(dict.fromkeys(value for value in values if value))

    @property
    def is_complete(self) -> bool:
        return not self.malformed_records

    def to_dict(self) -> dict[str, object]:
        return {
            "staged": list(self.staged),
            "unstaged": list(self.unstaged),
            "untracked": list(self.untracked),
            "ignored": list(self.ignored),
            "conflicted": list(self.conflicted),
            "malformed_records": list(self.malformed_records),
            "changed_paths": list(self.changed_paths),
            "complete": self.is_complete,
        }


@dataclass(frozen=True)
class RepositoryIdentity:
    """Stable facts identifying one Git worktree at one observed revision."""

    worktree_root: Path
    git_dir: Path
    common_git_dir: Path
    filesystem_id: tuple[int, int] | None
    remotes: tuple[tuple[str, str], ...]
    default_branch: str
    branch: str
    detached: bool
    head_sha: str
    status_fingerprint: str
    repository_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "worktree_root": str(self.worktree_root),
            "git_dir": str(self.git_dir),
            "common_git_dir": str(self.common_git_dir),
            "filesystem_id": list(self.filesystem_id)
            if self.filesystem_id is not None
            else None,
            "remotes": [{"name": name, "url": url} for name, url in self.remotes],
            "default_branch": self.default_branch,
            "branch": self.branch,
            "detached": self.detached,
            "head_sha": self.head_sha,
            "status_fingerprint": self.status_fingerprint,
            "repository_id": self.repository_id,
        }


@dataclass(frozen=True)
class RepositoryHandle:
    """Task/run-bound immutable repository observation."""

    schema_version: int
    handle_id: str
    task_id: str
    run_id: str
    captured_at: float
    max_age_seconds: float
    identity: RepositoryIdentity
    dirty_state: DirtyState

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "handle_id": self.handle_id,
            "task_id": redact(self.task_id)[:128],
            "run_id": redact(self.run_id)[:128],
            "captured_at": self.captured_at,
            "max_age_seconds": self.max_age_seconds,
            "identity": self.identity.to_dict(),
            "dirty_state": self.dirty_state.to_dict(),
        }


@dataclass(frozen=True)
class HandleValidation:
    """Result of recapturing a handle immediately before a mutation."""

    fresh: bool
    reasons: tuple[str, ...]
    current: RepositoryHandle | None

    def to_dict(self) -> dict[str, object]:
        return {
            "fresh": self.fresh,
            "reasons": list(self.reasons),
            "current": self.current.to_dict() if self.current is not None else None,
        }


def _safe_remote(value: str) -> str:
    """Strip credentials before any remote URL is retained or rendered."""

    remote = str(value or "").strip()
    if not remote:
        return ""
    try:
        parsed = urlsplit(remote)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.scheme:
        if "@" in parsed.netloc:
            parsed = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[1])
        return redact(urlunsplit(parsed))
    # SSH's scp-like syntax has no URL scheme, but can still carry user data.
    if "@" in remote and ":" in remote:
        remote = remote.rsplit("@", 1)[1]
    return redact(remote)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _decode_path(value: bytes) -> str:
    # Git emits filesystem bytes. surrogateescape preserves an unusual filename
    # without replacing it with a lossy display character.
    return value.decode("utf-8", "surrogateescape").replace("\\", "/")


def parse_porcelain_v2(raw: bytes) -> DirtyState:
    """Parse ``git status --porcelain=v2 -z`` without display-format guesses."""

    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    ignored: list[str] = []
    conflicted: list[str] = []
    malformed: list[str] = []
    records = raw.split(b"\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        kind = record[:1]
        try:
            if kind == b"#":
                continue
            if kind in {b"?", b"!"}:
                if not record.startswith(kind + b" ") or len(record) <= 2:
                    raise ValueError("missing ordinary path")
                path = _decode_path(record[2:])
                (untracked if kind == b"?" else ignored).append(path)
                continue
            if kind == b"1":
                fields = record.split(b" ", 8)
                if len(fields) != 9:
                    raise ValueError("malformed ordinary record")
                xy, path = fields[1], _decode_path(fields[8])
                if len(xy) != 2:
                    raise ValueError("missing ordinary XY")
                if xy[:1] != b".":
                    staged.append(path)
                if xy[1:2] != b".":
                    unstaged.append(path)
                continue
            if kind == b"2":
                fields = record.split(b" ", 9)
                if len(fields) != 10:
                    raise ValueError("malformed rename/copy record")
                xy, path = fields[1], _decode_path(fields[9])
                if len(xy) != 2:
                    raise ValueError("missing rename/copy XY")
                if xy[:1] != b".":
                    staged.append(path)
                if xy[1:2] != b".":
                    unstaged.append(path)
                if index >= len(records):
                    raise ValueError("missing rename/copy source path")
                index += 1  # source path is observed but destination is mutable path
                continue
            if kind == b"u":
                fields = record.split(b" ", 10)
                if len(fields) != 11:
                    raise ValueError("malformed unmerged record")
                conflicted.append(_decode_path(fields[10]))
                continue
            raise ValueError("unknown porcelain record")
        except ValueError as exc:
            malformed.append(f"{kind.decode('ascii', 'replace')}:{exc}")

    return DirtyState(
        staged=_dedupe(staged),
        unstaged=_dedupe(unstaged),
        untracked=_dedupe(untracked),
        ignored=_dedupe(ignored),
        conflicted=_dedupe(conflicted),
        malformed_records=tuple(malformed),
    )


def _run_git(
    root: Path,
    args: list[str],
    *,
    git_run: GitRun,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "capture_output": True,
        "check": False,
        "timeout": 12.0,
    }
    if text:
        kwargs.update({"text": True, "encoding": "utf-8", "errors": "replace"})
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        return git_run(["git", *args], **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepositoryProbeError("probe_unavailable", str(exc)) from exc


def _git_text(root: Path, args: list[str], *, git_run: GitRun, required: bool) -> str:
    result = _run_git(root, args, git_run=git_run)
    if result.returncode == 0:
        return str(result.stdout or "").strip()
    if required:
        detail = str(result.stderr or result.stdout or "Git command failed")
        raise RepositoryProbeError("probe_unavailable", detail)
    return ""


def _git_bytes(root: Path, args: list[str], *, git_run: GitRun) -> bytes:
    result = _run_git(root, args, git_run=git_run, text=False)
    if result.returncode != 0:
        detail = str(result.stderr or result.stdout or "Git command failed")
        raise RepositoryProbeError("probe_unavailable", detail)
    output = result.stdout or b""
    return output.encode("utf-8", "surrogateescape") if isinstance(output, str) else output


def _filesystem_id(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.stat()
    except OSError:
        return None
    return int(metadata.st_dev), int(metadata.st_ino)


def _resolve_git_path(root: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve(strict=False)


def _remotes(root: Path, *, git_run: GitRun) -> tuple[tuple[str, str], ...]:
    names = _git_text(root, ["remote"], git_run=git_run, required=False).splitlines()
    values: list[tuple[str, str]] = []
    for name in sorted(value.strip() for value in names if value.strip()):
        url = _git_text(root, ["remote", "get-url", name], git_run=git_run, required=False)
        values.append((name, _safe_remote(url)))
    return tuple(values)


def _default_branch(root: Path, branch: str, *, git_run: GitRun) -> str:
    remote_head = _git_text(
        root,
        ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        git_run=git_run,
        required=False,
    )
    if remote_head.startswith("origin/"):
        return remote_head.removeprefix("origin/")
    return branch


def _status_fingerprint(state: DirtyState) -> str:
    payload = json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8", "surrogateescape")).hexdigest()


def _repository_id(
    *,
    root: Path,
    git_dir: Path,
    common_git_dir: Path,
    filesystem_id: tuple[int, int] | None,
    remotes: tuple[tuple[str, str], ...],
) -> str:
    material = {
        "root": os.path.normcase(str(root)),
        "git_dir": os.path.normcase(str(git_dir)),
        "common_git_dir": os.path.normcase(str(common_git_dir)),
        "filesystem_id": filesystem_id,
        "remotes": remotes,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", "surrogateescape")).hexdigest()


def _probe_repository(path: Path, *, git_run: GitRun) -> tuple[RepositoryIdentity, DirtyState]:
    selected = path.expanduser().resolve(strict=False)
    start = selected.parent if selected.is_file() else selected
    if not start.exists():
        raise RepositoryProbeError("repository_missing", f"Repository path is missing: {start}")
    top = _git_text(start, ["rev-parse", "--show-toplevel"], git_run=git_run, required=False)
    if not top:
        raise RepositoryProbeError("probe_unavailable", "Path is not a Git worktree")
    root = Path(top).resolve(strict=True)
    git_dir = _resolve_git_path(root, _git_text(root, ["rev-parse", "--git-dir"], git_run=git_run, required=True))
    common_git_dir = _resolve_git_path(
        root,
        _git_text(root, ["rev-parse", "--git-common-dir"], git_run=git_run, required=True),
    )
    head_sha = _git_text(root, ["rev-parse", "HEAD"], git_run=git_run, required=True)
    branch = _git_text(
        root, ["symbolic-ref", "--quiet", "--short", "HEAD"], git_run=git_run, required=False
    )
    dirty_state = parse_porcelain_v2(
        _git_bytes(
            root,
            [
                "status",
                "--porcelain=v2",
                "-z",
                "--branch",
                "--untracked-files=all",
                "--ignored=matching",
            ],
            git_run=git_run,
        )
    )
    remotes = _remotes(root, git_run=git_run)
    filesystem_id = _filesystem_id(root)
    identity = RepositoryIdentity(
        worktree_root=root,
        git_dir=git_dir,
        common_git_dir=common_git_dir,
        filesystem_id=filesystem_id,
        remotes=remotes,
        default_branch=_default_branch(root, branch, git_run=git_run),
        branch=branch,
        detached=not bool(branch),
        head_sha=head_sha,
        status_fingerprint=_status_fingerprint(dirty_state),
        repository_id=_repository_id(
            root=root,
            git_dir=git_dir,
            common_git_dir=common_git_dir,
            filesystem_id=filesystem_id,
            remotes=remotes,
        ),
    )
    return identity, dirty_state


def capture_repository_handle(
    path: str | Path,
    *,
    task_id: str,
    run_id: str,
    max_age_seconds: float = DEFAULT_MAX_HANDLE_AGE_SECONDS,
    git_run: GitRun = subprocess.run,
    now: Callable[[], float] = time.time,
) -> RepositoryHandle:
    """Capture one canonical, task-bound repository observation."""

    identity, dirty_state = _probe_repository(Path(path), git_run=git_run)
    captured_at = float(now())
    safe_age = max(0.0, float(max_age_seconds))
    material = {
        "schema_version": SCHEMA_VERSION,
        "task_id": str(task_id),
        "run_id": str(run_id),
        "captured_at": captured_at,
        "repository_id": identity.repository_id,
        "head_sha": identity.head_sha,
        "status_fingerprint": identity.status_fingerprint,
    }
    handle_id = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return RepositoryHandle(
        schema_version=SCHEMA_VERSION,
        handle_id=handle_id,
        task_id=str(task_id),
        run_id=str(run_id),
        captured_at=captured_at,
        max_age_seconds=safe_age,
        identity=identity,
        dirty_state=dirty_state,
    )


def _identity_differences(
    previous: RepositoryHandle,
    current: RepositoryHandle,
    *,
    observed_at: float,
) -> tuple[str, ...]:
    before = previous.identity
    after = current.identity
    reasons: list[str] = []
    if observed_at - previous.captured_at > previous.max_age_seconds:
        reasons.append("handle_expired")
    if before.worktree_root != after.worktree_root:
        reasons.append("worktree_relocated")
    if before.filesystem_id != after.filesystem_id:
        reasons.append("repository_replaced")
    if before.git_dir != after.git_dir:
        reasons.append("git_directory_changed")
    if before.common_git_dir != after.common_git_dir:
        reasons.append("common_git_directory_changed")
    if before.remotes != after.remotes or before.default_branch != after.default_branch:
        reasons.append("remote_changed")
    if before.branch != after.branch:
        reasons.append("branch_changed")
    if before.head_sha != after.head_sha:
        reasons.append("head_changed")
    if before.status_fingerprint != after.status_fingerprint:
        reasons.append("dirty_state_changed")
    if after.detached:
        reasons.append("detached_head")
    if not after.repository_id:
        reasons.append("probe_unavailable")
    return tuple(dict.fromkeys(reasons))


def revalidate_repository_handle(
    handle: RepositoryHandle,
    *,
    git_run: GitRun = subprocess.run,
    now: Callable[[], float] = time.time,
) -> HandleValidation:
    """Recapture identity; callers must use this immediately before mutation."""

    observed_at = float(now())
    try:
        current = capture_repository_handle(
            handle.identity.worktree_root,
            task_id=handle.task_id,
            run_id=handle.run_id,
            max_age_seconds=handle.max_age_seconds,
            git_run=git_run,
            now=now,
        )
    except RepositoryProbeError as exc:
        return HandleValidation(False, (exc.reason,), None)
    reasons = _identity_differences(handle, current, observed_at=observed_at)
    return HandleValidation(not reasons, reasons, current)
