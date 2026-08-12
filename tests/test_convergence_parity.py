"""#648: account, local and free routes judge progress the same way.

The defect had a witness. A real run was asked to solve issue #614 on the
account route; it spent 60 steps reading the issue, the tests, the cancellation
modules and the git history, said "before writing edits", and was stopped one
step short by a guard that counted *edits*, not learning:

    No-progress guard: stopped after 60 steps without an edit attempt

The same investigation on the local route would have continued, because
`tool_loop.py` adopted #569's evidence scoring and the account route did not.
That is the whole of #648's "current defect": the same objective is healthy,
stuck or failed depending on which provider ran it.

These tests hold the two halves of the contract:

1. a long *productive* investigation keeps its budget on every route;
2. a *repetitive* one loses it quickly on every route -- materially before the
   60-step ceiling that stopped the witness run.
"""

from __future__ import annotations

import unittest

from opaihub.accounts import _LEDGER_TOOL_FOR_EVENT, _progress_observation
from opaihub.progress_evidence import MUTATING_TOOLS, ProgressLedger


PATIENCE = 12
CEILING = 60


def _event(etype: str, title: str, detail: str = "x", status: str = "ok") -> dict:
    return {"type": etype, "title": title, "detail": detail, "status": status}


def _run(events: list[dict]) -> ProgressLedger:
    ledger = ProgressLedger()
    for event in events:
        ledger.record(_progress_observation(event, str(event["type"])))
    return ledger


class WitnessRunTests(unittest.TestCase):
    """The exact run from the report must not be stopped for lacking an edit."""

    def test_sixty_steps_of_new_evidence_is_not_stagnant(self) -> None:
        """The witness: 60 distinct reads, no edit yet, still learning.

        Under the old rule this stopped at step 60 with "without an edit
        attempt". Under the evidence rule it is nowhere near stagnant, because
        every step returned something not seen before.
        """

        ledger = _run(
            [
                _event("file_read", f"read opaihub/module_{index}.py", f"body {index}")
                for index in range(CEILING)
            ]
        )
        self.assertFalse(ledger.is_stagnant(patience=PATIENCE))
        self.assertGreater(ledger.score, 0)
        self.assertEqual(ledger.milestones, 0, "no edit happened, and that is fine")

    def test_a_mixed_investigation_survives_too(self) -> None:
        """Reads, searches and a couple of failures still count as learning."""

        events: list[dict] = []
        for index in range(20):
            events.append(
                _event("command_run", f"gh issue view {index}", f"issue {index}")
            )
            events.append(
                _event("file_read", f"read tests/test_{index}.py", f"t{index}")
            )
            if index % 7 == 0:
                events.append(
                    _event("command_run", f"grep missing_{index}", "", status="error")
                )
        ledger = _run(events)
        self.assertFalse(ledger.is_stagnant(patience=PATIENCE))


class RepetitionTests(unittest.TestCase):
    """A loop must be caught early, not allowed to run to the ceiling."""

    def test_a_repeated_search_stagnates_well_before_the_ceiling(self) -> None:
        """#648: "intervention materially before step 60"."""

        ledger = ProgressLedger()
        steps = 0
        for _ in range(CEILING):
            ledger.record(
                _progress_observation(
                    _event("command_run", "grep -rn cancel_requested", "same output"),
                    "command_run",
                )
            )
            steps += 1
            if ledger.is_stagnant(patience=PATIENCE):
                break
        self.assertTrue(ledger.is_stagnant(patience=PATIENCE))
        self.assertLess(
            steps,
            CEILING // 2,
            "a pure repetition loop should be caught in far fewer steps than the "
            "ceiling that stopped the productive witness run",
        )

    def test_the_same_failure_twice_scores_negative(self) -> None:
        """A retried failing command is worse than useless, and scores that way."""

        ledger = ProgressLedger()
        first = ledger.record(
            _progress_observation(
                _event("command_run", "pytest tests/nope.py", "", status="error"),
                "command_run",
            )
        )
        second = ledger.record(
            _progress_observation(
                _event("command_run", "pytest tests/nope.py", "", status="error"),
                "command_run",
            )
        )
        self.assertGreater(first, 0, "a new failure is information")
        self.assertLess(second, 0, "the same failure again is not")

    def test_an_irrelevant_edit_cannot_reset_stagnation_forever(self) -> None:
        """#648 edge case: "rapid irrelevant edit to reset a counter".

        Under the old rule a single `file_edit` cleared `edit_attempted` and
        disarmed the guard for the rest of the run. Scoring makes the *same*
        edit repeated worth nothing, so it cannot be used to buy budget.
        """

        ledger = ProgressLedger()
        for _ in range(30):
            ledger.record(
                _progress_observation(
                    _event("file_edit", "edit scratch.txt", "same one-line change"),
                    "file_edit",
                )
            )
        self.assertTrue(
            ledger.is_stagnant(patience=PATIENCE),
            "repeating one identical edit bought unlimited budget",
        )


class RouteParityTests(unittest.TestCase):
    """Provider choice cannot change what progress means (#648 AC1/AC2)."""

    def test_the_account_route_scores_through_the_canonical_ledger(self) -> None:
        """Not a copy of the policy -- the same object the tool loop uses."""

        import inspect

        from opaihub import accounts, tool_loop

        self.assertIn("ProgressLedger", inspect.getsource(accounts))
        self.assertIn("progress_evidence", inspect.getsource(tool_loop))

    def test_an_edit_event_is_scored_as_a_repository_mutation(self) -> None:
        """The mapping must land `file_edit` in MUTATING_TOOLS.

        If it did not, a real repository change would score as ordinary
        evidence and the account route would undervalue exactly the action the
        old guard overvalued -- the same divergence, inverted.
        """

        self.assertIn(_LEDGER_TOOL_FOR_EVENT["file_edit"], MUTATING_TOOLS)

    def test_every_step_event_type_has_a_scoring_identity(self) -> None:
        """An unmapped event type would score as its own raw name.

        That is not wrong so much as unowned: it would silently fall into the
        "ran, told us little" bucket without anyone choosing that.
        """

        for etype in (
            "tool_call",
            "file_read",
            "file_edit",
            "command_run",
            "context_read",
            "ci_watch",
        ):
            with self.subTest(event=etype):
                self.assertIn(etype, _LEDGER_TOOL_FOR_EVENT)

    def test_identical_work_scores_identically_on_both_routes(self) -> None:
        """Same evidence in, same judgement out, whichever route observed it.

        The account route reports activity events and the tool loop reports
        tool observations. This drives one ledger from each shape and requires
        the same verdict -- which is the acceptance criterion "provider choice
        cannot change the meaning of progress".
        """

        account = _run(
            [
                _event("file_read", f"read m_{index}.py", f"b{index}")
                for index in range(30)
            ]
        )
        loop = ProgressLedger()
        for index in range(30):
            loop.record(
                {
                    "tool": "read_file",
                    "arguments": f"read m_{index}.py",
                    "content": f"b{index}",
                    "ok": True,
                }
            )
        self.assertEqual(account.score, loop.score)
        self.assertEqual(
            account.is_stagnant(patience=PATIENCE),
            loop.is_stagnant(patience=PATIENCE),
        )


class ObservabilityTests(unittest.TestCase):
    """A stop must explain itself with evidence (#648: record reason + caps)."""

    def test_the_summary_carries_the_evidence_behind_a_stop(self) -> None:
        ledger = _run([_event("file_read", "read a.py", "body")])
        summary = ledger.summary()
        for key in (
            "score",
            "best_score",
            "steps_since_best",
            "distinct_observations",
            "repeated_failures",
            "milestones",
        ):
            with self.subTest(field=key):
                self.assertIn(key, summary)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
