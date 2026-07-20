"""The one canonical task/run state machine, shared end to end (#379).

Every surface — engine, GUI store, CLI renderer, receipt, ledger — derives its
state word from this module and never invents its own. Divergent state language
is how "it said X but did Y" bugs are born; one contract kills that class.

The lifecycle is deliberately coarse and honest::

    queued -> preparing -> running -> verifying -> <terminal>

with the terminals matching the completion verdict (#378/#402) one-for-one so a
run's live state and its final verdict speak the same words:
``completed | partial | blocked | failed | cancelled | timeout``.

Presentation-layer detail (the Calm Stream phases in ``activity.py`` —
authenticating/streaming/tool_call/…) stays underneath this model; it refines
"running", it never contradicts the canonical state.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from .completion import CompletionVerdict, verdict_label


class RunState(str, Enum):
    """The canonical lifecycle state of a single run."""

    # Non-terminal (a run is doing something; a spinner here is never a
    # progress *claim*).
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    VERIFYING = "verifying"
    # Terminal (immutable; one-for-one with CompletionVerdict).
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


TERMINAL_STATES: frozenset[RunState] = frozenset(
    {
        RunState.COMPLETED,
        RunState.PARTIAL,
        RunState.BLOCKED,
        RunState.FAILED,
        RunState.CANCELLED,
        RunState.TIMEOUT,
    }
)

NON_TERMINAL_STATES: frozenset[RunState] = frozenset(RunState) - TERMINAL_STATES

# The active-phase order; a run advances forward through these, but may reach a
# terminal from any of them (a provider can fail during preparing, a user can
# cancel while running, verification can find no evidence, ...).
_ACTIVE_ORDER: tuple[RunState, ...] = (
    RunState.QUEUED,
    RunState.PREPARING,
    RunState.RUNNING,
    RunState.VERIFYING,
)


def _legal_transitions() -> dict[RunState, frozenset[RunState]]:
    table: dict[RunState, frozenset[RunState]] = {}
    for index, state in enumerate(_ACTIVE_ORDER):
        # Forward to any later active phase, plus any terminal state.
        forward = set(_ACTIVE_ORDER[index + 1 :])
        table[state] = frozenset(forward | set(TERMINAL_STATES))
    # Terminal states are immutable — no legal transition leaves them.
    for state in TERMINAL_STATES:
        table[state] = frozenset()
    return table


_TRANSITIONS = _legal_transitions()

# Non-terminal labels; terminal labels come from the shared verdict vocabulary
# (#396) so the live state and the final verdict never disagree ("Timed out").
_ACTIVE_LABELS = {
    RunState.QUEUED: "Queued",
    RunState.PREPARING: "Preparing",
    RunState.RUNNING: "Running",
    RunState.VERIFYING: "Verifying",
}


def is_terminal(state: RunState | str) -> bool:
    """True when ``state`` is a terminal (immutable) run state."""

    return _coerce(state) in TERMINAL_STATES


def can_transition(current: RunState | str, nxt: RunState | str) -> bool:
    """True when moving ``current -> nxt`` is a legal transition.

    A terminal state is immutable (no outbound transition), and a cancel is
    legal from any non-terminal state (see :func:`cancel`).
    """

    return _coerce(nxt) in _TRANSITIONS[_coerce(current)]


def transition(current: RunState | str, nxt: RunState | str) -> RunState:
    """Apply a transition, returning the new state, or the current one unchanged
    when the move is illegal — the machine never raises on a bad edge, it simply
    refuses it (mirrors the front-end ``message-state`` contract)."""

    current_state = _coerce(current)
    next_state = _coerce(nxt)
    return next_state if can_transition(current_state, next_state) else current_state


def cancel(current: RunState | str) -> RunState:
    """Cancel from any non-terminal state; a terminal state is left untouched
    (you cannot un-finish a run)."""

    current_state = _coerce(current)
    if current_state in TERMINAL_STATES:
        return current_state
    return RunState.CANCELLED


def run_state_for_verdict(verdict: CompletionVerdict | str) -> RunState:
    """Map a completion verdict to its canonical terminal run state (1:1)."""

    value = str(getattr(verdict, "value", verdict) or "").strip().lower()
    return RunState(value)


def label(state: RunState | str) -> str:
    """The one user-facing label for a run state, shared by every surface."""

    run_state = _coerce(state)
    if run_state in TERMINAL_STATES:
        return verdict_label(run_state.value)
    return _ACTIVE_LABELS[run_state]


def _coerce(state: Any) -> RunState:
    if isinstance(state, RunState):
        return state
    return RunState(str(getattr(state, "value", state) or "").strip().lower())
