"""Append-only, crash-safe journals with monotonic sequence numbers (#517).

Every durable run/step transition today is persisted by rewriting one whole
snapshot file (see ``workflow_runner.py``). An atomic replace makes each
individual rewrite safe — a reader never sees a torn file — but the *history*
of how a run got there only exists as an array embedded in that one file.
There is no independent, append-only record a corrupted or truncated snapshot
can be checked against, no monotonic order spanning a crash, and nothing to
replay from to prove two recoveries converge to the same state.

``vestahub.ledger`` already solved the hard part of this for usage events:
append-only JSONL, a monotonically increasing ``ledger_sequence``, atomic
fsync'd appends, and a recoverable "head" projection that tail-scans past a
stale cache instead of trusting it blindly. This module generalizes that
mechanism (via a caller-supplied ``reduce``/``empty`` pair, the same shape as
:func:`functools.reduce`) for any append-only, replayable run log, and adds
the two things #517 asks for that the ledger does not do:

**Quarantine, not guessing.** A crash can only ever corrupt the *tail* of an
append-only file — the last, possibly-unterminated line. Anything else that
fails to parse, or parses but fails the caller's ``validate``, is not a crash
artifact; it is unknown data sitting in the middle of otherwise-trusted
history. Silently skipping it (the ledger's current policy) would let a
reconstruction quietly diverge from what actually happened. This module
instead moves the whole journal aside into a quarantine directory with a
manifest recording why, and starts the caller from its last durable snapshot
(or an honestly-empty projection) instead — corruption becomes an observable
event, never a guess.

**Snapshot and compaction.** :func:`compact` persists the current projection
as a snapshot and rotates the journal to a fresh, empty segment, so a log
that is read on every turn does not grow without bound. Sequence numbers keep
counting up across the rotation — the next append after a compaction resumes
from the snapshot's sequence, not from one — and the rotated segment is
renamed, never deleted: compaction shortens the replay path, it does not
destroy evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .atomic_io import atomic_write_text, interprocess_transaction

JOURNAL_SCHEMA_VERSION = 1

Reduce = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
Empty = Callable[[], dict[str, Any]]
Validate = Callable[[dict[str, Any]], bool]


class JournalCorruption(RuntimeError):
    """A journal contained data that could not be trusted and was quarantined."""


@dataclass(frozen=True)
class Recovery:
    """The outcome of loading (and, if needed, repairing) one journal."""

    projection: dict[str, Any]
    sequence: int
    offset: int
    digest: str
    quarantined: bool
    quarantine_path: Path | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "offset": self.offset,
            "quarantined": self.quarantined,
            "quarantine_path": str(self.quarantine_path)
            if self.quarantine_path
            else None,
        }


def head_path(journal_path: Path) -> Path:
    return journal_path.with_name(journal_path.name + ".head.json")


def _lock_path(journal_path: Path) -> Path:
    return journal_path.with_name(journal_path.name + ".lock")


def _default_quarantine_dir(journal_path: Path) -> Path:
    return journal_path.parent / "quarantine"


def _default_snapshot_dir(journal_path: Path) -> Path:
    return journal_path.parent / "snapshots"


def _line_digest(raw: bytes) -> str:
    return hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()


def _sequence_of(record: Any) -> int | None:
    if not isinstance(record, dict):
        return None
    value = record.get("sequence")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _scan(
    journal_path: Path,
    *,
    start_offset: int,
    validate: Validate | None,
) -> tuple[list[tuple[dict[str, Any], str]], int, bool]:
    """Records found at or after ``start_offset``.

    Returns ``(records, end_offset, corrupt)``. ``corrupt`` is true only when
    a line that is *not* the file's final line fails to parse or fails
    ``validate`` — a crash can only ever leave the final line torn, so
    anything earlier that is unreadable is genuine corruption, not a crash
    artifact.
    """

    if not journal_path.exists():
        return [], 0, False
    size = journal_path.stat().st_size
    if start_offset < 0 or start_offset > size:
        return [], 0, True

    records: list[tuple[dict[str, Any], str]] = []
    end_offset = start_offset
    corrupt = False
    with journal_path.open("rb") as handle:
        handle.seek(start_offset)
        while raw := handle.readline():
            terminated = raw.endswith(b"\n")
            at_eof = handle.tell() >= size
            try:
                value = json.loads(raw.decode("utf-8"))
                sequence = _sequence_of(value)
                if sequence is None or (validate is not None and not validate(value)):
                    raise ValueError("record failed validation")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                if terminated and at_eof:
                    # The one shape a crash can produce: a garbage or
                    # half-written final line. Forgiven — but ``end_offset``
                    # deliberately does not advance past it, so the next
                    # append truncates it away rather than leaving it as
                    # noise a later full rescan could mistake for interior
                    # corruption.
                    continue
                if not terminated:
                    # Torn trailing bytes with no newline at all — same case.
                    break
                corrupt = True
                break
            end_offset = handle.tell()
            records.append((value, _line_digest(raw)))
            if not terminated:
                break
    return records, end_offset, corrupt


def _is_record_boundary(journal_path: Path, offset: int) -> bool:
    if offset == 0:
        return True
    if not journal_path.exists() or offset < 0 or offset > journal_path.stat().st_size:
        return False
    with journal_path.open("rb") as handle:
        handle.seek(offset - 1)
        return handle.read(1) == b"\n"


def _last_sequenced_before(journal_path: Path, offset: int) -> tuple[int, str]:
    if offset <= 0 or not journal_path.exists():
        return 0, ""
    position = min(offset, journal_path.stat().st_size)
    remainder = b""
    with journal_path.open("rb") as handle:
        while position > 0:
            amount = min(64 * 1024, position)
            position -= amount
            handle.seek(position)
            data = handle.read(amount) + remainder
            lines = data.split(b"\n")
            remainder = lines[0] if position else b""
            candidates = lines[1:] if position else lines
            for raw in reversed(candidates):
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                sequence = _sequence_of(value)
                if sequence is not None:
                    return sequence, _line_digest(raw)
    return 0, ""


def quarantine(
    journal_path: Path,
    *,
    reason: str,
    quarantine_dir: Path | None = None,
) -> Path:
    """Move a journal (and its head, if any) aside; never deletes evidence."""

    target_dir = quarantine_dir or _default_quarantine_dir(journal_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    unique = uuid.uuid4().hex[:8]
    destination = target_dir / f"{journal_path.name}.{stamp}.{unique}"
    manifest = {
        "quarantined_at": stamp,
        "reason": str(reason)[:500],
        "original_path": str(journal_path),
        "journal_size": journal_path.stat().st_size if journal_path.exists() else 0,
    }
    if journal_path.exists():
        journal_path.replace(destination)
    head = head_path(journal_path)
    if head.exists():
        head.replace(destination.with_name(destination.name + ".head.json"))
    atomic_write_text(
        destination.with_name(destination.name + ".manifest.json"),
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    return destination


def _read_head(journal_path: Path) -> dict[str, Any] | None:
    path = head_path(journal_path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema") != JOURNAL_SCHEMA_VERSION:
        return None
    sequence = value.get("sequence")
    offset = value.get("offset")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or not isinstance(value.get("digest"), str)
        or not isinstance(value.get("projection"), dict)
    ):
        return None
    return value


def _latest_snapshot(
    journal_path: Path, snapshot_dir: Path | None
) -> tuple[int, dict[str, Any]] | None:
    """The highest-sequence durable snapshot for ``journal_path``, if any."""

    directory = snapshot_dir or _default_snapshot_dir(journal_path)
    if not directory.is_dir():
        return None
    prefix = f"{journal_path.name}."
    suffix = ".snapshot.json"
    best: tuple[int, dict[str, Any]] | None = None
    for candidate in directory.iterdir():
        name = candidate.name
        if not (name.startswith(prefix) and name.endswith(suffix)):
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or value.get("schema") != JOURNAL_SCHEMA_VERSION:
            continue
        sequence = value.get("sequence")
        projection = value.get("projection")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
            or not isinstance(projection, dict)
        ):
            continue
        if best is None or sequence > best[0]:
            best = (sequence, dict(projection))
    return best


def _recover(
    journal_path: Path,
    *,
    reduce: Reduce,
    empty: Empty,
    validate: Validate | None,
    quarantine_dir: Path | None,
    snapshot_dir: Path | None,
) -> Recovery:
    baseline = _latest_snapshot(journal_path, snapshot_dir)
    baseline_sequence, baseline_projection = baseline if baseline else (0, empty())

    cached = _read_head(journal_path)
    if cached is not None and int(cached["sequence"]) >= baseline_sequence:
        offset = int(cached["offset"])
        prefix_sequence, prefix_digest = _last_sequenced_before(journal_path, offset)
        if (
            journal_path.exists()
            and offset <= journal_path.stat().st_size
            and _is_record_boundary(journal_path, offset)
            and prefix_sequence == int(cached["sequence"])
            and prefix_digest == str(cached["digest"])
        ):
            tail, end_offset, corrupt = _scan(
                journal_path, start_offset=offset, validate=validate
            )
            if corrupt:
                path = quarantine(
                    journal_path,
                    reason="corrupt record found while catching up a cached head",
                    quarantine_dir=quarantine_dir,
                )
                return Recovery(
                    baseline_projection, baseline_sequence, 0, "", True, path
                )
            projection = dict(cached["projection"])
            sequence = int(cached["sequence"])
            digest = str(cached["digest"])
            for record, record_digest in sorted(
                tail, key=lambda item: int(item[0]["sequence"])
            ):
                projection = reduce(projection, record)
                sequence = int(record["sequence"])
                digest = record_digest
            return Recovery(projection, sequence, end_offset, digest, False, None)
        # Cache present but unverifiable against the file: fall through to a
        # full rescan of the live segment, rebuilt on top of the snapshot.

    records, end_offset, corrupt = _scan(
        journal_path, start_offset=0, validate=validate
    )
    if corrupt:
        path = quarantine(
            journal_path,
            reason="corrupt or unrecognized record found during full recovery",
            quarantine_dir=quarantine_dir,
        )
        return Recovery(baseline_projection, baseline_sequence, 0, "", True, path)
    projection = baseline_projection
    sequence = baseline_sequence
    digest = ""
    for record, record_digest in sorted(
        records, key=lambda item: int(item[0]["sequence"])
    ):
        if int(record["sequence"]) <= baseline_sequence:
            continue
        projection = reduce(projection, record)
        sequence = int(record["sequence"])
        digest = record_digest
    return Recovery(projection, sequence, end_offset, digest, False, None)


def _append_line(
    journal_path: Path, record: dict[str, Any], *, truncate_to: int
) -> tuple[int, str]:
    """Write one record starting exactly at the last *confirmed* offset.

    Truncating to ``truncate_to`` before writing removes any unconfirmed tail
    a prior crash left behind (torn JSON, a forgiven garbage line) instead of
    writing past it. That keeps the physical file always equal to "every
    record recovery has confirmed, plus this one" — a later full rescan never
    finds resolved garbage sitting between two valid records.
    """

    journal_path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if not journal_path.exists():
        journal_path.touch()
    with journal_path.open("r+b") as handle:
        offset = max(0, truncate_to)
        handle.truncate(offset)
        handle.seek(offset)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
        end_offset = handle.tell()
    return end_offset, _line_digest(line)


def load(
    journal_path: Path,
    *,
    reduce: Reduce,
    empty: Empty,
    validate: Validate | None = None,
    quarantine_dir: Path | None = None,
    snapshot_dir: Path | None = None,
) -> Recovery:
    """The current projection, recovering past a stale or absent cache.

    Never raises on corruption — a corrupt journal is quarantined and
    recovery falls back to the last durable snapshot (or an honestly-empty
    projection) instead (see :attr:`Recovery.quarantined`). Only a caller that
    ignores that flag would silently treat quarantined data as normal.
    """

    with interprocess_transaction(_lock_path(journal_path)):
        return _recover(
            journal_path,
            reduce=reduce,
            empty=empty,
            validate=validate,
            quarantine_dir=quarantine_dir,
            snapshot_dir=snapshot_dir,
        )


def replay(
    journal_path: Path,
    *,
    reduce: Reduce,
    empty: Empty,
    validate: Validate | None = None,
    snapshot_dir: Path | None = None,
) -> dict[str, Any]:
    """Pure full replay, ignoring any cached head entirely.

    Starts from the latest durable snapshot (if any) and rescans the live
    segment from byte zero — it never trusts the head cache's own claimed
    projection, only its own reduction of the raw records. For proving replay
    determinism: this and :func:`load` must always agree, and calling this
    twice on the same journal must always agree with itself. Raises
    :class:`JournalCorruption` rather than quarantining, since a pure replay
    is a read-only check, not a recovery — it must not mutate disk.
    """

    baseline = _latest_snapshot(journal_path, snapshot_dir)
    baseline_sequence, baseline_projection = baseline if baseline else (0, empty())
    with interprocess_transaction(_lock_path(journal_path)):
        records, _end_offset, corrupt = _scan(
            journal_path, start_offset=0, validate=validate
        )
    if corrupt:
        raise JournalCorruption(f"corrupt record in journal: {journal_path}")
    projection = baseline_projection
    for record, _digest in sorted(records, key=lambda item: int(item[0]["sequence"])):
        if int(record["sequence"]) <= baseline_sequence:
            continue
        projection = reduce(projection, record)
    return projection


def append_if(
    journal_path: Path,
    build_event: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    reduce: Reduce,
    empty: Empty,
    validate: Validate | None = None,
    quarantine_dir: Path | None = None,
    snapshot_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Append one event chosen from the *current* projection, atomically.

    ``build_event`` receives the just-recovered projection — under the same
    lock that recovered it — and returns either the event to append, or
    ``None`` to append nothing. That is the difference between this and
    calling :func:`load` followed by :func:`append`: a caller that reads the
    projection first and decides afterward leaves a window between the read
    and the write for a second caller to act on the same stale read, and both
    append. Two threads racing to move the same not-yet-cancelled scope to
    ``cancelled`` are exactly this shape of bug — this closes it by making
    "read current state, decide, write" one atomic step.

    Returns ``None`` when ``build_event`` declined (nothing was appended).
    """

    with interprocess_transaction(_lock_path(journal_path)):
        recovery = _recover(
            journal_path,
            reduce=reduce,
            empty=empty,
            validate=validate,
            quarantine_dir=quarantine_dir,
            snapshot_dir=snapshot_dir,
        )
        event = build_event(recovery.projection)
        if event is None:
            return None
        if "sequence" in event:
            raise ValueError("event must not pre-assign a sequence")
        record = {**dict(event), "sequence": recovery.sequence + 1}
        if validate is not None and not validate(record):
            raise ValueError("event failed validation")
        offset, digest = _append_line(journal_path, record, truncate_to=recovery.offset)
        projection = reduce(recovery.projection, record)
        head = {
            "schema": JOURNAL_SCHEMA_VERSION,
            "sequence": record["sequence"],
            "offset": offset,
            "digest": digest,
            "projection": projection,
        }
        atomic_write_text(
            head_path(journal_path),
            json.dumps(head, indent=2, sort_keys=True, allow_nan=False) + "\n",
        )
    return record, projection


def append(
    journal_path: Path,
    event: Mapping[str, Any],
    *,
    reduce: Reduce,
    empty: Empty,
    validate: Validate | None = None,
    quarantine_dir: Path | None = None,
    snapshot_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Durably append one event, returning ``(event_with_sequence, projection)``.

    ``event`` must not itself contain ``sequence`` — the next monotonic value
    is assigned here, under the same lock used to recover the prior state, so
    two processes appending concurrently can never assign the same sequence.
    Unconditional: for an append whose *content* depends on the current
    projection (not just its sequence number), use :func:`append_if` instead.
    """

    if "sequence" in event:
        raise ValueError("event must not pre-assign a sequence")
    fixed = dict(event)
    result = append_if(
        journal_path,
        lambda _projection: fixed,
        reduce=reduce,
        empty=empty,
        validate=validate,
        quarantine_dir=quarantine_dir,
        snapshot_dir=snapshot_dir,
    )
    if result is None:
        # Unreachable: the callback above always returns ``fixed``, never
        # None. Guarded explicitly (not with `assert`, which vanishes under
        # `-O`) because a silent `None` here would surface as a confusing
        # `NoneType is not iterable` at the caller instead of this message.
        raise RuntimeError("append_if unexpectedly declined an unconditional append")
    return result


def compact(
    journal_path: Path,
    *,
    reduce: Reduce,
    empty: Empty,
    validate: Validate | None = None,
    quarantine_dir: Path | None = None,
    snapshot_dir: Path | None = None,
) -> Path:
    """Snapshot the current projection and rotate to a fresh journal segment.

    Sequence numbers keep counting up across the rotation: the next
    :func:`append` after this reads the new snapshot as its baseline, so it
    resumes at ``sequence + 1`` rather than restarting at one. The old segment
    is renamed alongside the new snapshot, never deleted — compaction
    shortens how far a future replay has to scan, it does not remove
    evidence. Safe to call on an empty or missing journal.
    """

    with interprocess_transaction(_lock_path(journal_path)):
        recovery = _recover(
            journal_path,
            reduce=reduce,
            empty=empty,
            validate=validate,
            quarantine_dir=quarantine_dir,
            snapshot_dir=snapshot_dir,
        )
        target_dir = snapshot_dir or _default_snapshot_dir(journal_path)
        target_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file = (
            target_dir / f"{journal_path.name}.{recovery.sequence}.snapshot.json"
        )
        atomic_write_text(
            snapshot_file,
            json.dumps(
                {
                    "schema": JOURNAL_SCHEMA_VERSION,
                    "sequence": recovery.sequence,
                    "projection": recovery.projection,
                },
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
        )
        if journal_path.exists():
            rotated = journal_path.with_name(
                f"{journal_path.name}.upto-{recovery.sequence}.segment"
            )
            journal_path.replace(rotated)
        head_path(journal_path).unlink(missing_ok=True)
        return snapshot_file
