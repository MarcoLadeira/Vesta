"""#818: two processes creating a journal together must not brick it.

Found by an independent review of this branch, and it was this branch's bug.
`main` has one migration, whose every statement is `CREATE TABLE IF NOT
EXISTS`, so re-running it is harmless. This branch added migration 2 --
`ALTER TABLE leases ADD COLUMN` -- which SQLite cannot express idempotently.

`migrate()` read the schema version *before* taking any write lock. Two
processes creating a journal together both read 0. One migrated fully to 2 and
committed. The other, still believing 0, re-ran migration 1 silently and wrote
version **1 over the 2**, then failed on migration 2 with `duplicate column
name` -- and kept failing, because the recorded version now claimed that
migration had never been applied. Every subsequent `open_store` raised, on a
database that was structurally perfect.

Measured before the fix: 2 of 25 rounds of six concurrent processes, and 1 of
20 with two real `vesta ask` turns on a fresh project. Only *creation* races;
upgrading an existing journal was never affected.

Worse, `store_health` called the result `complete` and doctor called the
project ready, because integrity is checked on a raw connection that never
migrates. A confident answer with nothing behind it, inside the store this
epic exists to make authoritative. Both halves are pinned here.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vestahub import journal_store

SRC = str(Path(__file__).resolve().parents[1])


class _FreshJournal(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = journal_store.journal_path(self.root)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def version(self) -> int:
        connection = sqlite3.connect(self.path)
        try:
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            connection.close()

    def lease_columns(self) -> set[str]:
        connection = sqlite3.connect(self.path)
        try:
            return {row[1] for row in connection.execute("PRAGMA table_info(leases)")}
        finally:
            connection.close()


class AStaleWriterCannotUndoAMigrationTests(_FreshJournal):
    """The exact interleaving, forced rather than waited for."""

    def _migrate_with_a_stale_read(self, connection: sqlite3.Connection) -> None:
        """Run migrate() as a process whose opening read predates the winner."""

        real = journal_store._stored_version
        seen = {"calls": 0}

        def stale_first(target):
            seen["calls"] += 1
            return 0 if seen["calls"] == 1 else real(target)

        with mock.patch.object(journal_store, "_stored_version", stale_first):
            journal_store.migrate(connection)

    def test_the_loser_does_not_break_the_journal(self):
        winner = journal_store._connect(self.path)
        loser = journal_store._connect(self.path)
        self.addCleanup(winner.close)
        self.addCleanup(loser.close)

        journal_store.migrate(winner)
        self.assertEqual(self.version(), journal_store.compatibility_version())

        self._migrate_with_a_stale_read(loser)

        self.assertEqual(
            self.version(),
            journal_store.compatibility_version(),
            "a stale writer dragged the recorded schema version backwards",
        )

    def test_the_journal_still_opens_afterwards(self):
        winner = journal_store._connect(self.path)
        loser = journal_store._connect(self.path)
        self.addCleanup(winner.close)
        self.addCleanup(loser.close)

        journal_store.migrate(winner)
        self._migrate_with_a_stale_read(loser)

        store = journal_store.open_store(self.root)
        store.close()

    def test_the_version_can_only_move_forward(self):
        """The second guard, exercised through the production statement.

        The first version of this test wrote the upsert SQL itself, which
        proved SQLite's MAX works and nothing about whether Vesta uses it --
        teeth-testing caught it by sabotaging the real statement and watching
        the test stay green.
        """

        journal_store.open_store(self.root).close()
        connection = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(connection.close)
        journal_store._record_schema_version(connection, 5)

        journal_store._record_schema_version(connection, 1)

        self.assertEqual(
            self.version(), 5, "a lower version was written over a higher one"
        )

    def test_a_higher_version_is_still_recorded(self):
        """Monotonic must not mean frozen."""

        journal_store.open_store(self.root).close()
        connection = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(connection.close)

        journal_store._record_schema_version(
            connection, journal_store.SCHEMA_VERSION + 5
        )

        self.assertEqual(self.version(), journal_store.SCHEMA_VERSION + 5)

    def test_a_genuinely_newer_schema_is_still_refused(self):
        """The monotonic guard must not swallow a real incompatibility."""

        journal_store.open_store(self.root).close()
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            (str(journal_store.SCHEMA_VERSION + 1),),
        )
        connection.commit()
        connection.close()

        with self.assertRaises(journal_store.IncompatibleSchemaError):
            store = journal_store.open_store(self.root)
            store.close()


class RealConcurrentProcessesTests(_FreshJournal):
    """Threads share an interpreter. The processes that raced did not."""

    def test_six_processes_creating_one_journal_together(self):
        script = (
            "import sys\n"
            f"sys.path.insert(0, r'{SRC}')\n"
            "from pathlib import Path\n"
            "from vestahub import journal_store\n"
            "try:\n"
            f"    store = journal_store.open_store(Path(r'{self.root}'))\n"
            "    store.close()\n"
            "    print('OK')\n"
            "except Exception as exc:\n"
            "    print(f'{type(exc).__name__}: {exc}')\n"
        )
        children = [
            subprocess.Popen(  # nosec B603 - fixed argv, this interpreter
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                text=True,
            )
            for _ in range(6)
        ]
        answers = [child.communicate(timeout=120)[0].strip() for child in children]

        duplicates = [a for a in answers if "duplicate column" in a]
        self.assertEqual(
            duplicates,
            [],
            f"a concurrent creation hit the non-idempotent migration: {answers}",
        )
        self.assertEqual(
            self.version(),
            journal_store.compatibility_version(),
            f"the journal was left below the current schema: {answers}",
        )

    def test_the_journal_is_usable_after_the_race(self):
        self.test_six_processes_creating_one_journal_together()

        store = journal_store.open_store(self.root)
        try:
            columns = {row[1] for row in store.execute("PRAGMA table_info(leases)")}
        finally:
            store.close()

        self.assertIn("owner_pid", columns)
        self.assertIn("owner_boot", columns)


class TheShapeTheRaceLeftBehindNowHealsTests(_FreshJournal):
    """v1 recorded, v2's columns already present -- once a bricked journal.

    Migrations are now checked against the database rather than trusted from
    the recorded number, so a statement whose effect is already there is not
    run again. The journal the race used to brick simply opens, and is
    stamped with the version it really has.
    """

    def race_shape(self) -> None:
        journal_store.open_store(self.root).close()
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'"
        )
        connection.commit()
        connection.close()

    def test_it_opens(self):
        self.race_shape()

        store = journal_store.open_store(self.root)
        store.close()

        self.assertEqual(self.version(), journal_store.compatibility_version())

    def test_health_calls_it_openable(self):
        self.race_shape()

        health = journal_store.store_health(self.root)

        self.assertTrue(health["openable"], health["open_error"])


class AJournalNobodyCanOpenIsNotHealthyTests(_FreshJournal):
    """The other half: doctor must not call it fine.

    `check_integrity` reads a raw connection, so it answers "is this database
    structurally sound" -- a different question from "can Vesta use it". A
    journal whose migration cannot complete passes every structural check and
    refuses every write.
    """

    def brick(self) -> None:
        """A table squatting on the name one of v1's indexes needs.

        The race shape above no longer bricks anything, so this is a journal
        that genuinely cannot be migrated: the index is missing, and the
        statement that would create it collides with an object of another
        kind.
        """

        journal_store.open_store(self.root).close()
        connection = sqlite3.connect(self.path)
        connection.execute("DROP INDEX events_by_run")
        connection.execute("CREATE TABLE events_by_run (squatter TEXT)")
        connection.execute(
            "UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'"
        )
        connection.commit()
        connection.close()

    def test_open_store_really_does_fail(self):
        self.brick()

        with self.assertRaises(sqlite3.OperationalError):
            store = journal_store.open_store(self.root)
            store.close()

    def test_store_health_says_it_cannot_be_opened(self):
        self.brick()

        health = journal_store.store_health(self.root)

        self.assertFalse(health["openable"])
        self.assertIn("already a table", health["open_error"])

    def test_the_file_is_still_reported_as_structurally_sound(self):
        """Both answers are true, and they are kept apart on purpose."""

        self.brick()

        health = journal_store.store_health(self.root)

        self.assertEqual(health["integrity"]["state"], "complete")
        self.assertFalse(health["openable"])

    def test_doctor_escalates_it(self):
        from vesta import cli

        self.brick()
        health = journal_store.store_health(self.root)

        self.assertTrue(
            cli._journal_needs_attention(
                {"available": True, "present": True, **health}
            ),
            "doctor called a journal healthy that nothing can open",
        )

    def test_a_healthy_journal_is_still_openable_and_not_escalated(self):
        from vesta import cli

        journal_store.open_store(self.root).close()
        health = journal_store.store_health(self.root)

        self.assertTrue(health["openable"])
        self.assertEqual(health["open_error"], "")
        self.assertFalse(
            cli._journal_needs_attention({"available": True, "present": True, **health})
        )

    def test_a_project_with_no_journal_is_not_reported_as_unopenable(self):
        health = journal_store.store_health(self.root)

        self.assertFalse(health["present"])
        self.assertTrue(health["openable"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
