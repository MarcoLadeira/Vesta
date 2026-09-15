"""Bounded, expiring local result cache for Vesta answers.

Answers are eligible for reuse only when the shared repository fingerprint can
prove the relevant Git state is complete. Cache entries are local, schema
versioned, expire after a documented lifetime, and are written atomically.
Neither raw prompts nor file contents are persisted as cache metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .evidence_cache import RepoFingerprint, assess_repo_fingerprint
from .state import state_dir


RESULT_CACHE_VERSION = 2
DEFAULT_TTL_SECONDS = 3600


@dataclass(frozen=True)
class CacheLookup:
    """A privacy-safe cache outcome for callers that need evidence metadata."""

    entry: dict[str, Any] | None
    outcome: str
    key: str
    age_seconds: int | None = None
    reason: str | None = None


def normalize_task(task: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation for near-dup matching."""
    lowered = (task or "").lower().strip()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return " ".join(lowered.split())


def task_hash(task: str, model: str) -> str:
    """Return a task-only one-way identifier suitable for result metadata."""
    basis = f"{normalize_task(task)}\n{model}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def cache_dir(project_root: Path) -> Path:
    return state_dir(project_root) / "answers"


def _key(task: str, model: str, assessment: RepoFingerprint) -> str:
    basis = (
        f"v{RESULT_CACHE_VERSION}\n{normalize_task(task)}\n{model}\n{assessment.digest}"
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def cache_key(project_root: Path, task: str, model: str) -> str:
    """Return a compatibility cache key; callers should inspect lookup metadata."""
    root = project_root.expanduser().resolve()
    return _key(task, model, assess_repo_fingerprint(root))


def _utc_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("cache timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def lookup_with_meta(
    project_root: Path,
    task: str,
    model: str,
    *,
    now: datetime | None = None,
) -> CacheLookup:
    """Read one valid, unexpired cache entry or return a safe outcome reason."""
    root = project_root.expanduser().resolve()
    assessment = assess_repo_fingerprint(root)
    key = _key(task, model, assessment)
    if not assessment.cacheable:
        return CacheLookup(
            entry=None,
            outcome="bypass",
            key=key,
            reason=assessment.bypass_reason,
        )

    path = cache_dir(root) / f"{key}.json"
    if not path.exists():
        return CacheLookup(entry=None, outcome="miss", key=key)
    data: Any | None = None
    for attempt in range(3):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return CacheLookup(entry=None, outcome="miss", key=key)
        except (OSError, json.JSONDecodeError):
            if attempt == 2:
                return CacheLookup(entry=None, outcome="corrupt", key=key)
            time.sleep(0.001)
    if not isinstance(data, dict):
        return CacheLookup(entry=None, outcome="corrupt", key=key)
    if data.get("schema_version") != RESULT_CACHE_VERSION:
        return CacheLookup(entry=None, outcome="schema_mismatch", key=key)
    if data.get("key") != key or data.get("model") != model:
        return CacheLookup(entry=None, outcome="corrupt", key=key)
    if not isinstance(data.get("answer"), str):
        return CacheLookup(entry=None, outcome="corrupt", key=key)

    created_at = _parse_timestamp(data.get("created_at"))
    expires_at = _parse_timestamp(data.get("expires_at"))
    current = _utc_now(now)
    if created_at is None or expires_at is None or expires_at < created_at:
        return CacheLookup(entry=None, outcome="corrupt", key=key)
    if current >= expires_at:
        return CacheLookup(
            entry=None,
            outcome="expired",
            key=key,
            age_seconds=max(0, int((current - created_at).total_seconds())),
        )
    if current < created_at:
        return CacheLookup(entry=None, outcome="corrupt", key=key)
    return CacheLookup(
        entry=data,
        outcome="hit",
        key=key,
        age_seconds=int((current - created_at).total_seconds()),
    )


def lookup(project_root: Path, task: str, model: str) -> dict[str, Any] | None:
    """Compatibility wrapper returning an entry only for a valid cache hit."""
    return lookup_with_meta(project_root, task, model).entry


def _atomic_write(path: Path, payload: dict[str, Any]) -> bool:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def store(
    project_root: Path,
    task: str,
    model: str,
    answer: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
    expected_key: str | None = None,
) -> Path | None:
    """Store one answer atomically when the repository state is cacheable.

    ``expected_key`` lets a caller prove that the repository state did not
    change between its cache lookup and this write. A changed state is never
    populated with an answer produced for an earlier state.
    """
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    root = project_root.expanduser().resolve()
    assessment = assess_repo_fingerprint(root)
    if not assessment.cacheable:
        return None
    created_at = _utc_now(now)
    expires_at = created_at + timedelta(seconds=ttl_seconds)
    key = _key(task, model, assessment)
    if expected_key is not None and key != expected_key:
        return None
    path = cache_dir(root) / f"{key}.json"
    payload = {
        "schema_version": RESULT_CACHE_VERSION,
        "key": key,
        "model": model,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "answer": answer,
    }
    return path if _atomic_write(path, payload) else None
