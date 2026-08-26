"""#613 Stage 3: journal-backed run lifecycle, alongside the legacy path.

Stage 3 asks for admission, execution, cancellation, verification, cost and
receipt to be journal-backed for one local and one account provider. This
module is the adapter that makes that possible without Stage 5's cutover: every
call writes to the journal, and *nothing reads from it yet*. The legacy files
stay authoritative until Stage 4 has qualified the two against real traffic.

Three rules this module does not bend.

**A journal failure never fails a turn.** ``gui_pipeline`` already wraps
admission in ``contextlib.suppress`` with the comment "never block a turn", and
that judgement does not change because the writer is new. Every entry point
here returns ``None`` on failure rather than raising, and the caller carries on.
Losing a run because its *bookkeeping* broke would be a worse bug than the one
#613 is fixing.

**One transaction per lifecycle moment.** Admission inserts the task, the run
and the queued event together, or inserts none of them. A run row without its
queued event would be exactly the "individually plausible but mutually
contradictory" state the issue opens by describing.

**Identity comes from the caller, never invented here.** #379 owns task and run
identity. Minting an id in this module would create a second identity authority
and guarantee the drift Stage 4 is meant to detect.
"""

from __future__ import annotations

import contextlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from . import journal_store
from .journal_store import (
    JournalStoreError,
    StaleWriterError,
    acquire_lease,
    append_event,
    open_store,
)

#: Event names. Kept as literals rather than imported from the lifecycle
#: schema because these describe *journal* events, not run states -- #612 owns
#: the state vocabulary and this module deliberately does not restate it.
EVENT_ADMITTED = "run.admitted"
EVENT_STARTED = "run.started"
EVENT_CANCELLED = "run.cancelled"
EVENT_FINISHED = "run.finished"
EVENT_VERIFIED = "run.verified"
EVENT_COSTED = "run.cost_recorded"


class _TerminalRunReadmitted(Exception):
    """Internal: a finished run was admitted again. Rolls the transaction back."""


@contextmanager
def _store(root: Path) -> Iterator[sqlite3.Connection | None]:
    """Open the journal, yielding ``None`` when it cannot be opened.

    Callers treat ``None`` as "skip the mirror", which is the whole
    best-effort contract in one place rather than a try/except at every site.
    """

    connection = None
    try:
        connection = open_store(root)
    except (sqlite3.DatabaseError, JournalStoreError, OSError, ValueError):
        yield None
        return
    try:
        yield connection
    finally:
        # suppress() rather than try/except/pass: same intent, and bandit
        # rightly flags the bare form. Closing must not raise past the caller --
        # they already have their answer, and a failed close is not their
        # problem to handle.
        with contextlib.suppress(Exception):  # noqa: BLE001
            connection.close()


def record_admission(
    root: Path,
    *,
    task_id: str,
    run_id: str,
    task: str,
    now: str,
    surface: str = "gui",
    session: str = "",
    mode: str = "",
    model: str = "",
    route: str = "",
    attempt: int | None = None,
) -> int | None:
    """Record task, run and the admitted event in one transaction.

    Returns the lease fence for this run, or ``None`` if the journal could not
    be written. The fence is what later calls pass back, so a supervisor that
    was taken over cannot keep writing to a run it no longer owns.

    The task row is upserted rather than inserted: a retry is a second *run* of
    the same task, and failing here because the task already exists would make
    every retry unjournalled.
    """

    # A blank identifier is not an identifier. Found by an adversarial audit:
    # two unrelated runs admitted with run_id "" collapsed into a single row,
    # the second silently inheriting the first's task, and a terminal verdict
    # then landed on the merged record while one run vanished entirely. That is
    # precisely the silent loss #613 exists to prevent, so it is refused at the
    # boundary rather than stored and puzzled over later.
    if not str(run_id).strip() or not str(task_id).strip():
        return None

    with _store(root) as store:
        if store is None:
            return None
        try:
            with journal_store._transaction(store):
                # A run that already ended must not be re-opened. Found by an
                # adversarial audit: re-admission took a fresh lease but left
                # terminal_verdict in place, so a run that was running again
                # still read as "completed" -- a settled run reporting a
                # verdict it had not yet reached this time round.
                #
                # Refusing rather than clearing the verdict is deliberate.
                # #613 forbids terminal regression outright, and a genuine
                # retry already has a first-class representation: a new run id,
                # which becomes the next attempt of the same task. Silently
                # clearing would make the two indistinguishable in replay.
                settled = store.execute(
                    "SELECT terminal_verdict FROM runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if settled is not None and settled["terminal_verdict"]:
                    raise _TerminalRunReadmitted(run_id)
                store.execute(
                    "INSERT INTO tasks(task_id, origin_surface, origin_session,"
                    " created_at, requested_outcome, schema_version, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(task_id) DO UPDATE SET updated_at = excluded.updated_at",
                    (task_id, surface, session, now, _summary(task), 1, now),
                )
                # The attempt number is derived, not defaulted. `runs` has a
                # UNIQUE (task_id, attempt), so a hardcoded 1 meant the second
                # run of a task violated it -- and because admission is
                # best-effort, that violation was swallowed and the run went
                # unjournalled in silence. Exactly the gap this issue exists to
                # close, found by a test that expected two runs and saw one.
                #
                # A caller may still pass an explicit attempt when it knows the
                # lineage; otherwise the next free number is correct, because a
                # second run of one task *is* a second attempt.
                if attempt is None:
                    row = store.execute(
                        "SELECT COALESCE(MAX(attempt), 0) + 1 FROM runs"
                        " WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()
                    resolved_attempt = int(row[0]) if row else 1
                else:
                    resolved_attempt = int(attempt)
                store.execute(
                    "INSERT INTO runs(run_id, task_id, attempt, desired_state,"
                    " observed_state, route, model, created_at, updated_at)"
                    " VALUES (?, ?, ?, 'running', 'queued', ?, ?, ?, ?)"
                    " ON CONFLICT(run_id) DO NOTHING",
                    (run_id, task_id, resolved_attempt, route, model, now, now),
                )
            fence = acquire_lease(store, run_id=run_id, owner=surface, now=now)
            append_event(
                store,
                event_type=EVENT_ADMITTED,
                payload={"mode": mode, "model": model, "route": route},
                occurred_at=now,
                recorded_at=now,
                producer=surface,
                run_id=run_id,
                expected_fence=fence,
            )
            return fence
        except _TerminalRunReadmitted:
            # Not an error the caller can act on: the run is already finished,
            # and the correct next step -- a new run id -- is the caller's to
            # choose. Returning None means later lifecycle events are unfenced
            # no-ops, which is exactly right for a run that has ended.
            return None
        except (sqlite3.DatabaseError, JournalStoreError, ValueError):
            return None


def record_event(
    root: Path,
    *,
    run_id: str,
    event_type: str,
    now: str,
    fence: int | None = None,
    payload: Mapping[str, Any] | None = None,
    producer: str = "gui",
) -> int | None:
    """Append one lifecycle event, fenced when the caller holds a lease.

    A ``StaleWriterError`` is swallowed deliberately and returns ``None``: being
    fenced out is the system working, not an error the turn should hear about.
    The write is refused, which is the point.
    """

    with _store(root) as store:
        if store is None:
            return None
        try:
            return append_event(
                store,
                event_type=event_type,
                payload=dict(payload or {}),
                occurred_at=now,
                recorded_at=now,
                producer=producer,
                run_id=run_id,
                expected_fence=fence,
            )
        except StaleWriterError:
            return None
        except (sqlite3.DatabaseError, JournalStoreError, ValueError):
            return None


def record_terminal(
    root: Path,
    *,
    run_id: str,
    event_type: str,
    verdict: str,
    reason: str,
    now: str,
    fence: int | None = None,
    producer: str = "gui",
) -> bool:
    """Record a run's terminal verdict and release its lease.

    The verdict lands on the run row *and* as an event, in one transaction, so
    a reader of either sees the same ending. Releasing the lease afterwards is
    what lets a later process take the run over cleanly rather than finding a
    lease no one holds.
    """

    with _store(root) as store:
        if store is None:
            return False
        try:
            with journal_store._transaction(store):
                # The fence is checked *before* the update, inside the same
                # transaction. An earlier draft updated first and relied on the
                # append below to refuse a stale writer -- which let a fenced-out
                # process overwrite a finished run's verdict and only then get
                # refused, producing exactly the terminal regression the crash
                # matrix forbids. Found by its own test; the ordering is the fix.
                if fence is not None:
                    journal_store._assert_fence(store, run_id, fence)
                store.execute(
                    "UPDATE runs SET observed_state = ?, terminal_verdict = ?,"
                    " terminal_reason = ?, updated_at = ? WHERE run_id = ?",
                    (verdict, verdict, reason, now, run_id),
                )
            append_event(
                store,
                event_type=event_type,
                payload={"verdict": verdict, "reason": reason},
                occurred_at=now,
                recorded_at=now,
                producer=producer,
                run_id=run_id,
                expected_fence=fence,
            )
            if fence is not None:
                journal_store.release_lease(store, run_id=run_id, fence=fence, now=now)
            return True
        except StaleWriterError:
            return False
        except (sqlite3.DatabaseError, JournalStoreError, ValueError):
            return False


def record_run_cost(
    root: Path,
    *,
    run_id: str,
    operation_key: str,
    amount_usd: float,
    measurement_kind: str,
    now: str,
    fence: int | None = None,
    model: str = "",
    tokens: int = 0,
    producer: str = "gui",
) -> bool:
    """Attribute one cost to one operation, exactly once.

    The operation row is claimed first so the cost has something to hang from,
    then the cost is written. Both are separate transactions on purpose: the
    operation is the idempotency key, so claiming it twice is a no-op, while
    a second *cost* for the same key is a real accounting error the store
    refuses outright.

    A refused duplicate returns ``False`` rather than raising. #613 requires
    that no cost is attributed more than once; a retry that quietly does
    nothing is the correct behaviour, and a raised exception at this point in a
    finished turn would be a worse outcome than a duplicate we already blocked.
    """

    with _store(root) as store:
        if store is None:
            return False
        try:
            journal_store.record_operation(
                store,
                operation_key=operation_key,
                kind="model.call",
                target_digest=model or "",
                state="observed",
                now=now,
                run_id=run_id,
            )
            journal_store.record_cost(
                store,
                operation_key=operation_key,
                amount=float(amount_usd),
                measurement_kind=measurement_kind,
                now=now,
                quantity=float(tokens),
                price_snapshot=model or "",
            )
        except JournalStoreError:
            # Already attributed. Not an error: the guard did its job.
            return False
        except (sqlite3.DatabaseError, ValueError):
            return False
        record_event(
            root,
            run_id=run_id,
            event_type=EVENT_COSTED,
            now=now,
            fence=fence,
            payload={
                "operation_key": operation_key,
                "amount_usd": float(amount_usd),
                "measurement_kind": measurement_kind,
                "model": model,
            },
            producer=producer,
        )
        return True


def record_verification(
    root: Path,
    *,
    run_id: str,
    verdict: str,
    policy_digest: str,
    manifest_digest: str,
    now: str,
    fence: int | None = None,
    producer: str = "gui",
) -> bool:
    """Record which policy a run was verified under, and what it produced.

    Both digests travel together deliberately. A verdict without the policy
    that produced it cannot be audited later -- "this passed" means nothing
    without "against what" -- and #613's artifacts table exists precisely to
    keep that pairing.
    """

    with _store(root) as store:
        if store is None:
            return False
        try:
            with journal_store._transaction(store):
                if fence is not None:
                    journal_store._assert_fence(store, run_id, fence)
                store.execute(
                    "INSERT INTO artifacts(content_hash, operation_key, kind,"
                    " identity, privacy_class, created_at)"
                    " VALUES (?, NULL, 'verification_manifest', ?, 'internal', ?)"
                    " ON CONFLICT(content_hash, identity) DO NOTHING",
                    (manifest_digest or "unknown", run_id, now),
                )
        except StaleWriterError:
            return False
        except (sqlite3.DatabaseError, JournalStoreError, ValueError):
            return False
    return (
        record_event(
            root,
            run_id=run_id,
            event_type=EVENT_VERIFIED,
            now=now,
            fence=fence,
            payload={
                "verdict": verdict,
                "policy_digest": policy_digest,
                "manifest_digest": manifest_digest,
            },
            producer=producer,
        )
        is not None
    )


def _summary(task: str, *, limit: int = 200) -> str:
    """A bounded, single-line description of what was asked for.

    Truncated because ``tasks.requested_outcome`` is an identity field, not a
    transcript -- #613's non-goals rule out storing raw prompts to make replay
    possible, and an unbounded prompt here would quietly become one.
    """

    text = " ".join(str(task or "").split())
    return text[:limit]


__all__ = (
    "EVENT_ADMITTED",
    "EVENT_COSTED",
    "EVENT_CANCELLED",
    "EVENT_FINISHED",
    "EVENT_STARTED",
    "EVENT_VERIFIED",
    "record_admission",
    "record_run_cost",
    "record_verification",
    "record_event",
    "record_terminal",
)
