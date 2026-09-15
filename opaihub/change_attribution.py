"""Immutable, content-addressed repository mutation evidence (#620).

The legacy runtime inferred ownership by subtracting human-readable
``git status --short`` lines.  That representation cannot distinguish the
index from the worktree and does not change when an already-dirty path is
modified again.  This module defines the durable evidence contract used by
the capture, attribution, verification, and delivery layers.

This file deliberately contains no mutation code.  It validates and
serializes facts; later layers may append facts, but cannot rewrite them into
more favourable ownership after the fact.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any


SCHEMA_VERSION = 1

_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_MODE = re.compile(r"[0-7]{6}\Z")
_DRIVE_PATH = re.compile(r"[A-Za-z]:/")

_WORKTREE_KINDS = frozenset(
    {"missing", "file", "symlink", "directory", "gitlink", "other", "unavailable"}
)
_PROBE_STATES = frozenset({"available", "unavailable", "torn"})
_OPERATION_STATES = frozenset(
    {"intent", "succeeded", "failed", "cancelled", "uncertain"}
)
_CHANGE_SET_STATES = frozenset(
    {"active", "completed", "failed", "cancelled", "uncertain"}
)
_CLASSIFICATIONS = frozenset(
    {
        "pre_existing_user",
        "opai_only",
        "overlapping",
        "concurrent_external",
        "uncertain",
    }
)
_CONFIDENCE = frozenset({"high", "medium", "low"})


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _content_digest(kind: str, value: Mapping[str, Any]) -> str:
    payload = f"opai:{kind}:v{SCHEMA_VERSION}\0{_canonical_json(value)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _items(value: Any, field_name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be an array")
    return value


def _text(
    value: Any, field_name: str, *, required: bool = True, limit: int = 512
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if "\0" in value:
        raise ValueError(f"{field_name} must not contain NUL")
    if len(value) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    if required and not value:
        raise ValueError(f"{field_name} is required")
    return value


def _timestamp(value: Any, field_name: str, *, required: bool = True) -> str:
    text = _text(value, field_name, required=required, limit=64)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return text


def _object_id(value: Any, field_name: str, *, required: bool = True) -> str:
    text = _text(value, field_name, required=required, limit=64).lower()
    if text and not _OBJECT_ID.fullmatch(text):
        raise ValueError(f"{field_name} must be a 40- or 64-character object id")
    return text


def _fingerprint(value: Any, field_name: str, *, required: bool = True) -> str:
    text = _text(value, field_name, required=required, limit=64).lower()
    if text and not _FINGERPRINT.fullmatch(text):
        raise ValueError(f"{field_name} must be a SHA-256 fingerprint")
    return text


def _mode(value: Any, field_name: str, *, required: bool = True) -> str:
    text = _text(value, field_name, required=required, limit=6)
    if text and not _MODE.fullmatch(text):
        raise ValueError(f"{field_name} must be a six-digit Git mode")
    return text


def _path(value: Any) -> str:
    # This is a Git path, not a host-language path. A backslash is a legal,
    # distinct filename byte on POSIX and must never alias a slash.
    text = _text(value, "path", limit=4096)
    if text.startswith("/") or _DRIVE_PATH.match(text):
        raise ValueError("path must be repository-relative")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path must be a canonical repository-relative path")
    try:
        text.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError as exc:
        raise ValueError("path contains an unsupported surrogate") from exc
    return text


def _path_bytes_hex(path: str) -> str:
    return path.encode("utf-8", "surrogateescape").hex()


def _filesystem_id(value: Any, field_name: str) -> tuple[int, int] | None:
    if value is None:
        return None
    items = _items(value, field_name)
    if len(items) != 2 or any(
        isinstance(item, bool) or not isinstance(item, int) for item in items
    ):
        raise ValueError(f"{field_name} must contain device and inode integers")
    device, inode = int(items[0]), int(items[1])
    if device < 0 or inode < 0:
        raise ValueError(f"{field_name} values must not be negative")
    return device, inode


def _string_tuple(
    value: Any, field_name: str, *, path_values: bool = False
) -> tuple[str, ...]:
    result: list[str] = []
    for item in _items(value, field_name):
        result.append(
            _path(item) if path_values else _text(item, field_name, limit=512)
        )
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(result)


@dataclass(frozen=True)
class GitEntry:
    """One immutable tree entry."""

    mode: str
    object_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _mode(self.mode, "mode"))
        object.__setattr__(self, "object_id", _object_id(self.object_id, "object_id"))

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "object_id": self.object_id}

    @classmethod
    def from_dict(cls, value: Any) -> "GitEntry":
        payload = _mapping(value, "git_entry")
        return cls(mode=payload.get("mode"), object_id=payload.get("object_id"))


@dataclass(frozen=True)
class IndexEntry:
    """One semantic Git index entry, preserving conflict stages and flags."""

    stage: int
    mode: str
    object_id: str
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.stage, bool)
            or not isinstance(self.stage, int)
            or self.stage not in range(4)
        ):
            raise ValueError("stage must be an integer from 0 through 3")
        object.__setattr__(self, "mode", _mode(self.mode, "mode"))
        object.__setattr__(self, "object_id", _object_id(self.object_id, "object_id"))
        flags = tuple(sorted(_string_tuple(self.flags, "flags")))
        object.__setattr__(self, "flags", flags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "mode": self.mode,
            "object_id": self.object_id,
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "IndexEntry":
        payload = _mapping(value, "index_entry")
        return cls(
            stage=payload.get("stage"),
            mode=payload.get("mode"),
            object_id=payload.get("object_id"),
            flags=tuple(_items(payload.get("flags", ()), "flags")),
        )


@dataclass(frozen=True)
class WorktreeEntry:
    """Filesystem identity kept separate from HEAD and the Git index."""

    kind: str
    mode: str = ""
    object_id: str = ""
    size: int | None = None
    binary: bool | None = None
    filesystem_id: tuple[int, int] | None = None
    parent_filesystem_id: tuple[int, int] | None = None
    nlink: int | None = None
    link_target_bytes_b64: str = ""
    reparse_tag: str = ""

    def __post_init__(self) -> None:
        kind = _text(self.kind, "kind", limit=32)
        if kind not in _WORKTREE_KINDS:
            raise ValueError(f"unsupported worktree kind: {kind}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "mode", _mode(self.mode, "mode", required=False))
        object.__setattr__(
            self,
            "object_id",
            _object_id(self.object_id, "object_id", required=False),
        )
        if self.size is not None and (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or self.size < 0
        ):
            raise ValueError("size must be a non-negative integer or null")
        if self.binary is not None and not isinstance(self.binary, bool):
            raise ValueError("binary must be true, false, or null")
        object.__setattr__(
            self,
            "filesystem_id",
            _filesystem_id(self.filesystem_id, "filesystem_id"),
        )
        object.__setattr__(
            self,
            "parent_filesystem_id",
            _filesystem_id(self.parent_filesystem_id, "parent_filesystem_id"),
        )
        if self.nlink is not None and (
            isinstance(self.nlink, bool)
            or not isinstance(self.nlink, int)
            or self.nlink < 1
        ):
            raise ValueError("nlink must be a positive integer or null")
        target = _text(
            self.link_target_bytes_b64,
            "link_target_bytes_b64",
            required=False,
            limit=8192,
        )
        if target:
            try:
                base64.b64decode(target, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("link_target_bytes_b64 must be valid base64") from exc
        object.__setattr__(self, "link_target_bytes_b64", target)
        object.__setattr__(
            self,
            "reparse_tag",
            _text(self.reparse_tag, "reparse_tag", required=False, limit=64),
        )
        if kind in {"missing", "unavailable"} and any(
            (self.mode, self.object_id, self.size is not None, target)
        ):
            raise ValueError(f"{kind} worktree entries cannot claim content identity")
        if kind == "file" and not (
            self.mode and self.object_id and self.size is not None
        ):
            raise ValueError("file worktree entries require complete content identity")
        if kind == "symlink" and not (
            self.mode and self.object_id and self.size is not None and target
        ):
            raise ValueError(
                "symlink worktree entries require complete content identity"
            )
        if kind == "gitlink" and not (self.mode == "160000" and self.object_id):
            raise ValueError(
                "gitlink worktree entries require complete content identity"
            )

    @property
    def complete(self) -> bool:
        if self.kind == "missing":
            return True
        if self.kind == "file":
            return bool(self.mode and self.object_id and self.size is not None)
        if self.kind == "symlink":
            return bool(
                self.mode
                and self.object_id
                and self.size is not None
                and self.link_target_bytes_b64
            )
        if self.kind == "gitlink":
            return self.mode == "160000" and bool(self.object_id)
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "mode": self.mode,
            "object_id": self.object_id,
            "size": self.size,
            "binary": self.binary,
            "filesystem_id": list(self.filesystem_id) if self.filesystem_id else None,
            "parent_filesystem_id": (
                list(self.parent_filesystem_id) if self.parent_filesystem_id else None
            ),
            "nlink": self.nlink,
            "link_target_bytes_b64": self.link_target_bytes_b64,
            "reparse_tag": self.reparse_tag,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "WorktreeEntry":
        payload = _mapping(value, "worktree_entry")
        return cls(
            kind=payload.get("kind"),
            mode=payload.get("mode", ""),
            object_id=payload.get("object_id", ""),
            size=payload.get("size"),
            binary=payload.get("binary"),
            filesystem_id=payload.get("filesystem_id"),
            parent_filesystem_id=payload.get("parent_filesystem_id"),
            nlink=payload.get("nlink"),
            link_target_bytes_b64=payload.get("link_target_bytes_b64", ""),
            reparse_tag=payload.get("reparse_tag", ""),
        )


@dataclass(frozen=True)
class PathIdentity:
    """HEAD, index, and filesystem identity for one raw repository path."""

    path: str
    head: GitEntry | None
    index: tuple[IndexEntry, ...]
    worktree: WorktreeEntry
    ignored: bool = False
    probe_error: str = ""
    path_bytes_hex: str = ""
    filesystem_alias: str = ""

    def __post_init__(self) -> None:
        path = _path(self.path)
        object.__setattr__(self, "path", path)
        if self.head is not None and not isinstance(self.head, GitEntry):
            raise ValueError("head must be a GitEntry or null")
        entries = tuple(sorted(self.index, key=lambda entry: entry.stage))
        if any(not isinstance(entry, IndexEntry) for entry in entries):
            raise ValueError("index must contain IndexEntry values")
        if len({entry.stage for entry in entries}) != len(entries):
            raise ValueError("index must not contain duplicate stages")
        object.__setattr__(self, "index", entries)
        if not isinstance(self.worktree, WorktreeEntry):
            raise ValueError("worktree must be a WorktreeEntry")
        if not isinstance(self.ignored, bool):
            raise ValueError("ignored must be a boolean")
        object.__setattr__(
            self,
            "probe_error",
            _text(self.probe_error, "probe_error", required=False, limit=256),
        )
        expected_bytes = _path_bytes_hex(path)
        supplied_bytes = _text(
            self.path_bytes_hex,
            "path_bytes_hex",
            required=False,
            limit=8192,
        ).lower()
        if supplied_bytes and supplied_bytes != expected_bytes:
            raise ValueError("path_bytes_hex does not match path")
        object.__setattr__(self, "path_bytes_hex", expected_bytes)
        alias = _text(
            self.filesystem_alias,
            "filesystem_alias",
            required=False,
            limit=4096,
        )
        object.__setattr__(self, "filesystem_alias", alias)

    @property
    def complete(self) -> bool:
        return not self.probe_error and self.worktree.complete

    @property
    def digest(self) -> str:
        return _content_digest("path-identity", self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "path_bytes_hex": self.path_bytes_hex,
            "filesystem_alias": self.filesystem_alias,
            "head": self.head.to_dict() if self.head else None,
            "index": [entry.to_dict() for entry in self.index],
            "worktree": self.worktree.to_dict(),
            "ignored": self.ignored,
            "probe_error": self.probe_error,
            "complete": self.complete,
        }
        if include_digest:
            payload["identity_digest"] = self.digest
        return payload

    @classmethod
    def from_dict(cls, value: Any) -> "PathIdentity":
        payload = _mapping(value, "path_identity")
        head_raw = payload.get("head")
        identity = cls(
            path=payload.get("path"),
            path_bytes_hex=payload.get("path_bytes_hex", ""),
            filesystem_alias=payload.get("filesystem_alias", ""),
            head=GitEntry.from_dict(head_raw) if head_raw is not None else None,
            index=tuple(
                IndexEntry.from_dict(item)
                for item in _items(payload.get("index", ()), "index")
            ),
            worktree=WorktreeEntry.from_dict(payload.get("worktree")),
            ignored=payload.get("ignored", False),
            probe_error=payload.get("probe_error", ""),
        )
        expected = payload.get("identity_digest")
        if expected is not None and expected != identity.digest:
            raise ValueError("identity_digest does not match path evidence")
        if "complete" in payload and payload.get("complete") is not identity.complete:
            raise ValueError("complete does not match path evidence")
        return identity


@dataclass(frozen=True)
class RepositorySnapshot:
    """One stable repository observation around a controlled operation."""

    repository_id: str
    worktree_id: str
    worktree_root: str
    handle_id: str
    branch: str
    head_sha: str
    index_fingerprint: str
    worktree_fingerprint: str
    captured_at: str
    paths: tuple[PathIdentity, ...] = ()
    probe_status: str = "available"
    unavailable_reason: str = ""
    object_format: str = "sha1"
    head_ref: str = ""
    head_tree: str = ""
    lease_id: str = ""
    owned_worktree: bool = False
    git_semantics: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "repository_id", _text(self.repository_id, "repository_id")
        )
        object.__setattr__(self, "worktree_id", _text(self.worktree_id, "worktree_id"))
        object.__setattr__(
            self,
            "worktree_root",
            _text(self.worktree_root, "worktree_root", limit=4096),
        )
        object.__setattr__(self, "handle_id", _text(self.handle_id, "handle_id"))
        object.__setattr__(
            self,
            "branch",
            _text(self.branch, "branch", required=False, limit=512),
        )
        object.__setattr__(
            self,
            "head_sha",
            _object_id(self.head_sha, "head_sha", required=False),
        )
        status = _text(self.probe_status, "probe_status", limit=32)
        if status not in _PROBE_STATES:
            raise ValueError(f"unsupported probe_status: {status}")
        object.__setattr__(self, "probe_status", status)
        required_fingerprint = status == "available"
        object.__setattr__(
            self,
            "index_fingerprint",
            _fingerprint(
                self.index_fingerprint,
                "index_fingerprint",
                required=required_fingerprint,
            ),
        )
        object.__setattr__(
            self,
            "worktree_fingerprint",
            _fingerprint(
                self.worktree_fingerprint,
                "worktree_fingerprint",
                required=required_fingerprint,
            ),
        )
        object.__setattr__(
            self, "captured_at", _timestamp(self.captured_at, "captured_at")
        )
        reason = _text(
            self.unavailable_reason,
            "unavailable_reason",
            required=False,
            limit=256,
        )
        if status != "available" and not reason:
            raise ValueError(
                "unavailable_reason is required when a probe is unavailable"
            )
        if status == "available" and reason:
            raise ValueError("unavailable_reason requires an unavailable probe")
        object.__setattr__(self, "unavailable_reason", reason)
        paths = tuple(sorted(self.paths, key=lambda item: item.path_bytes_hex))
        if any(not isinstance(item, PathIdentity) for item in paths):
            raise ValueError("paths must contain PathIdentity values")
        if len({item.path_bytes_hex for item in paths}) != len(paths):
            raise ValueError("paths must not contain duplicate raw identities")
        if status == "available" and any(not item.filesystem_alias for item in paths):
            raise ValueError(
                "available path evidence requires a probed filesystem alias"
            )
        aliases = [item.filesystem_alias for item in paths]
        if len(set(aliases)) != len(aliases):
            raise ValueError("paths contain a filesystem alias collision")
        object.__setattr__(self, "paths", paths)
        object_format = _text(self.object_format, "object_format", limit=16)
        if object_format not in {"sha1", "sha256"}:
            raise ValueError("object_format must be sha1 or sha256")
        object.__setattr__(self, "object_format", object_format)
        object.__setattr__(
            self,
            "head_ref",
            _text(self.head_ref, "head_ref", required=False, limit=512),
        )
        object.__setattr__(
            self,
            "head_tree",
            _object_id(self.head_tree, "head_tree", required=False),
        )
        object.__setattr__(
            self,
            "lease_id",
            _text(self.lease_id, "lease_id", required=False),
        )
        if not isinstance(self.owned_worktree, bool):
            raise ValueError("owned_worktree must be a boolean")
        semantics: list[tuple[str, str]] = []
        for item in self.git_semantics:
            pair = _items(item, "git_semantics entry")
            if len(pair) != 2:
                raise ValueError("git_semantics entries must be key/value pairs")
            semantics.append(
                (
                    _text(pair[0], "git_semantics key", limit=128),
                    _text(pair[1], "git_semantics value", required=False, limit=512),
                )
            )
        if len({key for key, _value in semantics}) != len(semantics):
            raise ValueError("git_semantics keys must be unique")
        object.__setattr__(self, "git_semantics", tuple(sorted(semantics)))

    @property
    def complete(self) -> bool:
        return self.probe_status == "available" and all(
            path.complete for path in self.paths
        )

    @property
    def digest(self) -> str:
        return _content_digest(
            "repository-snapshot", self.to_dict(include_identity=False)
        )

    @property
    def snapshot_id(self) -> str:
        return f"snapshot-{self.digest[:24]}"

    def to_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "repository_id": self.repository_id,
            "worktree_id": self.worktree_id,
            "worktree_root": self.worktree_root,
            "handle_id": self.handle_id,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "head_ref": self.head_ref,
            "head_tree": self.head_tree,
            "object_format": self.object_format,
            "index_fingerprint": self.index_fingerprint,
            "worktree_fingerprint": self.worktree_fingerprint,
            "captured_at": self.captured_at,
            "probe_status": self.probe_status,
            "unavailable_reason": self.unavailable_reason,
            "complete": self.complete,
            "lease_id": self.lease_id,
            "owned_worktree": self.owned_worktree,
            "git_semantics": [
                {"key": key, "value": value} for key, value in self.git_semantics
            ],
            "paths": [path.to_dict() for path in self.paths],
        }
        if include_identity:
            payload["snapshot_id"] = self.snapshot_id
            payload["digest"] = self.digest
        return payload

    @classmethod
    def from_dict(cls, value: Any) -> "RepositorySnapshot":
        payload = _mapping(value, "repository_snapshot")
        semantics: list[tuple[str, str]] = []
        for item in _items(payload.get("git_semantics", ()), "git_semantics"):
            pair = _mapping(item, "git_semantics entry")
            semantics.append((pair.get("key"), pair.get("value", "")))
        snapshot = cls(
            repository_id=payload.get("repository_id"),
            worktree_id=payload.get("worktree_id"),
            worktree_root=payload.get("worktree_root"),
            handle_id=payload.get("handle_id"),
            branch=payload.get("branch", ""),
            head_sha=payload.get("head_sha", ""),
            head_ref=payload.get("head_ref", ""),
            head_tree=payload.get("head_tree", ""),
            object_format=payload.get("object_format", "sha1"),
            index_fingerprint=payload.get("index_fingerprint", ""),
            worktree_fingerprint=payload.get("worktree_fingerprint", ""),
            captured_at=payload.get("captured_at"),
            probe_status=payload.get("probe_status", "available"),
            unavailable_reason=payload.get("unavailable_reason", ""),
            lease_id=payload.get("lease_id", ""),
            owned_worktree=payload.get("owned_worktree", False),
            git_semantics=tuple(semantics),
            paths=tuple(
                PathIdentity.from_dict(item)
                for item in _items(payload.get("paths", ()), "paths")
            ),
        )
        expected_id = payload.get("snapshot_id")
        if expected_id is not None and expected_id != snapshot.snapshot_id:
            raise ValueError("snapshot_id does not match repository evidence")
        expected_digest = payload.get("digest")
        if expected_digest is not None and expected_digest != snapshot.digest:
            raise ValueError("digest does not match repository evidence")
        if "complete" in payload and payload.get("complete") is not snapshot.complete:
            raise ValueError("complete does not match repository evidence")
        return snapshot


@dataclass(frozen=True)
class MutationOperation:
    """A durable mutation intent and its observed outcome."""

    operation_id: str
    task_id: str
    run_id: str
    repository_id: str
    worktree_id: str
    kind: str
    paths: tuple[str, ...]
    intent_at: str
    before: tuple[PathIdentity, ...]
    before_snapshot_digest: str
    status: str = "intent"
    observed_at: str = ""
    after: tuple[PathIdentity, ...] = ()
    after_snapshot_digest: str = ""
    error_code: str = ""
    error_detail: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "operation_id",
            "task_id",
            "run_id",
            "repository_id",
            "worktree_id",
        ):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "kind", _text(self.kind, "kind", limit=128))
        paths = _string_tuple(self.paths, "paths", path_values=True)
        if not paths:
            raise ValueError("paths must not be empty")
        object.__setattr__(self, "paths", paths)
        object.__setattr__(self, "intent_at", _timestamp(self.intent_at, "intent_at"))
        before = tuple(sorted(self.before, key=lambda item: item.path_bytes_hex))
        if any(not isinstance(item, PathIdentity) for item in before):
            raise ValueError("before must contain PathIdentity values")
        if {item.path for item in before} != set(paths):
            raise ValueError("before identities must cover every operation path")
        object.__setattr__(self, "before", before)
        object.__setattr__(
            self,
            "before_snapshot_digest",
            _fingerprint(self.before_snapshot_digest, "before_snapshot_digest"),
        )
        status = _text(self.status, "status", limit=32)
        if status not in _OPERATION_STATES:
            raise ValueError(f"unsupported operation status: {status}")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "observed_at",
            _timestamp(self.observed_at, "observed_at", required=status != "intent"),
        )
        after = tuple(sorted(self.after, key=lambda item: item.path_bytes_hex))
        if any(not isinstance(item, PathIdentity) for item in after):
            raise ValueError("after must contain PathIdentity values")
        if len({item.path_bytes_hex for item in after}) != len(after):
            raise ValueError("after must not contain duplicate raw path identities")
        object.__setattr__(self, "after", after)
        object.__setattr__(
            self,
            "after_snapshot_digest",
            _fingerprint(
                self.after_snapshot_digest,
                "after_snapshot_digest",
                required=status == "succeeded",
            ),
        )
        if status == "intent" and (
            after or self.after_snapshot_digest or self.observed_at
        ):
            raise ValueError("an intent cannot contain observed outcome evidence")
        if status == "succeeded" and {item.path for item in after} != set(paths):
            raise ValueError(
                "successful after identities must cover every operation path"
            )
        object.__setattr__(
            self,
            "error_code",
            _text(self.error_code, "error_code", required=False, limit=128),
        )
        object.__setattr__(
            self,
            "error_detail",
            _text(self.error_detail, "error_detail", required=False, limit=512),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "repository_id": self.repository_id,
            "worktree_id": self.worktree_id,
            "kind": self.kind,
            "paths": list(self.paths),
            "intent_at": self.intent_at,
            "before": [item.to_dict() for item in self.before],
            "before_snapshot_digest": self.before_snapshot_digest,
            "status": self.status,
            "observed_at": self.observed_at,
            "after": [item.to_dict() for item in self.after],
            "after_snapshot_digest": self.after_snapshot_digest,
            "error_code": self.error_code,
            "error_detail": self.error_detail,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "MutationOperation":
        payload = _mapping(value, "mutation_operation")
        return cls(
            operation_id=payload.get("operation_id"),
            task_id=payload.get("task_id"),
            run_id=payload.get("run_id"),
            repository_id=payload.get("repository_id"),
            worktree_id=payload.get("worktree_id"),
            kind=payload.get("kind"),
            paths=tuple(_items(payload.get("paths", ()), "paths")),
            intent_at=payload.get("intent_at"),
            before=tuple(
                PathIdentity.from_dict(item)
                for item in _items(payload.get("before", ()), "before")
            ),
            before_snapshot_digest=payload.get("before_snapshot_digest"),
            status=payload.get("status", "intent"),
            observed_at=payload.get("observed_at", ""),
            after=tuple(
                PathIdentity.from_dict(item)
                for item in _items(payload.get("after", ()), "after")
            ),
            after_snapshot_digest=payload.get("after_snapshot_digest", ""),
            error_code=payload.get("error_code", ""),
            error_detail=payload.get("error_detail", ""),
        )


@dataclass(frozen=True)
class PathAttribution:
    """A conservative ownership conclusion with direct evidence references."""

    path: str
    classification: str
    operation_ids: tuple[str, ...]
    before_identity_digest: str
    after_identity_digest: str
    confidence: str
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _path(self.path))
        classification = _text(self.classification, "classification", limit=32)
        if classification not in _CLASSIFICATIONS:
            raise ValueError(f"unsupported classification: {classification}")
        object.__setattr__(self, "classification", classification)
        operation_ids = _string_tuple(self.operation_ids, "operation_ids")
        if classification in {"opai_only", "overlapping"} and not operation_ids:
            raise ValueError(f"{classification} requires a producer operation")
        object.__setattr__(self, "operation_ids", operation_ids)
        for field_name in ("before_identity_digest", "after_identity_digest"):
            value = _fingerprint(
                getattr(self, field_name),
                field_name,
                required=classification != "uncertain",
            )
            object.__setattr__(self, field_name, value)
        confidence = _text(self.confidence, "confidence", limit=16)
        if confidence not in _CONFIDENCE:
            raise ValueError(f"unsupported confidence: {confidence}")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(
            self,
            "reasons",
            tuple(sorted(_string_tuple(self.reasons, "reasons"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "classification": self.classification,
            "operation_ids": list(self.operation_ids),
            "before_identity_digest": self.before_identity_digest,
            "after_identity_digest": self.after_identity_digest,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "PathAttribution":
        payload = _mapping(value, "path_attribution")
        return cls(
            path=payload.get("path"),
            classification=payload.get("classification"),
            operation_ids=tuple(
                _items(payload.get("operation_ids", ()), "operation_ids")
            ),
            before_identity_digest=payload.get("before_identity_digest", ""),
            after_identity_digest=payload.get("after_identity_digest", ""),
            confidence=payload.get("confidence"),
            reasons=tuple(_items(payload.get("reasons", ()), "reasons")),
        )


def _snapshot_path(snapshot: RepositorySnapshot, path: str) -> PathIdentity | None:
    return next((item for item in snapshot.paths if item.path == path), None)


def _operation_path(
    identities: tuple[PathIdentity, ...], path: str
) -> PathIdentity | None:
    return next((item for item in identities if item.path == path), None)


def _has_pre_existing_delta(identity: PathIdentity) -> bool | None:
    """Return whether a path already differed from HEAD at the baseline.

    ``None`` means the evidence is incomplete and therefore cannot authorize
    Vesta-only ownership.
    """

    if not identity.complete:
        return None
    if identity.head is None:
        return bool(identity.index) or identity.worktree.kind != "missing"
    if len(identity.index) != 1 or identity.index[0].stage != 0:
        return True
    indexed = identity.index[0]
    if (indexed.mode, indexed.object_id) != (
        identity.head.mode,
        identity.head.object_id,
    ):
        return True
    expected_kind = {
        "120000": "symlink",
        "160000": "gitlink",
    }.get(identity.head.mode, "file")
    return (
        identity.worktree.kind != expected_kind
        or identity.worktree.mode != identity.head.mode
        or identity.worktree.object_id != identity.head.object_id
    )


def _validate_producer_chain(
    attribution: PathAttribution,
    *,
    baseline: RepositorySnapshot,
    terminal: RepositorySnapshot,
    operations: tuple[MutationOperation, ...],
) -> None:
    """Prove that a claimed owned transition is contiguous and observed."""

    before = _snapshot_path(baseline, attribution.path)
    after = _snapshot_path(terminal, attribution.path)
    if before is None or after is None or not before.complete or not after.complete:
        raise ValueError("owned attribution requires complete path identities")
    if attribution.before_identity_digest != before.digest:
        raise ValueError("attribution before identity does not match baseline")
    if attribution.after_identity_digest != after.digest:
        raise ValueError("attribution after identity does not match terminal snapshot")

    operation_by_id = {item.operation_id: item for item in operations}
    positions = {item.operation_id: index for index, item in enumerate(operations)}
    producer_chain = [operation_by_id[item] for item in attribution.operation_ids]
    if any(item.status != "succeeded" for item in producer_chain):
        raise ValueError("owned attribution requires a successful producer chain")
    if [positions[item.operation_id] for item in producer_chain] != sorted(
        positions[item.operation_id] for item in producer_chain
    ):
        raise ValueError("producer operations must follow journal order")

    current = before
    for operation in producer_chain:
        observed_before = _operation_path(operation.before, attribution.path)
        observed_after = _operation_path(operation.after, attribution.path)
        if observed_before is None or observed_after is None:
            raise ValueError("producer operation is missing path evidence")
        if observed_before.digest != current.digest:
            raise ValueError("producer chain has an unexplained transition")
        current = observed_after
    if current.digest != after.digest:
        raise ValueError("producer chain does not reach the terminal identity")

    pre_existing = _has_pre_existing_delta(before)
    if attribution.classification == "opai_only" and pre_existing is not False:
        raise ValueError("opai_only requires a complete clean baseline identity")
    if attribution.classification == "overlapping" and pre_existing is not True:
        raise ValueError("overlapping requires a pre-existing user delta")


@dataclass(frozen=True)
class ChangeSet:
    """One run's immutable baseline, operations, and terminal attribution."""

    task_id: str
    run_id: str
    repository_id: str
    worktree_id: str
    baseline: RepositorySnapshot
    created_at: str
    operations: tuple[MutationOperation, ...] = ()
    snapshots: tuple[RepositorySnapshot, ...] = ()
    terminal_snapshot: RepositorySnapshot | None = None
    attributions: tuple[PathAttribution, ...] = ()
    status: str = "active"
    terminal_at: str = ""
    integrity_incidents: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != SCHEMA_VERSION
        ):
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        for field_name in ("task_id", "run_id", "repository_id", "worktree_id"):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        if not isinstance(self.baseline, RepositorySnapshot):
            raise ValueError("baseline must be a RepositorySnapshot")
        if self.baseline.repository_id != self.repository_id:
            raise ValueError("baseline repository_id does not match ChangeSet")
        if self.baseline.worktree_id != self.worktree_id:
            raise ValueError("baseline worktree_id does not match ChangeSet")
        object.__setattr__(
            self, "created_at", _timestamp(self.created_at, "created_at")
        )
        operations = tuple(self.operations)
        if any(not isinstance(item, MutationOperation) for item in operations):
            raise ValueError("operations must contain MutationOperation values")
        if len({item.operation_id for item in operations}) != len(operations):
            raise ValueError(
                "operations must not contain duplicate operation_id values"
            )
        for operation in operations:
            for field_name in (
                "task_id",
                "run_id",
                "repository_id",
                "worktree_id",
            ):
                if getattr(operation, field_name) != getattr(self, field_name):
                    raise ValueError(
                        f"operation {field_name} does not match ChangeSet {field_name}"
                    )
        object.__setattr__(self, "operations", operations)
        if self.terminal_snapshot is not None:
            if not isinstance(self.terminal_snapshot, RepositorySnapshot):
                raise ValueError(
                    "terminal_snapshot must be a RepositorySnapshot or null"
                )
            if self.terminal_snapshot.repository_id != self.repository_id:
                raise ValueError(
                    "terminal_snapshot repository_id does not match ChangeSet"
                )
            if self.terminal_snapshot.worktree_id != self.worktree_id:
                raise ValueError(
                    "terminal_snapshot worktree_id does not match ChangeSet"
                )
        snapshots = tuple(sorted(self.snapshots, key=lambda item: item.digest))
        if any(not isinstance(item, RepositorySnapshot) for item in snapshots):
            raise ValueError("snapshots must contain RepositorySnapshot values")
        known_snapshots: dict[str, RepositorySnapshot] = {
            self.baseline.digest: self.baseline
        }
        if self.terminal_snapshot is not None:
            known_snapshots[self.terminal_snapshot.digest] = self.terminal_snapshot
        for snapshot in snapshots:
            if snapshot.repository_id != self.repository_id:
                raise ValueError("snapshot repository_id does not match ChangeSet")
            if snapshot.worktree_id != self.worktree_id:
                raise ValueError("snapshot worktree_id does not match ChangeSet")
            if snapshot.digest in known_snapshots:
                raise ValueError(
                    "snapshots must not duplicate baseline or terminal evidence"
                )
            known_snapshots[snapshot.digest] = snapshot
        object.__setattr__(self, "snapshots", snapshots)
        for operation in operations:
            before_snapshot = known_snapshots.get(operation.before_snapshot_digest)
            if before_snapshot is None:
                raise ValueError("operation references an unknown before snapshot")
            for identity in operation.before:
                observed = _snapshot_path(before_snapshot, identity.path)
                if observed is None or observed.digest != identity.digest:
                    raise ValueError(
                        "operation before identity does not match referenced snapshot"
                    )
            if operation.after and not operation.after_snapshot_digest:
                raise ValueError("operation after evidence requires a snapshot digest")
            if operation.after_snapshot_digest:
                after_snapshot = known_snapshots.get(operation.after_snapshot_digest)
                if after_snapshot is None:
                    raise ValueError("operation references an unknown after snapshot")
                for identity in operation.after:
                    observed = _snapshot_path(after_snapshot, identity.path)
                    if observed is None or observed.digest != identity.digest:
                        raise ValueError(
                            "operation after identity does not match "
                            "referenced snapshot"
                        )
        attributions = tuple(
            sorted(self.attributions, key=lambda item: _path_bytes_hex(item.path))
        )
        if any(not isinstance(item, PathAttribution) for item in attributions):
            raise ValueError("attributions must contain PathAttribution values")
        if len({item.path for item in attributions}) != len(attributions):
            raise ValueError("attributions must not contain duplicate paths")
        operation_ids = {item.operation_id for item in operations}
        known_paths = {item.path for item in self.baseline.paths}
        if self.terminal_snapshot is not None:
            known_paths.update(item.path for item in self.terminal_snapshot.paths)
        for attribution in attributions:
            unknown_operations = set(attribution.operation_ids) - operation_ids
            if unknown_operations:
                raise ValueError("attribution references an unknown operation_id")
            if attribution.path not in known_paths:
                raise ValueError("attribution path is absent from repository snapshots")
            if attribution.classification in {"opai_only", "overlapping"}:
                if self.terminal_snapshot is None:
                    raise ValueError("owned attribution requires a terminal snapshot")
                _validate_producer_chain(
                    attribution,
                    baseline=self.baseline,
                    terminal=self.terminal_snapshot,
                    operations=operations,
                )
        object.__setattr__(self, "attributions", attributions)
        status = _text(self.status, "status", limit=32)
        if status not in _CHANGE_SET_STATES:
            raise ValueError(f"unsupported ChangeSet status: {status}")
        object.__setattr__(self, "status", status)
        terminal_at = _timestamp(
            self.terminal_at,
            "terminal_at",
            required=status != "active",
        )
        if status != "active" and self.terminal_snapshot is None:
            raise ValueError("terminal_snapshot is required for a terminal ChangeSet")
        if status != "active" and any(
            operation.status == "intent" for operation in operations
        ):
            raise ValueError(
                "terminal ChangeSet contains an unresolved mutation intent"
            )
        if status == "completed" and any(
            operation.status in {"cancelled", "uncertain"} for operation in operations
        ):
            raise ValueError(
                "completed ChangeSet contains an unresolved mutation operation"
            )
        if status == "completed" and (
            not self.baseline.complete
            or self.terminal_snapshot is None
            or not self.terminal_snapshot.complete
        ):
            raise ValueError(
                "completed ChangeSet requires complete repository snapshots"
            )
        if status == "active" and (
            self.terminal_snapshot is not None or terminal_at or attributions
        ):
            raise ValueError("active ChangeSet cannot contain terminal evidence")
        object.__setattr__(self, "terminal_at", terminal_at)
        incidents = tuple(
            sorted(_string_tuple(self.integrity_incidents, "integrity_incidents"))
        )
        if status == "completed" and incidents:
            raise ValueError("completed ChangeSet cannot contain integrity incidents")
        object.__setattr__(
            self,
            "integrity_incidents",
            incidents,
        )

    @property
    def digest(self) -> str:
        return _content_digest("change-set", self.to_dict(include_identity=False))

    @property
    def change_set_id(self) -> str:
        return f"changeset-{self.digest[:24]}"

    @property
    def attributed_paths(self) -> tuple[str, ...]:
        return tuple(
            item.path
            for item in self.attributions
            if item.classification == "opai_only"
        )

    def to_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "repository_id": self.repository_id,
            "worktree_id": self.worktree_id,
            "created_at": self.created_at,
            "status": self.status,
            "terminal_at": self.terminal_at,
            "baseline": self.baseline.to_dict(),
            "operations": [operation.to_dict() for operation in self.operations],
            "snapshots": [snapshot.to_dict() for snapshot in self.snapshots],
            "terminal_snapshot": (
                self.terminal_snapshot.to_dict() if self.terminal_snapshot else None
            ),
            "attributions": [item.to_dict() for item in self.attributions],
            "attributed_paths": list(self.attributed_paths),
            "integrity_incidents": list(self.integrity_incidents),
        }
        if include_identity:
            payload["change_set_id"] = self.change_set_id
            payload["digest"] = self.digest
        return payload

    @classmethod
    def from_dict(cls, value: Any) -> "ChangeSet":
        payload = _mapping(value, "change_set")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported ChangeSet schema_version: {payload.get('schema_version')}"
            )
        terminal_raw = payload.get("terminal_snapshot")
        change_set = cls(
            schema_version=payload.get("schema_version"),
            task_id=payload.get("task_id"),
            run_id=payload.get("run_id"),
            repository_id=payload.get("repository_id"),
            worktree_id=payload.get("worktree_id"),
            created_at=payload.get("created_at"),
            status=payload.get("status", "active"),
            terminal_at=payload.get("terminal_at", ""),
            baseline=RepositorySnapshot.from_dict(payload.get("baseline")),
            operations=tuple(
                MutationOperation.from_dict(item)
                for item in _items(payload.get("operations", ()), "operations")
            ),
            snapshots=tuple(
                RepositorySnapshot.from_dict(item)
                for item in _items(payload.get("snapshots", ()), "snapshots")
            ),
            terminal_snapshot=(
                RepositorySnapshot.from_dict(terminal_raw)
                if terminal_raw is not None
                else None
            ),
            attributions=tuple(
                PathAttribution.from_dict(item)
                for item in _items(payload.get("attributions", ()), "attributions")
            ),
            integrity_incidents=tuple(
                _items(payload.get("integrity_incidents", ()), "integrity_incidents")
            ),
        )
        expected_id = payload.get("change_set_id")
        if expected_id is not None and expected_id != change_set.change_set_id:
            raise ValueError("change_set_id does not match mutation evidence")
        expected_digest = payload.get("digest")
        if expected_digest is not None and expected_digest != change_set.digest:
            raise ValueError("digest does not match mutation evidence")
        if (
            "attributed_paths" in payload
            and tuple(payload.get("attributed_paths") or ())
            != change_set.attributed_paths
        ):
            raise ValueError("attributed_paths does not match canonical attribution")
        return change_set


__all__ = [
    "SCHEMA_VERSION",
    "ChangeSet",
    "GitEntry",
    "IndexEntry",
    "MutationOperation",
    "PathAttribution",
    "PathIdentity",
    "RepositorySnapshot",
    "WorktreeEntry",
]
