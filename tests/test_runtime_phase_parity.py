"""The engine has one lifecycle, not two (#295, Workstream A / Phase 0).

`RunState` is the canonical lifecycle #379 established. `RuntimePhase` is the
engine's *working* vocabulary — a finer, workflow-shaped account of what a run
is doing (planning, testing, preparing a PR), and `AgentRuntime` is what
actually drives it: it validates every move, raises on an illegal one, and keeps
a sequenced history.

The problem Phase 0 exists to freeze is that the two had drifted apart. Fourteen
of the eighteen runtime phases had no canonical mapping at all, so the engine
was running one lifecycle while the canonical model described another — and
`RunState.transition()` had no production caller, so the canonical machine was
not enforcing anything live. #295 allows exactly one lifecycle: "No adapter, UI
component or provider may invent a parallel vocabulary."

A refinement is fine. An unmapped phase is a second lifecycle in disguise.
"""

from __future__ import annotations

import pytest

from opaihub.agent_runtime import _FORWARD, RuntimePhase
from opaihub.run_state import (
    TERMINAL_STATES,
    RunState,
    can_transition,
    canonical_for_runtime_phase,
)


def test_every_runtime_phase_declares_the_state_it_refines() -> None:
    # The guard that keeps the two vocabularies bound: adding a phase without
    # deciding what it means canonically fails here rather than silently
    # creating a second lifecycle.
    for phase in RuntimePhase:
        assert isinstance(canonical_for_runtime_phase(phase), RunState), phase


def test_an_unknown_phase_is_rejected_not_guessed() -> None:
    with pytest.raises(ValueError):
        canonical_for_runtime_phase("some_invented_phase")


def test_the_phases_that_share_a_word_share_a_meaning() -> None:
    # blocked/failed/completed exist in both vocabularies. If they ever diverged,
    # the same word would mean two things — the worst possible drift.
    for word in ("blocked", "failed", "completed"):
        assert canonical_for_runtime_phase(word) is RunState(word)


def test_terminal_phases_map_to_terminal_states() -> None:
    # A phase the engine treats as an ending must not refine a live state.
    for phase in (RuntimePhase.BLOCKED, RuntimePhase.FAILED, RuntimePhase.COMPLETED):
        assert canonical_for_runtime_phase(phase) in TERMINAL_STATES


def test_live_phases_never_map_to_a_terminal_state() -> None:
    # The converse: a phase the engine can still move on from must not claim the
    # run is over. `_FORWARD` having outbound edges is the engine's own
    # definition of "not finished".
    for phase, onward in _FORWARD.items():
        if not onward:
            continue
        assert canonical_for_runtime_phase(phase) not in TERMINAL_STATES, phase


def test_awaiting_approval_refines_the_state_added_for_it() -> None:
    # #295's waiting_user case: the engine already had a phase for stopping to
    # ask; the canonical model gained the matching non-terminal state, and this
    # is where the two meet.
    assert canonical_for_runtime_phase(RuntimePhase.AWAITING_APPROVAL) is (
        RunState.AWAITING_INPUT
    )


def test_no_legal_phase_move_implies_an_illegal_canonical_transition() -> None:
    """The strongest property: the two graphs cannot contradict each other.

    Every edge the engine permits must project onto an edge the canonical
    machine permits (or onto no movement at all, when both phases refine the
    same state). Without this the engine could legally walk a path the canonical
    lifecycle forbids, and the "one lifecycle" claim would be words only.
    """
    offenders: list[str] = []
    for phase, onward in _FORWARD.items():
        source = canonical_for_runtime_phase(phase)
        for nxt in onward:
            target = canonical_for_runtime_phase(nxt)
            if source is target:
                continue  # a refinement step inside one canonical state
            if not can_transition(source, target):
                offenders.append(
                    f"{phase.value} -> {nxt.value} projects to "
                    f"{source.value} -> {target.value}, which is illegal"
                )
    assert not offenders, "\n".join(offenders)


def test_every_phase_can_still_reach_blocked_and_failed() -> None:
    # AgentRuntime._move always allows BLOCKED/FAILED as escapes. Those project
    # to terminal states, which the canonical machine allows from any live
    # state — so the escape hatch is legal in both graphs.
    for phase in RuntimePhase:
        source = canonical_for_runtime_phase(phase)
        if source in TERMINAL_STATES:
            continue
        assert can_transition(source, RunState.BLOCKED), phase
        assert can_transition(source, RunState.FAILED), phase
