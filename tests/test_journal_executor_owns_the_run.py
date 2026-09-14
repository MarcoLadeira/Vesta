"""#818 review finding 1: the process running a background run owns it.

The journal lease named whoever *first* saved a run. For a background run that
is ``opai automation enqueue``, which exits at once; ``opai automation run``
executes it in a second process that never took the lease over. A concurrent
``opai automation recover`` then asked "is the owner alive?", got "no", and
wrote "failed: the owning session ended before it finished" onto a run that
was running -- the false record the liveness check was added to prevent.

These tests reproduce that with real processes: one enqueues and exits, this
one executes, and a third recovers while the run is live.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from opaihub import background_runs, journal_runtime, journal_store
from opaihub.background_runs import BackgroundRunner, load_run
from opaihub.run_state import RunState

NOW = "2026-09-10T10:00:00+00:00"
LATER = "2026-09-10T10:01:00+00:00"
REPO = str(Path(__file__).resolve().parents[1])


def _in_another_process(root: Path, body: str) -> str:
    """Run ``body`` in a fresh interpreter against ``root``; return its stdout."""

    script = (
        "import sys\n"
        f"sys.path.insert(0, {REPO!r})\n"
        "from pathlib import Path\n"
        f"root = Path({str(root)!r})\n" + body
    )
    done = subprocess.run(  # nosec B603 - fixed argv, our own interpreter
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": REPO},
    )
    if done.returncode != 0:
        raise AssertionError(f"child failed:\n{done.stdout}\n{done.stderr}")
    return done.stdout.strip()


def _lease(root: Path, run_id: str) -> dict:
    store = journal_store.open_store(root)
    try:
        row = store.execute(
            "SELECT owner_pid, owner_boot, fence, released_at FROM leases"
            " WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return dict(row) if row is not None else {}
    finally:
        store.close()


class _Root(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class TheExecutingProcessTakesTheLeaseTests(_Root):
    """The ownership rule itself, on the snapshot mirror."""

    def snapshot(self, state: str, *, verdict: str = "") -> bool:
        return journal_runtime.record_run_snapshot(
            self.root,
            run_id="bg-1",
            task_id="task-1",
            task="queued work",
            state=state,
            verdict=verdict,
            now=LATER,
        )

    def queue_elsewhere(self) -> None:
        out = _in_another_process(
            self.root,
            "from opaihub import journal_runtime\n"
            "print(journal_runtime.record_run_snapshot(root, run_id='bg-1',"
            " task_id='task-1', task='queued work', state='queued',"
            f" now={NOW!r}))\n",
        )
        self.assertEqual(out, "True")

    def test_starting_a_run_queued_elsewhere_takes_its_lease(self):
        self.queue_elsewhere()
        queued_by = _lease(self.root, "bg-1")
        self.assertNotEqual(queued_by["owner_pid"], os.getpid())

        self.assertTrue(self.snapshot("preparing"))

        running = _lease(self.root, "bg-1")
        self.assertEqual(running["owner_pid"], os.getpid())
        self.assertGreater(running["fence"], queued_by["fence"])
        self.assertIsNone(running["released_at"])

    def test_the_new_owner_can_finish_the_run(self):
        self.queue_elsewhere()
        self.snapshot("running")

        self.assertTrue(self.snapshot("completed", verdict="completed"))

        store = journal_store.open_store(self.root)
        try:
            verdict = store.execute(
                "SELECT terminal_verdict FROM runs WHERE run_id = 'bg-1'"
            ).fetchone()[0]
        finally:
            store.close()
        self.assertEqual(verdict, "completed")

    def test_describing_a_run_does_not_take_it(self):
        """A cancel request from another window must not fence out the runner."""

        self.queue_elsewhere()
        before = _lease(self.root, "bg-1")

        self.assertTrue(self.snapshot("cancel_requested"))

        self.assertEqual(_lease(self.root, "bg-1"), before)

    def test_saving_again_while_running_keeps_one_owner(self):
        self.queue_elsewhere()
        self.snapshot("running")
        first = _lease(self.root, "bg-1")

        self.snapshot("running")

        self.assertEqual(_lease(self.root, "bg-1")["fence"], first["fence"])


class RecoverLeavesALiveRunAloneTests(_Root):
    """The reviewer's reproduction, end to end."""

    def test_recover_does_not_fail_a_run_executing_in_a_live_process(self):
        run_id = _in_another_process(
            self.root,
            "from opaihub.background_runs import enqueue_automation\n"
            "print(enqueue_automation(root, 'bug_fix', 'fix the flaky test').run_id)\n",
        )
        executing = threading.Event()
        release = threading.Event()

        def executor(_root, _run, _cancel):
            executing.set()
            release.wait(timeout=60)
            return {"status": "answered", "answer": "done"}

        runner = BackgroundRunner(self.root, executor=executor)
        thread = runner.start(run_id)
        self.addCleanup(thread.join, 60)
        self.addCleanup(release.set)
        self.assertTrue(executing.wait(timeout=60), "the run never started")
        self.assertIs(
            background_runs._coerce_run_state(load_run(self.root, run_id).run_state),
            RunState.RUNNING,
        )

        recovered = _in_another_process(
            self.root,
            "from opaihub.background_runs import recover_interrupted_runs\n"
            "print(','.join(r.run_id for r in recover_interrupted_runs(root)))\n",
        )

        self.assertEqual(recovered, "", "recover filed a live run as interrupted")
        self.assertIs(
            background_runs._coerce_run_state(load_run(self.root, run_id).run_state),
            RunState.RUNNING,
        )

        release.set()
        thread.join(timeout=60)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if background_runs._is_terminal_run(load_run(self.root, run_id)):
                break
            time.sleep(0.05)
        self.assertTrue(background_runs._is_terminal_run(load_run(self.root, run_id)))
        # The executing process finished it under the lease it took, so the
        # ending was recorded and the lease let go -- not refused as stale.
        self.assertIsNotNone(
            _lease(self.root, run_id).get("released_at"),
            "the finished run still holds its lease",
        )


class WhichStatesTransferOwnershipTests(unittest.TestCase):
    """Derived from the lifecycle contract; pinned here so a change is seen."""

    def test_the_states_doing_the_work_and_no_others(self):
        self.assertEqual(
            journal_runtime._EXECUTING_STATES,
            {
                RunState.PREPARING.value,
                RunState.RUNNING.value,
                RunState.VERIFYING.value,
            },
        )

    def test_a_queued_run_is_not_executing(self):
        """Queued is active, but nobody is working on it yet."""

        self.assertNotIn(RunState.QUEUED.value, journal_runtime._EXECUTING_STATES)


if __name__ == "__main__":
    unittest.main()
