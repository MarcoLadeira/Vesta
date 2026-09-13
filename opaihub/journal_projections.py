"""A reducer for the canonical event log, and a parity check against `runs`.

``journal_store.rebuild_projection`` is a deterministic fold over the event
history: hand it a ``reduce`` and an ``empty`` and it replays every event in
sequence, stopping at the first unreadable one rather than folding past it.

Nothing in OPai had ever handed it a reducer. A fold with no reducer cannot
answer anything, so #818's "collapse task/run/status projections into
deterministic reducers over canonical events" had nothing to collapse into.
This module is that reducer, and -- more usefully -- the parity assertion it
makes possible.

**Why parity here is worth having.** Migration step 2 asks for parity
assertions between legacy projections and canonical reducers, and step 4 for a
qualification comparison. Both have been blocked on the same thing: the legacy
corpus and the journal's runs come from different subsystems, so their
populations never overlap and no amount of waiting produces a comparison.

This check needs no legacy corpus at all. It compares the journal against
*itself*: the `runs` table and the `events` table are written by the same
lifecycle calls, each moment in one transaction. They are two recordings of
one history, and if they disagree, that is exactly the "individually plausible
but mutually contradictory" state #613 opens by describing -- inside the store
that is supposed to be canonical.

**It only reads.** Nothing here persists a projection. The check runs from
``opai doctor``, and a diagnostic that writes to the store it is diagnosing is
one more thing that can go wrong while something already has.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from . import journal_runtime, journal_store

#: The fold's name and version, as ``rebuild_projection`` requires. Bump the
#: version when the shape `reduce_runs` produces changes, so a caller that does
#: persist a projection never loads an older build's payload as this one's.
RUN_PROJECTION = "runs"
RUN_PROJECTION_VERSION = 1

#: Events that end a run, and therefore carry a verdict.
_TERMINAL_EVENTS = (
    journal_runtime.EVENT_FINISHED,
    journal_runtime.EVENT_CANCELLED,
)


def empty_runs() -> dict[str, Any]:
    return {"runs": {}}


def _blank_entry(run_id: str) -> dict[str, Any]:
    """What a run looks like before any event has been folded into it."""

    return {
        "run_id": run_id,
        "admitted": False,
        "started": False,
        "terminal_verdict": "",
        "terminal_reason": "",
        "verifications": 0,
        "cost_events": 0,
        "cost_usd": 0.0,
        "cancel_phase": "",
        "events": 0,
    }


def _apply(entry: dict[str, Any], event: Mapping[str, Any]) -> None:
    """Fold one event into one run's entry, in place.

    Total: an event this build does not recognise still increments the run's
    event count and changes nothing else. Ignoring it entirely would make the
    projection's ``events`` disagree with the log for a reason that is not a
    defect, and raising would make one unknown event type destroy the whole
    projection.

    A terminal verdict is written once. Replaying the same history twice must
    produce the same answer, and the store already forbids a run ending twice
    -- so a second terminal event is a contradiction to preserve, not to apply.
    """

    entry["events"] += 1
    event_type = str(event.get("event_type") or "")
    payload = event.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}

    if event_type == journal_runtime.EVENT_ADMITTED:
        entry["admitted"] = True
    elif event_type == journal_runtime.EVENT_STARTED:
        entry["started"] = True
    elif event_type in _TERMINAL_EVENTS:
        if not entry["terminal_verdict"]:
            entry["terminal_verdict"] = str(payload.get("verdict") or "")
            entry["terminal_reason"] = str(payload.get("reason") or "")
    elif event_type == journal_runtime.EVENT_VERIFIED:
        entry["verifications"] += 1
    elif event_type == journal_runtime.EVENT_COSTED:
        entry["cost_events"] += 1
        amount = payload.get("amount_usd")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool):
            entry["cost_usd"] = round(entry["cost_usd"] + float(amount), 10)
    elif event_type == journal_runtime.EVENT_CANCEL_PHASE:
        entry["cancel_phase"] = str(payload.get("phase") or "")


def reduce_runs(
    projection: Mapping[str, Any], event: Mapping[str, Any]
) -> dict[str, Any]:
    """Fold one event into the run projection, leaving the input untouched.

    The public, pure form, for a caller that holds on to its input. Purity has
    a price, stated honestly: the outer mapping is copied shallowly on every
    event, which is linear in the number of runs (pointer copies -- a few
    microseconds per event at thousands of runs), and only the touched run's
    entry is copied deeply. The parity check below does not pay it; see
    :func:`_fold_in_place`.
    """

    runs: dict[str, Any] = dict(projection.get("runs") or {})
    folded: dict[str, Any] = {"runs": runs}
    run_id = str(event.get("run_id") or "")
    if not run_id:
        # Journal-wide events (retention, backups) have no run. They are part
        # of the history and belong to no run's state.
        return folded
    # The caller's entry is never mutated; this one is ours to change.
    entry = dict(runs.get(run_id) or _blank_entry(run_id))
    runs[run_id] = entry
    _apply(entry, event)
    return folded


def _fold_in_place(
    projection: dict[str, Any], event: Mapping[str, Any]
) -> dict[str, Any]:
    """The same fold, mutating an accumulator nobody else can see.

    Constant cost per event. Safe only because the accumulator is created
    fresh for one replay and never escapes it -- which is why this is private
    and :func:`reduce_runs` is what other callers get.
    """

    run_id = str(event.get("run_id") or "")
    if run_id:
        runs = projection["runs"]
        entry = runs.get(run_id)
        if entry is None:
            entry = runs[run_id] = _blank_entry(run_id)
        _apply(entry, event)
    return projection


def _fold_on(store: sqlite3.Connection, *, now: str) -> Any:
    return journal_store.rebuild_projection(
        store,
        projection_type=RUN_PROJECTION,
        projection_version=RUN_PROJECTION_VERSION,
        reduce=_fold_in_place,
        empty=empty_runs,
        now=now,
        persist=False,
    )


def rebuild_runs(root: Path, *, now: str) -> Any:
    """Replay the whole event log into the run projection. Writes nothing."""

    store = journal_store.open_store(root)
    try:
        return _fold_on(store, now=now)
    finally:
        store.close()


def run_table_parity(root: Path, *, now: str) -> dict[str, Any]:
    """Does the folded event history agree with the `runs` table?

    Both are written by the same lifecycle calls in the same transactions, so
    disagreement is not drift between two subsystems -- it is the canonical
    store contradicting itself.

    **One snapshot.** The replay and the table read happen inside a single
    read transaction. Read on two connections, a run admitted between them --
    an ordinary event while the app is open -- showed up in one and not the
    other and was reported as a contradiction.

    Never raises, and never claims agreement it did not verify. A journal that
    cannot be read comes back ``comparable: False``; so does a projection that
    had to stop at an unreadable event, because a *short* history compared
    against a complete table would report disagreements that are really just
    the part it never got to read.
    """

    report: dict[str, Any] = {
        "comparable": False,
        "reason": "",
        "runs_in_table": 0,
        "runs_in_projection": 0,
        "disagreements": [],
        "disagreement_count": 0,
    }

    try:
        store = journal_store.open_store(root)
    except Exception as exc:  # noqa: BLE001 - a report must not raise
        report["reason"] = journal_store.describe_open_failure(exc)
        return report
    try:
        # DEFERRED: a read snapshot, taken at the first read below and held to
        # the end. No write lock -- this must never make a turn wait.
        store.execute("BEGIN")
        result = _fold_on(store, now=now)
        rows = store.execute(
            "SELECT run_id, terminal_verdict, terminal_reason FROM runs"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        report["reason"] = type(exc).__name__
        return report
    finally:
        with contextlib.suppress(Exception):  # noqa: BLE001
            store.execute("ROLLBACK")
        store.close()

    if result.integrity != journal_store.INTEGRITY_COMPLETE:
        report["reason"] = (
            f"projection stopped at sequence {result.first_invalid_sequence}"
        )
        return report

    projected = dict(result.payload.get("runs") or {})
    disagreements: list[dict[str, Any]] = []
    table = {str(row["run_id"]): row for row in rows}
    # The row keeps less of a reason than the event does. Comparing the whole
    # event reason reported every reason longer than the row's width as a
    # contradiction; both are redacted the same way first, so the row is
    # exactly this prefix of the event whenever they agree.
    width = journal_runtime.TERMINAL_REASON_CHARS

    for run_id, row in sorted(table.items()):
        folded = projected.get(run_id)
        if folded is None:
            disagreements.append(
                {
                    "run_id": run_id,
                    "field": "existence",
                    "table": "present",
                    "events": "absent",
                }
            )
            continue
        for field, in_table, in_events in (
            (
                "terminal_verdict",
                str(row["terminal_verdict"] or ""),
                str(folded.get("terminal_verdict") or ""),
            ),
            (
                "terminal_reason",
                str(row["terminal_reason"] or ""),
                str(folded.get("terminal_reason") or "")[:width],
            ),
        ):
            if in_table != in_events:
                disagreements.append(
                    {
                        "run_id": run_id,
                        "field": field,
                        "table": in_table,
                        "events": in_events,
                    }
                )

    for run_id in sorted(set(projected) - set(table)):
        disagreements.append(
            {
                "run_id": run_id,
                "field": "existence",
                "table": "absent",
                "events": "present",
            }
        )

    report["comparable"] = True
    report["runs_in_table"] = len(table)
    report["runs_in_projection"] = len(projected)
    # Bounded for the same reason every other report here is: this is for
    # acting on, not for dumping.
    report["disagreements"] = disagreements[:50]
    report["disagreement_count"] = len(disagreements)
    return report


__all__ = (
    "RUN_PROJECTION",
    "RUN_PROJECTION_VERSION",
    "empty_runs",
    "reduce_runs",
    "rebuild_runs",
    "run_table_parity",
)
