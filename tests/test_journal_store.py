"""#613: the SQLite WAL journal store — schema, fencing, integrity, migrations.

Stage 2 proved each record *can* be rebuilt from a shadow. This is the store
#613 actually asks for: one transactional history with a sequence that is
monotonic across records, operation identity that cannot be claimed twice,
fencing that stops a stale supervisor, and integrity that is typed rather than
silently emptied.

The tests worth reading are the ones about failure, because that is where the
issue is specific:

- a database from a *newer* Vesta is ``incompatible``, not ``corrupt`` -- those
  want opposite responses (upgrade vs recover), and collapsing them sends the
  user the wrong way;
- an unreadable event payload is ``degraded`` **with the first bad sequence**,
  not a short read, so a caller knows where its view stops being trustworthy;
- a stale fence is refused rather than merged, which is the acceptance
  criterion about lease takeover.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from opaihub import journal_store
from opaihub.journal_store import (
    INTEGRITY_COMPLETE,
    INTEGRITY_CORRUPT,
    INTEGRITY_DEGRADED,
    INTEGRITY_INCOMPATIBLE,
    IncompatibleSchemaError,
    JournalStoreError,
    StaleWriterError,
    acquire_lease,
    append_event,
    check_integrity,
    journal_path,
    open_store,
    read_events,
    record_cost,
    record_operation,
    release_lease,
    store_health,
)

NOW = "2026-08-23T12:00:00+00:00"


class _StoreFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = open_store(self.root)
        self.addCleanup(self.store.close)

    def _task(self, task_id: str = "task-a") -> str:
        self.store.execute(
            "INSERT INTO tasks(task_id, origin_surface, created_at, schema_version,"
            " updated_at) VALUES (?, 'cli', ?, 1, ?)",
            (task_id, NOW, NOW),
        )
        return task_id

    def _run(self, run_id: str = "run-a", *, task_id: str = "task-a") -> str:
        self.store.execute(
            "INSERT INTO runs(run_id, task_id, attempt, desired_state,"
            " observed_state, created_at, updated_at)"
            " VALUES (?, ?, 1, 'running', 'queued', ?, ?)",
            (run_id, task_id, NOW, NOW),
        )
        return run_id


class SchemaAndMigrationTests(_StoreFixture):
    def test_the_store_opens_in_wal_mode(self):
        """WAL is the reason this is SQLite; a silent fallback would matter."""

        mode = self.store.execute("PRAGMA journal_mode").fetchone()[0]

        self.assertEqual(str(mode).lower(), "wal")

    def test_every_required_table_exists(self):
        names = {
            row[0]
            for row in self.store.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        self.assertLessEqual(
            {
                "tasks",
                "runs",
                "events",
                "operations",
                "approvals",
                "artifacts",
                "cost_events",
                "leases",
                "projections",
            },
            names,
        )

    def test_migrating_twice_is_a_no_op(self):
        first = journal_store.migrate(self.store)
        second = journal_store.migrate(self.store)

        self.assertEqual(first, journal_store.SCHEMA_VERSION)
        self.assertEqual(second, journal_store.SCHEMA_VERSION)

    def test_reopening_an_existing_store_keeps_its_data(self):
        self._task()
        self._run()
        append_event(
            self.store,
            event_type="run.queued",
            payload={"detail": "first"},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="test",
            run_id="run-a",
        )
        self.store.close()

        reopened = open_store(self.root)
        self.addCleanup(reopened.close)

        self.assertEqual(len(read_events(reopened)), 1)

    def test_a_newer_database_is_refused_rather_than_migrated_down(self):
        self.store.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '999')"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )

        with self.assertRaises(IncompatibleSchemaError):
            journal_store.migrate(self.store)


class MonotonicSequenceTests(_StoreFixture):
    def test_sequences_increase_and_are_never_reused(self):
        """Reuse would make two different events indistinguishable in replay."""

        self._task()
        self._run()
        first = append_event(
            self.store,
            event_type="a",
            payload={},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="test",
            run_id="run-a",
        )
        second = append_event(
            self.store,
            event_type="b",
            payload={},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="test",
            run_id="run-a",
        )
        self.assertGreater(second, first)

        self.store.execute("DELETE FROM events WHERE sequence = ?", (second,))
        third = append_event(
            self.store,
            event_type="c",
            payload={},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="test",
            run_id="run-a",
        )

        self.assertGreater(third, second, "a deleted sequence must not be reused")

    def test_concurrent_appends_all_land_with_distinct_sequences(self):
        self._task()
        self._run()

        def append(index: int) -> int:
            connection = open_store(self.root)
            try:
                return append_event(
                    connection,
                    event_type=f"event-{index}",
                    payload={"index": index},
                    occurred_at=NOW,
                    recorded_at=NOW,
                    producer="test",
                    run_id="run-a",
                )
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            sequences = list(pool.map(append, range(16)))

        self.assertEqual(len(set(sequences)), 16)
        self.assertEqual(len(read_events(self.store)), 16)

    def test_a_reader_is_not_blocked_by_an_open_writer(self):
        """The WAL property acceptance criterion 8 depends on."""

        self._task()
        self._run()
        reader = open_store(self.root)
        self.addCleanup(reader.close)

        self.store.execute("BEGIN IMMEDIATE")
        self.store.execute(
            "INSERT INTO events(run_id, event_type, event_schema_version,"
            " occurred_at, recorded_at, producer, payload, payload_hash,"
            " privacy_class) VALUES ('run-a', 'x', 1, ?, ?, 'test', '{}', '', 'internal')",
            (NOW, NOW),
        )
        try:
            # Must not raise "database is locked".
            rows = read_events(reader)
        finally:
            self.store.execute("ROLLBACK")

        self.assertEqual(rows, [])


class FencingTests(_StoreFixture):
    """#613: stale supervisors cannot write after lease takeover."""

    def setUp(self) -> None:
        super().setUp()
        self._task()
        self._run()

    def test_a_fence_increases_on_every_acquisition(self):
        first = acquire_lease(self.store, run_id="run-a", owner="worker-1", now=NOW)
        second = acquire_lease(self.store, run_id="run-a", owner="worker-2", now=NOW)

        self.assertEqual(first, 1)
        self.assertEqual(second, 2)

    def test_the_current_holder_can_write(self):
        fence = acquire_lease(self.store, run_id="run-a", owner="worker-1", now=NOW)

        sequence = append_event(
            self.store,
            event_type="run.started",
            payload={},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="worker-1",
            run_id="run-a",
            expected_fence=fence,
        )

        self.assertGreater(sequence, 0)

    def test_a_fenced_out_writer_is_refused(self):
        stale = acquire_lease(self.store, run_id="run-a", owner="worker-1", now=NOW)
        acquire_lease(self.store, run_id="run-a", owner="worker-2", now=NOW)

        with self.assertRaises(StaleWriterError):
            append_event(
                self.store,
                event_type="run.finished",
                payload={},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="worker-1",
                run_id="run-a",
                expected_fence=stale,
            )

    def test_a_refused_write_leaves_no_event_behind(self):
        """Teeth: the refusal must roll back, not merely raise after inserting."""

        stale = acquire_lease(self.store, run_id="run-a", owner="worker-1", now=NOW)
        acquire_lease(self.store, run_id="run-a", owner="worker-2", now=NOW)

        with self.assertRaises(StaleWriterError):
            append_event(
                self.store,
                event_type="run.finished",
                payload={},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="worker-1",
                run_id="run-a",
                expected_fence=stale,
            )

        self.assertEqual(read_events(self.store), [])

    def test_writing_after_release_is_refused(self):
        fence = acquire_lease(self.store, run_id="run-a", owner="worker-1", now=NOW)
        self.assertTrue(release_lease(self.store, run_id="run-a", fence=fence, now=NOW))

        with self.assertRaises(StaleWriterError):
            append_event(
                self.store,
                event_type="late",
                payload={},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="worker-1",
                run_id="run-a",
                expected_fence=fence,
            )

    def test_a_stale_holder_cannot_release_the_new_holders_lease(self):
        stale = acquire_lease(self.store, run_id="run-a", owner="worker-1", now=NOW)
        acquire_lease(self.store, run_id="run-a", owner="worker-2", now=NOW)

        self.assertFalse(
            release_lease(self.store, run_id="run-a", fence=stale, now=NOW)
        )


class OperationIdentityTests(_StoreFixture):
    def setUp(self) -> None:
        super().setUp()
        self._task()
        self._run()

    def test_a_duplicate_claim_does_not_create_a_second_operation(self):
        created = record_operation(
            self.store,
            operation_key="op-1",
            kind="git.push",
            target_digest="abc",
            state="intended",
            now=NOW,
            run_id="run-a",
        )
        again = record_operation(
            self.store,
            operation_key="op-1",
            kind="git.push",
            target_digest="abc",
            state="executing",
            now=NOW,
            run_id="run-a",
        )

        self.assertTrue(created)
        self.assertFalse(again, "the second claim must not create a new operation")
        count = self.store.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
        self.assertEqual(count, 1)

    def test_an_unknown_operation_state_is_refused(self):
        with self.assertRaises(ValueError):
            record_operation(
                self.store,
                operation_key="op-2",
                kind="git.push",
                target_digest="abc",
                state="probably-fine",
                now=NOW,
            )

    def test_a_cost_can_only_be_attributed_once(self):
        """#613's property requirement, enforced by the schema rather than audit."""

        record_operation(
            self.store,
            operation_key="op-1",
            kind="model.call",
            target_digest="abc",
            state="observed",
            now=NOW,
            run_id="run-a",
        )
        record_cost(
            self.store,
            operation_key="op-1",
            amount=0.42,
            measurement_kind="actual",
            now=NOW,
        )

        with self.assertRaises(JournalStoreError):
            record_cost(
                self.store,
                operation_key="op-1",
                amount=0.42,
                measurement_kind="actual",
                now=NOW,
            )

    def test_an_unknown_measurement_kind_is_refused(self):
        record_operation(
            self.store,
            operation_key="op-1",
            kind="model.call",
            target_digest="abc",
            state="observed",
            now=NOW,
        )

        with self.assertRaises(ValueError):
            record_cost(
                self.store,
                operation_key="op-1",
                amount=1.0,
                measurement_kind="vibes",
                now=NOW,
            )

    def test_a_cost_without_an_operation_is_refused(self):
        """Foreign keys are on; an orphan cost is unattributable by definition."""

        with self.assertRaises((sqlite3.IntegrityError, JournalStoreError)):
            record_cost(
                self.store,
                operation_key="never-claimed",
                amount=1.0,
                measurement_kind="actual",
                now=NOW,
            )


class IntegrityIsTypedTests(_StoreFixture):
    def test_a_healthy_store_is_complete(self):
        report = check_integrity(self.store)

        self.assertEqual(report.state, INTEGRITY_COMPLETE)
        self.assertTrue(report.usable)
        self.assertIn("quick_check", report.checks)

    def test_a_newer_schema_is_incompatible_not_corrupt(self):
        """Different failures, opposite responses: upgrade vs recover."""

        self.store.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '999')"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )

        report = check_integrity(self.store)

        self.assertEqual(report.state, INTEGRITY_INCOMPATIBLE)
        self.assertFalse(report.usable)
        self.assertNotEqual(report.state, INTEGRITY_CORRUPT)

    def test_an_unreadable_payload_is_degraded_with_the_first_bad_sequence(self):
        """A caller must know *where* its view stops being trustworthy."""

        self._task()
        self._run()
        good = append_event(
            self.store,
            event_type="a",
            payload={"n": 1},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="test",
            run_id="run-a",
        )
        bad = append_event(
            self.store,
            event_type="b",
            payload={"n": 2},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="test",
            run_id="run-a",
        )
        self.store.execute(
            "UPDATE events SET payload = '{not json' WHERE sequence = ?", (bad,)
        )

        report = check_integrity(self.store)

        self.assertEqual(report.state, INTEGRITY_DEGRADED)
        self.assertEqual(report.first_invalid_sequence, bad)
        self.assertTrue(report.usable, "degraded is still actionable")
        self.assertNotEqual(report.first_invalid_sequence, good)

    def test_an_unreadable_event_is_surfaced_not_skipped(self):
        """Skipping would turn a gap into a shorter, plausible-looking history."""

        self._task()
        self._run()
        for index in range(3):
            append_event(
                self.store,
                event_type=f"e{index}",
                payload={"index": index},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="test",
                run_id="run-a",
            )
        self.store.execute("UPDATE events SET payload = 'nope' WHERE sequence = 2")

        rows = read_events(self.store)

        self.assertEqual(len(rows), 3, "the row count must not shrink")
        self.assertFalse(rows[1]["readable"])
        self.assertIsNone(rows[1]["payload"])
        self.assertTrue(rows[0]["readable"] and rows[2]["readable"])

    def test_corruption_never_becomes_an_empty_permissive_read(self):
        """The failure #613 names explicitly, checked end to end."""

        self._task()
        self._run()
        append_event(
            self.store,
            event_type="approval.granted",
            payload={"scope": "push"},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="test",
            run_id="run-a",
        )
        self.store.execute("UPDATE events SET payload = '' WHERE sequence = 1")

        report = check_integrity(self.store)
        rows = read_events(self.store)

        self.assertNotEqual(report.state, INTEGRITY_COMPLETE)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["readable"])


class HealthReportingTests(unittest.TestCase):
    """Doctor/preflight: acceptance criterion about visible integrity."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_project_with_no_journal_reports_absent_not_broken(self):
        health = store_health(self.root)

        self.assertFalse(health["present"])
        self.assertEqual(health["integrity"]["state"], INTEGRITY_COMPLETE)

    def test_a_healthy_journal_reports_wal_and_complete(self):
        connection = open_store(self.root)
        connection.close()

        health = store_health(self.root)

        self.assertTrue(health["present"])
        self.assertEqual(str(health["journal_mode"]).lower(), "wal")
        self.assertEqual(health["integrity"]["state"], INTEGRITY_COMPLETE)

    def test_a_corrupt_file_reports_corrupt_rather_than_raising(self):
        connection = open_store(self.root)
        connection.close()
        journal_path(self.root).write_bytes(b"this is not a database")

        health = store_health(self.root)

        self.assertTrue(health["present"])
        self.assertEqual(health["integrity"]["state"], INTEGRITY_CORRUPT)

    def test_the_health_report_is_json_serialisable(self):
        """It goes into doctor output, so it has to survive serialisation."""

        connection = open_store(self.root)
        connection.close()

        json.dumps(store_health(self.root))


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class CostAmountsMustBeRealSpendTests(_StoreFixture):
    """A spend record that is negative or non-finite is not a spend record.

    NaN is the dangerous one. It round-trips through JSON, and every
    ``spent + cost > limit`` comparison against it is false -- so a single NaN
    would silently disable the budget ceiling it was meant to count against,
    which is the exact failure `budget.py` already guards its own caps from.
    """

    def _operation(self, key: str = "op-1") -> str:
        self._task()
        self._run()
        record_operation(
            self.store,
            operation_key=key,
            kind="model.call",
            target_digest="d",
            state="observed",
            now=NOW,
            run_id="run-a",
        )
        return key

    def test_a_negative_cost_is_refused(self):
        key = self._operation()

        with self.assertRaises(ValueError):
            record_cost(
                self.store,
                operation_key=key,
                amount=-5.0,
                measurement_kind="actual",
                now=NOW,
            )

        total = self.store.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM cost_events"
        ).fetchone()[0]
        self.assertEqual(float(total), 0.0)

    def test_a_nan_cost_is_refused(self):
        key = self._operation()

        with self.assertRaises(ValueError):
            record_cost(
                self.store,
                operation_key=key,
                amount=float("nan"),
                measurement_kind="actual",
                now=NOW,
            )

    def test_an_infinite_cost_is_refused(self):
        key = self._operation()

        with self.assertRaises(ValueError):
            record_cost(
                self.store,
                operation_key=key,
                amount=float("inf"),
                measurement_kind="actual",
                now=NOW,
            )

    def test_zero_is_a_legitimate_cost(self):
        """Teeth the other way: a free local route really does cost nothing."""

        key = self._operation()

        record_cost(
            self.store,
            operation_key=key,
            amount=0.0,
            measurement_kind="actual",
            now=NOW,
        )

        total = self.store.execute("SELECT SUM(amount) FROM cost_events").fetchone()[0]
        self.assertEqual(float(total), 0.0)
