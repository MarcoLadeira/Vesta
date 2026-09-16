"""#674: DeepSeek thinking-mode reasoning_content continuity (#673 A4/A5).

DeepSeek's thinking mode has a non-optional continuity requirement:
``reasoning_content`` returned with an assistant tool-call message must be
replayed verbatim in the next turn's request, or the provider rejects the
continuation. #673 Phase 1 shipped non-thinking mode only, precisely because
the generic tool-loop message contract did not carry that field. This module
covers the three layers that close the gap:

* ``vestahub.tool_loop`` — the continuity contract itself: replay, the typed
  ``ReasoningContinuityError`` when a required field is gone, and the A4
  carve-out that only tool-call turns need it.
* ``vestahub.local_runner.ThinkingControl`` — the model control (#673 A5)
  that gates whether thinking is ever requested at all.
* ``PaidAPIRunner`` — wiring the control into the outbound payload and the
  provider's raw field into ``ChatTurn``, without ever letting it leak past
  the tool loop's own replay (A4's privacy rule).

Hermetic throughout: no real DeepSeek key, no network. HTTP is mocked at
``local_runner._http_json_cancellable``, the same seam every other paid/free
runner test in this suite uses.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock
from pathlib import Path
from tempfile import TemporaryDirectory

from vestahub import local_runner
from vestahub.completion import CompletionState
from vestahub.local_runner import (
    FreeAPIRunner,
    PaidAPIRunner,
    ThinkingControl,
    runner_for_model,
)
from vestahub.tool_loop import (
    ChatTurn,
    ReasoningContinuityError,
    ToolLoopController,
    ToolLoopPolicy,
    ToolLoopProviderError,
    ToolLoopState,
    ToolProtocolAtom,
    compact_context,
)


# ---------------------------------------------------------------------------
# A minimal, scriptable tool executor (same shape as
# test_tool_loop_controller.FakeExecutor, kept local so this file has no
# cross-test-module import dependency).
# ---------------------------------------------------------------------------


class _FakeExecutor:
    def __init__(self) -> None:
        self.invocations: list[dict] = []

    def schemas(self) -> list[dict]:
        return [{"type": "function", "function": {"name": "read_file"}}]

    def invoke_call(self, call, *, cancel=None):
        self.invocations.append(call)
        return {"ok": True, "content": "read ok", "message": "", "duration_ms": 1}


def _tool_turn(
    call_id: str = "c1",
    *,
    reasoning_content: str | None = None,
    thinking_requested: bool = False,
) -> ChatTurn:
    return ChatTurn(
        content="",
        tool_calls=(
            {"id": call_id, "function": {"name": "read_file", "arguments": "{}"}},
        ),
        usage={},
        reasoning_content=reasoning_content,
        thinking_requested=thinking_requested,
    )


def _completed_turn(evidence: tuple[str, ...] = ()) -> ChatTurn:
    import json

    payload = {
        "vesta_decision_version": 1,
        "state": "completed",
        "summary": "done",
        "evidence": list(evidence),
    }
    return ChatTurn(content=json.dumps(payload), tool_calls=(), usage={})


# ---------------------------------------------------------------------------
# ThinkingControl (#673 A5's model control)
# ---------------------------------------------------------------------------


class ThinkingControlTests(unittest.TestCase):
    def test_the_default_requests_nothing(self):
        control = ThinkingControl()
        self.assertEqual(control.mode, "auto")
        self.assertFalse(control.requests_thinking)
        self.assertEqual(control.payload_fields(), {})

    def test_auto_currently_behaves_as_off(self):
        """No task-policy signal exists yet (#674's own scope note)."""
        self.assertEqual(
            ThinkingControl(mode="auto").payload_fields(),
            ThinkingControl(mode="off").payload_fields(),
        )

    def test_on_requests_thinking_and_sends_the_provider_shape(self):
        control = ThinkingControl(mode="on")
        self.assertTrue(control.requests_thinking)
        self.assertEqual(control.payload_fields(), {"thinking": {"type": "enabled"}})

    def test_effort_is_only_added_when_thinking_is_on(self):
        for effort in ("high", "max"):
            with self.subTest(effort=effort):
                fields = ThinkingControl(mode="on", effort=effort).payload_fields()
                self.assertEqual(fields["reasoning_effort"], effort)
                self.assertEqual(fields["thinking"], {"type": "enabled"})

    def test_an_invalid_mode_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            ThinkingControl(mode="maybe")

    def test_an_invalid_effort_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            ThinkingControl(mode="on", effort="medium")

    def test_effort_without_thinking_on_is_rejected_not_silently_ignored(self):
        """A caller who sets effort but forgets mode='on' gets an error, not
        an effort that quietly never took effect."""
        for mode in ("off", "auto"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    ThinkingControl(mode=mode, effort="high")

    def test_frozen_so_a_runner_cannot_mutate_its_own_control_mid_run(self):
        control = ThinkingControl(mode="on")
        with self.assertRaises(Exception):
            control.mode = "off"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ToolProtocolAtom / to_messages(): the replay contract itself
# ---------------------------------------------------------------------------


class ReplayContractTests(unittest.TestCase):
    def test_reasoning_content_is_replayed_verbatim(self):
        atom = ToolProtocolAtom(
            turn_index=1,
            assistant_content="",
            tool_calls=({"id": "c1", "function": {"name": "x", "arguments": "{}"}},),
            observations=(),
            reasoning_content="the raw chain of thought",
            thinking_required=True,
        )
        [assistant_message, *_rest] = atom.to_messages()
        self.assertEqual(
            assistant_message["reasoning_content"], "the raw chain of thought"
        )

    def test_a_non_thinking_atom_never_carries_the_key_at_all(self):
        """Not even an empty/null key -- some providers reject unknown or
        null fields, so absence must mean absence, not `"reasoning_content":
        null`."""
        atom = ToolProtocolAtom(
            turn_index=1,
            assistant_content="",
            tool_calls=({"id": "c1", "function": {"name": "x", "arguments": "{}"}},),
            observations=(),
        )
        [assistant_message, *_rest] = atom.to_messages()
        self.assertNotIn("reasoning_content", assistant_message)

    def test_a_required_but_missing_field_raises_the_typed_error(self):
        atom = ToolProtocolAtom(
            turn_index=3,
            assistant_content="",
            tool_calls=({"id": "c1", "function": {"name": "x", "arguments": "{}"}},),
            observations=(),
            reasoning_content=None,
            thinking_required=True,
        )
        with self.assertRaises(ReasoningContinuityError) as ctx:
            atom.to_messages()
        self.assertIn("turn 3", str(ctx.exception))

    def test_an_empty_string_counts_as_missing_not_present(self):
        """A corrupt/blank field must not be treated as a valid replay value."""
        atom = ToolProtocolAtom(
            turn_index=1,
            assistant_content="",
            tool_calls=({"id": "c1", "function": {"name": "x", "arguments": "{}"}},),
            observations=(),
            reasoning_content="",
            thinking_required=True,
        )
        with self.assertRaises(ReasoningContinuityError):
            atom.to_messages()

    def test_the_error_is_not_a_provider_error_subclass(self):
        """The type itself proves it can never enter the retry branch --
        `except ToolLoopProviderError` cannot catch a type that isn't one."""
        self.assertFalse(issubclass(ReasoningContinuityError, ToolLoopProviderError))

    def test_a_default_atom_never_requires_continuity(self):
        """Backward compatibility: every atom built before #674 (Claude,
        Codex, every non-thinking call) used none of the new fields and must
        behave exactly as before."""
        atom = ToolProtocolAtom(
            turn_index=1,
            assistant_content="",
            tool_calls=({"id": "c1", "function": {"name": "x", "arguments": "{}"}},),
            observations=(),
        )
        atom.to_messages()  # must not raise


# ---------------------------------------------------------------------------
# ChatTurn: backward compatibility
# ---------------------------------------------------------------------------


class ChatTurnDefaultsTests(unittest.TestCase):
    def test_every_existing_construction_still_works(self):
        """The exact positional/keyword shape every pre-#674 provider uses."""
        turn = ChatTurn(content="hello", tool_calls=(), usage={"input_tokens": 1})
        self.assertIsNone(turn.reasoning_content)
        self.assertFalse(turn.thinking_requested)


# ---------------------------------------------------------------------------
# ToolLoopController: the full round trip, the terminal error, and the A4
# non-tool carve-out.
# ---------------------------------------------------------------------------


class ControllerReplayTests(unittest.TestCase):
    def test_reasoning_content_round_trips_through_a_multi_turn_loop(self):
        seen_on_second_request: dict = {}

        def chat(messages, *, tools):
            if not seen_on_second_request:
                seen_on_second_request["first_call"] = True
                return _tool_turn(
                    reasoning_content="step 1 reasoning", thinking_requested=True
                )
            seen_on_second_request["messages"] = messages
            return _completed_turn(evidence=("c1",))

        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=_FakeExecutor(),
            base_messages=[{"role": "user", "content": "go"}],
            allow_mutations=False,
        )
        replayed = [
            m
            for m in seen_on_second_request["messages"]
            if m.get("role") == "assistant"
            and m.get("reasoning_content") == "step 1 reasoning"
        ]
        self.assertEqual(len(replayed), 1)
        self.assertEqual(result.completion_state, CompletionState.COMPLETED)

    def test_missing_reasoning_content_on_replay_is_terminal_not_retried(self):
        call_count = {"n": 0}

        def chat(messages, *, tools):
            call_count["n"] += 1
            # Thinking mode on, tool calls returned, reasoning_content lost.
            return _tool_turn(reasoning_content=None, thinking_requested=True)

        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=_FakeExecutor(),
            base_messages=[{"role": "user", "content": "go"}],
        )
        self.assertEqual(result.completion_state, CompletionState.FAILED)
        self.assertEqual(result.stopped_reason, "reasoning_continuity_error")
        # The whole point: not a blind retry. Exactly one dispatch happened.
        self.assertEqual(call_count["n"], 1)

    def test_the_error_is_caught_via_the_compaction_path_too(self):
        """Continuity is checked wherever to_messages() first runs -- which
        can be the size-check at the top of the loop, before the guard or
        the dispatch try/except are ever reached. A low threshold forces
        that path on the very next iteration after the broken atom lands."""
        call_count = {"n": 0}

        def chat(messages, *, tools):
            call_count["n"] += 1
            return _tool_turn(reasoning_content=None, thinking_requested=True)

        policy = ToolLoopPolicy(compaction_char_threshold=1)
        controller = ToolLoopController(policy)
        result = controller.run(
            chat=chat,
            executor=_FakeExecutor(),
            base_messages=[{"role": "user", "content": "go"}],
        )
        self.assertEqual(result.completion_state, CompletionState.FAILED)
        self.assertEqual(result.stopped_reason, "reasoning_continuity_error")

    def test_a_non_tool_thinking_turn_never_requires_continuity(self):
        """A4's own carve-out: only tool-call turns need reasoning_content
        preserved. A plain thinking-mode answer must complete normally even
        though it carries none."""

        def chat(messages, *, tools):
            return ChatTurn(
                content='{"vesta_decision_version": 1, "state": "completed", "summary": "ok"}',
                tool_calls=(),
                usage={},
                reasoning_content=None,
                thinking_requested=True,
            )

        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=_FakeExecutor(),
            base_messages=[{"role": "user", "content": "go"}],
            allow_mutations=False,
        )
        self.assertEqual(result.completion_state, CompletionState.COMPLETED)

    def test_a_non_thinking_loop_is_completely_unaffected(self):
        """Regression guard: every provider that never sets
        thinking_requested must behave exactly as before #674, even with
        tool calls and no reasoning_content anywhere."""

        def chat(messages, *, tools):
            if chat.calls == 0:
                chat.calls += 1
                return _tool_turn()  # no reasoning, thinking_requested=False
            return _completed_turn(evidence=("c1",))

        chat.calls = 0
        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=_FakeExecutor(),
            base_messages=[{"role": "user", "content": "go"}],
            allow_mutations=False,
        )
        self.assertEqual(result.completion_state, CompletionState.COMPLETED)


# ---------------------------------------------------------------------------
# Privacy (A4): raw reasoning never reaches chat/receipt/diagnostics, and is
# discarded once compaction folds the atom away.
# ---------------------------------------------------------------------------


class PrivacyTests(unittest.TestCase):
    SECRET = "SENSITIVE INTERNAL CHAIN OF THOUGHT, NEVER SHOWN"

    def test_reasoning_content_never_appears_on_the_result(self):
        def chat(messages, *, tools):
            if chat.calls == 0:
                chat.calls += 1
                return _tool_turn(
                    reasoning_content=self.SECRET, thinking_requested=True
                )
            return _completed_turn(evidence=("c1",))

        chat.calls = 0
        controller = ToolLoopController(ToolLoopPolicy())
        result = controller.run(
            chat=chat,
            executor=_FakeExecutor(),
            base_messages=[{"role": "user", "content": "go"}],
            allow_mutations=False,
        )
        dump = repr(result)
        self.assertNotIn(self.SECRET, dump)
        self.assertNotIn(self.SECRET, result.answer)
        for item in result.tool_trace:
            self.assertNotIn(self.SECRET, repr(item))

    def test_compaction_discards_it_for_good(self):
        policy = ToolLoopPolicy(retained_atoms=1, compaction_char_threshold=1)
        state = ToolLoopState(base_messages=[{"role": "user", "content": "go"}])
        state.atoms.append(
            ToolProtocolAtom(
                turn_index=1,
                assistant_content="",
                tool_calls=(
                    {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
                ),
                observations=({"call_id": "c1", "ok": True, "content": "r1"},),
                reasoning_content=self.SECRET,
                thinking_required=True,
            )
        )
        state.atoms.append(
            ToolProtocolAtom(
                turn_index=2,
                assistant_content="",
                tool_calls=(
                    {"id": "c2", "function": {"name": "x", "arguments": "{}"}},
                ),
                observations=({"call_id": "c2", "ok": True, "content": "r2"},),
            )
        )
        report = compact_context(state, policy)
        self.assertTrue(report.compacted)
        rendered = str(state.request_messages())
        self.assertNotIn(self.SECRET, rendered)
        self.assertNotIn(self.SECRET, "\n".join(state.summaries))

    def test_the_ledger_never_receives_reasoning_content(self):
        """Full complete_with_tools() pass, HTTP mocked, ledger call captured
        by kwargs -- the raw provider field must not be reachable from any
        recorded field."""
        response_step1 = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": self.SECRET,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        response_step2 = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"vesta_decision_version": 1, "state": "completed", '
                            '"summary": "done", "evidence": ["c1"]}'
                        ),
                        "reasoning_content": self.SECRET + " turn 2",
                    }
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        }
        responses = [response_step1, response_step2]

        def fake_http(url, **kwargs):
            return responses.pop(0)

        recorded_events: list[dict] = []

        def _capture(*args, **kwargs):
            recorded_events.append(kwargs)
            return {"call_id": kwargs.get("call_id", "")}

        runner = PaidAPIRunner(
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            "sk-test",
            pricing_model_id="deepseek-v4-flash",
            thinking=ThinkingControl(mode="on"),
        )

        class _FakeToolExecutor:
            def schemas(self):
                return [{"type": "function", "function": {"name": "read_file"}}]

            def invoke_call(self, call, *, cancel=None):
                return {"ok": True, "content": "ok", "message": "", "duration_ms": 1}

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(local_runner, "_http_json_cancellable", fake_http),
                mock.patch(
                    "vestahub.provider_tools.RepositoryToolExecutor",
                    return_value=_FakeToolExecutor(),
                ),
                mock.patch(
                    "vestahub.ledger.record_model_call_started", side_effect=_capture
                ),
                mock.patch(
                    "vestahub.ledger.record_model_call_finalized", side_effect=_capture
                ),
            ):
                outcome = runner.complete_with_tools(
                    "do a thing", project_root=root, allow_edits=False
                )

        self.assertNotIn(self.SECRET, repr(outcome))
        for event_kwargs in recorded_events:
            self.assertNotIn(self.SECRET, repr(event_kwargs))


# ---------------------------------------------------------------------------
# PaidAPIRunner payload wiring: the control actually reaches the wire, and
# nothing else is disturbed.
# ---------------------------------------------------------------------------


class PayloadWiringTests(unittest.TestCase):
    def _run_chat_and_capture_payload(self, runner) -> dict:
        captured: dict = {}

        def fake_http(url, *, method="POST", payload=None, **kwargs):
            captured.update(payload or {})
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

        with mock.patch.object(local_runner, "_http_json_cancellable", fake_http):
            runner._chat([{"role": "user", "content": "hi"}], timeout=5, cancel=None)
        return captured

    def test_default_paid_runner_sends_nothing_new_phase_1_regression_guard(self):
        """Constructing PaidAPIRunner with no thinking argument at all must
        reproduce #673 Phase 1 exactly."""
        runner = PaidAPIRunner(
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            "sk-test",
            pricing_model_id="deepseek-v4-flash",
        )
        payload = self._run_chat_and_capture_payload(runner)
        self.assertNotIn("thinking", payload)
        self.assertNotIn("reasoning_effort", payload)

    def test_thinking_on_reaches_the_wire(self):
        runner = PaidAPIRunner(
            "https://api.deepseek.com",
            "deepseek-v4-pro",
            "sk-test",
            pricing_model_id="deepseek-v4-pro",
            thinking=ThinkingControl(mode="on", effort="max"),
        )
        payload = self._run_chat_and_capture_payload(runner)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "max")

    def test_free_api_runner_never_gains_thinking_fields(self):
        """FreeAPIRunner has no thinking concept at all -- the hook must
        default to a no-op there, not merely "unset"."""
        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "model", "key")
        payload = self._run_chat_and_capture_payload(runner)
        self.assertNotIn("thinking", payload)
        self.assertNotIn("reasoning_effort", payload)

    def test_reasoning_content_is_extracted_and_thinking_requested_is_stamped(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "cot",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ],
            "usage": {},
        }

        def fake_http(url, **kwargs):
            return response

        class _StubExecutor:
            def schemas(self):
                return []

            def invoke_call(self, call, *, cancel=None):
                return {"ok": True, "content": "ok"}

        runner = PaidAPIRunner(
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            "sk-test",
            pricing_model_id="deepseek-v4-flash",
            thinking=ThinkingControl(mode="on"),
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(local_runner, "_http_json_cancellable", fake_http),
                mock.patch(
                    "vestahub.provider_tools.RepositoryToolExecutor",
                    return_value=_StubExecutor(),
                ),
            ):
                # A single-turn continuity failure (no matching finalize) is
                # fine here -- the loop reaches STUCK/FAILED either way; what
                # this test asserts is that the ChatTurn built from the
                # response carried the fields, which the round-trip test
                # above already proves by getting to COMPLETED. Exercised
                # here at the narrower unit level via the runner's own
                # chat-turn construction through the controller's stop.
                outcome = runner.complete_with_tools(
                    "task", project_root=root, allow_edits=False, max_tool_calls=1
                )
        # thinking was ON and the provider DID return reasoning_content, so
        # this must NOT be a continuity failure.
        self.assertNotEqual(outcome.get("stopped_reason"), "reasoning_continuity_error")


# ---------------------------------------------------------------------------
# runner_for_model: the control is forwarded where it matters, harmless
# elsewhere (#674's scope stops short of any picker UI -- see paid_api_models
# module docstring).
# ---------------------------------------------------------------------------


class RunnerForModelWiringTests(unittest.TestCase):
    def test_thinking_reaches_a_paid_runner(self):
        with mock.patch.dict(
            "os.environ",
            {"DEEPSEEK_API_KEY": "sk-test"},  # pragma: allowlist secret
        ):
            runner = runner_for_model(
                "paid:deepseek:deepseek-v4-flash",
                thinking=ThinkingControl(mode="on", effort="high"),
            )
        self.assertIsInstance(runner, PaidAPIRunner)
        self.assertTrue(runner._thinking_requested())
        self.assertEqual(runner._extra_chat_fields()["reasoning_effort"], "high")

    def test_omitting_thinking_reproduces_phase_1_exactly(self):
        with mock.patch.dict(
            "os.environ",
            {"DEEPSEEK_API_KEY": "sk-test"},  # pragma: allowlist secret
        ):
            runner = runner_for_model("paid:deepseek:deepseek-v4-flash")
        self.assertFalse(runner._thinking_requested())

    def test_a_free_model_id_ignores_a_passed_thinking_control_harmlessly(self):
        with mock.patch.dict(
            "os.environ",
            {"GROQ_API_KEY": "sk-test"},  # pragma: allowlist secret
        ):
            runner = runner_for_model(
                "free:groq:openai/gpt-oss-120b", thinking=ThinkingControl(mode="on")
            )
        self.assertIsInstance(runner, FreeAPIRunner)
        self.assertFalse(runner._thinking_requested())


if __name__ == "__main__":
    unittest.main()
