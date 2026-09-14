"""#818: a whole turn, through the real pipeline, with the journal watching.

Every other test in this epic exercises one seam. This one runs a message
through ``handle_gui_message`` for real -- admission, activity events, the
consent handshake, cost telemetry, the completion verdict, the terminal record
-- with only the network call itself faked, and then asks two questions:

* did the user get exactly what they asked for, and
* did the canonical journal end up telling the truth about it?

Both matter, and the first matters more. The whole point of this epic is that
Vesta should be able to say true things about what it did *without the person
using it ever noticing that it is doing so*. A turn that journalled perfectly
and returned a worse answer would be a failure of the epic, not a success.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _helpers import FakeStreamingRunner, make_repo  # noqa: E402

from opaihub import journal_projections, journal_runtime, journal_store  # noqa: E402
from opaihub.gui_pipeline import handle_gui_message  # noqa: E402


class _RealTurn(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_repo(Path(self._tmp.name))

    def turn(self, message: str = "explain this repo", **kwargs) -> dict:
        runner = kwargs.pop("runner", None) or FakeStreamingRunner(
            chunks=["Here ", "is ", "the answer."]
        )
        return handle_gui_message(
            self.root,
            message,
            model_id="auto",
            mode=kwargs.pop("mode", "ask"),
            account_runner=runner,
            **kwargs,
        )

    def runs(self) -> list[dict]:
        store = journal_store.open_store(self.root)
        try:
            return [dict(row) for row in store.execute("SELECT * FROM runs")]
        finally:
            store.close()

    def tasks(self) -> list[dict]:
        store = journal_store.open_store(self.root)
        try:
            return [dict(row) for row in store.execute("SELECT * FROM tasks")]
        finally:
            store.close()


class TheUserGetsTheirAnswerTests(_RealTurn):
    """First, and most importantly: the turn works."""

    def test_a_turn_returns_something_the_user_can_act_on(self):
        """With `model="auto"` and no local model, Vesta stops and asks.

        That is the cost firewall working, not a failure: the request has not
        left the device and the user is being asked before it does. What
        matters here is that the turn returns a usable answer of *some* kind
        rather than nothing.
        """

        result = self.turn()

        self.assertIn("answer", result)
        self.assertTrue(result["answer"].strip(), "the turn returned nothing")

    def test_the_turn_reports_a_status_the_surfaces_understand(self):
        result = self.turn()

        self.assertTrue(str(result.get("status") or ""))
        self.assertNotEqual(result["status"], "error")

    def test_nothing_about_the_journal_reaches_the_result(self):
        result = self.turn()

        rendered = repr(result).lower()
        for leak in ("journal.sqlite", "sqlite3", "traceback", "opaihub.journal"):
            self.assertNotIn(leak, rendered, f"{leak!r} reached the user")


class TheJournalTellsTheTruthAboutItTests(_RealTurn):
    """And second: the record it left behind is accurate."""

    def test_the_turn_is_recorded_exactly_once(self):
        self.turn()

        runs = self.runs()

        self.assertEqual(len(runs), 1, "a turn is one run, not zero and not two")

    def test_the_run_is_closed_rather_than_left_hanging(self):
        self.turn()

        summary = journal_runtime.unterminated_summary(self.root)

        self.assertTrue(summary["available"])
        self.assertEqual(
            summary["unterminated"], 0, "the turn ended, so nothing is unfinished"
        )

    def test_the_recorded_ending_matches_what_the_user_was_told(self):
        """The defect this branch fixed, checked end to end.

        A `partial` turn used to be recorded as `completed`, so the journal
        and the answer disagreed about the same event. Whatever the verdict
        is, both must say it.
        """

        result = self.turn()
        runs = self.runs()

        verdict = str(runs[0]["terminal_verdict"] or "")
        self.assertTrue(verdict, "the run must have an ending")

        answered = str(result.get("status") or "") in ("answered", "completed")
        if answered:
            self.assertEqual(verdict, "completed")
        else:
            self.assertNotEqual(
                verdict,
                "completed",
                f"the user was told {result.get('status')!r} and the journal "
                f"recorded {verdict!r}",
            )

    def test_the_surface_is_recorded_as_the_desktop(self):
        """`handle_gui_message` is reached here as the GUI reaches it."""

        self.turn(surface="gui")

        self.assertEqual(self.tasks()[0]["origin_surface"], "gui")

    def test_a_cli_turn_is_recorded_as_the_cli(self):
        self.turn(surface="cli")

        self.assertEqual(self.tasks()[0]["origin_surface"], "cli")

    def test_the_events_and_the_runs_table_agree(self):
        """Two recordings of one history, after a real turn."""

        self.turn()

        report = journal_projections.run_table_parity(
            self.root, now="2026-09-10T10:00:00+00:00"
        )

        self.assertTrue(report["comparable"], report["reason"])
        self.assertEqual(report["disagreements"], [])

    def test_a_turn_stopped_by_the_privacy_gate_costs_nothing_and_says_so(self):
        """Nothing was sent, so there is nothing to charge for.

        A cost row here would be the inverse of the defect this epic is about
        -- inventing spend that never happened.
        """

        self.turn(runner=FakeStreamingRunner(chunks=["done."], cost=0.0421))

        store = journal_store.open_store(self.root)
        try:
            costs = [dict(row) for row in store.execute("SELECT * FROM cost_events")]
        finally:
            store.close()

        self.assertEqual(
            costs, [], "a turn that never reached a provider must record no spend"
        )

    def test_a_second_turn_is_a_second_run_not_a_second_admission(self):
        self.turn("first question")
        self.turn("second question")

        runs = self.runs()

        self.assertEqual(len(runs), 2)
        self.assertEqual(len({run["run_id"] for run in runs}), 2)


class TwoTurnsInARowStayHonestTests(_RealTurn):
    """Nothing leaks from one turn into the next."""

    def test_the_second_turn_does_not_inherit_the_first_runs_identity(self):
        self.turn("first")
        self.turn("second")

        runs = self.runs()
        verdicts = [str(run["terminal_verdict"] or "") for run in runs]

        self.assertTrue(all(verdicts), "both runs must have their own ending")

    def test_neither_turn_is_left_unfinished(self):
        self.turn("first")
        self.turn("second")

        summary = journal_runtime.unterminated_summary(self.root)

        self.assertEqual(summary["unterminated"], 0)


class TheEndingTheGateProducesIsRecordedHonestlyTests(_RealTurn):
    """The false-completion fix, on a real turn rather than a fixture.

    With `model="auto"` and no local model available, Vesta stops and asks
    before anything leaves the device. Measured, end to end:

        status              needs_auto_confirmation
        completion_verdict  blocked
        run_state           awaiting_input
        journal             awaiting_input

    The first recorder looked up the *status* and defaulted to "completed", so
    this would have been journalled as a success. The second preferred the
    verdict and filed it as `blocked` -- an immutable terminal for a turn whose
    user's next click resumes the same work (#818 review finding 2). The engine
    already says what this is, in ``run_state``, and that is what is recorded.
    """

    def test_the_gate_is_not_recorded_as_a_completion(self):
        result = self.turn()
        verdict = str(self.runs()[0]["terminal_verdict"] or "")

        self.assertIn("confirm", str(result.get("status") or "").lower())
        self.assertNotEqual(
            verdict,
            "completed",
            "Vesta stopped to ask a question; that is not a completed task",
        )

    def test_the_reason_names_what_actually_happened(self):
        self.turn()
        run = self.runs()[0]

        self.assertTrue(
            str(run["terminal_reason"] or ""),
            "an ending that is not a success has to say why",
        )

    def test_the_engines_run_state_is_what_reached_the_journal(self):
        """The engine's canonical state, not a re-derivation of it."""

        result = self.turn()
        state = str(result.get("run_state") or "")
        recorded = str(self.runs()[0]["terminal_verdict"] or "")

        self.assertTrue(state, "the turn carried no run_state to check")
        self.assertEqual(recorded, state)
        self.assertEqual(recorded, "awaiting_input")

    def test_a_turn_that_stopped_to_ask_is_not_filed_as_blocked(self):
        result = self.turn()
        verdict = str((result.get("completion_verdict") or {}).get("verdict") or "")

        # The verdict really does say blocked -- which is why preferring it
        # was wrong for this kind of ending.
        self.assertEqual(verdict, "blocked")
        self.assertNotEqual(self.runs()[0]["terminal_verdict"], "blocked")

    def test_the_run_still_counts_as_finished(self):
        """Asking a question ends the turn. It does not leave it running."""

        self.turn()

        summary = journal_runtime.unterminated_summary(self.root)

        self.assertEqual(summary["unterminated"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
