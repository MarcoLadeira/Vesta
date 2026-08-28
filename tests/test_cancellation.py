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
from opaihub.deadlines import TASK_DEADLINE, DeadlineBudget
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
    def __init__(self, lines, hang=False, stderr_lines=None, returncode=0):
        self.stdout = _Pipe(lines, hang)
        self.stderr = _Pipe(stderr_lines or [], False)
        self.terminated = False
        self.killed = False
        self._alive = True
        self.returncode = returncode

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self.terminated = True
        self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return self.returncode


class StubbornProc(FakeProc):
    """A process that remains alive after every best-effort stop signal."""

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        raise accounts.subprocess.TimeoutExpired("provider", timeout or 0)


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

    def test_nonzero_auth_exit_is_a_structured_failure(self):
        proc = FakeProc(
            ["401 Invalid authentication credentials\n"],
            returncode=1,
        )
        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x")

        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")
        self.assertEqual(result["returncode"], 1)

    def test_stderr_failure_is_redacted(self):
        proc = FakeProc(
            [],
            stderr_lines=[
                "Authorization: Bearer sk-secretvalue123 provider unavailable\n"
            ],
            returncode=1,
        )
        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x")

        self.assertNotIn("sk-secretvalue123", result["error"]["technicalMessage"])
        self.assertEqual(result["error"]["code"], "PROVIDER_UNAVAILABLE")

    def test_launch_permission_error_is_normalized_and_redacted(self):
        secret = "sk-secretvalue123"
        with mock.patch.object(
            accounts,
            "_popen",
            side_effect=PermissionError(f"[WinError 5] Access is denied: {secret}"),
        ):
            result = self._runner().stream("x")

        self.assertEqual(result["text"], "")
        self.assertIsInstance(result["error"], dict)
        self.assertEqual(result["error"]["code"], "SUBPROCESS_PERMISSION_DENIED")
        self.assertNotIn(secret, repr(result["error"]))

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

    def test_cancel_records_durable_teardown_evidence(self):
        proc = FakeProc(
            [
                '{"type":"assistant","message":{"content":[{"type":"text","text":"part"}]}}\n'
            ],
            hang=True,
        )
        cancel = threading.Event()
        result: dict = {}

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))

            def run():
                with mock.patch.object(accounts, "_popen", return_value=proc):
                    result.update(
                        self._runner().stream(
                            "x",
                            project_root=root,
                            cancel=cancel,
                            cancellation_scope_id="test-account-cancel",
                        )
                    )

            t = threading.Thread(target=run)
            t.start()
            time.sleep(0.25)
            cancel.set()
            t.join(timeout=3)

        evidence = result.get("cancellation") or {}
        self.assertTrue(result.get("cancelled"))
        self.assertEqual(result.get("status"), "cancelled")
        self.assertEqual(result.get("completion_state"), "cancelled")
        self.assertEqual(evidence.get("phase"), "terminated")
        self.assertEqual(
            [item["phase"] for item in evidence.get("history", [])],
            [
                "requested",
                "acknowledged",
                "draining",
                "force_terminating",
                "terminated",
            ],
        )
        self.assertIsNotNone(
            evidence.get("metrics", {}).get("acknowledgement_latency_seconds")
        )
        self.assertIsNotNone(
            evidence.get("metrics", {}).get("hard_stop_latency_seconds")
        )

    def test_stubborn_process_never_claims_terminated(self):
        proc = StubbornProc([], hang=True)
        cancel = threading.Event()
        result: dict = {}

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))

            def run():
                with mock.patch.object(accounts, "_popen", return_value=proc):
                    result.update(
                        self._runner().stream(
                            "x",
                            project_root=root,
                            cancel=cancel,
                            cancellation_scope_id="stubborn-provider",
                        )
                    )

            thread = threading.Thread(target=run)
            thread.start()
            time.sleep(0.25)
            cancel.set()
            thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertFalse(result.get("cancelled"))
        self.assertEqual(result.get("status"), "needs_attention")
        self.assertEqual(result.get("completion_state"), "needs_attention")
        self.assertEqual(result.get("stopped_reason"), "cancellation_unconfirmed")
        self.assertEqual(result["cancellation"]["phase"], "force_terminating")
        self.assertIsNone(result["cancellation"]["metrics"]["terminated_at"])

    def test_timeout_terminates_the_process(self):
        proc = FakeProc([], hang=True)
        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x", timeout=0.2)
        self.assertTrue(result.get("timed_out"))
        self.assertTrue(proc.terminated)

    def test_task_deadline_with_unproven_teardown_needs_attention(self):
        proc = StubbornProc([], hang=True)
        budget = DeadlineBudget(
            task_deadline_seconds=0.2,
            provider_idle_timeout_seconds=10.0,
            lane="long_horizon",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with mock.patch.object(accounts, "_popen", return_value=proc):
                result = self._runner().stream(
                    "x",
                    project_root=root,
                    timeout=0.2,
                    provider_idle_timeout=10.0,
                    cancellation_scope_id="stubborn-timeout",
                    deadline_budget=budget,
                )

        self.assertTrue(result.get("timed_out"))
        self.assertEqual(result.get("status"), "needs_attention")
        self.assertEqual(result.get("completion_state"), "needs_attention")
        self.assertEqual(result.get("stopped_reason"), "timeout_teardown_unconfirmed")
        self.assertEqual(result["timeout_event"]["timeout_origin"], TASK_DEADLINE)
        self.assertEqual(result["timeout_event"]["teardown_state"], "force_terminating")
        self.assertEqual(result["cancellation"]["phase"], "force_terminating")

    def test_active_stream_timeout_is_task_deadline_not_provider_idle(self):
        proc = FakeProc(
            [
                '{"type":"assistant","message":{"content":[{"type":"text","text":"part"}]}}\n'
            ],
            hang=True,
        )
        budget = DeadlineBudget(
            task_deadline_seconds=0.2,
            provider_idle_timeout_seconds=10.0,
            lane="long_horizon",
        )
        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream(
                "x",
                timeout=0.2,
                provider_idle_timeout=10.0,
                deadline_budget=budget,
                operation_id="op-active-deadline",
            )

        self.assertTrue(result.get("timed_out"))
        self.assertEqual(result["timeout_event"]["timeout_origin"], "task_deadline")
        self.assertEqual(result["timeout_event"]["provider_condition"], "responsive")
        self.assertEqual(
            result["timeout_event"]["deadline_budget_id"], budget.budget_id
        )
        self.assertEqual(result["timeout_event"]["operation_id"], "op-active-deadline")
        self.assertTrue(result["timeout_event"]["progress_observed"])
        self.assertTrue(proc.terminated)

    def test_active_deadline_snapshot_is_emitted_before_process_teardown(self):
        proc = FakeProc(
            [
                '{"type":"assistant","message":{"content":[{"type":"text","text":"part"}]}}\n'
            ],
            hang=True,
        )
        observed = {}

        def persist_timeout(snapshot):
            observed.update(snapshot)
            observed["process_was_running"] = not proc.terminated
            return {
                "state": "persisted",
                "checkpoint_id": "checkpoint-before-teardown",
                "recorded_before_teardown": True,
            }

        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream(
                "x",
                timeout=0.2,
                provider_idle_timeout=10.0,
                operation_id="operation-before-teardown",
                on_timeout=persist_timeout,
            )

        self.assertTrue(observed["process_was_running"])
        self.assertEqual(observed["timeout_event"]["timeout_origin"], TASK_DEADLINE)
        self.assertEqual(observed["timeout_event"]["teardown_state"], "requested")
        self.assertTrue(observed["partial_answer_retained"])
        self.assertEqual(result["timeout_checkpoint"]["state"], "persisted")
        self.assertTrue(proc.terminated)

    def test_timeout_checkpoint_failure_does_not_leave_provider_running(self):
        proc = FakeProc([], hang=True)

        def fail_to_persist(_snapshot):
            raise OSError("simulated checkpoint write failure")

        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream(
                "x",
                timeout=0.2,
                provider_idle_timeout=10.0,
                on_timeout=fail_to_persist,
            )

        self.assertTrue(result["timed_out"])
        self.assertEqual(result["timeout_checkpoint"]["state"], "failed")
        self.assertFalse(result["timeout_checkpoint"]["persisted"])
        self.assertNotIn("simulated checkpoint write failure", str(result))
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


class CodexStructuredStreamTests(unittest.TestCase):
    """Codex now streams structured JSONL (#106): typed activity events, final
    agent-message text, and the out-file demoted to a schema-drift fallback."""

    def _runner(self):
        return AccountRunner("codex", "/bin/codex", model="gpt-5.5")

    def test_stream_command_includes_json_flag(self):
        cmd = self._runner().build_command("x", stream=True, out_file="/t/o.txt")
        self.assertIn("--json", cmd)
        # Non-streaming (blocking complete()) stays exactly as before.
        self.assertNotIn("--json", self._runner().build_command("x"))

    def test_codex_jsonl_streams_events_and_text(self):
        lines = [
            '{"type":"thread.started"}\n',
            '{"type":"item.completed","item":{"type":"command_execution","command":"pytest -q"}}\n',
            '{"type":"item.completed","item":{"type":"agent_message","text":"All tests pass."}}\n',
            '{"type":"turn.completed"}\n',
        ]
        proc = FakeProc(lines)
        seen_events: list[dict] = []
        seen_text: list[str] = []
        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream(
                "x", on_event=seen_events.append, on_text=seen_text.append
            )
        types = [e["type"] for e in seen_events]
        self.assertIn("provider_request", types)
        self.assertIn("command_run", types)
        self.assertEqual("".join(seen_text), "All tests pass.")
        self.assertEqual(result["text"], "All tests pass.")
        # Codex reports no dollar cost — honest None.
        self.assertIsNone(result["cost"])

    def test_out_file_is_only_a_fallback_when_nothing_streamed(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("fallback answer")
            out_path = handle.name

        # Schema drift: no recognizable agent_message in the stream.
        proc = FakeProc(['{"type":"totally.unknown"}\n'])
        runner = self._runner()
        with mock.patch.object(accounts, "_popen", return_value=proc):
            with mock.patch.object(accounts.tempfile, "NamedTemporaryFile") as ntf:
                ntf.return_value.__enter__.return_value.name = out_path
                result = runner.stream("x")
        self.assertEqual(result["text"], "fallback answer")

    def test_cancel_still_kills_codex_stream(self):
        proc = FakeProc(['{"type":"thread.started"}\n'], hang=True)
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
        self.assertTrue(proc.terminated)

    def test_terminal_codex_error_stops_a_hung_stream_without_waiting_for_timeout(self):
        proc = FakeProc(
            [
                '{"type":"turn.failed","error":{"message":'
                '"The selected model is not supported"}}\n'
            ],
            hang=True,
            returncode=1,
        )

        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x", timeout=0.2)

        self.assertIn("not supported", result["error"]["technicalMessage"])
        self.assertFalse(result.get("timed_out"))
        deadline = time.monotonic() + 1
        while not proc.terminated and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(proc.terminated)

    def test_terminal_codex_error_is_not_blocked_by_tree_cleanup(self):
        proc = FakeProc(
            ['{"type":"turn.failed","error":{"message":"provider failed"}}\n'],
            hang=True,
            returncode=1,
        )
        cleanup_started = threading.Event()
        allow_cleanup = threading.Event()

        def slow_terminate(_proc):
            cleanup_started.set()
            allow_cleanup.wait(timeout=1)

        started = time.monotonic()
        with (
            mock.patch.object(accounts, "_popen", return_value=proc),
            mock.patch.object(accounts, "_terminate", side_effect=slow_terminate),
        ):
            result = self._runner().stream("x", timeout=0.2)
        elapsed = time.monotonic() - started
        allow_cleanup.set()

        self.assertTrue(cleanup_started.wait(timeout=0.1))
        self.assertIn("error", result)
        self.assertLess(elapsed, 0.2)

    def test_terminal_codex_error_preserves_unknown_returncode_until_reaped(self):
        proc = FakeProc(
            ['{"type":"turn.failed","error":{"message":"provider failed"}}\n'],
            hang=True,
            returncode=None,
        )

        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x", timeout=0.2)

        self.assertIsNone(result["returncode"])

    def test_terminal_codex_error_defers_output_file_cleanup_until_after_termination(
        self,
    ):
        proc = FakeProc(
            ['{"type":"turn.failed","error":{"message":"provider failed"}}\n'],
            hang=True,
            returncode=1,
        )
        cleanup_started = threading.Event()
        allow_cleanup = threading.Event()
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            out_path = Path(handle.name)
        original_unlink = Path.unlink

        def delayed_terminate(_proc):
            cleanup_started.set()
            allow_cleanup.wait(timeout=1)
            proc.terminated = True

        def windows_unlink(path, *args, **kwargs):
            if path == out_path and not proc.terminated:
                raise PermissionError("file is still open")
            return original_unlink(path, *args, **kwargs)

        named_file = mock.MagicMock()
        named_file.__enter__.return_value.name = str(out_path)
        named_file.__exit__.return_value = False
        try:
            with (
                mock.patch.object(accounts, "_popen", return_value=proc),
                mock.patch.object(
                    accounts, "_terminate", side_effect=delayed_terminate
                ),
                mock.patch.object(
                    accounts.tempfile, "NamedTemporaryFile", return_value=named_file
                ),
                mock.patch.object(Path, "unlink", new=windows_unlink),
            ):
                result = self._runner().stream("x", timeout=0.2)
                allow_cleanup.set()
                deadline = time.monotonic() + 1
                while out_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
        finally:
            allow_cleanup.set()
            out_path.unlink(missing_ok=True)

        self.assertTrue(cleanup_started.is_set())
        self.assertIn("error", result)
        self.assertFalse(out_path.exists())

    def test_claude_terminal_error_keeps_its_existing_synchronous_cleanup_path(self):
        proc = FakeProc(
            [
                '{"type":"result","subtype":"error_during_execution",'
                '"is_error":true,"result":"401 Invalid authentication credentials"}\n'
            ],
            returncode=1,
        )
        runner = AccountRunner("claude", "/bin/claude", model="haiku")

        with (
            mock.patch.object(accounts, "_popen", return_value=proc),
            mock.patch.object(accounts, "_terminate_async") as terminate_async,
        ):
            result = runner.stream("x")

        self.assertEqual(result["error"]["code"], "AUTH_INVALID")
        terminate_async.assert_not_called()


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

    def test_midflight_cancel_without_teardown_evidence_needs_attention(self):
        cancel = threading.Event()
        provider_started = threading.Event()

        class ObservedRunner(FakeStreamingRunner):
            def stream(self, prompt, **kwargs):
                provider_started.set()
                return super().stream(prompt, **kwargs)

        runner = ObservedRunner(chunks=["partial "], block=True)
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
        try:
            self.assertTrue(
                provider_started.wait(timeout=15),
                "the synthetic provider must be in flight",
            )
        finally:
            cancel.set()
            t.join(timeout=10)
        self.assertFalse(t.is_alive())
        self.assertEqual(
            runner.calls[0]["cancellation_scope_id"],
            f"account-{runner.calls[0]['operation_id'].replace(':', '-')}",
        )
        # This fake has no teardown journal. The runtime must preserve that
        # uncertainty instead of turning the observed stop flag into proof.
        self.assertEqual(result.get("status"), "needs_attention")
        self.assertEqual(
            (result.get("raw_result") or {}).get("stopped_reason"),
            "cancellation_unconfirmed",
        )


if __name__ == "__main__":
    unittest.main()
