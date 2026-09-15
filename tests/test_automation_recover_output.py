"""#818 review finding 11: `vesta automation recover` output.

The branch changed stdout from a JSON list of recovered runs to an object,
which breaks every script that parses it. And its "left_to_their_owner" half
listed every unfinished journal run with a live owner -- GUI chat turns
included -- rather than the background runs the sweep had actually skipped.
"""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from opaihub import cli, journal_runtime
from opaihub.background_runs import BackgroundRunner, enqueue_automation

NOW = "2026-09-10T10:00:00+00:00"


class RecoverOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def recover(self) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.cmd_automation(
                argparse.Namespace(project=str(self.root), automation_command="recover")
            )
        self.assertEqual(code, 0)
        return out.getvalue(), err.getvalue()

    def test_stdout_is_still_a_list(self):
        out, err = self.recover()

        self.assertEqual(json.loads(out), [])
        self.assertEqual(err, "")

    def test_a_live_run_is_named_on_stderr_and_a_chat_turn_is_not(self):
        # A GUI chat turn: an unfinished journal run owned by this live process.
        journal_runtime.record_admission(
            self.root, task_id="chat", run_id="chat-turn-1", task="hi", now=NOW
        )
        # A background run, executing right now in this process.
        run = enqueue_automation(self.root, "bug_fix", "fix the flaky test")
        executing, release = threading.Event(), threading.Event()

        def executor(_root, _run, _cancel):
            executing.set()
            release.wait(timeout=60)
            return {"status": "answered", "answer": "done"}

        thread = BackgroundRunner(self.root, executor=executor).start(run.run_id)
        self.addCleanup(thread.join, 60)
        self.addCleanup(release.set)
        self.assertTrue(executing.wait(timeout=60))

        out, err = self.recover()

        self.assertEqual(json.loads(out), [], "a live run was reconciled")
        self.assertIn(run.run_id, err)
        self.assertNotIn("chat-turn-1", err, "a chat turn is not a background run")


if __name__ == "__main__":
    unittest.main()
