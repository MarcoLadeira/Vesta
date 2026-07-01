"""Real cancellation: the CLI subprocess is killed on stop/timeout, the pipeline
returns a clean 'cancelled' result, and no real CLI is ever launched (Popen is
mocked with a scripted fake process).
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeStreamingRunner, make_repo

from opaihub import accounts
from opaihub.accounts import AccountRunner
from opaihub.gui_pipeline import handle_gui_message


class _Pipe:
    """Fake stdout: yields scripted lines, then EOF — or blocks (keep-alive)."""

    def __init__(self, lines, hang):
        self._lines = list(lines)
        self._hang = hang
        self._i = 0

    def readline(self):
        if self._i < len(self._lines):
            self._i += 1
            return self._lines[self._i - 1]
        if self._hang:
            time.sleep(0.03)
            return "\n"  # blank keep-alive, NOT EOF, so the loop keeps running
        return ""  # EOF


class FakeProc:
    def __init__(self, lines, hang=False):
        self.stdout = _Pipe(lines, hang)
        self.terminated = False
        self.killed = False
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        return 0


class RunnerCancellationTests(unittest.TestCase):
    def _runner(self):
        return AccountRunner("claude", "/bin/claude", model="opus")

    def test_normal_completion_returns_text_and_cost(self):
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Hi there"}]}}\n',
            '{"type":"result","total_cost_usd":0.02}\n',
        ]
        proc = FakeProc(lines)
        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x")
        self.assertEqual(result["text"], "Hi there")
        self.assertEqual(result["cost"], 0.02)
        self.assertFalse(result.get("cancelled"))

    def test_cancel_terminates_the_process(self):
        proc = FakeProc(
            [
                '{"type":"assistant","message":{"content":[{"type":"text","text":"part"}]}}\n'
            ],
            hang=True,
        )
        cancel = threading.Event()
        result: dict = {}

        def run():
            with mock.patch.object(accounts, "_popen", return_value=proc):
                result.update(self._runner().stream("x", cancel=cancel))

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.25)
        cancel.set()
        t.join(timeout=3)
        self.assertTrue(result.get("cancelled"))
        self.assertTrue(proc.terminated)  # the process was actually killed
        self.assertIn("part", result.get("text", ""))  # partial text preserved

    def test_timeout_terminates_the_process(self):
        proc = FakeProc([], hang=True)
        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x", timeout=0.2)
        self.assertTrue(result.get("timed_out"))
        self.assertTrue(proc.terminated)

    def test_streams_text_and_tool_events(self):
        seen_events = []
        seen_text = []
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"a.py"}}]}}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"answer"}]}}\n',
            '{"type":"result","total_cost_usd":0.01}\n',
        ]
        proc = FakeProc(lines)
        with mock.patch.object(accounts, "_popen", return_value=proc):
            self._runner().stream(
                "x", on_event=seen_events.append, on_text=seen_text.append
            )
        self.assertIn("file_read", [e["type"] for e in seen_events])
        self.assertEqual("".join(seen_text), "answer")


class PipelineCancellationTests(unittest.TestCase):
    def test_precancel_returns_cancelled_without_calling_runner(self):
        cancel = threading.Event()
        cancel.set()  # already cancelled before the model call

        class Boom(FakeStreamingRunner):
            def stream(self, *a, **k):
                raise AssertionError("runner must not run after pre-cancel")

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            result = handle_gui_message(
                root,
                "hi",
                model_id="account:claude:opus",
                mode="ask",
                account_runner=Boom(),
                on_event=lambda e: None,
                cancel=cancel,
            )
        self.assertEqual(result["status"], "cancelled")

    def test_midflight_cancel_returns_partial(self):
        cancel = threading.Event()
        runner = FakeStreamingRunner(chunks=["partial "], block=True)
        result: dict = {}

        def run():
            with tempfile.TemporaryDirectory() as tmp:
                root = make_repo(Path(tmp))
                result.update(
                    handle_gui_message(
                        root,
                        "hi",
                        model_id="account:claude:opus",
                        mode="ask",
                        account_runner=runner,
                        on_event=lambda e: None,
                        on_text=lambda t: None,
                        cancel=cancel,
                    )
                )

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.4)
        cancel.set()
        t.join(timeout=3)
        # Cancelling mid-flight always yields a clean 'cancelled' status. (Whether
        # partial text is captured depends on whether cancel lands before or after
        # the model call starts — the deterministic partial-text guarantee is
        # asserted at the runner level above.)
        self.assertEqual(result.get("status"), "cancelled")


if __name__ == "__main__":
    unittest.main()
