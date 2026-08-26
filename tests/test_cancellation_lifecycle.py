"""Cancellation as a durable lifecycle, not a boolean (#380).

`CancelPhase` refines the canonical `RunState.CANCEL_REQUESTED` the same way
`RuntimePhase` refines the rest of the run lifecycle (#379) — a closed,
ordered vocabulary, backed by the append-only journal #517 built, so
"cancellation acknowledgement latency" and "hard-stop latency" are numbers
read from durable evidence rather than estimated after the fact.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from opaihub.cancellation_lifecycle import CancelPhase, CancellationTracker, is_terminal
from opaihub.run_state import RunState, canonical_for_cancel_phase


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
