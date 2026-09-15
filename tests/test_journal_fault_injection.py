"""#613 fault injection: disk full, read-only, locked, corrupt, interrupted.

The required-tests section names these exactly: "Disk full, permission denied,
locked database, corrupt row/WAL, interrupted migration and unavailable
backup." They are the conditions under which a store stops being a store, and
the acceptance criterion they serve is the strictest one in the issue:

    Corrupt/incompatible data becomes typed degraded/blocked state and cannot
    remove budget, authority or verification protection.

So every test here asks the same question in a different costume: when the
storage layer fails, does Vesta end up *knowing less* -- or does it end up
believing something permissive that is not true? The second is the failure
mode worth testing for, because it is the one that looks like success.

Two notes on honesty of simulation.

``sqlite3`` raises ``OperationalError`` for a full disk, a read-only file and a
locked database alike, so these tests inject at the boundary rather than
claiming to reproduce the kernel condition. Where a real condition *can* be
produced cheaply -- an actually locked database, an actually corrupt file, an
actually read-only directory -- it is, and the test says which it is doing.

The backup case is the one #613 lists that this file cannot honestly cover:
there is no backup path in the store yet. Rather than write a test that passes
because the feature is absent, it is named in the ADR's open questions and
left out here.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vestahub import journal_store
from vestahub.journal_runtime import record_admission
from vestahub.journal_store import (
    INTEGRITY_COMPLETE,
    INTEGRITY_CORRUPT,
    INTEGRITY_DEGRADED,
    append_event,
    check_integrity,
    journal_path,
    open_store,
    read_events,
    record_cost,
    record_operation,
    store_health,
)

NOW = "2026-08-25T12:00:00+00:00"


NOW_ISO = "2026-08-26T12:00:00+00:00"


class _FailingConnection:
    """A connection whose ``execute`` fails, standing in for a full disk.

    ``sqlite3.Connection.execute`` is read-only and cannot be patched, so
    wrapping is the honest alternative: the store takes a connection as an
    argument, and this fails at exactly the boundary the store calls. Every
    other attribute delegates, so it behaves like the real thing up to the
    failure.

    ``fail_on`` narrows the failure to statements containing a substring, which
    is what makes an *interrupted* migration expressible rather than a
    migration that never starts.
    """

    def __init__(self, real, *, fail_on=None, error=None) -> None:
        self._real = real
        self._fail_on = fail_on
        self._error = error or sqlite3.OperationalError("database or disk is full")

    def execute(self, sql, *args, **kwargs):
        if self._fail_on is None or self._fail_on in str(sql):
            raise self._error
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


@contextlib.contextmanager
def _interrupted_migration(marker="CREATE TABLE IF NOT EXISTS cost_events"):
    """Fail one statement partway through the schema, then restore.

    Injected at ``_connect`` so the failure happens *inside* ``migrate`` with a
    real half-built database on disk -- the condition #613's "migration
    interrupted at every step" edge case is about, and something a mocked-out
    migrate() would never produce.
    """

    real_connect = journal_store._connect

    def failing(path, **kwargs):
        # `**kwargs` so the stub keeps matching `_connect`'s signature: it grew
        # a `timeout` when heartbeats needed to give up rather than wait.
        return _FailingConnection(
            real_connect(path, **kwargs),
            fail_on=marker,
            error=sqlite3.OperationalError("disk I/O error"),
        )

    with mock.patch.object(journal_store, "_connect", failing):
        yield


def _seed(store) -> None:
    store.execute(
        "INSERT INTO tasks(task_id, origin_surface, created_at, schema_version,"
        " updated_at) VALUES ('task-a', 'cli', ?, 1, ?)",
        (NOW, NOW),
    )
    store.execute(
        "INSERT INTO runs(run_id, task_id, attempt, desired_state, observed_state,"
        " created_at, updated_at)"
        " VALUES ('run-a', 'task-a', 1, 'running', 'queued', ?, ?)",
        (NOW, NOW),
    )


class _FaultFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = open_store(self.root)
        self.addCleanup(self.store.close)
        _seed(self.store)

    def _append(self, event_type: str = "e") -> int:
        return append_event(
            self.store,
            event_type=event_type,
            payload={"t": event_type},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="test",
            run_id="run-a",
        )


class DiskFullTests(_FaultFixture):
    """Injected at the boundary: sqlite reports a full disk as OperationalError."""

    def test_a_full_disk_raises_rather_than_silently_dropping_the_event(self):
        """The failure must reach the caller, not be swallowed into 'written'."""

        before = len(read_events(self.store))

        failing = _FailingConnection(self.store)
        with self.assertRaises(sqlite3.OperationalError):
            append_event(
                failing,
                event_type="lost",
                payload={},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="test",
                run_id="run-a",
            )

        self.assertEqual(len(read_events(self.store)), before)

    def test_the_store_is_still_readable_after_a_write_failed(self):
        failing = _FailingConnection(self.store)
        with self.assertRaises(sqlite3.OperationalError):
            append_event(
                failing,
                event_type="lost",
                payload={},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="test",
                run_id="run-a",
            )

        self.assertEqual(check_integrity(self.store).state, INTEGRITY_COMPLETE)

    def test_a_failed_cost_write_does_not_record_a_free_operation(self):
        """The permissive failure this criterion exists to prevent."""

        record_operation(
            self.store,
            operation_key="op-1",
            kind="model.call",
            target_digest="d",
            state="observed",
            now=NOW,
            run_id="run-a",
        )

        with self.assertRaises(sqlite3.OperationalError):
            record_cost(
                _FailingConnection(self.store),
                operation_key="op-1",
                amount=5.0,
                measurement_kind="actual",
                now=NOW,
            )

        total = self.store.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM cost_events"
        ).fetchone()[0]
        self.assertEqual(float(total), 0.0, "no cost row, and none invented")


class LockedDatabaseTests(_FaultFixture):
    """A genuinely locked database, not a mock: another connection holds it."""

    def test_a_second_writer_times_out_rather_than_corrupting(self):
        blocker = open_store(self.root)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute(
            "INSERT INTO events(run_id, event_type, event_schema_version,"
            " occurred_at, recorded_at, producer, payload, payload_hash,"
            " privacy_class) VALUES ('run-a', 'blocker', 1, ?, ?, 't', '{}', '',"
            " 'internal')",
            (NOW, NOW),
        )

        try:
            impatient = sqlite3.connect(journal_path(self.root), timeout=0.1)
            self.addCleanup(impatient.close)
            with self.assertRaises(sqlite3.OperationalError):
                impatient.execute("BEGIN IMMEDIATE")
                impatient.execute(
                    "INSERT INTO events(run_id, event_type, event_schema_version,"
                    " occurred_at, recorded_at, producer, payload, payload_hash,"
                    " privacy_class) VALUES ('run-a', 'second', 1, ?, ?, 't',"
                    " '{}', '', 'internal')",
                    (NOW, NOW),
                )
        finally:
            blocker.execute("ROLLBACK")

        self.assertEqual(check_integrity(self.store).state, INTEGRITY_COMPLETE)

    def test_a_reader_still_reads_while_the_database_is_write_locked(self):
        """WAL's whole point, restated as a fault case."""

        blocker = open_store(self.root)
        self.addCleanup(blocker.close)
        self._append("before")
        blocker.execute("BEGIN IMMEDIATE")
        try:
            rows = read_events(self.store)
        finally:
            blocker.execute("ROLLBACK")

        self.assertEqual([row["event_type"] for row in rows], ["before"])


class PermissionDeniedTests(unittest.TestCase):
    """A really read-only file where the OS allows it, skipped where it does not."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        store = open_store(self.root)
        _seed(store)
        store.close()

    def test_health_reports_rather_than_raising_when_the_file_is_unreadable(self):
        """doctor must survive a journal it cannot open."""

        path = journal_path(self.root)
        original = path.stat().st_mode
        try:
            os.chmod(path, 0)
            if os.access(path, os.R_OK):
                self.skipTest("this platform ignores the mode bits used here")
            health = store_health(self.root)
        finally:
            os.chmod(path, stat.S_IMODE(original))

        self.assertTrue(health["present"])
        self.assertIn(
            health["integrity"]["state"], {INTEGRITY_CORRUPT, INTEGRITY_COMPLETE}
        )

    @unittest.skipIf(sys.platform == "win32", "directory mode bits are advisory here")
    def test_opening_under_a_read_only_directory_fails_loudly(self):
        """A store that cannot be created must say so, not pretend to exist."""

        locked_root = self.root / "locked"
        locked_root.mkdir()
        original = locked_root.stat().st_mode
        try:
            os.chmod(locked_root, stat.S_IRUSR | stat.S_IXUSR)
            if os.access(locked_root, os.W_OK):
                self.skipTest("running with privileges that ignore the mode")
            with self.assertRaises((OSError, sqlite3.OperationalError)):
                open_store(locked_root).close()
        finally:
            os.chmod(locked_root, stat.S_IMODE(original))


class CorruptDataTests(_FaultFixture):
    """Corruption becomes typed state; it never becomes permissive emptiness."""

    def test_a_corrupt_row_is_degraded_with_its_sequence_not_dropped(self):
        self._append("first")
        bad = self._append("second")
        self.store.execute(
            "UPDATE events SET payload = '{torn' WHERE sequence = ?", (bad,)
        )

        report = check_integrity(self.store)

        self.assertEqual(report.state, INTEGRITY_DEGRADED)
        self.assertEqual(report.first_invalid_sequence, bad)
        self.assertEqual(len(read_events(self.store)), 2)

    def test_a_clobbered_database_file_is_corrupt_not_empty(self):
        """The one that matters: unreadable must never read as 'nothing here'.

        Note what this does *not* cover: clobbering the file makes ``_connect``
        fail, so the verdict comes from the exception path and ``quick_check``
        never runs. Teeth-testing found that -- forcing ``quick_check`` to
        always answer "ok" broke nothing here. The next test covers that branch
        directly rather than leaving it to look tested.
        """

        self.store.close()
        journal_path(self.root).write_bytes(b"\x00" * 4096)

        health = store_health(self.root)

        self.assertTrue(health["present"])
        self.assertEqual(health["integrity"]["state"], INTEGRITY_CORRUPT)

    def test_a_page_level_corruption_verdict_is_reported_as_corrupt(self):
        """The ``quick_check`` branch, which no other test here reaches.

        Real page corruption cannot be produced reliably across platforms and
        SQLite builds, so the verdict is injected at the boundary. That is
        weaker than corrupting a page for real, and worth saying: what it pins
        is that a non-"ok" verdict is *believed* rather than discarded, not
        that SQLite detects any particular damage.
        """

        failing = _FailingConnection(
            self.store,
            fail_on="PRAGMA quick_check",
            error=sqlite3.DatabaseError("database disk image is malformed"),
        )

        report = check_integrity(failing)

        self.assertEqual(report.state, INTEGRITY_CORRUPT)
        self.assertFalse(report.usable)

    def test_a_failing_foreign_key_check_is_also_corrupt(self):
        """The other integrity branch, for the same reason."""

        failing = _FailingConnection(
            self.store,
            fail_on="PRAGMA foreign_key_check",
            error=sqlite3.DatabaseError("malformed"),
        )

        report = check_integrity(failing)

        self.assertEqual(report.state, INTEGRITY_CORRUPT)
        self.assertFalse(report.usable)

    def test_a_deleted_wal_leaves_committed_data_intact(self):
        """WAL files are checkpointed; losing one must not lose committed rows."""

        self._append("committed")
        self.store.close()
        wal = journal_path(self.root).with_name(journal_path(self.root).name + "-wal")
        if wal.exists():
            wal.unlink()

        reopened = open_store(self.root)
        self.addCleanup(reopened.close)

        self.assertEqual(
            [row["event_type"] for row in read_events(reopened)], ["committed"]
        )
        self.assertEqual(check_integrity(reopened).state, INTEGRITY_COMPLETE)


class InterruptedMigrationTests(unittest.TestCase):
    """A migration that fails mid-way must leave a version that was fully applied."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_migration_that_fails_leaves_no_recorded_version(self):
        """Half a schema must never be recorded as a whole one."""

        with _interrupted_migration():
            with self.assertRaises(sqlite3.OperationalError):
                open_store(self.root)

        connection = sqlite3.connect(journal_path(self.root))
        self.addCleanup(connection.close)
        try:
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None

        self.assertIsNone(
            row, "a failed migration must not record a version it did not finish"
        )

    def test_a_retry_after_a_failed_migration_completes_cleanly(self):
        """The interruption must be recoverable, not terminal."""

        with _interrupted_migration():
            with self.assertRaises(sqlite3.OperationalError):
                open_store(self.root)

        store = open_store(self.root)
        self.addCleanup(store.close)
        report = check_integrity(store)

        self.assertEqual(report.state, INTEGRITY_COMPLETE)
        self.assertEqual(report.schema_version, journal_store.compatibility_version())


class ProtectionSurvivesFailureTests(_FaultFixture):
    """The acceptance criterion in its own words, checked end to end.

    "Corrupt/incompatible data ... cannot remove budget, authority or
    verification protection." A store that fails must make callers know less,
    never make them believe something permissive.
    """

    def test_a_corrupt_store_is_not_usable_so_callers_cannot_proceed_on_it(self):
        self._append("approval.granted")
        self.store.execute("UPDATE events SET payload = '' WHERE sequence = 1")
        self.store.close()
        journal_path(self.root).write_bytes(b"garbage")

        health = store_health(self.root)

        self.assertEqual(health["integrity"]["state"], INTEGRITY_CORRUPT)

    def test_an_unfinished_operation_never_reads_as_reconciled(self):
        record_operation(
            self.store,
            operation_key="op-1",
            kind="git.push",
            target_digest="d",
            state="executing",
            now=NOW,
            run_id="run-a",
        )

        with self.assertRaises(sqlite3.OperationalError):
            record_operation(
                _FailingConnection(self.store),
                operation_key="op-1",
                kind="git.push",
                target_digest="d",
                state="reconciled",
                now=NOW,
                run_id="run-a",
            )

        row = self.store.execute(
            "SELECT state, reconciled_at FROM operations WHERE operation_key = 'op-1'"
        ).fetchone()

        self.assertEqual(row["state"], "executing")
        self.assertIsNone(row["reconciled_at"])

    def test_a_degraded_store_is_still_usable_but_says_so(self):
        """Degraded is not blocked -- but it is never silent either."""

        self._append("first")
        bad = self._append("second")
        self.store.execute("UPDATE events SET payload = 'x' WHERE sequence = ?", (bad,))

        report = check_integrity(self.store)

        self.assertTrue(report.usable)
        self.assertEqual(report.state, INTEGRITY_DEGRADED)
        self.assertIsNotNone(report.first_invalid_sequence)


class TheBackupCaseIsNowCoveredTests(unittest.TestCase):
    """#613 lists "unavailable backup". This used to be a skip.

    It was recorded as a skip rather than omitted, so the gap stayed visible in
    test output instead of only in a document beside the code -- a passing test
    would have reported coverage of a feature that did not exist.

    ``vestahub.journal_backup`` now implements requirement 12, so the skip is
    replaced by the fault it was standing in for: recovery attempted when the
    backup is not there.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_recovery_with_no_backup_available_is_refused_by_name(self):
        from vestahub import journal_backup

        record_admission(self.root, task_id="t", run_id="r", task="x", now=NOW_ISO)
        journal_path(self.root).write_bytes(b"not a database")

        self.assertIsNone(journal_backup.latest_backup(self.root))
        report = journal_backup.restore_backup(
            self.root, journal_backup.backup_dir(self.root) / "absent.sqlite3"
        )
        self.assertEqual(report.reason, journal_backup.REFUSE_MISSING)

    def test_a_backup_taken_before_the_damage_recovers_the_runs(self):
        """The case the whole requirement exists for."""

        from vestahub import journal_backup

        record_admission(
            self.root, task_id="t", run_id="survivor", task="x", now=NOW_ISO
        )
        record = journal_backup.create_backup(self.root)
        journal_path(self.root).write_bytes(b"not a database")

        self.assertTrue(journal_backup.restore_backup(self.root, record.path).ok)

        store = open_store(self.root)
        self.addCleanup(store.close)
        self.assertEqual(
            {row[0] for row in store.execute("SELECT run_id FROM runs")},
            {"survivor"},
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
