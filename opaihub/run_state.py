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
    # The run stopped and handed control back to the user — an approval card, a
    # cloud confirmation, a model choice. This is NOT terminal: the user's
    # answer resumes the very same work. Before this state existed the pipeline
    # reported these turns as `blocked`, an immutable terminal, so an ordinary
    # "shall I run this command?" was recorded in history, receipts and the
    # ledger as a run that could not proceed (#295, invariant 12: honest
    # uncertainty; the lifecycle's `waiting_user`/`awaiting_approval`).
    AWAITING_INPUT = "awaiting_input"
    # Stop was pressed and acknowledged, but controllable work has not been
    # proven stopped yet. Distinct from CANCELLED so the acknowledgement is
    # immediate and honest without claiming teardown already finished
    # (#295 lifecycle: `cancel_requested`; invariant 9).
    CANCEL_REQUESTED = "cancel_requested"
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


# Non-terminal states that sit outside the linear phase order. Both are
# *interruptions* of the progression rather than steps in it, so they are
# reachable from any live phase and can hand control back to any of them.
_INTERRUPT_STATES: frozenset[RunState] = frozenset(
    {RunState.AWAITING_INPUT, RunState.CANCEL_REQUESTED}
)


def _legal_transitions() -> dict[RunState, frozenset[RunState]]:
    table: dict[RunState, frozenset[RunState]] = {}
    for index, state in enumerate(_ACTIVE_ORDER):
        # Forward to any later active phase, plus any terminal state.
        forward = set(_ACTIVE_ORDER[index + 1 :])
        allowed = forward | set(TERMINAL_STATES)
        # Stop can be pressed during any live phase, verification included.
        allowed.add(RunState.CANCEL_REQUESTED)
        # Asking the user is only legal *before* verification. Verification
        # judges evidence that already exists; it has no question to ask, and
        # the pre-existing invariant that verifying leads only to a terminal is
        # worth keeping. A future `repairing` state is what would change this,
        # and it should have to change it deliberately.
        if state is not RunState.VERIFYING:
            allowed.add(RunState.AWAITING_INPUT)
        table[state] = frozenset(allowed)
    # Answering resumes the work: a run returns to a live phase (an approved
    # command resumes execution) or ends. Not back into verifying, for the same
    # reason it cannot stop to ask from there.
    table[RunState.AWAITING_INPUT] = frozenset(
        {RunState.QUEUED, RunState.PREPARING, RunState.RUNNING}
        | {RunState.CANCEL_REQUESTED}
        | set(TERMINAL_STATES)
    )
    # A requested cancel only ends. It may still end as COMPLETED: pressing Stop
    # while the last step was already finishing is a race, and reporting that as
    # cancelled would be a lie about what actually happened.
    table[RunState.CANCEL_REQUESTED] = frozenset(TERMINAL_STATES)
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
    # Names the user as the thing being waited on, not the run as stuck.
    RunState.AWAITING_INPUT: "Waiting for you",
    RunState.CANCEL_REQUESTED: "Stopping",
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


# The GUI store (assets/web/message-state.js) and the Calm Stream (activity.py)
# refine the active phases into a richer presentation vocabulary
# (authenticating/sending/waiting/streaming/retrying). This adapter maps every
# such presentation state back to its canonical run state, so a surface with a
# finer phase language still speaks one lifecycle (#379) — it refines a canonical
# state, it never invents a new one. Canonical and terminal states map to
# themselves.
_PRESENTATION_TO_CANONICAL = {
    "retrying": RunState.QUEUED,
    "authenticating": RunState.PREPARING,
    "sending": RunState.RUNNING,
    "waiting": RunState.RUNNING,
    "streaming": RunState.RUNNING,
    # #295 names these `waiting_user` / `awaiting_approval` / `cancel_requested`.
    # Accepting the epic's words as aliases means a surface or document written
    # against the epic converges here instead of starting a rival vocabulary.
    "waiting_user": RunState.AWAITING_INPUT,
    "awaiting_approval": RunState.AWAITING_INPUT,
    "awaiting_input": RunState.AWAITING_INPUT,
    "cancel_requested": RunState.CANCEL_REQUESTED,
}

# Pipeline statuses that hand control back to the user *and can be resumed by
# their answer*. Each of these is re-sent as the same task plus one added
# authority (an approved command, a one-shot edit grant, cloud consent, a limit
# waiver), so the run genuinely continues.
#
# The test for membership is deliberately narrow: **does the user's answer
# resume this run?** `needs_model` deliberately fails it. That status means Auto
# found nothing it could call, and the remedy is to configure a provider in
# Settings and start again — a new run, not a continuation. Filing it here would
# make the `resumable` flag a false claim and would dress a genuine dead end up
# as a question, which is the opposite of the honesty this state exists for.
AWAITING_INPUT_STATUSES: frozenset[str] = frozenset(
    {
        "needs_command_approval",
        "needs_edit_approval",
        "needs_free_confirmation",
        "needs_auto_confirmation",
        "needs_limit_confirmation",
        "needs_confirmation",
    }
)


def is_awaiting_input(status: str) -> bool:
    """True when a pipeline status means "OPai handed control back to you"."""

    return str(status or "").strip().lower() in AWAITING_INPUT_STATUSES


def canonical_for(presentation_state: RunState | str) -> RunState:
    """Map a presentation/store state to its canonical run state.

    Raises ``ValueError`` on a state that is neither a canonical run state nor a
    known presentation refinement — an unmapped surface state is a bug, never a
    silently-swallowed unknown.
    """

    key = (
        str(getattr(presentation_state, "value", presentation_state) or "")
        .strip()
        .lower()
    )
    mapped = _PRESENTATION_TO_CANONICAL.get(key)
    return mapped if mapped is not None else RunState(key)


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
