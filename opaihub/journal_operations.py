"""#613 Stage 6: external effects recorded as operation transactions.

Stage 6 asks that "provider/tool/Git/GitHub/cost/approval actions use operation
transactions". Those actions already share one choke point --
:mod:`opaihub.idempotency`, which every exact-once external effect goes through
to claim a key before acting. Mirroring there covers all of them at once, and
covers the ones nobody has written yet, which call-site-by-call-site wiring
would not.

The mapping from idempotency's vocabulary to the journal's is the substance of
this module, and one row of it matters more than the rest.

===================  ==================  ====================================
idempotency          journal state       why
===================  ==================  ====================================
``begin`` (fresh)    ``executing``       claimed, about to touch the outside
``complete``         ``reconciled``      confirmed, with the effect's own ref
``abandon``          *row removed*       provably never happened
timeout / crash      ``executing``       **left alone** -- see below
===================  ==================  ====================================

An operation that was claimed and never confirmed stays ``executing`` forever
until something reconciles it. That is deliberate and it is the whole point:
``abandon`` is documented as being only for failures where the side effect
*certainly* did not happen. A network timeout is not one of those -- the request
may have arrived -- so its record must keep saying "we do not know". Downgrading
it to ``uncertain`` on a timer would be inventing a conclusion, and clearing it
would be worse: the next retry would fire a second real effect.

Best-effort, like every other Stage 3-6 mirror. The idempotency file is still
authoritative for exact-once until Stage 7 retires it; a journal write that
fails costs evidence, not correctness, and must never fail the effect it
describes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import journal_store
from .journal_runtime import _store
from .journal_store import JournalStoreError

#: What an operation row means in the journal, mapped from idempotency's
#: three-state vocabulary. Kept here rather than in idempotency so that module
#: keeps no knowledge of the journal at all -- the dependency points one way.
STATE_CLAIMED = "executing"
STATE_CONFIRMED = "reconciled"
STATE_UNCERTAIN = "uncertain"


def _kind_of(key: str) -> str:
    """The operation kind encoded in an idempotency key.

    ``operation_key`` builds keys as ``"<kind>:<digest>"``, so the kind is
    recoverable without the caller passing it again -- which matters because
    the mirror hooks a choke point that does not know what it is mirroring.
    """

    text = str(key or "")
    return text.split(":", 1)[0] if ":" in text else "operation"


def _digest_of(key: str) -> str:
    text = str(key or "")
    return text.split(":", 1)[1] if ":" in text else text


def record_claim(
    project_root: Path,
    key: str,
    *,
    now: str,
    run_id: str | None = None,
    process_ref: str = "",
) -> bool:
    """Mirror an idempotency claim as an operation about to take effect.

    Returns whether the journal row was created. A duplicate claim is a no-op
    rather than an error: the operation key *is* the identity, so claiming it
    twice describes one operation, which is exactly the guarantee being
    mirrored.
    """

    with _store(project_root) as store:
        if store is None:
            return False
        try:
            journal_store.record_operation(
                store,
                operation_key=str(key),
                kind=_kind_of(key),
                target_digest=_digest_of(key),
                state=STATE_CLAIMED,
                now=now,
                run_id=run_id,
                process_ref=process_ref,
            )
            return True
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
            return False


def record_confirmation(
    project_root: Path,
    key: str,
    *,
    now: str,
    external_ref: str = "",
    run_id: str | None = None,
) -> bool:
    """Mirror a completed effect, carrying whatever names it on the far side.

    ``external_ref`` is the PR number, commit sha or provider request id. It is
    what makes an operation *reconcilable* later: without it, a claimed
    operation and a completed one are distinguishable only by our own say-so,
    and #613's whole complaint is about state that cannot be checked against
    the world.
    """

    with _store(project_root) as store:
        if store is None:
            return False
        try:
            journal_store.record_operation(
                store,
                operation_key=str(key),
                kind=_kind_of(key),
                target_digest=_digest_of(key),
                state=STATE_CONFIRMED,
                now=now,
                run_id=run_id,
                external_ref=str(external_ref or "")[:256],
            )
            return True
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
            return False


def record_release(project_root: Path, key: str, *, now: str) -> bool:
    """Mirror an abandonment: the effect provably never happened.

    The row is deleted rather than marked, because idempotency's contract is
    that an abandoned key becomes *fresh* again -- a later retry is a genuine
    first attempt, and a lingering row claiming otherwise would make the
    journal disagree with the thing it mirrors.

    Only ever reached from ``abandon``, which is documented as being for
    failures where the side effect certainly did not occur.
    """

    with _store(project_root) as store:
        if store is None:
            return False
        try:
            with journal_store._transaction(store):
                store.execute(
                    "DELETE FROM operations WHERE operation_key = ?", (str(key),)
                )
            return True
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
            return False


def unreconciled_operations(
    project_root: Path, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Operations claimed but never confirmed -- the ones that need answering.

    This is the list a reconciliation pass works from, and the reason the
    mirror refuses to guess at timeouts. Every row here is a real question:
    did this effect happen? Answering it needs the far side, not a timer.
    """

    if not journal_store.journal_path(project_root).exists():
        return []
    with _store(project_root) as store:
        if store is None:
            return []
        try:
            rows = store.execute(
                "SELECT operation_key, kind, run_id, external_ref, attempts,"
                " created_at, updated_at FROM operations"
                " WHERE state = ? ORDER BY created_at LIMIT ?",
                (STATE_CLAIMED, int(limit)),
            ).fetchall()
            return [dict(row) for row in rows]
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
            return []


def operation_summary(project_root: Path) -> dict[str, Any]:
    """Counts by state, for doctor and for Stage 7's retirement telemetry.

    Reports an absent journal rather than creating one. ``open_store`` creates
    on demand, which is right for a writer and wrong here: a *reporting* call
    that silently brings a database into existence makes "does this project use
    the journal yet?" unanswerable, and Stage 7's retirement decision rests on
    exactly that question.
    """

    if not journal_store.journal_path(project_root).exists():
        return {"available": False, "states": {}, "unreconciled": 0}
    with _store(project_root) as store:
        if store is None:
            return {"available": False, "states": {}}
        try:
            rows = store.execute(
                "SELECT state, COUNT(*) AS n FROM operations GROUP BY state"
            ).fetchall()
            states = {str(row["state"]): int(row["n"]) for row in rows}
            return {
                "available": True,
                "states": states,
                "unreconciled": states.get(STATE_CLAIMED, 0),
            }
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
            return {"available": False, "states": {}}


def mirror_from_status(
    project_root: Path,
    key: str,
    status: Mapping[str, Any],
    *,
    now: str,
    run_id: str | None = None,
) -> bool:
    """Mirror whichever transition an idempotency status implies.

    Exists so the hook in :mod:`opaihub.idempotency` stays one line and holds
    no mapping logic of its own -- the vocabulary translation lives here, in
    the module that owns it.
    """

    state = str(status.get("state") or "")
    if state == "fresh":
        return record_claim(project_root, key, now=now, run_id=run_id)
    return False


__all__: Sequence[str] = (
    "STATE_CLAIMED",
    "STATE_CONFIRMED",
    "STATE_UNCERTAIN",
    "mirror_from_status",
    "operation_summary",
    "record_claim",
    "record_confirmation",
    "record_release",
    "unreconciled_operations",
)
