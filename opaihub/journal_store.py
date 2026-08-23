"""#613: the transactional local runtime journal, in SQLite WAL.

Stage 2 gave every JOURNAL_OWNED record a shadow journal and a dual read, but
those shadows are per-record JSONL files. They prove a record *can* be rebuilt;
they cannot give #613 what it actually asks for -- "one transactional local
history" with a monotonic sequence across records, operation identity, fencing
and typed integrity. That needs a single store, and the issue names SQLite in
WAL mode as the default.

WAL is the reason this is SQLite rather than more JSONL. Readers do not block
the writer and the writer does not block readers, which is exactly the shape of
the problem: a GUI and a CLI rehydrating while a worker commits critical state.
Functional requirement 8 -- concurrent readers must not block critical writes --
is a property of the storage engine here, not something layered on top.

Three things this module is deliberately strict about:

*No arbitrary SQL escapes it.* Functional requirement 1 asks for a small typed
API rather than SQL scattered through the application. Every statement lives
here and every value is bound as a parameter, which is also the security
requirement: no dynamic untrusted SQL.

*Corruption is typed, never emptied.* Requirement 8 says corruption must not
become permissive default state. :class:`IntegrityReport` distinguishes
complete / degraded / incompatible / corrupt and carries the first bad
sequence, so a caller can refuse to act rather than proceed on an empty read.

*A database newer than the application is incompatible, not corrupt.* Those are
different failures with different responses -- one wants an upgrade, the other
wants recovery -- and collapsing them would send the user down the wrong path.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .state import state_dir

#: Bumped whenever :data:`_MIGRATIONS` grows. A database reporting a higher
#: version than this was written by a newer OPai and is *incompatible* -- a
#: state the caller must be able to tell apart from corruption.
SCHEMA_VERSION = 1

#: Typed integrity outcomes (functional requirement 7).
INTEGRITY_COMPLETE = "complete"
INTEGRITY_DEGRADED = "degraded"
INTEGRITY_INCOMPATIBLE = "incompatible"
INTEGRITY_CORRUPT = "corrupt"

_PRIVACY_CLASSES = ("public", "internal", "sensitive", "secret_reference")
_OPERATION_STATES = (
    "intended",
    "executing",
    "observed",
    "reconciled",
    "uncertain",
)
_COST_KINDS = ("actual", "derived", "estimated", "unavailable")


class JournalStoreError(RuntimeError):
    """Raised when the journal cannot be opened or written safely."""


class IncompatibleSchemaError(JournalStoreError):
    """The database was written by a newer OPai than this one."""


class StaleWriterError(JournalStoreError):
    """A fenced-out process attempted a write after lease takeover."""


@dataclass(frozen=True)
class IntegrityReport:
    """Typed read integrity, never an empty-and-permissive fallback."""

    state: str
    schema_version: int
    first_invalid_sequence: int | None = None
    detail: str = ""
    checks: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        """Whether a caller may treat the store as authoritative.

        ``degraded`` is deliberately usable: it means some high-volume rows
        were dropped or quarantined, not that the critical state is unknown.
        ``incompatible`` and ``corrupt`` are not usable, and the caller is
        expected to surface that rather than fall back to defaults.
        """

        return self.state in (INTEGRITY_COMPLETE, INTEGRITY_DEGRADED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "schema_version": self.schema_version,
            "first_invalid_sequence": self.first_invalid_sequence,
            "detail": self.detail,
            "checks": list(self.checks),
        }


def journal_path(project_root: Path) -> Path:
    return state_dir(Path(project_root).expanduser().resolve()) / "journal.sqlite3"


# --------------------------------------------------------------------------
# Schema
#
# One migration list, applied in order inside a transaction. Each entry is
# (version, statements). Migrations are append-only: editing a shipped one
# would leave already-migrated databases silently different from fresh ones.
# --------------------------------------------------------------------------

_MIGRATION_1 = (
    # Monotonicity is delegated to AUTOINCREMENT rather than a counter this
    # module maintains: AUTOINCREMENT additionally guarantees a sequence is
    # never *reused* after a delete, which a MAX(seq)+1 scheme does not. #613
    # asks for deterministic ordering across rebuilds, and a reused sequence
    # would make two different events indistinguishable in a replay.
    """
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        origin_surface TEXT NOT NULL,
        origin_session TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        intent_revision INTEGER NOT NULL DEFAULT 1,
        repository_ref TEXT NOT NULL DEFAULT '',
        policy_digest TEXT NOT NULL DEFAULT '',
        requested_outcome TEXT NOT NULL DEFAULT '',
        schema_version INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        desired_state TEXT NOT NULL,
        observed_state TEXT NOT NULL,
        lease_fence INTEGER NOT NULL DEFAULT 0,
        route TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        budget_ref TEXT NOT NULL DEFAULT '',
        deadline_at TEXT,
        terminal_verdict TEXT,
        terminal_reason TEXT,
        predecessor_run_id TEXT REFERENCES runs(run_id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (task_id, attempt)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT REFERENCES runs(run_id),
        event_type TEXT NOT NULL,
        event_schema_version INTEGER NOT NULL,
        occurred_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        producer TEXT NOT NULL,
        parent_sequence INTEGER REFERENCES events(sequence),
        payload TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        privacy_class TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS events_by_run ON events(run_id, sequence)",
    """
    CREATE TABLE IF NOT EXISTS operations (
        operation_key TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        target_digest TEXT NOT NULL,
        authority_revision INTEGER NOT NULL DEFAULT 1,
        state TEXT NOT NULL,
        run_id TEXT REFERENCES runs(run_id),
        provider_ref TEXT NOT NULL DEFAULT '',
        process_ref TEXT NOT NULL DEFAULT '',
        external_ref TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        reconciled_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS approvals (
        fingerprint TEXT PRIMARY KEY,
        actor TEXT NOT NULL,
        policy_digest TEXT NOT NULL DEFAULT '',
        repository_ref TEXT NOT NULL DEFAULT '',
        run_id TEXT REFERENCES runs(run_id),
        operation_key TEXT REFERENCES operations(operation_key),
        nonce TEXT NOT NULL UNIQUE,
        issued_at TEXT NOT NULL,
        expires_at TEXT,
        consumed_at TEXT,
        revoked_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        content_hash TEXT NOT NULL,
        operation_key TEXT REFERENCES operations(operation_key),
        kind TEXT NOT NULL,
        identity TEXT NOT NULL,
        privacy_class TEXT NOT NULL,
        retention TEXT NOT NULL DEFAULT 'keep',
        exported_at TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (content_hash, identity)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cost_events (
        cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_key TEXT NOT NULL REFERENCES operations(operation_key),
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        quantity REAL NOT NULL DEFAULT 0,
        price_snapshot TEXT NOT NULL DEFAULT '',
        measurement_kind TEXT NOT NULL,
        reconciliation_state TEXT NOT NULL DEFAULT 'pending',
        uncertainty TEXT NOT NULL DEFAULT '',
        recorded_at TEXT NOT NULL
    )
    """,
    # One cost event per operation: #613's property tests require that no cost
    # is attributed more than once, and a UNIQUE index makes double-attribution
    # an insert failure rather than something a later audit has to notice.
    "CREATE UNIQUE INDEX IF NOT EXISTS cost_events_one_per_operation "
    "ON cost_events(operation_key)",
    """
    CREATE TABLE IF NOT EXISTS leases (
        run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
        owner TEXT NOT NULL,
        fence INTEGER NOT NULL CHECK (fence >= 1),
        acquired_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        expires_at TEXT,
        released_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS projections (
        projection_type TEXT NOT NULL,
        projection_version INTEGER NOT NULL,
        source_sequence INTEGER NOT NULL DEFAULT 0,
        payload TEXT NOT NULL,
        rebuilt_at TEXT NOT NULL,
        PRIMARY KEY (projection_type, projection_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)

_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = ((1, _MIGRATION_1),)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    try:
        # WAL is the reason this is SQLite: readers do not block the writer.
        # It is set before anything else so a partially-initialised database is
        # still opened the same way on the next attempt.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
    except BaseException:
        connection.close()
        raise
    if fresh:
        _restrict_permissions(path)
    return connection


def _restrict_permissions(path: Path) -> None:
    """Least-privilege on supported OSes; best-effort where chmod is a no-op.

    Windows ignores POSIX mode bits, so this is not a security guarantee
    there -- said plainly rather than implied, because a caller reading only
    the call site would assume otherwise.
    """

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One IMMEDIATE transaction: the write lock is taken up front.

    DEFERRED would let two writers both begin, then fail one at its first
    write with SQLITE_BUSY after it has already done work. IMMEDIATE makes the
    contention visible at the start instead.
    """

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def _stored_version(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.DatabaseError:
        return 0
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def migrate(connection: sqlite3.Connection) -> int:
    """Apply pending migrations transactionally; return the resulting version.

    Resumable by construction: each migration commits its own transaction and
    records the new version in the same transaction, so an interruption leaves
    the database at a version that was fully applied, never half of one.
    """

    current = _stored_version(connection)
    if current > SCHEMA_VERSION:
        raise IncompatibleSchemaError(
            f"journal schema v{current} is newer than this OPai (v{SCHEMA_VERSION})"
        )
    for version, statements in _MIGRATIONS:
        if version <= current:
            continue
        with _transaction(connection):
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(version),),
            )
        current = version
    return current


def open_store(project_root: Path) -> sqlite3.Connection:
    """Open (creating if needed) and migrate this project's journal."""

    connection = _connect(journal_path(project_root))
    try:
        migrate(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def check_integrity(connection: sqlite3.Connection) -> IntegrityReport:
    """Classify the store as complete, degraded, incompatible or corrupt."""

    checks: list[str] = []
    try:
        version = _stored_version(connection)
    except sqlite3.DatabaseError as exc:
        return IntegrityReport(
            state=INTEGRITY_CORRUPT, schema_version=0, detail=str(exc)[:200]
        )
    checks.append("schema_version")
    if version > SCHEMA_VERSION:
        return IntegrityReport(
            state=INTEGRITY_INCOMPATIBLE,
            schema_version=version,
            detail=(
                f"database schema v{version} was written by a newer OPai; "
                f"this build understands v{SCHEMA_VERSION}"
            ),
            checks=tuple(checks),
        )

    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as exc:
        return IntegrityReport(
            state=INTEGRITY_CORRUPT,
            schema_version=version,
            detail=str(exc)[:200],
            checks=tuple(checks),
        )
    checks.append("quick_check")
    verdict = str(row[0]) if row else "unknown"
    if verdict != "ok":
        return IntegrityReport(
            state=INTEGRITY_CORRUPT,
            schema_version=version,
            detail=verdict[:200],
            checks=tuple(checks),
        )

    try:
        broken = connection.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.DatabaseError as exc:
        return IntegrityReport(
            state=INTEGRITY_CORRUPT,
            schema_version=version,
            detail=str(exc)[:200],
            checks=tuple(checks),
        )
    checks.append("foreign_key_check")
    if broken:
        return IntegrityReport(
            state=INTEGRITY_CORRUPT,
            schema_version=version,
            detail=f"{len(broken)} row(s) violate a foreign key",
            checks=tuple(checks),
        )

    first_bad = _first_unreadable_event(connection)
    checks.append("event_payloads")
    if first_bad is not None:
        return IntegrityReport(
            state=INTEGRITY_DEGRADED,
            schema_version=version,
            first_invalid_sequence=first_bad,
            detail="one or more event payloads are unreadable",
            checks=tuple(checks),
        )
    return IntegrityReport(
        state=INTEGRITY_COMPLETE, schema_version=version, checks=tuple(checks)
    )


def _first_unreadable_event(connection: sqlite3.Connection) -> int | None:
    """The first sequence whose payload will not parse, or ``None``.

    Reported rather than skipped. #613 requires the *first* invalid sequence
    precisely so a reader knows where its view stops being trustworthy instead
    of silently continuing past the gap.
    """

    for row in connection.execute(
        "SELECT sequence, payload FROM events ORDER BY sequence"
    ):
        try:
            json.loads(row["payload"])
        except (TypeError, ValueError):
            return int(row["sequence"])
    return None


def _payload_hash(payload: Mapping[str, Any]) -> str:
    from hashlib import sha256

    return sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()


def append_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    payload: Mapping[str, Any],
    occurred_at: str,
    recorded_at: str,
    producer: str,
    run_id: str | None = None,
    parent_sequence: int | None = None,
    privacy_class: str = "internal",
    event_schema_version: int = 1,
    expected_fence: int | None = None,
) -> int:
    """Append one event transactionally; return its monotonic sequence.

    ``expected_fence`` is the caller's fencing token. When given, the append
    is refused unless the run's current lease still matches -- this is what
    stops a stale supervisor writing after takeover (#613's third acceptance
    criterion). Passing ``None`` means the event is not lease-scoped, which is
    correct for events that belong to no run.
    """

    if privacy_class not in _PRIVACY_CLASSES:
        raise ValueError(f"unknown privacy class: {privacy_class!r}")
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    with _transaction(connection):
        if expected_fence is not None:
            _assert_fence(connection, run_id, expected_fence)
        cursor = connection.execute(
            "INSERT INTO events("
            " run_id, event_type, event_schema_version, occurred_at, recorded_at,"
            " producer, parent_sequence, payload, payload_hash, privacy_class"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                event_type,
                int(event_schema_version),
                occurred_at,
                recorded_at,
                producer,
                parent_sequence,
                encoded,
                _payload_hash(payload),
                privacy_class,
            ),
        )
        return int(cursor.lastrowid)


def _assert_fence(
    connection: sqlite3.Connection, run_id: str | None, expected_fence: int
) -> None:
    if run_id is None:
        raise ValueError("a fenced write must name the run it is fenced to")
    row = connection.execute(
        "SELECT fence, released_at FROM leases WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise StaleWriterError(f"no lease held for run {run_id!r}")
    if row["released_at"] is not None:
        raise StaleWriterError(f"lease for run {run_id!r} was already released")
    if int(row["fence"]) != int(expected_fence):
        raise StaleWriterError(
            f"fence {expected_fence} is stale for run {run_id!r}; "
            f"current holder is fence {int(row['fence'])}"
        )


def acquire_lease(
    connection: sqlite3.Connection, *, run_id: str, owner: str, now: str
) -> int:
    """Take (or take over) a run's lease and return the new fencing token.

    The token strictly increases on every acquisition, including takeover, so
    a previous holder's token can never be mistaken for the current one.
    """

    with _transaction(connection):
        row = connection.execute(
            "SELECT fence FROM leases WHERE run_id = ?", (run_id,)
        ).fetchone()
        fence = (int(row["fence"]) + 1) if row is not None else 1
        connection.execute(
            "INSERT INTO leases(run_id, owner, fence, acquired_at, heartbeat_at,"
            " expires_at, released_at) VALUES (?, ?, ?, ?, ?, NULL, NULL) "
            "ON CONFLICT(run_id) DO UPDATE SET"
            " owner = excluded.owner, fence = excluded.fence,"
            " acquired_at = excluded.acquired_at,"
            " heartbeat_at = excluded.heartbeat_at,"
            " expires_at = NULL, released_at = NULL",
            (run_id, owner, fence, now, now),
        )
        connection.execute(
            "UPDATE runs SET lease_fence = ?, updated_at = ? WHERE run_id = ?",
            (fence, now, run_id),
        )
        return fence


def release_lease(
    connection: sqlite3.Connection, *, run_id: str, fence: int, now: str
) -> bool:
    """Release a lease, but only if ``fence`` is still the current holder."""

    with _transaction(connection):
        cursor = connection.execute(
            "UPDATE leases SET released_at = ?, heartbeat_at = ? "
            "WHERE run_id = ? AND fence = ? AND released_at IS NULL",
            (now, now, run_id, int(fence)),
        )
        return cursor.rowcount > 0


def read_events(
    connection: sqlite3.Connection,
    *,
    run_id: str | None = None,
    after_sequence: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read events in sequence order, skipping nothing silently.

    An unreadable payload is returned with ``payload=None`` and
    ``readable=False`` rather than omitted, so a caller counting events sees
    the gap. :func:`check_integrity` is what reports where it starts.
    """

    sql = "SELECT * FROM events WHERE sequence > ?"
    params: list[Any] = [int(after_sequence)]
    if run_id is not None:
        sql += " AND run_id = ?"
        params.append(run_id)
    sql += " ORDER BY sequence"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows: list[dict[str, Any]] = []
    for row in connection.execute(sql, params):
        record = dict(row)
        try:
            record["payload"] = json.loads(row["payload"])
            record["readable"] = True
        except (TypeError, ValueError):
            record["payload"] = None
            record["readable"] = False
        rows.append(record)
    return rows


def record_operation(
    connection: sqlite3.Connection,
    *,
    operation_key: str,
    kind: str,
    target_digest: str,
    state: str,
    now: str,
    run_id: str | None = None,
    provider_ref: str = "",
    process_ref: str = "",
    external_ref: str = "",
) -> bool:
    """Claim or advance one operation. ``True`` when this call created it.

    The idempotency key is the primary key, so a duplicate claim is a no-op
    rather than a second external effect -- which is the whole point of the
    table.
    """

    if state not in _OPERATION_STATES:
        raise ValueError(f"unknown operation state: {state!r}")
    with _transaction(connection):
        existing = connection.execute(
            "SELECT state FROM operations WHERE operation_key = ?", (operation_key,)
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO operations(operation_key, kind, target_digest, state,"
                " run_id, provider_ref, process_ref, external_ref, attempts,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    operation_key,
                    kind,
                    target_digest,
                    state,
                    run_id,
                    provider_ref,
                    process_ref,
                    external_ref,
                    now,
                    now,
                ),
            )
            return True
        connection.execute(
            "UPDATE operations SET state = ?, provider_ref = ?, external_ref = ?,"
            " attempts = attempts + 1, updated_at = ?,"
            " reconciled_at = CASE WHEN ? = 'reconciled' THEN ? ELSE reconciled_at END"
            " WHERE operation_key = ?",
            (
                state,
                provider_ref or "",
                external_ref or "",
                now,
                state,
                now,
                operation_key,
            ),
        )
        return False


def record_cost(
    connection: sqlite3.Connection,
    *,
    operation_key: str,
    amount: float,
    measurement_kind: str,
    now: str,
    currency: str = "USD",
    quantity: float = 0.0,
    price_snapshot: str = "",
    uncertainty: str = "",
) -> int:
    """Record the single cost event for one operation.

    Raises on a second attempt. #613 requires that no cost is attributed more
    than once, and a duplicate here means an accounting bug the caller needs
    to see -- not something to absorb quietly.
    """

    if measurement_kind not in _COST_KINDS:
        raise ValueError(f"unknown measurement kind: {measurement_kind!r}")
    with _transaction(connection):
        try:
            cursor = connection.execute(
                "INSERT INTO cost_events(operation_key, amount, currency, quantity,"
                " price_snapshot, measurement_kind, uncertainty, recorded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_key,
                    float(amount),
                    currency,
                    float(quantity),
                    price_snapshot,
                    measurement_kind,
                    uncertainty,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise JournalStoreError(
                f"operation {operation_key!r} already has a cost event"
            ) from exc
        return int(cursor.lastrowid)


def store_health(project_root: Path) -> dict[str, Any]:
    """Doctor/preflight summary: does the journal exist, and is it trustworthy?"""

    path = journal_path(project_root)
    if not path.exists():
        return {
            "present": False,
            "path": str(path),
            "integrity": IntegrityReport(
                state=INTEGRITY_COMPLETE, schema_version=0, detail="no journal yet"
            ).to_dict(),
        }
    try:
        connection = _connect(path)
    except sqlite3.DatabaseError as exc:
        return {
            "present": True,
            "path": str(path),
            "integrity": IntegrityReport(
                state=INTEGRITY_CORRUPT, schema_version=0, detail=str(exc)[:200]
            ).to_dict(),
        }
    try:
        report = check_integrity(connection)
        return {
            "present": True,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "journal_mode": str(
                connection.execute("PRAGMA journal_mode").fetchone()[0]
            ),
            "integrity": report.to_dict(),
        }
    finally:
        connection.close()


__all__: Sequence[str] = (
    "SCHEMA_VERSION",
    "INTEGRITY_COMPLETE",
    "INTEGRITY_DEGRADED",
    "INTEGRITY_INCOMPATIBLE",
    "INTEGRITY_CORRUPT",
    "IntegrityReport",
    "IncompatibleSchemaError",
    "JournalStoreError",
    "StaleWriterError",
    "acquire_lease",
    "append_event",
    "check_integrity",
    "journal_path",
    "migrate",
    "open_store",
    "read_events",
    "record_cost",
    "record_operation",
    "release_lease",
    "store_health",
)
