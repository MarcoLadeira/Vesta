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

That is why these handlers catch ``TypeError`` as well as the database errors.
``append_event`` deliberately refuses a payload it cannot encode, raising
before it takes a write lock -- the store will not store what it cannot
represent. But a caller bug in a payload must not take down the turn being
observed, so the refusal stops here rather than propagating. Strict store,
forgiving mirror.

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
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from . import call_reconciliation, journal_liveness, journal_store, owner_lease
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
#: One step of a cancellation, not the whole of it. #818 asks for cancellation
#: to be "explicitly two-phase: requested/acknowledged/terminated/reconciled",
#: and `cancellation_lifecycle` already models that properly -- in a *different*
#: journal. This is the canonical store learning the same phases, so a reader
#: of one record does not have to consult the other to know whether a stop was
#: asked for, seen, or confirmed.
EVENT_CANCEL_PHASE = "run.cancel_phase"
EVENT_FINISHED = "run.finished"
EVENT_VERIFIED = "run.verified"
EVENT_COSTED = "run.cost_recorded"
#: A durable snapshot showed the run in a new state. Distinct from
#: EVENT_STARTED so that a replay can tell "this run began" from "this run
#: moved", which matters when the legacy record only ever showed states.
EVENT_TRANSITIONED = "run.transitioned"

#: Requirement 9: what each event class may carry, so ``privacy_class`` means
#: something instead of defaulting to "internal" for everything.
#:
#: ``internal`` is for payloads made entirely of machine-chosen values -- a
#: mode, a model id, a route, a state name. ``sensitive`` is for anything that
#: can carry text originating from a person or a provider, which in practice
#: means any free-form reason string.
#:
#: The default is ``sensitive``, not ``internal``. An event type nobody
#: classified gets the cautious label, so forgetting this table over-protects
#: rather than under-protects. Over-protecting shows up as a support bundle
#: missing something useful; under-protecting shows up as a leak nobody
#: notices.
PRIVACY_INTERNAL = "internal"
PRIVACY_SENSITIVE = "sensitive"
EVENT_PRIVACY = {
    EVENT_ADMITTED: PRIVACY_INTERNAL,
    EVENT_STARTED: PRIVACY_INTERNAL,
    EVENT_TRANSITIONED: PRIVACY_INTERNAL,
    EVENT_COSTED: PRIVACY_INTERNAL,
    # These carry a reason or a detail, which is free-form by design.
    EVENT_FINISHED: PRIVACY_SENSITIVE,
    EVENT_CANCELLED: PRIVACY_SENSITIVE,
    EVENT_CANCEL_PHASE: PRIVACY_SENSITIVE,
    EVENT_VERIFIED: PRIVACY_SENSITIVE,
}


def privacy_class_for(event_type: str) -> str:
    """The privacy class of an event, defaulting to the cautious one."""

    return EVENT_PRIVACY.get(str(event_type), PRIVACY_SENSITIVE)


#: How much of a terminal reason the `runs` row keeps. The event payload keeps
#: more (``journal_store.MAX_PAYLOAD_STRING``), so anything comparing the two --
#: ``journal_projections.run_table_parity`` -- has to compare this much.
TERMINAL_REASON_CHARS = 500

#: Run states that mean a process is *executing* the run, not just describing
#: it. A process that saves one of these is the run's owner from then on.
_EXECUTING_STATES = frozenset({"preparing", "running", "verifying"})


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
            return _admit_on(
                store,
                run_id=run_id,
                task_id=task_id,
                task=task,
                now=now,
                surface=surface,
                session=session,
                mode=mode,
                model=model,
                route=route,
                attempt=attempt,
            )
        except _TerminalRunReadmitted:
            # Not an error the caller can act on: the run is already finished,
            # and the correct next step -- a new run id -- is the caller's to
            # choose. Returning None means later lifecycle events are unfenced
            # no-ops, which is exactly right for a run that has ended.
            return None
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
            return None


def _admit_on(
    store: sqlite3.Connection,
    *,
    run_id: str,
    task_id: str,
    task: str,
    now: str,
    surface: str,
    session: str = "",
    mode: str = "",
    model: str = "",
    route: str = "",
    attempt: int | None = None,
) -> int:
    """Admission on an already-open connection.

    Split out so that a caller doing several journal writes for one save can do
    them on one connection. Opening the store per call was measured at more
    than double the time inside ``_save_run``'s interprocess lock.
    """

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
                "SELECT COALESCE(MAX(attempt), 0) + 1 FROM runs WHERE task_id = ?",
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
        # Lease and admitted event inside the same transaction as the rows:
        # the module docstring promised "together, or none of them", and until
        # _transaction could be joined these were two more commits after it.
        fence = _take_lease(store, run_id=run_id, surface=surface, now=now)
        append_event(
            store,
            event_type=EVENT_ADMITTED,
            payload={"mode": mode, "model": model, "route": route},
            privacy_class=privacy_class_for(EVENT_ADMITTED),
            occurred_at=now,
            recorded_at=now,
            producer=surface,
            run_id=run_id,
            expected_fence=fence,
        )
    return fence


def _take_lease(
    store: sqlite3.Connection, *, run_id: str, surface: str, now: str
) -> int:
    """Take (or take over) ``run_id``'s lease for *this* process."""

    return acquire_lease(
        store,
        run_id=run_id,
        owner=surface,
        now=now,
        # The surface says "gui"; this says which gui. Reusing owner_lease's
        # boot id rather than minting a second one is deliberate: the two
        # authorities #818 has to reconcile now name the same process with the
        # same identifier, so a later comparison between them is a comparison
        # and not a translation.
        owner_pid=os.getpid(),
        owner_boot=owner_lease.boot_id(),
    )


def _held_by_this_process(store: sqlite3.Connection, run_id: str) -> bool:
    row = store.execute(
        "SELECT owner_pid, owner_boot FROM leases WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return False
    return row["owner_pid"] == os.getpid() and str(row["owner_boot"] or "") == str(
        owner_lease.boot_id()
    )


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
                privacy_class=privacy_class_for(event_type),
                occurred_at=now,
                recorded_at=now,
                producer=producer,
                run_id=run_id,
                expected_fence=fence,
            )
        except StaleWriterError:
            return None
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
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
            _terminal_on(
                store,
                run_id=run_id,
                event_type=event_type,
                verdict=verdict,
                reason=reason,
                now=now,
                fence=fence,
                producer=producer,
            )
            return True
        except StaleWriterError:
            return False
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
            return False


def _terminal_on(
    store: sqlite3.Connection,
    *,
    run_id: str,
    event_type: str,
    verdict: str,
    reason: str,
    now: str,
    fence: int | None = None,
    producer: str = "gui",
) -> None:
    """The terminal write on an already-open connection. See :func:`_admit_on`.

    Verdict, event and lease release are one transaction. They were three, so
    a crash after the first left a run the `runs` table called finished and
    the event log did not -- and a lease still held on it.
    """

    with journal_store._transaction(store):
        # The fence is checked *before* the update, inside the same
        # transaction. An earlier draft updated first and relied on the
        # append below to refuse a stale writer -- which let a fenced-out
        # process overwrite a finished run's verdict and only then get
        # refused, producing exactly the terminal regression the crash
        # matrix forbids. Found by its own test; the ordering is the fix.
        if fence is not None:
            journal_store._assert_fence(store, run_id, fence)
        # Redacted here as well as in the event payload. `minimise()` guards
        # the events table, and this is a direct UPDATE into `runs`, so it
        # bypasses that entirely -- found by a test that read the raw database
        # file and found a key the event path had already scrubbed. A reason
        # quotes provider errors, and provider errors quote the request that
        # failed, which is where a credential turns up.
        store.execute(
            "UPDATE runs SET observed_state = ?, terminal_verdict = ?,"
            " terminal_reason = ?, updated_at = ? WHERE run_id = ?",
            (
                verdict,
                verdict,
                journal_store.redact(str(reason))[:TERMINAL_REASON_CHARS],
                now,
                run_id,
            ),
        )
        append_event(
            store,
            event_type=event_type,
            payload={"verdict": verdict, "reason": reason},
            privacy_class=privacy_class_for(event_type),
            occurred_at=now,
            recorded_at=now,
            producer=producer,
            run_id=run_id,
            expected_fence=fence,
        )
        if fence is not None:
            journal_store.release_lease(store, run_id=run_id, fence=fence, now=now)


def live_fence(root: Path, run_id: str) -> int | None:
    """The fence of the lease currently held on ``run_id``, if any.

    ``None`` covers three situations a caller must not distinguish: the run was
    never admitted, the run finished and ``record_terminal`` released its
    lease, or the store could not be opened. All three mean the same thing to a
    mirror -- there is no live lease to write under.
    """

    with _store(root) as store:
        if store is None:
            return None
        return _live_fence_on(store, run_id)


def _live_fence_on(store: sqlite3.Connection, run_id: str) -> int | None:
    with contextlib.suppress(Exception):
        row = store.execute(
            "SELECT fence, released_at FROM leases WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is not None and row["released_at"] is None:
            return int(row["fence"])
    return None


def record_run_snapshot(
    root: Path,
    *,
    run_id: str,
    task_id: str,
    task: str,
    state: str,
    verdict: str = "",
    now: str,
    surface: str = "background",
    **details: Any,
) -> bool:
    """Mirror one durable run snapshot, admitting the run the first time only.

    Written for callers whose legacy record is a *file rewritten in place*
    rather than a stream of events -- ``background_runs`` saves a whole run
    document on every transition, so the mirror is handed a state, not a
    change.

    That shape is why this exists instead of calling ``record_admission`` from
    the save path. Admission is idempotent about the run *row*, but not about
    the rest: each call takes a fresh lease and appends another ``admitted``
    event, so a run that transitioned six times would read as six admissions of
    one run. A run is admitted once; everything after that is a transition.

    Everything happens on **one** connection. That is a performance
    requirement, not tidiness: this runs inside ``_save_run``'s interprocess
    lock, and an earlier version that called ``live_fence``, ``record_event``
    and ``record_terminal`` in turn opened the store three times per save and
    more than doubled the time the lock was held. Requirement 4 asks that
    high-frequency writes not block critical state commits, and a mirror that
    slows the thing it mirrors is a mirror that gets removed.

    Returns whether anything was recorded. Like the rest of Stage 3 it never
    raises -- a legacy write must not fail because its mirror did.
    """

    if not str(run_id).strip() or not str(task_id).strip():
        return False

    with _store(root) as store:
        if store is None:
            return False
        try:
            fence = _live_fence_on(store, run_id)
            if fence is None:
                if _is_settled(store, run_id):
                    # The run already ended. Its verdict is immutable and a
                    # retry belongs to a new run id, so there is nothing here
                    # to record.
                    return False
                fence = _admit_on(
                    store,
                    run_id=run_id,
                    task_id=task_id,
                    task=task,
                    now=now,
                    surface=surface,
                    mode="",
                    model="",
                    route="",
                    session="",
                    attempt=None,
                )
            else:
                with journal_store._transaction(store):
                    if state in _EXECUTING_STATES and not _held_by_this_process(
                        store, run_id
                    ):
                        # #818 review finding 1, reproduced: the lease named
                        # whoever *first* saved the run. For a background run
                        # that is `automation enqueue`, which exits at once --
                        # `automation run` executes it in a second process
                        # that never took the lease over. A concurrent
                        # `automation recover` then saw the owner gone and
                        # wrote "the owning session ended" onto a live run,
                        # the exact false record the liveness check exists
                        # to prevent. The reverse held too: a GUI that only
                        # queued a run kept it looking owned for hours after
                        # the process running it had died.
                        #
                        # The process executing a run owns it. Only these
                        # states transfer ownership: a cancel request or a
                        # recovery sweep from another process describes the
                        # run without running it, and must not fence out the
                        # process that is.
                        fence = _take_lease(
                            store, run_id=run_id, surface=surface, now=now
                        )
                    append_event(
                        store,
                        event_type=EVENT_TRANSITIONED,
                        payload={"state": state, **details},
                        privacy_class=privacy_class_for(EVENT_TRANSITIONED),
                        occurred_at=now,
                        recorded_at=now,
                        producer=surface,
                        run_id=run_id,
                        expected_fence=fence,
                    )

            if verdict:
                _terminal_on(
                    store,
                    run_id=run_id,
                    event_type=EVENT_FINISHED,
                    verdict=verdict,
                    reason=state,
                    now=now,
                    fence=fence,
                    producer=surface,
                )
            return True
        except _TerminalRunReadmitted:
            return False
        except StaleWriterError:
            return False
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
            return False


def _is_settled(store: sqlite3.Connection, run_id: str) -> bool:
    row = store.execute(
        "SELECT terminal_verdict FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    return bool(row is not None and row["terminal_verdict"])


def _fence_for_late_evidence(
    store: sqlite3.Connection, run_id: str, fence: int | None
) -> tuple[int | None, bool]:
    """Which fence a piece of after-the-fact evidence should be written under.

    Evidence does not always arrive before the run it describes ends. #818's
    qualification names two of these outright -- "late provider completion" and
    "delayed usage reporting" -- and a provider CLI reporting its usage after
    OPai has already filed the turn is the ordinary way to reach them.

    ``record_terminal`` releases the lease, so a fenced append is refused from
    that moment on. That refusal is right for a *stale* writer and wrong here:
    there is no current holder for a settled run to be stale relative to. A
    writer fenced out by a genuine takeover is a different shape entirely --
    takeover leaves a new, unreleased lease, so the run is not settled and this
    path is never taken.

    Returns the fence to use and whether the run had already ended, so the
    caller can say so in the payload rather than filing late evidence as though
    it arrived on time.
    """

    if not _is_settled(store, run_id):
        return fence, False
    return None, True


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
            fence, after_terminal = _fence_for_late_evidence(store, run_id, fence)
        except (sqlite3.DatabaseError, JournalStoreError):
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
                # Recorded rather than hidden: a reader replaying this history
                # must be able to tell spend that arrived while the run was
                # live from spend that turned up after it was filed.
                "after_terminal": after_terminal,
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

    after_terminal = False
    with _store(root) as store:
        if store is None:
            return False
        try:
            # Resolved before the artifact write, because that write asserts
            # the fence too -- and a settled run has no live lease to assert
            # against, so leaving it until afterwards refused the whole record
            # rather than only its event.
            fence, after_terminal = _fence_for_late_evidence(store, run_id, fence)
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
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
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
                # A verification that lands after the verdict is a
                # contradiction worth keeping, not one to drop silently: it is
                # evidence that the terminal state was reached without it.
                "after_terminal": after_terminal,
            },
            producer=producer,
        )
        is not None
    )


def beat_lease(root: Path, *, run_id: str, now: str) -> bool:
    """Restamp the heartbeat on a lease **this process holds**. Never raises.

    The identity check is the whole guard, and it is the rule ``owner_lease``
    states for the file it keeps: "refreshing someone else's lease would keep a
    dead owner looking alive forever, which is the exact failure this module
    exists to prevent". So the update matches on pid *and* boot id, and a
    process that has been superseded restamps nothing.

    No fence is needed for the same reason. A fence proves you were the current
    holder at acquisition; the identity proves you are the process that
    acquired it, which is the stronger claim here -- a lease taken over by
    somebody else no longer carries our identity, so the update simply matches
    no rows.

    Best-effort, like every other mirror on this path: a heartbeat that cannot
    be written costs evidence, never the turn it describes.
    """

    if not str(run_id).strip():
        return False
    if not journal_store.journal_path(root).exists():
        return False
    with _store(root) as store:
        if store is None:
            return False
        try:
            with journal_store._transaction(store):
                cursor = store.execute(
                    "UPDATE leases SET heartbeat_at = ?"
                    " WHERE run_id = ? AND released_at IS NULL"
                    " AND owner_pid = ? AND owner_boot = ?",
                    (now, run_id, os.getpid(), owner_lease.boot_id()),
                )
                return cursor.rowcount > 0
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
            return False


def record_cancellation_phase(
    root: Path,
    *,
    run_id: str,
    phase: str,
    reason_code: str,
    now: str,
    producer: str = "cancellation",
) -> bool:
    """Mirror one accepted cancellation phase into the canonical journal.

    The phases themselves are decided and made durable by
    ``cancellation_lifecycle``, which is deliberately left as the authority:
    it holds the lock that makes two racing cancellations converge, and a
    mirror that tried to re-decide anything would be a second opinion on the
    one question #380 exists to give a single answer to.

    So this records, and only records. It is called after the transition has
    already been accepted, outside that lock, and every failure is swallowed --
    a cancellation must never be slowed or refused by its own bookkeeping.

    A scope with no journalled run simply writes nothing: the event table's
    ``run_id`` is a foreign key, so an unknown run is refused by the store
    rather than inventing a row for it.
    """

    if not str(run_id).strip() or not str(phase).strip():
        return False
    if not journal_store.journal_path(root).exists():
        return False
    fence: int | None = None
    with _store(root) as store:
        if store is None:
            return False
        try:
            fence = _live_fence_on(store, run_id)
            fence, after_terminal = _fence_for_late_evidence(store, run_id, fence)
        except (sqlite3.DatabaseError, JournalStoreError):
            return False
    return (
        record_event(
            root,
            run_id=run_id,
            event_type=EVENT_CANCEL_PHASE,
            now=now,
            fence=fence,
            payload={
                "phase": str(phase),
                "reason_code": str(reason_code or ""),
                # A `terminated` that lands after the run was filed is the
                # normal shape of a confirmed teardown, not an anomaly -- but
                # replay still has to be able to see which side of the verdict
                # it arrived on.
                "after_terminal": after_terminal,
            },
            producer=producer,
        )
        is not None
    )


def unterminated_runs(
    root: Path,
    *,
    limit: int = 100,
    is_pid_running: Callable[[int], bool | None] = call_reconciliation.pid_is_running,
) -> list[dict[str, Any]]:
    """Runs this installation admitted and never recorded an ending for.

    #613 opens by describing this exact state:

        A run may appear active with no worker or disappear after restart.
        ...
        Recovery logic cannot know whether to resume, reconcile, block or
        request attention.

    ``journal_operations.unreconciled_operations`` answers that question for
    external effects. This answers it for runs, which is the half that was
    missing -- and it is the first question worth asking after a crash.

    **It reports rather than concludes.** An unterminated run with a lease
    still held is either running right now or was abandoned by a process that
    died before releasing it: a lease is released by ``record_terminal``, not
    by a process exiting.

    This used to be unanswerable, and said so -- each row carried "the owner",
    which was the string ``"gui"``, and told the caller to go and check whether
    that process still existed. It could not. #818 gives the lease a real
    process identity, so ``owner_liveness`` can now answer, in the closed
    vocabulary ``journal_liveness`` owns.

    What has not changed is the refusal. A pid that is not running is
    conclusive; a pid that is running is not, because pids get reused, and that
    case reports ``owner_unverified`` rather than being rounded up to "alive".
    A lease written before the identity columns existed reports ``unknown``.
    Inventing the distinction where the evidence does not reach is still the
    failure this issue exists to remove -- a plausible answer with nothing
    behind it.
    """

    if not journal_store.journal_path(root).exists():
        return []
    with _store(root) as store:
        if store is None:
            return []
        try:
            rows = store.execute(
                "SELECT r.run_id, r.task_id, r.attempt, r.observed_state,"
                " r.created_at, r.updated_at,"
                " l.owner AS lease_owner, l.heartbeat_at AS lease_heartbeat_at,"
                " l.released_at AS lease_released_at,"
                " l.acquired_at AS lease_acquired_at,"
                " l.owner_pid, l.owner_boot"
                " FROM runs r LEFT JOIN leases l ON l.run_id = r.run_id"
                " WHERE r.terminal_verdict IS NULL OR r.terminal_verdict = ''"
                " ORDER BY r.created_at LIMIT ?",
                (int(limit),),
            ).fetchall()
        except (sqlite3.DatabaseError, JournalStoreError, TypeError, ValueError):
            return []
    pending = []
    for row in rows:
        entry = dict(row)
        # Named for what it is: somebody still holds the lease. Whether that
        # somebody is alive is a separate question, answered below.
        entry["lease_held"] = bool(
            entry.get("lease_owner") and not entry.get("lease_released_at")
        )
        # #818: the caller no longer has to go and find the process itself.
        # The lease names one, so the question can be asked here -- and the
        # vocabulary keeps its refusals: `unknown` for a pre-migration lease
        # with no pid, `owner_unverified` for a pid that exists but cannot be
        # proved to be the same process. Neither is upgraded into "alive".
        entry["owner_liveness"] = journal_liveness.owner_liveness(
            entry, is_pid_running=is_pid_running
        )
        pending.append(entry)
    return pending


def _why_unopenable(root: Path) -> str:
    """The word for a journal that would not open.

    ``_store`` swallows every open failure alike, and the two that matter need
    different words: "a newer OPai wrote this" points at an upgrade;
    "unreadable" points at a corrupt file, and sending someone to the wrong
    one of those wastes their evening.
    """

    return (
        "incompatible" if journal_store.written_by_a_newer_opai(root) else "unreadable"
    )


def unterminated_summary(
    root: Path,
    *,
    is_pid_running: Callable[[int], bool | None] = call_reconciliation.pid_is_running,
) -> dict[str, Any]:
    """Counts for doctor, and now a breakdown by who still owns the work.

    ``abandoned`` is the count a recovery pass can act on: runs whose owning
    process is provably gone. It is deliberately narrower than "not owned
    here" -- an unverified owner is excluded, because offering to recover work
    another OPai is doing is the mistake this whole mechanism is built to
    avoid.
    """

    facts: dict[str, Any] = {
        "available": False,
        # Why the counts below are not an answer, when they are not one.
        # "" means they are.
        "unavailable_reason": "",
        "unterminated": 0,
        "lease_held": 0,
        "abandoned": 0,
        "by_owner": {verdict: 0 for verdict in journal_liveness.VERDICTS},
    }
    if not journal_store.journal_path(root).exists():
        facts["unavailable_reason"] = "no_journal"
        return facts

    # `available` means the store was *read*, not that a file exists. It used
    # to mean the latter, so an unreadable journal reported
    # `available: True, unterminated: 0` -- indistinguishable from a healthy
    # journal with nothing pending. This is the report a recovery pass makes
    # after a crash, which is the worst possible moment to answer "nothing to
    # worry about" when the truth is "I could not look".
    #
    # Found by running an older build against a journal a newer one had
    # migrated -- a downgrade this branch's schema bump makes reachable.
    # `store_health` said "incompatible" loudly and this said zero.
    with _store(root) as store:
        if store is None:
            facts["unavailable_reason"] = _why_unopenable(root)
            return facts
        try:
            report = journal_store.check_integrity(store)
        except (sqlite3.DatabaseError, JournalStoreError):
            facts["unavailable_reason"] = "unreadable"
            return facts
    if not report.usable:
        # `usable` already treats `degraded` as serviceable -- some rows are
        # unreadable and the critical state is not unknown -- so only corrupt
        # and incompatible stop the count meaning anything.
        facts["unavailable_reason"] = report.state
        return facts

    pending = unterminated_runs(root, limit=10_000, is_pid_running=is_pid_running)
    facts["available"] = True
    facts["unterminated"] = len(pending)
    facts["lease_held"] = sum(1 for entry in pending if entry["lease_held"])
    for entry in pending:
        verdict = str(entry.get("owner_liveness") or journal_liveness.OWNER_UNKNOWN)
        facts["by_owner"][verdict] = facts["by_owner"].get(verdict, 0) + 1
    facts["abandoned"] = facts["by_owner"][journal_liveness.OWNER_GONE]
    return facts


def _summary(task: str, *, limit: int = 200) -> str:
    """A bounded, redacted, single-line description of what was asked for.

    Truncated because ``tasks.requested_outcome`` is an identity field, not a
    transcript -- #613's non-goals rule out storing raw prompts to make replay
    possible, and an unbounded prompt here would quietly become one.

    Redacted because truncation alone was not enough, which was demonstrated
    rather than argued: a task reading ``fix my auth, the key is sk-ant-...``
    put that key verbatim into ``journal.sqlite3``, found by reading the raw
    file back and searching its bytes. A prompt is the one field here that
    carries whatever the user happened to type, which makes it the one most
    likely to carry something they never meant to persist.
    """

    text = " ".join(str(task or "").split())
    return journal_store.redact(text)[:limit]


def unevidenced_completions(root: Path) -> dict[str, Any]:
    """Which runs are recorded as completed with nothing behind the verdict.

    #818 AC6 asks that ``completed`` be impossible without the required
    objective, verification and delivery evidence. It is not: the store
    accepts whatever verdict a caller hands it, and a run admitted and
    immediately terminated as ``completed`` -- no verification event, no
    artifact, no cost, no recorded ending beyond the verdict itself -- is
    written without complaint. Measured, not inferred.

    This does not refuse the write, and that restraint is deliberate. Every
    mirror in this module records rather than re-decides, because the layer
    that *can* decide is the completion machinery in ``opaihub.completion``
    which has the answer, the diff and the policy in front of it. A journal
    that started overruling verdicts would be a second opinion on the one
    question the epic exists to give a single answer to -- and refusing to
    record a terminal state would leave the run reading as unfinished, which
    is a worse lie than an unevidenced completion.

    So it counts, and makes the gap addressable. Enforcement is Stage 5's, and
    it needs these numbers to be zero first.

    **Two numbers, because one of them flatters.** ``unevidenced`` is the weak
    bar: a run with no trace of any kind -- no ``run.verified`` event, no
    verification manifest, no recorded cost. ``without_verification`` is the
    bar AC6 actually sets, which names objective, verification and delivery
    evidence and does not mention cost at all.

    The distinction is not academic. On the journal of the machine this was
    written on: 21 completed runs, **0** unevidenced, **20** with no
    verification. Every real turn records a cost, so the weak number reads as
    a clean bill of health for a criterion that is plainly unmet. Reporting
    only that would have been this epic's own failure -- a confident answer
    with the inconvenient half left out.
    """

    # Every key present on every path: a caller must not have to know which
    # branch produced a report to read it.
    empty: dict[str, Any] = {
        "available": False,
        "unavailable_reason": "",
        "completed": 0,
        "unevidenced": 0,
        "without_verification": 0,
        "run_ids": [],
        "unverified_run_ids": [],
    }
    with _store(root) as store:
        if store is None:
            empty["unavailable_reason"] = _why_unopenable(root)
            return empty
        try:
            rows = store.execute(
                "SELECT r.run_id AS run_id,"
                " (SELECT COUNT(*) FROM events e WHERE e.run_id = r.run_id"
                "  AND e.event_type = ?) AS verifications,"
                " (SELECT COUNT(*) FROM artifacts a WHERE a.identity = r.run_id"
                "  AND a.kind = 'verification_manifest') AS manifests,"
                " (SELECT COUNT(*) FROM events e WHERE e.run_id = r.run_id"
                "  AND e.event_type = ?) AS costs"
                " FROM runs r WHERE r.terminal_verdict = 'completed'"
                " ORDER BY r.run_id",
                (EVENT_VERIFIED, EVENT_COSTED),
            ).fetchall()
        except (sqlite3.DatabaseError, JournalStoreError):
            empty["unavailable_reason"] = "unreadable"
            return empty

    bare = [
        str(row["run_id"])
        for row in rows
        if not (row["verifications"] or row["manifests"] or row["costs"])
    ]
    # AC6 names objective, verification and delivery evidence. It does not
    # name cost, and every real turn records one -- so counting cost as
    # evidence makes the weak number read as zero on a journal where the
    # criterion is plainly unmet.
    unverified = [
        str(row["run_id"])
        for row in rows
        if not (row["verifications"] or row["manifests"])
    ]
    return {
        "available": True,
        "unavailable_reason": "",
        "completed": len(rows),
        "unevidenced": len(bare),
        "without_verification": len(unverified),
        # Bounded: a report is for acting on, and a thousand ids is a dump.
        "run_ids": bare[:50],
        "unverified_run_ids": unverified[:50],
    }


def unconfirmed_cancellations(root: Path) -> dict[str, Any]:
    """Runs recorded as cancelled with nothing showing the work stopped.

    #818 AC5 asks that ``cancelled`` be impossible while owned controllable
    work is still alive. It is not. Measured with two real processes: a
    process holding no fence for a run can write ``cancelled`` for it while
    the owning process is demonstrably still running, and the store accepts it.

    And on this repository's own journal, all six cancelled runs carry no
    cancellation-phase evidence at all -- every one says the run stopped, and
    not one records that anything did.

    Evidence here means a ``run.cancel_phase`` event that reached
    ``terminated``. ``cancellation_lifecycle`` already models the full ladder
    (requested, acknowledged, draining, force_terminating, terminated), and
    ``terminated`` is the phase that means *confirmed stopped* rather than
    *asked to stop*. A terminal ``cancelled`` verdict with no terminated phase
    behind it is a claim about the world nobody checked.

    Like :func:`unevidenced_completions` this counts rather than refuses, and
    for a stronger reason than consistency: a Stop that OPai declined to
    record would be a Stop the user pressed and did not get. Refusing here
    would trade a reporting fault for a blocking one, which is the wrong
    trade in every case. So the write stands and the gap is made visible.
    """

    empty: dict[str, Any] = {
        "available": False,
        "unavailable_reason": "",
        "cancelled": 0,
        "unconfirmed": 0,
        "run_ids": [],
    }
    with _store(root) as store:
        if store is None:
            empty["unavailable_reason"] = _why_unopenable(root)
            return empty
        try:
            rows = store.execute(
                "SELECT run_id FROM runs WHERE terminal_verdict = 'cancelled'"
                " ORDER BY run_id"
            ).fetchall()
            confirmed: set[str] = set()
            for event in store.execute(
                "SELECT run_id, payload FROM events WHERE event_type = ?",
                (EVENT_CANCEL_PHASE,),
            ):
                run_id = str(event["run_id"] or "")
                if not run_id:
                    continue
                # The payload is JSON text and an unreadable one proves
                # nothing, so it simply does not count as evidence.
                try:
                    phase = str(
                        (json.loads(event["payload"] or "{}") or {}).get("phase") or ""
                    )
                except (TypeError, ValueError):
                    continue
                if phase == "terminated":
                    confirmed.add(run_id)
        except (sqlite3.DatabaseError, JournalStoreError):
            empty["unavailable_reason"] = "unreadable"
            return empty

    bare = [str(row["run_id"]) for row in rows if str(row["run_id"]) not in confirmed]
    return {
        "available": True,
        "unavailable_reason": "",
        "cancelled": len(rows),
        "unconfirmed": len(bare),
        # Bounded: a report is for acting on, not for dumping.
        "run_ids": bare[:50],
    }


__all__ = (
    "EVENT_ADMITTED",
    "EVENT_PRIVACY",
    "EVENT_COSTED",
    "EVENT_CANCELLED",
    "EVENT_CANCEL_PHASE",
    "EVENT_FINISHED",
    "EVENT_STARTED",
    "EVENT_VERIFIED",
    "privacy_class_for",
    "record_admission",
    "unterminated_runs",
    "unterminated_summary",
    "unevidenced_completions",
    "unconfirmed_cancellations",
    "record_run_cost",
    "record_verification",
    "record_cancellation_phase",
    "record_event",
    "record_terminal",
    "beat_lease",
)
