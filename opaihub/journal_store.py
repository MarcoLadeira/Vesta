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

_MIGRATION_2 = (
    """CREATE TABLE IF NOT EXISTS agent_objectives (
        objective_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
        status TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS objective_assignments (
        assignment_id TEXT PRIMARY KEY,
        objective_id TEXT NOT NULL REFERENCES agent_objectives(objective_id),
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
        status TEXT NOT NULL,
        owner TEXT NOT NULL DEFAULT '',
        fence INTEGER NOT NULL DEFAULT 0,
        expires_at TEXT,
        position INTEGER NOT NULL,
        payload TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS assignments_by_objective ON objective_assignments(objective_id, position)",
    """CREATE TABLE IF NOT EXISTS objective_cost_events (
        operation_key TEXT PRIMARY KEY REFERENCES operations(operation_key),
        objective_id TEXT NOT NULL REFERENCES agent_objectives(objective_id),
        assignment_id TEXT REFERENCES objective_assignments(assignment_id),
        amount_usd TEXT,
        measurement_kind TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )""",
)

_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, _MIGRATION_1),
    (2, _MIGRATION_2),
)


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
        return int(cursor.lastrowid)


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
        return int(cursor.lastrowid)


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
