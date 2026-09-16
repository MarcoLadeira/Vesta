"""Cancellation as a durable lifecycle, not a boolean (#380).

`CancelPhase` refines the canonical `RunState.CANCEL_REQUESTED` the same way
`RuntimePhase` refines the rest of the run lifecycle (#379) — a closed,
ordered vocabulary, backed by the append-only journal #517 built, so
"cancellation acknowledgement latency" and "hard-stop latency" are numbers
read from durable evidence rather than estimated after the fact.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from vestahub import journal_runtime
from vestahub.journal_runtime import record_admission, record_terminal
from vestahub.journal_store import open_store
from vestahub.cancellation_lifecycle import CancelPhase, CancellationTracker, is_terminal
from vestahub.run_state import RunState, canonical_for_cancel_phase


class _Temp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def tracker(self, scope_id: str = "run-1") -> CancellationTracker:
        return CancellationTracker(self.root, scope_id)


class FreshTrackerTests(_Temp):
    def test_a_fresh_scope_has_no_phase_or_history(self) -> None:
        tracker = self.tracker()
        self.assertIsNone(tracker.phase())
        self.assertEqual(tracker.history(), ())


class CanonicalProjectionTests(unittest.TestCase):
    def test_every_cancel_phase_declares_the_state_it_refines(self) -> None:
        for phase in CancelPhase:
            self.assertIsInstance(canonical_for_cancel_phase(phase), RunState)

    def test_only_confirmed_termination_projects_to_cancelled(self) -> None:
        for phase in CancelPhase:
            expected = (
                RunState.CANCELLED
                if phase is CancelPhase.TERMINATED
                else RunState.CANCEL_REQUESTED
            )
            self.assertIs(canonical_for_cancel_phase(phase), expected)

    def test_an_unknown_cancel_phase_is_rejected_not_guessed(self) -> None:
        with self.assertRaises(ValueError):
            canonical_for_cancel_phase("some_invented_phase")


class SingleStepTests(_Temp):
    def test_request_records_the_requested_phase(self) -> None:
        tracker = self.tracker()
        self.assertEqual(tracker.request(), CancelPhase.REQUESTED)
        self.assertEqual(tracker.phase(), CancelPhase.REQUESTED)

    def test_acknowledge_bootstraps_a_request_first(self) -> None:
        tracker = self.tracker()
        tracker.acknowledge()
        phases = [entry["phase"] for entry in tracker.history()]
        self.assertEqual(phases, ["requested", "acknowledged"])

    def test_the_full_walk_records_every_phase_once(self) -> None:
        tracker = self.tracker()
        tracker.request()
        tracker.acknowledge()
        tracker.begin_draining()
        tracker.force_terminate()
        tracker.mark_terminated()
        phases = [entry["phase"] for entry in tracker.history()]
        self.assertEqual(
            phases,
            [
                "requested",
                "acknowledged",
                "draining",
                "force_terminating",
                "terminated",
            ],
        )


class SkipTests(_Temp):
    """Nothing was in flight: the tracker may jump straight to the end."""

    def test_mark_terminated_on_a_fresh_scope_walks_through_acknowledged(self) -> None:
        tracker = self.tracker()
        self.assertEqual(tracker.mark_terminated(), CancelPhase.TERMINATED)
        phases = [entry["phase"] for entry in tracker.history()]
        # Skips draining/force_terminating entirely — there was nothing to
        # drain — but still records the phases it did pass through honestly.
        self.assertEqual(phases, ["requested", "acknowledged", "terminated"])

    def test_a_skip_that_never_forces_reports_forced_false(self) -> None:
        tracker = self.tracker()
        tracker.mark_terminated()
        self.assertFalse(tracker.metrics().forced)


class IdempotencyTests(_Temp):
    """Repeated and simultaneous cancellation (#380's own named test cases)."""

    def test_requesting_twice_does_not_duplicate_history(self) -> None:
        tracker = self.tracker()
        tracker.request()
        tracker.request()
        tracker.request()
        self.assertEqual(len(tracker.history()), 1)

    def test_an_earlier_phase_call_after_a_later_one_is_a_no_op(self) -> None:
        tracker = self.tracker()
        tracker.mark_terminated()
        before = tracker.history()
        self.assertEqual(tracker.request(), CancelPhase.TERMINATED)
        self.assertEqual(tracker.acknowledge(), CancelPhase.TERMINATED)
        self.assertEqual(tracker.history(), before)

    def test_terminated_is_immutable(self) -> None:
        tracker = self.tracker()
        tracker.mark_terminated()
        tracker.force_terminate()
        tracker.begin_draining()
        self.assertEqual(tracker.phase(), CancelPhase.TERMINATED)
        self.assertTrue(is_terminal(tracker.phase()))

    def test_two_racing_callers_converge_on_one_consistent_history(self) -> None:
        # Simulates a GUI Stop click and a CLI Ctrl+C landing on the same
        # scope: both call through independent tracker instances (as two
        # threads/processes would), and the durable result must not fork.
        gui = self.tracker()
        cli = self.tracker()
        results: list[CancelPhase] = []
        barrier = threading.Barrier(2)

        def act(tracker: CancellationTracker) -> None:
            barrier.wait(timeout=5)
            results.append(tracker.request())

        threads = [
            threading.Thread(target=act, args=(gui,)),
            threading.Thread(target=act, args=(cli,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(set(results), {CancelPhase.REQUESTED})
        self.assertEqual(len(gui.history()), 1)
        self.assertEqual(gui.history(), cli.history())


class DurabilityTests(_Temp):
    def test_a_fresh_tracker_instance_sees_prior_evidence(self) -> None:
        first = self.tracker()
        first.request()
        first.acknowledge()
        second = self.tracker()  # a new process reopening the same scope
        self.assertEqual(second.phase(), CancelPhase.ACKNOWLEDGED)
        self.assertEqual(len(second.history()), 2)

    def test_evidence_contains_phase_history_and_recorded_latency(self) -> None:
        tracker = self.tracker("run-evidence")
        tracker.request()
        tracker.acknowledge()
        tracker.mark_terminated()

        evidence = tracker.evidence()

        self.assertEqual(evidence["scope_id"], "run-evidence")
        self.assertEqual(evidence["phase"], "terminated")
        self.assertEqual(evidence["history"][-1]["phase"], "terminated")
        self.assertIsNotNone(evidence["metrics"]["acknowledgement_latency_seconds"])
        self.assertIsNotNone(evidence["metrics"]["hard_stop_latency_seconds"])

    def test_different_scopes_never_cross_talk(self) -> None:
        run_a = self.tracker("run-a")
        run_b = self.tracker("run-b")
        run_a.mark_terminated()
        self.assertIsNone(run_b.phase())


class MetricsTests(_Temp):
    def test_metrics_are_none_before_the_relevant_phase_lands(self) -> None:
        tracker = self.tracker()
        metrics = tracker.metrics()
        self.assertIsNone(metrics.requested_at)
        self.assertIsNone(metrics.acknowledgement_latency_seconds)
        self.assertIsNone(metrics.hard_stop_latency_seconds)

    def test_metrics_report_zero_latency_for_an_instantaneous_skip(self) -> None:
        tracker = self.tracker()
        tracker.mark_terminated()
        metrics = tracker.metrics()
        self.assertIsNotNone(metrics.acknowledgement_latency_seconds)
        self.assertIsNotNone(metrics.hard_stop_latency_seconds)
        self.assertGreaterEqual(metrics.hard_stop_latency_seconds, 0.0)

    def test_forced_is_true_only_when_force_terminating_actually_happened(self) -> None:
        tracker = self.tracker()
        tracker.request()
        tracker.acknowledge()
        tracker.begin_draining()
        tracker.force_terminate()
        tracker.mark_terminated()
        self.assertTrue(tracker.metrics().forced)

    def test_metrics_to_dict_is_json_shaped(self) -> None:
        tracker = self.tracker()
        tracker.mark_terminated()
        data = tracker.metrics().to_dict()
        self.assertIn("hard_stop_latency_seconds", data)
        self.assertIn("forced", data)


class ScopeSafetyTests(_Temp):
    def test_an_unsafe_scope_id_is_refused(self) -> None:
        for bad in ("../escape", "has space", "trailing/slash/"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    CancellationTracker(self.root, bad)


class CancellationPhasesReachTheCanonicalJournalTests(unittest.TestCase):
    """#818: "make cancellation explicitly two-phase", and "cancellation and
    teardown evidence" among the things a canonical record must reconstruct.

    The phases were already modelled properly and already durable -- in a
    *different* journal. So a reader of the canonical record could see that a
    run ended and not whether the stop was asked for, seen, or confirmed, and
    answering that meant consulting a second record. Two authorities for one
    question is the shape #818 exists to remove.

    `cancellation_lifecycle` stays the authority: it holds the lock that makes
    two racing cancellations converge. This only mirrors what it already
    decided.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.fence = record_admission(
            self.root,
            task_id="t",
            run_id="r1",
            task="a run",
            now="2026-09-08T10:00:00+00:00",
            surface="background",
        )

    def _tracker(self, **kwargs) -> CancellationTracker:
        return CancellationTracker(self.root, "background-r1", **kwargs)

    def _phases(self) -> list[tuple[str, str]]:
        store = open_store(self.root)
        try:
            return [
                (
                    json.loads(row["payload"])["phase"],
                    json.loads(row["payload"])["reason_code"],
                )
                for row in store.execute(
                    "SELECT payload FROM events WHERE run_id = 'r1'"
                    " AND event_type = ? ORDER BY sequence",
                    (journal_runtime.EVENT_CANCEL_PHASE,),
                )
            ]
        finally:
            store.close()

    def test_each_accepted_phase_is_recorded_in_order(self):
        tracker = self._tracker(journal_run_id="r1")

        tracker.request()
        tracker.acknowledge()
        tracker.mark_terminated()

        self.assertEqual(
            [phase for phase, _reason in self._phases()],
            ["requested", "acknowledged", "terminated"],
        )

    def test_the_reason_travels_with_the_phase(self):
        tracker = self._tracker(journal_run_id="r1")

        tracker.request(reason_code="user_requested")

        self.assertEqual(self._phases()[0], ("requested", "user_requested"))

    def test_an_idempotent_repeat_does_not_duplicate_the_evidence(self):
        """Two callers racing to cancel must converge, not double-record."""

        tracker = self._tracker(journal_run_id="r1")

        tracker.request()
        tracker.request()
        tracker.request()

        self.assertEqual(len(self._phases()), 1)

    def test_a_scope_that_is_not_a_journalled_run_records_nothing(self):
        """Scope ids are namespaced per caller and only some name a run.

        Guessing would write under a foreign key that does not exist or --
        worse -- one that does and belongs to something else.
        """

        tracker = self._tracker()

        with mock.patch.object(journal_runtime, "record_cancellation_phase") as mirror:
            self.assertEqual(tracker.request(), CancelPhase.REQUESTED)

        # Asserted on the *call*, not only on the absence of a row. A blank
        # run id is refused by the store too, so checking the table alone
        # passes even with this guard removed -- which it did, until this line.
        mirror.assert_not_called()
        self.assertEqual(self._phases(), [])

    def test_an_unknown_run_is_refused_by_the_store_not_invented(self):
        tracker = CancellationTracker(
            self.root, "background-ghost", journal_run_id="no-such-run"
        )

        self.assertEqual(tracker.request(), CancelPhase.REQUESTED)
        self.assertEqual(self._phases(), [])

    def test_a_broken_mirror_never_breaks_the_stop(self):
        """The one property that matters more than the evidence itself."""

        tracker = self._tracker(journal_run_id="r1")

        with mock.patch.object(
            journal_runtime,
            "record_cancellation_phase",
            side_effect=RuntimeError("journal on fire"),
        ):
            phase = tracker.request()

        self.assertEqual(phase, CancelPhase.REQUESTED)
        self.assertEqual(tracker.phase(), CancelPhase.REQUESTED)

    def test_a_terminated_phase_after_the_run_was_filed_still_lands(self):
        """Confirmed teardown routinely arrives after the verdict."""

        tracker = self._tracker(journal_run_id="r1")
        tracker.request()
        record_terminal(
            self.root,
            run_id="r1",
            event_type=journal_runtime.EVENT_CANCELLED,
            verdict="cancelled",
            reason="user",
            now="2026-09-08T10:00:00+00:00",
            fence=self.fence,
        )

        tracker.mark_terminated()

        self.assertIn("terminated", [phase for phase, _ in self._phases()])

    def test_a_real_background_run_names_its_journal_run(self):
        """The wiring, not just the mechanism.

        `_background_cancellation_tracker` is the one caller that knows a
        scope is a journalled run. Removing the argument it passes left every
        other test here green, because they construct the tracker themselves.
        """

        from vestahub import background_runs
        from vestahub.run_state import RunState

        run = background_runs.enqueue_automation(
            self.root, "refactor", "a task", run_id="bg-run"
        )
        background_runs._transition_run(
            self.root, run.run_id, target=RunState.RUNNING, reason_code="started"
        )

        tracker = background_runs._background_cancellation_tracker(
            self.root, run.run_id
        )
        self.assertEqual(tracker.journal_run_id, run.run_id)

        tracker.request()

        store = open_store(self.root)
        try:
            phases = [
                json.loads(row["payload"])["phase"]
                for row in store.execute(
                    "SELECT payload FROM events WHERE run_id = ?"
                    " AND event_type = ? ORDER BY sequence",
                    (run.run_id, journal_runtime.EVENT_CANCEL_PHASE),
                )
            ]
        finally:
            store.close()

        self.assertEqual(phases, ["requested"])

    def test_blank_identifiers_are_refused(self):
        for run_id, phase in (("", "requested"), ("r1", "")):
            with self.subTest(run_id=run_id, phase=phase):
                self.assertFalse(
                    journal_runtime.record_cancellation_phase(
                        self.root,
                        run_id=run_id,
                        phase=phase,
                        reason_code="x",
                        now="2026-09-08T10:00:00+00:00",
                    )
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
