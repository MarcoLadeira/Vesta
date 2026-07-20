"""Stop/close lifecycle (#140, #141): cancel events actually reach the runner,
and window teardown drains workers instead of destroying live threads.

Qt-free: the helpers operate duck-typed on the QThread subset (``isRunning`` /
``wait``), so fakes cover the policy without a display or PySide6.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opai.gui_desktop import build_chat_job
from opai.gui_lifecycle import drain_workers, signal_cancels


class FakeWorker:
    """QThread stand-in: reports running state, optionally finishes on wait."""

    def __init__(self, *, running: bool = True, finish_on_wait: bool = True) -> None:
        self._running = running
        self._finish_on_wait = finish_on_wait
        self.waited_ms: int | None = None

    def isRunning(self) -> bool:  # noqa: N802 - QThread API
        return self._running

    def wait(self, ms: int) -> bool:
        self.waited_ms = ms
        if self._finish_on_wait:
            self._running = False
            return True
        return False


class BrokenWorker:
    """Simulates a QThread whose C++ object is already destroyed."""

    def isRunning(self) -> bool:  # noqa: N802 - QThread API
        raise RuntimeError("Internal C++ object already deleted.")

    def wait(self, ms: int) -> bool:  # pragma: no cover - never reached
        raise AssertionError("wait() must not be called on a dead thread")


class SignalCancelsTests(unittest.TestCase):
    def test_sets_every_pending_event_and_counts_them(self):
        cancels = {"a": threading.Event(), "b": threading.Event()}
        self.assertEqual(signal_cancels(cancels), 2)
        self.assertTrue(all(event.is_set() for event in cancels.values()))

    def test_already_set_events_are_not_recounted(self):
        first, second = threading.Event(), threading.Event()
        first.set()
        self.assertEqual(signal_cancels({"a": first, "b": second}), 1)
        self.assertTrue(second.is_set())

    def test_empty_map_is_a_no_op(self):
        self.assertEqual(signal_cancels({}), 0)


class DrainWorkersTests(unittest.TestCase):
    def test_finished_workers_are_skipped_without_waiting(self):
        worker = FakeWorker(running=False)
        self.assertEqual(drain_workers([worker]), [])
        self.assertIsNone(worker.waited_ms, "wait() must not run for idle workers")

    def test_workers_that_finish_in_time_leave_no_stragglers(self):
        workers = [FakeWorker(), FakeWorker()]
        self.assertEqual(drain_workers(workers), [])
        self.assertTrue(all(w.waited_ms is not None for w in workers))

    def test_hung_worker_is_returned_as_straggler_not_dropped(self):
        hung = FakeWorker(finish_on_wait=False)
        stragglers = drain_workers([FakeWorker(), hung], total_ms=50)
        self.assertEqual(stragglers, [hung])

    def test_budget_is_shared_not_per_worker(self):
        workers = [FakeWorker(), FakeWorker(finish_on_wait=False)]
        drain_workers(workers, total_ms=100)
        for worker in workers:
            self.assertIsNotNone(worker.waited_ms)
            self.assertLessEqual(
                worker.waited_ms,
                100,
                "no worker may be granted more than the total shutdown budget",
            )

    def test_destroyed_thread_objects_are_tolerated(self):
        self.assertEqual(drain_workers([BrokenWorker(), FakeWorker()]), [])


class BuildChatJobTests(unittest.TestCase):
    """#141: the classic GUI's send must thread a real cancel Event into the
    pipeline so Stop kills the CLI instead of only hiding the result."""

    def test_cancel_event_reaches_handle_gui_message(self):
        captured: dict = {}

        def fake_handle(root, composed, *, model_id, mode, cancel):
            captured.update(model_id=model_id, mode=mode, cancel=cancel)
            return {"status": "answered", "answer": "ok"}

        with mock.patch(
            "opaihub.gui_pipeline.handle_gui_message", side_effect=fake_handle
        ):
            job, cancel = build_chat_job(
                Path("."), "hello", model_id="auto", mode="ask"
            )
            result = job()

        self.assertIs(
            captured["cancel"],
            cancel,
            "the event returned to the GUI must be the one the pipeline polls",
        )
        self.assertEqual(captured["model_id"], "auto")
        self.assertEqual(result["status"], "answered")

    def test_pre_cancelled_job_records_only_a_terminal_verdict(self):
        # A pre-flight cancel does no work and spends nothing, so it leaves no
        # model_call / route / receipt / task_outcome trace. But the #402 verdict
        # contract records exactly one terminal completion_verdict for the audit
        # trail — the same behaviour handle_gui_message has (see
        # test_savings_honesty.test_cancelled_before_run_records_only_a_terminal_verdict).
        from opaihub.ledger import read_events

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            job, cancel = build_chat_job(root, "task", model_id="auto", mode="ask")
            cancel.set()
            result = job()
            events = read_events(root)

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(
            [event["event_type"] for event in events],
            ["completion_verdict"],
            "a pre-flight cancel records only its terminal verdict, no spend",
        )
        self.assertEqual(events[0]["verdict"], "cancelled")


if __name__ == "__main__":
    unittest.main()
