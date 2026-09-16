"""#818: the canonical journal was recording unfinished turns as completed.

The epic's closing evidence has to show **zero false completion**. Measured on
this repository's own journal before the fix:

    journal            21 completed, 6 cancelled
    saved conversations 16 complete, 5 partial, 5 needs_attention

Sixteen plus five is twenty-one. The journal was filing every `partial` turn
as a success, and the arithmetic is not a coincidence -- it is the same turns,
recorded twice, disagreeing.

The cause was a five-entry map with a completing default:

    event, verdict = _TERMINAL_EVENTS.get(
        status, (journal_runtime.EVENT_FINISHED, "completed")
    )

The comment above it argued that guessing "failed" would invent a verdict
Stage 4 could not tell from a real contradiction. True -- and it quietly
licensed the more dangerous guess in the other direction. Seven of the legacy
status strings the pipeline actually emits fell through that default,
including `timeout` and `provider_blocked`: outright failures recorded as
successes.

The first fix replaced it with a hand-written table of both vocabularies,
and that drifted too (#818 review finding 2): it missed 10 of the 13 statuses
the lifecycle calls AWAITING_INPUT, and it let the verdict -- BLOCKED for every
approval card -- turn "shall I push?" into a permanent `blocked`. The journal
now records the engine's own canonical ``run_state`` (#379), and derives a
result without one through the one generated status map every surface shares.
An ending nobody can name is ``unknown``, never a success.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import vestahub.gui_pipeline as gui_pipeline
from vestahub import journal_runtime, journal_store
from vestahub.run_state import AWAITING_INPUT_STATUSES

NOW = "2026-09-10T10:00:00+00:00"


class _EndedTurn(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._next = 0

    def end(
        self,
        status: str,
        *,
        verdict_state: str = "",
        reason: str = "",
        run_state: str = "",
    ) -> str:
        """Run one turn's ending through the real recorder; return the verdict."""

        self._next += 1
        run_id = f"run-{self._next}"
        fence = journal_runtime.record_admission(
            self.root,
            task_id=f"task-{self._next}",
            run_id=run_id,
            task="a turn",
            now=NOW,
            surface="gui",
        )
        token = gui_pipeline._JOURNAL_RUN.set({"run_id": run_id, "fence": fence})
        result: dict = {"status": status, "reason": reason}
        if verdict_state:
            result["completion_verdict"] = {"verdict": verdict_state}
        if run_state:
            result["run_state"] = run_state
        try:
            gui_pipeline._record_turn_ending(self.root, result)
        finally:
            gui_pipeline._JOURNAL_RUN.reset(token)

        store = journal_store.open_store(self.root)
        try:
            row = store.execute(
                "SELECT terminal_verdict FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            store.close()
        return str(row["terminal_verdict"] or "")


class UnfinishedTurnsAreNotSuccessesTests(_EndedTurn):
    """The five that were being filed as completed, and two more."""

    def test_a_partial_turn_is_recorded_as_partial(self):
        self.assertEqual(self.end("incomplete"), "partial")
        self.assertEqual(self.end("partial"), "partial")
        self.assertEqual(self.end("stuck_no_progress"), "partial")

    def test_a_turn_needing_attention_says_so(self):
        self.assertEqual(self.end("needs_attention"), "needs_attention")

    def test_a_timeout_is_not_a_completion(self):
        self.assertEqual(self.end("timeout"), "timeout")

    def test_a_blocked_provider_is_not_a_completion(self):
        """`legacy_status` emits "provider_blocked"; the map only had "blocked"."""

        self.assertEqual(self.end("provider_blocked"), "blocked")
        self.assertEqual(self.end("blocked"), "blocked")

    def test_waiting_for_the_person_is_not_a_completion(self):
        for status in ("needs_user_input", "needs_confirmation", "awaiting_input"):
            with self.subTest(status=status):
                self.assertEqual(self.end(status), "awaiting_input")

    def test_a_retryable_provider_error_is_a_failure(self):
        self.assertEqual(self.end("retryable_provider_error"), "failed")

    def test_the_endings_that_were_already_right_still_are(self):
        self.assertEqual(self.end("answered", run_state="completed"), "completed")
        self.assertEqual(self.end("cancelled"), "cancelled")
        self.assertEqual(self.end("failed"), "failed")
        self.assertEqual(self.end("error"), "failed")
        self.assertEqual(self.end("duplicate_request"), "duplicate")


class AnUnmappedEndingIsNotASuccessTests(_EndedTurn):
    """The default, which is the whole defect in one line."""

    def test_a_status_nobody_mapped_becomes_unknown(self):
        self.assertEqual(self.end("something_new_in_a_later_build"), "unknown")

    def test_it_does_not_become_failed_either(self):
        """A fabricated failure is indistinguishable from a real one.

        That was the old comment's reasoning and it was right; it just did not
        follow it through to the other guess.
        """

        self.assertNotEqual(self.end("something_new_in_a_later_build"), "failed")

    def test_the_run_still_counts_as_ended(self):
        """Unknown *how* it ended is not the same as still running.

        Refusing to write a terminal verdict would leave the run reading as
        unfinished forever, which is a worse answer than an unnamed ending.
        """

        self.end("something_new_in_a_later_build")

        summary = journal_runtime.unterminated_summary(self.root)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["unterminated"], 0)

    def test_an_unknown_ending_is_not_counted_as_a_completion(self):
        self.end("something_new_in_a_later_build")

        report = journal_runtime.unevidenced_completions(self.root)

        self.assertEqual(report["completed"], 0)


class TheVerdictOutranksTheDerivedStatusTests(_EndedTurn):
    """The authoritative answer was sitting unread in the same result dict."""

    def test_a_partial_verdict_beats_an_answered_status(self):
        self.assertEqual(self.end("answered", verdict_state="partial"), "partial")

    def test_a_blocked_verdict_beats_an_answered_status(self):
        self.assertEqual(self.end("answered", verdict_state="blocked"), "blocked")

    def test_a_bare_answered_status_cannot_claim_completion(self):
        """#618's rule, which the saved conversation already follows.

        "The provider answered" is transport, not completion. With no verdict
        and no run state behind it, the most it supports is "could not be
        verified" -- and the conversation records the same turn that way.
        """

        self.assertEqual(self.end("answered"), "needs_attention")
        self.assertEqual(self.end("answered_by_account"), "needs_attention")

    def test_other_statuses_come_from_the_shared_map(self):
        # #15: from generated_lifecycle.LEGACY_STATUS_MAP, not a local copy.
        self.assertEqual(self.end("model_unavailable"), "failed")
        self.assertEqual(self.end("stuck"), "partial")
        self.assertEqual(self.end("blocked_panic"), "blocked")
        self.assertEqual(self.end("timed_out"), "timeout")

    def test_a_turn_with_no_status_at_all_is_unknown(self):
        # The wrapper used to default a missing status to "completed".
        self.assertEqual(self.end(""), "unknown")

    def test_an_unrecognised_verdict_does_not_fall_back_to_the_status(self):
        """Falling back would let a stale "answered" override a real verdict.

        The verdict is the more authoritative field. If this build cannot name
        the value it carries, the honest answer is that the ending is unknown
        -- not that the derived status can stand in for it.
        """

        self.assertEqual(
            self.end("answered", verdict_state="some_future_state"), "unknown"
        )


class ATurnThatStoppedToAskIsNotBlockedTests(_EndedTurn):
    """#818 review finding 2: approval cards were filed as permanent `blocked`.

    For every needs_* status the completion verdict is BLOCKED, and the old
    recorder preferred the verdict. The lifecycle calls these AWAITING_INPUT --
    non-terminal, because the user's answer resumes the same work -- and says
    so in the engine's own ``run_state``.
    """

    def test_every_awaiting_status_is_recorded_as_awaiting(self):
        for status in sorted(AWAITING_INPUT_STATUSES):
            with self.subTest(status=status):
                self.assertEqual(
                    self.end(status, verdict_state="blocked"), "awaiting_input"
                )

    def test_the_engine_state_is_what_is_recorded(self):
        self.assertEqual(
            self.end(
                "needs_command_approval",
                verdict_state="blocked",
                run_state="awaiting_input",
            ),
            "awaiting_input",
        )

    def test_the_engine_state_outranks_a_contradicting_status(self):
        self.assertEqual(
            self.end("answered", verdict_state="completed", run_state="partial"),
            "partial",
        )

    def test_a_state_this_build_cannot_name_is_unknown(self):
        self.assertEqual(self.end("answered", run_state="a_later_state"), "unknown")


class ThePipelineActuallyPassesTheVerdictTests(unittest.TestCase):
    """A recorder that reads a verdict nobody passes it changes nothing.

    This branch has found seven pieces of machinery in #613 that nothing
    called correctly. The wiring is checked rather than assumed.
    """

    def test_handle_gui_message_hands_over_the_whole_result(self):
        source = Path("vestahub/gui_pipeline.py").read_text(encoding="utf-8")

        self.assertIn("_record_turn_ending(root, result)", source)
        self.assertIn('payload.get("run_state")', source)
        self.assertNotIn('get("status") or "completed"', source)

    def test_the_map_has_no_completing_default_left(self):
        """The literal shape of the defect, pinned so it cannot come back."""

        source = Path("vestahub/gui_pipeline.py").read_text(encoding="utf-8")

        self.assertNotIn(
            'status, (journal_runtime.EVENT_FINISHED, "completed")',
            source,
            "an unmapped ending must not default to a success again",
        )
        self.assertIn("_UNKNOWN_ENDING", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
