"""Tests for the activity/cancellation core (opai.activity) — pure, no display."""

from __future__ import annotations

import unittest

from opai.activity import (
    STILL_WORKING_S,
    TAKING_LONGER_S,
    classify_error,
    error_card,
    make_event,
    parse_claude_line,
    parse_claude_stream,
    should_apply,
    stage_message,
)


class EventModelTests(unittest.TestCase):
    def test_make_event_has_id_and_timestamp(self):
        ev = make_event("streaming", "running", "Streaming")
        self.assertTrue(ev["id"])
        self.assertEqual(ev["type"], "streaming")
        self.assertEqual(ev["status"], "running")
        self.assertIsInstance(ev["timestamp"], int)

    def test_unknown_type_and_status_degrade(self):
        ev = make_event("nope", "weird", "x")
        self.assertEqual(ev["type"], "tool_call")
        self.assertEqual(ev["status"], "running")


class StaleGuardTests(unittest.TestCase):
    def test_only_matching_request_applies(self):
        self.assertTrue(should_apply("abc", "abc"))
        self.assertFalse(should_apply("abc", "xyz"))

    def test_none_current_never_applies(self):
        # A cancelled request sets current to None → all late output ignored.
        self.assertFalse(should_apply(None, "abc"))
        self.assertFalse(should_apply("", "abc"))
        self.assertFalse(should_apply("abc", None))


class ThresholdTests(unittest.TestCase):
    def test_waiting_then_taking_longer_then_still_working(self):
        early = stage_message(1, model_label="Claude · Opus")
        self.assertFalse(early["suggest_faster"])
        self.assertIn("Waiting", early["stage"])

        mid = stage_message(TAKING_LONGER_S + 1, model_label="Claude · Opus")
        self.assertTrue(mid["suggest_faster"])
        self.assertIn("longer than usual", mid["reassurance"])
        self.assertEqual(mid["severity"], "warning")

        late = stage_message(STILL_WORKING_S + 1, model_label="Opus")
        self.assertIn("Still working", late["stage"])
        self.assertTrue(late["suggest_faster"])

    def test_streaming_overrides_waiting(self):
        sm = stage_message(999, streaming=True, model_label="Opus")
        self.assertEqual(sm["stage"], "Streaming response")
        self.assertFalse(sm["suggest_faster"])


class ErrorMapperTests(unittest.TestCase):
    def test_known_status_is_actionable(self):
        # The short name is the provider ("Claude" from "Claude · Opus").
        card = error_card("account_timeout", model_label="Claude · Opus")
        self.assertIn("Claude", card["title"])
        self.assertIn("retry", card["actions"])
        self.assertNotIn("{model}", card["title"])

    def test_cancelled_is_neutral_not_scary(self):
        card = error_card("cancelled")
        self.assertEqual(card["tone"], "neutral")

    def test_unknown_status_has_fallback(self):
        card = error_card("kaboom")
        self.assertTrue(card["title"])
        self.assertIn("retry", card["actions"])

    def test_detail_is_redacted_and_capped(self):
        card = error_card(
            "account_error", detail="boom --dangerously-skip-permissions " + "x" * 5000
        )
        self.assertNotIn("--dangerously-skip-permissions", card["detail"])
        self.assertLessEqual(len(card["detail"]), 2000)

    def test_detail_redacts_credentials_and_duplicate_errors(self):
        repeated = "Bearer sk-secretvalue123\nBearer sk-secretvalue123"

        card = error_card("unauthorized", detail=repeated)

        self.assertNotIn("sk-secretvalue123", card["detail"])
        self.assertEqual(card["detail"].count("[REDACTED]"), 1)

    def test_classify_error_maps_common_strings(self):
        self.assertEqual(classify_error("HTTP 429 rate limit exceeded"), "rate_limit")
        self.assertEqual(
            classify_error("401 Unauthorized: not logged in"), "unauthorized"
        )
        self.assertEqual(classify_error("network connection reset"), "network")
        self.assertEqual(
            classify_error("context window exceeded, too long"), "context_too_large"
        )


class ClaudeStreamParserTests(unittest.TestCase):
    def test_text_delta_and_streaming_event(self):
        line = (
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Hi"}]}}'
        )
        part = parse_claude_line(line)
        self.assertEqual(part["text"], "Hi")
        self.assertTrue(any(e["type"] == "streaming" for e in part["events"]))

    def test_tool_use_becomes_typed_event(self):
        line = (
            '{"type":"assistant","message":{"content":['
            '{"type":"tool_use","name":"Read","input":{"file_path":"a.py"}},'
            '{"type":"tool_use","name":"Bash","input":{"command":"pytest"}}]}}'
        )
        part = parse_claude_line(line)
        types = [e["type"] for e in part["events"]]
        self.assertIn("file_read", types)
        self.assertIn("command_run", types)
        titles = " ".join(e["title"] for e in part["events"])
        self.assertIn("a.py", titles)
        self.assertIn("pytest", titles)

    def test_result_carries_cost_and_done(self):
        part = parse_claude_line(
            '{"type":"result","total_cost_usd":0.5,"result":"final"}'
        )
        self.assertEqual(part["cost"], 0.5)
        self.assertTrue(part["done"])

    def test_malformed_line_degrades_to_text_not_crash(self):
        part = parse_claude_line("not json at all")
        self.assertEqual(part["text"], "not json at all")
        self.assertEqual(part["events"], [])
        self.assertEqual(parse_claude_line("")["text"], "")

    def test_parse_whole_stream(self):
        lines = [
            '{"type":"system","model":"claude-opus"}',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello "}]}}',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"world"}]}}',
            '{"type":"result","total_cost_usd":0.02}',
        ]
        agg = parse_claude_stream(lines)
        self.assertEqual(agg["text"], "Hello world")
        self.assertEqual(agg["cost"], 0.02)
        self.assertTrue(agg["done"])


class CodexStreamParserTests(unittest.TestCase):
    def _parse(self, line):
        from opai.activity import parse_codex_line

        return parse_codex_line(line)

    def test_agent_message_text_only_on_completion(self):
        started = self._parse(
            '{"type":"item.started","item":{"type":"agent_message","text":"partial"}}'
        )
        self.assertEqual(started["text"], "")
        done = self._parse(
            '{"type":"item.completed","item":{"type":"agent_message","text":"final answer"}}'
        )
        self.assertEqual(done["text"], "final answer")

    def test_command_execution_becomes_command_run_event(self):
        part = self._parse(
            '{"type":"item.completed","item":{"type":"command_execution","command":"pytest -q"}}'
        )
        self.assertEqual(part["events"][0]["type"], "command_run")
        self.assertIn("pytest -q", part["events"][0]["title"])
        self.assertEqual(part["events"][0]["status"], "success")

    def test_file_change_and_connect_and_done(self):
        edit = self._parse(
            '{"type":"item.started","item":{"type":"file_change","path":"src/app.py"}}'
        )
        self.assertEqual(edit["events"][0]["type"], "file_edit")
        boot = self._parse('{"type":"thread.started"}')
        self.assertEqual(boot["events"][0]["type"], "provider_request")
        self.assertIn("Codex", boot["events"][0]["title"])
        done = self._parse('{"type":"turn.completed","usage":{"input_tokens":10}}')
        self.assertTrue(done["done"])
        # Codex never reports dollar cost — honest None, not zero.
        self.assertIsNone(done["cost"])

    def test_turn_failed_surfaces_an_error_event(self):
        part = self._parse('{"type":"turn.failed","error":{"message":"boom"}}')
        self.assertEqual(part["events"][0]["type"], "error")
        self.assertIn("boom", part["events"][0]["title"])

    def test_proto_exec_command_shape_is_tolerated(self):
        part = self._parse(
            '{"id":"1","msg":{"type":"exec_command_begin","command":["git","status"]}}'
        )
        self.assertEqual(part["events"][0]["type"], "command_run")
        self.assertIn("git status", part["events"][0]["title"])

    def test_noise_is_skipped_never_leaked_as_text(self):
        # Non-JSON CLI noise must not pollute the answer (unlike claude, where
        # raw text can be a legitimate delta).
        self.assertEqual(self._parse("plain progress log line")["text"], "")
        self.assertEqual(self._parse('{"type":"totally.unknown"}')["events"], [])
        self.assertEqual(self._parse("")["text"], "")


if __name__ == "__main__":
    unittest.main()
