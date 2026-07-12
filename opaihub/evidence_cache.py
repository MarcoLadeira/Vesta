"""Stable evidence caching for the OPai router (power-efficiency roadmap Phase 2).

The evidence collector re-scans the whole repository (a recursive language walk
plus several git calls) on every ``opai route``. The raw ``cache_key`` baked
into the evidence payload includes a timestamp, so it never matches and nothing
is ever reused.

This module adds a *stable* fingerprint (git HEAD + working-tree state, or
marker mtimes for non-git projects) so identical repo states reuse a cached
evidence pack instead of re-scanning. Cache reads are side-effect free (they
never create files), so ``opai route`` stays read-only by default (#12); only an
explicit ``write=True`` persists a pack.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess  # nosec B404
import time
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .evidence import collect_evidence
from .proc import no_window_kwargs
from .state import state_dir


EVIDENCE_CACHE_VERSION = 3
DEFAULT_TTL_SECONDS = 3600


@dataclass(frozen=True)
class FingerprintLimits:
    """Bounds for dirty-content hashing before cache reuse is refused."""

    max_dirty_files: int = 64
    max_file_bytes: int = 256 * 1024
    max_total_bytes: int = 1024 * 1024


@dataclass(frozen=True)
class RepoFingerprint:
    """A cacheability assessment that never contains repository content."""

    digest: str
    cacheable: bool
    bypass_reason: str | None
    dirty_file_count: int
    dirty_bytes: int


DEFAULT_FINGERPRINT_LIMITS = FingerprintLimits()
_OPAI_STATE_DIRS = frozenset({".opaihub", ".opcoding"})
_FINGERPRINT_CHUNK_BYTES = 64 * 1024


def _git_bytes(root: Path, args: list[str]) -> bytes | None:
    git = shutil.which("git")
    if not git:
        return None
    try:
        completed = subprocess.run(  # nosec B603
            [git, "-C", str(root), *args],
            capture_output=True,
            timeout=10,
            **no_window_kwargs(),  # no flashing console window on Windows
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _git_output(root: Path, args: list[str]) -> str | None:
    output = _git_bytes(root, args)
    return output.decode("utf-8", errors="replace") if output is not None else None


def _digest(*parts: bytes) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(len(part).to_bytes(8, "big"))
        hasher.update(part)
    return hasher.hexdigest()[:16]


def _relative_path(raw_path: bytes) -> str | None:
    text = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
    pure = PurePosixPath(text)
    if (
        not text
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        return None
    return pure.as_posix()


def _is_opai_state_path(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return bool(parts) and parts[0].casefold() in _OPAI_STATE_DIRS


def _status_records(raw: bytes) -> list[tuple[bytes, list[bytes]]] | None:
    records = raw.split(b"\0")
    parsed: list[tuple[bytes, list[bytes]]] = []
    index = 0
    while index < len(records) - 1:
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            return None
        code = record[:2]
        paths = [record[3:]]
        if b"R" in code or b"C" in code:
            if index >= len(records) - 1:
                return None
            paths.append(records[index])
            index += 1
        parsed.append((code, paths))
    return parsed


def _bypass_fingerprint(
    reason: str,
    *,
    status_basis: bytes = b"",
    dirty_file_count: int = 0,
    dirty_bytes: int = 0,
) -> RepoFingerprint:
    # A bypass digest deliberately changes per call. Compatibility callers that
    # only consume `repo_fingerprint()` therefore cannot accidentally reuse an
    # entry while newer callers inspect `cacheable` and skip the cache outright.
    digest = _digest(
        b"opai-repo-fingerprint-v3-bypass",
        reason.encode("utf-8"),
        status_basis,
        str(time.monotonic_ns()).encode("ascii"),
    )
    return RepoFingerprint(
        digest=digest,
        cacheable=False,
        bypass_reason=reason,
        dirty_file_count=dirty_file_count,
        dirty_bytes=dirty_bytes,
    )


def _content_digest(
    root: Path,
    relative: str,
    *,
    limits: FingerprintLimits,
    total_bytes: int,
) -> tuple[str | None, int, str | None]:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            return None, total_bytes, "unsafe_path"
        if candidate.is_symlink():
            return None, total_bytes, "symlink_input"
        if not candidate.exists():
            return "missing", total_bytes, None
        before = candidate.stat()
    except OSError:
        return None, total_bytes, "unreadable_file"

    if not stat.S_ISREG(before.st_mode):
        return None, total_bytes, "not_regular_file"
    if before.st_size > limits.max_file_bytes:
        return None, total_bytes, "file_too_large"
    if total_bytes + before.st_size > limits.max_total_bytes:
        return None, total_bytes, "dirty_bytes_exceeded"

    hasher = hashlib.sha256()
    bytes_read = 0
    try:
        with candidate.open("rb") as handle:
            while chunk := handle.read(_FINGERPRINT_CHUNK_BYTES):
                if b"\0" in chunk:
                    return None, total_bytes + bytes_read, "binary_file"
                hasher.update(chunk)
                bytes_read += len(chunk)
    except OSError:
        return None, total_bytes, "unreadable_file"

    try:
        after = candidate.stat()
    except OSError:
        return None, total_bytes, "file_changed_during_hash"
    if (
        bytes_read != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        return None, total_bytes, "file_changed_during_hash"
    return hasher.hexdigest(), total_bytes + bytes_read, None


def assess_repo_fingerprint(
    project_root: Path,
    *,
    limits: FingerprintLimits = DEFAULT_FINGERPRINT_LIMITS,
) -> RepoFingerprint:
    """Assess whether Git state is complete enough for safe cache reuse."""
    root = project_root.expanduser().resolve()
    status = _git_bytes(
        root,
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
        ],
    )
    if status is None:
        return _bypass_fingerprint("no_git_repository")
    parsed = _status_records(status)
    if parsed is None:
        return _bypass_fingerprint("unparseable_git_status", status_basis=status)
    head = _git_bytes(root, ["rev-parse", "HEAD"]) or b""

    status_parts = [b"opai-repo-fingerprint-v3", b"git", head.strip()]
    candidates: set[str] = set()
    for code, raw_paths in parsed:
        normalized_paths: list[str] = []
        for raw_path in raw_paths:
            relative = _relative_path(raw_path)
            if relative is None:
                return _bypass_fingerprint(
                    "unsafe_path", status_basis=b"\0".join(status_parts)
                )
            normalized_paths.append(relative)
        if all(_is_opai_state_path(path) for path in normalized_paths):
            continue
        status_parts.extend(
            [
                code,
                *[path.encode("utf-8", "surrogateescape") for path in normalized_paths],
            ]
        )
        if code == b"!!":
            return _bypass_fingerprint(
                "ignored_input", status_basis=b"\0".join(status_parts)
            )
        candidates.update(
            path for path in normalized_paths if not _is_opai_state_path(path)
        )

    status_basis = b"\0".join(status_parts)
    if not candidates:
        return RepoFingerprint(
            digest=_digest(status_basis),
            cacheable=True,
            bypass_reason=None,
            dirty_file_count=0,
            dirty_bytes=0,
        )
    if len(candidates) > limits.max_dirty_files:
        return _bypass_fingerprint(
            "too_many_dirty_files",
            status_basis=status_basis,
            dirty_file_count=len(candidates),
        )

    manifest = bytearray()
    dirty_bytes = 0
    for relative in sorted(candidates):
        digest, dirty_bytes, reason = _content_digest(
            root,
            relative,
            limits=limits,
            total_bytes=dirty_bytes,
        )
        if reason is not None:
            return _bypass_fingerprint(
                reason,
                status_basis=status_basis,
                dirty_file_count=len(candidates),
                dirty_bytes=dirty_bytes,
            )
        manifest.extend(relative.encode("utf-8", "surrogateescape"))
        manifest.extend(b"\0")
        manifest.extend(str(digest).encode("ascii"))
        manifest.extend(b"\0")
    return RepoFingerprint(
        digest=_digest(status_basis, bytes(manifest)),
        cacheable=True,
        bypass_reason=None,
        dirty_file_count=len(candidates),
        dirty_bytes=dirty_bytes,
    )


def repo_fingerprint(project_root: Path) -> str:
    """Return a compatibility digest; inspect the assessment before caching."""
    return assess_repo_fingerprint(project_root).digest


def evidence_cache_key(
    project_root: Path,
    task: str,
    *,
    assessment: RepoFingerprint | None = None,
) -> str:
    root = project_root.expanduser().resolve()
    fingerprint = assessment or assess_repo_fingerprint(root)
    basis = f"v{EVIDENCE_CACHE_VERSION}\n{root}\n{task}\n{fingerprint.digest}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def cache_path(project_root: Path, key: str) -> Path:
    return state_dir(project_root) / "cache" / "evidence" / f"{key}.json"


def collect_evidence_cached(
    project_root: Path,
    task: str = "",
    *,
    write: bool = False,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (evidence, meta), reusing a cached pack when the repo is unchanged.

    ``meta`` carries ``cache_hit``, ``cache_key``, ``age_seconds``, and explicit
    bypass data. Reads never write; pass ``write=True`` to persist a freshly
    collected pack only when the repository assessment is cacheable.
    """
    root = project_root.expanduser().resolve()
    assessment = assess_repo_fingerprint(root)
    key = evidence_cache_key(root, task, assessment=assessment)
    if not assessment.cacheable:
        return collect_evidence(root, task), {
            "cache_hit": False,
            "cache_key": key,
            "age_seconds": 0,
            "cache_bypassed": True,
            "cache_bypass_reason": assessment.bypass_reason,
        }
    path = cache_path(root, key)

    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            cached_at = float(stored.get("cached_at", 0))
            age = time.time() - cached_at
            if age <= ttl_seconds and isinstance(stored.get("evidence"), dict):
                return stored["evidence"], {
                    "cache_hit": True,
                    "cache_key": key,
                    "age_seconds": int(age),
                    "cache_bypassed": False,
                    "cache_bypass_reason": None,
                }
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    evidence = collect_evidence(root, task)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"cached_at": time.time(), "key": key, "evidence": evidence},
                sort_keys=True,
                default=str,
            ),
            encoding="utf-8",
        )
    return evidence, {
        "cache_hit": False,
        "cache_key": key,
        "age_seconds": 0,
        "cache_bypassed": False,
        "cache_bypass_reason": None,
    }
