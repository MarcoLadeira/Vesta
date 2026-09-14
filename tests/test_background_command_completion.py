"""A backgrounded command's result must reach the completion check (#486).

The witness run: Vesta was asked to fix an issue, it started a full pytest suite
and waited for it, and the turn ended on *"Partial — required verification
evidence is missing, unavailable, or damaged"* instead of on the test result.
Two separate defects produced that one sentence.

1. The account runner's idle watchdog measured "time since the provider last
   streamed something" and knew nothing about tool calls. A provider waiting on
   a long command streams nothing while it waits, so the run was killed at the
   idle timeout — while the command was still making progress — and the result
   it was about to produce never arrived.

2. When a turn genuinely does end with a command still running, the verdict saw
   only the absence of a result and reported the evidence as missing. That names
   Vesta's own bookkeeping as the problem when the real state is knowable and
   different: the command has not finished yet.

These tests pin both: a command that finishes after a delay longer than the idle
timeout still has its result surfaced, and a command that really is still
running is reported as such rather than as missing evidence.
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

from opai.activity import ActivitySession
from opaihub import accounts
from opaihub.accounts import AccountRunner
from opaihub.completion import (
    MEASURED_EVIDENCE_KEYS,
    CompletionVerdict,
    evaluate_completion,
    evidence_payload,
    objective_from_request,
    unfinished_background_work,
)


def _assistant_tool_use(call_id: str, command: str, *, background: bool = False) -> str:
    inputs: dict[str, object] = {"command": command}
    if background:
        inputs["run_in_background"] = True
    return (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        f'"id":"{call_id}","name":"Bash","input":{_json(inputs)}}}]}}}}\n'
    )


def _shell_query(call_id: str, tool: str, shell_id: str) -> str:
    return (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        f'"id":"{call_id}","name":"{tool}","input":{{"bash_id":"{shell_id}"}}}}]}}}}\n'
    )


def _tool_result(call_id: str, text: str) -> str:
    return (
        '{"type":"user","message":{"content":[{"type":"tool_result",'
        f'"tool_use_id":"{call_id}","content":{_json(text)}}}]}}}}\n'
    )


def _assistant_text(text: str) -> str:
    return (
        '{"type":"assistant","message":{"content":[{"type":"text",'
        f'"text":{_json(text)}}}]}}}}\n'
    )


def _json(value: object) -> str:
    import json

    return json.dumps(value)


class _SlowPipe:
    """Scripted stdout that goes quiet in the middle, like a provider working.

    ``quiet_after`` lines are delivered, then blank keep-alives for
    ``quiet_seconds`` (a blank line is not activity — the runner ignores it, so
    the idle clock keeps running), then the rest.
    """

    def __init__(self, lines, *, quiet_after: int, quiet_seconds: float):
        self._lines = list(lines)
        self._i = 0
        self._quiet_after = quiet_after
        self._quiet_seconds = quiet_seconds
        self._quiet_until: float | None = None

    def readline(self) -> str:
        if self._i == self._quiet_after:
            if self._quiet_until is None:
                self._quiet_until = time.monotonic() + self._quiet_seconds
            if time.monotonic() < self._quiet_until:
                time.sleep(0.01)
                return "\n"
        if self._i < len(self._lines):
            self._i += 1
            return self._lines[self._i - 1]
        return ""


class _EmptyPipe:
    def readline(self) -> str:
        return ""


class _SlowProc:
    def __init__(self, lines, *, quiet_after: int, quiet_seconds: float):
        self.stdout = _SlowPipe(
            lines, quiet_after=quiet_after, quiet_seconds=quiet_seconds
        )
        self.stderr = _EmptyPipe()
        self.terminated = False
        self._alive = True
        self.returncode = 0

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self.terminated = True
        self._alive = False

    def kill(self):
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return self.returncode


class InFlightCommandIsNotProviderSilenceTests(unittest.TestCase):
    """The reproduction: a long command must not be killed as an idle provider."""

    def _runner(self) -> AccountRunner:
        return AccountRunner("claude", "/bin/claude", model="opus")

    def test_command_finishing_after_the_idle_timeout_still_reports_its_result(self):
        lines = [
            _assistant_tool_use("toolu_1", "python -m pytest -q"),
            # ... the suite runs here, silently, for longer than the idle
            # timeout — the exact condition that used to kill the run ...
            _tool_result("toolu_1", "1631 passed, 12 skipped in 402.11s"),
            _assistant_text("The suite passed: 1631 passed, 12 skipped."),
            '{"type":"result","total_cost_usd":0.4}\n',
        ]
        proc = _SlowProc(lines, quiet_after=1, quiet_seconds=0.5)

        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream(
                "run the suite", timeout=20.0, provider_idle_timeout=0.2
            )

        self.assertFalse(result.get("timed_out"), result)
        self.assertIn("1631 passed", result["text"])
        self.assertFalse(proc.terminated)
        # Nothing was left open, so no still-running work is reported.
        self.assertNotIn("background_work", result)

    def test_silence_with_nothing_in_flight_still_times_out(self):
        """The watchdog is narrowed, not disabled: a quiet provider still stops."""

        lines = [
            _assistant_text("Working on it."),
            _assistant_text("never reached"),
        ]
        proc = _SlowProc(lines, quiet_after=1, quiet_seconds=5.0)

        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x", timeout=20.0, provider_idle_timeout=0.2)

        self.assertTrue(result.get("timed_out"))
        self.assertEqual(
            result["timeout_event"]["timeout_origin"], "provider_idle_timeout"
        )
        self.assertTrue(proc.terminated)

    def test_task_deadline_still_bounds_a_tool_call_that_never_returns(self):
        """An in-flight call defers the idle clock; it cannot defer the deadline."""

        lines = [
            _assistant_tool_use("toolu_1", "python -m pytest -q"),
            _assistant_text("never reached"),
        ]
        proc = _SlowProc(lines, quiet_after=1, quiet_seconds=10.0)

        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x", timeout=0.3, provider_idle_timeout=0.1)

        self.assertTrue(result.get("timed_out"))
        self.assertEqual(result["timeout_event"]["timeout_origin"], "task_deadline")
        self.assertTrue(proc.terminated)

    def test_unfinished_background_command_is_reported_on_the_result(self):
        lines = [
            _assistant_tool_use("toolu_1", "python -m pytest -q", background=True),
            _tool_result("toolu_1", "Command running in background with ID: bash_1"),
            _assistant_text("Started the suite; I'll report when it finishes."),
            '{"type":"result","total_cost_usd":0.1}\n',
        ]
        proc = _SlowProc(lines, quiet_after=len(lines), quiet_seconds=0)

        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x", timeout=20.0)

        unfinished = result["background_work"]["unfinished"]
        self.assertEqual([item["id"] for item in unfinished], ["bash_1"])
        self.assertIn("pytest", unfinished[0]["command"])

    def test_observed_background_completion_is_not_reported_as_unfinished(self):
        lines = [
            _assistant_tool_use("toolu_1", "python -m pytest -q", background=True),
            _tool_result("toolu_1", "Command running in background with ID: bash_1"),
            _shell_query("toolu_2", "BashOutput", "bash_1"),
            _tool_result("toolu_2", "<status>completed</status>\n1631 passed"),
            _assistant_text("The suite passed."),
            '{"type":"result","total_cost_usd":0.1}\n',
        ]
        proc = _SlowProc(lines, quiet_after=len(lines), quiet_seconds=0)

        with mock.patch.object(accounts, "_popen", return_value=proc):
            result = self._runner().stream("x", timeout=20.0)

        self.assertNotIn("background_work", result)


class ActivitySessionInFlightTrackingTests(unittest.TestCase):
    """The bookkeeping both fixes read from."""

    def test_a_tool_call_is_in_flight_until_its_result_arrives(self):
        session = ActivitySession("req")
        self.assertFalse(session.has_work_in_flight())

        session.parse_claude_line(_assistant_tool_use("toolu_1", "pytest -q"))
        self.assertTrue(session.has_work_in_flight())

        session.parse_claude_line(_tool_result("toolu_1", "ok"))
        self.assertFalse(session.has_work_in_flight())

    def test_a_successful_result_closes_the_call_even_with_no_correction_event(self):
        """A green result emits no event; it must still close the record."""

        session = ActivitySession("req")
        session.parse_claude_line(_assistant_tool_use("toolu_1", "pytest -q"))
        parsed = session.parse_claude_line(_tool_result("toolu_1", "1631 passed"))

        self.assertEqual(parsed["events"], [])
        self.assertFalse(session.has_work_in_flight())

    def test_a_tool_use_without_an_id_is_never_tracked(self):
        """An untrackable call must not suppress the watchdog forever."""

        session = ActivitySession("req")
        session.parse_claude_line(
            '{"type":"assistant","message":{"content":[{"type":"tool_use",'
            '"name":"Bash","input":{"command":"pytest"}}]}}\n'
        )
        self.assertFalse(session.has_work_in_flight())

    def test_backgrounded_command_survives_its_own_tool_call_returning(self):
        session = ActivitySession("req")
        session.parse_claude_line(
            _assistant_tool_use("toolu_1", "pytest -q", background=True)
        )
        session.parse_claude_line(
            _tool_result("toolu_1", "Command running in background with ID: bash_7")
        )

        # The *call* finished immediately; the command it started did not.
        self.assertFalse(session.has_work_in_flight())
        unfinished = session.background_work()["unfinished"]
        self.assertEqual([item["id"] for item in unfinished], ["bash_7"])

    def test_a_still_running_output_report_does_not_close_the_command(self):
        session = ActivitySession("req")
        session.parse_claude_line(
            _assistant_tool_use("toolu_1", "pytest -q", background=True)
        )
        session.parse_claude_line(
            _tool_result("toolu_1", "Command running in background with ID: bash_7")
        )
        session.parse_claude_line(_shell_query("toolu_2", "BashOutput", "bash_7"))
        session.parse_claude_line(
            _tool_result("toolu_2", "<status>running</status>\ncollecting ...")
        )

        self.assertEqual(len(session.background_work()["unfinished"]), 1)

    def test_killing_the_shell_closes_the_command(self):
        session = ActivitySession("req")
        session.parse_claude_line(
            _assistant_tool_use("toolu_1", "pytest -q", background=True)
        )
        session.parse_claude_line(
            _tool_result("toolu_1", "Command running in background with ID: bash_7")
        )
        session.parse_claude_line(_shell_query("toolu_2", "KillShell", "bash_7"))
        session.parse_claude_line(_tool_result("toolu_2", "Shell bash_7 killed"))

        self.assertEqual(session.background_work()["unfinished"], [])

    def test_an_open_call_is_reported_as_unfinished_work(self):
        session = ActivitySession("req")
        session.parse_claude_line(_assistant_tool_use("toolu_1", "pytest -q"))

        unfinished = session.background_work()["unfinished"]
        self.assertEqual([item["kind"] for item in unfinished], ["tool_call"])


class StillRunningIsReportedAsItselfTests(unittest.TestCase):
    """The verdict names the cause instead of reporting an absence."""

    def _objective(self):
        return objective_from_request(
            "Fix the ignore-file race and run the tests", mode="ship"
        )

    def _payload(self, **extra):
        measured = {
            "status": "answered",
            "answer": "Started the suite; I'll report when it finishes.",
            "completion_state": "completed",
            "changed_files": ["opaihub/context_engine.py"],
            **extra,
        }
        return evidence_payload(measured)

    def _running(self):
        return {
            "unfinished": [
                {
                    "kind": "background_command",
                    "id": "bash_1",
                    "command": "python -m pytest -q",
                }
            ]
        }

    def test_background_work_is_an_allowlisted_measured_key(self):
        self.assertIn("background_work", MEASURED_EVIDENCE_KEYS)
        payload = self._payload(background_work=self._running())
        self.assertEqual(unfinished_background_work(payload), ("python -m pytest -q",))

    def test_missing_test_evidence_is_reported_as_the_command_still_running(self):
        verdict = evaluate_completion(
            self._objective(), self._payload(background_work=self._running())
        )

        self.assertIs(verdict.verdict, CompletionVerdict.PARTIAL)
        self.assertEqual(verdict.reason_code, "work_still_running")
        self.assertIn("python -m pytest -q", verdict.reason)
        self.assertIn("still running", verdict.reason)

    def test_without_running_work_the_original_reason_is_unchanged(self):
        verdict = evaluate_completion(self._objective(), self._payload())

        self.assertIs(verdict.verdict, CompletionVerdict.PARTIAL)
        self.assertEqual(verdict.reason_code, "tests_not_verified")

    def test_a_deadline_that_expired_with_work_running_says_so(self):
        payload = self._payload(
            status="failed",
            completion_state="timeout",
            stopped_reason="task_deadline",
            background_work=self._running(),
        )
        verdict = evaluate_completion(self._objective(), payload)

        self.assertIs(verdict.verdict, CompletionVerdict.TIMEOUT)
        self.assertIn("python -m pytest -q", verdict.reason)

    def test_still_running_work_cannot_promote_an_unverified_run(self):
        """The new key only ever explains; it never awards a completion."""

        verdict = evaluate_completion(
            self._objective(),
            self._payload(changed_files=[], background_work=self._running()),
        )

        self.assertIsNot(verdict.verdict, CompletionVerdict.COMPLETED)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
