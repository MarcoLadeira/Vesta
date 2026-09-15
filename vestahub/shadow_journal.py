"""Shadow-journal a legacy durable file, without making the journal authoritative (#613).

#613 Stage 2 asks for one thing across every ``JOURNAL_OWNED`` module in Stage
1's inventory: *"existing path remains authoritative; every state change also
records a canonical event; compare legacy and journal projections and record
contradictions."*

The first two migrations (``vestahub.owner_lease``, ``vestahub.worktree_leases``)
each hand-rolled that, and the second one re-derived the first one's shape
almost line for line. This is that shape, extracted once, so the remaining
modules are a few lines each rather than a fresh opportunity to get the same
details wrong.

**What a caller still owns.** The legacy write, and the lock it happens under.
This module never decides anything and never writes the authoritative file --
it mirrors a decision that has already been made and already been persisted.

**Two things this encodes that were learned the hard way.**

*The journal cannot live beside the file it shadows.* ``run_journal``'s head
cache is unconditionally named ``<name>.head.json``, and modules that keep
records in a flat directory list them with a non-recursive ``*.json`` glob --
so a sibling journal silently poisons the legacy listing with a filename that
is not a record id. Found by ``worktree_leases``'s existing tests, not by
inspection. :func:`journal_path_for` puts journals in a dedicated
subdirectory, structurally outside such a glob.

*The comparator must read both sides under the writer's own lock.* An earlier
draft tolerated a one-apart fence, to paper over a race between an unlocked
reader and an in-flight write. That tolerance inspected only the fence, so a
genuinely corrupt record whose fence happened to land one away would have
passed as "just lagging" -- defeating the one thing the comparator exists for.
Taking the lock removes the race instead of tolerating it, and lets the
comparison be exact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from . import run_journal
from .atomic_io import interprocess_transaction

#: Subdirectory every shadow journal lives in, relative to the record it
#: shadows. Never the record's own directory -- see the module docstring.
JOURNAL_SUBDIR = "journal"

#: The one event type this module writes. Whole-record mirroring is the shape
#: every migrated module has needed so far: the legacy write is a complete
#: overwrite, so the latest event's payload *is* the projection and no
#: field-level reduce logic is required.
SNAPSHOT_EVENT = "record_saved"

#: A record the owning module deleted. Snapshots alone cannot express this: a
#: module that removes a record when its work finishes would leave the shadow
#: asserting the last state forever, so every completed record would read as a
#: contradiction against an absent file. A tombstone reduces the projection
#: back to ``{}``, which is exactly what the legacy reader sees once the file
#: is gone -- so deletion becomes agreement rather than permanent noise.
DELETION_EVENT = "record_deleted"


def journal_path_for(record_path: Path | str) -> Path:
    """The shadow journal for the legacy record at ``record_path``."""

    path = Path(record_path)
    return path.parent / JOURNAL_SUBDIR / (path.stem + ".journal.jsonl")


def _empty() -> dict[str, Any]:
    return {}


def _reduce(_projection: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    if event.get("type") == DELETION_EVENT:
        return {}
    return dict(event["record"])


def _validator(
    is_valid_record: Callable[[Mapping[str, Any]], bool] | None,
) -> Callable[[dict[str, Any]], bool]:
    def validate(event: dict[str, Any]) -> bool:
        kind = event.get("type")
        if kind == DELETION_EVENT:
            # A tombstone carries no record to validate; its whole content is
            # the fact that the record is gone.
            return True
        if kind != SNAPSHOT_EVENT:
            return False
        record = event.get("record")
        if not isinstance(record, Mapping):
            return False
        return True if is_valid_record is None else bool(is_valid_record(record))

    return validate


def record_snapshot(
    record_path: Path | str,
    record: Mapping[str, Any],
    *,
    is_valid_record: Callable[[Mapping[str, Any]], bool] | None = None,
) -> None:
    """Mirror an already-written record. Never raises.

    Call this from *inside* the same lock the legacy write happened under, so
    the journal can never observe writes in a different order than the file
    did.

    Swallowing every failure is deliberate and is the whole reason this is
    safe to add to a liveness-critical path: the record is already durably
    written and the caller's result does not depend on this. Failing a real
    state transition because its shadow could not be appended would be a
    worse outcome than the missing shadow entry -- and the contradiction
    report is what makes such a gap visible rather than silent.
    """

    try:
        run_journal.append(
            journal_path_for(record_path),
            {"type": SNAPSHOT_EVENT, "record": dict(record)},
            reduce=_reduce,
            empty=_empty,
            validate=_validator(is_valid_record),
        )
    except Exception:  # nosec B110 - shadow evidence, never authoritative
        pass


def record_deletion(record_path: Path | str) -> None:
    """Mirror an already-performed deletion. Never raises.

    Call this from inside the same lock the legacy delete happened under, for
    the same ordering reason :func:`record_snapshot` needs it.

    Without this, a module that removes a record when its work finishes would
    leave the shadow asserting the last state forever, and every completed
    record would read as a contradiction against an absent file -- turning the
    dual read into constant noise precisely where it should be quiet.
    """

    try:
        run_journal.append(
            journal_path_for(record_path),
            {"type": DELETION_EVENT},
            reduce=_reduce,
            empty=_empty,
            validate=_validator(None),
        )
    except Exception:  # nosec B110 - shadow evidence, never authoritative
        pass


def projection(
    record_path: Path | str,
    *,
    is_valid_record: Callable[[Mapping[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """The state the shadow journal alone would reconstruct. Never raises.

    ``{}`` for an absent journal, and for one whose corruption
    :func:`run_journal.load` quarantined -- both mean "the shadow has nothing
    trustworthy to say", which is exactly what a contradiction report should
    surface rather than crash on.
    """

    try:
        return run_journal.load(
            journal_path_for(record_path),
            reduce=_reduce,
            empty=_empty,
            validate=_validator(is_valid_record),
        ).projection
    except Exception:
        return {}


def contradiction_report(
    record_path: Path | str,
    read_legacy: Callable[[], Mapping[str, Any]],
    *,
    is_valid_record: Callable[[Mapping[str, Any]], bool] | None = None,
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """``None`` when the legacy record and its shadow agree; else what differs.

    ``read_legacy`` must read the raw persisted record *without* the strict
    validation a module's own loader applies. A loader that raises on anything
    malformed is right for every other caller -- they need a record they can
    trust or an explicit error -- but here it would raise past the very
    divergence this is meant to report.

    Both sides are read under ``record_path``'s own lock, so they are one
    consistent snapshot rather than a mix of before-and-after some other
    process's in-flight write.
    """

    path = Path(record_path)
    with interprocess_transaction(path):
        try:
            legacy = dict(read_legacy())
        except Exception:
            legacy = {}
        shadow = projection(path, is_valid_record=is_valid_record)
    if legacy == shadow:
        return None
    mismatched = sorted(
        {*legacy.keys(), *shadow.keys()}
        - {key for key in legacy if legacy.get(key) == shadow.get(key)}
    )
    report: dict[str, Any] = {
        "path": str(path),
        "legacy": legacy,
        "shadow": shadow,
        "mismatched_fields": mismatched,
    }
    if identity:
        report.update(identity)
    return report
