"""#613 Stage 5: read a run's state from the journal, but only when qualified.

Stage 5 is the cutover: GUI, CLI and receipts stop reading files and start
reading journal projections *for migrated runs*. Stage 4's instruction gates it
-- "no switch until differences are understood and classified" -- and this
module is where that gate becomes code rather than intention.

So the reader does not simply prefer the journal. It asks
:func:`journal_qualification.qualify` first, and serves the legacy record
whenever the journal is not qualified. A read path that silently trusted an
unqualified journal would be exactly the failure #613 exists to remove, wearing
the costume of progress.

Three rules this module does not bend.

**Every read says where it came from.** :class:`RunView` carries ``source`` and
``integrity``. A caller that cannot tell a journal read from a legacy read
cannot reason about what it is showing, and a receipt that cannot name its
source is not evidence. It is also what makes Stage 7 possible: retiring a
legacy write is only safe once telemetry shows nothing reads it, and that
telemetry is this field.

**Degraded is served, but never silently.** The store's ``usable`` contract
already treats ``degraded`` as actionable -- some rows are unreadable, the
critical state is not unknown -- so refusing to read would be an overreaction.
The view says ``degraded`` and carries the first bad sequence.

**Falling back is not failing.** A run the journal never heard of is not an
error; it is a run from before the migration. It reads from legacy, reports
``source="legacy"``, and nobody is alarmed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import journal_qualification, journal_store
from .journal_store import JournalStoreError, open_store

SOURCE_JOURNAL = "journal"
SOURCE_LEGACY = "legacy"
SOURCE_ABSENT = "absent"

#: Why a read fell back to the legacy record. Recorded rather than inferred,
#: because "the journal was not used" has several causes with different fixes:
#: a store that is not there yet, a store that is not trustworthy, evidence
#: that has not been qualified, and a run that predates the migration.
FALLBACK_NO_STORE = "journal_unavailable"
FALLBACK_UNUSABLE = "journal_integrity"
FALLBACK_UNQUALIFIED = "not_qualified"
FALLBACK_UNKNOWN_RUN = "run_not_in_journal"


@dataclass(frozen=True)
class RunView:
    """One run's state, plus an honest account of where it came from."""

    run_id: str
    source: str
    state: Mapping[str, Any] | None = None
    integrity: str = journal_store.INTEGRITY_COMPLETE
    first_invalid_sequence: int | None = None
    fallback_reason: str = ""

    @property
    def found(self) -> bool:
        return self.state is not None

    @property
    def from_journal(self) -> bool:
        return self.source == SOURCE_JOURNAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source": self.source,
            "found": self.found,
            "state": dict(self.state) if self.state is not None else None,
            "integrity": self.integrity,
            "first_invalid_sequence": self.first_invalid_sequence,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True)
class ReaderPolicy:
    """When the journal may be read from, decided once rather than per call.

    ``require_qualification`` is the Stage 4 gate. Turning it off is a
    deliberate act with a name, not a default someone drifts into: a caller
    that disables it is asserting it has other evidence, and that assertion is
    visible in the code that made it.
    """

    require_qualification: bool = True
    minimum_runs: int = 1
    #: Legacy field naming when the run started, used to tell a run that
    #: predates the journal from one the journal lost. Without this every
    #: pre-migration run reads as missing and blocks the cutover forever.
    legacy_timestamp_key: str = "created_at"
    accepted_kinds: tuple[str, ...] = field(
        default_factory=lambda: tuple(journal_qualification.DEFAULT_ACCEPTED)
    )
    verdict_equivalent: Mapping[str, str] = field(default_factory=dict)


class JournalReader:
    """Serves run state from the journal when qualified, from legacy otherwise.

    Qualification is computed **once per reader**, not per read. Two reasons.
    It is a whole-store judgement, so recomputing it per run would let one read
    see a different answer than the read beside it -- and inconsistency is what
    #613 is trying to end, not something a reader should introduce. It is also
    a full comparison, so per-call qualification would make every read pay for
    it.
    """

    def __init__(
        self,
        project_root: Path,
        legacy_runs: Mapping[str, Mapping[str, Any]],
        *,
        policy: ReaderPolicy | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.legacy_runs = dict(legacy_runs)
        self.policy = policy or ReaderPolicy()
        self._report: journal_qualification.QualificationReport | None = None
        self._journal: dict[str, dict[str, Any]] | None = None
        self._integrity = journal_store.INTEGRITY_COMPLETE
        self._first_invalid: int | None = None
        self._blocked_reason = ""
        self._boundary = ""
        self._load()

    # -- setup ------------------------------------------------------------
    def _load(self) -> None:
        try:
            connection = open_store(self.project_root)
        except (sqlite3.DatabaseError, JournalStoreError, OSError, ValueError):
            self._blocked_reason = FALLBACK_NO_STORE
            return
        try:
            report = journal_store.check_integrity(connection)
            self._integrity = report.state
            self._first_invalid = report.first_invalid_sequence
            if not report.usable:
                self._blocked_reason = FALLBACK_UNUSABLE
                return
            self._journal = journal_qualification.journal_runs(connection)
            # Everything the legacy record holds from before the journal's
            # first event was written by a build that had no journal. Treating
            # those as "missing" would block a real installation forever.
            self._boundary = journal_qualification.journal_started_at(connection)
        except (sqlite3.DatabaseError, JournalStoreError):
            self._blocked_reason = FALLBACK_UNUSABLE
            return
        finally:
            connection.close()

        if not self.policy.require_qualification:
            return
        self._report = journal_qualification.qualify(
            self.project_root,
            self.legacy_runs,
            accepted_kinds=self.policy.accepted_kinds,
            verdict_equivalent=self.policy.verdict_equivalent,
            pre_migration_ids=journal_qualification.pre_migration_ids_from(
                self.legacy_runs,
                self._boundary,
                timestamp_key=self.policy.legacy_timestamp_key,
            ),
            minimum_runs=self.policy.minimum_runs,
        )
        if not self._report.qualified:
            self._blocked_reason = FALLBACK_UNQUALIFIED

    # -- reads ------------------------------------------------------------
    @property
    def qualification(self) -> journal_qualification.QualificationReport | None:
        """The report the cutover decision was made on, for diagnostics."""

        return self._report

    @property
    def serving_from_journal(self) -> bool:
        return not self._blocked_reason and self._journal is not None

    def read_run(self, run_id: str) -> RunView:
        """One run's state, from the journal when allowed and legacy otherwise."""

        if self.serving_from_journal:
            entry = (self._journal or {}).get(run_id)
            if entry is not None:
                return RunView(
                    run_id=run_id,
                    source=SOURCE_JOURNAL,
                    state=dict(entry),
                    integrity=self._integrity,
                    first_invalid_sequence=self._first_invalid,
                )
            return self._legacy_view(run_id, FALLBACK_UNKNOWN_RUN)
        return self._legacy_view(run_id, self._blocked_reason or FALLBACK_NO_STORE)

    def _legacy_view(self, run_id: str, reason: str) -> RunView:
        legacy = self.legacy_runs.get(run_id)
        return RunView(
            run_id=run_id,
            source=SOURCE_LEGACY if legacy is not None else SOURCE_ABSENT,
            state=dict(legacy) if legacy is not None else None,
            integrity=self._integrity,
            first_invalid_sequence=self._first_invalid,
            fallback_reason=reason,
        )

    def compared_runs(self) -> int:
        """How many runs exist in *both* records.

        Stage 7's evidence measure, and distinct from ``source_counts`` -- a
        run served from the journal that the legacy record never held was never
        actually checked against anything. Only the overlap was.
        """

        return len(set(self.legacy_runs) & set(self._journal or {}))

    def read_all(self) -> dict[str, RunView]:
        """Every run either side knows about, so a listing loses nothing.

        The union matters: reading only the journal's runs after a cutover
        would drop pre-migration history from a list view, which users would
        experience as their past disappearing.
        """

        ids = set(self.legacy_runs) | set(self._journal or {})
        return {run_id: self.read_run(run_id) for run_id in sorted(ids)}

    def source_counts(self) -> dict[str, int]:
        """Telemetry for Stage 7, which retires legacy only at zero reads."""

        counts = {SOURCE_JOURNAL: 0, SOURCE_LEGACY: 0, SOURCE_ABSENT: 0}
        for view in self.read_all().values():
            counts[view.source] = counts.get(view.source, 0) + 1
        return counts

    def diagnostics(self) -> dict[str, Any]:
        """What a doctor or receipt needs to explain this reader's behaviour."""

        return {
            "report": "vesta-journal-reader",
            "serving_from_journal": self.serving_from_journal,
            "integrity": self._integrity,
            "first_invalid_sequence": self._first_invalid,
            "fallback_reason": self._blocked_reason,
            "qualification": self._report.to_dict() if self._report else None,
            "source_counts": self.source_counts(),
        }


def read_run(
    project_root: Path,
    run_id: str,
    legacy_runs: Mapping[str, Mapping[str, Any]],
    *,
    policy: ReaderPolicy | None = None,
) -> RunView:
    """One-shot convenience read. Prefer :class:`JournalReader` for many runs."""

    return JournalReader(project_root, legacy_runs, policy=policy).read_run(run_id)


def legacy_runs_from(
    records: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    *,
    run_id_key: str = "run_id",
    verdict: Callable[[Mapping[str, Any]], str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Normalise a legacy source into the shape the comparison expects.

    Legacy records come from several subsystems with different field names, and
    leaving each caller to hand-shape a dict would guarantee two of them shape
    it differently -- which surfaces as phantom verdict mismatches that are
    really adapter bugs.
    """

    if isinstance(records, Mapping):
        items = [{**dict(value), run_id_key: key} for key, value in records.items()]
    else:
        items = [dict(item) for item in records]

    out: dict[str, dict[str, Any]] = {}
    for item in items:
        run_id = str(item.get(run_id_key) or "")
        if not run_id:
            continue
        entry = dict(item)
        if verdict is not None:
            entry["terminal_verdict"] = verdict(item)
        out[run_id] = entry
    return out


__all__: Sequence[str] = (
    "FALLBACK_NO_STORE",
    "FALLBACK_UNKNOWN_RUN",
    "FALLBACK_UNQUALIFIED",
    "FALLBACK_UNUSABLE",
    "JournalReader",
    "ReaderPolicy",
    "RunView",
    "SOURCE_ABSENT",
    "SOURCE_JOURNAL",
    "SOURCE_LEGACY",
    "legacy_runs_from",
    "read_run",
)
