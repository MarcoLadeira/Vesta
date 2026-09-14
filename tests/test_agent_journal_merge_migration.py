import sqlite3
import unittest

from opaihub import journal_store


class AgentJournalMergeMigrationTests(unittest.TestCase):
    def test_both_previous_branch_schemas_upgrade_without_losing_rows(self):
        for previous in (journal_store._MIGRATION_2, journal_store._MIGRATION_3):
            with (
                self.subTest(previous=previous[0]),
                sqlite3.connect(":memory:", isolation_level=None) as db,
            ):
                db.row_factory = sqlite3.Row
                for statement in journal_store._MIGRATION_1 + previous:
                    db.execute(statement)
                db.execute("INSERT INTO schema_meta VALUES ('schema_version', '2')")
                db.execute(
                    "INSERT INTO tasks (task_id, origin_surface, created_at, schema_version, updated_at) VALUES ('kept-task', 'gui', '2026-09-15', 1, '2026-09-15')"
                )
                if previous is journal_store._MIGRATION_3:
                    db.execute(
                        "INSERT INTO agent_objectives VALUES ('kept-team', 'kept-task', 'kept-run', 'running', '{}', '2026-09-15', '2026-09-15')"
                    )
                journal_store.migrate(db)
                journal_store.migrate(db)
                self.assertEqual(
                    [tuple(row) for row in db.execute("SELECT task_id FROM tasks")],
                    [("kept-task",)],
                )
                self.assertTrue(
                    {"owner_pid", "owner_boot"}
                    <= {row[1] for row in db.execute("PRAGMA table_info(leases)")}
                )
                self.assertTrue(
                    {
                        "agent_objectives",
                        "objective_assignments",
                        "objective_cost_events",
                    }
                    <= {
                        row[0]
                        for row in db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                )
                if previous is journal_store._MIGRATION_3:
                    self.assertEqual(
                        db.execute(
                            "SELECT objective_id FROM agent_objectives"
                        ).fetchone()[0],
                        "kept-team",
                    )
                self.assertEqual(
                    int(
                        db.execute(
                            "SELECT value FROM schema_meta WHERE key='schema_version'"
                        ).fetchone()[0]
                    ),
                    journal_store.compatibility_version(),
                )
