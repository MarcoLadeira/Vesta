"""Continuous, checkpointed tool-loop controller and adaptive compaction.

This module replaces the old terminal ``tool_budget_exhausted`` behaviour of the
free/local tool runner.  ``12`` is a *maintenance* checkpoint interval, never a
default quota: a productive run keeps going, compacting its context so it stays
bounded, and only stops for an honest reason.

The controller is transport-agnostic.  Callers inject:

* ``chat(messages, *, tools) -> ChatTurn`` — one provider round-trip.  It raises
  :class:`ToolLoopProviderError` for a retryable transport failure.
* an ``executor`` exposing ``schemas()`` and ``invoke_call(call, *, cancel)``
  (the repository tool executor, or a scripted fake in tests).

Every no-tool response is parsed through a strict, versioned completion
decision; a claimed ``completed`` is verified against real evidence before it is
ever reported as :class:`~opaihub.completion.CompletionState.COMPLETED`.  Reading
files — even unique ones — is not goal progress, so a run that only reads stops
as ``STUCK_NO_PROGRESS`` rather than faking success.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import time
from typing import Any, Callable, Mapping, Sequence

from .completion import CompletionState
from .deadlines import (
    DeadlineBudget,
    TASK_DEADLINE,
    timeout_event as build_timeout_event,
)
from .progress_evidence import ProgressLedger

DECISION_SCHEMA_VERSION = 1
_DECISION_KEY = "opai_decision_version"

# Tools that change repository state.  A *successful* call to one of these is a
# milestone; reads (however many, however unique) are not.  Callers may override
# the classification through ``ToolLoopPolicy.mutating_tools``.
DEFAULT_MUTATING_TOOLS = frozenset(
    {
        "apply_patch",
        "write_file",
        "create_file",
        "delete_file",
        "run_command",
        "git_commit",
    }
)

# Decision states the model may legitimately declare in a no-tool response.
_DECISION_STATES = {
    "completed": CompletionState.COMPLETED,
    "needs_user_input": CompletionState.NEEDS_USER_INPUT,
    "needs_consent": CompletionState.NEEDS_CONSENT,
    "blocked": CompletionState.PROVIDER_BLOCKED,
    "provider_blocked": CompletionState.PROVIDER_BLOCKED,
    "stuck": CompletionState.STUCK_NO_PROGRESS,
    "stuck_no_progress": CompletionState.STUCK_NO_PROGRESS,
    "failed": CompletionState.FAILED,
}


class ToolLoopProviderError(Exception):
    """Raised by an injected ``chat`` to signal a retryable transport failure."""


class ToolLoopTaskDeadlineExceeded(TimeoutError):
    """An active provider turn exhausted the task clock, not a retry clock."""

    def __init__(
        self,
        *,
        elapsed_seconds: float,
        provider_responsive: bool | None,
        phase: str,
        teardown_state: str,
        cost_state: str = "unknown",
    ) -> None:
        super().__init__("task deadline expired during the provider turn")
        self.elapsed_seconds = elapsed_seconds
        self.provider_responsive = provider_responsive
        self.phase = phase
        self.teardown_state = teardown_state
        self.cost_state = cost_state


class ReasoningContinuityError(RuntimeError):
    """A thinking-mode tool-call turn's required ``reasoning_content`` is gone.

    DeepSeek's thinking mode (#674, #673 A4) has a non-optional continuity
    requirement: ``reasoning_content`` returned alongside an assistant
    tool-call message must be replayed verbatim in the next turn's request, or
    the provider rejects the continuation. This is raised when a turn
    dispatched under thinking mode produced tool calls but no
    ``reasoning_content`` to carry forward — deliberately NOT a
    :class:`ToolLoopProviderError`, so it is never retried. Re-sending the
    same broken payload cannot fix a missing field; the data is gone, not
    delayed.
    """


class InvalidCompletionDecision(ValueError):
    """A no-tool response carried a decision block that could not be honoured."""


@dataclass(frozen=True)
class ToolLoopPolicy:
    """Bounds for a continuous run.  ``12`` is maintenance, not a quota."""

    checkpoint_interval: int = 12
    compaction_char_threshold: int = 48_000
    compaction_context_fraction: float = 0.35
    provider_context_chars: int | None = None
    retained_atoms: int = 2
    observation_char_cap: int = 8_000
    summary_char_cap: int = 16_000
    evidence_fingerprint_cap: int = 256
    max_calls_per_subgoal: int = 12
    # #569: the absolute ceiling on exploration that never reaches a milestone.
    # The evidence ledger catches *loops* early (it notices repetition at any
    # call count), but a run that keeps finding genuinely new material forever
    # still has to stop. Set well above the report's 60-step investigation so
    # that scenario completes, while unbounded exploration remains bounded.
    max_exploration_calls: int = 80
    max_identical_failures: int = 3
    # Anti-thrash for *successful* repeats (F13): the 2nd identical success
    # carries a notice to the model; the 3rd is never executed — the loop
    # stops as STUCK_NO_PROGRESS/repeated_success instead of burning turns.
    max_identical_successes: int = 3
    max_active_seconds: float = 600.0
    # A transport blip mid-loop used to end the whole run and throw away every
    # tool call already made — twenty minutes of real work lost to a momentary
    # 503. The loop's own state is untouched by a failed round-trip, so the
    # turn can simply be re-issued from exactly where it was. Bounded, because
    # a genuinely down provider must still stop honestly rather than spin.
    max_provider_retries: int = 2
    provider_retry_delay_seconds: float = 1.5
    # Deprecated, opt-in external ceiling.  ``None`` means "no ceiling"; the GUI
    # never sets it.  Hitting it is a recoverable stop, never a fake completion.
    max_tool_calls: int | None = None
    mutating_tools: frozenset[str] = DEFAULT_MUTATING_TOOLS

    def compaction_threshold(self) -> int:
        """The serialized-character size at which context must compact."""

        threshold = int(self.compaction_char_threshold)
        if self.provider_context_chars:
            fraction = int(
                self.provider_context_chars * self.compaction_context_fraction
            )
            if fraction > 0:
                threshold = min(threshold, fraction)
        return max(1, threshold)


@dataclass(frozen=True)
class CompletionDecision:
    """A strict, versioned decision parsed from a no-tool response."""

    state: CompletionState
    summary: str = ""
    evidence: tuple[str, ...] = ()
    question: str = ""


def parse_completion_decision(text: str) -> CompletionDecision | None:
    """Parse a versioned decision object out of ``text``.

    Returns ``None`` when the response carries no decision block at all (the
    model simply answered in prose).  Raises :class:`InvalidCompletionDecision`
    when a decision block *is* present but malformed, the wrong version, or names
    an unknown state — an invalid decision is never silently treated as success.
    """

    if not text or _DECISION_KEY not in text:
        return None
    payload = _extract_decision_object(text)
    if payload is None:
        raise InvalidCompletionDecision("decision block is not valid JSON")
    version = payload.get(_DECISION_KEY)
    if version != DECISION_SCHEMA_VERSION:
        raise InvalidCompletionDecision(f"unsupported decision version: {version!r}")
    raw_state = str(payload.get("state", "")).strip().lower().replace("-", "_")
    state = _DECISION_STATES.get(raw_state)
    if state is None:
        raise InvalidCompletionDecision(f"unknown decision state: {raw_state!r}")
    evidence = payload.get("evidence") or ()
    if isinstance(evidence, (str, bytes)):
        evidence = (str(evidence),)
    else:
        evidence = tuple(str(item) for item in evidence)
    return CompletionDecision(
        state=state,
        summary=str(payload.get("summary", "") or ""),
        evidence=evidence,
        question=str(payload.get("question", "") or ""),
    )


def _extract_decision_object(text: str) -> dict[str, Any] | None:
    """Find the first balanced JSON object containing the decision key."""

    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except ValueError:
                        break
                    if isinstance(parsed, dict) and _DECISION_KEY in parsed:
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


@dataclass(frozen=True)
class ToolProtocolAtom:
    """One complete protocol unit: an assistant tool-call turn plus its results.

    An atom is never split during compaction — an assistant ``tool_calls``
    message and every matching ``tool`` observation stay together, so the
    provider never sees a tool result without its originating call.
    """

    turn_index: int
    assistant_content: str
    tool_calls: tuple[Mapping[str, Any], ...]
    observations: tuple[Mapping[str, Any], ...]
    # #674: DeepSeek thinking-mode continuity. ``reasoning_content`` is the raw
    # provider field to replay verbatim; ``thinking_required`` records whether
    # THIS turn was dispatched with thinking on, so a turn made under
    # non-thinking mode is never held to a continuity rule that never applied
    # to it. Never surfaced outside this atom — not in ToolLoopResult, not in
    # the ledger, not in diagnostics (A4's privacy rule) — and discarded for
    # good the moment compaction folds this atom into a summary line.
    reasoning_content: str | None = None
    thinking_required: bool = False

    def to_messages(self) -> list[dict[str, Any]]:
        if self.thinking_required and not self.reasoning_content:
            # Every atom is a tool-call turn by construction (a no-tool
            # response never becomes an atom — see ToolLoopController.run),
            # so `thinking_required` alone is sufficient: A4's "non-tool
            # thinking turns don't need continuity" carve-out cannot apply
            # here, there is no non-tool case to exempt.
            raise ReasoningContinuityError(
                f"turn {self.turn_index} was dispatched with thinking mode on "
                "and returned tool calls, but no reasoning_content to replay. "
                "The provider will reject this continuation without it."
            )
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": self.assistant_content or "",
            "tool_calls": [dict(call) for call in self.tool_calls],
        }
        if self.reasoning_content:
            assistant_message["reasoning_content"] = self.reasoning_content
        messages: list[dict[str, Any]] = [assistant_message]
        for observation in self.observations:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(observation.get("call_id") or ""),
                    "content": str(observation.get("content") or ""),
                }
            )
        return messages

    def serialized_chars(self) -> int:
        return len(json.dumps(self.to_messages(), ensure_ascii=False, sort_keys=True))

    def summary_line(self) -> str:
        parts: list[str] = []
        for observation in self.observations:
            tool = str(observation.get("tool") or "tool")
            status = "ok" if observation.get("ok") else "error"
            detail = str(
                observation.get("error_code") or observation.get("summary") or ""
            ).strip()
            parts.append(f"{tool}:{status}" + (f"({detail})" if detail else ""))
        return f"turn {self.turn_index}: " + ", ".join(parts)


@dataclass
class ToolLoopState:
    """Mutable state of a run: full atom history plus bounded working context."""

    base_messages: list[dict[str, Any]]
    atoms: list[ToolProtocolAtom] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: deque[str] = field(default_factory=lambda: deque(maxlen=64))
    repeated_failures: dict[str, int] = field(default_factory=dict)
    repeated_successes: dict[str, int] = field(default_factory=dict)
    turn_index: int = 0
    tool_calls_used: int = 0
    calls_since_milestone: int = 0
    milestones: int = 0
    # #569: evidence-based progress. Unlike calls_since_milestone this can tell
    # a long productive investigation apart from a loop.
    progress: ProgressLedger = field(default_factory=ProgressLedger)
    compactions: int = 0
    cumulative_serialized_chars: int = 0

    def request_messages(self) -> list[dict[str, Any]]:
        """Build the next request: base + compact summary + retained atoms."""

        messages = [dict(message) for message in self.base_messages]
        if self.summaries:
            messages.append(
                {
                    "role": "system",
                    "content": "Summary of earlier verified steps:\n"
                    + "\n".join(self.summaries),
                }
            )
        for atom in self.atoms:
            messages.extend(atom.to_messages())
        for note in self.notes:
            messages.append({"role": "system", "content": note})
        return messages

    def serialized_chars(self) -> int:
        return len(
            json.dumps(self.request_messages(), ensure_ascii=False, sort_keys=True)
        )


@dataclass(frozen=True)
class CompactionReport:
    compacted: bool
    chars_before: int
    chars_after: int
    atoms_retained: int
    atoms_summarized: int


def compact_context(state: ToolLoopState, policy: ToolLoopPolicy) -> CompactionReport:
    """Fold older protocol atoms into a bounded summary when context grows large.

    At most ``policy.retained_atoms`` complete atoms are kept verbatim; older
    atoms become one bounded summary line each.  Atom boundaries are respected,
    so a tool result is never separated from its call.
    """

    before = state.serialized_chars()
    threshold = policy.compaction_threshold()
    if before <= threshold or len(state.atoms) <= policy.retained_atoms:
        return CompactionReport(False, before, before, len(state.atoms), 0)

    keep = max(0, int(policy.retained_atoms))
    older = state.atoms[:-keep] if keep else list(state.atoms)
    retained = state.atoms[-keep:] if keep else []
    for atom in older:
        state.summaries.append(atom.summary_line())
    # Keep the running summary bounded from the front (oldest first).
    joined = "\n".join(state.summaries)
    if len(joined) > policy.summary_char_cap:
        while (
            state.summaries
            and len("\n".join(state.summaries)) > policy.summary_char_cap
        ):
            state.summaries.pop(0)
    state.atoms = retained
    state.compactions += 1
    after = state.serialized_chars()
    return CompactionReport(True, before, after, len(retained), len(older))


def verify_completion(
    decision: CompletionDecision | None,
    state: ToolLoopState,
    policy: ToolLoopPolicy,
    *,
    allow_mutations: bool,
) -> CompletionState:
    """Decide the honest final state for a no-tool response.

    A ``completed`` claim (explicit decision or bare prose) is only honoured when
    the run produced genuine goal progress: at least one milestone (a successful
    mutating tool) for an editing task, or at least one successful tool
    observation for a read-only task.  Otherwise the run is ``STUCK_NO_PROGRESS``
    — reading alone never fakes completion.
    """

    if decision is not None and decision.state is not CompletionState.COMPLETED:
        # The model asked to stop for a non-success reason; honour it as-is.
        return decision.state

    if allow_mutations:
        # An editing task is not done until it actually edited something.
        progressed = state.milestones > 0
    else:
        # A read-only task may answer from context without any tool. But if it
        # *attempted* tools and none succeeded, its answer is not grounded.
        attempted = bool(state.atoms) or bool(state.summaries)
        progressed = (not attempted) or _has_successful_observation(state)
    if not progressed:
        return CompletionState.STUCK_NO_PROGRESS

    if decision is not None and decision.evidence:
        if not _evidence_is_grounded(decision.evidence, state):
            return CompletionState.STUCK_NO_PROGRESS
    return CompletionState.COMPLETED


def _has_successful_observation(state: ToolLoopState) -> bool:
    for atom in state.atoms:
        if any(observation.get("ok") for observation in atom.observations):
            return True
    # Older successful evidence may already be compacted into summaries.
    return any(":ok" in summary for summary in state.summaries)


def _evidence_is_grounded(evidence: Sequence[str], state: ToolLoopState) -> bool:
    known: set[str] = set()
    for atom in state.atoms:
        for observation in atom.observations:
            call_id = str(observation.get("call_id") or "")
            if call_id:
                known.add(call_id)
            tool = str(observation.get("tool") or "")
            if tool:
                known.add(tool)
    known.update(state.evidence)
    # Grounded when at least one cited reference maps to real observed work.
    return any(str(item) in known for item in evidence)


@dataclass(frozen=True)
class ChatTurn:
    """Normalized provider response consumed by the controller."""

    content: str = ""
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    # #674: the provider's raw reasoning field for this turn, if any, and
    # whether the runner dispatched this turn with thinking mode on. Both
    # default off so every existing ``ChatTurn(...)`` caller (Claude, Codex,
    # every non-thinking provider) is unaffected — reasoning_content is simply
    # never carried for them.
    reasoning_content: str | None = None
    thinking_requested: bool = False


@dataclass(frozen=True)
class ToolLoopResult:
    """The typed outcome of a run.  Only ``COMPLETED`` means success."""

    completion_state: CompletionState
    answer: str = ""
    user_question: str = ""
    stopped_reason: str = ""
    tool_trace: tuple[Mapping[str, Any], ...] = ()
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    measurement: str = "estimated"
    quota_snapshot: Mapping[str, Any] | None = None
    milestones: int = 0
    compactions: int = 0
    cumulative_serialized_chars: int = 0
    last_error: str = ""
    blocked_reason: str = ""
    # #569: the evidence ledger's own account of the run (score, distinct
    # observations, repeated failures, milestones). This is what lets a stop be
    # explained — "stopped after 60 steps that stopped teaching us anything" —
    # instead of an unexplained halt.
    progress: Mapping[str, Any] | None = None
    # Set on a NEEDS_CONSENT exit caused by a tool that requires user approval
    # (F17): {"command": <exact string>, "reason": <why>}. The pipeline turns
    # this into an approval card and threads the granted string back down.
    consent_payload: Mapping[str, Any] | None = None
    timeout_event: Mapping[str, Any] | None = None


ChatCallable = Callable[..., ChatTurn]


# Observation notice codes surfaced to the model (F19/F11/F13).
EMPTY_OUTPUT_NOTICE_CODE = "EMPTY_OUTPUT"
DUPLICATE_SUCCESS_NOTICE = "DUPLICATE_SUCCESS"
COMMAND_NEEDS_APPROVAL_CODE = "COMMAND_NEEDS_APPROVAL"

_EMPTY_OUTPUT_GUIDANCE = (
    "The command ran but produced no output. Try a different form (for "
    "example add --json, or verify the command writes to stdout) before "
    "continuing."
)
_DUPLICATE_SUCCESS_GUIDANCE = (
    "[OPai notice] This exact call already succeeded; repeating it returns "
    "the same result. Move on to the next step instead of re-running it."
)


@dataclass(frozen=True)
class _ConsentStop:
    """Internal signal: a tool call requires user approval before it may run."""

    command: str
    reason: str


@dataclass(frozen=True)
class _RepeatedSuccessStop:
    """Internal signal: the same call succeeded enough times already (F13)."""

    tool: str


def _observation_output_empty(observation: Mapping[str, Any]) -> bool:
    """True when an ``ok`` observation carries no usable payload (F19)."""

    if str(observation.get("content") or "").strip():
        return False
    if str(observation.get("message") or "").strip():
        return False
    data = observation.get("data")
    if isinstance(data, Mapping):
        if "stdout" in data or "stderr" in data:
            return not (
                str(data.get("stdout") or "").strip()
                or str(data.get("stderr") or "").strip()
            )
        return not any(
            str(value).strip() for value in data.values() if value is not None
        )
    return not str(data or "").strip()


class ToolLoopController:
    """Drives a bounded-but-continuous tool loop to an honest completion state."""

    def __init__(
        self,
        policy: ToolLoopPolicy | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.policy = policy or ToolLoopPolicy()
        self._clock = clock
        # Injected so tests exercise the retry policy without waiting for it.
        self._sleep = sleep

    def run(
        self,
        *,
        chat: ChatCallable,
        executor: Any,
        base_messages: Sequence[Mapping[str, Any]],
        allow_mutations: bool = True,
        tool_calling_enabled: bool = True,
        guard: Callable[[int], Any] | None = None,
        cancel: Any = None,
        deadline_budget: DeadlineBudget | None = None,
    ) -> ToolLoopResult:
        policy = self.policy
        state = ToolLoopState(
            base_messages=[dict(message) for message in base_messages]
        )
        trace: list[dict[str, Any]] = []
        input_tokens = 0
        output_tokens = 0
        model_calls = 0
        measured = False
        quota: Mapping[str, Any] | None = None
        last_error = ""
        invalid_decisions = 0
        provider_retries = 0
        started = self._clock()
        terminal_timeout_event: Mapping[str, Any] | None = None
        # ``max_active_seconds`` is a no-progress backstop, not the immutable
        # task deadline carried by ``deadline_budget``. Measured from the start,
        # the backstop used to kill a run even while it produced new evidence;
        # it therefore resets on progress, while the distinct task clock below
        # never does.
        #
        # This clock is not the loop's protection against spinning: the evidence
        # ledger below (`is_stagnant`) is, and it fires within a few steps of a
        # run that stops learning. The deadline is only a backstop for a run that
        # is somehow neither progressing nor detected as stagnant, so it restarts
        # on every new high-water score.
        last_progress_at = started
        best_progress_seen = state.progress.best_score

        def _result(
            state_value: CompletionState,
            *,
            answer: str = "",
            question: str = "",
            stopped: str = "",
            blocked_reason: str = "",
            consent: Mapping[str, Any] | None = None,
        ) -> ToolLoopResult:
            return ToolLoopResult(
                completion_state=state_value,
                answer=answer,
                user_question=question,
                stopped_reason=stopped,
                tool_trace=tuple(trace),
                model_calls=model_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                measurement="provider" if measured else "estimated",
                quota_snapshot=quota,
                milestones=state.milestones,
                compactions=state.compactions,
                cumulative_serialized_chars=state.cumulative_serialized_chars,
                last_error=last_error,
                blocked_reason=blocked_reason,
                progress=state.progress.summary(),
                consent_payload=consent,
                timeout_event=terminal_timeout_event,
            )

        def _continuity_stop(exc: ReasoningContinuityError) -> ToolLoopResult:
            # #674: terminal, never retried. A missing reasoning_content is a
            # permanently lost field, not a transient transport blip — the
            # ToolLoopProviderError retry path would just resend the same
            # broken request and fail identically every time.
            nonlocal last_error
            last_error = str(exc)
            return _result(CompletionState.FAILED, stopped="reasoning_continuity_error")

        def _task_deadline_stop(
            *,
            elapsed_seconds: float,
            provider_responsive: bool | None,
            phase: str,
            teardown_state: str,
            cost_state: str = "unknown",
        ) -> ToolLoopResult:
            nonlocal terminal_timeout_event
            assert deadline_budget is not None
            terminal_timeout_event = build_timeout_event(
                origin=TASK_DEADLINE,
                owner="tool_loop_controller",
                configured_seconds=deadline_budget.task_deadline_seconds,
                elapsed_seconds=elapsed_seconds,
                provider_responsive=provider_responsive,
                phase=phase,
                budget=deadline_budget,
                progress_observed=state.progress.best_score > 0,
                external_effect_possible=allow_mutations,
                teardown_state=teardown_state,
                cost_state=cost_state,
                verification_state="incomplete",
            )
            return _result(CompletionState.TIMEOUT, stopped=TASK_DEADLINE)

        while True:
            if _cancelled(cancel):
                return _result(CompletionState.CANCELLED, stopped="cancelled")
            # One clock read per iteration: reading it separately for the reset
            # and the comparison let time advance between them, so a run that
            # had just made progress could still be judged over its deadline.
            now = self._clock()
            elapsed = now - started
            if (
                deadline_budget is not None
                and elapsed > deadline_budget.task_deadline_seconds
            ):
                return _task_deadline_stop(
                    elapsed_seconds=elapsed,
                    provider_responsive=True if model_calls else None,
                    phase="between_provider_turns",
                    teardown_state="not_required",
                )
            if state.progress.best_score > best_progress_seen:
                # New evidence since the last check: the run is working, so the
                # deadline starts again from here.
                best_progress_seen = state.progress.best_score
                last_progress_at = now
            if now - last_progress_at > policy.max_active_seconds:
                return _result(
                    CompletionState.STUCK_NO_PROGRESS, stopped="controller_timeout"
                )

            # Maintenance checkpoint (never terminal): compact when the request
            # grows past the threshold, and at least once every checkpoint_interval
            # calls. ``12`` is this maintenance cadence, not a stopping quota;
            # compaction is a no-op while the context is still small.
            #
            # #674: serialized_chars() renders every atom via to_messages(),
            # which is where a broken continuity atom raises — so the very
            # first size check after a bad turn is appended is already "on
            # replay", before any request is built or dispatched.
            try:
                due_by_size = state.serialized_chars() >= policy.compaction_threshold()
                due_by_interval = (
                    model_calls > 0 and model_calls % policy.checkpoint_interval == 0
                )
                if due_by_size or due_by_interval:
                    compact_context(state, policy)
                state.cumulative_serialized_chars += state.serialized_chars()
            except ReasoningContinuityError as exc:
                return _continuity_stop(exc)

            # Per-turn guard (Task 6): the financial/consent/provider check runs
            # before *every* provider turn — the first and every continuation —
            # so a run that becomes unaffordable or loses consent stops honestly
            # mid-flight rather than continuing to spend.
            if guard is not None:
                decision = guard(model_calls + 1)
                if decision is not None and not decision.allowed:
                    guard_state = decision.to_completion_state()
                    reason = (
                        decision.blocked_reason.value
                        if getattr(decision, "blocked_reason", None) is not None
                        else ""
                    )
                    # A clean, recognised token (e.g. "daily_cap") so downstream
                    # completion-state mapping stays honest; free-text detail is
                    # kept separately for diagnostics.
                    last_error = str(getattr(decision, "detail", "") or "")
                    return _result(
                        guard_state,
                        stopped=reason or guard_state.value,
                        blocked_reason=reason,
                    )

            tools = executor.schemas() if tool_calling_enabled else []
            try:
                turn = chat(state.request_messages(), tools=tools)
            except ReasoningContinuityError as exc:
                return _continuity_stop(exc)
            except ToolLoopTaskDeadlineExceeded as exc:
                if deadline_budget is None:
                    raise
                return _task_deadline_stop(
                    elapsed_seconds=exc.elapsed_seconds,
                    provider_responsive=exc.provider_responsive,
                    phase=exc.phase,
                    teardown_state=exc.teardown_state,
                    cost_state=exc.cost_state,
                )
            except ToolLoopProviderError as exc:
                last_error = str(exc)
                # The round-trip failed, so it changed nothing: the request
                # messages, the tool trace, and every milestone are exactly as
                # they were. Re-issue the same turn instead of discarding the
                # whole run's progress over a blip. The budget is per-run, so a
                # provider that keeps failing still stops honestly — and the
                # retry is not counted as a model call, because none happened.
                if provider_retries < policy.max_provider_retries and not _cancelled(
                    cancel
                ):
                    provider_retries += 1
                    self._sleep(policy.provider_retry_delay_seconds)
                    if not _cancelled(cancel):
                        continue
                return _result(
                    CompletionState.RETRYABLE_PROVIDER_ERROR, stopped="provider_error"
                )
            model_calls += 1
            usage = dict(turn.usage or {})
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            measured = measured or usage.get("measurement") == "provider"
            quota = usage.get("quota_snapshot") or quota

            calls = list(turn.tool_calls or [])
            if not calls:
                # A no-tool response is a completion claim: parse + verify it.
                try:
                    decision = parse_completion_decision(turn.content or "")
                except InvalidCompletionDecision as exc:
                    invalid_decisions += 1
                    last_error = str(exc)
                    if invalid_decisions >= 2:
                        return _result(
                            CompletionState.STUCK_NO_PROGRESS,
                            stopped="invalid_decision",
                        )
                    state.notes.append(
                        "Your previous decision JSON was invalid. Reply with a single "
                        f'JSON object containing "{_DECISION_KEY}": '
                        f'{DECISION_SCHEMA_VERSION} and a valid "state".'
                    )
                    continue
                final = verify_completion(
                    decision, state, policy, allow_mutations=allow_mutations
                )
                answer = (decision.summary if decision else "") or (turn.content or "")
                question = decision.question if decision else ""
                stopped = "" if final is CompletionState.COMPLETED else final.value
                return _result(final, answer=answer, question=question, stopped=stopped)

            # An external ceiling (deprecated) is recoverable, never a completion.
            if policy.max_tool_calls is not None and (
                state.tool_calls_used + len(calls) > policy.max_tool_calls
            ):
                return _result(
                    CompletionState.STUCK_NO_PROGRESS, stopped="external_ceiling"
                )

            observations = self._execute(
                calls, executor, cancel, trace, state, allow_mutations
            )
            if observations is _CANCELLED:
                return _result(CompletionState.CANCELLED, stopped="cancelled")
            if isinstance(observations, _ConsentStop):
                # F17: a confirm-class command (git push, gh mutation, …) needs
                # an explicit user grant. Exit with the exact command so the
                # pipeline can render an approval card and thread the grant
                # back as a one-shot allow_command.
                last_error = COMMAND_NEEDS_APPROVAL_CODE
                return _result(
                    CompletionState.NEEDS_CONSENT,
                    stopped="approval_required",
                    consent={
                        "command": observations.command,
                        "reason": observations.reason,
                    },
                )
            if isinstance(observations, _RepeatedSuccessStop):
                # F13: the model is re-running a call that already succeeded;
                # stop honestly instead of burning turns on identical work.
                return _result(
                    CompletionState.STUCK_NO_PROGRESS, stopped="repeated_success"
                )
            for observation in observations:
                if not observation.get("ok"):
                    last_error = str(
                        observation.get("error_code")
                        or observation.get("message")
                        or ""
                    )

            state.turn_index += 1
            state.atoms.append(
                ToolProtocolAtom(
                    turn_index=state.turn_index,
                    assistant_content=turn.content or "",
                    tool_calls=tuple(calls),
                    observations=tuple(observations),
                    reasoning_content=turn.reasoning_content,
                    thinking_required=turn.thinking_requested,
                )
            )
            state.tool_calls_used += len(calls)

            # Anti-thrash: the same action failing again and again is not
            # progress — stop quickly rather than burn the whole subgoal budget.
            if any(
                count >= policy.max_identical_failures
                for count in state.repeated_failures.values()
            ):
                return _result(
                    CompletionState.STUCK_NO_PROGRESS, stopped="repeated_failure"
                )

            # Stagnation guard (#569), in two parts. The old rule was "no edit
            # in N calls", which killed the 60-step investigation from the
            # report's crash evidence — a run that was learning the whole time.
            #
            # 1. Evidence stagnation: has anything been learned recently? This
            #    catches a loop at any call count, because repetition scores
            #    nothing however early it starts.
            if state.progress.is_stagnant(patience=policy.max_calls_per_subgoal):
                return _result(CompletionState.STUCK_NO_PROGRESS, stopped="no_progress")
            # 2. An absolute exploration ceiling. A run that keeps finding
            #    genuinely new material forever still has to stop, or "keeps
            #    learning" becomes "never finishes".
            if state.calls_since_milestone > policy.max_exploration_calls:
                return _result(
                    CompletionState.STUCK_NO_PROGRESS, stopped="exploration_limit"
                )

    def _execute(
        self,
        calls: list[Mapping[str, Any]],
        executor: Any,
        cancel: Any,
        trace: list[dict[str, Any]],
        state: ToolLoopState,
        allow_mutations: bool,
    ) -> list[dict[str, Any]] | Any:
        observations: list[dict[str, Any]] = []
        made_milestone = False
        for call in calls:
            if _cancelled(cancel):
                return _CANCELLED
            function = call.get("function") if isinstance(call, Mapping) else {}
            name = str((function or {}).get("name") or "unknown")
            call_id = str(call.get("id") or "") if isinstance(call, Mapping) else ""
            signature = name + "|" + str((function or {}).get("arguments") or "")
            # F13: the Nth identical successful call is never executed — stop
            # the loop instead of re-running work whose result cannot change.
            if state.repeated_successes.get(signature, 0) >= max(
                1, self.policy.max_identical_successes - 1
            ):
                return _RepeatedSuccessStop(tool=name)
            observation = dict(executor.invoke_call(call, cancel=cancel))
            ok = bool(observation.get("ok"))
            observation.setdefault("tool", name)
            observation.setdefault("call_id", call_id)
            # F19: an ok observation with no usable payload is marked so the
            # model is told to try a different form, not fed silence.
            if ok and _observation_output_empty(observation):
                observation["notice_code"] = EMPTY_OUTPUT_NOTICE_CODE
                observation["content"] = _EMPTY_OUTPUT_GUIDANCE
            observation.setdefault(
                "content", json.dumps(observation, sort_keys=True, default=str)
            )
            self._cap_observation(observation)
            if ok:
                state.repeated_failures.pop(signature, None)
                successes = state.repeated_successes.get(signature, 0) + 1
                state.repeated_successes[signature] = successes
                if successes == 2:
                    # Second identical success: warn the model inline so it
                    # moves on (the third is stopped before execution above).
                    observation["notice"] = DUPLICATE_SUCCESS_NOTICE
                    observation["content"] = (
                        str(observation.get("content") or "")
                        + "\n"
                        + _DUPLICATE_SUCCESS_GUIDANCE
                    )
                if name in self.policy.mutating_tools:
                    made_milestone = True
            else:
                state.repeated_failures[signature] = (
                    state.repeated_failures.get(signature, 0) + 1
                )
            observations.append(observation)
            # #569: score what this observation actually taught us. The
            # milestone counter below still drives the legacy guard; this
            # ledger is what distinguishes a long *productive* investigation
            # from a loop, and it is what the stagnation check consults.
            state.progress.record(
                {
                    "tool": name,
                    "arguments": str((function or {}).get("arguments") or ""),
                    "content": observation.get("content") or "",
                    "ok": ok,
                }
            )
            trace.append(
                {
                    "tool": name,
                    "call_id": call_id,
                    "ok": ok,
                    "error_code": str(observation.get("error_code") or ""),
                    "message": str(observation.get("message") or ""),
                    "duration_ms": int(observation.get("duration_ms") or 0),
                }
            )
            fingerprint = (name + "|" + call_id)[: self.policy.evidence_fingerprint_cap]
            state.evidence.append(fingerprint)
            if not ok and str(observation.get("error_code") or "") == (
                COMMAND_NEEDS_APPROVAL_CODE
            ):
                # F17: confirm-class command — stop the loop for approval with
                # the exact command string and the classifier's reason.
                return _ConsentStop(
                    command=str(observation.get("command") or ""),
                    reason=str(
                        observation.get("approval_reason")
                        or observation.get("message")
                        or ""
                    ),
                )
        if made_milestone:
            state.milestones += 1
            state.calls_since_milestone = 0
        else:
            state.calls_since_milestone += len(calls)
        return observations

    def _cap_observation(self, observation: dict[str, Any]) -> None:
        cap = self.policy.observation_char_cap
        content = observation.get("content")
        if isinstance(content, str) and len(content) > cap:
            observation["content"] = content[:cap] + "…[truncated]"


_CANCELLED = object()


def _cancelled(cancel: Any) -> bool:
    return bool(cancel is not None and getattr(cancel, "is_set", lambda: False)())
