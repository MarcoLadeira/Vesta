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

*Exception text goes through the sanctioned redactor before it is stored.* An
integrity detail reaches doctor output and support bundles, so every
``str(exc)`` here is wrapped in ``redact``. #622's ratchet exists because 58
sites already are not; this module was not going to be the 59th.

To be precise about what that buys: ``redact`` scrubs *secrets*, not
filesystem paths, so a corrupt-database detail can still name the file it
failed to open. That is the boundary the codebase has agreed on, not a claim
that the string is safe for anywhere.

*Corruption is typed, never emptied.* Requirement 8 says corruption must not
become permissive default state. :class:`IntegrityReport` distinguishes
complete / degraded / incompatible / corrupt and carries the first bad
sequence, so a caller can refuse to act rather than proceed on an empty read.

*A database newer than the application is incompatible, not corrupt.* Those are
different failures with different responses -- one wants an upgrade, the other
wants recovery -- and collapsing them would send the user down the wrong path.
"""

from __future__ import annotations

import getpass
import json
import os
import sqlite3
import subprocess  # nosec B404 - fixed argv ACL calls only
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .command_runner import redact
from .state import state_dir

#: Bumped whenever :data:`_MIGRATIONS` grows. A database reporting a higher
#: version than this was written by a newer OPai and is *incompatible* -- a
#: state the caller must be able to tell apart from corruption.
SCHEMA_VERSION = 2

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

#: How far along an operation each state is. An operation may move forward
#: through these and never back.
#:
#: The `runs` table has forbidden terminal regression since #613 -- re-admitting
#: a settled run raises rather than clearing its verdict. `operations` had no
#: such guard: `record_operation` set `state` unconditionally, so any caller
#: writing an earlier state over a later one would quietly un-reconcile a
#: settled external effect. Nothing does today, because the two layers that
#: write operations happen to use disjoint key schemes -- which is luck, not
#: design, and it is exactly the luck that runs out when those keys are
#: unified (#818 asks for one operation identity per external effect).
#:
#: ``uncertain`` is deliberately absent: it is an escalation, not a position on
#: the ladder. An outcome that becomes unknowable after it was reconciled is a
#: real thing to be able to record, and refusing it would be the opposite of
#: honest.
_OPERATION_PROGRESS = {
    "intended": 0,
    "executing": 1,
    "observed": 2,
    "reconciled": 3,
}
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

# v2 gives a lease an owner that names a *process*, not a category (#818).
#
# ``owner`` has always held the surface -- "gui", "cli" -- which is a useful
# label and a useless identity: every OPai process on the machine writes the
# same one. ``unterminated_runs`` documented that a caller could look at
# whether the owning process still exists, and then handed it the string
# "gui". These two columns are what that sentence needs to be true.
#
# Added rather than repurposed. ``owner`` keeps its meaning, so every existing
# reader keeps working and no migration has to guess what an old value meant.
#
# Nullable on purpose: rows written before this migration have no process
# behind them to name, and inventing one would manufacture exactly the
# confident-but-baseless answer this journal exists to prevent. A NULL here
# reads as "unknown", which is the truth about a pre-migration row.
_MIGRATION_2: tuple[str, ...] = (
    "ALTER TABLE leases ADD COLUMN owner_pid INTEGER",
    "ALTER TABLE leases ADD COLUMN owner_boot TEXT",
)

_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, _MIGRATION_1),
    (2, _MIGRATION_2),
)


#: How long a statement waits for another writer before giving up. Long
#: enough for an ordinary commit anywhere in OPai to finish.
BUSY_TIMEOUT_SECONDS = 10.0


def _connect(
    path: Path, *, timeout: float = BUSY_TIMEOUT_SECONDS
) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    connection = sqlite3.connect(path, timeout=timeout, isolation_level=None)
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


#: Characters that would let a user name be misread as extra icacls
#: arguments or as a second access-control entry.
SEPARATORS = (":", '"', "/", "\\")


def _restrict_permissions(path: Path) -> None:
    """Least-privilege on every supported OS, including Windows.

    #613 asks for least-privilege database permissions. ``chmod`` delivers that
    on POSIX and does nothing at all on Windows, where the file inherits the
    parent directory's ACL -- which on a default install grants read to every
    local user. The journal holds task text, model routes, cost records and
    approval fingerprints, so "every local user can read it" is not a detail.

    Called once, when the database file is first created, so the cost of
    shelling out to ``icacls`` is paid per project rather than per open.
    """

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if os.name == "nt":
        _restrict_permissions_windows(path)


def _restrict_permissions_windows(path: Path) -> tuple[bool, str]:
    """Break ACL inheritance and grant the owning user alone full control.

    ``/inheritance:r`` removes the inherited entries rather than merely adding
    one, and that is the part that matters: granting the current user full
    control while leaving the inherited local-users entry in place would look
    like a restriction and be none.

    Returns whether it succeeded and why not, so the doctor check can say so.
    Silently failing to secure a file is worse than not trying, because nobody
    looks again. It never raises: a journal that cannot be locked down is still
    a journal, and refusing to open it would trade a confidentiality problem
    for an availability one.
    """

    user = os.environ.get("USERNAME") or getpass.getuser()
    if not user or any(ch in user for ch in SEPARATORS):
        # A name that could be read as a second access-control entry. icacls is
        # given a fixed argv here and never a shell, so this is belt and braces
        # rather than the only defence -- but a user name that cannot be
        # expressed safely is not one to hand to an ACL editor.
        return False, "the current user name cannot be used in an ACL entry"
    try:
        completed = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(F)"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, redact(str(exc))[:200]
    if completed.returncode != 0:
        return False, redact(completed.stderr.strip() or completed.stdout.strip())[:200]
    return True, ""


#: Principals whose presence in an ACL means somebody other than the owner can
#: read the journal. Matched by name rather than parsed generally, because an
#: ACL parser that is subtly wrong would report "restricted" for a file that is
#: not, which is the one error this check must never make.
SHARED_PRINCIPALS = ("BUILTIN\\Users", "Everyone", "Authenticated Users")


def permissions_health(path: Path) -> dict[str, Any]:
    """Whether the journal file is readable only by its owner.

    Reported rather than re-applied on every open. Forcing the ACL each time
    would fight a user who widened it deliberately, and checking is what
    surfaces the case that actually matters: a database restored, copied or
    synced from somewhere else, arriving with whatever permissions it had
    there.
    """

    facts: dict[str, Any] = {"checked": False, "restricted": False, "detail": ""}
    if not path.exists():
        return facts
    if os.name != "nt":
        try:
            mode = path.stat().st_mode & 0o777
        except OSError as exc:
            facts["detail"] = redact(str(exc))[:200]
            return facts
        facts["checked"] = True
        facts["restricted"] = not (mode & 0o077)
        facts["detail"] = f"mode {mode:04o}"
        return facts
    try:
        completed = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["icacls", str(path)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        facts["detail"] = redact(str(exc))[:200]
        return facts
    if completed.returncode != 0:
        facts["detail"] = redact(completed.stderr.strip())[:200]
        return facts
    facts["checked"] = True
    shared = [name for name in SHARED_PRINCIPALS if name in completed.stdout]
    facts["restricted"] = not shared
    facts["detail"] = ("readable by " + ", ".join(shared)) if shared else "owner only"
    return facts


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One IMMEDIATE transaction: the write lock is taken up front.

    DEFERRED would let two writers both begin, then fail one at its first
    write with SQLITE_BUSY after it has already done work. IMMEDIATE makes the
    contention visible at the start instead.

    **Re-entrant.** Inside a transaction already open on this connection, the
    block joins it and commits or rolls back with it. That is what lets one
    lifecycle moment be one transaction: a run's terminal verdict, its event
    and the release of its lease used to commit as three, so a crash between
    them left the `runs` table and the event log telling different stories --
    the exact contradiction ``journal_projections.run_table_parity`` exists to
    catch. Nested use raised before, so nothing relied on it being refused.
    """

    if connection.in_transaction:
        yield connection
        return
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


def _record_schema_version(connection: sqlite3.Connection, version: int) -> None:
    """Record the schema version, and never let it go backwards.

    MAX rather than assignment: the version is monotonic, and a write that
    lowers it is always a mistake rather than an intent. This is the second of
    two guards against the migration race -- with the first (re-reading under
    the write lock) in place nothing should reach here with a stale value at
    all, which is exactly why it is worth keeping: the failure it prevents is
    a journal nobody can open, permanently.

    CAST because the column is TEXT, where "10" sorts below "2".

    Named rather than inlined so a test can exercise *this* statement instead
    of writing its own copy -- a test that reimplements the SQL proves SQLite
    works, not that OPai uses it.
    """

    connection.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(MAX("
        "CAST(schema_meta.value AS INTEGER), "
        "CAST(excluded.value AS INTEGER)) AS TEXT)",
        (str(version),),
    )


def migrate(connection: sqlite3.Connection) -> int:
    """Apply pending migrations transactionally; return the resulting version.

    Resumable by construction: each migration commits its own transaction and
    records the new version in the same transaction, so an interruption leaves
    the database at a version that was fully applied, never half of one.

    **Safe against a second process doing the same thing.** The version is
    re-read inside each write transaction, not just once at the top, because
    the gap between deciding and locking is wide enough to lose a whole
    migration in -- and the failure that produced was permanent rather than
    transient.

    What happened without it: two processes creating a journal together both
    read version 0. One migrated fully to 2 and committed. The other, still
    believing 0, re-ran migration 1 -- every statement `CREATE TABLE IF NOT
    EXISTS`, so silently fine -- and wrote version **1 over the 2**. Migration
    2 is `ALTER TABLE leases ADD COLUMN`, which SQLite cannot express
    idempotently, so it then failed with `duplicate column name` and kept
    failing: the recorded version claimed it had never been applied while the
    columns were already there. Every subsequent `open_store` raised, forever,
    on a journal that was structurally fine.

    Measured before the fix: 2 of 25 rounds of six concurrent processes, and
    1 of 20 with two real `opai ask` turns on a fresh project. Only fresh
    creation races -- upgrading an existing journal was never affected --
    which is exactly the new-install and new-workspace case.

    Two guards, either of which would be sufficient, because the cost of
    being wrong here is a journal nobody can open:

    1. the version is re-read under the write lock, so a migration another
       process has already applied is skipped rather than repeated;
    2. the recorded version can only ever move forward, so a stale writer
       cannot drag it backwards even if it did somehow re-run.
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
            # `current` was read before this lock existed. Another process may
            # have applied this migration in the meantime, and re-running one
            # that is not idempotent is what bricked the journal.
            applied = _stored_version(connection)
            if applied >= version:
                current = applied
                continue
            for statement in statements:
                connection.execute(statement)
            _record_schema_version(connection, version)
        current = max(current, version)
    return current


def open_store(
    project_root: Path, *, timeout: float = BUSY_TIMEOUT_SECONDS
) -> sqlite3.Connection:
    """Open (creating if needed) and migrate this project's journal.

    ``timeout`` is how long any statement on the connection may wait for
    another writer. The default suits a write that must land; a caller that
    would rather skip than wait -- a heartbeat -- passes something tiny.
    """

    connection = _connect(journal_path(project_root), timeout=timeout)
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
            state=INTEGRITY_CORRUPT, schema_version=0, detail=redact(str(exc))[:200]
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
            detail=redact(str(exc))[:200],
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
            detail=redact(str(exc))[:200],
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
    # Requirement 9: minimise sensitive payload content. Redaction happens
    # here, at the one place every event passes through, rather than at the
    # dozen call sites that build payloads -- one call site that forgot would
    # write a secret to disk and nothing would ever say so.
    #
    # Before hashing, deliberately: ``payload_hash`` has to describe what is
    # actually stored, or a self-verifying record verifies a payload that does
    # not exist anywhere.
    safe_payload = minimise(payload)
    encoded = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"))
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
                _payload_hash(safe_payload),
                privacy_class,
            ),
        )
        return _inserted_row_id(cursor)


#: Longest string kept verbatim in an event payload. Payloads describe what
#: happened -- a state name, a verdict, a model id, a reason -- and nothing that
#: shape runs to a kilobyte. Anything that does is a transcript arriving where a
#: description belongs, and #613's non-goals rule out storing transcripts to
#: make replay possible.
MAX_PAYLOAD_STRING = 1024

#: How far into a nested payload minimisation will walk.
#:
#: This exists because of what the recursion does when it runs out: an
#: adversarial pass fed ``minimise`` a payload holding a reference to itself and
#: got ``RecursionError``. That mattered more than it looks. ``json.dumps``
#: refuses a cycle with ``ValueError``, which ``record_event`` catches, so a
#: cyclic payload used to be a swallowed mirror failure; raising
#: ``RecursionError`` ahead of it turned that into an exception escaping into a
#: real turn. A bound restores the contract, and it covers honest deep nesting
#: at the same time.
#:
#: A payload twenty levels deep is already past describing what happened.
MAX_PAYLOAD_DEPTH = 20

#: What replaces a value too deep, or one that refers back to itself.
TRUNCATED_DEPTH = "[TRUNCATED_DEPTH]"


def minimise(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strip secrets and bound the size of everything in an event payload.

    Requirement 9 asks that sensitive payload content be minimised and that raw
    prompts not be required for runtime recovery. This is the first half, and
    it is applied at the store boundary so that no call site can forget it.

    Two things happen to every string, however deeply nested:

    *It goes through the sanctioned redactor.* Before this, a prompt like
    ``fix my auth, the key is sk-ant-...`` was written verbatim into
    ``journal.sqlite3`` -- demonstrated by reading the raw file back and
    finding the key in it. The database is owner-only now, which bounds who can
    read it, but a secret at rest is still a secret at rest and it propagates
    into every backup taken from then on.

    *And it is truncated.* Redaction only catches shapes it recognises, so a
    long paste that happens to contain something private survives it. Bounding
    the length does not make that safe, but it does stop the journal quietly
    becoming the transcript store this issue's non-goals exclude.

    Keys are redacted too. A payload built by interpolating user input into a
    key name is unusual, and "unusual" is exactly where this kind of thing
    hides.
    """

    return {
        _minimise_text(str(key)): _minimise_value(value, 0)
        for key, value in dict(payload).items()
    }


def _minimise_text(text: str) -> str:
    return redact(text)[:MAX_PAYLOAD_STRING]


def _minimise_value(value: Any, depth: int) -> Any:
    """One payload value, redacted, bounded, and left encodable or not.

    The line this draws is deliberate, and it was drawn after getting it wrong
    once. Containers with a faithful JSON analogue are converted -- a ``set``
    becomes a list, and nothing is invented in doing so. An arbitrary object is
    *not*: there is no honest JSON form of one, and substituting ``repr`` would
    turn a caller's bug into stored garbage that reads like data.

    So an unencodable payload still raises out of ``json.dumps``, before
    ``BEGIN``, exactly as ``append_event`` is designed to. The store refuses
    what it cannot represent; the *mirror* is what must survive that, and it
    does, by catching the error rather than by the store pretending.
    """

    if depth > MAX_PAYLOAD_DEPTH:
        return TRUNCATED_DEPTH
    if isinstance(value, str):
        return _minimise_text(value)
    if isinstance(value, bool) or value is None:
        # Before the numeric check: bool is a subclass of int, and letting it
        # fall through would store True as 1.
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN and the infinities are not JSON. json.dumps emits them anyway as
        # bare NaN/Infinity, which no strict parser will read back -- so a
        # projection rebuilt elsewhere would fail on a row written here.
        return value if value == value and value not in (_INF, -_INF) else str(value)
    if isinstance(value, Mapping):
        return {
            _minimise_text(str(key)): _minimise_value(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_minimise_value(item, depth + 1) for item in value]
    # Anything else is returned untouched, so json.dumps raises TypeError on it
    # as it always has. See the docstring: refusing is the store's job, and
    # surviving the refusal is the mirror's.
    return value


_INF = float("inf")


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
    connection: sqlite3.Connection,
    *,
    run_id: str,
    owner: str,
    now: str,
    owner_pid: int | None = None,
    owner_boot: str = "",
) -> int:
    """Take (or take over) a run's lease and return the new fencing token.

    The token strictly increases on every acquisition, including takeover, so
    a previous holder's token can never be mistaken for the current one.

    ``owner`` is the surface -- a category. ``owner_pid`` and ``owner_boot``
    name the process behind it, which is what a later caller needs to ask
    whether the work is still being tended. The store records them; it does
    not interpret them, and in particular it never decides from them that a
    run is dead. Both default to absent, because a caller that cannot honestly
    name its process must be able to say so.
    """

    with _transaction(connection):
        row = connection.execute(
            "SELECT fence FROM leases WHERE run_id = ?", (run_id,)
        ).fetchone()
        fence = (int(row["fence"]) + 1) if row is not None else 1
        connection.execute(
            "INSERT INTO leases(run_id, owner, fence, acquired_at, heartbeat_at,"
            " expires_at, released_at, owner_pid, owner_boot)"
            " VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET"
            " owner = excluded.owner, fence = excluded.fence,"
            " acquired_at = excluded.acquired_at,"
            " heartbeat_at = excluded.heartbeat_at,"
            " owner_pid = excluded.owner_pid,"
            " owner_boot = excluded.owner_boot,"
            " expires_at = NULL, released_at = NULL",
            (
                run_id,
                owner,
                fence,
                # `acquired_at` and `heartbeat_at` are deliberately the *same*
                # value, not two reads of the clock. `journal_liveness` decides
                # whether anyone was ever tending a lease by asking whether the
                # heartbeat has moved past the acquisition -- because most
                # surfaces never beat one, and judging them by a clock they
                # never wound would report every CLI and background run as
                # having stopped responding.
                #
                # Two `now()` calls here would differ by microseconds, which is
                # enough to make that check say yes for every lease in the
                # database. Anything that changes this must change
                # `_stopped_responding` with it; the test in
                # tests/test_journal_liveness.py fails loudly if it does not.
                now,
                now,
                _positive_pid(owner_pid),
                str(owner_boot or ""),
            ),
        )
        connection.execute(
            "UPDATE runs SET lease_fence = ?, updated_at = ? WHERE run_id = ?",
            (fence, now, run_id),
        )
        return fence


def _inserted_row_id(cursor: sqlite3.Cursor) -> int:
    """The row id an INSERT just produced.

    ``lastrowid`` is typed ``int | None`` because it is ``None`` before a
    cursor has inserted anything. After an INSERT it is always set, so this
    never fires in practice -- but coercing the ``None`` away silently would
    turn "the insert did not happen" into row 0, which is a worse answer than
    saying so.
    """

    row_id = cursor.lastrowid
    if row_id is None:  # pragma: no cover - an INSERT always reports one
        raise JournalStoreError("the store did not report a row id for the insert")
    return int(row_id)


def _positive_pid(value: object) -> int | None:
    """A usable process id, or ``None``.

    Zero and negatives are not process ids on any platform OPai runs on, and
    storing one would let a liveness probe ask a meaningless question and get
    a meaningful-looking answer. Absent is the honest record.

    The type is narrowed before converting rather than converted and caught,
    which fixes a real hole as well as a mypy complaint. ``int(True)`` is 1,
    and pid 1 exists on every system OPai runs on -- so a lease carrying a
    boolean would have been probed as a live process and reported as one.
    A float is refused for the same reason: truncating 2.9 to pid 2 invents an
    identity nobody recorded.
    """

    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, str)):
        return None
    try:
        pid = int(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is an ArithmeticError, not a ValueError: int(inf)
        # raises it and would escape this guard entirely.
        return None
    return pid if pid > 0 else None


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

    # An external reference is usually a plain PR or run URL, which the
    # redactor leaves alone. It is occasionally a signed URL, where the
    # signature *is* the credential -- and a signed URL in a durable record
    # outlives the request it was minted for. Ordinary URLs are unchanged by
    # this, so it costs nothing to be sure.
    external_ref = redact(str(external_ref or ""))[:2048]

    if state not in _OPERATION_STATES:
        raise ValueError(f"unknown operation state: {state!r}")
    with _transaction(connection):
        existing = connection.execute(
            "SELECT state FROM operations WHERE operation_key = ?", (operation_key,)
        ).fetchone()
        if existing is not None and _would_regress(str(existing["state"]), state):
            # Refused rather than ignored. A caller writing an earlier state
            # over a later one has a real bug -- it believes an effect is still
            # in flight that this store has already settled -- and swallowing
            # it would leave the two of them disagreeing silently, which is the
            # failure mode this journal exists to remove.
            raise JournalStoreError(
                f"operation {operation_key!r} is already {existing['state']!r};"
                f" it cannot go back to {state!r}"
            )
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


def _would_regress(current: str, proposed: str) -> bool:
    """True when ``proposed`` is behind ``current`` on the operation ladder.

    Anything off the ladder -- ``uncertain``, or a state a newer OPai wrote
    that this build does not know -- is never a regression. Refusing a state
    we cannot rank would turn a forwards-compatibility problem into a hard
    failure, and this is the wrong place to be strict about that.
    """

    here = _OPERATION_PROGRESS.get(current)
    there = _OPERATION_PROGRESS.get(proposed)
    if here is None or there is None:
        return False
    return there < here


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
    # A spend record cannot be negative or non-finite. NaN is the dangerous one:
    # it round-trips through JSON, compares false against every budget ceiling,
    # and would silently disable the cap it was meant to count against.
    amount_value = float(amount)
    if amount_value != amount_value or amount_value in (
        float("inf"),
        float("-inf"),
    ):
        raise ValueError("cost amount must be a finite number")
    if amount_value < 0:
        raise ValueError("cost amount must not be negative")
    with _transaction(connection):
        try:
            cursor = connection.execute(
                "INSERT INTO cost_events(operation_key, amount, currency, quantity,"
                " price_snapshot, measurement_kind, uncertainty, recorded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_key,
                    amount_value,
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
        return _inserted_row_id(cursor)


# --------------------------------------------------------------------------
# Projections
#
# #613: "Rebuild every projection deterministically from the same immutable
# snapshot" and "Projection deletion and rebuild produces the same canonical
# serialized state." Both are about *bytes*, not about a dict that happens to
# compare equal, so everything here goes through one canonical encoder.
# --------------------------------------------------------------------------


def canonical_bytes(payload: Any) -> bytes:
    """The one serialisation a projection is compared by.

    Sorted keys and fixed separators, so two rebuilds of the same history are
    byte-identical rather than merely equal-as-dicts. ``ensure_ascii`` keeps
    the bytes stable regardless of the reader's locale or console encoding.
    """

    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


@dataclass(frozen=True)
class ProjectionResult:
    """A rebuilt projection and how much of the history it could trust."""

    projection_type: str
    projection_version: int
    payload: Any
    source_sequence: int
    integrity: str
    first_invalid_sequence: int | None = None

    @property
    def complete(self) -> bool:
        return self.integrity == INTEGRITY_COMPLETE

    def to_bytes(self) -> bytes:
        return canonical_bytes(self.payload)


def rebuild_projection(
    connection: sqlite3.Connection,
    *,
    projection_type: str,
    projection_version: int,
    reduce: Any,
    empty: Any,
    now: str,
    run_id: str | None = None,
    persist: bool = True,
) -> ProjectionResult:
    """Fold the event history into a projection, deterministically.

    Stops at the first unreadable event rather than folding past it. That is
    the difference between a projection that is *short* and one that is
    *wrong*: skipping a corrupt event would produce a state that looks
    complete and describes a history that never happened. The result carries
    ``degraded`` and the offending sequence so a caller can refuse to treat it
    as authoritative -- requirement 8, at the projection layer rather than
    only at the raw read.
    """

    projection = empty() if callable(empty) else empty
    source_sequence = 0
    integrity = INTEGRITY_COMPLETE
    first_invalid: int | None = None

    for record in read_events(connection, run_id=run_id):
        if not record["readable"]:
            integrity = INTEGRITY_DEGRADED
            first_invalid = int(record["sequence"])
            break
        projection = reduce(projection, record)
        source_sequence = int(record["sequence"])

    result = ProjectionResult(
        projection_type=projection_type,
        projection_version=int(projection_version),
        payload=projection,
        source_sequence=source_sequence,
        integrity=integrity,
        first_invalid_sequence=first_invalid,
    )
    if persist:
        _persist_projection(connection, result, now=now)
    return result


def _persist_projection(
    connection: sqlite3.Connection, result: ProjectionResult, *, now: str
) -> None:
    with _transaction(connection):
        connection.execute(
            "INSERT INTO projections(projection_type, projection_version,"
            " source_sequence, payload, rebuilt_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(projection_type, projection_version) DO UPDATE SET"
            " source_sequence = excluded.source_sequence,"
            " payload = excluded.payload,"
            " rebuilt_at = excluded.rebuilt_at",
            (
                result.projection_type,
                result.projection_version,
                result.source_sequence,
                canonical_bytes(result.payload).decode("utf-8"),
                now,
            ),
        )


def load_projection(
    connection: sqlite3.Connection, *, projection_type: str, projection_version: int
) -> dict[str, Any] | None:
    """The stored projection, or ``None`` when it has never been built."""

    row = connection.execute(
        "SELECT * FROM projections WHERE projection_type = ?"
        " AND projection_version = ?",
        (projection_type, int(projection_version)),
    ).fetchone()
    if row is None:
        return None
    record = dict(row)
    try:
        record["payload"] = json.loads(row["payload"])
        record["readable"] = True
    except (TypeError, ValueError):
        # A projection is disposable, so an unreadable one is a rebuild
        # trigger rather than a crisis -- but it is still never returned as
        # empty-and-fine.
        record["payload"] = None
        record["readable"] = False
    return record


def drop_projection(
    connection: sqlite3.Connection, *, projection_type: str, projection_version: int
) -> bool:
    """Delete a projection. Deliberately easy: they are disposable by design."""

    with _transaction(connection):
        cursor = connection.execute(
            "DELETE FROM projections WHERE projection_type = ?"
            " AND projection_version = ?",
            (projection_type, int(projection_version)),
        )
        return cursor.rowcount > 0


def written_by_a_newer_opai(project_root: Path) -> bool:
    """Whether the journal's schema is ahead of what this build understands.

    Read-only and without migrating, on purpose: :func:`open_store` migrates,
    and a newer schema is exactly what makes migration refuse -- asking it
    would be asking the thing that already said no.
    """

    path = journal_path(project_root)
    if not path.exists():
        return False
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    except (sqlite3.DatabaseError, OSError):
        return False
    try:
        connection.row_factory = sqlite3.Row
        return _stored_version(connection) > SCHEMA_VERSION
    finally:
        connection.close()


def _openable(project_root: Path) -> tuple[bool, str]:
    """Would OPai be able to open this journal -- asked without changing it.

    ``check_integrity`` reads a raw connection: it answers "is this database
    structurally sound", which is not the same question as "will OPai be able
    to use it". A journal whose migration cannot complete passes every
    structural check and still refuses every write. That gap was not
    theoretical: a migration race left a journal recording schema v1 with v2's
    columns already present, so ``open_store`` raised ``duplicate column
    name`` on every attempt while doctor called the project ready.

    The first answer to that called ``open_store`` -- which *migrates*, so
    running doctor upgraded the journal as a side effect (#818 review finding
    16). Now the pending migrations are applied inside a transaction that is
    always rolled back: the same statements, failing exactly as a real open
    would, and nothing kept.
    """

    try:
        connection = _connect(journal_path(project_root))
    except Exception as exc:  # noqa: BLE001 - health must not become the problem
        return False, f"{type(exc).__name__}: {redact(str(exc))[:180]}"
    try:
        current = _stored_version(connection)
        if current > SCHEMA_VERSION:
            return False, (
                f"IncompatibleSchemaError: journal schema v{current} is newer"
                f" than this OPai (v{SCHEMA_VERSION})"
            )
        pending = [
            statement
            for version, statements in _MIGRATIONS
            if version > current
            for statement in statements
        ]
        if pending:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in pending:
                    connection.execute(statement)
            finally:
                connection.execute("ROLLBACK")
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {redact(str(exc))[:180]}"
    finally:
        connection.close()


def store_health(project_root: Path) -> dict[str, Any]:
    """Doctor/preflight summary: does the journal exist, and is it usable?

    Two questions, reported separately because they can disagree.
    ``integrity`` describes the *file*; ``openable`` describes whether OPai can
    work with it. A journal can be structurally perfect and still unusable.
    """

    path = journal_path(project_root)
    if not path.exists():
        return {
            "present": False,
            "path": str(path),
            "openable": True,
            "open_error": "",
            "integrity": IntegrityReport(
                state=INTEGRITY_COMPLETE, schema_version=0, detail="no journal yet"
            ).to_dict(),
        }
    openable, open_error = _openable(project_root)
    try:
        connection = _connect(path)
    except sqlite3.DatabaseError as exc:
        return {
            "present": True,
            "path": str(path),
            "openable": openable,
            "open_error": open_error,
            "integrity": IntegrityReport(
                state=INTEGRITY_CORRUPT, schema_version=0, detail=redact(str(exc))[:200]
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
            "openable": openable,
            "open_error": open_error,
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
    "ProjectionResult",
    "IncompatibleSchemaError",
    "JournalStoreError",
    "StaleWriterError",
    "acquire_lease",
    "append_event",
    "canonical_bytes",
    "check_integrity",
    "written_by_a_newer_opai",
    "drop_projection",
    "journal_path",
    "load_projection",
    "migrate",
    "open_store",
    "read_events",
    "rebuild_projection",
    "record_cost",
    "record_operation",
    "release_lease",
    "store_health",
)
