"""#818: the crash scenarios the required qualification names and the matrix lacks.

``tests/test_journal_crash_matrix.py`` covers termination around transactions
and commits, duplicate events, stale writers, corrupt rows, late completion and
delayed usage reporting. The issue's *Required qualification* asks for four
more, and they are the ones where "we do not know" is the only honest answer:

* **disk failure** -- the journal cannot be read or written at all;
* **provider timeout** -- a call was dispatched and never answered;
* **cancellation races** -- two stops arriving at once;
* **GitHub timeout** -- a delivery request that may or may not have landed.

Each one is a chance for a surface to guess in the confident direction, and the
closing evidence this epic asks for is *zero false completion, zero duplicate
side effects, zero hidden active work*. So these tests are mostly about what
OPai refuses to say.

The honest limit, stated as the matrix states its own: a real external effect
cannot be simulated here. What is proved is that the local record keeps saying
"we do not know" and that nothing downstream converts that into a conclusion.
"""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opaihub import (
    cancellation_lifecycle,
    journal_operations,
    journal_runtime,
    journal_store,
)
from opaihub.cancellation_lifecycle import CancelPhase, CancellationTracker

NOW = "2026-09-08T10:00:00+00:00"
LATER = "2026-09-08T10:05:00+00:00"


def _disk_failure(*_args, **_kwargs):
    """What SQLite raises when the volume is gone, full, or unreadable."""

    raise sqlite3.OperationalError("disk I/O error")


class _FailsOnQuery:
    """A connection that opens cleanly and dies on one statement.

    The volume that disappears mid-query is a different failure from the one
    that will not open, and it reaches a different branch. ``failing_disk``
    only ever produces the second.
    """

    _POISON = "terminal_verdict = 'completed'"

    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, *rest):
        if self._POISON in sql:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return self._connection.execute(sql, *rest)

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _JournalledRun(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.fence = journal_runtime.record_admission(
            self.root,
            task_id="task-1",
            run_id="run-1",
            task="a turn",
            now=NOW,
            surface="gui",
        )

    def failing_disk(self):
        """Every journal open raises, for the duration of the block.

        Both module-level aliases are patched, and that detail is load-bearing
        rather than belt-and-braces. ``journal_operations`` reaches the store
        through ``journal_runtime._store``, while the cancellation comparator
        calls ``journal_store.open_store`` directly -- so patching either one
        alone injects no failure at all for half the callers, and the test then
        passes while proving nothing. That is what the first version of this
        fixture did, and it took a test that expected a failure report and got
        ``None`` to notice.
        """

        stack = contextlib.ExitStack()
        stack.enter_context(
            mock.patch.object(journal_runtime, "open_store", _disk_failure)
        )
        stack.enter_context(
            mock.patch.object(journal_store, "open_store", _disk_failure)
        )
        return stack


class DiskFailureTests(_JournalledRun):
    """The write boundary: the journal is simply not available."""

    def test_unfinished_work_reads_as_unknown_not_as_none(self):
        with self.failing_disk():
            summary = journal_runtime.unterminated_summary(self.root)

        self.assertFalse(summary["available"])
        self.assertEqual(summary["unavailable_reason"], "unreadable")
        # The dangerous answer would be `available: True, unterminated: 0` --
        # byte for byte what a healthy, idle journal reports, handed to a
        # recovery pass at the worst possible moment.
        self.assertEqual(summary["unterminated"], 0)

    def test_an_operation_summary_does_not_claim_zero_unreconciled(self):
        with self.failing_disk():
            summary = journal_operations.operation_summary(self.root)

        self.assertFalse(summary["available"])
        self.assertNotIn(
            "unreconciled",
            summary,
            "an unreadable store must not report a count it never read",
        )

    def test_a_claim_that_could_not_be_written_says_so(self):
        with self.failing_disk():
            claimed = journal_operations.record_claim(
                self.root, "github:pr:abc", now=NOW, run_id="run-1"
            )

        self.assertFalse(claimed)

    def test_the_truth_comes_back_when_the_disk_does(self):
        with self.failing_disk():
            journal_runtime.unterminated_summary(self.root)

        summary = journal_runtime.unterminated_summary(self.root)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["unterminated"], 1)

    def test_a_failed_write_leaves_no_half_operation(self):
        with self.failing_disk():
            journal_operations.record_claim(self.root, "github:pr:abc", now=NOW)

        summary = journal_operations.operation_summary(self.root)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["unreconciled"], 0)


class ProviderTimeoutTests(_JournalledRun):
    """The provider-execution boundary: dispatched, never answered."""

    KEY = "provider:claude:req-9"

    def test_a_dispatched_call_that_never_answered_stays_unreconciled(self):
        journal_operations.record_claim(self.root, self.KEY, now=NOW, run_id="run-1")

        summary = journal_operations.operation_summary(self.root)

        self.assertEqual(summary["unreconciled"], 1)
        self.assertEqual(summary["states"], {journal_operations.STATE_CLAIMED: 1})

    def test_a_retry_is_not_told_it_made_a_fresh_claim(self):
        """The trap this epic is walking towards.

        The legacy idempotency file is still what decides whether an effect may
        be performed, so a wrong answer here is currently harmless. Stage 7
        retires that file, and then this *is* the decision -- a retry after a
        timeout being told "you created this claim" is a second paid call, or a
        second pull request.
        """

        first = journal_operations.record_claim(
            self.root, self.KEY, now=NOW, run_id="run-1"
        )
        retry = journal_operations.record_claim(
            self.root, self.KEY, now=LATER, run_id="run-1"
        )

        self.assertTrue(first, "the first claim created the row")
        self.assertFalse(retry, "the retry created nothing and must not say it did")

    def test_a_retry_never_creates_a_second_operation(self):
        journal_operations.record_claim(self.root, self.KEY, now=NOW, run_id="run-1")
        journal_operations.record_claim(self.root, self.KEY, now=LATER, run_id="run-1")

        store = journal_store.open_store(self.root)
        try:
            rows = store.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
        finally:
            store.close()

        self.assertEqual(rows, 1)

    def test_a_timed_out_call_is_never_downgraded_to_certainty(self):
        """ "The request may have arrived" must not decay into "it did not"."""

        journal_operations.record_claim(self.root, self.KEY, now=NOW, run_id="run-1")

        store = journal_store.open_store(self.root)
        try:
            state = store.execute(
                "SELECT state FROM operations WHERE operation_key = ?", (self.KEY,)
            ).fetchone()[0]
        finally:
            store.close()

        self.assertEqual(state, journal_operations.STATE_CLAIMED)
        self.assertNotEqual(state, "reconciled")

    def test_the_run_is_not_reported_finished_because_the_provider_went_quiet(self):
        summary = journal_runtime.unterminated_summary(self.root)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["unterminated"], 1)


class GithubDeliveryTimeoutTests(_JournalledRun):
    """The delivery boundary: did the pull request get created or not?"""

    KEY = "github:pr:MarcoLadeira/OPai:feat-x"

    def test_the_record_keeps_saying_we_do_not_know(self):
        journal_operations.record_claim(self.root, self.KEY, now=NOW, run_id="run-1")

        store = journal_store.open_store(self.root)
        try:
            row = store.execute(
                "SELECT state, external_ref FROM operations WHERE operation_key = ?",
                (self.KEY,),
            ).fetchone()
        finally:
            store.close()

        self.assertEqual(row[0], journal_operations.STATE_CLAIMED)
        # No external reference: nothing on the far side has been observed, and
        # inventing one would make an unverifiable record look reconcilable.
        self.assertEqual(row[1], "")

    def test_a_later_confirmation_carries_what_names_the_effect(self):
        journal_operations.record_claim(self.root, self.KEY, now=NOW, run_id="run-1")
        journal_operations.record_confirmation(
            self.root,
            self.KEY,
            now=LATER,
            external_ref="https://github.com/MarcoLadeira/OPai/pull/847",
            run_id="run-1",
        )

        store = journal_store.open_store(self.root)
        try:
            row = store.execute(
                "SELECT state, external_ref FROM operations WHERE operation_key = ?",
                (self.KEY,),
            ).fetchone()
        finally:
            store.close()

        self.assertEqual(row[0], journal_operations.STATE_CONFIRMED)
        self.assertIn("/pull/847", row[1])

    def test_a_confirmed_delivery_cannot_be_walked_back_to_a_claim(self):
        """Terminal regression, at the boundary that spends real money."""

        journal_operations.record_claim(self.root, self.KEY, now=NOW, run_id="run-1")
        journal_operations.record_confirmation(
            self.root, self.KEY, now=LATER, external_ref="pr/847", run_id="run-1"
        )
        journal_operations.record_claim(self.root, self.KEY, now=LATER, run_id="run-1")

        store = journal_store.open_store(self.root)
        try:
            state = store.execute(
                "SELECT state FROM operations WHERE operation_key = ?", (self.KEY,)
            ).fetchone()[0]
        finally:
            store.close()

        self.assertEqual(state, journal_operations.STATE_CONFIRMED)

    def test_the_store_raises_rather_than_silently_regressing(self):
        store = journal_store.open_store(self.root)
        self.addCleanup(store.close)
        journal_store.record_operation(
            store,
            operation_key=self.KEY,
            kind="github",
            target_digest="d",
            state="reconciled",
            now=NOW,
        )

        with self.assertRaises(journal_store.JournalStoreError) as caught:
            journal_store.record_operation(
                store,
                operation_key=self.KEY,
                kind="github",
                target_digest="d",
                state="intended",
                now=LATER,
            )

        self.assertIn("cannot go back", str(caught.exception))

    def _write(self, store, state: str, now: str = LATER) -> None:
        journal_store.record_operation(
            store,
            operation_key=self.KEY,
            kind="github",
            target_digest="d",
            state=state,
            now=now,
        )

    def test_uncertain_is_not_a_way_back_down_the_ladder(self):
        """#818 review finding 19: reconciled -> uncertain -> intended passed.

        `uncertain` has no rank, so nothing looked like going backwards.
        """

        store = journal_store.open_store(self.root)
        self.addCleanup(store.close)
        self._write(store, "reconciled", now=NOW)
        self._write(store, "uncertain")

        for earlier in ("intended", "executing", "observed"):
            with self.subTest(state=earlier):
                with self.assertRaises(journal_store.JournalStoreError):
                    self._write(store, earlier)

    def test_uncertainty_is_resolved_by_finding_out_not_by_rerunning(self):
        """Back to executing would mean re-running an effect of unknown outcome."""

        store = journal_store.open_store(self.root)
        self.addCleanup(store.close)
        self._write(store, "executing", now=NOW)
        self._write(store, "uncertain")

        with self.assertRaises(journal_store.JournalStoreError):
            self._write(store, "intended")
        with self.assertRaises(journal_store.JournalStoreError):
            self._write(store, "executing")
        self._write(store, "observed")
        self._write(store, "reconciled")

    def test_an_outcome_can_still_become_uncertain(self):
        """Recording that something became unknowable is never refused."""

        store = journal_store.open_store(self.root)
        self.addCleanup(store.close)
        self._write(store, "reconciled", now=NOW)

        self._write(store, "uncertain")
        self._write(store, "reconciled")


class CancellationRaceTests(_JournalledRun):
    """Two stops at once: they converge, and nothing moves backwards."""

    def tracker(self) -> CancellationTracker:
        return CancellationTracker(self.root, "run-1", journal_run_id="run-1")

    def test_racing_cancellations_converge_on_one_history(self):
        barrier = threading.Barrier(8)
        errors: list[BaseException] = []

        def stop() -> None:
            try:
                barrier.wait(timeout=30)
                self.tracker().request(reason_code="user_requested")
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                errors.append(exc)

        threads = [threading.Thread(target=stop) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(errors, [], "a racing cancellation raised")
        history = self.tracker().history()
        requested = [row for row in history if row["phase"] == CancelPhase.REQUESTED]
        self.assertEqual(
            len(requested), 1, f"eight stops wrote {len(requested)} events"
        )

    def test_a_late_request_cannot_pull_a_terminated_scope_backwards(self):
        tracker = self.tracker()
        tracker.request()
        tracker.acknowledge()
        tracker.mark_terminated()

        tracker.request(reason_code="a_second_stop_click")

        self.assertIs(self.tracker().phase(), CancelPhase.TERMINATED)

    def test_the_phase_reaches_the_canonical_journal(self):
        self.tracker().request(reason_code="user_requested")

        store = journal_store.open_store(self.root)
        try:
            events = [
                event
                for event in journal_store.read_events(store, run_id="run-1")
                if event.get("event_type") == journal_runtime.EVENT_CANCEL_PHASE
            ]
        finally:
            store.close()

        self.assertTrue(events, "the cancellation left no canonical evidence")
        self.assertEqual(events[-1]["payload"]["phase"], CancelPhase.REQUESTED.value)

    def test_a_cancellation_survives_the_journal_being_unavailable(self):
        """Bookkeeping must never be able to refuse a stop."""

        with self.failing_disk():
            phase = self.tracker().request(reason_code="user_requested")

        self.assertIs(phase, CancelPhase.REQUESTED)
        self.assertIs(self.tracker().phase(), CancelPhase.REQUESTED)

    def test_reaching_terminated_is_recorded_as_its_own_phase(self):
        tracker = self.tracker()
        tracker.request()
        tracker.acknowledge()
        tracker.begin_draining()
        tracker.force_terminate()
        tracker.mark_terminated()

        phases = [row["phase"] for row in tracker.history()]

        self.assertEqual(
            phases,
            [
                CancelPhase.REQUESTED,
                CancelPhase.ACKNOWLEDGED,
                CancelPhase.DRAINING,
                CancelPhase.FORCE_TERMINATING,
                CancelPhase.TERMINATED,
            ],
        )


class CancellationDualReadTests(_JournalledRun):
    """Stage 2's dual read for the one record that had none.

    ``cancellation_lifecycle`` owns the phase ladder -- "cancellation and
    teardown evidence" in this epic's canonical-record list -- and it was the
    single durable writer in the tree with no #613 classification at all. The
    inventory ratchet has been failing on ``main`` because of it.

    Its shape is unusual and the comparator reflects that: there is no legacy
    snapshot to compare against a shadow, because its authority is *already* a
    sequenced ``run_journal``. The two things that can actually disagree are
    that phase log and the cancel-phase events mirrored into the canonical
    journal, and the mirror is best-effort by design -- a cancellation must
    never be refused by its own bookkeeping -- so drift is a real possibility
    rather than a hypothetical one.
    """

    def tracker(self) -> CancellationTracker:
        return CancellationTracker(self.root, "run-1", journal_run_id="run-1")

    def report(self, scope: str = "run-1", run_id: str = "run-1"):
        return cancellation_lifecycle.cancellation_contradiction_report(
            self.root, scope, journal_run_id=run_id
        )

    def test_agreement_reports_nothing(self):
        tracker = self.tracker()
        tracker.request()
        tracker.acknowledge()

        self.assertIsNone(self.report())

    def test_a_dropped_mirror_is_reported_not_smoothed_over(self):
        """The comparator has to be able to fail, or it decides nothing."""

        tracker = self.tracker()
        tracker.request()
        with mock.patch.object(
            journal_runtime, "record_cancellation_phase", lambda *a, **k: False
        ):
            tracker.acknowledge()

        report = self.report()

        self.assertIsNotNone(report, "a lost mirrored phase went unnoticed")
        self.assertEqual(report["authoritative"], ["requested", "acknowledged"])
        self.assertEqual(report["mirrored"], ["requested"])
        self.assertTrue(report["mirror_is_behind"])

    def test_an_unreadable_journal_is_not_reported_as_agreement(self):
        self.tracker().request()

        with self.failing_disk():
            report = self.report()

        self.assertIsNotNone(report)
        self.assertFalse(report["comparable"])
        self.assertEqual(report["reason"], "OperationalError")

    def test_a_scope_that_names_no_run_has_nothing_to_disagree_with(self):
        """Scope ids are namespaced per caller; only some name a journalled run."""

        self.assertIsNone(self.report(scope="aci-1234", run_id=""))


class UnevidencedCompletionTests(_JournalledRun):
    """#818 AC6: `completed` is supposed to be impossible without evidence.

    It is not. The store records whatever verdict a caller hands it, so a run
    can be admitted and immediately terminated as ``completed`` with no
    verification event, no manifest and no cost -- measured, not inferred.

    Refusing the write would be the wrong fix and these tests pin why. The
    layer that *can* judge a completion is ``opaihub.completion``, which has
    the answer, the diff and the policy in front of it; a journal that started
    overruling verdicts would be a second opinion on the one question this
    epic exists to give a single answer to. And refusing to record a terminal
    state would leave the run reading as unfinished, which is a worse lie than
    an unevidenced completion.

    So the honest intermediate step is to count them. Enforcement is Stage 5's
    and it needs this number to be zero first.
    """

    def complete(self, run_id: str, task_id: str) -> int | None:
        fence = journal_runtime.record_admission(
            self.root,
            task_id=task_id,
            run_id=run_id,
            task="a turn",
            now=NOW,
            surface="cli",
        )
        return fence

    def terminate(self, run_id: str, fence: int | None) -> None:
        journal_runtime.record_terminal(
            self.root,
            run_id=run_id,
            event_type="run.completed",
            verdict="completed",
            reason="",
            now=LATER,
            fence=fence,
        )

    def test_a_verdict_with_nothing_behind_it_is_counted(self):
        fence = self.complete("run-2", "task-2")
        self.terminate("run-2", fence)

        report = journal_runtime.unevidenced_completions(self.root)

        self.assertTrue(report["available"])
        self.assertEqual(report["unevidenced"], 1)
        self.assertIn("run-2", report["run_ids"])

    def test_a_verified_run_is_not_counted(self):
        fence = self.complete("run-2", "task-2")
        journal_runtime.record_verification(
            self.root,
            run_id="run-2",
            verdict="verified",
            policy_digest="policy",
            manifest_digest="manifest",
            now=NOW,
            fence=fence,
        )
        self.terminate("run-2", fence)

        report = journal_runtime.unevidenced_completions(self.root)

        self.assertEqual(report["completed"], 1)
        self.assertEqual(report["unevidenced"], 0)

    def test_a_run_that_cost_something_is_not_counted(self):
        """Spending money is a trace that something actually happened."""

        fence = self.complete("run-2", "task-2")
        journal_runtime.record_run_cost(
            self.root,
            run_id="run-2",
            operation_key="run-2:claude",
            amount_usd=0.0421,
            measurement_kind="actual",
            now=NOW,
            fence=fence,
        )
        self.terminate("run-2", fence)

        report = journal_runtime.unevidenced_completions(self.root)

        self.assertEqual(report["unevidenced"], 0)

    def test_runs_that_did_not_complete_are_not_counted(self):
        fence = self.complete("run-2", "task-2")
        journal_runtime.record_terminal(
            self.root,
            run_id="run-2",
            event_type="run.failed",
            verdict="failed",
            reason="provider_error",
            now=LATER,
            fence=fence,
        )

        report = journal_runtime.unevidenced_completions(self.root)

        self.assertEqual(report["completed"], 0)
        self.assertEqual(report["unevidenced"], 0)

    def test_an_unreadable_journal_reports_unknown_not_zero(self):
        """Zero unevidenced completions is reassuring. It must be earned."""

        with self.failing_disk():
            report = journal_runtime.unevidenced_completions(self.root)

        self.assertFalse(report["available"])
        self.assertEqual(report["unavailable_reason"], "unreadable")
        self.assertEqual(report["unevidenced"], 0)

    def test_a_store_that_opens_and_then_fails_is_also_unknown(self):
        """The other half of a disk failure, and the half nothing reached.

        ``failing_disk`` makes the *open* raise, which takes the
        ``store is None`` path. A volume that dies mid-query opens fine and
        then throws, and that branch had no test at all -- teeth-testing found
        it by sabotaging a line no test could reach.
        """

        fence = self.complete("run-2", "task-2")
        self.terminate("run-2", fence)

        real_open = journal_store.open_store

        def opens_then_fails(*args, **kwargs):
            # A proxy rather than monkeypatching the method: sqlite3.Connection
            # is a C type and `connection.execute = ...` raises AttributeError,
            # which is a failure of the test rather than of the code.
            return _FailsOnQuery(real_open(*args, **kwargs))

        with mock.patch.object(journal_runtime, "open_store", opens_then_fails):
            report = journal_runtime.unevidenced_completions(self.root)

        self.assertFalse(report["available"])
        self.assertEqual(report["unavailable_reason"], "unreadable")
        self.assertEqual(report["unevidenced"], 0)

    def test_cost_alone_does_not_satisfy_the_criterion_ac6_states(self):
        """The number that flatters, and the number that does not.

        AC6 names objective, verification and delivery evidence. It does not
        name cost -- and every real turn records one. Measured on this repo's
        own journal: 21 completed runs, 0 unevidenced, 20 with no verification.
        Reporting only the first would be a confident answer with the
        inconvenient half left out, which is the failure this epic is about.
        """

        fence = self.complete("run-2", "task-2")
        journal_runtime.record_run_cost(
            self.root,
            run_id="run-2",
            operation_key="run-2:claude",
            amount_usd=0.05,
            measurement_kind="actual",
            now=NOW,
            fence=fence,
        )
        self.terminate("run-2", fence)

        report = journal_runtime.unevidenced_completions(self.root)

        self.assertEqual(report["unevidenced"], 0, "cost is a trace of something")
        self.assertEqual(
            report["without_verification"],
            1,
            "a paid run that was never verified still fails AC6",
        )

    def test_a_verified_run_satisfies_both_numbers(self):
        fence = self.complete("run-2", "task-2")
        journal_runtime.record_verification(
            self.root,
            run_id="run-2",
            verdict="verified",
            policy_digest="policy",
            manifest_digest="manifest",
            now=NOW,
            fence=fence,
        )
        self.terminate("run-2", fence)

        report = journal_runtime.unevidenced_completions(self.root)

        self.assertEqual(report["unevidenced"], 0)
        self.assertEqual(report["without_verification"], 0)

    def test_doctor_reports_the_criterion_number_too(self):
        from opai import cli

        fence = self.complete("run-2", "task-2")
        journal_runtime.record_run_cost(
            self.root,
            run_id="run-2",
            operation_key="run-2:claude",
            amount_usd=0.05,
            measurement_kind="actual",
            now=NOW,
            fence=fence,
        )
        self.terminate("run-2", fence)

        facts = cli._journal_migration(self.root)

        self.assertEqual(facts["completed_runs_without_evidence"], 0)
        self.assertEqual(facts["completed_runs_without_verification"], 1)

    def test_the_id_list_is_bounded(self):
        """A report is for acting on; a thousand ids is a dump."""

        for index in range(60):
            fence = self.complete(f"bare-{index:03d}", f"task-{index:03d}")
            self.terminate(f"bare-{index:03d}", fence)

        report = journal_runtime.unevidenced_completions(self.root)

        self.assertEqual(report["unevidenced"], 60)
        self.assertEqual(len(report["run_ids"]), 50)

    def test_doctor_actually_asks(self):
        """The seventh piece of machinery nothing called would be this one."""

        from opai import cli

        fence = self.complete("run-2", "task-2")
        self.terminate("run-2", fence)

        facts = cli._journal_migration(self.root)

        self.assertTrue(facts["completed_runs_known"])
        self.assertEqual(facts["completed_runs"], 1)
        self.assertEqual(facts["completed_runs_without_evidence"], 1)


class UnconfirmedCancellationTests(_JournalledRun):
    """#818 AC5: `cancelled` is impossible while owned work is still alive.

    It is not. Measured with two real processes: a process holding no fence
    for a run can write `cancelled` for it while the owning process is
    demonstrably still running, and the store accepts it.

    And on this repository's own journal all six cancelled runs carry no
    cancellation-phase evidence whatsoever -- every one says the run stopped,
    and not one records that anything did.

    Counting rather than refusing, and here the reason is stronger than
    consistency with AC6: a Stop that OPai declined to record would be a Stop
    the user pressed and did not get. Refusing would trade a reporting fault
    for a blocking one, which is never the right trade.
    """

    def cancel(self, run_id: str, fence: int | None) -> None:
        journal_runtime.record_terminal(
            self.root,
            run_id=run_id,
            event_type=journal_runtime.EVENT_CANCELLED,
            verdict="cancelled",
            reason="stop clicked",
            now=LATER,
            fence=fence,
        )

    def admit(self, run_id: str) -> int | None:
        return journal_runtime.record_admission(
            self.root,
            task_id=f"task-{run_id}",
            run_id=run_id,
            task="a turn",
            now=NOW,
            surface="gui",
        )

    def test_a_cancellation_with_no_phase_evidence_is_counted(self):
        self.cancel("run-2", self.admit("run-2"))

        report = journal_runtime.unconfirmed_cancellations(self.root)

        self.assertTrue(report["available"])
        self.assertEqual(report["cancelled"], 1)
        self.assertEqual(report["unconfirmed"], 1)
        self.assertIn("run-2", report["run_ids"])

    def test_reaching_terminated_is_what_counts_as_confirmation(self):
        fence = self.admit("run-2")
        tracker = CancellationTracker(self.root, "run-2", journal_run_id="run-2")
        tracker.request()
        tracker.acknowledge()
        tracker.mark_terminated()
        self.cancel("run-2", fence)

        report = journal_runtime.unconfirmed_cancellations(self.root)

        self.assertEqual(report["cancelled"], 1)
        self.assertEqual(report["unconfirmed"], 0)

    def test_asking_to_stop_is_not_evidence_that_it_stopped(self):
        """`requested` is the phase that means nobody has confirmed anything."""

        fence = self.admit("run-2")
        CancellationTracker(self.root, "run-2", journal_run_id="run-2").request()
        self.cancel("run-2", fence)

        report = journal_runtime.unconfirmed_cancellations(self.root)

        self.assertEqual(report["unconfirmed"], 1)

    def test_a_completed_run_is_not_a_cancellation(self):
        fence = self.admit("run-2")
        journal_runtime.record_terminal(
            self.root,
            run_id="run-2",
            event_type=journal_runtime.EVENT_FINISHED,
            verdict="completed",
            reason="",
            now=LATER,
            fence=fence,
        )

        report = journal_runtime.unconfirmed_cancellations(self.root)

        self.assertEqual(report["cancelled"], 0)
        self.assertEqual(report["unconfirmed"], 0)

    def test_an_unreadable_journal_reports_unknown_not_zero(self):
        with self.failing_disk():
            report = journal_runtime.unconfirmed_cancellations(self.root)

        self.assertFalse(report["available"])
        self.assertEqual(report["unavailable_reason"], "unreadable")
        self.assertEqual(report["unconfirmed"], 0)

    def test_recording_a_stop_is_never_refused(self):
        """The property that matters more than the count.

        A Stop OPai declined to record is a Stop the user pressed and did not
        get. Whatever this report says, the write goes through.
        """

        fence = self.admit("run-2")

        self.assertTrue(
            journal_runtime.record_terminal(
                self.root,
                run_id="run-2",
                event_type=journal_runtime.EVENT_CANCELLED,
                verdict="cancelled",
                reason="stop clicked",
                now=LATER,
                fence=fence,
            )
        )

    def test_doctor_actually_asks(self):
        from opai import cli

        self.cancel("run-2", self.admit("run-2"))

        facts = cli._journal_migration(self.root)

        self.assertTrue(facts["cancelled_runs_known"])
        self.assertEqual(facts["cancelled_runs"], 1)
        self.assertEqual(facts["cancelled_runs_unconfirmed"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
