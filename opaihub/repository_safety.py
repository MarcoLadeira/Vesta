"""Canonical repository identity and mutation-safety primitives (#536).

This module owns the read-only facts which must be captured before Vesta can
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
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from . import shadow_journal
from .atomic_io import atomic_write_text, interprocess_transaction
from .command_runner import redact
from .state import state_dir
from .boundary_errors import safe_detail


SCHEMA_VERSION = 2
DEFAULT_MAX_HANDLE_AGE_SECONDS = 300.0

GitRun = Callable[..., subprocess.CompletedProcess[Any]]


class RepositoryProbeError(RuntimeError):
    """A read-only repository probe could not establish trustworthy identity."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = redact(detail)[:400]
        super().__init__(self.detail or reason)


class RepositorySafetyPersistenceError(RuntimeError):
    """A durable repository-safety record cannot be trusted."""


class RepositorySafetyError(RuntimeError):
    """A mutation was denied by the canonical safety decision."""

    def __init__(self, decision: "MutationDecision") -> None:
        self.decision = decision
        detail = ", ".join(decision.reasons) or decision.assessment.rule_id
        super().__init__(f"Repository mutation blocked: {detail}")


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
    index_fingerprint: str
    working_tree_fingerprint: str
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
            "index_fingerprint": self.index_fingerprint,
            "working_tree_fingerprint": self.working_tree_fingerprint,
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


@dataclass(frozen=True)
class DirtyAssessment:
    """A deterministic dirty-worktree classification with its evidence."""

    classification: str
    outcome: str
    affected_paths: tuple[str, ...]
    overlapping_paths: tuple[str, ...]
    rule_id: str
    confidence: str

    def to_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "outcome": self.outcome,
            "affected_paths": list(self.affected_paths),
            "overlapping_paths": list(self.overlapping_paths),
            "rule_id": self.rule_id,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class MutationDecision:
    """The revalidation/classification result a side-effect boundary consumes."""

    allowed: bool
    operation: str
    validation: HandleValidation
    assessment: DirtyAssessment
    reasons: tuple[str, ...] = ()
    requires_isolation: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "operation": self.operation,
            "validation": self.validation.to_dict(),
            "assessment": self.assessment.to_dict(),
            "reasons": list(self.reasons),
            "requires_isolation": self.requires_isolation,
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


def _is_opai_state_path(value: str) -> bool:
    normalized = value.replace("\\", "/").strip("/")
    return normalized == ".opaihub" or normalized.startswith(
        (".opaihub/", ".opcoding/", ".opcoding")
    )


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            value for value in values if value and not _is_opai_state_path(value)
        )
    )


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
            malformed.append(f"{kind.decode('ascii', 'replace')}:{safe_detail(exc)}")

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
    input_text: str | None = None,
) -> subprocess.CompletedProcess[Any]:
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "capture_output": True,
        "check": False,
        "timeout": 12.0,
    }
    if text:
        kwargs.update({"text": True, "encoding": "utf-8", "errors": "replace"})
    if input_text is not None:
        kwargs["input"] = input_text
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        # `--no-optional-locks` is what makes these probes genuinely read-only.
        # `git status` and `git diff` opportunistically *refresh the index* --
        # they take `.git/index.lock` and rewrite `.git/index` to update its
        # stat cache. Sequentially that was invisible. Run concurrently (the
        # probe issues them in parallel) they contend for that lock and rewrite
        # the index underneath each other, so `index_fingerprint` changed
        # between capturing a repository handle and revalidating it, and a
        # perfectly ordinary edit was refused as `REPOSITORY_SAFETY_BLOCKED`.
        #
        # The flag tells git to skip every lock it only wanted as an
        # optimisation, which is exactly the guarantee the parallel probe
        # assumed it already had.
        return git_run(["git", "--no-optional-locks", *args], **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepositoryProbeError("probe_unavailable", safe_detail(exc)) from exc


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
    return (
        output.encode("utf-8", "surrogateescape") if isinstance(output, str) else output
    )


def capture_workspace_status(
    path: str | Path,
    *,
    git_run: GitRun = subprocess.run,
) -> tuple[str, DirtyState]:
    """Read live branch and dirty paths with one read-only Git process.

    A passive GUI badge does not need the content fingerprints and remote
    identity that mutation safety deliberately captures. Reusing the canonical
    porcelain parser keeps its path handling exact while avoiding the full
    repository-handle probe after every completed turn.
    """

    root = Path(path).expanduser().resolve(strict=False)
    raw = _git_bytes(
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
    branch, _head_sha = _status_identity(raw)
    return branch, parse_porcelain_v2(raw)


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


def _status_identity(raw: bytes) -> tuple[str, str]:
    """Return branch and commit identity from porcelain-v2 branch headers."""
    branch = ""
    head_sha = ""
    for record in raw.split(b"\0"):
        if record.startswith(b"# branch.head "):
            value = record[len(b"# branch.head ") :].decode("utf-8", "surrogateescape")
            branch = "" if value == "(detached)" else value
        elif record.startswith(b"# branch.oid "):
            value = record[len(b"# branch.oid ") :].decode("utf-8", "surrogateescape")
            head_sha = "" if value.startswith("(") else value
    return branch, head_sha


def _filesystem_git_layout(root: Path) -> tuple[Path, Path] | None:
    """Resolve standard and linked-worktree Git directories without a process."""
    marker = root / ".git"
    try:
        if marker.is_dir():
            git_dir = marker.resolve(strict=True)
        elif marker.is_file():
            prefix, separator, value = (
                marker.read_text(encoding="utf-8", errors="replace")
                .strip()
                .partition(":")
            )
            if not separator or prefix.strip().casefold() != "gitdir":
                return None
            git_dir = _resolve_git_path(root, value.strip())
            if not git_dir.is_dir():
                return None
        else:
            return None
        common_marker = git_dir / "commondir"
        if common_marker.is_file():
            common_git_dir = _resolve_git_path(
                git_dir,
                common_marker.read_text(encoding="utf-8", errors="replace").strip(),
            )
            if not common_git_dir.is_dir():
                return None
        else:
            common_git_dir = git_dir
    except OSError:
        return None
    return git_dir, common_git_dir


def _repository_layout(start: Path, *, git_run: GitRun) -> tuple[Path, Path, Path]:
    for candidate in (start, *start.parents):
        layout = _filesystem_git_layout(candidate)
        if layout is None:
            continue
        root = candidate.resolve(strict=True)
        return root, layout[0], layout[1]

    top = _git_text(
        start, ["rev-parse", "--show-toplevel"], git_run=git_run, required=False
    )
    if not top:
        # A distinct reason, because these are different situations and only
        # one of them is a fault: "there is no repository here" is an ordinary
        # workspace (a plain folder, a new project, a synced drive), while
        # "probe_unavailable" means a repository exists and could not be read,
        # which is an anomaly worth refusing over. Collapsing the two is what
        # made an edit-capable run in any non-Git folder look like a safety
        # incident and told the user to "inspect the repository" when there
        # was none to inspect.
        raise RepositoryProbeError("not_a_repository", "Path is not a Git worktree")
    root = Path(top).resolve(strict=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        git_dir_future = pool.submit(
            _git_text,
            root,
            ["rev-parse", "--git-dir"],
            git_run=git_run,
            required=True,
        )
        common_git_dir_future = pool.submit(
            _git_text,
            root,
            ["rev-parse", "--git-common-dir"],
            git_run=git_run,
            required=True,
        )
        git_dir = _resolve_git_path(root, git_dir_future.result())
        common_git_dir = _resolve_git_path(root, common_git_dir_future.result())
    return root, git_dir, common_git_dir


def _status_fingerprint(state: DirtyState) -> str:
    payload = json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8", "surrogateescape")).hexdigest()


def _index_fingerprint(root: Path, git_dir: Path, *, git_run: GitRun) -> str:
    """Fingerprint what the index *means*: staged mode, blob, stage and path.

    This used to hash the raw ``.git/index`` bytes, which carry each entry's
    stat cache (mtime, ctime, size, inode) alongside its content identity. Git
    rewrites that cache whenever it feels like it — refreshing entries it
    considers "racily clean", or rolling the index back when a partial
    ``git commit`` fails — so the fingerprint changed while nothing about the
    user's staged content did.

    The observed failure: ``git add`` refreshed the stat cache, a handle was
    captured against the new bytes, the commit then failed with "nothing to
    commit" and git restored the previous index, and the next edit was refused
    as ``REPOSITORY_SAFETY_BLOCKED`` with ``index_changed``. Nothing unsafe had
    happened; the safety gate was reacting to git's own bookkeeping.

    ``ls-files --stage`` is the content identity the docstring always claimed
    to want: a same-path stage change still alters the blob sha and is caught,
    while stat churn is invisible. Falling back to the raw bytes keeps the
    check strict (never silently absent) if the command is unavailable.
    """

    listing = _run_git(root, ["ls-files", "--stage", "-z"], git_run=git_run, text=False)
    if listing.returncode == 0:
        payload = listing.stdout or b""
        return hashlib.sha256(b"staged\0" + payload).hexdigest()

    index = git_dir / "index"
    try:
        return hashlib.sha256(index.read_bytes()).hexdigest()
    except FileNotFoundError:
        # A freshly initialized repository may have no index until its first
        # add. Treat absence as a stable observed state, not a probe failure.
        return hashlib.sha256(b"missing-index").hexdigest()
    except OSError as exc:
        raise RepositoryProbeError("index_unavailable", safe_detail(exc)) from exc


def _nested_repository_marker(root: Path, relative: str, *, git_run: GitRun) -> str:
    """A stable marker for an untracked path git reports as a directory.

    ``--untracked-files=all`` normally expands every untracked directory into
    its individual files; the one case it does not is a nested repository (a
    plain vendored/cloned ``.git``, not a registered submodule) — git stops
    at that boundary and reports the directory itself. That path cannot be
    content-hashed (``git hash-object`` refuses a directory with "Unable to
    hash (NULL)", which used to crash the whole capture), so its own HEAD is
    folded in instead: detectable when the nested repository's committed
    state moves, stable when it does not, and never a crash (#536).
    """

    nested_root = root / relative.rstrip("/")
    head = _git_text(
        nested_root, ["rev-parse", "HEAD"], git_run=git_run, required=False
    )
    return f"nested-repository:{head}" if head else "unhashable-directory"


def _hash_objects(root: Path, paths: list[str], *, git_run: GitRun) -> dict[str, str]:
    """Hash many untracked paths in as few processes as possible.

    One ``git hash-object`` spawn per untracked path made a dirty tree with
    many untracked files the single largest cost in a GUI boot (each Windows
    process spawn runs tens of milliseconds; a few hundred untracked files
    made this multi-second). ``--stdin-paths`` hashes an arbitrary number of
    paths in one process. A path containing a literal newline cannot be
    represented unambiguously in that newline-delimited stream (this git
    build's ``hash-object`` has no ``-z`` mode), so those rare paths still
    fall back to one call each — correctness over batching for that edge case.
    """

    if not paths:
        return {}
    directories = {path for path in paths if (root / path.rstrip("/")).is_dir()}
    hashes: dict[str, str] = {
        path: _nested_repository_marker(root, path, git_run=git_run)
        for path in directories
    }
    files = [path for path in paths if path not in directories]
    batchable = [path for path in files if "\n" not in path]
    exceptional = [path for path in files if "\n" in path]
    if batchable:
        result = _run_git(
            root,
            ["hash-object", "--no-filters", "--stdin-paths"],
            git_run=git_run,
            input_text="\n".join(batchable) + "\n",
        )
        if result.returncode != 0:
            detail = str(result.stderr or result.stdout or "Git command failed")
            raise RepositoryProbeError("probe_unavailable", detail)
        lines = str(result.stdout or "").splitlines()
        if len(lines) != len(batchable):
            raise RepositoryProbeError(
                "probe_unavailable",
                "hash-object returned an unexpected number of hashes",
            )
        hashes.update(zip(batchable, lines))
    for path in exceptional:
        hashes[path] = _git_text(
            root,
            ["hash-object", "--no-filters", "--", path],
            git_run=git_run,
            required=True,
        )
    return hashes


def _working_tree_fingerprint(
    staged_diff: bytes,
    unstaged_diff: bytes,
    sorted_untracked: list[str],
    untracked_hashes: dict[str, str],
) -> str:
    """Hash tracked diffs and untracked blobs, not merely their status labels."""

    digest = hashlib.sha256()
    for label, chunk in (("staged", staged_diff), ("unstaged", unstaged_diff)):
        digest.update(label.encode("ascii") + b"\0")
        digest.update(chunk)
    for path in sorted_untracked:
        # Git reads the path through its own worktree rules; a missing hash
        # for a path git status just reported as untracked is unsafe.
        digest.update(path.encode("utf-8", "surrogateescape") + b"\0")
        digest.update(untracked_hashes[path].encode("ascii", "replace"))
    return digest.hexdigest()


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


def _probe_repository(
    path: Path, *, git_run: GitRun
) -> tuple[RepositoryIdentity, DirtyState]:
    selected = path.expanduser().resolve(strict=False)
    start = selected.parent if selected.is_file() else selected
    if not start.exists():
        raise RepositoryProbeError(
            "repository_missing", f"Repository path is missing: {start}"
        )
    root, git_dir, common_git_dir = _repository_layout(start, git_run=git_run)

    # None of the calls below depend on each other's output, only on `root` —
    # each spawns a `git.exe` process, and on Windows that spawn latency (tens
    # of ms) dominates a capture once it is paid a dozen times over in series.
    # Running them concurrently overlaps that latency instead of stacking it.
    with ThreadPoolExecutor(max_workers=8) as pool:
        status_future = pool.submit(
            _git_bytes,
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
        remote_names_future = pool.submit(
            _git_text, root, ["remote"], git_run=git_run, required=False
        )
        remote_head_future = pool.submit(
            _git_text,
            root,
            ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
            git_run=git_run,
            required=False,
        )
        staged_diff_future = pool.submit(
            _git_bytes,
            root,
            ["diff", "--cached", "--binary", "--no-ext-diff", "--"],
            git_run=git_run,
        )
        unstaged_diff_future = pool.submit(
            _git_bytes,
            root,
            ["diff", "--binary", "--no-ext-diff", "--"],
            git_run=git_run,
        )
        index_fingerprint_future = pool.submit(
            _index_fingerprint, root, git_dir, git_run=git_run
        )

        raw_status = status_future.result()
        branch, head_sha = _status_identity(raw_status)
        dirty_state = parse_porcelain_v2(raw_status)

        remote_names = sorted(
            value.strip()
            for value in remote_names_future.result().splitlines()
            if value.strip()
        )
        remote_url_futures = {
            name: pool.submit(
                _git_text,
                root,
                ["remote", "get-url", name],
                git_run=git_run,
                required=False,
            )
            for name in remote_names
        }
        remotes = tuple(
            (name, _safe_remote(remote_url_futures[name].result()))
            for name in remote_names
        )

        remote_head = remote_head_future.result()
        default_branch = (
            remote_head.removeprefix("origin/")
            if remote_head.startswith("origin/")
            else branch
        )

        sorted_untracked = sorted(dirty_state.untracked)
        untracked_hashes = _hash_objects(root, sorted_untracked, git_run=git_run)
        staged_diff = staged_diff_future.result()
        unstaged_diff = unstaged_diff_future.result()
        index_fingerprint = index_fingerprint_future.result()

    filesystem_id = _filesystem_id(root)
    identity = RepositoryIdentity(
        worktree_root=root,
        git_dir=git_dir,
        common_git_dir=common_git_dir,
        filesystem_id=filesystem_id,
        remotes=remotes,
        default_branch=default_branch,
        branch=branch,
        detached=not bool(branch),
        head_sha=head_sha,
        status_fingerprint=_status_fingerprint(dirty_state),
        index_fingerprint=index_fingerprint,
        working_tree_fingerprint=_working_tree_fingerprint(
            staged_diff, unstaged_diff, sorted_untracked, untracked_hashes
        ),
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
    if before.index_fingerprint != after.index_fingerprint:
        reasons.append("index_changed")
    if before.working_tree_fingerprint != after.working_tree_fingerprint:
        reasons.append("working_tree_changed")
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


def _normal_path(value: str) -> PurePosixPath | None:
    text = str(value or "").replace("\\", "/").strip()
    if not text or "\0" in text:
        return None
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        return None
    return candidate


def _paths_overlap(left: PurePosixPath, right: PurePosixPath) -> bool:
    return left == right or left in right.parents or right in left.parents


def classify_dirty_state(
    dirty: DirtyState,
    *,
    planned_paths: Iterable[str] | None,
    opai_owned_paths: Iterable[str] = (),
) -> DirtyAssessment:
    """Classify user changes without letting an unknown scope authorize a write."""

    affected = dirty.changed_paths
    if dirty.malformed_records:
        return DirtyAssessment(
            "unsafe",
            "block",
            affected,
            (),
            "malformed_status",
            "unknown",
        )
    if planned_paths is None:
        return DirtyAssessment(
            "unknown",
            "block",
            affected,
            (),
            "unknown_scope",
            "unknown",
        )
    planned = tuple(_normal_path(item) for item in planned_paths)
    if not planned or any(item is None for item in planned):
        return DirtyAssessment(
            "unknown",
            "block",
            affected,
            (),
            "invalid_scope",
            "unknown",
        )
    dirty_paths = tuple(_normal_path(item) for item in affected)
    if any(item is None for item in dirty_paths):
        return DirtyAssessment(
            "unsafe",
            "block",
            affected,
            (),
            "unsafe_dirty_path",
            "unknown",
        )
    if dirty.conflicted:
        return DirtyAssessment(
            "unsafe",
            "block",
            affected,
            tuple(dirty.conflicted),
            "git_conflict",
            "high",
        )
    if not dirty_paths:
        return DirtyAssessment("clean", "proceed", (), (), "clean_tree", "high")

    planned_clean = tuple(item for item in planned if item is not None)
    dirty_clean = tuple(item for item in dirty_paths if item is not None)
    owned = tuple(_normal_path(item) for item in opai_owned_paths)
    owned_clean = tuple(item for item in owned if item is not None)

    def is_opai_owned(path: PurePosixPath) -> bool:
        return any(_paths_overlap(path, prefix) for prefix in owned_clean)

    foreign_dirty = tuple(path for path in dirty_clean if not is_opai_owned(path))
    overlaps = tuple(
        path.as_posix()
        for path in foreign_dirty
        if any(_paths_overlap(path, target) for target in planned_clean)
    )
    if overlaps:
        return DirtyAssessment(
            "overlapping",
            "block",
            affected,
            overlaps,
            "planned_scope_overlap",
            "high",
        )

    if owned_clean and not foreign_dirty:
        return DirtyAssessment(
            "compatible",
            "proceed_carefully",
            affected,
            (),
            "opai_owned_changes",
            "high",
        )
    return DirtyAssessment(
        "unrelated",
        "isolate",
        affected,
        (),
        "unrelated_user_changes",
        "high",
    )


def _degraded_assessment(rule_id: str) -> DirtyAssessment:
    return DirtyAssessment("unknown", "block", (), (), rule_id, "unknown")


def require_mutation_permitted(
    handle: RepositoryHandle,
    *,
    planned_paths: Iterable[str],
    operation: str,
    opai_owned_paths: Iterable[str] = (),
    git_run: GitRun = subprocess.run,
    allow_isolation: bool = False,
    now: Callable[[], float] = time.time,
) -> MutationDecision:
    """Revalidate then classify immediately before a repository side effect."""

    validation = revalidate_repository_handle(handle, git_run=git_run, now=now)
    if not validation.fresh or validation.current is None:
        decision = MutationDecision(
            False,
            str(operation),
            validation,
            _degraded_assessment("stale_handle"),
            validation.reasons or ("probe_unavailable",),
        )
        raise RepositorySafetyError(decision)
    assessment = classify_dirty_state(
        validation.current.dirty_state,
        planned_paths=planned_paths,
        opai_owned_paths=opai_owned_paths,
    )
    if assessment.outcome in {"proceed", "proceed_carefully"}:
        return MutationDecision(True, str(operation), validation, assessment)
    if assessment.outcome == "isolate" and allow_isolation:
        return MutationDecision(
            True,
            str(operation),
            validation,
            assessment,
            ("isolation_required",),
            requires_isolation=True,
        )
    reason = (
        "isolation_required" if assessment.outcome == "isolate" else assessment.rule_id
    )
    raise RepositorySafetyError(
        MutationDecision(False, str(operation), validation, assessment, (reason,))
    )


def build_repository_safety_receipt(
    handle: RepositoryHandle | Mapping[str, Any] | None,
    assessment: DirtyAssessment | Mapping[str, Any] | None,
    leases: Iterable[object] = (),
) -> dict[str, object]:
    """Return a stable, redacted cross-surface repository-safety receipt.

    The GUI and CLI deliberately derive this from the same canonical identity
    projection. It is an observation, not authority: lease paths and actions
    remain separate from the compact receipt so rendering it cannot imply a
    cleanup or apply action is available.
    """

    if isinstance(handle, RepositoryHandle):
        identity = handle.identity.to_dict()
    elif isinstance(handle, Mapping):
        raw_identity = handle.get("identity")
        identity = dict(raw_identity) if isinstance(raw_identity, Mapping) else {}
    else:
        identity = {}
    if isinstance(assessment, DirtyAssessment):
        assessment_payload = assessment.to_dict()
    elif isinstance(assessment, Mapping):
        assessment_payload = dict(assessment)
    else:
        assessment_payload = _degraded_assessment("assessment_unavailable").to_dict()

    states: dict[str, int] = {}
    for raw in leases:
        state = (
            str(raw.get("state") or "")
            if isinstance(raw, Mapping)
            else str(getattr(raw, "state", "") or "")
        )
        if state:
            states[state] = states.get(state, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "repository_id": str(identity.get("repository_id") or ""),
        "assessment": assessment_payload,
        "lease_summary": {
            "total": sum(states.values()),
            "states": dict(sorted(states.items())),
            "needs_review": sum(
                count
                for state, count in states.items()
                if state in {"needs_review", "cleanup_failed"}
            ),
        },
    }


def _handle_path(project_root: Path, handle_id: str) -> Path:
    clean = "".join(char for char in str(handle_id) if char.isalnum() or char in "-_")
    if not clean or clean != str(handle_id):
        raise RepositorySafetyPersistenceError("Invalid repository handle id")
    return (
        state_dir(project_root.expanduser().resolve())
        / "repository"
        / "handles"
        / f"{clean}.json"
    )


def _valid_handle_record(record):
    """A mirrored handle must carry the identity the loader checks it against."""

    return (
        isinstance(record.get("handle_id"), str)
        and bool(record.get("handle_id"))
        and record.get("schema_version") == SCHEMA_VERSION
    )


def _read_handle_raw(path: Path) -> dict[str, Any]:
    """Read the handle as persisted, without the loader's strict validation.

    ``load_repository_handle`` raises on anything malformed, which is right
    for a safety gate but would raise past the very divergence the
    contradiction report exists to describe.
    """

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def repository_handle_projection(project_root: Path, handle_id: str) -> dict[str, Any]:
    """Rebuild one repository handle from its shadow journal."""

    return shadow_journal.projection(
        _handle_path(project_root, handle_id), is_valid_record=_valid_handle_record
    )


def repository_handle_contradiction_report(
    project_root: Path, handle_id: str
) -> dict[str, Any] | None:
    """``None`` when the persisted handle and its shadow agree, else what differs."""

    path = _handle_path(project_root, handle_id)
    return shadow_journal.contradiction_report(
        path,
        lambda: _read_handle_raw(path),
        is_valid_record=_valid_handle_record,
        identity={"handle_id": handle_id},
    )


def save_repository_handle(project_root: Path, handle: RepositoryHandle) -> Path:
    """Durably save a redacted handle; inability to save is a safety failure."""

    path = _handle_path(project_root, handle.handle_id)
    payload = json.dumps(handle.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with interprocess_transaction(path):
            atomic_write_text(path, payload)
            # #613 Stage 2: mirrored inside the same lock. Deliberately
            # still best-effort even though this module fails closed on a
            # save error: during Stage 2 the file is authoritative, so a
            # journal that could not be appended is a lost shadow, not a lost
            # safety guarantee. Raising here would turn a Stage 2 migration
            # detail into a new way for the safety gate to refuse work.
            shadow_journal.record_snapshot(
                path, handle.to_dict(), is_valid_record=_valid_handle_record
            )
    except (OSError, TimeoutError, ValueError) as exc:
        raise RepositorySafetyPersistenceError(
            f"Could not persist repository safety handle: {redact(str(exc))[:240]}"
        ) from exc
    return path


def _tuple_of_strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RepositorySafetyPersistenceError(f"Invalid persisted {field}")
    return tuple(value)


def _load_handle_payload(data: Any) -> RepositoryHandle:
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise RepositorySafetyPersistenceError("Unsupported repository handle schema")
    identity_raw = data.get("identity")
    dirty_raw = data.get("dirty_state")
    if not isinstance(identity_raw, dict) or not isinstance(dirty_raw, dict):
        raise RepositorySafetyPersistenceError("Invalid persisted repository handle")
    filesystem = identity_raw.get("filesystem_id")
    if filesystem is not None and (
        not isinstance(filesystem, list)
        or len(filesystem) != 2
        or not all(isinstance(item, int) for item in filesystem)
    ):
        raise RepositorySafetyPersistenceError("Invalid persisted filesystem identity")
    remotes_raw = identity_raw.get("remotes")
    if not isinstance(remotes_raw, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("url"), str)
        for item in remotes_raw
    ):
        raise RepositorySafetyPersistenceError("Invalid persisted remotes")
    try:
        identity = RepositoryIdentity(
            worktree_root=Path(str(identity_raw["worktree_root"])),
            git_dir=Path(str(identity_raw["git_dir"])),
            common_git_dir=Path(str(identity_raw["common_git_dir"])),
            filesystem_id=tuple(filesystem) if filesystem is not None else None,
            remotes=tuple(
                (item["name"], _safe_remote(item["url"])) for item in remotes_raw
            ),
            default_branch=str(identity_raw.get("default_branch") or ""),
            branch=str(identity_raw.get("branch") or ""),
            detached=bool(identity_raw.get("detached")),
            head_sha=str(identity_raw["head_sha"]),
            status_fingerprint=str(identity_raw["status_fingerprint"]),
            index_fingerprint=str(identity_raw["index_fingerprint"]),
            working_tree_fingerprint=str(identity_raw["working_tree_fingerprint"]),
            repository_id=str(identity_raw["repository_id"]),
        )
        dirty = DirtyState(
            staged=_tuple_of_strings(dirty_raw.get("staged"), "staged paths"),
            unstaged=_tuple_of_strings(dirty_raw.get("unstaged"), "unstaged paths"),
            untracked=_tuple_of_strings(dirty_raw.get("untracked"), "untracked paths"),
            ignored=_tuple_of_strings(dirty_raw.get("ignored"), "ignored paths"),
            conflicted=_tuple_of_strings(
                dirty_raw.get("conflicted"), "conflicted paths"
            ),
            malformed_records=_tuple_of_strings(
                dirty_raw.get("malformed_records"), "malformed status records"
            ),
        )
        return RepositoryHandle(
            schema_version=SCHEMA_VERSION,
            handle_id=str(data["handle_id"]),
            task_id=str(data.get("task_id") or ""),
            run_id=str(data.get("run_id") or ""),
            captured_at=float(data["captured_at"]),
            max_age_seconds=float(data["max_age_seconds"]),
            identity=identity,
            dirty_state=dirty,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RepositorySafetyPersistenceError("Malformed repository handle") from exc


def load_repository_handle(project_root: Path, handle_id: str) -> RepositoryHandle:
    """Read a handle strictly; corrupt state is never treated as a fresh handle."""

    path = _handle_path(project_root, handle_id)
    try:
        with interprocess_transaction(path):
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise RepositorySafetyPersistenceError(
            f"Could not read repository safety handle: {redact(str(exc))[:240]}"
        ) from exc
    handle = _load_handle_payload(data)
    if handle.handle_id != handle_id:
        raise RepositorySafetyPersistenceError(
            "Persisted repository handle identity mismatch"
        )
    return handle
