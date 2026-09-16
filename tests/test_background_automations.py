"""Background coding-workflow automations (#176): durable, gated, cancellable."""

from __future__ import annotations

import json
import sys
import subprocess
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vestahub.background_runs import (
    AutomationRun,
    BackgroundRunner,
    RUN_STATUSES,
    _RUN_STATE_FOR_LEGACY_STATUS,
    enqueue_automation,
    list_automation_schedules,
    list_runs,
    load_run,
    read_notifications,
    recover_interrupted_runs,
    runs_owned_by_a_live_process,
    request_cancel,
    schedule_automation,
    tick_automations,
)
from vestahub.journal_store import journal_path, open_store
from vestahub.run_state import RunState
from vestahub.run_result import RunResult


def _completed_executor(project_root, run, cancel_event):
    # Mirrors what the real executor returns. Background runs execute through
    # handle_gui_message, which since #618 always projects a canonical
    # RunResult and emits run_state alongside it. A bare {"status": "answered"}
    # modelled a shape production no longer produces, and the difference is not
    # cosmetic: that payload now imports as needs_attention, because a legacy
    # status alone records that the provider replied and says nothing about
    # whether the work was verified. See
    # test_a_legacy_only_payload_cannot_complete_a_background_run.
    #
    # The RunResult is built through the real contract rather than hand-shaped:
    # _canonical_result validates before trusting, and a stub payload is
    # indistinguishable from a corrupted record, so it degrades to
    # needs_attention exactly as it should.
    from vestahub.run_result import RunResult

    canonical = RunResult.from_payload(
        state="completed",
        reason_detail="background objective met",
        final_transition_at="2026-08-11T12:00:00+00:00",
        mutating=False,
        verification={"applicable": False, "verdict": "not_applicable"},
        delivery={
            "applicable": True,
            "verdict": "delivered",
            "record_ref": {"kind": "turn_record", "id": "bg"},
        },
        economics={
            "integrity": "reconciled",
            "record_ref": {"kind": "ledger_event", "id": "bg"},
        },
    )
    return {
        "status": "answered",
        "run_state": "completed",
        "run_result": canonical.to_dict(),
        "changed_files": ["app.py"],
    }


class EnqueueTests(unittest.TestCase):
    def test_every_background_compatibility_status_has_a_canonical_mapping(self):
        # A new status without an explicit mapping would be a new, hidden state
        # machine. Keep the compatibility vocabulary closed over RunState.
        self.assertEqual(set(_RUN_STATE_FOR_LEGACY_STATUS), RUN_STATUSES)
        self.assertTrue(
            all(
                isinstance(state, RunState)
                for state in _RUN_STATE_FOR_LEGACY_STATUS.values()
            )
        )

    def test_enqueue_rejects_unknown_workflow_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                enqueue_automation(Path(tmp), "delete_everything", "task")

    def test_enqueue_persists_a_durable_queued_run_with_notification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "fix the flaky login test")

            restored = load_run(root, run.run_id)
            self.assertEqual(restored.status, "queued")
            self.assertEqual(restored.run_state, RunState.QUEUED.value)
            self.assertEqual(restored.reason_code, "queued")
            self.assertEqual(
                [item["state"] for item in restored.state_history], ["queued"]
            )
            self.assertEqual(restored.workflow_id, "bug_fix")
            self.assertEqual(restored.owner, "user")
            self.assertFalse(restored.allow_cloud)
            notes = read_notifications(root)
            self.assertEqual(notes[-1]["run_id"], run.run_id)
            self.assertEqual(notes[-1]["status"], "queued")

    def test_cloud_escalation_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                enqueue_automation(root, "bug_fix", "task", allow_cloud=True)
            run = enqueue_automation(
                root, "bug_fix", "task", allow_cloud=True, cloud_confirmed=True
            )
            self.assertTrue(load_run(root, run.run_id).allow_cloud)

    def test_run_files_and_notifications_never_store_raw_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
            run = enqueue_automation(root, "bug_fix", f"rotate token={secret}")
            raw_run = json.dumps(load_run(root, run.run_id).to_dict())
            raw_notes = json.dumps(read_notifications(root))
            events = (root / ".vestahub" / "agent" / "events.jsonl").read_text(
                encoding="utf-8"
            )

        self.assertNotIn(secret, raw_run)
        self.assertNotIn(secret, raw_notes)
        self.assertNotIn(secret, events)

    def test_duplicate_run_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            enqueue_automation(root, "bug_fix", "task", run_id="run-1")
            with self.assertRaises(FileExistsError):
                enqueue_automation(root, "bug_fix", "task", run_id="run-1")


class CancellationTests(unittest.TestCase):
    def test_cancelling_a_queued_run_prevents_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            cancelled = request_cancel(root, run.run_id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(cancelled.run_state, RunState.CANCELLED.value)
            self.assertEqual(cancelled.reason_code, "cancelled_before_start")
            self.assertEqual(cancelled.result["cancellation"]["phase"], "terminated")
            self.assertEqual(
                cancelled.result["cancellation"]["scope_id"],
                f"background-{run.run_id}",
            )
            self.assertEqual(cancelled.cancellation["phase"], "terminated")
            self.assertEqual(
                load_run(root, run.run_id).cancellation["phase"], "terminated"
            )

            runner = BackgroundRunner(root, executor=_completed_executor)
            with self.assertRaises(ValueError):
                runner.run_now(run.run_id)

    def test_cancelling_a_running_run_signals_the_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            started = threading.Event()
            release = threading.Event()

            def waiting_executor(project_root, current, cancel_event):
                started.set()
                cancel_event.wait(timeout=10)
                release.wait(timeout=10)
                return {"status": "cancelled"}

            runner = BackgroundRunner(root, executor=waiting_executor)
            thread = runner.start(run.run_id)
            self.assertTrue(started.wait(timeout=10))
            requested = request_cancel(root, run.run_id)
            self.assertEqual(requested.run_state, RunState.CANCEL_REQUESTED.value)
            self.assertEqual(requested.cancellation["phase"], "requested")
            self.assertEqual(
                load_run(root, run.run_id).cancellation["phase"], "requested"
            )
            release.set()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

            final = load_run(root, run.run_id)
            self.assertEqual(final.status, "cancelled")
            self.assertTrue(final.cancel_requested)
            self.assertEqual(
                final.result["background_cancellation"]["phase"], "terminated"
            )

    def test_executor_exception_after_cancel_needs_attention_not_claimed_cancel(self):
        # #666: an executor that *raises* while a stop is in flight proves
        # nothing about the in-flight process tree. The run must end in
        # needs_attention with the evidence, and the journal must stop at the
        # acknowledgement the worker actually observed — never a fabricated
        # "terminated".
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            started = threading.Event()

            def cancelled_executor(_root, _run, cancel_event):
                started.set()
                cancel_event.wait(timeout=10)
                raise RuntimeError("executor interrupted by cancellation")

            runner = BackgroundRunner(root, executor=cancelled_executor)
            thread = runner.start(run.run_id)
            self.assertTrue(started.wait(timeout=10))
            request_cancel(root, run.run_id)
            thread.join(timeout=10)

            final = load_run(root, run.run_id)

        self.assertFalse(thread.is_alive())
        self.assertEqual(final.run_state, RunState.NEEDS_ATTENTION.value)
        self.assertEqual(final.reason_code, "cancellation_unconfirmed")
        evidence = final.result["background_cancellation"]
        self.assertEqual(evidence["phase"], "acknowledged")
        phases = [entry["phase"] for entry in evidence["history"]]
        self.assertEqual(phases, ["requested", "acknowledged"])
        self.assertIsNone(evidence["metrics"]["terminated_at"])

    def test_confirmed_cancel_records_each_phase_as_it_actually_happens(self):
        # #666: the phases must correspond to real events — the request is
        # durably written when the user asks, the acknowledgement when the
        # worker observes it, and termination only after the executor has
        # returned (controllable work proven stopped).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            started = threading.Event()
            release = threading.Event()

            def waiting_executor(_root, _run, cancel_event):
                started.set()
                cancel_event.wait(timeout=10)
                return {"status": "cancelled"}

            runner = BackgroundRunner(root, executor=waiting_executor)
            thread = runner.start(run.run_id)
            self.assertTrue(started.wait(timeout=10))
            request_cancel(root, run.run_id)
            requested_at = load_run(root, run.run_id).cancellation["metrics"][
                "requested_at"
            ]
            release.set()
            thread.join(timeout=10)

            final = load_run(root, run.run_id)

        self.assertEqual(final.run_state, RunState.CANCELLED.value)
        evidence = final.result["background_cancellation"]
        self.assertEqual(evidence["phase"], "terminated")
        self.assertEqual(
            [entry["phase"] for entry in evidence["history"]],
            ["requested", "acknowledged", "terminated"],
        )
        metrics = evidence["metrics"]
        self.assertEqual(metrics["requested_at"], requested_at)
        self.assertIsNotNone(metrics["acknowledgement_latency_seconds"])
        self.assertIsNotNone(metrics["hard_stop_latency_seconds"])
        # The persisted run record carries the terminal evidence too, so it
        # stays self-describing if the journal is later unreadable.
        self.assertEqual(final.cancellation["phase"], "terminated")

    def test_unproven_provider_teardown_keeps_the_background_journal_honest(self):
        # #666: when the provider's own journal stops short of "terminated",
        # the background journal must not claim what it cannot show — the run
        # ends in needs_attention with both evidences intact.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            started = threading.Event()

            def stubborn_executor(_root, _run, cancel_event):
                started.set()
                cancel_event.wait(timeout=10)
                return {
                    "status": "cancelled",
                    "cancellation": {
                        "scope_id": "account-stubborn",
                        "phase": "force_terminating",
                        "history": [
                            {"phase": "requested", "at": "t0", "reason_code": "user"},
                            {
                                "phase": "acknowledged",
                                "at": "t1",
                                "reason_code": "loop",
                            },
                            {
                                "phase": "force_terminating",
                                "at": "t2",
                                "reason_code": "kill",
                            },
                        ],
                        "metrics": {"terminated_at": None},
                    },
                }

            runner = BackgroundRunner(root, executor=stubborn_executor)
            thread = runner.start(run.run_id)
            self.assertTrue(started.wait(timeout=10))
            request_cancel(root, run.run_id)
            thread.join(timeout=10)

            final = load_run(root, run.run_id)

        self.assertEqual(final.run_state, RunState.NEEDS_ATTENTION.value)
        self.assertEqual(final.reason_code, "cancellation_unconfirmed")
        evidence = final.result["background_cancellation"]
        # The background journal mirrors the provider's real depth — including
        # the force kill — but never claims a termination nobody observed.
        self.assertEqual(evidence["phase"], "force_terminating")
        phases = [entry["phase"] for entry in evidence["history"]]
        self.assertNotIn("terminated", phases)

    def test_cancelling_a_finished_run_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            BackgroundRunner(root, executor=_completed_executor).run_now(run.run_id)
            final = request_cancel(root, run.run_id)
            self.assertEqual(final.status, "completed")


class RunnerTests(unittest.TestCase):
    def test_a_successful_run_completes_with_bounded_result_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "feature", "add the toggle")
            final = BackgroundRunner(root, executor=_completed_executor).run_now(
                run.run_id
            )
            self.assertEqual(final.status, "completed")
            self.assertEqual(final.run_state, RunState.COMPLETED.value)
            # `canonical_completed`, not `background_completed`: the run now
            # resolves through the canonical RunResult rather than the legacy
            # status branch, and the reason code says which authority answered.
            # That distinction is the point of #618 -- if this ever reads
            # `background_completed` again, a legacy string decided the outcome.
            self.assertEqual(final.reason_code, "canonical_completed")
            self.assertEqual(
                [item["state"] for item in final.state_history],
                ["queued", "preparing", "running", "completed"],
            )
            self.assertEqual(final.result["changed_files"], ["app.py"])
            self.assertEqual(final.result["run_state"], RunState.COMPLETED.value)
            self.assertEqual(final.result["reason_code"], "canonical_completed")
            statuses = [note["status"] for note in read_notifications(root)]
            self.assertEqual(statuses, ["queued", "running", "completed"])
            self.assertEqual(
                read_notifications(root)[-1]["run_state"], RunState.COMPLETED.value
            )

    def test_partial_and_timeout_keep_their_canonical_terminal_meaning(self):
        for provider_status, expected in (
            ("partial", RunState.PARTIAL),
            ("timeout", RunState.TIMEOUT),
        ):
            with (
                self.subTest(provider_status=provider_status),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                run = enqueue_automation(root, "bug_fix", "task")
                final = BackgroundRunner(
                    root,
                    executor=lambda _root, _run, _cancel: {"status": provider_status},
                ).run_now(run.run_id)

                self.assertEqual(final.status, expected.value)
                self.assertEqual(final.run_state, expected.value)
                self.assertEqual(final.result["run_state"], expected.value)

    def test_pipeline_canonical_verdict_beats_legacy_answer_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            final = BackgroundRunner(
                root,
                executor=lambda _root, _run, _cancel: {
                    "status": "answered",
                    "run_state": "partial",
                    "completion_verdict": {
                        "verdict": "partial",
                        "reason_code": "change_not_verified",
                    },
                },
            ).run_now(run.run_id)

            self.assertEqual(final.status, "partial")
            self.assertEqual(final.run_state, RunState.PARTIAL.value)
            self.assertEqual(final.reason_code, "change_not_verified")

    def test_run_result_beats_conflicting_verdict_and_legacy_status(self):
        canonical = RunResult.from_payload(
            state="partial",
            reason_detail="Canonical verification remained incomplete.",
            final_transition_at="2026-08-11T12:00:00Z",
        ).to_dict()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            final = BackgroundRunner(
                root,
                executor=lambda _root, _run, _cancel: {
                    "status": "answered",
                    "completion_verdict": {"verdict": "completed"},
                    "run_result": canonical,
                },
            ).run_now(run.run_id)

        self.assertEqual(final.run_state, RunState.PARTIAL.value)
        self.assertEqual(final.result["run_result"]["lifecycle"]["state"], "partial")

    def test_invalid_explicit_run_result_cannot_fall_back_to_answered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            final = BackgroundRunner(
                root,
                executor=lambda _root, _run, _cancel: {
                    "status": "answered",
                    "run_result": {"schema_version": 1},
                },
            ).run_now(run.run_id)

        self.assertEqual(final.run_state, RunState.NEEDS_ATTENTION.value)
        self.assertEqual(
            final.result["run_result"]["lifecycle"]["state"], "needs_attention"
        )

    def test_stale_worker_cannot_resurrect_a_run_cancelled_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            cancelled = request_cancel(root, run.run_id)
            calls = []

            def executor(_root, _run, _cancel_event):
                calls.append(True)
                return {"status": "answered"}

            # The stale object is the one a thread received before cancellation.
            BackgroundRunner(root, executor=executor)._execute(run)

            restored = load_run(root, run.run_id)
            self.assertEqual(calls, [])
            self.assertEqual(restored, cancelled)

    def test_confirmation_needing_results_block_and_are_never_auto_approved(self):
        for status in (
            "needs_auto_confirmation",
            "needs_command_approval",
            "needs_edit_approval",
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                run = enqueue_automation(root, "bug_fix", "task")

                def gated_executor(project_root, current, cancel_event):
                    return {"status": status, "cloudStarted": False}

                final = BackgroundRunner(root, executor=gated_executor).run_now(
                    run.run_id
                )
                self.assertEqual(final.status, "blocked")
                self.assertEqual(final.run_state, RunState.BLOCKED.value)
                self.assertIn("confirmation", final.message.lower())

    def test_executor_exceptions_fail_closed_with_redacted_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")

            def broken_executor(project_root, current, cancel_event):
                raise RuntimeError("boom token=sk-abcdefghijklmnopqrst")

            final = BackgroundRunner(root, executor=broken_executor).run_now(run.run_id)
            self.assertEqual(final.status, "failed")
            self.assertNotIn("sk-abcdefghijklmnopqrst", final.message)

    def test_concurrency_stays_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = enqueue_automation(root, "bug_fix", "task one")
            second = enqueue_automation(root, "bug_fix", "task two")
            release = threading.Event()
            started = threading.Event()

            def slow_executor(project_root, current, cancel_event):
                started.set()
                release.wait(timeout=10)
                return {"status": "answered"}

            runner = BackgroundRunner(root, executor=slow_executor, max_concurrent=1)
            thread = runner.start(first.run_id)
            self.assertTrue(started.wait(timeout=10))
            with self.assertRaises(RuntimeError):
                runner.start(second.run_id)
            release.set()
            thread.join(timeout=10)


def _really_dead_pid() -> int:
    """A process id that is genuinely not running.

    Spawned and reaped rather than guessed. A made-up high number is a
    coin flip -- if the machine happens to be using it, the lease reads
    ``owner_unverified`` and the test passes or fails for a reason that has
    nothing to do with the code.
    """

    child = subprocess.Popen(  # nosec B603 - fixed argv, does nothing
        [sys.executable, "-c", ""],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child.wait(timeout=30)
    return child.pid


def _simulate_owner_death(root: Path, run_id: str) -> None:
    """Make the run's journal lease name a process that is really gone.

    Editing the run *file* to say "running" is not a crash: #818 gave the
    lease a process identity, and until this exists the journal correctly
    reports that the test process itself still owns the run. Which it does --
    a fixture that only rewrites the file is simulating a lie, not a crash.
    """

    from vestahub.call_reconciliation import pid_is_running
    from vestahub.journal_store import open_store

    dead = _really_dead_pid()
    if pid_is_running(dead) is not False:  # pragma: no cover - pid was reused
        raise unittest.SkipTest("the reaped pid was immediately reused")
    store = open_store(root)
    try:
        store.execute(
            "UPDATE leases SET owner_pid = ?, owner_boot = 'a-dead-vesta'"
            " WHERE run_id = ?",
            (dead, run_id),
        )
    finally:
        store.close()


class DurabilityTests(unittest.TestCase):
    def test_orphaned_running_runs_are_recovered_as_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            # Simulate a crash: the run file says running, but no session owns it.
            path = root / ".vestahub" / "agent" / "background" / "runs"
            data = json.loads((path / f"{run.run_id}.json").read_text("utf-8"))
            data["status"] = "running"
            (path / f"{run.run_id}.json").write_text(json.dumps(data), encoding="utf-8")
            _simulate_owner_death(root, run.run_id)

            recovered = recover_interrupted_runs(root)
            self.assertEqual([item.run_id for item in recovered], [run.run_id])
            self.assertEqual(load_run(root, run.run_id).status, "interrupted")
            restored = load_run(root, run.run_id)
            self.assertEqual(restored.run_state, RunState.FAILED.value)
            self.assertEqual(restored.reason_code, "interrupted")

    def test_a_crash_mid_teardown_is_recovered_as_needs_attention_not_failed(self):
        """#614: a crash while CANCEL_REQUESTED must not be filed as a bare
        "failed". Vesta asked to stop the run and never observed whether that
        stop finished, so claiming either "cancelled" (a stop nobody saw) or
        plain "failed" (silently dropping the stop request, and inviting a
        naive retry to overlap the unreconciled attempt) is dishonest. Only
        NEEDS_ATTENTION with "cancellation_unconfirmed" says what Vesta
        actually knows.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            # Simulate a crash mid-teardown: cancellation was requested but
            # the owning session died before it could be confirmed. The
            # legacy status for CANCEL_REQUESTED is "running" (nothing has
            # observed it stop yet), which is exactly what the orphan sweep
            # scans for.
            path = root / ".vestahub" / "agent" / "background" / "runs"
            record_path = path / f"{run.run_id}.json"
            data = json.loads(record_path.read_text("utf-8"))
            data["status"] = "running"
            data["run_state"] = RunState.CANCEL_REQUESTED.value
            data["cancel_requested"] = True
            record_path.write_text(json.dumps(data), encoding="utf-8")
            _simulate_owner_death(root, run.run_id)

            recovered = recover_interrupted_runs(root)
            self.assertEqual([item.run_id for item in recovered], [run.run_id])

            restored = load_run(root, run.run_id)
            self.assertEqual(restored.run_state, RunState.NEEDS_ATTENTION.value)
            self.assertEqual(restored.reason_code, "cancellation_unconfirmed")
            self.assertNotEqual(restored.run_state, RunState.FAILED.value)
            self.assertNotEqual(restored.run_state, RunState.CANCELLED.value)
            self.assertIn("cancellation", restored.result)

    def test_legacy_record_without_run_state_is_migrated_from_its_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            path = (
                root
                / ".vestahub"
                / "agent"
                / "background"
                / "runs"
                / f"{run.run_id}.json"
            )
            data = json.loads(path.read_text("utf-8"))
            data.pop("run_state")
            data.pop("reason_code")
            data.pop("state_history")
            data["status"] = "interrupted"
            path.write_text(json.dumps(data), encoding="utf-8")

            restored = load_run(root, run.run_id)
            self.assertEqual(restored.status, "interrupted")
            self.assertEqual(restored.run_state, RunState.FAILED.value)
            self.assertEqual(restored.reason_code, "interrupted")
            self.assertEqual(restored.state_history[-1]["state"], "failed")

    def test_active_runs_are_not_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = enqueue_automation(root, "bug_fix", "task")
            path = root / ".vestahub" / "agent" / "background" / "runs"
            data = json.loads((path / f"{run.run_id}.json").read_text("utf-8"))
            data["status"] = "running"
            (path / f"{run.run_id}.json").write_text(json.dumps(data), encoding="utf-8")
            recovered = recover_interrupted_runs(root, active_run_ids=(run.run_id,))
            self.assertEqual(recovered, [])
            self.assertEqual(load_run(root, run.run_id).status, "running")


class SchedulingTests(unittest.TestCase):
    def test_schedule_validates_workflow_cadence_and_cloud_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                schedule_automation(root, "nope", "task", cadence="daily")
            with self.assertRaises(ValueError):
                schedule_automation(root, "bug_fix", "task", cadence="fortnightly")
            with self.assertRaises(ValueError):
                schedule_automation(
                    root, "bug_fix", "task", cadence="daily", allow_cloud=True
                )
            schedule = schedule_automation(root, "bug_fix", "task", cadence="daily")
            self.assertEqual(schedule["cadence"], "daily")
            self.assertEqual(len(list_automation_schedules(root)), 1)

    def test_tick_enqueues_once_per_cadence_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schedule_automation(root, "bug_fix", "nightly sweep", cadence="daily")
            moment = datetime(2026, 7, 5, 8, 0, tzinfo=timezone.utc)

            first = tick_automations(root, now=moment)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0].workflow_id, "bug_fix")

            again = tick_automations(root, now=moment + timedelta(hours=1))
            self.assertEqual(again, [])

            next_day = tick_automations(root, now=moment + timedelta(days=1))
            self.assertEqual(len(next_day), 1)
            self.assertEqual(len(list_runs(root)), 2)

    def test_manual_cadence_never_ticks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schedule_automation(root, "bug_fix", "on demand", cadence="manual")
            self.assertEqual(tick_automations(root), [])


class CliTests(unittest.TestCase):
    def test_automation_cli_enqueues_lists_and_cancels(self):
        import contextlib
        import io

        from vestahub.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "--project",
                        tmp,
                        "automation",
                        "enqueue",
                        "bug_fix",
                        "--task",
                        "fix the login test",
                    ]
                )
            self.assertEqual(code, 0)
            run_id = json.loads(out.getvalue())["run_id"]

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["--project", tmp, "automation", "list"]), 0)
            self.assertEqual(json.loads(out.getvalue())[0]["run_id"], run_id)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(
                    main(["--project", tmp, "automation", "cancel", run_id]), 0
                )
            self.assertEqual(json.loads(out.getvalue())["status"], "cancelled")

    def test_automation_cli_reports_gate_violations_as_errors(self):
        import contextlib
        import io

        from vestahub.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "--project",
                        tmp,
                        "automation",
                        "enqueue",
                        "bug_fix",
                        "--task",
                        "task",
                        "--allow-cloud",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("confirmation", json.loads(out.getvalue())["message"])


class RunListingTests(unittest.TestCase):
    def test_list_runs_filters_by_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queued = enqueue_automation(root, "bug_fix", "one")
            done = enqueue_automation(root, "feature", "two")
            BackgroundRunner(root, executor=_completed_executor).run_now(done.run_id)

            self.assertEqual(
                [run.run_id for run in list_runs(root, status="queued")],
                [queued.run_id],
            )
            self.assertEqual(
                [run.run_id for run in list_runs(root, status="completed")],
                [done.run_id],
            )
            self.assertIsInstance(list_runs(root)[0], AutomationRun)


class RecoveryLeavesLiveWorkAloneTests(unittest.TestCase):
    """#818: a terminal verdict must never land on work that is still running.

    Reproduced against a real second process before this existed: a run being
    executed by a live Vesta was filed as ``failed`` with the message "the
    owning session ended before it finished", while the owning session was
    demonstrably still there. ``active_run_ids`` could not have prevented it --
    it names only the calling process's own runs, and `vestahub/cli.py` passes
    none at all.
    """

    def _running_run(self, root: Path):
        run = enqueue_automation(root, "bug_fix", "task")
        path = root / ".vestahub" / "agent" / "background" / "runs"
        record = path / f"{run.run_id}.json"
        data = json.loads(record.read_text("utf-8"))
        data["status"] = "running"
        record.write_text(json.dumps(data), encoding="utf-8")
        return run

    def test_a_run_this_process_owns_is_not_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._running_run(root)

            # No fixture needed: the journal genuinely records this process as
            # the lease owner, because this process really did admit the run.
            recovered = recover_interrupted_runs(root)

            self.assertEqual(recovered, [])
            self.assertEqual(load_run(root, run.run_id).status, "running")

    def test_a_run_owned_by_another_live_process_is_not_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._running_run(root)
            # A different Vesta, still alive: a foreign boot id on a pid that
            # is genuinely in use. This is the case `active_run_ids` is blind
            # to by construction.
            store = open_store(root)
            try:
                store.execute(
                    "UPDATE leases SET owner_pid = ?, owner_boot = 'another-vesta'",
                    (os.getpid(),),
                )
            finally:
                store.close()

            recovered = recover_interrupted_runs(root)

            self.assertEqual(recovered, [])
            self.assertEqual(load_run(root, run.run_id).status, "running")

    def test_a_run_whose_owner_died_is_still_reconciled(self):
        """The fix must not simply switch recovery off."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._running_run(root)
            _simulate_owner_death(root, run.run_id)

            recovered = recover_interrupted_runs(root)

            self.assertEqual([item.run_id for item in recovered], [run.run_id])
            self.assertEqual(load_run(root, run.run_id).status, "interrupted")

    def test_a_run_with_no_journal_at_all_still_reconciles(self):
        """Absence of evidence is not evidence of life.

        Every run admitted before #818 has no process on its lease. If those
        counted as "might be alive" they would sit in ``running`` forever with
        no way out -- the ghost state this recovery exists to clear.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._running_run(root)
            journal_path(root).unlink()

            recovered = recover_interrupted_runs(root)

            self.assertEqual([item.run_id for item in recovered], [run.run_id])

    def test_a_lease_written_before_818_still_reconciles(self):
        """The migration case, and distinct from having no journal at all.

        The journal is present and holds a lease for this run; what it lacks
        is a process on that lease, because it was written before the identity
        columns existed. Deleting the whole journal does not exercise this --
        `unterminated_runs` returns nothing and the predicate is never asked.
        A sabotage that made a pid-less lease count as alive survived the
        no-journal test untouched, which is why this one exists.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._running_run(root)
            store = open_store(root)
            try:
                store.execute("UPDATE leases SET owner_pid = NULL, owner_boot = ''")
                held = store.execute(
                    "SELECT COUNT(*) FROM leases WHERE run_id = ?", (run.run_id,)
                ).fetchone()[0]
            finally:
                store.close()
            self.assertEqual(held, 1, "the lease must exist for this to test anything")

            recovered = recover_interrupted_runs(root)

            self.assertEqual([item.run_id for item in recovered], [run.run_id])
            self.assertEqual(load_run(root, run.run_id).status, "interrupted")

    def test_an_explicitly_active_run_is_still_skipped(self):
        """The old signal keeps working; the journal is an addition to it."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._running_run(root)
            _simulate_owner_death(root, run.run_id)

            recovered = recover_interrupted_runs(root, active_run_ids=(run.run_id,))

            self.assertEqual(recovered, [])

    def test_the_live_set_is_empty_rather_than_raising_on_a_broken_journal(self):
        """Recovery runs when things are already wrong; it must not add to it."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._running_run(root)
            journal_path(root).write_bytes(b"not a database")

            self.assertEqual(runs_owned_by_a_live_process(root), frozenset())


if __name__ == "__main__":
    unittest.main()
