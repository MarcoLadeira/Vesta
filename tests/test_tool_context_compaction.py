"""Tests for adaptive context compaction in the tool-loop controller (Task 5).

Compaction keeps a long run's context bounded while preserving protocol-atom
integrity (a tool result is never separated from its originating call) and a
bounded record of earlier evidence.
"""

from __future__ import annotations

import json
import unittest

from vestahub.tool_loop import (
    ChatTurn,
    ToolLoopController,
    ToolLoopPolicy,
    ToolLoopState,
    ToolProtocolAtom,
    compact_context,
)


def _atom(turn_index: int, *, size: int = 4000, content: str = "x") -> ToolProtocolAtom:
    body = content * size
    return ToolProtocolAtom(
        turn_index=turn_index,
        assistant_content="",
        tool_calls=({"id": f"c{turn_index}", "function": {"name": "read_file"}},),
        observations=(
            {
                "call_id": f"c{turn_index}",
                "tool": "read_file",
                "ok": True,
                "content": body,
            },
        ),
    )


class CompactContextTests(unittest.TestCase):
    def test_small_context_is_left_untouched(self):
        state = ToolLoopState(base_messages=[{"role": "user", "content": "hi"}])
        state.atoms = [_atom(1, size=10), _atom(2, size=10)]
        report = compact_context(state, ToolLoopPolicy())
        self.assertFalse(report.compacted)
        self.assertEqual(len(state.atoms), 2)

    def test_large_context_folds_to_bounded_retained_atoms(self):
        policy = ToolLoopPolicy(compaction_char_threshold=20_000, retained_atoms=2)
        state = ToolLoopState(base_messages=[{"role": "user", "content": "hi"}])
        state.atoms = [_atom(i) for i in range(1, 9)]
        report = compact_context(state, policy)
        self.assertTrue(report.compacted)
        self.assertLessEqual(len(state.atoms), policy.retained_atoms)
        self.assertLess(report.chars_after, report.chars_before)
        self.assertTrue(state.summaries)

    def test_protocol_atoms_are_never_split(self):
        policy = ToolLoopPolicy(compaction_char_threshold=20_000, retained_atoms=2)
        state = ToolLoopState(base_messages=[{"role": "user", "content": "hi"}])
        state.atoms = [_atom(i) for i in range(1, 9)]
        compact_context(state, policy)
        messages = state.request_messages()
        # Every tool message must be preceded by an assistant tool_calls message.
        seen_tool_calls = False
        for message in messages:
            if message.get("role") == "assistant" and message.get("tool_calls"):
                seen_tool_calls = True
            if message.get("role") == "tool":
                self.assertTrue(
                    seen_tool_calls,
                    "a tool observation appeared without its assistant tool_calls",
                )

    def test_summary_stays_within_the_cap(self):
        policy = ToolLoopPolicy(
            compaction_char_threshold=1_000, retained_atoms=1, summary_char_cap=500
        )
        state = ToolLoopState(base_messages=[{"role": "user", "content": "hi"}])
        for i in range(1, 40):
            state.atoms.append(_atom(i, size=200))
            compact_context(state, policy)
        self.assertLessEqual(len("\n".join(state.summaries)), policy.summary_char_cap)

    def test_unicode_survives_compaction_summary(self):
        policy = ToolLoopPolicy(compaction_char_threshold=1_000, retained_atoms=1)
        state = ToolLoopState(base_messages=[{"role": "user", "content": "hi"}])
        state.atoms = [
            ToolProtocolAtom(
                turn_index=1,
                assistant_content="",
                tool_calls=({"id": "c1", "function": {"name": "read_file"}},),
                observations=(
                    {
                        "call_id": "c1",
                        "tool": "读取",
                        "ok": True,
                        "content": "内容" * 2000,
                    },
                ),
            ),
            _atom(2),
            _atom(3),
        ]
        report = compact_context(state, policy)
        self.assertTrue(report.compacted)
        # Summary is valid, serializable text (no surrogate/encoding damage).
        json.dumps(state.summaries, ensure_ascii=False)


class ReplayCompactionTests(unittest.TestCase):
    """A long run with compaction serializes far less than the naive replay."""

    def _replay(self, *, compact: bool):
        # 12 turns of large reads, then a completing decision. With compaction,
        # each request stays bounded; without it, every turn re-sends everything.
        turns = [
            ChatTurn(
                content="",
                tool_calls=(
                    {
                        "id": f"c{i}",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": f"file{i}.py"}),
                        },
                    },
                ),
                usage={},
            )
            for i in range(12)
        ]
        turns.append(
            ChatTurn(
                content=json.dumps(
                    {
                        "vesta_decision_version": 1,
                        "state": "completed",
                        "evidence": ["read_file"],
                    }
                )
            )
        )

        class BigExecutor:
            def schemas(self):
                return [{"type": "function", "function": {"name": "read_file"}}]

            def invoke_call(self, call, *, cancel=None):
                return {"ok": True, "content": "L" * 6000, "duration_ms": 1}

        threshold = 12_000 if compact else 10_000_000
        policy = ToolLoopPolicy(
            compaction_char_threshold=threshold,
            retained_atoms=2,
            observation_char_cap=6100,
        )
        iterator = iter(turns)

        def chat(messages, *, tools):
            return next(iterator)

        controller = ToolLoopController(policy)
        return controller.run(
            chat=chat,
            executor=BigExecutor(),
            base_messages=[{"role": "user", "content": "read them all"}],
            allow_mutations=False,
        )

    def test_twelve_turn_replay_is_at_most_half_the_legacy_input(self):
        bounded = self._replay(compact=True)
        legacy = self._replay(compact=False)
        self.assertGreater(legacy.cumulative_serialized_chars, 0)
        self.assertLessEqual(
            bounded.cumulative_serialized_chars,
            legacy.cumulative_serialized_chars * 0.5,
        )
        self.assertGreater(bounded.compactions, 0)


if __name__ == "__main__":
    unittest.main()
