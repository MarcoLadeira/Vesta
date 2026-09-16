"""True mid-flight cancellation for the local/auto path (issue #107).

A blocked local-model HTTP call must abort promptly when the user hits Stop —
not merely have its late result ignored. Uses a loopback-only hanging server
(standard test practice; no external network, no model, no spend).
"""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeLocalRunner, make_repo

from vestahub.ask import run_ask
from vestahub.gui_pipeline import handle_gui_message
from vestahub.local_runner import (
    FreeAPIRunner,
    LocalRunCancelled,
    OllamaRunner,
    _http_json_cancellable,
)


class EditableUpgradeCompatibilityTests(unittest.TestCase):
    def test_ask_import_survives_stale_local_runner_during_editable_upgrade(self):
        script = """
import sys
import types

stale = types.ModuleType("vestahub.local_runner")
stale.LocalRunner = type("LocalRunner", (), {})
stale.detect_local_runner = lambda *args, **kwargs: None
sys.modules["vestahub.local_runner"] = stale

import vestahub.ask
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)


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


class _RespondingHandler(http.server.BaseHTTPRequestHandler):
    """Answers immediately with an OpenAI-shaped body and a quota header."""

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps(
            {
                "choices": [{"message": {"content": "hello from free tier"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-RateLimit-Limit-Requests", "100")
        self.send_header("X-RateLimit-Remaining-Requests", "97")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


class FreeTierCancelTests(unittest.TestCase):
    """Free-tier HTTP requests must abort on Stop, not just hide the result."""

    def _free_runner(self, base: str) -> FreeAPIRunner:
        runner = FreeAPIRunner("https://example.test/v1", "m", "secret-key")
        runner.base_url = base  # loopback fixture; the HTTPS check ran on init
        return runner

    def test_transport_cancellable_helper_aborts_promptly(self):
        server = _DaemonServer(("127.0.0.1", 0), _HangingHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            cancel = threading.Event()
            threading.Timer(0.4, cancel.set).start()
            started = time.monotonic()
            with self.assertRaises(LocalRunCancelled):
                _http_json_cancellable(
                    f"{base}/chat/completions",
                    payload={"model": "m", "messages": []},
                    timeout=25.0,
                    cancel=cancel,
                    extra_headers={"Authorization": "Bearer secret-key"},
                )
            self.assertLess(time.monotonic() - started, 5.0)
        finally:
            server.shutdown()
            server.server_close()

    def test_free_runner_complete_aborts_on_cancel(self):
        server = _DaemonServer(("127.0.0.1", 0), _HangingHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            runner = self._free_runner(base)
            cancel = threading.Event()
            threading.Timer(0.4, cancel.set).start()
            started = time.monotonic()
            with self.assertRaises(LocalRunCancelled):
                runner.complete("hello", timeout=25.0, cancel=cancel)
            self.assertLess(time.monotonic() - started, 5.0)
        finally:
            server.shutdown()
            server.server_close()

    def test_free_runner_success_still_parses_body_and_quota_headers(self):
        server = _DaemonServer(("127.0.0.1", 0), _RespondingHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            runner = self._free_runner(base)
            answer = runner.complete("hi", cancel=threading.Event())
            self.assertEqual(answer, "hello from free tier")
            self.assertEqual(runner.last_usage["tokens"], 7)
            self.assertEqual(runner.last_usage["measurement"], "provider")
            self.assertEqual(runner.last_usage["quota_snapshot"]["remaining"], 97)
        finally:
            server.shutdown()
            server.server_close()

    def test_free_runner_answers_when_not_cancelled(self):
        server = _DaemonServer(("127.0.0.1", 0), _RespondingHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            runner = self._free_runner(base)
            self.assertEqual(runner.complete("hi", cancel=None), "hello from free tier")
        finally:
            server.shutdown()
            server.server_close()


class FreeTierPipelineCancelTests(unittest.TestCase):
    def test_cancelled_free_call_never_records_spend_or_savings(self):
        from vestahub.ledger import read_events

        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with mock.patch(
                "vesta.app_state.ask",
                return_value={"status": "cancelled", "answer": ""},
            ):
                result = handle_gui_message(
                    root,
                    "hi",
                    model_id="free:groq:llama",
                    mode="ask",
                    allow_cloud=True,
                    on_event=events.append,
                    cancel=threading.Event(),
                )
        self.assertEqual(result["status"], "cancelled")
        self.assertIn("cancelled", [e["type"] for e in events])
        ledger_types = {e.get("event_type") for e in read_events(root)}
        self.assertNotIn("gui_receipt", ledger_types)
        self.assertNotIn("route_decision", ledger_types)


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
                "vestahub.ask.run_ask",
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
