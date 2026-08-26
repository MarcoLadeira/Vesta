"""#613 Stage 4: qualify the journal against the legacy record before trusting it.

Stage 4's instruction is short and strict: *"Reconstruct both ways against
golden and real captured fixtures. No switch until differences are understood
and classified."*

The switch it gates is Stage 5, where GUI, CLI and receipts start reading
journal projections instead of files. That is the moment a mistake stops being
a bookkeeping gap and starts being wrong answers shown to a user, so the bar
for crossing it is not "the journal looks right" -- it is "every difference
between the two has been seen and named".

This module makes that measurable rather than a judgement call.

**Differences are classified, not counted.** A raw count cannot distinguish
"the journal is missing runs the legacy record has" from "the journal has runs
the legacy record never knew about"; the first blocks a cutover and the second
is usually a legacy gap the journal just fixed. Every difference gets a
:class:`Difference` with a ``kind``, and the report is only *qualified* when
every kind present has been explicitly accepted.

**Absence is not agreement.** A project that never journalled anything would
otherwise qualify trivially, which is the most dangerous possible false pass --
it reports readiness for a cutover with no evidence at all. A report over an
empty journal is ``insufficient_evidence``, never ``qualified``.

**The comparison is one-directional about authority.** Legacy is still the
source of truth here, so a run the legacy record has and the journal does not
is a *missing* run -- a real gap. The reverse is ``extra``, which is
informational: the journal is written from a choke point that catches exits the
legacy path never recorded, so extras are expected early and are not by
themselves a reason to block.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import journal_store
from .journal_store import JournalStoreError, open_store

#: A run the legacy record has and the journal does not. Blocks a cutover:
#: reading from the journal would lose it.
KIND_MISSING = "missing_from_journal"

#: A run the journal has and the legacy record does not. Informational -- the
#: journal's choke point catches exits (a raising turn, a Ctrl-C) that the
#: legacy path never wrote down, so early extras are usually the journal being
#: *better*, not wrong.
KIND_EXTRA = "extra_in_journal"

#: Both have the run, and they disagree about how it ended. Always blocks: this
#: is the "individually plausible but mutually contradictory" state the issue
#: opens by describing.
KIND_VERDICT = "verdict_mismatch"

#: Both have the run, and the journal never recorded a terminal event. Blocks:
#: an unterminated run reads as still-running forever after a cutover.
KIND_UNTERMINATED = "unterminated_in_journal"

#: A run the legacy record has that *predates the journal*. Informational, and
#: the difference between a migration that can finish and one that cannot.
#:
#: Without this, every run recorded before the journal existed reads as
#: KIND_MISSING and blocks forever -- a real installation with months of
#: history could never qualify, so Stage 5 could never begin. Found by an
#: upgrade-path test rather than by inspection: the fresh-install tests all
#: passed because they had no history to predate anything.
KIND_PRE_MIGRATION = "predates_journal"

#: No journal evidence at all. Never qualifies.
KIND_NO_EVIDENCE = "no_evidence"

STATUS_QUALIFIED = "qualified"
STATUS_BLOCKED = "blocked"
STATUS_INSUFFICIENT = "insufficient_evidence"

#: Difference kinds that do not, on their own, prevent a cutover. Deliberately
#: a small set, and deliberately *not* configurable from outside: the point of
#: Stage 4 is that somebody decided each of these is acceptable and wrote down
#: why, not that a caller can widen the tolerance until the report goes green.
DEFAULT_ACCEPTED = (KIND_EXTRA, KIND_PRE_MIGRATION)


@dataclass(frozen=True)
class Difference:
    """One classified disagreement between the journal and the legacy record."""

    kind: str
    run_id: str
    journal: Any = None
    legacy: Any = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "run_id": self.run_id,
            "journal": self.journal,
            "legacy": self.legacy,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class QualificationReport:
    """Whether the journal may be trusted for reads, and what stands in the way."""

    status: str
    compared_runs: int
    differences: tuple[Difference, ...] = field(default_factory=tuple)
    accepted_kinds: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""

    @property
    def qualified(self) -> bool:
        return self.status == STATUS_QUALIFIED

    @property
    def blocking(self) -> tuple[Difference, ...]:
        """Only the differences that actually prevent a cutover."""

        return tuple(
            item for item in self.differences if item.kind not in self.accepted_kinds
        )

    def kind_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.differences:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": "opai-journal-qualification",
            "status": self.status,
            "qualified": self.qualified,
            "compared_runs": self.compared_runs,
            "kind_counts": self.kind_counts(),
            "accepted_kinds": list(self.accepted_kinds),
            "blocking_count": len(self.blocking),
            "differences": [item.to_dict() for item in self.differences],
            "detail": self.detail,
        }


def journal_runs(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Every run the journal knows about, keyed by run id.

    Read from the ``runs`` table rather than replayed from events, because the
    thing being qualified is what a Stage 5 reader would actually see. Replay
    determinism is already proven separately; qualifying the replay against
    itself would prove nothing about the cutover.
    """

    rows = connection.execute(
        "SELECT run_id, task_id, observed_state, terminal_verdict, terminal_reason"
        " FROM runs ORDER BY run_id"
    )
    return {str(row["run_id"]): dict(row) for row in rows}


def pre_migration_ids_from(
    legacy: Mapping[str, Mapping[str, Any]],
    boundary: str,
    *,
    timestamp_key: str = "created_at",
) -> frozenset[str]:
    """Legacy runs that started before the journal did.

    Timestamps are compared as strings, which is correct for ISO-8601 in a
    fixed offset and is what every writer in this codebase emits. A record
    with no usable timestamp is deliberately *excluded* -- it cannot be proven
    to predate the journal, and guessing in the permissive direction would let
    a genuinely lost run pass as "old", which is the one mistake this whole
    classification exists to prevent.
    """

    if not boundary:
        return frozenset()
    found = set()
    for run_id, record in legacy.items():
        stamp = record.get(timestamp_key)
        if isinstance(stamp, str) and stamp and stamp < boundary:
            found.add(str(run_id))
    return frozenset(found)


def journal_started_at(connection: sqlite3.Connection) -> str:
    """When this journal first recorded anything, or "" if it never has.

    The natural migration boundary: everything the legacy record holds from
    before this instant was written by a build that had no journal to write to.
    """

    row = connection.execute("SELECT MIN(recorded_at) FROM events").fetchone()
    return str(row[0]) if row and row[0] else ""


def compare(
    journal: Mapping[str, Mapping[str, Any]],
    legacy: Mapping[str, Mapping[str, Any]],
    *,
    verdict_equivalent: Mapping[str, str] | None = None,
    pre_migration_ids: Iterable[str] | None = None,
) -> list[Difference]:
    """Classify every disagreement between two views of the same runs.

    ``verdict_equivalent`` maps a legacy verdict onto the journal's vocabulary.
    It exists because the two sides were written years apart and use different
    words for the same ending; without it every run would look like a mismatch
    and the report would be noise. It is *not* a place to paper over a real
    disagreement -- each entry is a claim that two words mean one thing, and
    the caller has to make that claim explicitly.
    """

    equivalents = dict(verdict_equivalent or {})
    predates = frozenset(pre_migration_ids or ())
    differences: list[Difference] = []

    for run_id in sorted(set(legacy) - set(journal)):
        if run_id in predates:
            differences.append(
                Difference(
                    kind=KIND_PRE_MIGRATION,
                    run_id=run_id,
                    legacy=dict(legacy[run_id]),
                    detail="this run predates the journal, so its absence is expected",
                )
            )
            continue
        differences.append(
            Difference(
                kind=KIND_MISSING,
                run_id=run_id,
                legacy=dict(legacy[run_id]),
                detail="the legacy record has this run and the journal does not",
            )
        )

    for run_id in sorted(set(journal) - set(legacy)):
        differences.append(
            Difference(
                kind=KIND_EXTRA,
                run_id=run_id,
                journal=dict(journal[run_id]),
                detail="the journal has this run and the legacy record does not",
            )
        )

    for run_id in sorted(set(journal) & set(legacy)):
        entry = journal[run_id]
        other = legacy[run_id]
        if not isinstance(other, Mapping):
            # A legacy record that is not a mapping cannot be compared. It is a
            # difference in its own right -- something upstream produced a shape
            # nobody expects -- and crashing the whole comparison over one bad
            # row would hide every other finding in the report.
            differences.append(
                Difference(
                    kind=KIND_VERDICT,
                    run_id=run_id,
                    journal=dict(entry),
                    legacy=repr(other)[:200],
                    detail="the legacy record is not a mapping and cannot be compared",
                )
            )
            continue
        journal_verdict = str(entry.get("terminal_verdict") or "")
        legacy_verdict = str(other.get("terminal_verdict") or "")
        if not journal_verdict:
            differences.append(
                Difference(
                    kind=KIND_UNTERMINATED,
                    run_id=run_id,
                    journal=dict(entry),
                    legacy=dict(other),
                    detail="the journal never recorded how this run ended",
                )
            )
            continue
        normalised = equivalents.get(legacy_verdict, legacy_verdict)
        if normalised and normalised != journal_verdict:
            differences.append(
                Difference(
                    kind=KIND_VERDICT,
                    run_id=run_id,
                    journal=journal_verdict,
                    legacy=legacy_verdict,
                    detail="the two records disagree about how this run ended",
                )
            )
    return differences


def qualify(
    project_root: Path,
    legacy_runs: Mapping[str, Mapping[str, Any]],
    *,
    accepted_kinds: Iterable[str] = DEFAULT_ACCEPTED,
    verdict_equivalent: Mapping[str, str] | None = None,
    pre_migration_ids: Iterable[str] | None = None,
    minimum_runs: int = 1,
) -> QualificationReport:
    """Decide whether the journal is ready to be read from.

    ``minimum_runs`` is why an empty journal cannot qualify. Stage 4 is a
    statement about *evidence*, and no evidence is not weak evidence -- it is
    none. Defaulting to 1 keeps the rule honest at the smallest possible scale
    while letting a caller demand a real sample before a production cutover.
    """

    accepted = tuple(dict.fromkeys(accepted_kinds))
    try:
        connection = open_store(project_root)
    except (sqlite3.DatabaseError, JournalStoreError, OSError, ValueError) as exc:
        return QualificationReport(
            status=STATUS_INSUFFICIENT,
            compared_runs=0,
            accepted_kinds=accepted,
            detail=f"the journal could not be opened: {type(exc).__name__}",
        )
    try:
        integrity = journal_store.check_integrity(connection)
        if not integrity.usable:
            # A store that is not trustworthy cannot qualify, and saying
            # "blocked" here would imply the differences were examined. They
            # were not -- there was nothing examinable.
            return QualificationReport(
                status=STATUS_INSUFFICIENT,
                compared_runs=0,
                accepted_kinds=accepted,
                detail=f"journal integrity is {integrity.state}",
            )
        entries = journal_runs(connection)
    finally:
        connection.close()

    differences = tuple(
        compare(
            entries,
            legacy_runs,
            verdict_equivalent=verdict_equivalent,
            pre_migration_ids=pre_migration_ids,
        )
    )
    compared = len(set(entries) & set(legacy_runs))

    if len(entries) < max(1, int(minimum_runs)):
        return QualificationReport(
            status=STATUS_INSUFFICIENT,
            compared_runs=compared,
            differences=differences,
            accepted_kinds=accepted,
            detail=(
                f"the journal holds {len(entries)} run(s); "
                f"{max(1, int(minimum_runs))} required before a cutover"
            ),
        )

    blocking = [item for item in differences if item.kind not in accepted]
    if blocking:
        kinds = sorted({item.kind for item in blocking})
        return QualificationReport(
            status=STATUS_BLOCKED,
            compared_runs=compared,
            differences=differences,
            accepted_kinds=accepted,
            detail="unaccepted difference kinds: " + ", ".join(kinds),
        )
    return QualificationReport(
        status=STATUS_QUALIFIED,
        compared_runs=compared,
        differences=differences,
        accepted_kinds=accepted,
        detail=f"{compared} run(s) compared with no unaccepted differences",
    )


__all__: Sequence[str] = (
    "DEFAULT_ACCEPTED",
    "Difference",
    "KIND_EXTRA",
    "KIND_MISSING",
    "KIND_NO_EVIDENCE",
    "KIND_PRE_MIGRATION",
    "KIND_UNTERMINATED",
    "KIND_VERDICT",
    "QualificationReport",
    "STATUS_BLOCKED",
    "STATUS_INSUFFICIENT",
    "STATUS_QUALIFIED",
    "compare",
    "journal_runs",
    "journal_started_at",
    "pre_migration_ids_from",
    "qualify",
)
