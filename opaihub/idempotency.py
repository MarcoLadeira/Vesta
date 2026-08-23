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
import time
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from . import shadow_journal
from .atomic_io import atomic_write_text, interprocess_transaction
from .state import state_dir

FRESH = "fresh"
IN_FLIGHT = "in_flight"
DONE = "done"

# After this long an unresolved operation is explicitly stale and requires
# reconciliation. Time alone never proves that an external effect did not
# happen, so expiry cannot make the operation fresh again (#616).
UNCERTAIN_TTL_SECONDS = 24 * 3600.0

# Bound the store without deleting exact-once history. At capacity, new claims
# fail closed until confirmed history is deliberately archived by a future
# reconciliation/retention policy.
MAX_RECORDS = 512


class OperationPersistenceError(OSError):
    """The exact-once claim store could not preserve a trustworthy state."""


def operation_key(kind: str, **parts: Any) -> str:
    """A stable key for one logical operation.

    Built only from the parts that make two calls the same request. Long or
    user-authored values (a PR body, a comment) are hashed rather than stored,
    so the key is stable without the store holding their content.
    """
    clean_kind = "".join(
        ch for ch in str(kind or "").lower() if ch.isalnum() or ch in "._-"
    )[:48]
    material = {str(name): _normalize(value) for name, value in sorted(parts.items())}
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
    path = _path(project_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise OperationPersistenceError("operation claim store is unreadable") from exc
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise OperationPersistenceError("operation claim store is corrupt") from exc
    if not isinstance(data, dict):
        raise OperationPersistenceError("operation claim store has an invalid schema")
    return data


def _valid_operation_store(record: Mapping[str, Any]) -> bool:
    """A mirrored store must carry the operation map a reader needs.

    An empty map is valid, deliberately: abandoning the last in-flight claim
    is a state change worth recording. Requiring a non-empty map would drop it
    and leave the shadow asserting claims that no longer exist -- the same
    trap already met in checkpoints (`pending`), agent runtime (`idle`) and
    scheduler (an empty list).
    """

    return isinstance(record.get("operations"), dict)


def _save(project_root: Path, store: dict[str, Any]) -> None:
    if len(store) > MAX_RECORDS:
        raise OperationPersistenceError(
            "operation claim store is full and requires reconciliation"
        )
    path = _path(project_root)
    try:
        atomic_write_text(path, json.dumps(store, sort_keys=True, indent=2) + "\n")
    except OSError as exc:
        raise OperationPersistenceError("operation claim store is unwritable") from exc
    # #613 Stage 2: mirror the canonical event. Every caller (begin, complete,
    # abandon) already holds interprocess_transaction(_path(...)), so the
    # journal observes writes in exactly the order the file took them.
    #
    # This file is one document keyed by operation rather than one file per
    # record, so the journalled "record" is the whole store, carried under a
    # single key because the helper mirrors mappings.
    shadow_journal.record_snapshot(
        path, {"operations": store}, is_valid_record=_valid_operation_store
    )


def shadow_journal_projection(project_root: Path) -> dict[str, Any]:
    """The operation store the shadow journal alone would reconstruct."""

    return shadow_journal.projection(
        _path(project_root), is_valid_record=_valid_operation_store
    )


def operation_contradiction_report(project_root: Path) -> dict[str, Any] | None:
    """``None`` when the file and its shadow agree; otherwise what differs.

    The dual-read #613 asks for. ``_load`` is deliberately not reused: it
    raises OperationPersistenceError on a corrupt or invalid-schema store,
    which is right for its callers -- an idempotency claim must fail closed --
    but would raise past the very divergence this exists to report.
    """

    path = _path(project_root)

    def read_legacy() -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {"operations": data} if isinstance(data, dict) else {}

    return shadow_journal.contradiction_report(
        path, read_legacy, is_valid_record=_valid_operation_store
    )


def _blocked(key: str, exc: BaseException) -> dict[str, Any]:
    return {
        "state": IN_FLIGHT,
        "result": {},
        "key": str(key),
        "persistence_blocked": True,
        "requires_reconciliation": True,
        "reason": str(exc)[:200],
    }


def _status_from_store(
    store: dict[str, Any], key: str, *, now: float | None = None
) -> dict[str, Any]:
    stable_key = str(key)
    if stable_key not in store:
        return {"state": FRESH, "result": {}, "key": stable_key}
    entry = store.get(stable_key)
    if not isinstance(entry, dict):
        raise OperationPersistenceError("operation claim record has an invalid schema")
    state = str(entry.get("state") or "")
    stamp = time.time() if now is None else float(now)
    try:
        at = float(entry["at"])
    except (TypeError, ValueError):
        raise OperationPersistenceError(
            "operation claim record has an invalid timestamp"
        ) from None
    except KeyError:
        raise OperationPersistenceError(
            "operation claim record has no timestamp"
        ) from None
    if state not in {IN_FLIGHT, DONE}:
        raise OperationPersistenceError("operation claim record has an invalid state")
    result = entry.get("result")
    if not isinstance(result, dict):
        raise OperationPersistenceError("operation claim result has an invalid schema")
    outcome = {
        "state": state,
        "result": result,
        "key": stable_key,
        "at": at,
    }
    if state == IN_FLIGHT and (stamp - at) > UNCERTAIN_TTL_SECONDS:
        outcome["expired"] = True
        outcome["requires_reconciliation"] = True
    return outcome


def status(project_root: Path, key: str, *, now: float | None = None) -> dict[str, Any]:
    """What is known about ``key`` without changing anything."""
    try:
        with interprocess_transaction(_path(project_root)):
            return _status_from_store(_load(project_root), str(key), now=now)
    except (OSError, ValueError) as exc:
        return _blocked(str(key), exc)


def begin(project_root: Path, key: str, *, now: float | None = None) -> dict[str, Any]:
    """Claim ``key`` before performing its side effect.

    Returns the status *before* the claim, so the caller can branch on it:
    ``fresh`` means go ahead, ``in_flight`` means it may already exist and must
    not be repeated, ``done`` means use the recorded result.

    Only a ``fresh`` key is claimed — this never overwrites an existing record,
    because that would erase the very uncertainty it exists to preserve.
    """
    try:
        with interprocess_transaction(_path(project_root)):
            store = _load(project_root)
            current = _status_from_store(store, str(key), now=now)
            if current["state"] != FRESH:
                return current
            stamp = time.time() if now is None else float(now)
            store[str(key)] = {"state": IN_FLIGHT, "at": stamp, "result": {}}
            _save(project_root, store)
            return current
    except (OSError, ValueError) as exc:
        return _blocked(str(key), exc)


def complete(
    project_root: Path,
    key: str,
    result: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> None:
    """Confirm ``key`` succeeded, recording a small result summary."""
    stamp = time.time() if now is None else float(now)
    with interprocess_transaction(_path(project_root)):
        store = _load(project_root)
        store[str(key)] = {
            "state": DONE,
            "at": stamp,
            "result": _clean_result(result),
        }
        _save(project_root, store)


def abandon(project_root: Path, key: str) -> None:
    """Release ``key`` after an attempt that provably did not take effect.

    Only for failures where the side effect certainly did not happen — a
    validation error, a refused approval, a request that never left the
    machine. A network timeout is **not** one of those: the request may have
    arrived, so its key must stay ``in_flight``.
    """
    with interprocess_transaction(_path(project_root)):
        store = _load(project_root)
        if str(key) in store:
            store.pop(str(key), None)
            _save(project_root, store)


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
