"""#613 Stage 7: decide whether legacy authoritative writes may be retired.

Stage 7's instruction is one sentence and every word of it is a precondition:

    Remove authoritative legacy writes only after usage telemetry is zero and
    migration/recovery tests pass.

That is a *decision*, not a code change, and the decision is what this module
implements. Retirement itself is then a one-line consequence of the answer --
which is the right shape, because the dangerous part of Stage 7 was never
deleting a write, it was deleting it too early.

Five preconditions, each of which can independently block:

1. **The journal is trustworthy.** A store reporting anything but ``complete``
   integrity is not a foundation to remove a fallback from.
2. **The journal is qualified.** Stage 4's gate: differences understood and
   classified. Retiring on unqualified evidence would be the same mistake as
   reading on it, with no way back.
3. **Enough runs to mean something.** One agreeing run is not a migration.
4. **Nothing is still reading legacy.** The literal telemetry the instruction
   names. A single legacy read means a caller would lose data.
5. **No unreconciled operations.** An external effect whose outcome is unknown
   is exactly the state the legacy record might still explain.

The bias is deliberate and one-directional: every uncertainty blocks. A false
"not ready" costs another week of dual writes. A false "ready" deletes the only
remaining copy of state that turns out to have been needed, and there is no
undo. Those are not symmetric, so the code is not symmetric.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import journal_operations, journal_reader, journal_store

#: Individual reasons retirement is refused. Named rather than counted, because
#: each one has a different fix and a caller that only knows "blocked" cannot
#: act on it.
BLOCK_NO_JOURNAL = "journal_absent"
BLOCK_INTEGRITY = "journal_not_complete"
BLOCK_UNQUALIFIED = "not_qualified"
BLOCK_SAMPLE = "insufficient_sample"
BLOCK_LEGACY_READS = "legacy_still_read"
BLOCK_UNRECONCILED = "operations_unreconciled"
#: The legacy record supplied nothing to compare against, so "no legacy
#: reads" is vacuously true rather than evidence.
BLOCK_NO_COMPARISON = "nothing_compared"

STATUS_READY = "ready"
STATUS_BLOCKED = "blocked"

#: How many journal-served runs count as evidence that the migration works.
#: Deliberately not 1: a single agreeing run proves the plumbing connects, not
#: that the migration holds. The number is a starting position for a human to
#: raise, not a claim that 20 is statistically meaningful.
DEFAULT_MINIMUM_RUNS = 20


@dataclass(frozen=True)
class RetirementReport:
    """Whether legacy writes may be retired, and precisely what stands in the way."""

    status: str
    blockers: tuple[str, ...] = field(default_factory=tuple)
    journal_reads: int = 0
    legacy_reads: int = 0
    unreconciled: int = 0
    compared_runs: int = 0
    integrity: str = ""
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.status == STATUS_READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": "opai-journal-retirement",
            "status": self.status,
            "ready": self.ready,
            "blockers": list(self.blockers),
            "journal_reads": self.journal_reads,
            "legacy_reads": self.legacy_reads,
            "unreconciled": self.unreconciled,
            "compared_runs": self.compared_runs,
            "integrity": self.integrity,
            "detail": self.detail,
        }


def assess(
    project_root: Path,
    legacy_runs: Mapping[str, Mapping[str, Any]],
    *,
    minimum_runs: int = DEFAULT_MINIMUM_RUNS,
    policy: journal_reader.ReaderPolicy | None = None,
) -> RetirementReport:
    """Answer Stage 7's question against real telemetry, not intention.

    Collects every blocker rather than returning at the first one. A caller
    told only "not ready, integrity" would fix integrity, re-run, and discover
    the sample was too small anyway -- so the report names everything standing
    in the way at once.
    """

    blockers: list[str] = []

    if not journal_store.journal_path(project_root).exists():
        return RetirementReport(
            status=STATUS_BLOCKED,
            blockers=(BLOCK_NO_JOURNAL,),
            detail="this project has no journal, so there is nothing to retire onto",
        )

    reader = journal_reader.JournalReader(
        project_root, legacy_runs, policy=policy or journal_reader.ReaderPolicy()
    )
    counts = reader.source_counts()
    journal_reads = int(counts.get(journal_reader.SOURCE_JOURNAL, 0))
    legacy_reads = int(counts.get(journal_reader.SOURCE_LEGACY, 0))

    integrity = ""
    connection = None
    try:
        connection = journal_store.open_store(project_root)
        report = journal_store.check_integrity(connection)
        integrity = report.state
        if report.state != journal_store.INTEGRITY_COMPLETE:
            # Degraded is *usable* for reading and still not enough to retire a
            # fallback on: the whole point of keeping legacy is to have
            # something left when the journal is imperfect.
            blockers.append(BLOCK_INTEGRITY)
    except Exception:  # noqa: BLE001 - any failure to read integrity blocks
        blockers.append(BLOCK_INTEGRITY)
    finally:
        if connection is not None:
            connection.close()

    if not reader.serving_from_journal:
        blockers.append(BLOCK_UNQUALIFIED)

    if journal_reads < max(1, int(minimum_runs)):
        blockers.append(BLOCK_SAMPLE)

    # An empty legacy record makes every other signal vacuously true: zero
    # legacy reads because there was nothing to read, and a qualification that
    # compared nothing to nothing. Retiring on that is the unevidenced cutover
    # Stage 4 exists to forbid -- and the likeliest way to reach it is a legacy
    # loader that failed and returned {} instead of raising, which is precisely
    # when deleting the fallback is most destructive.
    compared = reader.compared_runs()
    if compared < max(1, int(minimum_runs)):
        blockers.append(BLOCK_NO_COMPARISON)

    if legacy_reads:
        blockers.append(BLOCK_LEGACY_READS)

    summary = journal_operations.operation_summary(project_root)
    unreconciled = int(summary.get("unreconciled", 0))
    if unreconciled:
        blockers.append(BLOCK_UNRECONCILED)

    if blockers:
        return RetirementReport(
            status=STATUS_BLOCKED,
            blockers=tuple(dict.fromkeys(blockers)),
            journal_reads=journal_reads,
            legacy_reads=legacy_reads,
            unreconciled=unreconciled,
            compared_runs=compared,
            integrity=integrity,
            detail="; ".join(_explain(name) for name in dict.fromkeys(blockers)),
        )
    return RetirementReport(
        status=STATUS_READY,
        journal_reads=journal_reads,
        legacy_reads=legacy_reads,
        unreconciled=unreconciled,
        compared_runs=compared,
        integrity=integrity,
        detail=(
            f"{journal_reads} run(s) served from the journal, no legacy reads, "
            "no unreconciled operations"
        ),
    )


def _explain(blocker: str) -> str:
    return {
        BLOCK_NO_JOURNAL: "there is no journal to retire onto",
        BLOCK_INTEGRITY: "journal integrity is not complete",
        BLOCK_UNQUALIFIED: "the journal has not been qualified against the legacy record",
        BLOCK_SAMPLE: "too few runs have been served from the journal to be evidence",
        BLOCK_LEGACY_READS: "something is still reading the legacy record",
        BLOCK_UNRECONCILED: "external operations are still unreconciled",
        BLOCK_NO_COMPARISON: (
            "too few runs exist in both records, so the comparison proved nothing"
        ),
    }.get(blocker, blocker)


def legacy_writes_required(
    project_root: Path,
    legacy_runs: Mapping[str, Mapping[str, Any]],
    *,
    minimum_runs: int = DEFAULT_MINIMUM_RUNS,
) -> bool:
    """Whether a caller must still write the legacy record.

    The one call a write site needs. It answers ``True`` on every uncertainty,
    including every kind of failure, because the cost of a needless legacy
    write is a duplicated file and the cost of a wrongly-skipped one is state
    that exists nowhere.

    Deliberately not cached. A write site asking "should I still write this?"
    is asking about *now*, and a cached "no" that outlived the condition that
    justified it is precisely how a migration loses data quietly.
    """

    try:
        return not assess(project_root, legacy_runs, minimum_runs=minimum_runs).ready
    except Exception:  # noqa: BLE001 - never let a failed check skip a write
        return True


__all__: Sequence[str] = (
    "BLOCK_INTEGRITY",
    "BLOCK_LEGACY_READS",
    "BLOCK_NO_COMPARISON",
    "BLOCK_NO_JOURNAL",
    "BLOCK_SAMPLE",
    "BLOCK_UNQUALIFIED",
    "BLOCK_UNRECONCILED",
    "DEFAULT_MINIMUM_RUNS",
    "RetirementReport",
    "STATUS_BLOCKED",
    "STATUS_READY",
    "assess",
    "legacy_writes_required",
)
