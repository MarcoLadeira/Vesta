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
from opaihub.deadlines import DeadlineBudget, TASK_DEADLINE
from opaihub.tool_loop import (
    ChatTurn,
    InvalidCompletionDecision,
    ToolLoopController,
    ToolLoopPolicy,
    ToolLoopProviderError,
    ToolLoopTaskDeadlineExceeded,
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
        # (Arguments vary per call: identical repeated calls are anti-thrash
        # stopped by design — see ControllerDuplicateSuccessTests.)
        turns = [
            tool_turn(f"c{i}", "apply_patch", f'{{"patch":"p{i}"}}') for i in range(12)
        ]
        turns.append(decision_turn(evidence=["apply_patch"]))
        result = self._run(turns)
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(result.model_calls, 13)
        self.assertGreaterEqual(result.milestones, 1)

    def test_productive_work_continues_well_past_twelve(self):
        turns = [
            tool_turn(f"c{i}", "apply_patch", f'{{"patch":"p{i}"}}') for i in range(30)
        ]
        turns.append(decision_turn(evidence=["apply_patch"]))
        result = self._run(turns)
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(result.model_calls, 31)

    def test_unique_reads_do_not_fake_goal_progress(self):
        # Endless unique reads never make goal progress: the run stops as stuck
        # instead of spinning or fabricating success.
        #
        # Since #569 the *reason* is `exploration_limit`, not `no_progress`,
        # and that distinction is exactly what the evidence ledger buys: each
        # of these reads returns something new, so the run genuinely is
        # learning rather than looping. What stops it is the absolute
        # exploration ceiling, not a stagnation verdict. Both map to
        # STUCK_NO_PROGRESS, so the honest outcome is unchanged.
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
        self.assertEqual(result.stopped_reason, "exploration_limit")

    def test_a_long_productive_investigation_is_no_longer_killed_at_twelve(self):
        # The regression this epic exists to fix. Before #569 the guard counted
        # calls since the last *edit*, so 60 distinct reads — the report's crash
        # evidence — were stopped at 12 even though every one taught something.
        import json as _json

        paths = [f"file{i}.py" for i in range(60)]
        turns = [
            tool_turn(f"c{i}", "read_file", _json.dumps({"path": path}))
            for i, path in enumerate(paths)
        ]
        turns.append(decision_turn(evidence=["read_file"]))
        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=scripted_chat(turns),
            executor=FakeExecutor(),
            base_messages=[{"role": "user", "content": "investigate"}],
            allow_mutations=False,
        )
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(result.model_calls, 61)

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
        # The provider-retry backoff is policy, not something to wait for.
        return ToolLoopController(
            ToolLoopPolicy(**policy_kwargs), sleep=lambda _seconds: None
        )

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

    def test_provider_exception_canary_is_redacted_at_the_tool_loop_boundary(self):
        secret = "sk-live-abc123SECRETKEYxyz789"

        def chat(messages, *, tools):
            raise ToolLoopProviderError(f"401 invalid key {secret}")

        result = self._controller(max_provider_retries=0).run(
            chat=chat,
            executor=FakeExecutor(),
            base_messages=self._base(),
            provider_id="gemini",
            operation_id="run-canary",
            operation_kind="model_call_free",
        )

        self.assertNotIn(secret, result.last_error)
        self.assertIsNotNone(result.boundary_error)
        payload = dict(result.boundary_error or {})
        self.assertEqual(payload["code"], "AUTH_INVALID")
        self.assertEqual(payload["operation_id"], "run-canary")
        self.assertEqual(payload["effect_continuity"], "not_dispatched")
        self.assertNotIn(secret, json.dumps(payload))

    def test_invalid_provider_decision_is_typed_and_redacted(self):
        secret = "sk-live-abc123SECRETKEYxyz789"
        invalid = decision_turn(state=secret)

        result = self._controller().run(
            chat=scripted_chat([invalid, invalid]),
            executor=FakeExecutor(),
            base_messages=self._base(),
            provider_id="gemini",
            operation_id="decision-canary",
        )

        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)
        self.assertNotIn(secret, result.last_error)
        payload = dict(result.boundary_error or {})
        self.assertEqual(payload["category"], "provider_malformed_output")
        self.assertEqual(payload["code"], "INVALID_COMPLETION_DECISION")
        self.assertNotIn(secret, json.dumps(payload))

    def test_inflight_task_deadline_is_terminal_and_never_retried(self):
        attempts = 0

        def chat(messages, *, tools):
            nonlocal attempts
            attempts += 1
            raise ToolLoopTaskDeadlineExceeded(
                elapsed_seconds=10.5,
                provider_responsive=True,
                phase="provider_turn",
                teardown_state="transport_closed",
            )

        budget = DeadlineBudget(
            task_deadline_seconds=10,
            provider_idle_timeout_seconds=3,
            lane="stable",
        )
        result = self._controller(max_provider_retries=2).run(
            chat=chat,
            executor=FakeExecutor(),
            base_messages=self._base(),
            deadline_budget=budget,
        )

        self.assertEqual(attempts, 1)
        self.assertIs(result.completion_state, CompletionState.TIMEOUT)
        self.assertEqual(result.stopped_reason, TASK_DEADLINE)
        self.assertIsNotNone(result.timeout_event)
        assert result.timeout_event is not None
        self.assertEqual(result.timeout_event["timeout_origin"], TASK_DEADLINE)
        self.assertEqual(result.timeout_event["provider_condition"], "responsive")
        self.assertEqual(result.timeout_event["phase"], "provider_turn")
        self.assertEqual(result.timeout_event["retry_safety"], "reconcile_before_retry")

    def test_a_blip_resumes_the_run_instead_of_discarding_its_progress(self):
        # A failed round-trip changes nothing about the loop's state, so the
        # turn can be re-issued. Losing an entire long run to a momentary 503
        # was a large part of "sometimes I can do the task, sometimes I can't".
        attempts: list[int] = []

        def chat(messages, *, tools):
            attempts.append(len(attempts))
            if len(attempts) == 1:
                raise ToolLoopProviderError("503 upstream")
            if len(attempts) == 2:
                return tool_turn("c1", "apply_patch")
            return decision_turn(evidence=["c1"])

        executor = FakeExecutor()
        result = self._controller().run(
            chat=chat, executor=executor, base_messages=self._base()
        )
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(len(executor.invocations), 1)
        # The failed round-trip produced nothing, so it is not billed as one.
        self.assertEqual(result.model_calls, 2)

    def test_the_retry_budget_is_bounded(self):
        attempts: list[int] = []

        def chat(messages, *, tools):
            attempts.append(len(attempts))
            raise ToolLoopProviderError("503 upstream")

        result = self._controller(max_provider_retries=2).run(
            chat=chat, executor=FakeExecutor(), base_messages=self._base()
        )
        self.assertIs(result.completion_state, CompletionState.RETRYABLE_PROVIDER_ERROR)
        self.assertEqual(len(attempts), 3, "one attempt plus two retries")

    def test_stop_wins_over_the_retry(self):
        cancel = threading.Event()

        def chat(messages, *, tools):
            cancel.set()
            raise ToolLoopProviderError("503 upstream")

        result = self._controller().run(
            chat=chat,
            executor=FakeExecutor(),
            base_messages=self._base(),
            cancel=cancel,
        )
        # Stopping is the user's decision; a retry must never override it.
        self.assertIs(result.completion_state, CompletionState.RETRYABLE_PROVIDER_ERROR)

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

    def test_the_deadline_measures_time_without_progress_not_total_elapsed(self):
        """A run still producing new evidence must not be stopped for elapsed time.

        The clock ran from the start of the turn, so a ten-minute budget stopped
        a run for *taking* ten minutes even while every step was still teaching
        it something new -- the user's real request was killed mid-work for
        making progress too slowly. Coding tasks legitimately run for hours.

        Each step here takes 5s against a 10s budget, so no single gap without
        progress is ever over -- but the run's *total* elapsed time reaches 35s,
        which the old rule would have stopped at the third step. Every tool
        result is distinct, so the evidence high-water mark rises each time.
        """

        elapsed = iter(float(step * 5) for step in range(200))

        def clock():
            try:
                return next(elapsed)
            except StopIteration:  # pragma: no cover - generous supply above
                return 20_000.0

        controller = ToolLoopController(
            ToolLoopPolicy(max_active_seconds=10.0), clock=clock
        )
        turns = [
            tool_turn(f"c{i}", "read_file", f'{{"path": "f{i}.py"}}') for i in range(5)
        ]
        # Real work ends in a change, so the completion claim is legitimate and
        # the false-completion guard is not what this test is measuring.
        turns.append(tool_turn("c5", "apply_patch", '{"path": "f0.py"}'))
        turns.append(decision_turn(evidence=["apply_patch"]))

        result = controller.run(
            chat=scripted_chat(turns),
            executor=FakeExecutor(),
            base_messages=self._base(),
        )

        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertNotEqual(result.stopped_reason, "controller_timeout")

    def test_the_deadline_still_stops_a_run_that_stops_making_progress(self):
        """The backstop must remain real: no new evidence, and the clock wins.

        Same 5s-per-step clock as above, so the difference is purely that this
        run keeps making the identical call and never earns a reset.
        """

        elapsed = iter(float(step * 5) for step in range(200))

        def clock():
            try:
                return next(elapsed)
            except StopIteration:  # pragma: no cover - generous supply above
                return 20_000.0

        controller = ToolLoopController(
            ToolLoopPolicy(max_active_seconds=10.0), clock=clock
        )

        def chat(messages, *, tools):
            # The identical call every time: repetition scores nothing, so the
            # evidence high-water mark never moves and the clock is never reset.
            return tool_turn("c1", "read_file", '{"path": "same.py"}')

        result = controller.run(
            chat=chat, executor=FakeExecutor(), base_messages=self._base()
        )

        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)

    def test_task_deadline_does_not_reset_when_active_work_makes_progress(self):
        """The hard task clock is independent from the no-progress backstop."""

        elapsed = iter([0.0, 5.0, 11.0])

        def clock():
            try:
                return next(elapsed)
            except StopIteration:  # pragma: no cover - terminal before exhaustion
                return 11.0

        budget = DeadlineBudget(
            task_deadline_seconds=10.0,
            provider_idle_timeout_seconds=3.0,
            lane="long_horizon",
        )
        controller = ToolLoopController(
            ToolLoopPolicy(max_active_seconds=1000.0), clock=clock
        )
        result = controller.run(
            chat=scripted_chat(
                [
                    tool_turn("c1", "read_file", '{"path": "new-evidence.py"}'),
                    tool_turn("c2", "apply_patch", '{"path": "changed.py"}'),
                ]
            ),
            executor=FakeExecutor(),
            base_messages=self._base(),
            deadline_budget=budget,
        )

        self.assertIs(result.completion_state, CompletionState.TIMEOUT)
        self.assertEqual(result.stopped_reason, TASK_DEADLINE)
        self.assertIsNotNone(result.timeout_event)
        assert result.timeout_event is not None
        self.assertEqual(result.timeout_event["timeout_origin"], TASK_DEADLINE)
        self.assertEqual(result.timeout_event["deadline_budget_id"], budget.budget_id)
        self.assertEqual(result.timeout_event["provider_condition"], "responsive")
        self.assertTrue(result.timeout_event["progress_observed"])


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


class ControllerGuardTests(unittest.TestCase):
    """The per-turn guard (Task 6) runs before every provider turn."""

    def _base(self):
        return [{"role": "user", "content": "task"}]

    def test_guard_runs_before_every_provider_turn(self):
        from opaihub.execution_guard import GuardDecision, GuardOutcome

        checked: list[int] = []

        def guard(turn_index):
            checked.append(turn_index)
            return GuardDecision(GuardOutcome.ALLOW)

        turns = [tool_turn("c1", "apply_patch"), tool_turn("c2", "apply_patch")]
        turns.append(decision_turn(evidence=["apply_patch"]))
        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=scripted_chat(turns),
            executor=FakeExecutor(),
            base_messages=self._base(),
            guard=guard,
        )
        self.assertEqual(checked, [1, 2, 3])
        self.assertIs(result.completion_state, CompletionState.COMPLETED)

    def test_guard_block_stops_the_run_before_spending(self):
        from opaihub.completion import ProviderBlockedReason
        from opaihub.execution_guard import GuardDecision, GuardOutcome

        calls = {"chat": 0}

        def chat(messages, *, tools):
            calls["chat"] += 1
            return tool_turn("c1", "apply_patch")

        def guard(turn_index):
            return GuardDecision(
                GuardOutcome.BLOCKED, ProviderBlockedReason.DAILY_CAP, detail="daily"
            )

        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat, executor=FakeExecutor(), base_messages=self._base(), guard=guard
        )
        self.assertIs(result.completion_state, CompletionState.PROVIDER_BLOCKED)
        self.assertEqual(result.blocked_reason, "daily_cap")
        self.assertEqual(calls["chat"], 0)  # blocked before any provider call

    def test_guard_consent_maps_to_needs_consent(self):
        from opaihub.execution_guard import GuardDecision, GuardOutcome

        def guard(turn_index):
            return GuardDecision(GuardOutcome.NEEDS_CONSENT, detail="confirm cloud")

        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=scripted_chat([tool_turn("c1", "apply_patch")]),
            executor=FakeExecutor(),
            base_messages=self._base(),
            guard=guard,
        )
        self.assertIs(result.completion_state, CompletionState.NEEDS_CONSENT)

    def test_tool_calling_disabled_offers_no_tools(self):
        seen_tools = []

        def chat(messages, *, tools):
            seen_tools.append(tools)
            return ChatTurn(content="Answered from context.")

        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=FakeExecutor(),
            base_messages=self._base(),
            allow_mutations=False,
            tool_calling_enabled=False,
        )
        self.assertEqual(seen_tools, [[]])  # no tool schemas offered
        self.assertIs(result.completion_state, CompletionState.COMPLETED)


class ControllerDuplicateSuccessTests(unittest.TestCase):
    """F13: identical successful calls are noticed, then stopped (not run)."""

    def _base(self):
        return [{"role": "user", "content": "task"}]

    def test_second_identical_success_carries_a_notice_to_the_model(self):
        seen_messages: list[list[dict]] = []
        calls = {"n": 0}

        def chat(messages, *, tools):
            calls["n"] += 1
            seen_messages.append(messages)
            if calls["n"] <= 2:
                return tool_turn(f"c{calls['n']}", "read_file", '{"path":"a.py"}')
            return decision_turn(evidence=["read_file"])

        executor = FakeExecutor()
        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=executor,
            base_messages=self._base(),
            allow_mutations=False,
        )
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        self.assertEqual(len(executor.invocations), 2)
        # The model saw the duplicate notice in the tool observation.
        final_request = json.dumps(seen_messages[-1], default=str)
        self.assertIn("already succeeded", final_request)

    def test_third_identical_success_is_never_executed(self):
        def chat(messages, *, tools):
            return tool_turn("c1", "read_file", '{"path":"a.py"}')

        executor = FakeExecutor()
        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=executor,
            base_messages=self._base(),
            allow_mutations=False,
        )
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)
        self.assertEqual(result.stopped_reason, "repeated_success")
        # Two executions (first + noticed duplicate); the third was stopped.
        self.assertEqual(len(executor.invocations), 2)

    def test_failures_still_use_the_failure_budget_not_the_success_one(self):
        executor = FakeExecutor({"read_file": {"ok": False, "error_code": "NOPE"}})

        def chat(messages, *, tools):
            return tool_turn("c1", "read_file", '{"path":"a.py"}')

        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=executor,
            base_messages=self._base(),
            allow_mutations=False,
        )
        self.assertIs(result.completion_state, CompletionState.STUCK_NO_PROGRESS)
        self.assertEqual(result.stopped_reason, "repeated_failure")
        self.assertEqual(len(executor.invocations), 3)  # max_identical_failures


class ControllerEmptyOutputTests(unittest.TestCase):
    """F19: an ok observation with no usable payload is marked EMPTY_OUTPUT."""

    def test_empty_successful_observation_is_explained_to_the_model(self):
        seen_messages: list[list[dict]] = []
        calls = {"n": 0}

        def chat(messages, *, tools):
            calls["n"] += 1
            seen_messages.append(messages)
            if calls["n"] == 1:
                return tool_turn("c1", "run_command", '{"command":"git status"}')
            return decision_turn(evidence=["run_command"])

        executor = FakeExecutor(
            {
                "run_command": {
                    "ok": True,
                    "data": {"stdout": "", "stderr": ""},
                    "content": "",
                }
            }
        )
        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=executor,
            base_messages=[{"role": "user", "content": "task"}],
        )
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        final_request = json.dumps(seen_messages[-1], default=str)
        self.assertIn("produced no output", final_request)
        self.assertIn("--json", final_request)

    def test_nonempty_observation_is_not_marked(self):
        seen_messages: list[list[dict]] = []
        calls = {"n": 0}

        def chat(messages, *, tools):
            calls["n"] += 1
            seen_messages.append(messages)
            if calls["n"] == 1:
                return tool_turn("c1", "run_command", '{"command":"git status"}')
            return decision_turn(evidence=["run_command"])

        executor = FakeExecutor(
            {
                "run_command": {
                    "ok": True,
                    "data": {"stdout": " M app.py", "stderr": ""},
                    "content": "",
                }
            }
        )
        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=executor,
            base_messages=[{"role": "user", "content": "task"}],
        )
        self.assertIs(result.completion_state, CompletionState.COMPLETED)
        final_request = json.dumps(seen_messages[-1], default=str)
        self.assertNotIn("produced no output", final_request)


class ControllerConsentGateTests(unittest.TestCase):
    """F17: COMMAND_NEEDS_APPROVAL becomes a NEEDS_CONSENT exit with payload."""

    def test_needs_approval_observation_exits_with_command_and_reason(self):
        executor = FakeExecutor(
            {
                "run_command": {
                    "ok": False,
                    "error_code": "COMMAND_NEEDS_APPROVAL",
                    "message": "Requires explicit user confirmation before execution.",
                    "command": "git push origin main",
                    "approval_reason": "Requires explicit user confirmation before execution.",
                }
            }
        )

        def chat(messages, *, tools):
            return tool_turn("c1", "run_command", '{"command":"git push origin main"}')

        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=executor,
            base_messages=[{"role": "user", "content": "task"}],
        )
        self.assertIs(result.completion_state, CompletionState.NEEDS_CONSENT)
        self.assertEqual(result.stopped_reason, "approval_required")
        self.assertEqual(
            result.consent_payload,
            {
                "command": "git push origin main",
                "reason": "Requires explicit user confirmation before execution.",
            },
        )
        self.assertEqual(result.model_calls, 1)  # loop stopped immediately
        # The blocked call is still traced honestly.
        self.assertEqual(result.tool_trace[0]["error_code"], "COMMAND_NEEDS_APPROVAL")


if __name__ == "__main__":
    unittest.main()
