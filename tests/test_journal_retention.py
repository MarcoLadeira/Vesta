"""#613 functional requirement 5: retention and compaction by event class.

    Define retention/compaction separately for audit-critical versus
    high-volume presentation events.

Almost every test here is about what is **not** deleted, which is the right
weighting: the cost of keeping too much is disk space, which is visible and
fixable, and the cost of deleting too much is a history that quietly stopped
being able to explain itself.

The load-bearing test is ``test_compacting_does_not_change_a_projection``. It
rebuilds a projection, compacts, rebuilds again, and compares canonical bytes.
That single assertion is what makes retention safe at all: if compaction can
change a projection then something a projection depends on was classified as
disposable, and the classification is wrong. Every other rule here is a
narrowing on top of that one guarantee.

``test_an_unclassified_event_type_is_never_pruned`` is the other half. The
failure mode of a delete-list is that somebody adds an event type, does not
know this module exists, and finds out a year later that the thing they needed
was being deleted the whole time. Default-deny makes that impossible by
construction, and the test makes the default explicit rather than incidental.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from opaihub import journal_retention
from opaihub.journal_retention import (
    DEFAULT_FLOOR_PER_RUN,
    PRESENTATION_EVENTS,
    compact,
    is_prunable,
    plan,
    retention_health,
)
from opaihub.journal_runtime import (
    EVENT_ADMITTED,
    EVENT_FINISHED,
    EVENT_TRANSITIONED,
    record_admission,
    record_terminal,
)
from opaihub.journal_store import (
    append_event,
    canonical_bytes,
    journal_path,
    open_store,
    rebuild_projection,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
NOW_ISO = NOW.isoformat()
LONG_AGO = (NOW - timedelta(days=400)).isoformat()
RECENT = (NOW - timedelta(days=2)).isoformat()


def _verdicts(projection, record):
    """A fold over exactly the audit-critical events.

    Deliberately reduces over admission and terminal only -- the two things a
    run's state is actually made of -- so that if compaction ever removed one,
    the rebuilt projection would differ and the invariant test would say so.
    """

    state = dict(projection or {})
    if record["event_type"] in (EVENT_FINISHED, EVENT_ADMITTED):
        payload = record["payload"] or {}
        state[str(record["run_id"])] = str(payload.get("verdict") or "running")
    return state


class _RetentionFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _store(self):
        store = open_store(self.root)
        self.addCleanup(store.close)
        return store

    def _run(
        self,
        run_id: str,
        *,
        transitions: int = 0,
        recorded_at: str = LONG_AGO,
        finish: bool = True,
    ) -> None:
        fence = record_admission(
            self.root, task_id="task-a", run_id=run_id, task="a task", now=NOW_ISO
        )
        store = open_store(self.root)
        try:
            for index in range(transitions):
                append_event(
                    store,
                    event_type=EVENT_TRANSITIONED,
                    payload={"state": f"s{index}"},
                    occurred_at=recorded_at,
                    recorded_at=recorded_at,
                    producer="test",
                    run_id=run_id,
                    expected_fence=fence,
                )
        finally:
            store.close()
        if finish:
            record_terminal(
                self.root,
                run_id=run_id,
                event_type=EVENT_FINISHED,
                verdict="completed",
                reason="ok",
                now=NOW_ISO,
                fence=fence,
            )

    def _counts(self) -> dict[str, int]:
        store = open_store(self.root)
        self.addCleanup(store.close)
        return {
            row["event_type"]: row["c"]
            for row in store.execute(
                "SELECT event_type, COUNT(*) c FROM events GROUP BY event_type"
            )
        }


class TheClassificationIsDefaultDenyTests(unittest.TestCase):
    """An event nobody classified must be kept, not deleted."""

    def test_a_presentation_event_is_prunable(self):
        self.assertTrue(is_prunable(EVENT_TRANSITIONED))

    def test_audit_critical_events_are_not_prunable(self):
        for event_type in (EVENT_ADMITTED, EVENT_FINISHED, "run.cost_recorded"):
            with self.subTest(event_type=event_type):
                self.assertFalse(is_prunable(event_type))

    def test_an_unclassified_event_type_is_never_pruned(self):
        """The failure mode this design exists to make impossible."""

        self.assertFalse(is_prunable("some.future.event"))
        self.assertFalse(is_prunable(""))

    def test_the_prunable_set_stays_small_and_deliberate(self):
        """A ratchet. Growing this set is a decision, not a refactor.

        Each entry claims that nothing anywhere depends on that event, which is
        a statement about the whole system. Adding one should require editing
        this number and thinking about why.
        """

        self.assertLessEqual(len(PRESENTATION_EVENTS), 3, sorted(PRESENTATION_EVENTS))


class NothingAuditCriticalIsEverRemovedTests(_RetentionFixture):
    def test_admission_and_terminal_survive_compaction(self):
        self._run("run-a", transitions=20)

        compact(self.root, now=NOW_ISO, floor_per_run=0)

        counts = self._counts()
        self.assertEqual(counts.get(EVENT_ADMITTED), 1)
        self.assertEqual(counts.get(EVENT_FINISHED), 1)

    def test_cost_events_survive_compaction(self):
        """Spend is the one record a cost tool cannot afford to lose."""

        from opaihub.journal_runtime import record_run_cost

        fence = record_admission(
            self.root, task_id="t", run_id="run-a", task="x", now=NOW_ISO
        )
        record_run_cost(
            self.root,
            run_id="run-a",
            operation_key="op-1",
            amount_usd=1.25,
            measurement_kind="actual",
            now=NOW_ISO,
            fence=fence,
        )
        record_terminal(
            self.root,
            run_id="run-a",
            event_type=EVENT_FINISHED,
            verdict="completed",
            reason="",
            now=NOW_ISO,
            fence=fence,
        )

        compact(self.root, now=NOW_ISO, floor_per_run=0)

        store = self._store()
        self.assertEqual(
            store.execute("SELECT COUNT(*) FROM cost_events").fetchone()[0], 1
        )

    def test_compacting_does_not_change_a_projection(self):
        """The invariant that makes any of this safe.

        If compaction can change a projection, then something a projection
        depends on was classified as disposable and the classification is
        wrong. Comparing canonical bytes rather than dicts, because #613 asks
        for byte-identical rebuilds.
        """

        for index in range(4):
            self._run(f"run-{index}", transitions=15)

        store = open_store(self.root)
        try:
            before = rebuild_projection(
                store,
                projection_type="verdicts",
                projection_version=1,
                reduce=_verdicts,
                empty={},
                now=NOW_ISO,
            )
        finally:
            store.close()

        removed = compact(self.root, now=NOW_ISO, floor_per_run=0).removed_events
        self.assertGreater(removed, 0, "nothing was compacted; the test proves nothing")

        store = open_store(self.root)
        try:
            after = rebuild_projection(
                store,
                projection_type="verdicts",
                projection_version=1,
                reduce=_verdicts,
                empty={},
                now=NOW_ISO,
            )
        finally:
            store.close()

        # The *payload* is what must be invariant. ``source_sequence`` moves
        # because the highest surviving sequence changed, which is the
        # compaction working rather than the projection differing.
        self.assertEqual(
            canonical_bytes(before.payload), canonical_bytes(after.payload)
        )
        self.assertEqual(before.integrity, after.integrity)


class OnlySettledOldEventsAreRemovedTests(_RetentionFixture):
    def test_a_live_run_keeps_all_of_its_history(self):
        """A run still being written to is the one most likely to need it."""

        self._run("live", transitions=20, finish=False)

        report = compact(self.root, now=NOW_ISO, floor_per_run=0)

        self.assertEqual(report.removed_events, 0)
        self.assertEqual(self._counts().get(EVENT_TRANSITIONED), 20)

    def test_recent_events_are_kept_even_on_a_finished_run(self):
        self._run("run-a", transitions=20, recorded_at=RECENT)

        report = compact(self.root, now=NOW_ISO, floor_per_run=0)

        self.assertEqual(report.removed_events, 0)
        self.assertEqual(report.kept_within_window, 20)

    def test_old_events_on_a_finished_run_are_removed(self):
        self._run("run-a", transitions=20, recorded_at=LONG_AGO)

        report = compact(self.root, now=NOW_ISO, floor_per_run=0)

        self.assertEqual(report.removed_events, 20)
        self.assertEqual(self._counts().get(EVENT_TRANSITIONED, 0), 0)

    def test_a_floor_of_recent_events_survives_regardless_of_age(self):
        """A finished run should still show how it got there, not only that it did."""

        self._run("run-a", transitions=20, recorded_at=LONG_AGO)

        compact(self.root, now=NOW_ISO, floor_per_run=DEFAULT_FLOOR_PER_RUN)

        self.assertEqual(self._counts().get(EVENT_TRANSITIONED), DEFAULT_FLOOR_PER_RUN)

    def test_the_floor_keeps_the_most_recent_ones(self):
        self._run("run-a", transitions=10, recorded_at=LONG_AGO)

        compact(self.root, now=NOW_ISO, floor_per_run=3)

        store = self._store()
        states = [
            json.loads(row["payload"])["state"]
            for row in store.execute(
                "SELECT payload FROM events WHERE event_type = ? ORDER BY sequence",
                (EVENT_TRANSITIONED,),
            )
        ]
        self.assertEqual(states, ["s7", "s8", "s9"])

    def test_the_window_is_measured_on_recorded_at_not_occurred_at(self):
        """A clock rollback must not make yesterday's events look ancient.

        #613 lists clock rollback as an edge case. ``occurred_at`` comes from
        whatever clock produced the event; ``recorded_at`` is ours.
        """

        fence = record_admission(
            self.root, task_id="t", run_id="run-a", task="x", now=NOW_ISO
        )
        store = open_store(self.root)
        try:
            append_event(
                store,
                event_type=EVENT_TRANSITIONED,
                payload={"state": "s"},
                occurred_at=LONG_AGO,  # a rolled-back clock
                recorded_at=RECENT,  # when we actually saw it
                producer="test",
                run_id="run-a",
                expected_fence=fence,
            )
        finally:
            store.close()
        record_terminal(
            self.root,
            run_id="run-a",
            event_type=EVENT_FINISHED,
            verdict="completed",
            reason="",
            now=NOW_ISO,
            fence=fence,
        )

        report = compact(self.root, now=NOW_ISO, floor_per_run=0)

        self.assertEqual(report.removed_events, 0)


class ThePlanIsInspectableWithoutDeletingTests(_RetentionFixture):
    """Deciding what to delete and deleting it are separate on purpose."""

    def test_planning_removes_nothing(self):
        self._run("run-a", transitions=10)

        store = self._store()
        removable, _ = plan(store, now=NOW_ISO, floor_per_run=0)

        self.assertEqual(len(removable), 10)
        self.assertEqual(self._counts().get(EVENT_TRANSITIONED), 10)

    def test_the_report_says_what_was_kept_and_why(self):
        self._run("old", transitions=8, recorded_at=LONG_AGO)
        self._run("recent", transitions=4, recorded_at=RECENT)
        self._run("live", transitions=6, recorded_at=LONG_AGO, finish=False)

        store = self._store()
        _, report = plan(store, now=NOW_ISO, floor_per_run=0)

        self.assertEqual(report.removed_events, 8)
        self.assertEqual(report.kept_within_window, 4)
        self.assertEqual(report.kept_live_runs, 6)
        self.assertGreater(report.kept_audit_critical, 0)

    def test_the_report_is_json_serialisable(self):
        self._run("run-a", transitions=3)

        json.loads(json.dumps(compact(self.root, now=NOW_ISO).to_dict()))


class CompactionReclaimsSpaceTests(_RetentionFixture):
    """Deleting rows alone leaves the file the same size."""

    def test_vacuum_returns_pages_to_the_filesystem(self):
        for index in range(12):
            self._run(f"run-{index}", transitions=60, recorded_at=LONG_AGO)

        before = journal_path(self.root).stat().st_size
        report = compact(self.root, now=NOW_ISO, floor_per_run=0, reclaim=True)
        after = journal_path(self.root).stat().st_size

        self.assertGreater(report.removed_events, 0)
        self.assertLess(after, before)
        self.assertGreater(report.reclaimed_bytes, 0)

    def test_reclaim_can_be_skipped(self):
        """VACUUM rewrites the whole database and wants a quiet moment."""

        self._run("run-a", transitions=20, recorded_at=LONG_AGO)

        report = compact(self.root, now=NOW_ISO, floor_per_run=0, reclaim=False)

        self.assertGreater(report.removed_events, 0)
        self.assertEqual(report.reclaimed_bytes, 0)


class HousekeepingNeverTakesTheAppDownTests(_RetentionFixture):
    """Housekeeping that can break the application does not get run."""

    def test_a_project_with_no_journal_is_not_an_error(self):
        report = compact(self.root, now=NOW_ISO)

        self.assertEqual(report.removed_events, 0)
        self.assertIn("no journal", report.detail)

    def test_a_corrupt_journal_is_reported_rather_than_raised(self):
        self._run("run-a", transitions=2)
        journal_path(self.root).write_bytes(b"not a database")

        report = compact(self.root, now=NOW_ISO)

        self.assertEqual(report.removed_events, 0)
        self.assertIn("could not run", report.detail)

    def test_an_unparseable_timestamp_falls_back_rather_than_raising(self):
        self._run("run-a", transitions=3)

        compact(self.root, now="not-a-timestamp")

    def test_health_of_a_project_with_no_journal_is_unavailable(self):
        facts = retention_health(self.root)

        self.assertFalse(facts["available"])

    def test_health_counts_what_would_be_pruned(self):
        self._run("run-a", transitions=10, recorded_at=LONG_AGO)

        facts = retention_health(self.root)

        self.assertTrue(facts["available"])
        self.assertGreater(facts["total_events"], 0)
        self.assertGreater(facts["audit_critical"], 0)

    def test_health_never_raises(self):
        self._run("run-a", transitions=2)

        with mock.patch.object(
            journal_retention, "plan", side_effect=RuntimeError("boom")
        ):
            facts = retention_health(self.root)

        self.assertFalse(facts["available"])


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
