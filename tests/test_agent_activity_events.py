from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from vesta.activity import (
    ActivitySession,
    derived_id,
    emit_event,
    make_event,
    parse_claude_line,
    parse_codex_line,
)
from vestahub.aci import AgentComputerInterface
from vestahub.github_workflow import GitHubAdapter


class AgentOperationActivityTests(unittest.TestCase):
    def test_test_run_emits_one_secret_free_validation_lifecycle(self):
        events = []
        secret = "must-" + "not-enter-events"

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "to" + f"ken={secret}", "")

        with tempfile.TemporaryDirectory() as tmp:
            result = AgentComputerInterface(
                Path(tmp), run=fake_run, on_event=events.append
            ).run_tests(["python", "-m", "unittest"], scope="focused")

        self.assertTrue(result.ok)
        self.assertEqual([event["type"] for event in events], ["validation"] * 2)
        self.assertEqual([event["status"] for event in events], ["running", "success"])
        self.assertEqual(events[0]["id"], events[1]["id"])
        self.assertEqual(events[0]["title"], "Running focused tests")
        self.assertEqual(events[1]["title"], "Focused tests passed")
        self.assertNotIn(secret, str(events))

    def test_failed_test_run_finishes_validation_as_error(self):
        events = []

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "1 failed")

        with tempfile.TemporaryDirectory() as tmp:
            result = AgentComputerInterface(
                Path(tmp), run=fake_run, on_event=events.append
            ).run_tests(["pytest"], scope="full")

        self.assertFalse(result.ok)
        self.assertEqual(events[-1]["status"], "error")
        self.assertEqual(events[-1]["title"], "Full tests failed")
        self.assertEqual(events[-1]["metadata"]["returncode"], 1)

    def test_pr_open_ci_watch_and_merge_emit_distinct_real_lifecycles(self):
        events = []
        check_calls = 0
        secret = "must-" + "not-enter-events"

        def fake_run(argv, **kwargs):
            nonlocal check_calls
            if argv[1:3] == ["pr", "create"]:
                return subprocess.CompletedProcess(
                    argv, 0, "https://github.test/pr/12\n", ""
                )
            if argv[1:3] == ["pr", "checks"]:
                check_calls += 1
                body = (
                    '[{"name":"tests","state":"PENDING","bucket":"pending"}]'
                    if check_calls == 1
                    else '[{"name":"tests","state":"SUCCESS","bucket":"pass"}]'
                )
                return subprocess.CompletedProcess(
                    argv, 8 if check_calls == 1 else 0, body, ""
                )
            if argv[1:3] == ["pr", "merge"]:
                return subprocess.CompletedProcess(argv, 0, "merged\n", "")
            raise AssertionError(argv)

        with tempfile.TemporaryDirectory() as tmp:
            adapter = GitHubAdapter(Path(tmp), run=fake_run, on_event=events.append)
            url = adapter.create_pr(
                title="Fix activity",
                body=f"No raw credential value {secret}",
                head="codex/activity",
                base="main",
                issue_number=109,
            )
            watched = adapter.watch_pr_checks(
                12, poll_interval=0, timeout=5, sleep=lambda _seconds: None
            )
            adapter.merge_pr(12)

        self.assertEqual(url, "https://github.test/pr/12")
        self.assertEqual(watched["status"], "passed")
        self.assertEqual(
            [event["type"] for event in events],
            [
                "tool_call",
                "tool_call",
                "ci_watch",
                "ci_watch",
                "ci_watch",
                "command_run",
                "command_complete",
            ],
        )
        self.assertEqual(events[0]["id"], events[1]["id"])
        self.assertEqual(events[2]["id"], events[3]["id"])
        self.assertEqual(events[3]["id"], events[4]["id"])
        self.assertEqual(events[5]["id"], events[6]["id"])
        self.assertEqual(events[1]["title"], "Pull request opened")
        self.assertEqual(events[4]["title"], "CI checks passed")
        self.assertEqual(events[6]["title"], "Pull request merged")
        self.assertNotIn(secret, str(events))

    def test_ci_watch_reports_failure_timeout_and_cancellation_truthfully(self):
        def failed_run(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv,
                1,
                '[{"name":"tests","state":"FAILURE","bucket":"fail"}]',
                "",
            )

        with tempfile.TemporaryDirectory() as tmp:
            failed_events = []
            failed = GitHubAdapter(
                Path(tmp), run=failed_run, on_event=failed_events.append
            ).watch_pr_checks(3, poll_interval=0, timeout=1)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed_events[-1]["status"], "error")

            clock = [0.0]

            def sleep(seconds):
                clock[0] += max(0.1, seconds)

            def pending_run(argv, **kwargs):
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    '[{"name":"tests","state":"PENDING","bucket":"pending"}]',
                    "",
                )

            timeout_events = []
            timed_out = GitHubAdapter(
                Path(tmp), run=pending_run, on_event=timeout_events.append
            ).watch_pr_checks(
                4,
                poll_interval=0.5,
                timeout=1,
                sleep=sleep,
                monotonic=lambda: clock[0],
            )
            self.assertEqual(timed_out["status"], "timed_out")
            self.assertEqual(timeout_events[-1]["status"], "warning")

            cancelled_events = []
            cancel = threading.Event()
            cancel.set()
            cancelled = GitHubAdapter(
                Path(tmp), run=pending_run, on_event=cancelled_events.append
            ).watch_pr_checks(5, cancel=cancel)
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(cancelled_events[-1]["status"], "cancelled")

    def test_activity_callback_failure_never_breaks_the_operation(self):
        def broken_callback(_event):
            raise RuntimeError("display closed")

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        with tempfile.TemporaryDirectory() as tmp:
            result = AgentComputerInterface(
                Path(tmp), run=fake_run, on_event=broken_callback
            ).run_tests(["pytest"], scope="focused")

        self.assertTrue(result.ok)

    def test_ci_transport_error_emits_error_instead_of_fake_pending_state(self):
        events = []

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "not authenticated")

        with tempfile.TemporaryDirectory() as tmp:
            adapter = GitHubAdapter(Path(tmp), run=fake_run, on_event=events.append)
            with self.assertRaises(RuntimeError):
                adapter.watch_pr_checks(9, poll_interval=0, timeout=1)

        self.assertEqual(events[-1]["status"], "error")
        self.assertEqual(events[-1]["title"], "CI watch failed")

    def test_empty_pr_create_output_finishes_event_as_error(self):
        events = []

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            adapter = GitHubAdapter(Path(tmp), run=fake_run, on_event=events.append)
            with self.assertRaises(RuntimeError):
                adapter.create_pr(
                    title="Fix", body="Body", head="codex/fix", base="main"
                )

        self.assertEqual(events[-1]["status"], "error")
        self.assertEqual(events[0]["id"], events[-1]["id"])


class EventSchemaV2Tests(unittest.TestCase):
    """Schema v2 (docs/AI_ACTIVITY_UX.md): requestId/phase/channel/group."""

    def test_v1_payload_shape_is_unchanged_when_v2_fields_absent(self):
        event = make_event("tool_call", "running", "Used tool")
        self.assertEqual(
            sorted(event),
            [
                "detail",
                "durationMs",
                "id",
                "metadata",
                "status",
                "timestamp",
                "title",
                "type",
            ],
        )

    def test_v2_fields_serialize_with_wire_names(self):
        event = make_event(
            "streaming",
            "running",
            "Streaming response",
            request_id="req-1",
            phase="stream",
            channel="status",
            group="req-1:g0",
        )
        self.assertEqual(event["requestId"], "req-1")
        self.assertEqual(event["phase"], "stream")
        self.assertEqual(event["channel"], "status")
        self.assertEqual(event["group"], "req-1:g0")

    def test_unknown_channel_falls_back_to_feed(self):
        event = make_event("streaming", "running", "x", channel="popup")
        self.assertEqual(event["channel"], "feed")

    def test_derived_id_is_stable_and_request_scoped(self):
        self.assertEqual(derived_id("req-1", "stream"), "req-1:stream")
        self.assertEqual(derived_id("req-1", "stream"), derived_id("req-1", "stream"))
        self.assertNotEqual(
            derived_id("req-1", "stream"), derived_id("req-2", "stream")
        )

    def test_derived_id_coalesces_repeated_states_to_one_event_id(self):
        stream_id = derived_id("req-1", "stream")
        first = make_event(
            "streaming", "running", "Streaming response", event_id=stream_id
        )
        final = make_event(
            "streaming", "success", "Response received", event_id=stream_id
        )
        self.assertEqual(first["id"], final["id"])

    def test_emit_event_forwards_v2_fields(self):
        events = []
        emit_event(
            events.append,
            "provider_request",
            "success",
            "Connected to Claude",
            request_id="req-9",
            channel="status",
            event_id=derived_id("req-9", "connect"),
        )
        self.assertEqual(events[0]["id"], "req-9:connect")
        self.assertEqual(events[0]["requestId"], "req-9")
        self.assertEqual(events[0]["channel"], "status")


def _claude_stream_fixture() -> list[str]:
    """A realistic multi-chunk stream: 2 system lines, text deltas, a run of
    two reads, one command, more text, then the result line."""
    return [
        json.dumps({"type": "system", "model": "claude-opus"}),
        json.dumps({"type": "system", "subtype": "warning"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hel"}]},
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "a.py"},
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "b.py"},
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "pytest"},
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "lo"}]},
            }
        ),
        json.dumps({"type": "result", "total_cost_usd": 0.01, "result": "Hello"}),
    ]


class ActivitySessionClaudeStreamTests(unittest.TestCase):
    """#223: the stream path emits stable derived ids that coalesce."""

    def _run_fixture(self) -> list[dict]:
        session = ActivitySession("req-1")
        events: list[dict] = []
        for line in _claude_stream_fixture():
            events.extend(session.parse_claude_line(line)["events"])
        return events

    def test_connect_fires_once_on_the_status_channel(self):
        events = self._run_fixture()
        connects = [e for e in events if e["type"] == "provider_request"]
        self.assertEqual(len(connects), 1)
        self.assertEqual(connects[0]["id"], "req-1:connect")
        self.assertEqual(connects[0]["channel"], "status")
        self.assertEqual(connects[0]["requestId"], "req-1")
        self.assertIn("claude-opus", connects[0]["title"])

    def test_stream_chunks_share_one_id_and_finish_with_real_duration(self):
        events = self._run_fixture()
        stream = [e for e in events if e["type"] == "streaming"]
        self.assertEqual(len(stream), 3)  # two running chunks + the finish
        self.assertEqual({e["id"] for e in stream}, {"req-1:stream"})
        self.assertEqual(
            [e["status"] for e in stream], ["running", "running", "success"]
        )
        final = stream[-1]
        self.assertEqual(final["title"], "Response received")
        self.assertIsInstance(final["durationMs"], int)
        self.assertGreaterEqual(final["durationMs"], 0)
        self.assertIn("chars", final["detail"])

    def test_tool_events_get_sequential_ids_and_type_run_groups(self):
        events = self._run_fixture()
        tools = [e for e in events if e["type"] in ("file_read", "command_run")]
        self.assertEqual(
            [e["id"] for e in tools],
            ["req-1:tool:0", "req-1:tool:1", "req-1:tool:2"],
        )
        # Two consecutive reads share a group; the command starts a new one.
        self.assertEqual(
            [e["group"] for e in tools], ["req-1:g0", "req-1:g0", "req-1:g1"]
        )

    def test_timestamps_are_real_and_monotonic(self):
        events = self._run_fixture()
        stamps = [e["timestamp"] for e in events]
        self.assertEqual(stamps, sorted(stamps))

    def test_provider_error_flips_the_stream_row_to_error(self):
        session = ActivitySession("req-2")
        session.parse_claude_line(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "par"}]},
                }
            )
        )
        part = session.parse_claude_line(
            json.dumps({"type": "result", "is_error": True, "error": "boom"})
        )
        self.assertTrue(part["error"])
        self.assertEqual(part["events"][-1]["id"], "req-2:stream")
        self.assertEqual(part["events"][-1]["status"], "error")
        self.assertEqual(part["events"][-1]["title"], "Streaming interrupted")

    def test_result_without_streamed_text_closes_no_stream_row(self):
        session = ActivitySession("req-3")
        part = session.parse_claude_line(
            json.dumps({"type": "result", "result": "answer"})
        )
        self.assertEqual(part["events"], [])
        self.assertEqual(part["text"], "answer")

    def test_v1_wrapper_keeps_stateless_per_line_behavior(self):
        line = json.dumps({"type": "system", "model": "m"})
        first = parse_claude_line(line)["events"][0]
        second = parse_claude_line(line)["events"][0]
        self.assertNotEqual(first["id"], second["id"])  # v1: random per line
        self.assertNotIn("requestId", first)
        self.assertNotIn("channel", first)

    def test_session_minted_request_id_still_coalesces(self):
        session = ActivitySession()
        chunk = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "x"}]},
            }
        )
        first = session.parse_claude_line(chunk)["events"][0]
        second = session.parse_claude_line(chunk)["events"][0]
        self.assertEqual(first["id"], second["id"])


class ActivitySessionCodexStreamTests(unittest.TestCase):
    """#224: Codex started/completed pairs coalesce into one measured row."""

    @staticmethod
    def _item_line(kind: str, item_id: str = "item_0") -> str:
        return json.dumps(
            {
                "type": kind,
                "item": {
                    "type": "command_execution",
                    "id": item_id,
                    "command": "pytest",
                },
            }
        )

    def test_started_and_completed_share_one_id_with_measured_duration(self):
        session = ActivitySession("req-1")
        started = session.parse_codex_line(self._item_line("item.started"))
        completed = session.parse_codex_line(self._item_line("item.completed"))
        first, final = started["events"][0], completed["events"][0]
        self.assertEqual(first["id"], "req-1:codex:item_0")
        self.assertEqual(final["id"], first["id"])
        self.assertEqual([first["status"], final["status"]], ["running", "success"])
        self.assertIsInstance(final["durationMs"], int)
        self.assertGreaterEqual(final["durationMs"], 0)

    def test_connect_fires_once_across_thread_and_session_lines(self):
        session = ActivitySession("req-2")
        first = session.parse_codex_line(json.dumps({"type": "thread.started"}))
        second = session.parse_codex_line(json.dumps({"type": "session.created"}))
        self.assertEqual(len(first["events"]), 1)
        self.assertEqual(first["events"][0]["id"], "req-2:connect")
        self.assertEqual(first["events"][0]["channel"], "status")
        self.assertEqual(second["events"], [])

    def test_legacy_exec_begin_end_coalesce_fifo(self):
        session = ActivitySession("req-3")
        begin = session.parse_codex_line(
            json.dumps({"msg": {"type": "exec_command_begin", "command": ["ls"]}})
        )["events"][0]
        end = session.parse_codex_line(
            json.dumps({"msg": {"type": "exec_command_end", "command": ["ls"]}})
        )["events"][0]
        self.assertEqual(begin["id"], end["id"])
        self.assertEqual([begin["status"], end["status"]], ["running", "success"])
        self.assertGreaterEqual(end["durationMs"], 0)

    def test_failed_turn_flips_open_items_to_error(self):
        session = ActivitySession("req-4")
        session.parse_codex_line(self._item_line("item.started"))
        part = session.parse_codex_line(
            json.dumps({"type": "turn.failed", "error": {"message": "boom"}})
        )
        self.assertTrue(part["error"])
        flipped = [e for e in part["events"] if e["id"] == "req-4:codex:item_0"]
        self.assertEqual(len(flipped), 1)
        self.assertEqual(flipped[0]["status"], "error")
        self.assertGreaterEqual(flipped[0]["durationMs"], 0)
        self.assertEqual(part["events"][-1]["type"], "error")

    def test_completed_without_started_still_renders_one_row(self):
        session = ActivitySession("req-5")
        part = session.parse_codex_line(self._item_line("item.completed"))
        event = part["events"][0]
        self.assertEqual(event["id"], "req-5:codex:item_0")
        self.assertEqual(event["status"], "success")
        self.assertIsNone(event["durationMs"])

    def test_v1_wrapper_keeps_two_events_with_random_ids(self):
        first = parse_codex_line(self._item_line("item.started"))["events"][0]
        second = parse_codex_line(self._item_line("item.completed"))["events"][0]
        self.assertNotEqual(first["id"], second["id"])  # v1: no coalescing
        self.assertNotIn("requestId", first)


if __name__ == "__main__":
    unittest.main()
