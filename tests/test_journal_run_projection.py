"""#818: a fold that finally has a reducer, and a parity check that can run.

``tests/test_journal_projections.py`` proves ``rebuild_projection`` folds
deterministically -- using a reducer it defines itself. Nothing in OPai ever
supplied one. It was the fifth piece of #613 machinery this branch has found
with no importer, alongside ``journal_reader``, the lease identity columns,
the ``approvals`` table and ``mirror_from_status``. A fold with no reducer
answers nothing, so #818's "collapse task/run/status projections into
deterministic reducers over canonical events" had nothing to collapse into.

The parity check is the part worth having. Migration steps 2 and 4 have both
been blocked on the same thing: the legacy corpus and the journal's runs come
from different subsystems, so their populations can never overlap and no
amount of waiting produces a comparison.

This one compares the journal against *itself*. The `runs` table and the
`events` table are written by the same lifecycle calls in the same
transactions -- two recordings of one history. If they disagree, the canonical
store is contradicting itself, which is the failure #613 opens by describing,
inside the thing meant to settle it. And it needs no legacy corpus, so unlike
the qualification comparison it can actually run today.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opaihub import journal_projections, journal_runtime, journal_store

NOW = "2026-09-08T10:00:00+00:00"
LATER = "2026-09-08T10:05:00+00:00"


class ReducerTests(unittest.TestCase):
    """The fold itself: pure, total, and order-dependent in the right way."""

    def fold(self, *events: dict) -> dict:
        projection = journal_projections.empty_runs()
        for event in events:
            projection = journal_projections.reduce_runs(projection, event)
        return projection

    def event(self, event_type: str, run_id: str = "run-1", **payload) -> dict:
        return {"run_id": run_id, "event_type": event_type, "payload": payload}

    def test_an_admitted_run_appears(self):
        folded = self.fold(self.event(journal_runtime.EVENT_ADMITTED))

        entry = folded["runs"]["run-1"]
        self.assertTrue(entry["admitted"])
        self.assertEqual(entry["terminal_verdict"], "")

    def test_a_terminal_event_carries_its_verdict(self):
        folded = self.fold(
            self.event(journal_runtime.EVENT_ADMITTED),
            self.event(
                journal_runtime.EVENT_FINISHED, verdict="completed", reason="answered"
            ),
        )

        entry = folded["runs"]["run-1"]
        self.assertEqual(entry["terminal_verdict"], "completed")
        self.assertEqual(entry["terminal_reason"], "answered")

    def test_a_second_ending_does_not_overwrite_the_first(self):
        """Replay must be idempotent, and the store forbids ending twice.

        A second terminal event in the log is a contradiction to preserve, not
        a correction to apply -- taking the later one would let a replay
        produce a different answer than the live path did.
        """

        folded = self.fold(
            self.event(journal_runtime.EVENT_ADMITTED),
            self.event(journal_runtime.EVENT_FINISHED, verdict="completed"),
            self.event(journal_runtime.EVENT_CANCELLED, verdict="cancelled"),
        )

        self.assertEqual(folded["runs"]["run-1"]["terminal_verdict"], "completed")

    def test_costs_accumulate(self):
        folded = self.fold(
            self.event(journal_runtime.EVENT_COSTED, amount_usd=0.01),
            self.event(journal_runtime.EVENT_COSTED, amount_usd=0.02),
        )

        entry = folded["runs"]["run-1"]
        self.assertEqual(entry["cost_events"], 2)
        self.assertAlmostEqual(entry["cost_usd"], 0.03, places=9)

    def test_a_non_numeric_cost_is_not_added(self):
        folded = self.fold(
            self.event(journal_runtime.EVENT_COSTED, amount_usd=None),
            self.event(journal_runtime.EVENT_COSTED, amount_usd=True),
        )

        self.assertEqual(folded["runs"]["run-1"]["cost_usd"], 0.0)
        self.assertEqual(folded["runs"]["run-1"]["cost_events"], 2)

    def test_an_unknown_event_type_still_counts(self):
        """Total, not selective.

        Dropping it would make the projection's event count disagree with the
        log for a reason that is not a defect; raising would let one unknown
        type destroy the whole projection.
        """

        folded = self.fold(self.event("run.something_new_in_a_later_build"))

        self.assertEqual(folded["runs"]["run-1"]["events"], 1)

    def test_an_event_with_no_run_belongs_to_no_run(self):
        folded = self.fold(
            {"run_id": "", "event_type": "journal.pruned", "payload": {}}
        )

        self.assertEqual(folded["runs"], {})

    def test_the_fold_does_not_mutate_what_it_was_given(self):
        """A reducer that edits its input is not replayable."""

        first = self.fold(self.event(journal_runtime.EVENT_ADMITTED))
        snapshot = json.dumps(first, sort_keys=True)

        journal_projections.reduce_runs(
            first, self.event(journal_runtime.EVENT_FINISHED, verdict="completed")
        )

        self.assertEqual(json.dumps(first, sort_keys=True), snapshot)

    def test_replaying_the_same_history_gives_the_same_answer(self):
        history = [
            self.event(journal_runtime.EVENT_ADMITTED),
            self.event(journal_runtime.EVENT_COSTED, amount_usd=0.04),
            self.event(journal_runtime.EVENT_VERIFIED),
            self.event(journal_runtime.EVENT_FINISHED, verdict="completed"),
        ]

        once = json.dumps(self.fold(*history), sort_keys=True)
        twice = json.dumps(self.fold(*history), sort_keys=True)

        self.assertEqual(once, twice)


class ParityTests(unittest.TestCase):
    """The journal against itself."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def admit(self, run_id: str, task_id: str) -> int | None:
        return journal_runtime.record_admission(
            self.root,
            task_id=task_id,
            run_id=run_id,
            task="a turn",
            now=NOW,
            surface="cli",
        )

    def finish(self, run_id: str, fence: int | None) -> None:
        journal_runtime.record_terminal(
            self.root,
            run_id=run_id,
            event_type=journal_runtime.EVENT_FINISHED,
            verdict="completed",
            reason="answered",
            now=LATER,
            fence=fence,
        )

    def test_a_healthy_journal_agrees_with_itself(self):
        self.finish("run-1", self.admit("run-1", "task-1"))

        report = journal_projections.run_table_parity(self.root, now=LATER)

        self.assertTrue(report["comparable"], report["reason"])
        self.assertEqual(report["disagreements"], [])
        self.assertEqual(report["runs_in_table"], 1)
        self.assertEqual(report["runs_in_projection"], 1)

    def test_an_unfinished_run_agrees_too(self):
        """No terminal event and no verdict is agreement, not a disagreement."""

        self.admit("run-1", "task-1")

        report = journal_projections.run_table_parity(self.root, now=LATER)

        self.assertTrue(report["comparable"])
        self.assertEqual(report["disagreements"], [])

    def test_a_verdict_written_behind_the_events_back_is_caught(self):
        """The comparator has to be able to fail, or it decides nothing."""

        self.admit("run-1", "task-1")
        store = journal_store.open_store(self.root)
        try:
            store.execute(
                "UPDATE runs SET terminal_verdict = 'completed' WHERE run_id = ?",
                ("run-1",),
            )
            store.commit()
        finally:
            store.close()

        report = journal_projections.run_table_parity(self.root, now=LATER)

        self.assertTrue(report["comparable"])
        self.assertEqual(report["disagreement_count"], 1)
        found = report["disagreements"][0]
        self.assertEqual(found["run_id"], "run-1")
        self.assertEqual(found["field"], "terminal_verdict")
        self.assertEqual(found["table"], "completed")
        self.assertEqual(found["events"], "")

    def test_a_run_row_with_no_events_is_caught(self):
        store = journal_store.open_store(self.root)
        try:
            store.execute(
                "INSERT INTO tasks(task_id, origin_surface, origin_session,"
                " created_at, requested_outcome, schema_version, updated_at)"
                " VALUES ('task-x', 'cli', '', ?, 'x', 1, ?)",
                (NOW, NOW),
            )
            store.execute(
                "INSERT INTO runs(run_id, task_id, attempt, desired_state,"
                " observed_state, created_at, updated_at)"
                " VALUES ('ghost', 'task-x', 1, 'queued', 'queued', ?, ?)",
                (NOW, NOW),
            )
            store.commit()
        finally:
            store.close()

        report = journal_projections.run_table_parity(self.root, now=LATER)

        self.assertEqual(report["disagreement_count"], 1)
        self.assertEqual(report["disagreements"][0]["field"], "existence")
        self.assertEqual(report["disagreements"][0]["events"], "absent")

    def test_an_unreadable_journal_is_not_reported_as_agreement(self):
        journal_store.journal_path(self.root).parent.mkdir(parents=True, exist_ok=True)
        journal_store.journal_path(self.root).write_bytes(b"not a database")

        report = journal_projections.run_table_parity(self.root, now=LATER)

        self.assertFalse(report["comparable"])
        self.assertTrue(report["reason"])
        self.assertEqual(report["disagreements"], [])

    def test_a_degraded_projection_refuses_to_compare(self):
        """A short history against a complete table would report false diffs."""

        self.finish("run-1", self.admit("run-1", "task-1"))
        store = journal_store.open_store(self.root)
        try:
            store.execute(
                "UPDATE events SET payload = ? WHERE sequence ="
                " (SELECT MIN(sequence) FROM events)",
                ("{not json",),
            )
            store.commit()
        finally:
            store.close()

        report = journal_projections.run_table_parity(self.root, now=LATER)

        self.assertFalse(report["comparable"])
        self.assertIn("stopped at sequence", report["reason"])

    def test_the_projection_persists_and_reloads(self):
        self.finish("run-1", self.admit("run-1", "task-1"))

        journal_projections.rebuild_runs(self.root, now=LATER)

        store = journal_store.open_store(self.root)
        try:
            loaded = journal_store.load_projection(
                store,
                projection_type=journal_projections.RUN_PROJECTION,
                projection_version=journal_projections.RUN_PROJECTION_VERSION,
            )
        finally:
            store.close()

        self.assertIsNotNone(loaded)
        self.assertTrue(loaded["readable"])
        self.assertEqual(
            loaded["payload"]["runs"]["run-1"]["terminal_verdict"], "completed"
        )


class DoctorActuallyAsksTests(unittest.TestCase):
    """The sixth piece of machinery nothing imports would have been this one.

    A reducer and a parity check that no surface calls would be the exact
    shape this branch has now found five times in #613. So the wiring is
    pinned, not assumed.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_journal_doctor_reports_parity(self):
        from opai import cli

        fence = journal_runtime.record_admission(
            self.root,
            task_id="task-1",
            run_id="run-1",
            task="a turn",
            now=NOW,
            surface="cli",
        )
        journal_runtime.record_terminal(
            self.root,
            run_id="run-1",
            event_type=journal_runtime.EVENT_FINISHED,
            verdict="completed",
            reason="answered",
            now=LATER,
            fence=fence,
        )

        facts = cli._journal_migration(self.root)

        self.assertTrue(facts["event_table_parity_known"])
        self.assertEqual(facts["event_table_disagreements"], 0)

    def test_doctor_reports_a_disagreement_it_finds(self):
        from opai import cli

        journal_runtime.record_admission(
            self.root,
            task_id="task-1",
            run_id="run-1",
            task="a turn",
            now=NOW,
            surface="cli",
        )
        store = journal_store.open_store(self.root)
        try:
            store.execute(
                "UPDATE runs SET terminal_verdict = 'completed' WHERE run_id = ?",
                ("run-1",),
            )
            store.commit()
        finally:
            store.close()

        facts = cli._journal_migration(self.root)

        self.assertTrue(facts["event_table_parity_known"])
        self.assertEqual(facts["event_table_disagreements"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
