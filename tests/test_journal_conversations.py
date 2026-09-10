"""#818: the journal against the chat it came from.

The closing evidence must show **zero cross-surface terminal disagreement**,
and migration steps 2 and 4 ask for parity between the legacy projections and
the canonical journal. Both sat at "not started" for one stated reason:
``journal_background.legacy_runs`` reads background automation JSON, the
journal's runs come overwhelmingly from GUI turns, and two populations that
never overlap cannot be compared.

That reason is a fact about *which pair* was being compared. A GUI turn has a
legacy outcome record too -- not a run file, but the assistant message in the
saved conversation, carrying ``complete`` / ``partial`` / ``needs_attention``.

Comparing those two is how the false-completion defect was found:

    journal              21 completed, 6 cancelled
    saved conversations  16 complete, 5 partial, 5 needs_attention

These tests pin the comparison, and -- more carefully -- pin what it refuses
to claim. An aggregate that lines up is a lead, not a join; two populations
can share a shape without sharing members. It pointed at the defect and the
defect was confirmed by reproducing it, not by the arithmetic.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opaihub import journal_conversations, journal_runtime, journal_store

NOW = "2026-09-10T10:00:00+00:00"
LATER = "2026-09-10T10:05:00+00:00"


class _Workspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.conversations = self.root / ".opaihub" / "gui" / "conversations"
        self.conversations.mkdir(parents=True, exist_ok=True)

    def write_conversation(self, conversation_id: str, *statuses: str) -> None:
        messages: list[dict[str, object]] = []
        for status in statuses:
            messages.append({"role": "user", "text": "?", "status": "complete"})
            messages.append(
                {"role": "assistant", "text": "!", "status": status, "timestamp": NOW}
            )
        (self.conversations / f"{conversation_id}.json").write_text(
            json.dumps({"id": conversation_id, "messages": messages}),
            encoding="utf-8",
        )

    def record_run(
        self, run_id: str, verdict: str, *, session: str = "", surface: str = "gui"
    ) -> None:
        fence = journal_runtime.record_admission(
            self.root,
            task_id=f"task-{run_id}",
            run_id=run_id,
            task="a turn",
            now=NOW,
            surface=surface,
            session=session,
        )
        journal_runtime.record_terminal(
            self.root,
            run_id=run_id,
            event_type=journal_runtime.EVENT_FINISHED,
            verdict=verdict,
            reason="",
            now=LATER,
            fence=fence,
        )


class ReadingTheConversationsTests(_Workspace):
    def test_assistant_turns_are_the_outcome_record(self):
        self.write_conversation("conv-1", "complete", "partial")

        found = journal_conversations.conversation_outcomes(self.root)

        self.assertEqual(
            [turn["status"] for turn in found["conv-1"]], ["complete", "partial"]
        )

    def test_user_messages_are_not_outcomes(self):
        self.write_conversation("conv-1", "complete")

        self.assertEqual(len(journal_conversations.conversation_outcomes(self.root)), 1)
        self.assertEqual(
            len(journal_conversations.conversation_outcomes(self.root)["conv-1"]), 1
        )

    def test_a_workspace_with_no_conversations_is_empty_not_an_error(self):
        empty = Path(tempfile.mkdtemp())

        self.assertEqual(journal_conversations.conversation_outcomes(empty), {})

    def test_an_unreadable_conversation_is_counted_not_hidden(self):
        self.write_conversation("conv-1", "complete")
        (self.conversations / "broken.json").write_text("{not json", encoding="utf-8")

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["unreadable_conversations"], 1)


class TheJoinTests(_Workspace):
    """Run-by-run, for runs whose task names a conversation."""

    def test_agreement_is_reported_as_agreement(self):
        self.write_conversation("conv-1", "complete")
        self.record_run("run-1", "completed", session="conv-1")

        report = journal_conversations.turn_parity(self.root)

        self.assertTrue(report["available"])
        self.assertEqual(report["joined"]["runs"], 1)
        self.assertEqual(report["joined"]["agreements"], 1)
        self.assertEqual(report["joined"]["disagreement_count"], 0)

    def test_the_defect_this_module_was_written_to_catch(self):
        """A partial chat turn against a completed journal run."""

        self.write_conversation("conv-1", "partial")
        self.record_run("run-1", "completed", session="conv-1")

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["joined"]["disagreement_count"], 1)
        found = report["joined"]["disagreements"][0]
        self.assertEqual(found["journal"], "completed")
        self.assertEqual(found["conversation"], "partial")

    def test_a_run_with_no_session_is_unjoinable_not_disagreeing(self):
        """Older runs predate origin_session. That is missing, not wrong."""

        self.write_conversation("conv-1", "partial")
        self.record_run("run-1", "completed", session="")

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["unjoinable_runs"], 1)
        self.assertEqual(report["joined"]["runs"], 0)
        self.assertEqual(report["joined"]["disagreement_count"], 0)

    def test_a_session_naming_no_conversation_is_unjoinable_too(self):
        self.record_run("run-1", "completed", session="conv-that-was-deleted")

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["unjoinable_runs"], 1)

    def test_a_mixed_conversation_is_reported_rather_than_guessed_at(self):
        """One run, several turns, and no per-turn run id to pick between them.

        Attributing the run to one of them would be inventing the join this
        module exists to make honestly.
        """

        self.write_conversation("conv-1", "complete", "partial")
        self.record_run("run-1", "completed", session="conv-1")

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["joined"]["disagreement_count"], 1)
        found = report["joined"]["disagreements"][0]
        self.assertIn("cannot attribute", found["note"])
        self.assertEqual(found["conversation"], ["completed", "partial"])

    def test_only_gui_runs_are_compared(self):
        """A CLI or background run has no chat to disagree with."""

        self.record_run("run-1", "completed", session="", surface="background")

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["unjoinable_runs"], 0)
        self.assertEqual(report["joined"]["runs"], 0)


class TheAggregateIsALeadNotAProofTests(_Workspace):
    def test_the_two_distributions_are_reported_separately(self):
        self.write_conversation("conv-1", "complete", "partial")
        self.record_run("run-1", "completed")
        self.record_run("run-2", "completed")

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["aggregate"]["journal"], {"completed": 2})
        self.assertEqual(
            report["aggregate"]["conversations"], {"completed": 1, "partial": 1}
        )

    def test_a_matching_aggregate_is_not_counted_as_agreement(self):
        """The whole point. Shape is not membership.

        Without a session these runs cannot be joined to anything, and a
        report that turned a matching distribution into "1 agreement" would be
        manufacturing the evidence this epic is about not manufacturing.
        """

        self.write_conversation("conv-1", "complete")
        self.record_run("run-1", "completed")

        report = journal_conversations.turn_parity(self.root)

        self.assertEqual(report["aggregate"]["journal"], {"completed": 1})
        self.assertEqual(report["aggregate"]["conversations"], {"completed": 1})
        self.assertEqual(report["joined"]["agreements"], 0)
        self.assertEqual(report["unjoinable_runs"], 1)


class AnUnreadableJournalIsNotAgreementTests(_Workspace):
    def test_a_corrupt_store_reports_why(self):
        self.write_conversation("conv-1", "complete")
        journal_store.journal_path(self.root).parent.mkdir(parents=True, exist_ok=True)
        journal_store.journal_path(self.root).write_bytes(b"not a database")

        report = journal_conversations.turn_parity(self.root)

        self.assertFalse(report["available"])
        self.assertTrue(report["reason"])
        self.assertEqual(report["joined"]["disagreements"], [])


class DoctorActuallyAsksTests(_Workspace):
    """A comparator no surface calls would be the eighth of these."""

    def test_journal_doctor_reports_the_cross_surface_parity(self):
        from opai import cli

        self.write_conversation("conv-1", "partial")
        self.record_run("run-1", "completed", session="conv-1")

        facts = cli._journal_migration(self.root)

        self.assertTrue(facts["turn_parity_known"])
        self.assertEqual(facts["turn_parity_joined"], 1)
        self.assertEqual(facts["turn_parity_disagreements"], 1)

    def test_doctor_reports_the_unjoinable_count(self):
        from opai import cli

        self.record_run("run-1", "completed", session="")

        facts = cli._journal_migration(self.root)

        self.assertEqual(facts["turn_parity_unjoinable"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
