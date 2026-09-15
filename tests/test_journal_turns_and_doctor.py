"""#818 review findings 4, 5, 10 and 16: the checks have to be right too.

4.  ``turn_parity`` matched a whole conversation against each run, so any chat
    with one complete and one partial turn was "2 of 2 runs disagree". Turns
    now carry their run id and are compared one to one.
5.  ``vesta journal status`` printed "unfinished: 0" over a real unfinished run
    when the journal had been written by a newer Vesta: every report shared one
    suppress block, and the CLI filled the gaps with reassuring defaults.
10. Every surface recorded the GUI's open conversation as its own.
16. ``store_health`` asked "can this be opened?" by opening it, which migrated
    it -- so running doctor changed the journal it was diagnosing.
"""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, make_repo

from vesta import cli
from vesta.gui_recents import begin_thread_turn, finish_thread_turn
from vestahub import journal_conversations, journal_runtime, journal_store
from vestahub.gui_pipeline import handle_gui_message

NOW = "2026-09-10T10:00:00+00:00"
LATER = "2026-09-10T10:01:00+00:00"


class _Root(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def run_ending(self, run_id: str, verdict: str | None) -> None:
        fence = journal_runtime.record_admission(
            self.root,
            task_id=f"task-{run_id}",
            run_id=run_id,
            task="t",
            now=NOW,
            surface="gui",
        )
        if verdict:
            journal_runtime.record_terminal(
                self.root,
                run_id=run_id,
                event_type=journal_runtime.EVENT_FINISHED,
                verdict=verdict,
                reason=verdict,
                now=LATER,
                fence=fence,
            )

    def save_chat(self, *turns: tuple[str, str]) -> None:
        """Save one conversation whose assistant turns are (status, run_id)."""

        directory = self.root / ".vestahub" / "gui" / "conversations"
        directory.mkdir(parents=True, exist_ok=True)
        messages = []
        for status, run_id in turns:
            messages.append({"role": "user", "text": "q", "status": "complete"})
            message = {"role": "assistant", "text": "a", "status": status}
            if run_id:
                message["run_id"] = run_id
            messages.append(message)
        (directory / "conv-1.json").write_text(
            json.dumps({"id": "conv-1", "messages": messages}), encoding="utf-8"
        )


class TurnsAreComparedOneToOneTests(_Root):
    def test_a_chat_with_mixed_endings_agrees_with_itself(self):
        """The reviewer's reproduction: this used to be "2 of 2 disagree"."""

        self.run_ending("run-a", "completed")
        self.run_ending("run-b", "partial")
        self.save_chat(("complete", "run-a"), ("partial", "run-b"))

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["joined"]["runs"], 2)
        self.assertEqual(report["joined"]["agreements"], 2)
        self.assertEqual(report["joined"]["disagreement_count"], 0)

    def test_a_real_contradiction_is_still_caught(self):
        """The false completion this check exists for."""

        self.run_ending("run-a", "completed")
        self.save_chat(("partial", "run-a"))

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["joined"]["disagreement_count"], 1)
        self.assertEqual(
            report["joined"]["disagreements"][0],
            {
                "run_id": "run-a",
                "conversation": "conv-1",
                "journal": "completed",
                "saved": "partial",
            },
        )

    def test_a_turn_that_stopped_to_ask_is_not_a_contradiction(self):
        # awaiting_input in the journal, its verdict (blocked) in the chat:
        # different words, the same answer to "did it finish the work?".
        self.run_ending("run-a", "awaiting_input")
        self.save_chat(("blocked", "run-a"))

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["joined"]["disagreement_count"], 0)
        self.assertEqual(report["joined"]["agreements"], 1)

    def test_older_turns_are_unjoinable_not_disagreements(self):
        self.run_ending("run-a", "completed")
        self.save_chat(("partial", ""), ("complete", "run-a"))

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["unjoinable_turns"], 1)
        self.assertEqual(report["joined"]["disagreement_count"], 0)

    def test_an_unfinished_run_is_in_progress_not_a_disagreement(self):
        self.run_ending("run-a", None)
        self.save_chat(("complete", "run-a"))

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["in_progress"], 1)
        self.assertEqual(report["joined"]["disagreement_count"], 0)

    def test_a_saved_turn_the_journal_never_saw_is_counted(self):
        self.save_chat(("complete", "run-nobody-admitted"))
        journal_store.open_store(self.root).close()

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["turns_without_a_run"], 1)
        self.assertEqual(report["joined"]["disagreement_count"], 0)

    def test_each_conversation_file_is_read_once(self):
        self.run_ending("run-a", "completed")
        self.save_chat(("complete", "run-a"))
        real_loads = json.loads
        seen: list[int] = []

        def counting(text, *args, **kwargs):
            seen.append(1)
            return real_loads(text, *args, **kwargs)

        with mock.patch.object(journal_conversations.json, "loads", counting):
            journal_conversations.turn_parity(self.root)

        self.assertEqual(len(seen), 1)


class TheSavedTurnKeepsItsRunTests(_Root):
    def test_the_run_id_survives_saving_and_archiving(self):
        begin_thread_turn(self.root, request_id="req-1", text="q", mode="ask")
        finish_thread_turn(
            self.root, request_id="req-1", answer="a", status="complete", run_id="run-7"
        )

        report = journal_conversations.conversation_outcomes(self.root)

        runs = [turn["run_id"] for turns in report.values() for turn in turns]
        self.assertEqual(runs, ["run-7"])

    def test_a_malformed_run_id_is_dropped(self):
        begin_thread_turn(self.root, request_id="req-1", text="q", mode="ask")
        finish_thread_turn(
            self.root,
            request_id="req-1",
            answer="a",
            status="complete",
            run_id="../../etc/passwd",
        )

        report = journal_conversations.conversation_outcomes(self.root)

        runs = [turn["run_id"] for turns in report.values() for turn in turns]
        self.assertEqual(runs, [""])


class ATurnBelongsToTheConversationItsCallerNamesTests(unittest.TestCase):
    """#10: the thread file names whichever chat the GUI last had open."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_repo(Path(self._tmp.name), files={"a.py": "x = 1\n"})

    def turn(self, **kw) -> str:
        """Run one turn; return the journal run id the pipeline reported."""

        reported: list[str] = []
        handle_gui_message(
            self.root,
            "explain what a.py does",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=FakeAccountRunner(text="It sets x."),
            on_journal_run=reported.append,
            **kw,
        )
        self.assertEqual(len(reported), 1, "the run id was not reported")
        return reported[0]

    def origin_session(self, run_id: str) -> str:
        store = journal_store.open_store(self.root)
        try:
            row = store.execute(
                "SELECT t.origin_session FROM runs r JOIN tasks t"
                " ON t.task_id = r.task_id WHERE r.run_id = ?",
                (run_id,),
            ).fetchone()
            return str(row[0] or "")
        finally:
            store.close()

    def test_the_named_conversation_is_recorded(self):
        run_id = self.turn(surface="gui", conversation_id="conv-9")

        self.assertEqual(self.origin_session(run_id), "conv-9")

    def test_a_turn_no_caller_named_belongs_to_no_conversation(self):
        # The GUI has a chat open; a CLI turn must not be filed under it.
        begin_thread_turn(self.root, request_id="gui-1", text="q", mode="ask")

        run_id = self.turn(surface="cli")

        self.assertEqual(self.origin_session(run_id), "")

    def test_the_reported_run_is_the_journals(self):
        run_id = self.turn(surface="gui")

        store = journal_store.open_store(self.root)
        try:
            row = store.execute(
                "SELECT COUNT(*) FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            store.close()
        self.assertEqual(row[0], 1)

    def test_reporting_it_leaves_the_result_untouched(self):
        """Background-only: the id travels beside the result, not in it."""

        result = handle_gui_message(
            self.root,
            "explain what a.py does",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=FakeAccountRunner(text="It sets x."),
            on_journal_run=lambda _run: None,
        )

        self.assertNotIn("journal_run_id", result)
        self.assertNotIn("on_journal_run", repr(result))

    def test_a_listener_that_raises_does_not_fail_the_turn(self):
        def broken(_run: str) -> None:
            raise RuntimeError("the listener broke")

        result = handle_gui_message(
            self.root,
            "explain what a.py does",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=FakeAccountRunner(text="It sets x."),
            on_journal_run=broken,
        )

        self.assertIn("It sets x.", str(result.get("answer") or ""))


class StatusSaysUnknownWhenItCouldNotLookTests(_Root):
    """#5: "unfinished: 0" over a real unfinished run."""

    def newer_journal_with_one_unfinished_run(self) -> None:
        self.run_ending("run-live", None)
        store = journal_store.open_store(self.root)
        try:
            store.execute(
                "UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'"
            )
        finally:
            store.close()

    def status_output(self) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli.cmd_journal(
                argparse.Namespace(
                    project=str(self.root), journal_command="status", json=False
                )
            )
        return buffer.getvalue()

    def test_status_does_not_claim_zero_unfinished(self):
        self.newer_journal_with_one_unfinished_run()

        output = self.status_output()

        self.assertNotIn("unfinished:     0", output)
        self.assertIn("unfinished:     unknown (incompatible)", output)
        self.assertIn("runs recorded:  unknown (incompatible)", output)

    def test_one_report_failing_does_not_silence_the_rest(self):
        self.run_ending("run-live", None)
        with mock.patch.object(
            journal_runtime,
            "unevidenced_completions",
            side_effect=RuntimeError("one report broke"),
        ):
            facts = cli._journal_migration(self.root)

        self.assertEqual(facts["report_errors"], {"completions": "RuntimeError"})
        self.assertTrue(facts["unterminated_runs_known"])
        self.assertEqual(facts["unterminated_runs"], 1)
        self.assertTrue(facts["turn_parity_known"])


class DoctorDoesNotMigrateTests(_Root):
    """#16: asking whether the journal opens must not change it."""

    def journal_one_migration_behind(self) -> None:
        store = journal_store.open_store(self.root)
        try:
            store.execute("ALTER TABLE leases DROP COLUMN owner_boot")
            store.execute("ALTER TABLE leases DROP COLUMN owner_pid")
            store.execute(
                "UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'"
            )
        finally:
            store.close()

    def stored_version(self) -> int:
        import sqlite3

        connection = sqlite3.connect(journal_store.journal_path(self.root))
        try:
            return int(
                connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def test_health_leaves_a_pending_migration_pending(self):
        self.journal_one_migration_behind()

        health = journal_store.store_health(self.root)

        self.assertTrue(health["openable"], health["open_error"])
        self.assertEqual(self.stored_version(), 1)

    def test_a_migration_that_would_fail_is_still_reported(self):
        # A table squatting on the name of an index v1 creates: the one
        # migration statement that is outstanding cannot succeed.
        store = journal_store.open_store(self.root)
        try:
            store.execute("DROP INDEX events_by_run")
            store.execute("CREATE TABLE events_by_run (squatter TEXT)")
            store.execute(
                "UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'"
            )
        finally:
            store.close()

        health = journal_store.store_health(self.root)

        self.assertFalse(health["openable"])
        self.assertIn("already a table", health["open_error"])
        self.assertEqual(self.stored_version(), 1)

    def test_a_newer_journal_is_reported_without_touching_it(self):
        store = journal_store.open_store(self.root)
        try:
            store.execute(
                "UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'"
            )
        finally:
            store.close()

        health = journal_store.store_health(self.root)

        self.assertFalse(health["openable"])
        self.assertIn("newer", health["open_error"])
        self.assertTrue(journal_store.written_by_a_newer_vesta(self.root))


class DoctorAsAWholeDoesNotMigrateTests(_Root):
    """#16's second half: every report doctor prints, not only store_health.

    The first fix made ``store_health`` ask without migrating and pinned only
    that. `vesta doctor` still upgraded the journal, through the migration report
    it prints straight after -- parity, unfinished runs and the rest each open
    the store the ordinary way. A test of one function could not see it.
    """

    journal_one_migration_behind = (
        DoctorDoesNotMigrateTests.journal_one_migration_behind
    )

    def owner_columns_present(self) -> bool:
        import sqlite3

        connection = sqlite3.connect(journal_store.journal_path(self.root))
        try:
            return "owner_pid" in {
                row[1] for row in connection.execute("PRAGMA table_info(leases)")
            }
        finally:
            connection.close()

    def test_doctor_leaves_a_pending_migration_pending(self):
        self.journal_one_migration_behind()

        cli._journal_doctor(self.root)

        self.assertFalse(self.owner_columns_present())

    def test_the_reports_say_why_they_could_not_look(self):
        self.journal_one_migration_behind()

        migration = cli._journal_doctor(self.root)["migration"]

        for key in (
            "runs_recorded_unknown_because",
            "unterminated_runs_unknown_because",
            "event_table_parity_unknown_because",
            "turn_parity_unknown_because",
        ):
            with self.subTest(key=key):
                self.assertEqual(migration[key], "migration pending")

    def test_a_pending_migration_is_not_something_to_escalate(self):
        self.journal_one_migration_behind()

        payload = cli._journal_doctor(self.root)

        self.assertTrue(payload["openable"])
        self.assertFalse(cli._journal_needs_attention(payload))

    def test_ordinary_use_still_migrates_after_doctor_has_looked(self):
        self.journal_one_migration_behind()
        cli._journal_doctor(self.root)

        journal_store.open_store(self.root).close()

        self.assertTrue(self.owner_columns_present())

    def test_doctor_on_a_project_with_no_journal_creates_none(self):
        cli._journal_doctor(self.root)

        self.assertFalse(journal_store.journal_path(self.root).exists())

    def test_reading_only_does_not_create_a_journal_either(self):
        with journal_store.reading_only():
            with self.assertRaises(journal_store.JournalStoreError):
                journal_store.open_store(self.root)

        self.assertFalse(journal_store.journal_path(self.root).exists())

    def test_reading_only_ends_with_its_block(self):
        self.journal_one_migration_behind()

        with journal_store.reading_only():
            pass
        journal_store.open_store(self.root).close()

        self.assertTrue(self.owner_columns_present())

    def test_a_turn_on_another_thread_is_not_made_read_only_by_doctor(self):
        import threading

        self.journal_one_migration_behind()
        opened: list[bool] = []

        def a_turn():
            journal_store.open_store(self.root).close()
            opened.append(True)

        with journal_store.reading_only():
            worker = threading.Thread(target=a_turn)
            worker.start()
            worker.join(timeout=30)

        self.assertEqual(opened, [True])
        self.assertTrue(self.owner_columns_present())

    def test_a_healthy_journal_still_reports_normally(self):
        journal_store.open_store(self.root).close()

        migration = cli._journal_doctor(self.root)["migration"]

        self.assertTrue(migration["runs_recorded_known"])
        self.assertTrue(migration["unterminated_runs_known"])
        self.assertTrue(migration["event_table_parity_known"])


if __name__ == "__main__":
    unittest.main()
