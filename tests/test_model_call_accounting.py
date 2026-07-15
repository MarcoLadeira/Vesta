"""Honest model-call accounting for the multi-step tool loop (#334).

A tool loop re-sends the growing context on every step, so the summed token
figure a task records is only legible next to the number of provider calls it
made. These tests pin that count end to end: the loop reports it, the ledger
persists it, and the usage snapshot aggregates it — so "5 tasks, 700k tokens"
shows the "many model calls" that explains it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._helpers import make_repo

from opaihub.ledger import record_model_call
from opaihub.local_runner import FreeAPIRunner
from opaihub.usage import build_usage_snapshots


def _tool_turn(name: str, args: dict) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": __import__("json").dumps(args),
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {
            "prompt_tokens": 5000,
            "completion_tokens": 100,
            "total_tokens": 5100,
        },
    }


def _final_turn(text: str) -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 6000, "completion_tokens": 40, "total_tokens": 6040},
    }


class ToolLoopCallCountTests(unittest.TestCase):
    def test_loop_reports_the_number_of_model_calls_not_just_summed_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            runner = FreeAPIRunner(
                "https://api.example.test/v1", "gemini-3.1-flash-lite", "test-key"
            )
            # Two read-only tool turns, then a final answer: three provider calls.
            script = [
                _tool_turn("read_file", {"path": "README.md"}),
                _tool_turn("search_repository", {"query": "def"}),
                _final_turn("Done."),
            ]
            with mock.patch.object(runner, "_chat", side_effect=script):
                result = runner.complete_with_tools(
                    "Summarize the repo", project_root=root, allow_edits=False
                )

        self.assertEqual(result["text"], "Done.")
        # Three calls were made — the honest count, not 1.
        self.assertEqual(runner.last_usage["model_calls"], 3)
        # Tokens are still the truthful cumulative sum across the calls.
        self.assertEqual(runner.last_usage["tokens"], 5100 + 5100 + 6040)


class LedgerAndSnapshotTests(unittest.TestCase):
    def test_record_and_snapshot_surface_model_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            # One task that was a 12-call tool loop, plus a plain 1-call task.
            record_model_call(
                root,
                "big agent task",
                model_tier="L2",
                provider_type="free_api",
                tokens=140000,
                confirmed=True,
                model_id="free:gemini:gemini-3.1-flash-lite",
                provider_id="gemini",
                model_calls=12,
            )
            record_model_call(
                root,
                "quick question",
                model_tier="L2",
                provider_type="free_api",
                tokens=800,
                confirmed=True,
                model_id="free:gemini:gemini-3.1-flash-lite",
                provider_id="gemini",
            )
            snapshots = build_usage_snapshots(
                root,
                [{"id": "free:gemini:gemini-3.1-flash-lite", "provider": "gemini"}],
            )

        snap = snapshots[0]
        self.assertEqual(snap["taskCount"], 2)
        self.assertEqual(snap["modelCalls"], 13)  # 12 + 1
        self.assertEqual(snap["used"], 140800)

    def test_legacy_events_without_the_field_count_as_one_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            event = record_model_call(
                root,
                "task",
                model_tier="L2",
                provider_type="free_api",
                tokens=100,
                confirmed=True,
                model_id="free:gemini:m",
                provider_id="gemini",
            )
            # Simulate an older event that predates the field.
            self.assertEqual(event["model_calls"], 1)
            snapshots = build_usage_snapshots(
                root, [{"id": "free:gemini:m", "provider": "gemini"}]
            )
        self.assertEqual(snapshots[0]["modelCalls"], 1)


if __name__ == "__main__":
    unittest.main()
