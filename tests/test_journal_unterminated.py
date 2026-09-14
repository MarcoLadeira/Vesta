"""#613: which runs never finished, which is the first question after a crash.

The issue opens by describing this state and names it as the thing recovery
cannot currently reason about:

    A run may appear active with no worker or disappear after restart.
    ...
    Recovery logic cannot know whether to resume, reconcile, block or request
    attention.

``journal_operations.unreconciled_operations`` already answered that for
external effects. Runs had no equivalent, so the half of the question that
concerns *work* rather than *side effects* could not be asked at all.

The design decision worth defending is that this **reports and does not
conclude**. An unterminated run whose lease is still held is either running
right now or was abandoned by a process that died, and nothing in the database
distinguishes them: a lease is released by ``record_terminal``, not by a
process exiting. So each row carries the owner and the heartbeat, and the
caller -- which can look at whether that process still exists -- decides.

Inventing the distinction here would be a plausible answer with nothing behind
it, which is the failure mode this whole issue exists to remove.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - fixed argv, throwaway project
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sqlite3

from opaihub import (
    journal_liveness,
    journal_runtime,
    journal_store,
    owner_lease,
)
from opaihub.journal_runtime import (
    EVENT_FINISHED,
    record_admission,
    record_terminal,
    unterminated_runs,
    unterminated_summary,
)
from opaihub.journal_store import journal_path, open_store

NOW = "2026-08-27T12:00:00+00:00"


#: The child program: admit a run, then die where nothing can clean up.
_ADMIT_THEN_DIE = """import sys
sys.path.insert(0, r'{cwd}')
from opaihub.journal_runtime import record_admission
record_admission(r'{root}', task_id='{task_id}', run_id='{run_id}', task='x', now='{now}')
import os
os._exit(9)
"""


def _admit_then_die(root, *, run_id: str, task_id: str = "t") -> None:
    """Admit a run in a real process, then kill it without releasing anything.

    The condition #613 opens by describing, produced the only honest way: a
    lease whose owner is genuinely gone, not one a test asserted about.
    ``os._exit`` skips every cleanup path, which is the point.
    """

    script = _ADMIT_THEN_DIE.format(
        cwd=os.getcwd(), root=root, task_id=task_id, run_id=run_id, now=NOW
    )
    subprocess.run(  # nosec B603 - fixed argv, throwaway project
        [sys.executable, "-c", script], capture_output=True, check=False
    )


class _PendingFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _admit(self, run_id: str, *, finish: bool = False) -> int | None:
        fence = record_admission(
            self.root, task_id="task-a", run_id=run_id, task="a task", now=NOW
        )
        if finish:
            record_terminal(
                self.root,
                run_id=run_id,
                event_type=EVENT_FINISHED,
                verdict="completed",
                reason="ok",
                now=NOW,
                fence=fence,
            )
        return fence

    def _ids(self) -> set[str]:
        return {entry["run_id"] for entry in unterminated_runs(self.root)}


class OnlyRunsWithoutAnEndingAreListedTests(_PendingFixture):
    def test_a_finished_run_is_not_pending(self):
        self._admit("done", finish=True)

        self.assertEqual(self._ids(), set())

    def test_an_admitted_run_with_no_ending_is_pending(self):
        self._admit("live")

        self.assertEqual(self._ids(), {"live"})

    def test_finished_and_unfinished_runs_are_separated(self):
        self._admit("done", finish=True)
        self._admit("live")
        self._admit("also-live")

        self.assertEqual(self._ids(), {"live", "also-live"})

    def test_every_terminal_verdict_counts_as_an_ending(self):
        """Cancelled and failed are endings too, not just completion."""

        for index, verdict in enumerate(("cancelled", "failed", "timeout")):
            run_id = f"run-{index}"
            fence = self._admit(run_id)
            record_terminal(
                self.root,
                run_id=run_id,
                event_type=EVENT_FINISHED,
                verdict=verdict,
                reason="",
                now=NOW,
                fence=fence,
            )

        self.assertEqual(self._ids(), set())

    def test_a_project_with_no_journal_has_nothing_pending(self):
        self.assertEqual(unterminated_runs(self.root), [])
        self.assertFalse(unterminated_summary(self.root)["available"])


class TheRowCarriesWhatARecoveryPassNeedsTests(_PendingFixture):
    def test_the_entry_names_the_run_its_task_and_its_attempt(self):
        self._admit("live")

        entry = unterminated_runs(self.root)[0]

        self.assertEqual(entry["run_id"], "live")
        self.assertEqual(entry["task_id"], "task-a")
        self.assertEqual(entry["attempt"], 1)
        self.assertTrue(entry["created_at"])

    def test_a_held_lease_is_reported_with_its_owner(self):
        self._admit("live")

        entry = unterminated_runs(self.root)[0]

        self.assertTrue(entry["lease_held"])
        self.assertTrue(entry["lease_owner"])
        self.assertTrue(entry["lease_heartbeat_at"])

    def test_a_released_lease_is_not_reported_as_held(self):
        """A lease can be released without the run reaching a verdict."""

        fence = self._admit("live")
        store = open_store(self.root)
        try:
            from opaihub.journal_store import release_lease

            release_lease(store, run_id="live", fence=fence, now=NOW)
        finally:
            store.close()

        entry = unterminated_runs(self.root)[0]

        self.assertFalse(entry["lease_held"])

    def test_the_oldest_run_comes_first(self):
        """A recovery pass works from the longest-outstanding one."""

        record_admission(
            self.root,
            task_id="t",
            run_id="older",
            task="x",
            now="2020-01-01T00:00:00+00:00",
        )
        record_admission(
            self.root,
            task_id="t",
            run_id="newer",
            task="x",
            now="2030-01-01T00:00:00+00:00",
        )

        self.assertEqual(
            [entry["run_id"] for entry in unterminated_runs(self.root)],
            ["older", "newer"],
        )

    def test_the_limit_is_honoured(self):
        for index in range(10):
            self._admit(f"run-{index}")

        self.assertEqual(len(unterminated_runs(self.root, limit=3)), 3)


class ARunAbandonedByADeadProcessIsVisibleTests(_PendingFixture):
    """The case the issue actually describes, produced by a real death."""

    def test_a_run_admitted_by_a_process_that_died_is_listed(self):
        _admit_then_die(self.root, run_id="orphan", task_id="t")

        entries = unterminated_runs(self.root)
        self.assertEqual([entry["run_id"] for entry in entries], ["orphan"])
        self.assertTrue(
            entries[0]["lease_held"],
            "the dead process never released its lease, which is the point",
        )


class ItReportsRatherThanConcludesTests(_PendingFixture):
    """What it now answers, and what it still refuses to.

    Until #818 a run being worked on and a run abandoned mid-flight were the
    same row, because ``lease_held`` was the only signal and a lease is
    released by a terminal record rather than by a process exiting. The test
    that stood here asserted that indistinguishability as though it were a
    virtue. It was a limitation: the lease recorded ``owner="gui"``, a
    category, so there was no process to go and ask about.

    The refusal that *is* a virtue survives, and is pinned below -- a running
    pid is never rounded up to "this work is alive", because pids get reused.
    """

    def test_a_live_run_and_an_abandoned_one_are_told_apart(self):
        """The case #613 opens with, produced by a real process death."""

        self._admit("live")
        _admit_then_die(self.root, run_id="orphan", task_id="task-a")

        rows = {entry["run_id"]: entry for entry in unterminated_runs(self.root)}

        self.assertEqual(set(rows), {"live", "orphan"})
        # Both still hold their lease. That has not changed, and it was never
        # the signal -- the owner is.
        self.assertTrue(rows["live"]["lease_held"])
        self.assertTrue(rows["orphan"]["lease_held"])
        self.assertEqual(rows["live"]["owner_liveness"], journal_liveness.OWNED_HERE)
        self.assertNotEqual(
            rows["orphan"]["owner_liveness"],
            journal_liveness.OWNED_HERE,
            "a dead process's run must never read as work this process is doing",
        )

    def test_our_own_lease_is_proved_by_identity_not_by_the_process_table(self):
        """Asserted against a probe that says everything is dead, so the
        machine's actual pid table cannot make this pass by accident."""

        self._admit("live")

        rows = unterminated_runs(self.root, is_pid_running=lambda pid: False)

        self.assertEqual(rows[0]["owner_liveness"], journal_liveness.OWNED_HERE)

    def test_a_running_pid_is_reported_unverified_rather_than_alive(self):
        self._admit("live")
        store = open_store(self.root)
        try:
            store.execute("UPDATE leases SET owner_boot = 'somebody-else'")
        finally:
            store.close()

        rows = unterminated_runs(self.root, is_pid_running=lambda pid: True)

        self.assertEqual(rows[0]["owner_liveness"], journal_liveness.OWNER_UNVERIFIED)

    def test_no_entry_claims_a_run_is_dead(self):
        """No field here asserts something the database cannot know."""

        self._admit("live")

        entry = unterminated_runs(self.root)[0]

        for forbidden in ("orphaned", "dead", "abandoned", "stale", "crashed"):
            self.assertNotIn(forbidden, entry)

    def test_the_verdict_is_always_one_of_the_closed_vocabulary(self):
        self._admit("live")

        entry = unterminated_runs(self.root)[0]

        self.assertIn(entry["owner_liveness"], journal_liveness.VERDICTS)


class ALeaseNamesTheProcessBehindItTests(_PendingFixture):
    """#818: ``owner`` is a category; these are an identity."""

    def test_admission_records_this_process(self):
        self._admit("live")

        entry = unterminated_runs(self.root)[0]

        self.assertEqual(entry["owner_pid"], os.getpid())
        self.assertEqual(entry["owner_boot"], owner_lease.boot_id())

    def test_the_surface_label_is_unchanged_by_the_addition(self):
        """Every existing reader of ``owner`` must see exactly what it saw."""

        self._admit("live")

        self.assertEqual(unterminated_runs(self.root)[0]["lease_owner"], "gui")

    def test_two_processes_no_longer_record_the_same_owner(self):
        """The reproduction that motivated this, inverted."""

        self._admit("mine")
        _admit_then_die(self.root, run_id="theirs", task_id="task-a")

        rows = {entry["run_id"]: entry for entry in unterminated_runs(self.root)}

        self.assertNotEqual(rows["mine"]["owner_pid"], rows["theirs"]["owner_pid"])
        self.assertNotEqual(rows["mine"]["owner_boot"], rows["theirs"]["owner_boot"])


class AJournalWrittenBeforeThisMigrationStillReadsTests(_PendingFixture):
    """The migration requirement: existing state is an input, not a casualty."""

    def _make_v1_journal(self) -> None:
        """A journal exactly as Vesta wrote it before the identity columns."""

        path = journal_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            for statement in journal_store._MIGRATION_1:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')"
            )
            connection.execute(
                "INSERT INTO tasks(task_id, origin_surface, origin_session,"
                " created_at, requested_outcome, schema_version, updated_at)"
                " VALUES ('t', 'gui', '', ?, 'old work', 1, ?)",
                (NOW, NOW),
            )
            connection.execute(
                "INSERT INTO runs(run_id, task_id, attempt, desired_state,"
                " observed_state, route, model, created_at, updated_at)"
                " VALUES ('old', 't', 1, 'running', 'queued', '', '', ?, ?)",
                (NOW, NOW),
            )
            connection.execute(
                "INSERT INTO leases(run_id, owner, fence, acquired_at, heartbeat_at)"
                " VALUES ('old', 'gui', 1, ?, ?)",
                (NOW, NOW),
            )
        finally:
            connection.close()

    def test_a_v1_journal_is_migrated_rather_than_rejected(self):
        self._make_v1_journal()

        store = open_store(self.root)
        try:
            version = journal_store._stored_version(store)
        finally:
            store.close()

        self.assertEqual(version, journal_store.compatibility_version())

    def test_a_lease_from_before_the_columns_reads_as_unknown(self):
        """Not "gone". Nobody recorded a process, so nobody can say."""

        self._make_v1_journal()

        entry = unterminated_runs(self.root)[0]

        self.assertEqual(entry["run_id"], "old")
        self.assertIsNone(entry["owner_pid"])
        self.assertEqual(entry["owner_liveness"], journal_liveness.OWNER_UNKNOWN)

    def test_an_old_run_is_never_counted_as_abandoned(self):
        """A migration that made every historical run look recoverable would
        greet the user with a pile of imaginary work to clean up."""

        self._make_v1_journal()

        summary = unterminated_summary(self.root)

        self.assertEqual(summary["unterminated"], 1)
        self.assertEqual(summary["abandoned"], 0)
        self.assertEqual(summary["by_owner"][journal_liveness.OWNER_UNKNOWN], 1)

    def test_a_new_run_in_a_migrated_journal_records_its_process(self):
        """The columns are usable after the migration, not merely present."""

        self._make_v1_journal()
        self._admit("fresh")

        rows = {entry["run_id"]: entry for entry in unterminated_runs(self.root)}

        self.assertEqual(rows["fresh"]["owner_pid"], os.getpid())
        self.assertIsNone(rows["old"]["owner_pid"])


class TheSummaryCountsWithoutJudgingTests(_PendingFixture):
    def test_it_counts_pending_runs_and_held_leases(self):
        self._admit("done", finish=True)
        self._admit("live")
        self._admit("also-live")

        summary = unterminated_summary(self.root)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["unterminated"], 2)
        self.assertEqual(summary["lease_held"], 2)

    def test_a_fully_settled_project_reports_zero(self):
        self._admit("done", finish=True)

        summary = unterminated_summary(self.root)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["unterminated"], 0)


class ReportingNeverBecomesTheProblemTests(_PendingFixture):
    """This is read after a crash, which is the worst time to raise."""

    def test_a_corrupt_journal_yields_an_empty_list_rather_than_an_error(self):
        self._admit("live")
        journal_path(self.root).write_bytes(b"not a database")

        self.assertEqual(unterminated_runs(self.root), [])

    def test_a_failure_to_open_the_store_is_not_an_error(self):
        self._admit("live")

        with mock.patch.object(
            journal_runtime, "open_store", side_effect=OSError("gone")
        ):
            self.assertEqual(unterminated_runs(self.root), [])

    def test_the_summary_never_raises(self):
        self._admit("live")
        journal_path(self.root).write_bytes(b"not a database")

        summary = unterminated_summary(self.root)

        self.assertEqual(summary["unterminated"], 0)


class ATaskNamesTheConversationItCameFromTests(_PendingFixture):
    """#818: the cross-reference the migration has no way to build without.

    Measured on a real checkout: 13 saved conversations, 27 journal tasks, zero
    shared identifiers. `gui_pipeline` admits with `runtime.task_id`, which
    `gui_recents` documents as falling back to the per-turn request id, while
    conversations are keyed by a stable `conversation_id`. `origin_session` --
    the column that could join them -- was `''` for every row, because
    `record_admission` was never passed a session.
    """

    def _session_of(self, task_id: str) -> str:
        store = open_store(self.root)
        try:
            row = store.execute(
                "SELECT origin_session FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        finally:
            store.close()
        return "" if row is None else str(row["origin_session"] or "")

    def test_a_session_given_at_admission_is_stored(self):
        record_admission(
            self.root,
            task_id="task-a",
            run_id="r1",
            task="a task",
            now=NOW,
            session="conv-1234",
        )

        self.assertEqual(self._session_of("task-a"), "conv-1234")

    def test_two_turns_of_one_conversation_share_it(self):
        """The join key: different tasks, same conversation."""

        for index, task in enumerate(("task-a", "task-b")):
            record_admission(
                self.root,
                task_id=task,
                run_id=f"r{index}",
                task="a task",
                now=NOW,
                session="conv-1234",
            )

        self.assertEqual(self._session_of("task-a"), self._session_of("task-b"))

    def test_the_gui_pipeline_actually_passes_one(self):
        """A static ratchet, because the alternative is a real turn.

        Testing `record_admission(session=...)` proves the store keeps a
        session; it says nothing about whether the one caller that has a
        conversation to name still hands it over. Deleting that argument left
        every other test here green, which is exactly the shape of gap #613
        kept hitting -- correct machinery nobody reaches.
        """

        repo = Path(__file__).resolve().parent.parent
        pipeline = (repo / "opaihub" / "gui_pipeline.py").read_text(encoding="utf-8")
        gui = (repo / "opai" / "gui_web.py").read_text(encoding="utf-8")
        cli = (repo / "opai" / "cli_stream.py").read_text(encoding="utf-8")

        # The conversation comes from the caller that recorded the turn in it.
        # Reading the workspace thread file instead filed every CLI, background
        # and build turn under whichever chat the GUI last had open (#818
        # review finding 10).
        self.assertIn('session=str(conversation_id or "")', pipeline)
        self.assertNotIn("current_conversation_id(root)", pipeline)
        self.assertIn("conversation_id=conversation_id", gui)
        self.assertIn("conversation_id=conversation_id", cli)

    def test_no_session_is_stored_as_empty_rather_than_invented(self):
        record_admission(
            self.root, task_id="task-a", run_id="r1", task="a task", now=NOW
        )

        self.assertEqual(self._session_of("task-a"), "")


class ALeaseCanBeRestampedByTheProcessHoldingItTests(_PendingFixture):
    """#818: `heartbeat_at` had two writers -- acquire and release -- and so
    recorded the moment a run started and nothing else.

    The identity check is the whole guard, and it is the rule `owner_lease`
    already states: "refreshing someone else's lease would keep a dead owner
    looking alive forever, which is the exact failure this module exists to
    prevent".
    """

    LATER = "2026-08-27T12:05:00+00:00"

    def _heartbeat(self, run_id: str = "live") -> str:
        entry = unterminated_runs(self.root)[0]
        self.assertEqual(entry["run_id"], run_id)
        return str(entry["lease_heartbeat_at"])

    def test_a_beat_moves_the_heartbeat(self):
        self._admit("live")
        before = self._heartbeat()

        self.assertTrue(
            journal_runtime.beat_lease(self.root, run_id="live", now=self.LATER)
        )
        self.assertNotEqual(self._heartbeat(), before)
        self.assertEqual(self._heartbeat(), self.LATER)

    def test_the_acquisition_time_is_left_alone(self):
        """The comparison that tells "beaten" from "never beaten" needs both."""

        self._admit("live")
        acquired = unterminated_runs(self.root)[0]["lease_acquired_at"]

        journal_runtime.beat_lease(self.root, run_id="live", now=self.LATER)

        self.assertEqual(unterminated_runs(self.root)[0]["lease_acquired_at"], acquired)

    def test_a_lease_owned_by_another_process_is_not_restamped(self):
        self._admit("live")
        before = self._heartbeat()
        store = open_store(self.root)
        try:
            store.execute("UPDATE leases SET owner_boot = 'a-different-opai'")
        finally:
            store.close()

        self.assertFalse(
            journal_runtime.beat_lease(self.root, run_id="live", now=self.LATER)
        )
        self.assertEqual(self._heartbeat(), before)

    def test_a_released_lease_is_not_restamped(self):
        """A finished run must not be able to look alive again."""

        fence = self._admit("live")
        store = open_store(self.root)
        try:
            from opaihub.journal_store import release_lease

            release_lease(store, run_id="live", fence=fence, now=NOW)
        finally:
            store.close()

        self.assertFalse(
            journal_runtime.beat_lease(self.root, run_id="live", now=self.LATER)
        )

    def test_beating_an_unknown_run_is_simply_false(self):
        self._admit("live")

        self.assertFalse(
            journal_runtime.beat_lease(self.root, run_id="nobody", now=self.LATER)
        )

    def test_a_blank_run_id_is_refused(self):
        self.assertFalse(journal_runtime.beat_lease(self.root, run_id="", now=NOW))

    def test_a_project_with_no_journal_is_simply_false(self):
        self.assertFalse(journal_runtime.beat_lease(self.root, run_id="x", now=NOW))

    def test_a_corrupt_journal_does_not_raise(self):
        """This runs on the turn path; it must never be the reason a turn dies."""

        self._admit("live")
        journal_path(self.root).write_bytes(b"not a database")

        self.assertFalse(
            journal_runtime.beat_lease(self.root, run_id="live", now=self.LATER)
        )

    def test_the_gui_pipeline_beats_from_the_activity_stream(self):
        """A static ratchet. A heartbeat nothing calls is a column nothing
        writes, which is exactly the state this replaced.

        Pinned against the activity emitter rather than a timer on purpose: a
        timer would keep restamping a wedged run's lease and report it healthy
        forever, hiding the very thing a heartbeat exists to expose.
        """

        source = (
            Path(__file__).resolve().parent.parent / "opaihub" / "gui_pipeline.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def _journal_beat(", source)
        self.assertIn("journal_runtime.beat_lease(", source)
        self.assertIn("_journal_beat(root)", source)
        self.assertIn("_HEARTBEAT_INTERVAL_SECONDS", source)
        self.assertIn(
            "_HEARTBEAT_INTERVAL_SECONDS = owner_lease.HEARTBEAT_INTERVAL_SECONDS",
            source,
            "one definition of the interval, shared with the legacy lease",
        )


class ACountNobodyCouldTakeIsNotZeroTests(_PendingFixture):
    """#818: "unknown ... is never represented as zero", in the recovery report.

    `available` used to mean "the journal file exists". So an unreadable store
    reported `available: True, unterminated: 0` -- byte for byte what a healthy
    journal with nothing pending reports. This is the summary a recovery pass
    reads after a crash, which is the worst possible moment to answer "nothing
    to worry about" when the truth is "I could not look".

    Found by running an older build against a journal a newer one had migrated
    -- a downgrade this branch's own schema bump makes reachable. `store_health`
    said "incompatible" in plain words and this said zero, in the same breath.
    """

    def _summary(self) -> dict:
        return unterminated_summary(self.root)

    def test_a_healthy_journal_reports_its_count_as_an_answer(self):
        self._admit("live")

        summary = self._summary()

        self.assertTrue(summary["available"])
        self.assertEqual(summary["unavailable_reason"], "")
        self.assertEqual(summary["unterminated"], 1)

    def test_a_corrupt_journal_does_not_report_zero_outstanding(self):
        self._admit("live")
        journal_path(self.root).write_bytes(b"not a database at all")

        summary = self._summary()

        self.assertFalse(summary["available"])
        self.assertEqual(summary["unavailable_reason"], "unreadable")

    def test_a_journal_from_a_newer_opai_says_so_rather_than_corrupt(self):
        """Two failures that need different words. "A newer Vesta wrote this"
        points at an upgrade; "unreadable" points at a corrupt file, and
        sending someone to the wrong one wastes their evening."""

        self._admit("live")
        store = sqlite3.connect(journal_path(self.root))
        try:
            store.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                (str(journal_store.SCHEMA_VERSION + 1),),
            )
            store.commit()
        finally:
            store.close()

        summary = self._summary()

        self.assertFalse(summary["available"])
        self.assertEqual(summary["unavailable_reason"], "incompatible")

    def test_no_journal_at_all_is_its_own_answer(self):
        """A project that has never journalled is not a broken one."""

        summary = self._summary()

        self.assertFalse(summary["available"])
        self.assertEqual(summary["unavailable_reason"], "no_journal")

    def test_a_degraded_journal_is_still_served(self):
        """`usable` already treats degraded as serviceable -- some rows are
        unreadable and the critical state is not unknown -- so only corrupt and
        incompatible stop the count meaning anything."""

        self._admit("live")
        store = open_store(self.root)
        try:
            store.execute("UPDATE events SET payload = 'not json' WHERE sequence = 1")
            store.commit()
        finally:
            store.close()

        summary = self._summary()

        self.assertTrue(summary["available"], "degraded is still an answer")

    def test_a_store_that_opens_but_fails_its_check_is_not_served(self):
        """The `usable` branch, which no other case here reaches.

        A store that will not open at all is caught earlier; a degraded one is
        deliberately still served. Only a store that opens cleanly and then
        fails its integrity check lands here, and real page corruption cannot
        be produced reliably across platforms -- so the verdict is injected,
        the same compromise `test_journal_fault_injection` documents. What
        this pins is that an unusable verdict is *believed*, not that SQLite
        detects any particular damage.
        """

        self._admit("live")
        corrupt = journal_store.IntegrityReport(
            state=journal_store.INTEGRITY_CORRUPT,
            schema_version=journal_store.SCHEMA_VERSION,
            detail="database disk image is malformed",
        )

        with mock.patch.object(journal_store, "check_integrity", return_value=corrupt):
            summary = self._summary()

        self.assertFalse(summary["available"])
        self.assertEqual(summary["unavailable_reason"], journal_store.INTEGRITY_CORRUPT)

    def test_a_readable_and_an_unreadable_journal_never_look_alike(self):
        """The property in one assertion, because collapsing these two back
        together is exactly the regression this class exists to catch."""

        self._admit("live")
        healthy = self._summary()
        journal_path(self.root).write_bytes(b"nope")
        broken = self._summary()

        self.assertNotEqual(
            (healthy["available"], healthy["unavailable_reason"]),
            (broken["available"], broken["unavailable_reason"]),
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
