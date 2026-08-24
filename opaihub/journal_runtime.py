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
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - closing must not raise past the caller
            pass


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
    attempt: int = 1,
) -> int | None:
    """Record task, run and the admitted event in one transaction.

    Returns the lease fence for this run, or ``None`` if the journal could not
    be written. The fence is what later calls pass back, so a supervisor that
    was taken over cannot keep writing to a run it no longer owns.

    The task row is upserted rather than inserted: a retry is a second *run* of
    the same task, and failing here because the task already exists would make
    every retry unjournalled.
    """

    with _store(root) as store:
        if store is None:
            return None
        try:
            with journal_store._transaction(store):
                store.execute(
                    "INSERT INTO tasks(task_id, origin_surface, origin_session,"
                    " created_at, requested_outcome, schema_version, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(task_id) DO UPDATE SET updated_at = excluded.updated_at",
                    (task_id, surface, session, now, _summary(task), 1, now),
                )
                store.execute(
                    "INSERT INTO runs(run_id, task_id, attempt, desired_state,"
                    " observed_state, route, model, created_at, updated_at)"
                    " VALUES (?, ?, ?, 'running', 'queued', ?, ?, ?, ?)"
                    " ON CONFLICT(run_id) DO NOTHING",
                    (run_id, task_id, int(attempt), route, model, now, now),
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
    "EVENT_CANCELLED",
    "EVENT_FINISHED",
    "EVENT_STARTED",
    "EVENT_VERIFIED",
    "record_admission",
    "record_event",
    "record_terminal",
)
