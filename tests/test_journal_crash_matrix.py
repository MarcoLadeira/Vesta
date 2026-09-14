"""#613 crash matrix: termination at every lifecycle boundary.

The first acceptance criterion is that a run can be reconstructed after process
termination at every lifecycle boundary, and the required-tests section asks
for termination injected before and after every transaction and
external-operation checkpoint, proving *no duplicate active owner, no terminal
regression, and no silent loss*.

Termination is simulated two ways, and the distinction matters.

**Killing the transaction.** Raising *after an INSERT and before COMMIT*, so
the rollback is SQLite's rather than an accident of where the failure landed.
Only one test here does that, and it is labelled as such -- teeth-testing the
first draft showed the obvious candidates (an unencodable payload, a refused
fence) never open a transaction at all, so they proved nothing about rollback
while appearing to. Swapping ROLLBACK for COMMIT in the store now fails exactly
one test, which is the point.

**Killing the process.** A real subprocess that writes and then dies via
``os._exit``, skipping atexit handlers and destructors so the connection is
never closed. A crash *after* COMMIT must leave the row present; a crash
*before* it must leave nothing.

Worth stating plainly: those two outcomes also hold for a *clean* exit, so
these tests do not isolate WAL recovery specifically -- a tidy shutdown would
pass them too. What they do prove is that abrupt death produces the same
answers as an orderly one, which is the property that matters to a caller and
the one that would break if the store depended on cleanup that a killed process
never performs.

The gap this file does not paper over: killing a process between an external
effect and its outcome record cannot be simulated without a real external
effect. What is proved here is that the *operation row* survives saying "we do
not know", which is what lets reconciliation run later. Whether the provider
actually charged us is outside any local store's knowledge.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed argv, this test's own interpreter
import sys
import tempfile
import textwrap
import unittest

from opaihub import journal_runtime
from pathlib import Path

from opaihub import journal_store
from opaihub.journal_store import (
    INTEGRITY_COMPLETE,
    StaleWriterError,
    acquire_lease,
    append_event,
    check_integrity,
    compatibility_version,
    open_store,
    read_events,
    record_cost,
    record_operation,
)

NOW = "2026-08-23T12:00:00+00:00"
_REPO = Path(__file__).resolve().parent.parent


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


def _run_child(root: Path, body: str) -> subprocess.CompletedProcess:
    """Run a snippet in a real interpreter that dies without cleaning up.

    ``os._exit`` skips atexit handlers, buffered flushes and destructors, which
    is the point: a killed process never closes its SQLite connection, and
    anything that only worked because Python tidied up would be proving the
    wrong thing.
    """

    script = textwrap.dedent("""
        import os, sys
        sys.path.insert(0, {repo!r})
        from pathlib import Path
        from opaihub.journal_store import (
            acquire_lease, append_event, open_store, record_operation,
        )
        root = Path({root!r})
        NOW = {now!r}
        store = open_store(root)
        {body}
        os._exit(9)
    """).format(repo=str(_REPO), root=str(root), now=NOW, body=body)
    return subprocess.run(  # nosec B603 - fixed argv, this interpreter, temp dir
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
    )


CRASH_NOW = "2026-09-08T10:00:00+00:00"


class _CrashFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = open_store(self.root)
        self.addCleanup(self.store.close)
        _seed(self.store)

    def _append(self, event_type: str) -> int:
        return append_event(
            self.store,
            event_type=event_type,
            payload={"t": event_type},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="test",
            run_id="run-a",
        )


class TerminationInsideATransactionLeavesNothingTests(_CrashFixture):
    """Crash between BEGIN and COMMIT: the rollback must be SQLite's, not ours."""

    def test_a_failure_after_an_insert_rolls_the_insert_back(self):
        """The one test here that actually exercises ROLLBACK.

        Written after teeth-testing showed the others do not. ``append_event``
        serialises its payload *before* ``BEGIN`` -- good design, since it does
        not hold a write lock while encoding -- so an unserialisable payload
        raises without ever opening a transaction, and a refused fence raises
        before any INSERT. Both leave the store unchanged for reasons that have
        nothing to do with rollback.

        This one inserts first and *then* fails, which is the only shape that
        distinguishes a real rollback from a no-op. Swapping ROLLBACK for
        COMMIT in the store fails exactly this test and nothing else.
        """

        before = len(read_events(self.store))

        with self.assertRaises(RuntimeError):
            with journal_store._transaction(self.store):
                self.store.execute(
                    "INSERT INTO events(run_id, event_type, event_schema_version,"
                    " occurred_at, recorded_at, producer, payload, payload_hash,"
                    " privacy_class) VALUES ('run-a', 'doomed', 1, ?, ?, 'test',"
                    " '{}', '', 'internal')",
                    (NOW, NOW),
                )
                raise RuntimeError("killed mid-transaction")

        rows = read_events(self.store)
        self.assertEqual(len(rows), before)
        self.assertNotIn("doomed", [row["event_type"] for row in rows])

    def test_a_payload_that_cannot_be_encoded_never_opens_a_transaction(self):
        """Stated for what it is: encoding fails before BEGIN, by design."""

        before = len(read_events(self.store))

        with self.assertRaises(TypeError):
            append_event(
                self.store,
                event_type="boom",
                payload={"unserialisable": object()},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="test",
                run_id="run-a",
            )

        self.assertEqual(len(read_events(self.store)), before)

    def test_a_refused_fenced_append_leaves_no_event(self):
        stale = acquire_lease(self.store, run_id="run-a", owner="w1", now=NOW)
        acquire_lease(self.store, run_id="run-a", owner="w2", now=NOW)
        before = len(read_events(self.store))

        with self.assertRaises(StaleWriterError):
            append_event(
                self.store,
                event_type="late",
                payload={},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="w1",
                run_id="run-a",
                expected_fence=stale,
            )

        self.assertEqual(len(read_events(self.store)), before)

    def test_the_store_is_still_complete_after_a_rolled_back_write(self):
        """A rollback must not leave the database degraded."""

        with self.assertRaises(TypeError):
            append_event(
                self.store,
                event_type="boom",
                payload={"bad": object()},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="test",
                run_id="run-a",
            )

        self.assertEqual(check_integrity(self.store).state, INTEGRITY_COMPLETE)


class ProcessDeathAroundCommitTests(unittest.TestCase):
    """A real killed process, because WAL recovery cannot be mocked honestly."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        store = open_store(self.root)
        _seed(store)
        store.close()

    def _reopen(self):
        store = open_store(self.root)
        self.addCleanup(store.close)
        return store

    def test_a_commit_survives_a_process_that_never_closed_the_connection(self):
        result = _run_child(
            self.root,
            "append_event(store, event_type='committed', payload={'n': 1},"
            " occurred_at=NOW, recorded_at=NOW, producer='child', run_id='run-a')",
        )
        self.assertEqual(result.returncode, 9, result.stderr[-500:])

        rows = read_events(self._reopen())

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "committed")

    def test_a_death_before_commit_leaves_nothing(self):
        result = _run_child(
            self.root,
            "store.execute('BEGIN IMMEDIATE');"
            ' store.execute("INSERT INTO events(run_id, event_type,'
            " event_schema_version, occurred_at, recorded_at, producer, payload,"
            " payload_hash, privacy_class) VALUES ('run-a', 'uncommitted', 1,"
            " ?, ?, 'child', '{}', '', 'internal')\", (NOW, NOW))",
        )
        self.assertEqual(result.returncode, 9, result.stderr[-500:])

        rows = read_events(self._reopen())

        self.assertEqual(rows, [], "an uncommitted insert must not survive")

    def test_the_database_is_still_complete_after_an_abrupt_death(self):
        _run_child(
            self.root,
            "append_event(store, event_type='a', payload={}, occurred_at=NOW,"
            " recorded_at=NOW, producer='child', run_id='run-a')",
        )

        report = check_integrity(self._reopen())

        self.assertEqual(report.state, INTEGRITY_COMPLETE)
        self.assertEqual(report.schema_version, compatibility_version())

    def test_a_second_process_can_write_after_the_first_was_killed(self):
        """A killed writer must not leave the database locked forever."""

        _run_child(
            self.root,
            "append_event(store, event_type='first', payload={}, occurred_at=NOW,"
            " recorded_at=NOW, producer='child', run_id='run-a')",
        )

        store = self._reopen()
        append_event(
            store,
            event_type="second",
            payload={},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="parent",
            run_id="run-a",
        )

        self.assertEqual(
            [row["event_type"] for row in read_events(store)], ["first", "second"]
        )


class NoDuplicateActiveOwnerTests(_CrashFixture):
    """The takeover half of the matrix: no two writers hold a run at once."""

    def test_a_crashed_holder_is_fenced_out_by_the_next_acquisition(self):
        crashed = acquire_lease(self.store, run_id="run-a", owner="w1", now=NOW)
        taken = acquire_lease(self.store, run_id="run-a", owner="w2", now=NOW)

        self.assertGreater(taken, crashed)
        with self.assertRaises(StaleWriterError):
            append_event(
                self.store,
                event_type="zombie",
                payload={},
                occurred_at=NOW,
                recorded_at=NOW,
                producer="w1",
                run_id="run-a",
                expected_fence=crashed,
            )

    def test_only_one_fence_is_current_however_many_takeovers_happen(self):
        fences = [
            acquire_lease(self.store, run_id="run-a", owner=f"w{index}", now=NOW)
            for index in range(6)
        ]

        self.assertEqual(fences, sorted(fences))
        self.assertEqual(len(set(fences)), len(fences))
        for stale in fences[:-1]:
            with self.assertRaises(StaleWriterError):
                append_event(
                    self.store,
                    event_type="zombie",
                    payload={},
                    occurred_at=NOW,
                    recorded_at=NOW,
                    producer="stale",
                    run_id="run-a",
                    expected_fence=stale,
                )
        append_event(
            self.store,
            event_type="live",
            payload={},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="current",
            run_id="run-a",
            expected_fence=fences[-1],
        )

    def test_a_killed_holder_does_not_block_a_takeover(self):
        """A crash must not strand the lease with a process that is gone."""

        result = _run_child(
            self.root,
            "acquire_lease(store, run_id='run-a', owner='killed', now=NOW)",
        )
        self.assertEqual(result.returncode, 9, result.stderr[-500:])

        store = open_store(self.root)
        self.addCleanup(store.close)
        taken = acquire_lease(store, run_id="run-a", owner="survivor", now=NOW)

        self.assertGreaterEqual(taken, 2)
        append_event(
            store,
            event_type="after.takeover",
            payload={},
            occurred_at=NOW,
            recorded_at=NOW,
            producer="survivor",
            run_id="run-a",
            expected_fence=taken,
        )


class NoSilentLossTests(_CrashFixture):
    """A gap is always typed, never an empty read."""

    def test_a_torn_payload_is_reported_rather_than_dropped(self):
        self._append("first")
        bad = self._append("second")
        self._append("third")
        self.store.execute(
            "UPDATE events SET payload = '{torn' WHERE sequence = ?", (bad,)
        )

        report = check_integrity(self.store)
        rows = read_events(self.store)

        self.assertEqual(report.first_invalid_sequence, bad)
        self.assertEqual(len(rows), 3, "the row count must not shrink")
        self.assertFalse(rows[1]["readable"])

    def test_a_missing_cost_never_becomes_a_free_operation(self):
        """An operation with no cost row reads as unknown, not as zero."""

        record_operation(
            self.store,
            operation_key="op-1",
            kind="model.call",
            target_digest="d",
            state="executing",
            now=NOW,
            run_id="run-a",
        )

        count = self.store.execute(
            "SELECT COUNT(*) FROM cost_events WHERE operation_key = 'op-1'"
        ).fetchone()[0]
        state = self.store.execute(
            "SELECT state FROM operations WHERE operation_key = 'op-1'"
        ).fetchone()[0]

        self.assertEqual(count, 0)
        self.assertNotEqual(
            state, "reconciled", "an unpaid-for operation must not read as settled"
        )

    def test_an_operation_interrupted_after_dispatch_stays_uncertain(self):
        """The boundary a local store genuinely cannot close.

        Killing a process between an external effect and its outcome record
        cannot be simulated without a real external effect. What is provable is
        that the operation row survives saying "we do not know", which is what
        lets reconciliation run later rather than silently assuming success.
        """

        record_operation(
            self.store,
            operation_key="op-2",
            kind="model.call",
            target_digest="d",
            state="executing",
            now=NOW,
            run_id="run-a",
        )
        record_operation(
            self.store,
            operation_key="op-2",
            kind="model.call",
            target_digest="d",
            state="uncertain",
            now=NOW,
            run_id="run-a",
        )

        row = self.store.execute(
            "SELECT state, reconciled_at FROM operations WHERE operation_key = 'op-2'"
        ).fetchone()

        self.assertEqual(row["state"], "uncertain")
        self.assertIsNone(row["reconciled_at"])


class NoTerminalRegressionTests(_CrashFixture):
    """A settled run does not reopen, and a paid cost is not paid twice."""

    def test_a_reconciled_operation_keeps_its_reconciled_timestamp(self):
        record_operation(
            self.store,
            operation_key="op-1",
            kind="git.push",
            target_digest="d",
            state="executing",
            now=NOW,
            run_id="run-a",
        )
        record_operation(
            self.store,
            operation_key="op-1",
            kind="git.push",
            target_digest="d",
            state="reconciled",
            now=NOW,
            run_id="run-a",
        )

        settled = self.store.execute(
            "SELECT reconciled_at FROM operations WHERE operation_key = 'op-1'"
        ).fetchone()[0]

        self.assertIsNotNone(settled)

    def test_a_cost_already_recorded_cannot_be_recorded_again_after_a_crash(self):
        """Replay after a crash must not double-charge."""

        record_operation(
            self.store,
            operation_key="op-1",
            kind="model.call",
            target_digest="d",
            state="observed",
            now=NOW,
            run_id="run-a",
        )
        record_cost(
            self.store,
            operation_key="op-1",
            amount=2.5,
            measurement_kind="actual",
            now=NOW,
        )

        with self.assertRaises(Exception):
            record_cost(
                self.store,
                operation_key="op-1",
                amount=2.5,
                measurement_kind="actual",
                now=NOW,
            )

        total = self.store.execute(
            "SELECT SUM(amount) FROM cost_events WHERE operation_key = 'op-1'"
        ).fetchone()[0]
        self.assertEqual(float(total), 2.5)


class MigrationInterruptionTests(unittest.TestCase):
    """ "Migration interrupted at every step", from #613's edge cases."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_process_killed_during_first_open_leaves_a_usable_store(self):
        result = _run_child(self.root, "pass")

        self.assertEqual(result.returncode, 9, result.stderr[-500:])

        store = open_store(self.root)
        self.addCleanup(store.close)
        report = check_integrity(store)

        self.assertEqual(report.state, INTEGRITY_COMPLETE)
        self.assertEqual(report.schema_version, compatibility_version())

    def test_the_schema_version_is_never_left_half_applied(self):
        """A migration that raises the stamp records it in its own transaction.

        The stamp is what an older build must know, not the newest migration
        (``journal_store._OLDER_BUILDS_CAN_IGNORE``).
        """

        _run_child(self.root, "pass")
        store = open_store(self.root)
        self.addCleanup(store.close)

        version = store.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

        self.assertEqual(int(version), compatibility_version())

    def test_an_interrupted_open_does_not_leave_a_partial_schema(self):
        _run_child(self.root, "pass")
        store = open_store(self.root)
        self.addCleanup(store.close)

        names = {
            row[0]
            for row in store.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        self.assertLessEqual(
            {"tasks", "runs", "events", "operations", "cost_events", "leases"},
            names,
        )


class ReconstructionAfterDeathTests(unittest.TestCase):
    """The criterion itself: rebuild the run's history after a kill."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        store = open_store(self.root)
        _seed(store)
        store.close()

    def test_the_full_history_is_readable_after_the_writer_was_killed(self):
        # dedent() runs before format(), so {body} lands at column 0 in the
        # generated script -- continuation lines must not be indented.
        body = chr(10).join(
            [
                "fence = acquire_lease(store, run_id='run-a', owner='w', now=NOW)",
                "append_event(store, event_type='run.queued', payload={},"
                " occurred_at=NOW, recorded_at=NOW, producer='w', run_id='run-a',"
                " expected_fence=fence)",
                "append_event(store, event_type='run.started', payload={},"
                " occurred_at=NOW, recorded_at=NOW, producer='w', run_id='run-a',"
                " expected_fence=fence)",
                "record_operation(store, operation_key='op-1', kind='model.call',"
                " target_digest='d', state='executing', now=NOW, run_id='run-a')",
            ]
        )
        result = _run_child(self.root, body)
        self.assertEqual(result.returncode, 9, result.stderr[-500:])

        store = open_store(self.root)
        self.addCleanup(store.close)
        rows = read_events(store)

        self.assertEqual(
            [row["event_type"] for row in rows], ["run.queued", "run.started"]
        )
        self.assertTrue(all(row["readable"] for row in rows))
        self.assertEqual(check_integrity(store).state, INTEGRITY_COMPLETE)

        operation = store.execute(
            "SELECT state FROM operations WHERE operation_key = 'op-1'"
        ).fetchone()
        self.assertEqual(
            operation["state"],
            "executing",
            "a dispatched operation must survive as in-flight, not vanish",
        )

    def test_the_payloads_survive_intact_not_merely_the_row_count(self):
        result = _run_child(
            self.root,
            "append_event(store, event_type='e', payload={'answer': 42},"
            " occurred_at=NOW, recorded_at=NOW, producer='w', run_id='run-a')",
        )
        self.assertEqual(result.returncode, 9, result.stderr[-500:])

        store = open_store(self.root)
        self.addCleanup(store.close)
        rows = read_events(store)

        self.assertEqual(rows[0]["payload"], {"answer": 42})
        self.assertEqual(
            json.loads(store.execute("SELECT payload FROM events").fetchone()[0]),
            {"answer": 42},
        )


class EvidenceThatArrivesAfterTheRunEndedTests(unittest.TestCase):
    """#818's qualification names two of these: "late provider completion" and
    "delayed usage reporting".

    A provider CLI reporting its usage after Vesta has already filed the turn is
    the ordinary way to reach them, not an exotic one.

    Measured before this existed: the cost row landed on a run already filed
    `completed`, the fenced event was refused because `record_terminal` had
    released the lease, and `record_run_cost` returned True anyway. So
    `cost_events` held spend that the event log had no record of -- two truths
    inside the one store whose entire premise is "one ordered event history".
    A projection rebuilt from events would have been missing the money.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.fence = journal_runtime.record_admission(
            self.root,
            task_id="t",
            run_id="r1",
            task="a turn",
            now=CRASH_NOW,
            surface="gui",
        )

    def _finish(self, verdict: str = "completed") -> None:
        journal_runtime.record_terminal(
            self.root,
            run_id="r1",
            event_type=journal_runtime.EVENT_FINISHED,
            verdict=verdict,
            reason="answered",
            now=CRASH_NOW,
            fence=self.fence,
        )

    def _events(self):
        store = open_store(self.root)
        try:
            return [
                (str(row["event_type"]), json.loads(row["payload"]))
                for row in store.execute(
                    "SELECT event_type, payload FROM events"
                    " WHERE run_id = 'r1' ORDER BY sequence"
                )
            ]
        finally:
            store.close()

    def _verdict(self) -> str:
        store = open_store(self.root)
        try:
            row = store.execute(
                "SELECT terminal_verdict FROM runs WHERE run_id = 'r1'"
            ).fetchone()
            return str(row["terminal_verdict"] or "")
        finally:
            store.close()

    def _cost_rows(self) -> int:
        store = open_store(self.root)
        try:
            return int(store.execute("SELECT COUNT(*) FROM cost_events").fetchone()[0])
        finally:
            store.close()

    def _record_cost(self) -> bool:
        return journal_runtime.record_run_cost(
            self.root,
            run_id="r1",
            operation_key="r1:late",
            amount_usd=4.20,
            measurement_kind="actual",
            now=CRASH_NOW,
            fence=self.fence,
        )

    def _record_verification(self, verdict: str = "failed") -> bool:
        return journal_runtime.record_verification(
            self.root,
            run_id="r1",
            verdict=verdict,
            policy_digest="p",
            manifest_digest="m",
            now=CRASH_NOW,
            fence=self.fence,
        )

    def test_a_late_cost_reaches_the_event_log_not_only_the_cost_table(self):
        self._finish()

        self.assertTrue(self._record_cost())

        self.assertEqual(self._cost_rows(), 1)
        kinds = [kind for kind, _payload in self._events()]
        self.assertIn(journal_runtime.EVENT_COSTED, kinds)

    def test_a_late_cost_says_that_it_was_late(self):
        """Replay has to be able to tell spend that arrived while the run was
        live from spend that turned up after it was filed."""

        self._finish()
        self._record_cost()

        payload = dict(
            next(
                p for kind, p in self._events() if kind == journal_runtime.EVENT_COSTED
            )
        )

        self.assertIs(payload["after_terminal"], True)

    def test_an_on_time_cost_is_not_marked_late(self):
        self._record_cost()

        payload = dict(
            next(
                p for kind, p in self._events() if kind == journal_runtime.EVENT_COSTED
            )
        )

        self.assertIs(payload["after_terminal"], False)

    def test_a_late_verification_is_recorded_rather_than_dropped(self):
        """A verification that lands after the verdict is a contradiction worth
        keeping: it is evidence the terminal state was reached without it."""

        self._finish()

        self.assertTrue(self._record_verification())

        kinds = [kind for kind, _payload in self._events()]
        self.assertIn(journal_runtime.EVENT_VERIFIED, kinds)

    def test_late_evidence_never_changes_the_verdict(self):
        """The whole point of the guard it relaxes. Recording is not revising."""

        self._finish("completed")

        self._record_cost()
        self._record_verification("failed")

        self.assertEqual(self._verdict(), "completed")

    def test_a_settled_run_still_cannot_be_re_admitted(self):
        """Relaxing the fence for evidence must not reopen the run itself."""

        self._finish()

        reopened = journal_runtime.record_run_snapshot(
            self.root,
            run_id="r1",
            task_id="t",
            task="a turn",
            state="running",
            now=CRASH_NOW,
        )

        self.assertFalse(reopened)
        self.assertEqual(self._verdict(), "completed")


class AStaleWriterIsStillRefusedTests(unittest.TestCase):
    """The case the relaxation must not touch.

    A writer fenced out by a *takeover* is a different shape from one writing
    after the end: takeover leaves a new, unreleased lease, so the run is not
    settled and the late-evidence path is never taken. If this ever passes,
    the fence has been given away rather than accounted for.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.old_fence = journal_runtime.record_admission(
            self.root,
            task_id="t",
            run_id="r1",
            task="a turn",
            now=CRASH_NOW,
            surface="gui",
        )
        store = open_store(self.root)
        try:
            # Somebody else takes the run over. The run is emphatically not
            # settled -- it is being worked on by its new owner.
            acquire_lease(store, run_id="r1", owner="gui", now=CRASH_NOW)
        finally:
            store.close()

    def _events(self) -> int:
        store = open_store(self.root)
        try:
            return int(
                store.execute(
                    "SELECT COUNT(*) FROM events WHERE run_id = 'r1'"
                ).fetchone()[0]
            )
        finally:
            store.close()

    def test_a_superseded_writer_cannot_record_a_cost(self):
        before = self._events()

        journal_runtime.record_run_cost(
            self.root,
            run_id="r1",
            operation_key="r1:stale",
            amount_usd=99.0,
            measurement_kind="actual",
            now=CRASH_NOW,
            fence=self.old_fence,
        )

        self.assertEqual(self._events(), before)

    def test_a_superseded_writer_cannot_record_a_verification(self):
        self.assertFalse(
            journal_runtime.record_verification(
                self.root,
                run_id="r1",
                verdict="passed",
                policy_digest="p",
                manifest_digest="m",
                now=CRASH_NOW,
                fence=self.old_fence,
            )
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
