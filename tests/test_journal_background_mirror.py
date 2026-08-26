"""#613: background runs mirrored into the journal, and the loop finally closed.

Stages 4, 5 and 7 were all written against a *legacy corpus* a caller supplies,
and no caller supplied one. The qualification, reader and retirement machinery
was correct, tested, and unreachable from the running application: doctor could
only answer "needs_legacy_comparison", because nothing had assembled the other
half of the comparison.

``background_runs`` is that other half -- the one legacy record in OPai that is
durable, enumerable and keyed by run id. Mirroring it makes both halves cover
the same population, which is what turns Stages 4-7 from machinery into a
migration that can actually be finished.

The subtle test here is ``test_a_run_is_admitted_once_however_often_it_is_saved``.
``_save_run`` rewrites the whole run document on every transition, so the
mirror is handed a *state*, not a change. Calling ``record_admission`` from
there would be idempotent about the run row and about nothing else: each call
takes a fresh lease and appends another ``admitted`` event, so a run that moved
six times would replay as six admissions of one run. A run is admitted once.
Everything after that is a transition, and the journal has to say so or the
replay is fiction.
"""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import background_runs, journal_background, journal_runtime
from opaihub.journal_runtime import EVENT_ADMITTED, EVENT_FINISHED, EVENT_TRANSITIONED
from opaihub.journal_store import journal_path, open_store

WORKFLOW = "bug_fix"


class _BackgroundFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _enqueue(self, task: str = "a task"):
        return background_runs.enqueue_automation(
            self.root, workflow_id=WORKFLOW, task=task
        )

    def _advance(self, run, **changes):
        updated = dataclasses.replace(run, **changes)
        background_runs._save_run(self.root, updated)
        return updated

    def _events(self) -> dict[str, int]:
        store = open_store(self.root)
        self.addCleanup(store.close)
        return {
            row["event_type"]: row["c"]
            for row in store.execute(
                "SELECT event_type, COUNT(*) c FROM events GROUP BY event_type"
            )
        }

    def _run_row(self, run_id: str):
        store = open_store(self.root)
        self.addCleanup(store.close)
        return store.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()


class TheMirrorFollowsTheSavePathTests(_BackgroundFixture):
    """Every lifecycle transition already routes through ``_save_run``."""

    def test_enqueuing_a_run_admits_it(self):
        run = self._enqueue()

        self.assertIsNotNone(self._run_row(run.run_id))
        self.assertEqual(self._events().get(EVENT_ADMITTED), 1)

    def test_a_run_is_admitted_once_however_often_it_is_saved(self):
        """The reason this is a snapshot mirror and not a call to admission."""

        run = self._enqueue()
        run = self._advance(run, run_state="preparing", status="queued")
        run = self._advance(run, run_state="running", status="running")
        self._advance(run, run_state="verifying", status="running")

        events = self._events()
        self.assertEqual(events.get(EVENT_ADMITTED), 1, "a run is admitted once")
        self.assertEqual(events.get(EVENT_TRANSITIONED), 3)

    def test_reaching_a_terminal_state_records_the_verdict(self):
        run = self._enqueue()

        self._advance(run, run_state="completed", status="done")

        self.assertEqual(self._run_row(run.run_id)["terminal_verdict"], "completed")
        self.assertEqual(self._events().get(EVENT_FINISHED), 1)

    def test_every_terminal_state_is_carried_across_by_name(self):
        """ "failed" and "cancelled" are different outcomes and must stay so."""

        for state in ("failed", "cancelled", "timeout", "partial", "blocked"):
            with self.subTest(state=state):
                run = self._enqueue(f"task {state}")
                self._advance(run, run_state=state)

                self.assertEqual(self._run_row(run.run_id)["terminal_verdict"], state)

    def test_a_non_terminal_state_records_no_verdict(self):
        """An unfinished run must not read as finished."""

        run = self._enqueue()

        self._advance(run, run_state="running", status="running")

        self.assertFalse(self._run_row(run.run_id)["terminal_verdict"])

    def test_two_runs_of_one_workflow_are_two_runs_of_one_task(self):
        first = self._enqueue("first")
        second = self._enqueue("second")

        store = open_store(self.root)
        self.addCleanup(store.close)
        rows = {
            row["run_id"]: row["attempt"]
            for row in store.execute("SELECT run_id, attempt FROM runs")
        }

        self.assertEqual(set(rows), {first.run_id, second.run_id})
        self.assertEqual(sorted(rows.values()), [1, 2], "one task, two attempts")


class TheMirrorNeverFailsARealWriteTests(_BackgroundFixture):
    """The legacy file is authoritative until Stage 7. The mirror is not."""

    def test_a_broken_mirror_still_persists_the_run(self):
        with mock.patch.object(
            journal_runtime, "record_run_snapshot", side_effect=OSError("disk full")
        ):
            run = self._enqueue()

        self.assertEqual(
            background_runs.load_run(self.root, run.run_id).run_id, run.run_id
        )

    def test_a_broken_mirror_still_advances_the_run(self):
        run = self._enqueue()

        with mock.patch.object(
            journal_runtime, "record_run_snapshot", side_effect=RuntimeError("gone")
        ):
            self._advance(run, run_state="completed", status="done")

        self.assertEqual(
            background_runs.load_run(self.root, run.run_id).run_state, "completed"
        )

    def test_a_snapshot_with_a_blank_identifier_is_refused_not_raised(self):
        self.assertFalse(
            journal_runtime.record_run_snapshot(
                self.root,
                run_id="",
                task_id="t",
                task="x",
                state="running",
                now="2026-08-26T11:00:00+00:00",
            )
        )


class TheLegacyCorpusTests(_BackgroundFixture):
    """The half of the comparison that was missing."""

    def test_finished_runs_carry_their_verdict(self):
        run = self._enqueue()
        self._advance(run, run_state="completed", status="done")

        corpus = journal_background.legacy_runs(self.root)

        self.assertEqual(corpus[run.run_id]["terminal_verdict"], "completed")

    def test_an_unfinished_run_reports_no_verdict_rather_than_a_guess(self):
        """A guessed verdict would fabricate a disagreement with the journal."""

        run = self._enqueue()
        self._advance(run, run_state="running", status="running")

        corpus = journal_background.legacy_runs(self.root)

        self.assertEqual(corpus[run.run_id]["terminal_verdict"], "")

    def test_an_unknown_state_reports_no_verdict(self):
        """A record from a newer OPai must not be read as finished."""

        run = self._enqueue()
        fake = dataclasses.replace(run, run_state="some-future-state")

        self.assertEqual(journal_background._terminal_verdict(fake), "")

    def test_a_project_with_no_runs_has_an_empty_corpus(self):
        self.assertEqual(journal_background.legacy_runs(self.root), {})

    def test_an_unreadable_corpus_is_empty_rather_than_an_exception(self):
        """Doctor calls this when things are already broken."""

        with mock.patch.object(
            background_runs, "list_runs", side_effect=OSError("unreadable")
        ):
            self.assertEqual(journal_background.legacy_runs(self.root), {})


class TheLoopActuallyClosesTests(_BackgroundFixture):
    """The point of the whole exercise: both halves now cover one population."""

    def _finished(self, count: int) -> None:
        for index in range(count):
            self._advance(
                self._enqueue(f"task {index}"),
                run_state="completed",
                status="done",
            )

    def test_a_migrated_project_qualifies_against_its_own_legacy_record(self):
        from opaihub import journal_reader

        self._finished(25)

        reader = journal_reader.JournalReader(
            self.root, journal_background.legacy_runs(self.root)
        )

        self.assertTrue(reader.serving_from_journal)
        self.assertEqual(reader.compared_runs(), 25)
        self.assertEqual(len(reader.qualification.differences), 0)

    def test_retirement_reaches_ready_on_a_real_corpus(self):
        """The end state of the migration, proved rather than asserted."""

        from opaihub import journal_retirement

        self._finished(25)

        report = journal_retirement.assess(
            self.root, journal_background.legacy_runs(self.root), minimum_runs=20
        )

        self.assertTrue(report.ready, report.detail)
        self.assertEqual(report.compared_runs, 25)
        self.assertEqual(report.legacy_reads, 0)

    def test_a_disagreement_between_the_halves_blocks(self):
        """Teeth: the comparison has to be able to fail, or it proves nothing."""

        from opaihub import journal_retirement

        self._finished(25)
        corpus = journal_background.legacy_runs(self.root)
        victim = sorted(corpus)[0]
        corpus[victim]["terminal_verdict"] = "cancelled"

        report = journal_retirement.assess(self.root, corpus, minimum_runs=20)

        self.assertFalse(report.ready)
        self.assertIn("not_qualified", report.blockers)

    def test_the_journal_is_not_required_for_background_runs_to_work(self):
        """A project whose journal cannot be opened still runs normally."""

        run = self._enqueue()
        journal_path(self.root).write_bytes(b"not a database")

        self._advance(run, run_state="completed", status="done")

        self.assertEqual(
            background_runs.load_run(self.root, run.run_id).run_state, "completed"
        )


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()


class TheMirrorOpensTheStoreOnceTests(_BackgroundFixture):
    """A structural ratchet standing in for a performance test.

    The mirror runs inside ``_save_run``'s interprocess lock, so its cost is
    time nothing else in the process can write. The first version called
    ``live_fence``, ``record_event`` and ``record_terminal`` in turn, opened the
    store three times per save, and more than doubled the time the lock was
    held -- measured at +570 ms on a slow volume and +40% on an ordinary one.

    Asserting the open count rather than a duration is deliberate: a timing
    threshold that passes on a developer SSD and fails on a CI runner teaches
    people to ignore it, while "how many times did this open the database" is
    the same number everywhere and is the thing that actually regressed.
    """

    def _opens_during(self, action) -> int:
        from opaihub import journal_store

        calls = 0
        real = journal_store.open_store

        def counting(*args, **kwargs):
            nonlocal calls
            calls += 1
            return real(*args, **kwargs)

        with mock.patch.object(journal_runtime, "open_store", counting):
            action()
        return calls

    def test_a_transition_opens_the_store_once(self):
        run = self._enqueue()

        opens = self._opens_during(
            lambda: self._advance(run, run_state="running", status="running")
        )

        self.assertEqual(opens, 1)

    def test_a_terminal_save_opens_the_store_once(self):
        run = self._enqueue()
        run = self._advance(run, run_state="running", status="running")

        opens = self._opens_during(
            lambda: self._advance(run, run_state="completed", status="done")
        )

        self.assertEqual(opens, 1)

    def test_the_first_save_of_a_run_opens_the_store_once(self):
        run = self._enqueue()
        # A fresh run id that the journal has never seen: the admission path.
        fresh = dataclasses.replace(run, run_id="never-seen-before")

        opens = self._opens_during(lambda: background_runs._save_run(self.root, fresh))

        self.assertEqual(opens, 1)
