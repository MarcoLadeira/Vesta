"""Tests for the continuous, checkpointed tool-loop controller (Task 5).

The controller replaces the old terminal ``tool_budget_exhausted`` behaviour:
``12`` is a maintenance checkpoint, not a quota.  A productive run continues;
an unproductive one stops honestly as ``STUCK_NO_PROGRESS``; and a claimed
completion is verified against real evidence before it is ever reported as
``COMPLETED``.
"""

from __future__ import annotations

import json
import threading
import unittest

from opaihub.completion import CompletionState
from opaihub.tool_loop import (
    ChatTurn,
    InvalidCompletionDecision,
    ToolLoopController,
    ToolLoopPolicy,
    ToolLoopProviderError,
    parse_completion_decision,
)


class FakeExecutor:
    """Scriptable stand-in for the repository tool executor."""

    def __init__(self, overrides: dict | None = None) -> None:
        self._overrides = overrides or {}
        self.invocations: list[dict] = []

    def schemas(self) -> list[dict]:
        return [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "apply_patch"}},
        ]

    def invoke_call(self, call, *, cancel=None):
        self.invocations.append(call)
        function = call.get("function", {}) if isinstance(call, dict) else {}
        name = str(function.get("name") or "unknown")
        arguments = str(function.get("arguments") or "")
        template = self._overrides.get(name, {"ok": True})
        observation = dict(template)
        observation.setdefault("message", "")
        observation.setdefault("duration_ms", 1)
        observation.setdefault(
            "content", json.dumps({"tool": name, "args": arguments}, sort_keys=True)
        )
        return observation


def tool_turn(call_id: str, name: str, arguments: str = "{}", usage=None) -> ChatTurn:
    return ChatTurn(
        content="",
        tool_calls=(
            {"id": call_id, "function": {"name": name, "arguments": arguments}},
        ),
        usage=usage
        or {"input_tokens": 5, "output_tokens": 2, "measurement": "provider"},
    )


def decision_turn(
    state="completed", summary="done", evidence=(), question=""
) -> ChatTurn:
    payload = {
        "opai_decision_version": 1,
        "state": state,
        "summary": summary,
        "evidence": list(evidence),
    }
    if question:
        payload["question"] = question
    return ChatTurn(
        content=json.dumps(payload), usage={"input_tokens": 3, "output_tokens": 1}
    )


def scripted_chat(turns):
    iterator = iter(turns)

    def chat(messages, *, tools):
        return next(iterator)

    return chat


class DecisionParsingTests(unittest.TestCase):
    def test_bare_prose_has_no_decision(self):
        self.assertIsNone(parse_completion_decision("All finished, looks good."))

    def test_valid_decision_is_parsed(self):
        decision = parse_completion_decision(
            'result {"opai_decision_version": 1, "state": "completed", '
            '"summary": "did it", "evidence": ["c1"]}'
        )
        assert decision is not None
        self.assertIs(decision.state, CompletionState.COMPLETED)
        self.assertEqual(decision.evidence, ("c1",))

    def test_unicode_summary_survives_parsing(self):
        decision = parse_completion_decision(
            '{"opai_decision_version": 1, "state": "completed", "summary": "café ✅ 完了"}'
        )
        assert decision is not None
        self.assertEqual(decision.summary, "café ✅ 完了")

    def test_wrong_version_is_invalid(self):
        with self.assertRaises(InvalidCompletionDecision):
            parse_completion_decision(
                '{"opai_decision_version": 2, "state": "completed"}'
            )

    def test_unknown_state_is_invalid(self):
        with self.assertRaises(InvalidCompletionDecision):
            parse_completion_decision(
                '{"opai_decision_version": 1, "state": "winning"}'
            )

    def test_malformed_json_block_is_invalid(self):
        with self.assertRaises(InvalidCompletionDecision):
            parse_completion_decision('{"opai_decision_version": 1, "state": ')


class ControllerCompletionTests(unittest.TestCase):
    def _run(
        self, turns, *, executor=None, policy=None, allow_mutations=True, cancel=None
    ):
        controller = ToolLoopController(policy or ToolLoopPolicy())
        return controller.run(
            chat=scripted_chat(turns),
            executor=executor or FakeExecutor(),
            base_messages=[{"role": "user", "content": "task"}],
            allow_mutations=allow_mutations,
            cancel=cancel,
        )

    def test_exactly_twelve_tools_can_still_complete(self):
        # The old budget stopped at 12; here 12 productive calls plus a final
        # decision completes — 12 is a checkpoint, not a terminal quota.
        turns = [tool_turn(f"c{i}", "apply_patch") for i in range(12)]
        turns.append(decision_turn(evidence=["apply_patch"]))
        result = self._run(turns)
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(result.model_calls, 13)
        self.assertGreaterEqual(result.milestones, 1)

    def test_productive_work_continues_well_past_twelve(self):
        turns = [tool_turn(f"c{i}", "apply_patch") for i in range(30)]
        turns.append(decision_turn(evidence=["apply_patch"]))
        result = self._run(turns)
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(result.model_calls, 31)

    def test_unique_reads_do_not_fake_goal_progress(self):
        # Endless unique reads never make goal progress: the run stops as stuck
        # instead of spinning or fabricating success.
        def chat(messages, *, tools):
            chat.n += 1
            return tool_turn(f"r{chat.n}", "read_file", f'{{"path":"f{chat.n}.py"}}')

        chat.n = 0
        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=FakeExecutor(),
            base_messages=[{"role": "user", "content": "task"}],
            allow_mutations=True,
        )
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)
        self.assertEqual(result.stopped_reason, "no_progress")

    def test_bare_prose_after_a_real_edit_completes(self):
        result = self._run([tool_turn("c1", "apply_patch"), ChatTurn(content="Done.")])
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(result.answer, "Done.")

    def test_bare_prose_without_progress_is_not_completed(self):
        result = self._run(
            [tool_turn("c1", "read_file"), ChatTurn(content="I think it's fine.")]
        )
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)

    def test_false_evidence_is_rejected(self):
        # A completion that cites evidence which never happened is not honoured.
        turns = [
            tool_turn("c1", "apply_patch"),
            decision_turn(evidence=["ghost-call-42"]),
        ]
        result = self._run(turns)
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)

    def test_grounded_evidence_is_accepted(self):
        turns = [tool_turn("c1", "apply_patch"), decision_turn(evidence=["c1"])]
        result = self._run(turns)
        self.assertIs(result.completion_state, CompletionState.COMPLETED)

    def test_read_only_task_answers_from_knowledge_without_tools(self):
        # An explain-style task that answers directly (no tools) is legitimately
        # complete — reading is optional, not required.
        result = self._run(
            [ChatTurn(content="app.py sets a value.")], allow_mutations=False
        )
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(result.answer, "app.py sets a value.")

    def test_read_only_task_completes_from_a_successful_read(self):
        turns = [tool_turn("c1", "read_file"), decision_turn(evidence=["read_file"])]
        result = self._run(turns, allow_mutations=False)
        self.assertIs(result.completion_state, CompletionState.COMPLETED)

    def test_read_only_task_without_a_successful_read_is_stuck(self):
        executor = FakeExecutor({"read_file": {"ok": False, "error_code": "NOT_FOUND"}})
        turns = [tool_turn("c1", "read_file"), decision_turn(evidence=["read_file"])]
        result = self._run(turns, executor=executor, allow_mutations=False)
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)

    def test_needs_user_input_decision_is_propagated(self):
        turns = [
            tool_turn("c1", "read_file"),
            decision_turn(state="needs_user_input", summary="", question="Which file?"),
        ]
        result = self._run(turns)
        self.assertIs(result.completion_state, CompletionState.NEEDS_USER_INPUT)
        self.assertEqual(result.user_question, "Which file?")


class ControllerRecoverableStateTests(unittest.TestCase):
    def _controller(self, **policy_kwargs):
        return ToolLoopController(ToolLoopPolicy(**policy_kwargs))

    def _base(self):
        return [{"role": "user", "content": "task"}]

    def test_cancellation_between_calls_returns_cancelled(self):
        cancel = threading.Event()

        def chat(messages, *, tools):
            cancel.set()  # cancel before the tool executes
            return tool_turn("c1", "apply_patch")

        result = self._controller().run(
            chat=chat,
            executor=FakeExecutor(),
            base_messages=self._base(),
            cancel=cancel,
        )
        self.assertIs(result.completion_state, CompletionState.CANCELLED)

    def test_provider_error_is_retryable(self):
        def chat(messages, *, tools):
            raise ToolLoopProviderError("503 upstream")

        result = self._controller().run(
            chat=chat, executor=FakeExecutor(), base_messages=self._base()
        )
        self.assertIs(result.completion_state, CompletionState.RETRYABLE_PROVIDER_ERROR)
        self.assertIn("503", result.last_error)

    def test_external_ceiling_stops_without_slicing_a_batch(self):
        # A deprecated external ceiling is recoverable and never runs a partial
        # batch or fakes success.
        batch = ChatTurn(
            content="",
            tool_calls=(
                {"id": "a", "function": {"name": "apply_patch", "arguments": "{}"}},
                {"id": "b", "function": {"name": "read_file", "arguments": "{}"}},
            ),
        )
        executor = FakeExecutor()
        result = self._controller(max_tool_calls=1).run(
            chat=scripted_chat([batch]),
            executor=executor,
            base_messages=self._base(),
        )
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)
        self.assertEqual(result.stopped_reason, "external_ceiling")
        self.assertEqual(executor.invocations, [])  # no partial, half-applied turn

    def test_two_invalid_decisions_stop_as_stuck(self):
        bad = ChatTurn(content='{"opai_decision_version": 1, "state": "elated"}')
        result = self._controller().run(
            chat=scripted_chat([bad, bad]),
            executor=FakeExecutor(),
            base_messages=self._base(),
        )
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)
        self.assertEqual(result.stopped_reason, "invalid_decision")

    def test_invalid_decision_is_recovered_when_the_retry_is_valid(self):
        bad = ChatTurn(content='{"opai_decision_version": 1, "state": "elated"}')
        good = decision_turn(evidence=["apply_patch"])
        result = self._controller().run(
            chat=scripted_chat([tool_turn("c1", "apply_patch"), bad, good]),
            executor=FakeExecutor(),
            base_messages=self._base(),
        )
        self.assertIs(result.completion_state, CompletionState.COMPLETED)

    def test_controller_timeout_is_stuck_not_success(self):
        ticks = iter([0.0] + [1000.0] * 10)

        def clock():
            try:
                return next(ticks)
            except StopIteration:
                return 1000.0

        controller = ToolLoopController(
            ToolLoopPolicy(max_active_seconds=1.0), clock=clock
        )

        def chat(messages, *, tools):
            return tool_turn("c1", "apply_patch")

        result = controller.run(
            chat=chat, executor=FakeExecutor(), base_messages=self._base()
        )
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)
        self.assertEqual(result.stopped_reason, "controller_timeout")


class ControllerTraceTests(unittest.TestCase):
    def test_duplicate_and_id_less_calls_are_all_traced(self):
        batch = ChatTurn(
            content="",
            tool_calls=(
                {"id": "dup", "function": {"name": "apply_patch", "arguments": "{}"}},
                {"id": "dup", "function": {"name": "apply_patch", "arguments": "{}"}},
                {"function": {"name": "read_file", "arguments": "{}"}},
            ),
        )
        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=scripted_chat([batch, decision_turn(evidence=["apply_patch"])]),
            executor=FakeExecutor(),
            base_messages=[{"role": "user", "content": "task"}],
        )
        self.assertEqual(len(result.tool_trace), 3)
        self.assertIs(result.completion_state, CompletionState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
