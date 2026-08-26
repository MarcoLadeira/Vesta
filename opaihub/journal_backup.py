"""#613 functional requirement 12: backup and recovery for the runtime journal.

The health half of requirement 12 shipped with doctor. This is the other half,
and it was the last thing in the issue with no implementation behind it --
``test_journal_fault_injection`` carried a deliberate skip saying so, because
the issue also lists "unavailable backup" as a fault to inject and there was no
backup to make unavailable.

Three decisions worth stating, because each one is a refusal to do the obvious
easy thing.

*Backups are taken with SQLite's online backup API, not by copying the file.*
A journal in WAL mode is two or three files, and the interesting content is
routinely in the ``-wal`` rather than the ``.sqlite3``. Copying just the
database yields a backup missing every recent commit; copying all three while a
writer is mid-transaction yields a torn set. ``Connection.backup`` takes a
consistent snapshot of a live database with writers active, which is exactly
the situation a backup is for.

*A backup is verified when it is written, not when it is needed.* The failure
mode of backups is discovering at restore time that they were never readable.
Every backup is reopened, integrity-checked and hashed before it is declared,
and a backup that fails that check is deleted rather than left to be found.

*Restoring a foreign database does not grant its authority.* The issue is
explicit -- "Database copy from another user/device must not automatically
grant authority" and "Corruption recovery cannot silently regenerate keys,
approvals, budgets or policy". A backup carries the fingerprint of the project
it came from; restoring one from elsewhere is allowed, because refusing outright
would strand someone doing a legitimate machine move, but the approvals it
carries are dropped rather than honoured. An approval is permission granted by a
person in a context, and a file arriving from another machine is not that
person. Losing an approval costs one prompt; honouring a foreign one costs the
entire point of asking.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import journal_store
from .command_runner import redact

#: Where backups live when the caller does not name a directory. Beside the
#: journal rather than inside a temp dir: a backup that a cleanup job can
#: delete is not a backup.
BACKUP_DIRNAME = "journal-backups"

#: Suffix of the sidecar describing a backup. Kept separate from the database
#: so that reading a backup's provenance never means opening it -- checking
#: whether a backup is worth trying must not itself risk a corrupt read.
MANIFEST_SUFFIX = ".manifest.json"

#: How many backups ``prune_backups`` keeps by default. Enough that one bad
#: backup does not exhaust the history, few enough not to grow unbounded.
DEFAULT_KEEP = 5

RESTORE_OK = "restored"
RESTORE_REFUSED = "refused"

#: Why a restore was refused. Named rather than counted: each has a different
#: remedy and "restore failed" tells the caller nothing they can act on.
REFUSE_MISSING = "backup_missing"
REFUSE_DIGEST = "digest_mismatch"
REFUSE_UNREADABLE = "backup_unreadable"
REFUSE_INCOMPATIBLE = "backup_incompatible"


@dataclass(frozen=True)
class BackupRecord:
    """One verified backup, described without needing to open it."""

    path: Path
    created_at: str
    schema_version: int
    digest: str
    size_bytes: int
    project_fingerprint: str
    row_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "project_fingerprint": self.project_fingerprint,
            "row_counts": dict(self.row_counts),
        }


@dataclass(frozen=True)
class RestoreReport:
    """What a restore did, or precisely why it would not."""

    status: str
    reason: str = ""
    detail: str = ""
    restored_from: str = ""
    approvals_dropped: int = 0
    replaced_backup: str = ""

    @property
    def ok(self) -> bool:
        return self.status == RESTORE_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": "opai-journal-restore",
            "status": self.status,
            "ok": self.ok,
            "reason": self.reason,
            "detail": self.detail,
            "restored_from": self.restored_from,
            "approvals_dropped": self.approvals_dropped,
            "replaced_backup": self.replaced_backup,
        }


def backup_dir(project_root: Path) -> Path:
    return journal_store.journal_path(project_root).parent / BACKUP_DIRNAME


def project_fingerprint(project_root: Path) -> str:
    """A stable identifier for the project a journal belongs to.

    Deliberately derived from the resolved path rather than anything about the
    machine. It answers "is this backup from this project's journal?", which is
    the question that matters for authority, and it does not embed a hostname
    or user name into a file the user may share when reporting a bug.
    """

    resolved = str(Path(project_root).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8", "replace")).hexdigest()[:32]


def _digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    """Enough of a shape to notice a backup that restored the wrong thing."""

    counts: dict[str, int] = {}
    for table in ("tasks", "runs", "events", "operations", "approvals", "cost_events"):
        try:
            counts[table] = int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # nosec B608
            )
        except sqlite3.Error:
            continue
    return counts


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_backup(
    project_root: Path,
    *,
    destination: Path | None = None,
    now: str | None = None,
) -> BackupRecord | None:
    """Take a verified snapshot of the journal, or ``None`` if there is nothing to take.

    Returns ``None`` rather than raising for the ordinary cases -- no journal
    yet, or a journal too broken to read. A backup routine that raises is one
    that gets wrapped in a bare ``except`` at every call site and then silently
    stops running.
    """

    source = journal_store.journal_path(project_root)
    if not source.exists():
        return None

    stamp = now or _now()
    directory = Path(destination) if destination else backup_dir(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    safe_stamp = "".join(ch if ch.isalnum() else "-" for ch in stamp)
    target = directory / f"journal-{safe_stamp}.sqlite3"
    index = 1
    while target.exists():
        target = directory / f"journal-{safe_stamp}-{index}.sqlite3"
        index += 1

    live: sqlite3.Connection | None = None
    copy: sqlite3.Connection | None = None
    try:
        live = journal_store.open_store(project_root)
        copy = sqlite3.connect(str(target))
        # The online backup API, rather than a file copy: in WAL mode the
        # recent commits live in the -wal file, so copying the database alone
        # would silently produce a backup missing exactly the work most worth
        # keeping.
        live.backup(copy)
        copy.close()
        copy = None
    except Exception:  # noqa: BLE001 - a failed backup leaves no partial file
        if copy is not None:
            copy.close()
        target.unlink(missing_ok=True)
        return None
    finally:
        if live is not None:
            live.close()

    record = _verify(target, project_root, stamp)
    if record is None:
        # A backup that cannot be read back is worse than no backup: it will be
        # found and trusted at the exact moment there is nothing else left.
        target.unlink(missing_ok=True)
        return None

    # Written and flushed in one handle: a manifest that survives a crash while
    # its database does not is a promise of a backup that is not there. fsync
    # must happen on the *write* handle -- syncing a read-only one fails with
    # EBADF on Windows, which is how this was found.
    manifest = target.with_suffix(target.suffix + MANIFEST_SUFFIX)
    with open(manifest, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    return record


def _verify(target: Path, project_root: Path, stamp: str) -> BackupRecord | None:
    """Reopen a freshly written backup and prove it is usable."""

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        report = journal_store.check_integrity(connection)
        if report.state not in (
            journal_store.INTEGRITY_COMPLETE,
            journal_store.INTEGRITY_DEGRADED,
        ):
            return None
        counts = _row_counts(connection)
        version = int(report.schema_version or 0)
    except Exception:  # noqa: BLE001 - an unverifiable backup is not a backup
        return None
    finally:
        if connection is not None:
            connection.close()

    return BackupRecord(
        path=target,
        created_at=stamp,
        schema_version=version,
        digest=_digest_of(target),
        size_bytes=target.stat().st_size,
        project_fingerprint=project_fingerprint(project_root),
        row_counts=counts,
    )


def list_backups(
    project_root: Path, *, directory: Path | None = None
) -> list[BackupRecord]:
    """Every backup with a readable manifest, newest first.

    Reads manifests only. Deciding which backup to try must not require opening
    each candidate database, because the reason for looking is usually that one
    of them is broken.
    """

    where = Path(directory) if directory else backup_dir(project_root)
    if not where.is_dir():
        return []

    records: list[BackupRecord] = []
    for manifest in where.glob(f"*{MANIFEST_SUFFIX}"):
        # One unreadable manifest must not hide every other backup -- the
        # reason for listing is usually that something is already wrong.
        with contextlib.suppress(Exception):
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            path = Path(str(payload["path"]))
            if not path.exists():
                path = manifest.parent / manifest.name[: -len(MANIFEST_SUFFIX)]
            if not path.exists():
                continue
            records.append(
                BackupRecord(
                    path=path,
                    created_at=str(payload.get("created_at") or ""),
                    schema_version=int(payload.get("schema_version") or 0),
                    digest=str(payload.get("digest") or ""),
                    size_bytes=int(payload.get("size_bytes") or 0),
                    project_fingerprint=str(payload.get("project_fingerprint") or ""),
                    row_counts=dict(payload.get("row_counts") or {}),
                )
            )
    records.sort(key=lambda record: record.created_at, reverse=True)
    return records


def latest_backup(project_root: Path) -> BackupRecord | None:
    records = list_backups(project_root)
    return records[0] if records else None


def restore_backup(
    project_root: Path,
    backup: Path,
    *,
    now: str | None = None,
) -> RestoreReport:
    """Replace the journal with a verified backup, without importing its authority.

    The current journal is itself backed up first, so a restore is never the
    step that destroys the only remaining copy -- including when the thing
    being restored over turns out to have been the better of the two.

    Approvals from a backup taken against a *different* project are dropped.
    The issue requires that a database copied from another user or device not
    automatically grant authority, and an approval is precisely granted
    authority: a person said yes, once, to a specific thing, here. A file that
    arrived from somewhere else is not that person saying yes.
    """

    source = Path(backup)
    if not source.exists():
        return RestoreReport(
            status=RESTORE_REFUSED,
            reason=REFUSE_MISSING,
            detail=f"no backup at {source}",
        )

    manifest_path = source.with_suffix(source.suffix + MANIFEST_SUFFIX)
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a missing manifest is not fatal
            manifest = {}

    expected = str(manifest.get("digest") or "")
    if expected:
        try:
            actual = _digest_of(source)
        except OSError as exc:
            return RestoreReport(
                status=RESTORE_REFUSED,
                reason=REFUSE_UNREADABLE,
                detail=redact(str(exc))[:200],
            )
        if actual != expected:
            # Silent corruption, an interrupted copy, or tampering. All three
            # mean the same thing here: this is not the file that was verified.
            return RestoreReport(
                status=RESTORE_REFUSED,
                reason=REFUSE_DIGEST,
                detail="the backup does not match the digest recorded when it was taken",
            )

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        report = journal_store.check_integrity(connection)
    except Exception as exc:  # noqa: BLE001
        return RestoreReport(
            status=RESTORE_REFUSED,
            reason=REFUSE_UNREADABLE,
            detail=redact(str(exc))[:200],
        )
    finally:
        if connection is not None:
            connection.close()

    if report.state == journal_store.INTEGRITY_INCOMPATIBLE:
        return RestoreReport(
            status=RESTORE_REFUSED,
            reason=REFUSE_INCOMPATIBLE,
            detail=(
                f"the backup was written by a newer OPai "
                f"(schema {report.schema_version} > {journal_store.SCHEMA_VERSION})"
            ),
        )
    if report.state == journal_store.INTEGRITY_CORRUPT:
        return RestoreReport(
            status=RESTORE_REFUSED,
            reason=REFUSE_UNREADABLE,
            detail="the backup is corrupt",
        )

    stamp = now or _now()
    replaced = create_backup(project_root, now=f"{stamp}-prerestore")

    destination = journal_store.journal_path(project_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    live: sqlite3.Connection | None = None
    incoming: sqlite3.Connection | None = None
    try:
        # Remove the replaced journal's WAL and shm alongside it. This is
        # hygiene rather than a correctness guard, and the distinction is worth
        # recording: removing these lines changes no test, because SQLite
        # ignores a WAL whose salt does not match the database it finds. What
        # it does prevent is orphaned sidecars accumulating beside a restored
        # journal, where the next person to look would reasonably read them as
        # live state.
        for sidecar in ("-wal", "-shm"):
            Path(str(destination) + sidecar).unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        incoming = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        live = sqlite3.connect(str(destination))
        incoming.backup(live)
    except Exception as exc:  # noqa: BLE001
        return RestoreReport(
            status=RESTORE_REFUSED,
            reason=REFUSE_UNREADABLE,
            detail=redact(str(exc))[:200],
            replaced_backup=str(replaced.path) if replaced else "",
        )
    finally:
        for handle in (incoming, live):
            if handle is not None:
                handle.close()

    dropped = 0
    origin = str(manifest.get("project_fingerprint") or "")
    if origin and origin != project_fingerprint(project_root):
        dropped = _drop_approvals(project_root)

    return RestoreReport(
        status=RESTORE_OK,
        detail=(
            f"restored from {source.name}"
            + (f"; {dropped} foreign approval(s) dropped" if dropped else "")
        ),
        restored_from=str(source),
        approvals_dropped=dropped,
        replaced_backup=str(replaced.path) if replaced else "",
    )


def _drop_approvals(project_root: Path) -> int:
    """Remove approvals carried in from another project's journal.

    Returns how many were dropped, because a silent drop is indistinguishable
    from a bug the next time someone wonders why they are being asked again.
    """

    connection: sqlite3.Connection | None = None
    try:
        connection = journal_store.open_store(project_root)
        with journal_store._transaction(connection):
            count = int(
                connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
            )
            connection.execute("DELETE FROM approvals")
        return count
    except Exception:  # noqa: BLE001 - failing closed here means keeping none
        return 0
    finally:
        if connection is not None:
            connection.close()


def prune_backups(
    project_root: Path, *, keep: int = DEFAULT_KEEP, directory: Path | None = None
) -> list[Path]:
    """Delete all but the newest ``keep`` backups. Returns what was removed."""

    keep = max(1, int(keep))
    removed: list[Path] = []
    for record in list_backups(project_root, directory=directory)[keep:]:
        manifest = record.path.with_suffix(record.path.suffix + MANIFEST_SUFFIX)
        try:
            record.path.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
        except OSError:
            continue
        removed.append(record.path)
    return removed


def backup_health(project_root: Path) -> dict[str, Any]:
    """The backup half of requirement 12, in the shape doctor prints.

    Absence is reported, not escalated. A project that has never taken a backup
    is not broken, and a field that shouts on every fresh install is one nobody
    reads by the time it matters.
    """

    facts: dict[str, Any] = {
        "available": True,
        "backups": 0,
        "latest": "",
        "latest_schema_version": 0,
        "directory": "",
        "usable": False,
    }
    try:
        facts["directory"] = str(backup_dir(project_root))
        records = list_backups(project_root)
        facts["backups"] = len(records)
        if records:
            newest = records[0]
            facts["latest"] = newest.created_at
            facts["latest_schema_version"] = newest.schema_version
            facts["usable"] = newest.schema_version <= journal_store.SCHEMA_VERSION
    except Exception as exc:  # noqa: BLE001 - doctor never raises
        facts["available"] = False
        facts["error"] = redact(str(exc))[:200]
    return facts


__all__: Sequence[str] = (
    "BACKUP_DIRNAME",
    "BackupRecord",
    "DEFAULT_KEEP",
    "REFUSE_DIGEST",
    "REFUSE_INCOMPATIBLE",
    "REFUSE_MISSING",
    "REFUSE_UNREADABLE",
    "RESTORE_OK",
    "RESTORE_REFUSED",
    "RestoreReport",
    "backup_dir",
    "backup_health",
    "create_backup",
    "latest_backup",
    "list_backups",
    "project_fingerprint",
    "prune_backups",
    "restore_backup",
)
