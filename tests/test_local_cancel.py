"""True mid-flight cancellation for the local/auto path (issue #107).

A blocked local-model HTTP call must abort promptly when the user hits Stop —
not merely have its late result ignored. Uses a loopback-only hanging server
(standard test practice; no external network, no model, no spend).
"""

from __future__ import annotations

import http.server
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeLocalRunner, make_repo

from opaihub.ask import run_ask
from opaihub.gui_pipeline import handle_gui_message
from opaihub.local_runner import LocalRunCancelled, OllamaRunner


class _HangingHandler(http.server.BaseHTTPRequestHandler):
    """Accepts the request then never responds — a stuck local model."""

    def do_POST(self):  # noqa: N802
        time.sleep(30)

    def log_message(self, *args):  # silence
        pass


class _DaemonServer(http.server.ThreadingHTTPServer):
    # Handler threads must not block shutdown (they sleep on purpose).
    daemon_threads = True


class RunnerCancelTests(unittest.TestCase):
    def setUp(self):
        self.server = _DaemonServer(("127.0.0.1", 0), _HangingHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_cancel_aborts_a_blocked_local_call_promptly(self):
        runner = OllamaRunner(base_url=self.base, model="test")
        cancel = threading.Event()
        threading.Timer(0.4, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(LocalRunCancelled):
            runner.complete("hello", timeout=25.0, cancel=cancel)
        elapsed = time.monotonic() - started
        # Aborted by the cancel watcher, not the 25s socket timeout.
        self.assertLess(elapsed, 5.0)

    def test_no_cancel_event_keeps_plain_timeout_behavior(self):
        runner = OllamaRunner(base_url=self.base, model="test")
        with self.assertRaises(Exception) as ctx:
            runner.complete("hello", timeout=0.4)
        self.assertNotIsInstance(ctx.exception, LocalRunCancelled)


class RunAskCancelTests(unittest.TestCase):
    def test_pre_set_cancel_never_calls_the_runner(self):
        class Boom(FakeLocalRunner):
            def complete(self, *a, **k):
                raise AssertionError("must not run after pre-cancel")

        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            result = run_ask(root, "task", runner=Boom(), record=False, cancel=cancel)
        self.assertEqual(result["status"], "cancelled")

    def test_runner_cancel_maps_to_cancelled_status(self):
        class Cancelling(FakeLocalRunner):
            def complete(self, *a, **k):
                raise LocalRunCancelled()

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            result = run_ask(
                root,
                "task",
                runner=Cancelling(),
                record=False,
                cancel=threading.Event(),
            )
        self.assertEqual(result["status"], "cancelled")

    def test_runner_without_cancel_kwarg_still_answers(self):
        # Backward compatibility: fakes/free-tier runners without the kwarg
        # keep working via the TypeError fallback.
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            result = run_ask(
                root,
                "task",
                runner=FakeLocalRunner(answer="hi"),
                record=False,
                cancel=threading.Event(),
            )
        self.assertEqual(result["status"], "answered_locally")
        self.assertEqual(result["answer"], "hi")


class PipelineLocalCancelTests(unittest.TestCase):
    def test_local_cancelled_result_is_clean(self):
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with mock.patch(
                "opaihub.ask.run_ask",
                return_value={"status": "cancelled", "answer": ""},
            ):
                result = handle_gui_message(
                    root,
                    "hi",
                    model_id="auto",
                    mode="ask",
                    on_event=events.append,
                    cancel=threading.Event(),
                )
        self.assertEqual(result["status"], "cancelled")
        self.assertIn("cancelled", [e["type"] for e in events])


if __name__ == "__main__":
    unittest.main()
