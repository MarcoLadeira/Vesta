"""The canonical task/run state machine shared end to end (#379)."""

from __future__ import annotations

from opaihub.completion import CompletionVerdict, verdict_label
from opaihub.run_state import (
    NON_TERMINAL_STATES,
    TERMINAL_STATES,
    RunState,
    can_transition,
    cancel,
    is_terminal,
    label,
    run_state_for_verdict,
    transition,
)


def test_terminal_states_are_one_for_one_with_the_completion_verdict() -> None:
    # The live model and the final verdict must speak the same words, so every
    # terminal run state is a verdict and vice versa.
    assert {s.value for s in TERMINAL_STATES} == {v.value for v in CompletionVerdict}


def test_states_partition_into_terminal_and_non_terminal() -> None:
    assert TERMINAL_STATES.isdisjoint(NON_TERMINAL_STATES)
    assert TERMINAL_STATES | NON_TERMINAL_STATES == set(RunState)
    assert {s.value for s in NON_TERMINAL_STATES} == {
        "queued",
        "preparing",
        "running",
        # Handing control back to the user is not an ending: the answer resumes
        # this same run (#295).
        "awaiting_input",
        # An acknowledged Stop is not yet a completed teardown (#295).
        "cancel_requested",
        "verifying",
    }


def test_forward_progress_is_legal_but_backward_is_not() -> None:
    assert can_transition(RunState.QUEUED, RunState.PREPARING)
    assert can_transition(RunState.PREPARING, RunState.RUNNING)
    assert can_transition(RunState.RUNNING, RunState.VERIFYING)
    # No going back up the ladder.
    assert not can_transition(RunState.RUNNING, RunState.QUEUED)
    assert not can_transition(RunState.VERIFYING, RunState.RUNNING)


def test_any_non_terminal_may_reach_any_terminal() -> None:
    for start in NON_TERMINAL_STATES:
        for end in TERMINAL_STATES:
            assert can_transition(start, end), (start, end)


def test_terminal_states_are_immutable() -> None:
    for terminal in TERMINAL_STATES:
        assert is_terminal(terminal)
        for other in RunState:
            assert not can_transition(terminal, other), (terminal, other)
        # transition() refuses the illegal edge and keeps the terminal state.
        assert transition(terminal, RunState.RUNNING) is terminal


def test_verifying_leads_only_to_an_outcome_or_an_acknowledged_stop() -> None:
    # Verification judges evidence that already exists. It never returns to
    # doing work, and it has no question to ask — the one non-terminal it may
    # reach is the acknowledgement of a Stop pressed while it was running.
    for other in NON_TERMINAL_STATES:
        allowed = other in {RunState.VERIFYING, RunState.CANCEL_REQUESTED}
        assert not can_transition(RunState.VERIFYING, other) or allowed, other
    assert not can_transition(RunState.VERIFYING, RunState.RUNNING)
    assert not can_transition(RunState.VERIFYING, RunState.AWAITING_INPUT)
    assert can_transition(RunState.VERIFYING, RunState.CANCEL_REQUESTED)
    assert can_transition(RunState.VERIFYING, RunState.COMPLETED)


def test_cancel_is_legal_from_any_non_terminal_and_a_no_op_when_terminal() -> None:
    for state in NON_TERMINAL_STATES:
        assert cancel(state) is RunState.CANCELLED
    for terminal in TERMINAL_STATES:
        # You cannot un-finish a completed/failed/... run by cancelling it.
        assert cancel(terminal) is terminal


def test_every_verdict_maps_to_its_terminal_state() -> None:
    for verdict in CompletionVerdict:
        state = run_state_for_verdict(verdict)
        assert is_terminal(state)
        assert state.value == verdict.value


def test_labels_are_shared_with_the_verdict_vocabulary() -> None:
    # Terminal labels come from the one verdict vocabulary (#396) — "Timed out",
    # never "Timeout" — and every state has a non-empty label.
    for terminal in TERMINAL_STATES:
        assert label(terminal) == verdict_label(terminal.value)
    assert label(RunState.TIMEOUT) == "Timed out"
    assert label(RunState.RUNNING) == "Running"
    for state in RunState:
        assert label(state)


def test_transition_and_helpers_accept_raw_strings() -> None:
    assert transition("running", "verifying") is RunState.VERIFYING
    assert cancel("preparing") is RunState.CANCELLED
    assert is_terminal("completed") is True
    assert run_state_for_verdict("partial") is RunState.PARTIAL
