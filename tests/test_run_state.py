"""The canonical task/run state machine shared end to end (#379)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from opaihub.completion import CompletionVerdict
from opaihub.generated_lifecycle import (
    EXIT_CODES,
    STATE_IDS,
    STATE_SPECS,
    TERMINAL_STATE_IDS,
)
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


def test_completion_verdicts_project_to_generated_terminal_states() -> None:
    # Existing verdicts keep their same-named state while generated lifecycle
    # truth may add a typed degraded terminal for incompatible data.
    assert {v.value for v in CompletionVerdict} <= {s.value for s in TERMINAL_STATES}
    assert {s.value for s in TERMINAL_STATES} == set(TERMINAL_STATE_IDS)


def test_run_state_and_exit_codes_are_generated_contract_projections() -> None:
    from opaihub.run_state import exit_code_for

    assert {state.value for state in RunState} == set(STATE_IDS)
    assert {
        state.value: exit_code_for(state) for state in TERMINAL_STATES
    } == EXIT_CODES


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
    # No going back up the ladder...
    assert not can_transition(RunState.RUNNING, RunState.QUEUED)
    # ...with one deliberate exception: the repair loop. #295 writes it
    # `verifying <-> repairing?` — verification finds a problem, work resumes
    # to fix it, and the evidence is judged again.
    assert can_transition(RunState.VERIFYING, RunState.RUNNING)


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


def test_verifying_leads_to_an_outcome_a_repair_or_an_acknowledged_stop() -> None:
    # Verification judges evidence. It may send the run back to work when the
    # evidence is bad (#295's `verifying <-> repairing?`, which the engine
    # already does as reviewing_diff -> implementing), it may be stopped, and
    # otherwise it produces an outcome. What it never does is stop to ask: it
    # has no question, only a verdict.
    for other in NON_TERMINAL_STATES:
        allowed = other in {
            RunState.VERIFYING,
            RunState.RUNNING,
            RunState.CANCEL_REQUESTED,
        }
        assert not can_transition(RunState.VERIFYING, other) or allowed, other
    assert can_transition(RunState.VERIFYING, RunState.RUNNING)
    assert not can_transition(RunState.VERIFYING, RunState.AWAITING_INPUT)
    assert can_transition(RunState.VERIFYING, RunState.CANCEL_REQUESTED)
    assert can_transition(RunState.VERIFYING, RunState.COMPLETED)


def test_cancel_is_legal_from_any_non_terminal_and_a_no_op_when_terminal() -> None:
    for state in NON_TERMINAL_STATES:
        assert cancel(state) is RunState.CANCEL_REQUESTED
    for terminal in TERMINAL_STATES:
        # You cannot un-finish a completed/failed/... run by cancelling it.
        assert cancel(terminal) is terminal


def test_every_verdict_maps_to_its_terminal_state() -> None:
    for verdict in CompletionVerdict:
        state = run_state_for_verdict(verdict)
        assert is_terminal(state)
        assert state.value == verdict.value


def test_labels_are_shared_with_the_verdict_vocabulary() -> None:
    # Labels come from the generated state specs — "Timed out", never
    # "Timeout" — and every state has a non-empty label.
    for terminal in TERMINAL_STATES:
        assert label(terminal) == STATE_SPECS[terminal.value]["label"]
    assert label(RunState.TIMEOUT) == "Timed out"
    assert label(RunState.RUNNING) == "Running"
    for state in RunState:
        assert label(state)


def test_transition_and_helpers_accept_raw_strings() -> None:
    assert transition("running", "verifying") is RunState.VERIFYING
    assert cancel("preparing") is RunState.CANCEL_REQUESTED
    assert is_terminal("completed") is True
    assert run_state_for_verdict("partial") is RunState.PARTIAL


def test_terminal_attack_preserves_state_and_writes_durable_diagnostic() -> None:
    # A missing project_root write, or a write which accidentally stores the
    # caller's raw source text, must make this fail. The rejected state update
    # and its durable evidence are one behavior, not two best-effort features.
    from opaihub.lifecycle_diagnostics import read_diagnostics

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = transition(
            "completed",
            "running",
            project_root=root,
            source="api_key=sk-secret-value-1234567890 lifecycle reducer",
        )
        event = read_diagnostics(root)[-1]

    assert result is RunState.COMPLETED
    assert (event["from"], event["to"]) == ("completed", "running")
    assert event["event_type"] == "illegal_lifecycle_transition"
    assert "sk-secret-value-1234567890" not in str(event)
