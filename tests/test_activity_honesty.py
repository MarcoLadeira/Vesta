"""Activity honesty tests (F19/F11/F13).

The timeline must never show "✓ Ran" for a command that failed or returned
nothing usable: Claude ``tool_result`` blocks and Codex completed-item exit
codes correct the optimistic tool_use rows, and repeated identical commands
are flagged instead of celebrated twice.
"""

from __future__ import annotations

import json
import unittest

from vesta.activity import ActivitySession, parse_claude_line, parse_codex_line


def _tool_use_line(tool_use_id: str, command: str, name: str = "Bash") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": name,
                        "input": {"command": command},
                    }
                ]
            },
        }
    )


def _tool_result_line(tool_use_id: str, content, *, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": content,
                        "is_error": is_error,
                    }
                ]
            },
        }
    )


class ClaudeToolResultTests(unittest.TestCase):
    def test_successful_result_with_output_keeps_the_row_green(self):
        session = ActivitySession("r1")
        use = session.parse_claude_line(_tool_use_line("t1", "gh issue view 219"))
        result = session.parse_claude_line(_tool_result_line("t1", "issue body text"))
        self.assertEqual(use["events"][0]["status"], "success")
        # No corrective event: the optimistic row was honest.
        self.assertEqual(result["events"], [])

    def test_is_error_result_flips_the_same_row_to_error(self):
        session = ActivitySession("r1")
        use = session.parse_claude_line(_tool_use_line("t1", "gh issue view 219"))
        result = session.parse_claude_line(
            _tool_result_line("t1", "gh: not found", is_error=True)
        )
        self.assertEqual(len(result["events"]), 1)
        correction = result["events"][0]
        self.assertEqual(correction["id"], use["events"][0]["id"])  # in-place upsert
        self.assertEqual(correction["status"], "error")
        self.assertIn("failed", correction["title"])
        self.assertIn("gh issue view 219", correction["title"])
        self.assertIn("gh: not found", correction["detail"])

    def test_empty_result_flips_the_row_to_warning(self):
        session = ActivitySession("r1")
        use = session.parse_claude_line(_tool_use_line("t1", "gh issue view 219"))
        result = session.parse_claude_line(_tool_result_line("t1", ""))
        correction = result["events"][0]
        self.assertEqual(correction["id"], use["events"][0]["id"])
        self.assertEqual(correction["status"], "warning")
        self.assertIn("no output returned", correction["title"])

    def test_whitespace_only_and_block_form_results_count_as_empty(self):
        for content in ("   \n ", [{"type": "text", "text": "  "}]):
            session = ActivitySession("r1")
            session.parse_claude_line(_tool_use_line("t1", "cmd"))
            result = session.parse_claude_line(_tool_result_line("t1", content))
            self.assertEqual(result["events"][0]["status"], "warning", content)

    def test_list_content_with_text_stays_green(self):
        session = ActivitySession("r1")
        session.parse_claude_line(_tool_use_line("t1", "cmd"))
        result = session.parse_claude_line(
            _tool_result_line("t1", [{"type": "text", "text": "real output"}])
        )
        self.assertEqual(result["events"], [])

    def test_unknown_tool_use_id_still_surfaces_an_honest_row(self):
        session = ActivitySession("r1")
        result = session.parse_claude_line(
            _tool_result_line("ghost", "boom", is_error=True)
        )
        self.assertEqual(result["events"][0]["status"], "error")
        self.assertIn("failed", result["events"][0]["title"])

    def test_stateless_parse_emits_corrective_followup_event(self):
        part = parse_claude_line(_tool_result_line("t1", "", is_error=False))
        self.assertEqual(part["events"][0]["status"], "warning")
        part = parse_claude_line(_tool_result_line("t1", "boom", is_error=True))
        self.assertEqual(part["events"][0]["status"], "error")
        # A successful result produces no noise.
        self.assertEqual(parse_claude_line(_tool_result_line("t1", "ok"))["events"], [])

    def test_error_detail_is_redacted(self):
        session = ActivitySession("r1")
        session.parse_claude_line(_tool_use_line("t1", "cmd"))
        result = session.parse_claude_line(
            _tool_result_line(
                "t1", "Authorization: Bearer sk-live-secret123456", is_error=True
            )
        )
        self.assertNotIn("sk-live-secret123456", result["events"][0]["detail"])


class ClaudeDuplicateCommandTests(unittest.TestCase):
    def test_second_identical_bash_command_is_a_warning(self):
        session = ActivitySession("r1")
        first = session.parse_claude_line(_tool_use_line("t1", "gh issue view 219"))
        second = session.parse_claude_line(_tool_use_line("t2", "gh issue view 219"))
        self.assertEqual(first["events"][0]["status"], "success")
        repeated = second["events"][0]
        self.assertEqual(repeated["status"], "warning")
        self.assertIn("Repeated command", repeated["title"])
        self.assertTrue(repeated["metadata"]["repeated"])

    def test_different_commands_are_not_flagged(self):
        session = ActivitySession("r1")
        session.parse_claude_line(_tool_use_line("t1", "git status"))
        other = session.parse_claude_line(_tool_use_line("t2", "git diff"))
        self.assertEqual(other["events"][0]["status"], "success")

    def test_whitespace_variants_count_as_the_same_command(self):
        session = ActivitySession("r1")
        session.parse_claude_line(_tool_use_line("t1", "git   status"))
        second = session.parse_claude_line(_tool_use_line("t2", "git status"))
        self.assertEqual(second["events"][0]["status"], "warning")

    def test_non_bash_tools_are_not_duplicate_tracked(self):
        session = ActivitySession("r1")
        session.parse_claude_line(_tool_use_line("t1", "a.py", name="Read"))
        # Read uses file_path input; same target twice is normal navigation.
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t2",
                            "name": "Read",
                            "input": {"file_path": "a.py"},
                        }
                    ]
                },
            }
        )
        second = session.parse_claude_line(line)
        self.assertEqual(second["events"][0]["status"], "success")


class CodexHonestyTests(unittest.TestCase):
    def _session_pair(self, completed_item: dict) -> list[dict]:
        session = ActivitySession("r1")
        started = {"type": "item.started", "item": dict(completed_item)}
        completed = {"type": "item.completed", "item": dict(completed_item)}
        session.parse_codex_line(json.dumps(started))
        return session.parse_codex_line(json.dumps(completed))["events"]

    def test_nonzero_exit_code_is_an_error_not_a_success(self):
        events = self._session_pair(
            {
                "id": "i1",
                "type": "command_execution",
                "command": "pytest -q",
                "exit_code": 1,
                "aggregated_output": "1 failed",
            }
        )
        self.assertEqual(events[0]["status"], "error")
        self.assertIn("exit 1", events[0]["title"])

    def test_zero_exit_with_empty_output_is_a_warning(self):
        events = self._session_pair(
            {
                "id": "i1",
                "type": "command_execution",
                "command": "gh issue view 219",
                "exit_code": 0,
                "aggregated_output": "",
            }
        )
        self.assertEqual(events[0]["status"], "warning")
        self.assertIn("no output returned", events[0]["title"])

    def test_zero_exit_with_output_stays_green(self):
        events = self._session_pair(
            {
                "id": "i1",
                "type": "command_execution",
                "command": "git status",
                "exit_code": 0,
                "aggregated_output": "On branch main",
            }
        )
        self.assertEqual(events[0]["status"], "success")

    def test_unverifiable_completion_is_left_alone(self):
        # No exit code in the payload: do not guess, keep the legacy behavior.
        events = self._session_pair(
            {"id": "i1", "type": "command_execution", "command": "make"}
        )
        self.assertEqual(events[0]["status"], "success")

    def test_stateless_completed_item_uses_exit_code(self):
        part = parse_codex_line(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "pytest",
                        "exit_code": 2,
                    },
                }
            )
        )
        self.assertEqual(part["events"][0]["status"], "error")
        self.assertIn("exit 2", part["events"][0]["title"])

    def test_legacy_exec_end_with_nonzero_exit_is_an_error(self):
        session = ActivitySession("r1")
        session.parse_codex_line(
            '{"msg":{"type":"exec_command_begin","command":["pytest"]}}'
        )
        part = session.parse_codex_line(
            '{"msg":{"type":"exec_command_end","command":["pytest"],'
            '"exit_code":3,"aggregated_output":"boom"}}'
        )
        self.assertEqual(part["events"][0]["status"], "error")
        self.assertIn("exit 3", part["events"][0]["title"])

    def test_legacy_exec_end_success_stays_green(self):
        session = ActivitySession("r1")
        session.parse_codex_line(
            '{"msg":{"type":"exec_command_begin","command":["pytest"]}}'
        )
        part = session.parse_codex_line(
            '{"msg":{"type":"exec_command_end","command":["pytest"],'
            '"exit_code":0,"aggregated_output":"ok"}}'
        )
        self.assertEqual(part["events"][0]["status"], "success")


if __name__ == "__main__":
    unittest.main()
