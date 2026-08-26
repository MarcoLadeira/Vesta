"""#613 functional requirement 5: retention and compaction, by event class.

    Define retention/compaction separately for audit-critical versus
    high-volume presentation events.

Two classes, and the whole design is in how the boundary between them fails.

*Audit-critical* is everything a run's state, spend, authority or outcome can
be reconstructed from. It is never deleted, at any age, for any reason. The
issue's acceptance criteria forbid terminal regression and silent loss, and an
event that proves a run finished, cost money, or was approved is exactly the
kind of thing whose absence rewrites history rather than merely shortening it.

*Presentation* is the high-volume remainder: the states a run passed through on
its way somewhere, kept because they are useful to look at and not because
anything depends on them.

**Classification is default-deny.** An event type nobody has classified is
treated as audit-critical and kept. That is the opposite of the convenient
default, and it is the point: the failure mode of an allow-by-default list is
that somebody adds an event type, forgets the retention list exists, and
discovers a year later that the thing they needed was being deleted the whole
time. Being kept forever is a disk-space bug, which is visible and fixable.
Being deleted is neither.

The invariant the tests pin, and the reason this is safe at all: **compacting
must not change any projection.** A projection rebuilt from the compacted
journal is byte-identical to one rebuilt before, because everything a
projection reduces over is audit-critical. If that ever stops being true, the
classification is wrong, and the test says so rather than the user finding out.

Three further rules, each narrowing what may be removed:

1. Only runs that have reached a terminal verdict. A live run's history is not
   history yet, and the process still writing to it is the one most likely to
   need it.
2. Only events older than the window, measured against ``recorded_at`` rather
   than ``occurred_at`` -- a clock rollback (an edge case the issue names)
   would otherwise make yesterday's events look ancient.
3. A floor of recent events per run survives regardless of age, so a finished
   run can still show how it got there rather than only that it ended.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from . import journal_runtime, journal_store

#: Event types that may be pruned once they are old enough. Membership is
#: opt-in and deliberately short: an event earns a place here by being
#: high-volume *and* by nothing depending on it, which is a claim about the
#: whole system rather than about the event.
PRESENTATION_EVENTS = frozenset({journal_runtime.EVENT_TRANSITIONED})

#: How long presentation events are kept by default. Long enough that "what
#: happened last week" is still answerable, short enough that a machine running
#: continuous background work does not accumulate forever.
DEFAULT_PRESENTATION_DAYS = 30

#: Presentation events kept per run regardless of age, so a finished run can
#: still show how it got there rather than only that it ended.
DEFAULT_FLOOR_PER_RUN = 5


def is_prunable(event_type: str) -> bool:
    """Whether this event class may ever be deleted.

    Default-deny: anything unrecognised is audit-critical. See the module
    docstring for why that asymmetry is deliberate.
    """

    return str(event_type) in PRESENTATION_EVENTS


@dataclass(frozen=True)
class RetentionReport:
    """What compaction removed, and what it deliberately left."""

    removed_events: int = 0
    runs_touched: int = 0
    kept_audit_critical: int = 0
    kept_within_window: int = 0
    kept_live_runs: int = 0
    reclaimed_bytes: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": "opai-journal-retention",
            "removed_events": self.removed_events,
            "runs_touched": self.runs_touched,
            "kept_audit_critical": self.kept_audit_critical,
            "kept_within_window": self.kept_within_window,
            "kept_live_runs": self.kept_live_runs,
            "reclaimed_bytes": self.reclaimed_bytes,
            "detail": self.detail,
        }


def _cutoff(now: str, days: int) -> str:
    try:
        moment = datetime.fromisoformat(str(now))
    except ValueError:
        moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment - timedelta(days=max(0, int(days)))).isoformat()


def plan(
    connection: sqlite3.Connection,
    *,
    now: str,
    presentation_days: int = DEFAULT_PRESENTATION_DAYS,
    floor_per_run: int = DEFAULT_FLOOR_PER_RUN,
) -> tuple[list[int], RetentionReport]:
    """Which event sequences may be removed, and why the rest stay.

    Separated from the deletion so the decision can be inspected and tested
    without destroying anything, and so ``compact`` has nothing left to decide.
    """

    cutoff = _cutoff(now, presentation_days)
    floor = max(0, int(floor_per_run))

    prunable = tuple(sorted(PRESENTATION_EVENTS))
    if not prunable:  # pragma: no cover - the set is not empty today
        return [], RetentionReport(detail="no event class is prunable")

    placeholders = ",".join("?" for _ in prunable)
    # Only runs that have settled. A live run's history is not history yet.
    rows = connection.execute(
        f"""
        SELECT e.sequence, e.run_id, e.recorded_at
        FROM events e
        JOIN runs r ON r.run_id = e.run_id
        WHERE e.event_type IN ({placeholders})
          AND r.terminal_verdict IS NOT NULL
          AND r.terminal_verdict != ''
        ORDER BY e.run_id, e.sequence DESC
        """,  # nosec B608 - placeholders are generated, values are bound
        prunable,
    ).fetchall()

    removable: list[int] = []
    within_window = 0
    seen_per_run: dict[str, int] = {}
    for row in rows:
        run_id = row["run_id"]
        rank = seen_per_run.get(run_id, 0)
        seen_per_run[run_id] = rank + 1
        if rank < floor:
            # The floor: the most recent few survive whatever their age.
            continue
        if str(row["recorded_at"]) >= cutoff:
            within_window += 1
            continue
        removable.append(int(row["sequence"]))

    total = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    prunable_total = connection.execute(
        f"SELECT COUNT(*) FROM events WHERE event_type IN ({placeholders})",  # nosec B608
        prunable,
    ).fetchone()[0]
    live = connection.execute(
        f"""
        SELECT COUNT(*) FROM events e
        LEFT JOIN runs r ON r.run_id = e.run_id
        WHERE e.event_type IN ({placeholders})
          AND (r.run_id IS NULL OR r.terminal_verdict IS NULL OR r.terminal_verdict = '')
        """,  # nosec B608
        prunable,
    ).fetchone()[0]

    report = RetentionReport(
        removed_events=len(removable),
        runs_touched=len({row["run_id"] for row in rows}),
        kept_audit_critical=int(total) - int(prunable_total),
        kept_within_window=within_window,
        kept_live_runs=int(live),
        detail=(
            f"{len(removable)} presentation event(s) older than {presentation_days} "
            f"day(s) may be removed; every audit-critical event is kept"
        ),
    )
    return removable, report


def compact(
    project_root: Path,
    *,
    now: str,
    presentation_days: int = DEFAULT_PRESENTATION_DAYS,
    floor_per_run: int = DEFAULT_FLOOR_PER_RUN,
    reclaim: bool = True,
) -> RetentionReport:
    """Apply retention, then reclaim the space it freed.

    Never raises. Retention is housekeeping, and housekeeping that can take the
    application down with it does not get run.

    ``reclaim`` runs ``VACUUM``, which is what actually returns the pages to the
    filesystem -- deleting rows alone leaves the file the same size, so a user
    who ran this to free disk would see nothing change and conclude it does not
    work. It is separable because VACUUM rewrites the whole database and wants
    a moment when nothing else is writing.
    """

    if not journal_store.journal_path(project_root).exists():
        return RetentionReport(detail="this project has no journal")

    connection: sqlite3.Connection | None = None
    try:
        # Measured before anything is removed: "reclaimed" should mean the
        # difference the whole operation made, not the slice of it after the
        # rows were already gone from the page cache.
        before = journal_store.journal_path(project_root).stat().st_size
        connection = journal_store.open_store(project_root)
        removable, report = plan(
            connection,
            now=now,
            presentation_days=presentation_days,
            floor_per_run=floor_per_run,
        )
        if removable:
            with journal_store._transaction(connection):
                # Chunked so a very large prune does not build one enormous
                # statement, and so each chunk commits work already done.
                for start in range(0, len(removable), 500):
                    chunk = removable[start : start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    connection.execute(
                        f"DELETE FROM events WHERE sequence IN ({placeholders})",  # nosec B608
                        chunk,
                    )
        if reclaim and removable:
            connection.execute("VACUUM")
        # Closed before measuring: in WAL mode the database file only shrinks
        # once the WAL is checkpointed back into it, which happens on close.
        # Measuring while still connected reports zero reclaimed however much
        # was actually freed.
        connection.close()
        connection = None
        after = journal_store.journal_path(project_root).stat().st_size
        return RetentionReport(
            removed_events=report.removed_events,
            runs_touched=report.runs_touched,
            kept_audit_critical=report.kept_audit_critical,
            kept_within_window=report.kept_within_window,
            kept_live_runs=report.kept_live_runs,
            reclaimed_bytes=max(0, before - after),
            detail=report.detail,
        )
    except Exception as exc:  # noqa: BLE001 - housekeeping never takes the app down
        from .command_runner import redact

        return RetentionReport(
            detail=f"retention could not run: {redact(str(exc))[:160]}"
        )
    finally:
        if connection is not None:
            connection.close()


def retention_health(project_root: Path) -> dict[str, Any]:
    """What retention *would* remove, without removing it (for doctor).

    A dry run rather than a scheduled prune, because deciding when to delete a
    user's history is not a diagnostic command's business.
    """

    facts: dict[str, Any] = {
        "available": True,
        "prunable_now": 0,
        "audit_critical": 0,
        "total_events": 0,
    }
    if not journal_store.journal_path(project_root).exists():
        facts["available"] = False
        return facts
    connection: sqlite3.Connection | None = None
    try:
        connection = journal_store.open_store(project_root)
        facts["total_events"] = int(
            connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        )
        _, report = plan(
            connection, now=datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
        facts["prunable_now"] = report.removed_events
        facts["audit_critical"] = report.kept_audit_critical
    except Exception:  # noqa: BLE001 - doctor never raises
        facts["available"] = False
    finally:
        if connection is not None:
            connection.close()
    return facts


__all__: Sequence[str] = (
    "DEFAULT_FLOOR_PER_RUN",
    "DEFAULT_PRESENTATION_DAYS",
    "PRESENTATION_EVENTS",
    "RetentionReport",
    "compact",
    "is_prunable",
    "plan",
    "retention_health",
)
