"""#818: a migration is trusted only once the database shows it happened.

Two pull requests open at the same time each defined journal migration **2**
with different contents -- lease owner columns in #847, agent-objective tables
in #842. Applied by number alone, whichever build touched a journal first
stamped it v2, and the other build then skipped its own v2 for ever: its
columns or tables simply never existed, and every write that needed them
failed on a database every structural check called perfect.

`migrate()` now verifies each migration against the catalogue and applies only
what is missing, so a journal stamped by either build -- or by a merge that
renumbered one of them -- heals the first time the merged build opens it.

That only holds while every migration statement is one `migrate()` can check,
and while version numbers stay unique. Both are ratcheted here, so a merge that
resolves the collision by keeping two ``(2, ...)`` entries fails loudly instead
of shipping.
"""

from __future__ import annotations

import contextlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import journal_store

SRC = str(Path(__file__).resolve().parents[1])

#: The shape #842's build leaves behind: its own migration 2 applied, recorded
#: as v2, and none of this branch's migration 2.
_ANOTHER_BUILDS_MIGRATION_2 = (
    """CREATE TABLE IF NOT EXISTS agent_objectives (
        objective_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        payload TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS objectives_by_run ON agent_objectives(run_id)",
)


class EveryMigrationCanBeCheckedTests(unittest.TestCase):
    def test_every_statement_is_a_shape_migrate_can_verify(self):
        unverifiable = [
            (version, statement.split("\n", 1)[0])
            for version, statements in journal_store._MIGRATIONS
            for statement in statements
            if not journal_store.migration_statement_is_verifiable(statement)
        ]

        self.assertEqual(
            unverifiable,
            [],
            "migrate() cannot tell whether these already happened, so a journal"
            " stamped by another build would skip them for ever. Use CREATE"
            " TABLE/INDEX IF NOT EXISTS or ALTER TABLE ... ADD COLUMN.",
        )

    def test_another_builds_statements_are_verifiable_too(self):
        for statement in _ANOTHER_BUILDS_MIGRATION_2:
            self.assertTrue(
                journal_store.migration_statement_is_verifiable(statement),
                statement,
            )

    def test_the_predicate_refuses_what_it_cannot_check(self):
        for statement in (
            "UPDATE runs SET state = 'x'",
            "CREATE TABLE runs_without_a_guard (x)",
            "DROP TABLE leases",
            "ALTER TABLE leases RENAME TO old_leases",
        ):
            self.assertFalse(
                journal_store.migration_statement_is_verifiable(statement),
                statement,
            )


class VersionNumbersStayUniqueTests(unittest.TestCase):
    """The collision itself: two builds, one number."""

    def test_versions_are_unique_contiguous_and_end_at_the_schema_version(self):
        versions = [version for version, _statements in journal_store._MIGRATIONS]

        self.assertEqual(
            versions,
            list(range(1, len(versions) + 1)),
            "migration versions must be 1..N with no repeats -- a merge of two"
            " branches that each added a migration must renumber one of them",
        )
        self.assertEqual(versions[-1], journal_store.SCHEMA_VERSION)

    def test_no_object_or_column_is_created_by_two_migrations(self):
        seen: dict[str, int] = {}
        repeated = []
        for version, statements in journal_store._MIGRATIONS:
            for statement in statements:
                created = journal_store._CREATES_OBJECT.match(statement)
                added = journal_store._ADDS_COLUMN.match(statement)
                if created is not None:
                    key = created.group(2).lower()
                elif added is not None:
                    key = f"{added.group(1)}.{added.group(2)}".lower()
                else:
                    continue
                if key in seen:
                    repeated.append((key, seen[key], version))
                seen[key] = version

        self.assertEqual(repeated, [])


class _Journal(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = journal_store.journal_path(self.root)

    def raw(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        self.addCleanup(connection.close)
        return connection

    def version(self) -> int:
        row = (
            self.raw()
            .execute("SELECT value FROM schema_meta WHERE key = 'schema_version'")
            .fetchone()
        )
        return int(row[0])

    def lease_columns(self) -> set[str]:
        return {row[1] for row in self.raw().execute("PRAGMA table_info(leases)")}

    def tables(self) -> set[str]:
        return {
            row[0]
            for row in self.raw().execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    def stamped_by_another_build(self) -> None:
        """v1, then the other build's v2 instead of ours, recorded as v2."""

        journal_store.open_store(self.root).close()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("ALTER TABLE leases DROP COLUMN owner_boot")
            connection.execute("ALTER TABLE leases DROP COLUMN owner_pid")
            for statement in _ANOTHER_BUILDS_MIGRATION_2:
                connection.execute(statement)
            connection.execute(
                "UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'"
            )
            connection.commit()
        finally:
            connection.close()


class AJournalAnotherBuildStampedHealsTests(_Journal):
    def test_the_missing_columns_are_added_despite_the_version_claiming_them(self):
        self.stamped_by_another_build()
        self.assertNotIn("owner_pid", self.lease_columns())

        journal_store.open_store(self.root).close()

        self.assertIn("owner_pid", self.lease_columns())
        self.assertIn("owner_boot", self.lease_columns())
        self.assertEqual(self.version(), journal_store.SCHEMA_VERSION)

    def test_the_other_builds_tables_are_left_exactly_as_they_were(self):
        self.stamped_by_another_build()
        connection = sqlite3.connect(self.path)
        connection.execute(
            "INSERT INTO agent_objectives VALUES ('objective-1', 'run-1', '{}')"
        )
        connection.commit()
        connection.close()

        journal_store.open_store(self.root).close()

        self.assertIn("agent_objectives", self.tables())
        rows = self.raw().execute("SELECT objective_id FROM agent_objectives")
        self.assertEqual([row[0] for row in rows], ["objective-1"])

    def test_a_lease_can_be_taken_on_the_healed_journal(self):
        from opaihub import journal_runtime

        self.stamped_by_another_build()

        fence = journal_runtime.record_admission(
            self.root,
            task_id="task-heal",
            run_id="run-heal",
            task="hello",
            now="2026-09-13T10:00:00Z",
        )

        self.assertIsNotNone(fence, "admission could not take a lease")
        owner = (
            self.raw()
            .execute("SELECT owner_pid FROM leases WHERE run_id = 'run-heal'")
            .fetchone()
        )
        self.assertIsNotNone(owner)

    def test_a_missing_table_from_an_earlier_migration_is_recreated(self):
        journal_store.open_store(self.root).close()
        connection = sqlite3.connect(self.path)
        connection.execute("DROP TABLE cost_events")
        connection.commit()
        connection.close()

        journal_store.open_store(self.root).close()

        self.assertIn("cost_events", self.tables())

    def test_health_reports_the_stamped_journal_as_openable_and_does_not_heal_it(
        self,
    ):
        self.stamped_by_another_build()

        health = journal_store.store_health(self.root)

        self.assertTrue(health["openable"], health["open_error"])
        self.assertNotIn("owner_pid", self.lease_columns())


class ACompleteJournalCostsNoWritesTests(_Journal):
    def test_opening_a_complete_journal_while_another_process_writes(self):
        journal_store.open_store(self.root).close()
        writer = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute("BEGIN IMMEDIATE")
        try:
            store = journal_store.open_store(self.root, timeout=0.05)
            store.close()
        finally:
            writer.execute("ROLLBACK")

    def test_the_recorded_version_is_not_rewritten(self):
        journal_store.open_store(self.root).close()
        watcher = self.raw()
        before = watcher.execute("PRAGMA data_version").fetchone()[0]

        journal_store.open_store(self.root).close()

        self.assertEqual(
            watcher.execute("PRAGMA data_version").fetchone()[0],
            before,
            "opening a complete journal committed a write",
        )


class HealingUnderConcurrencyTests(_Journal):
    def test_a_process_that_healed_it_while_this_one_waited_for_the_lock(self):
        """Deterministic: the other process commits between look and lock.

        Six real processes rarely land inside a two-millisecond window, so the
        test below cannot be relied on to catch a decision made from the
        catalogue as it was *before* the lock. This one arranges it: the moment
        this open asks for the write lock, another connection heals the journal
        first. Deciding from the stale catalogue re-runs `ADD COLUMN` and fails
        with `duplicate column name` -- the bricking bug, reintroduced.
        """

        self.stamped_by_another_build()
        real = journal_store._transaction
        raced: list[bool] = []

        @contextlib.contextmanager
        def another_process_gets_there_first(connection):
            if not raced:
                raced.append(True)
                other = journal_store._connect(self.path)
                try:
                    journal_store.migrate(other)
                finally:
                    other.close()
            with real(connection):
                yield connection

        with mock.patch.object(
            journal_store, "_transaction", another_process_gets_there_first
        ):
            store = journal_store.open_store(self.root)
            store.close()

        self.assertEqual(raced, [True], "the interleaving never happened")
        self.assertIn("owner_pid", self.lease_columns())

    def test_six_processes_healing_one_journal_together(self):
        self.stamped_by_another_build()
        script = (
            "import sys\n"
            f"sys.path.insert(0, r'{SRC}')\n"
            "from pathlib import Path\n"
            "from opaihub import journal_store\n"
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

        self.assertEqual(answers, ["OK"] * 6)
        self.assertIn("owner_pid", self.lease_columns())
        self.assertEqual(self.version(), journal_store.SCHEMA_VERSION)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
