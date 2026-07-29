"""Operation keys so a retry cannot repeat an outward side effect (#295 gate 4).

Gate 4: *"Duplicate side effect: 0 after retry, replay, reconnect or failover."*
Nothing enforced it. `create_pull_request` POSTs straight to GitHub, and
`comment_pr` straight to the issue thread — so a turn that was retried, resumed
after a crash, or re-sent after a reconnect opened a second pull request or
posted the same comment twice. Those are outward, visible to other people, and
not undoable by OPai.

**The three-state model, and why two is not enough.** The tempting design is a
set of completed keys: if the key is present, skip. That is wrong at exactly the
moment it matters — the process can die *between* performing the side effect and
recording it. A two-state store has no way to represent "this may or may not have
happened", so it must guess, and either guess is a real failure: redo and you
duplicate, skip and you silently drop work the user asked for.

So an operation passes through three states:

``fresh``
    Never attempted. Safe to perform.

``in_flight``
    Started and not confirmed. The side effect **may already exist**. A later
    attempt does not redo it and does not pretend it succeeded — it reports the
    uncertainty, which is what #295's failure taxonomy means by *"whether side
    effects may already exist"* and where `needs_attention` belongs.

``done``
    Confirmed, with the recorded result. A repeat returns that result rather
    than performing anything.

**Keys identify the operation, not the attempt.** A key is derived from what
makes two calls *the same request* — repository, branch, title — and never from
a timestamp, a random id or an attempt counter, because those would make every
retry look new, which is the bug.

The store is local JSON under ``.opaihub/health``. It records key hashes,
closed-vocabulary states, timestamps and small result summaries — never prompts,
tokens or full bodies, since the material being keyed (a PR body, a comment) can
contain anything the user wrote.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .state import state_dir

FRESH = "fresh"
IN_FLIGHT = "in_flight"
DONE = "done"

# An operation that started and never confirmed stays uncertain for this long.
# Past it the record is treated as abandoned rather than uncertain forever: a
# process that died mid-PUSH days ago should not block the user permanently.
# Deliberately generous — an outward side effect wrongly repeated is worse than
# one the user has to confirm by hand.
UNCERTAIN_TTL_SECONDS = 24 * 3600.0

# Bound the store so a long-lived workspace cannot grow it without limit.
MAX_RECORDS = 512


def operation_key(kind: str, **parts: Any) -> str:
    """A stable key for one logical operation.

    Built only from the parts that make two calls the same request. Long or
    user-authored values (a PR body, a comment) are hashed rather than stored,
    so the key is stable without the store holding their content.
    """
    clean_kind = "".join(
        ch for ch in str(kind or "").lower() if ch.isalnum() or ch in "._-"
    )[:48]
    material = {
        str(name): _normalize(value) for name, value in sorted(parts.items())
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return f"{clean_kind or 'operation'}:{digest}"


def _normalize(value: Any) -> str:
    text = "" if value is None else str(value)
    # Whitespace-only differences do not make a different operation; a retry
    # that re-renders the same body with a trailing newline is the same request.
    text = " ".join(text.split())
    if len(text) > 96:
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return text


def _path(project_root: Path) -> Path:
    return state_dir(project_root) / "health" / "operations.json"


def _load(project_root: Path) -> dict[str, Any]:
    try:
        data = json.loads(_path(project_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(project_root: Path, store: dict[str, Any]) -> None:
    if len(store) > MAX_RECORDS:
        # Drop the oldest by timestamp. Confirmed records are the ones safe to
        # forget first: re-running a `done` operation is at worst wasted work,
        # while forgetting an `in_flight` one loses a real uncertainty.
        ordered = sorted(
            store.items(),
            key=lambda item: (
                0 if str(item[1].get("state")) == IN_FLIGHT else 1,
                -float(item[1].get("at") or 0.0),
            ),
        )
        store = dict(ordered[:MAX_RECORDS])
    path = _path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(store, sort_keys=True, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def status(
    project_root: Path, key: str, *, now: float | None = None
) -> dict[str, Any]:
    """What is known about ``key`` without changing anything."""
    entry = _load(project_root).get(str(key))
    if not isinstance(entry, dict):
        return {"state": FRESH, "result": {}, "key": str(key)}
    state = str(entry.get("state") or "")
    stamp = time.time() if now is None else float(now)
    try:
        at = float(entry.get("at") or 0.0)
    except (TypeError, ValueError):
        at = 0.0
    if state == IN_FLIGHT and (stamp - at) > UNCERTAIN_TTL_SECONDS:
        # Old enough that holding the user hostage to it helps nobody.
        return {"state": FRESH, "result": {}, "key": str(key), "expired": True}
    if state not in {IN_FLIGHT, DONE}:
        return {"state": FRESH, "result": {}, "key": str(key)}
    result = entry.get("result")
    return {
        "state": state,
        "result": result if isinstance(result, dict) else {},
        "key": str(key),
        "at": at,
    }


def begin(project_root: Path, key: str, *, now: float | None = None) -> dict[str, Any]:
    """Claim ``key`` before performing its side effect.

    Returns the status *before* the claim, so the caller can branch on it:
    ``fresh`` means go ahead, ``in_flight`` means it may already exist and must
    not be repeated, ``done`` means use the recorded result.

    Only a ``fresh`` key is claimed — this never overwrites an existing record,
    because that would erase the very uncertainty it exists to preserve.
    """
    current = status(project_root, key, now=now)
    if current["state"] != FRESH:
        return current
    stamp = time.time() if now is None else float(now)
    try:
        store = _load(project_root)
        store[str(key)] = {"state": IN_FLIGHT, "at": stamp, "result": {}}
        _save(project_root, store)
    except OSError:
        # An unwritable store must not block the user's work. The cost is that
        # this one operation loses its duplicate protection, which is the same
        # position OPai was in before this module existed — never worse.
        return {"state": FRESH, "result": {}, "key": str(key), "unrecorded": True}
    return current


def complete(
    project_root: Path,
    key: str,
    result: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> None:
    """Confirm ``key`` succeeded, recording a small result summary."""
    stamp = time.time() if now is None else float(now)
    try:
        store = _load(project_root)
        store[str(key)] = {
            "state": DONE,
            "at": stamp,
            "result": _clean_result(result),
        }
        _save(project_root, store)
    except OSError:
        return


def abandon(project_root: Path, key: str) -> None:
    """Release ``key`` after an attempt that provably did not take effect.

    Only for failures where the side effect certainly did not happen — a
    validation error, a refused approval, a request that never left the
    machine. A network timeout is **not** one of those: the request may have
    arrived, so its key must stay ``in_flight``.
    """
    try:
        store = _load(project_root)
        if str(key) in store:
            store.pop(str(key), None)
            _save(project_root, store)
    except OSError:
        return


def _clean_result(result: dict[str, Any] | None) -> dict[str, Any]:
    """Keep a small, flat summary — never a body, prompt or token."""
    if not isinstance(result, dict):
        return {}
    out: dict[str, Any] = {}
    for name, value in sorted(result.items())[:8]:
        key = "".join(ch for ch in str(name) if ch.isalnum() or ch in "._-")[:32]
        if not key:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            out[key] = value
        else:
            out[key] = str(value)[:200]
    return out
