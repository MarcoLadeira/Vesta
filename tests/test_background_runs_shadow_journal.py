"""#613 Stage 2: background runs' shadow journal, and the states a terminal one hides.

Stage 1 named ``vestahub/background_runs.py`` JOURNAL_OWNED -- "runs: background
run records and notifications". Every lifecycle transition routes through one
``_save_run``, which makes this the cleanest module in the migration to mirror
and the one where the journal buys the most: the file only ever shows the
*latest* state, and ``_transition_run`` refuses to move once a run is terminal.
So the file cannot answer "what happened", only "what it ended as". The journal
keeps the sequence.

The validator is deliberately thin -- it checks for a ``run_id`` and nothing
else. Two reasons, and they point the same way. #612 made the lifecycle schema
the single authority on the state vocabulary and forbids modules restating it,
so a hand-written tuple of states here would be a second copy to drift. And it
would be the empty-state trap again: a freshly queued run is the record most
worth keeping, and a validator that knew only the states someone remembered to
type would drop the rest in silence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vestahub.background_runs import (
    _background_dir,
    enqueue_automation,
    list_runs,
    load_run,
    request_cancel,
    run_contradiction_report,
    run_shadow_projection,
)


class _RunFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _queue(
        self,
        task: str = "fix the failing login test",
        *,
        workflow_id: str = "bug_fix",
    ):
        return enqueue_automation(self.root, workflow_id, task)

    def _path(self, run_id: str) -> Path:
        return _background_dir(self.root) / "runs" / f"{run_id}.json"


class ShadowMirrorsRunTransitionsTests(_RunFixture):
    def test_queueing_is_mirrored_and_the_shadow_agrees_with_the_file(self):
        run = self._queue()

        shadow = run_shadow_projection(self.root, run.run_id)

        self.assertEqual(shadow["run_id"], run.run_id)
        self.assertIsNone(run_contradiction_report(self.root, run.run_id))

    def test_a_freshly_queued_run_is_not_dropped_as_an_empty_state(self):
        """The trap, seventh time: the opening record is a real record."""

        run = self._queue()

        shadow = run_shadow_projection(self.root, run.run_id)

        self.assertNotEqual(shadow, {})
        self.assertEqual(shadow["run_id"], run.run_id)

    def test_cancelling_moves_both_sides_together(self):
        run = self._queue()

        cancelled = request_cancel(self.root, run.run_id)

        self.assertEqual(
            run_shadow_projection(self.root, run.run_id)["run_state"],
            cancelled.to_dict()["run_state"],
        )
        self.assertIsNone(run_contradiction_report(self.root, run.run_id))

    def test_the_shadow_keeps_states_the_file_can_no_longer_show(self):
        """The reason this module benefits most.

        ``_transition_run`` refuses to move a terminal run, so the file ends up
        holding only the final state. Replaying the journal recovers the run's
        opening state as well -- what happened, not just what it ended as.
        """

        run = self._queue()
        queued_state = load_run(self.root, run.run_id).to_dict()["run_state"]
        request_cancel(self.root, run.run_id)
        final_state = load_run(self.root, run.run_id).to_dict()["run_state"]

        self.assertNotEqual(queued_state, final_state)

        journal = _journal_records(self._path(run.run_id))
        states = [record.get("run_state") for record in journal]

        self.assertIn(queued_state, states)
        self.assertIn(final_state, states)
        self.assertEqual(states[-1], final_state)

    def test_separate_runs_keep_separate_shadows(self):
        first = self._queue("fix the failing login test")
        second = self._queue("review the auth module", workflow_id="review")

        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(
            run_shadow_projection(self.root, first.run_id)["run_id"], first.run_id
        )
        self.assertEqual(
            run_shadow_projection(self.root, second.run_id)["run_id"], second.run_id
        )


class TheJournalMustNotBeListedAsARunTests(_RunFixture):
    """The glob trap, seventh occurrence -- silent-skip variant.

    ``list_runs`` globs ``*.json`` and swallows anything that fails to load, so
    a sibling journal head cache would not have crashed anything; it would just
    have sat in the runs directory forever being silently skipped. The shared
    helper's ``journal/`` subdirectory keeps it out.
    """

    def test_the_journal_never_appears_in_the_run_listing(self):
        run = self._queue()
        request_cancel(self.root, run.run_id)

        listed = list_runs(self.root)

        self.assertEqual([item.run_id for item in listed], [run.run_id])

    def test_no_journal_file_sits_in_the_globbed_runs_directory(self):
        run = self._queue()

        stray = [
            path.name
            for path in (_background_dir(self.root) / "runs").glob("*.json")
            if path.stem != run.run_id
        ]

        self.assertEqual(stray, [])


class ContradictionReportIsExactTests(_RunFixture):
    def test_an_out_of_band_state_change_is_reported(self):
        """The scenario #613 exists for: a run rewritten behind our back."""

        run = self._queue()
        path = self._path(run.run_id)
        tampered = {**json.loads(path.read_text(encoding="utf-8"))}
        tampered["task"] = "something nobody asked for"
        path.write_text(json.dumps(tampered), encoding="utf-8")

        report = run_contradiction_report(self.root, run.run_id)

        self.assertIsNotNone(report)
        self.assertIn("task", report["mismatched_fields"])
        self.assertEqual(report["run_id"], run.run_id)

    def test_a_lost_run_file_is_reported_against_a_surviving_shadow(self):
        run = self._queue()
        self._path(run.run_id).unlink()

        report = run_contradiction_report(self.root, run.run_id)

        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})
        self.assertEqual(report["shadow"]["run_id"], run.run_id)

    def test_a_corrupt_run_file_is_reported_rather_than_raising(self):
        """``load_run`` raises here by design; the comparator must not."""

        run = self._queue()
        self._path(run.run_id).write_text("{not json", encoding="utf-8")

        report = run_contradiction_report(self.root, run.run_id)

        self.assertIsNotNone(report)
        self.assertEqual(report["legacy"], {})
        self.assertEqual(report["shadow"]["run_id"], run.run_id)

    def test_a_never_queued_run_agrees_as_both_empty(self):
        self.assertEqual(run_shadow_projection(self.root, "run-nothing"), {})
        self.assertIsNone(run_contradiction_report(self.root, "run-nothing"))


def _journal_records(record_path: Path) -> list[dict]:
    from vestahub import shadow_journal

    journal = shadow_journal.journal_path_for(record_path)
    if not journal.exists():
        return []
    return [
        json.loads(line)["record"]
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip() and "record" in json.loads(line)
    ]


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
