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

import threading
import time
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
        # The repair loop. #295's lifecycle writes it `verifying ↔ repairing?`:
        # verification finds a problem, work resumes to fix it, and the evidence
        # is judged again. The engine already does exactly this
        # (`reviewing_diff -> implementing/testing` in agent_runtime._FORWARD),
        # so forbidding it here made the canonical machine describe a lifecycle
        # the product does not have. Caught by test_runtime_phase_parity's
        # graph-projection check.
        if state is RunState.VERIFYING:
            allowed.add(RunState.RUNNING)
        # Asking the user is only legal *before* verification. Verification
        # produces a verdict on evidence that already exists — it can send the
        # run back to work (above), but it has no question of its own to ask.
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


# One exit code per terminal state, so a script can branch on *which* ending it
# got (#295 Workstream H: "CLI exit codes map deterministically to canonical
# terminal states"). Every non-completed ending used to collapse to 2, which
# made `timeout` — worth retrying — indistinguishable from `blocked`, which is
# a refusal that retrying will hit again, and from `partial`, where work
# actually landed.
#
# Chosen to keep the ordinary idiom working: 0 is success and everything else is
# non-zero, so `if ! opai ask ...` behaves exactly as before. 2 stays on
# `failed`, the code it already meant. 130 is the shell's SIGINT convention and
# was already returned for Ctrl+C, so cancellation keeps it.
_EXIT_CODES: dict[RunState, int] = {
    RunState.COMPLETED: 0,
    RunState.FAILED: 2,
    RunState.PARTIAL: 3,
    RunState.BLOCKED: 4,
    RunState.TIMEOUT: 5,
    RunState.CANCELLED: 130,
}


def exit_code_for(state: RunState | str) -> int:
    """The process exit code for a terminal run state.

    A non-terminal state has no exit code — the run has not ended — and asking
    for one is a bug in the caller, so it raises rather than inventing a
    success. ``1`` is deliberately unused: argparse and most shells already
    spend it on usage errors, and a lifecycle outcome must not be confused with
    "you typed the command wrong".
    """
    run_state = _coerce(state)
    if run_state not in TERMINAL_STATES:
        raise ValueError(f"{run_state.value} is not a terminal state")
    return _EXIT_CODES[run_state]


def is_terminal(state: RunState | str) -> bool:
    """True when ``state`` is a terminal (immutable) run state."""

    return _coerce(state) in TERMINAL_STATES


def can_transition(current: RunState | str, nxt: RunState | str) -> bool:
    """True when moving ``current -> nxt`` is a legal transition.

    A terminal state is immutable (no outbound transition), and a cancel is
    legal from any non-terminal state (see :func:`cancel`).
    """

    return _coerce(nxt) in _TRANSITIONS[_coerce(current)]


def transition(
    current: RunState | str, nxt: RunState | str, *, source: str = ""
) -> RunState:
    """Apply a transition, returning the new state, or the current one unchanged
    when the move is illegal — the machine never raises on a bad edge, it simply
    refuses it (mirrors the front-end ``message-state`` contract).

    A refused edge is *recorded* (see :func:`illegal_transitions`). #295's alpha
    gate 7 asks for "0 unhandled illegal transitions; every attempted violation
    is rejected **and observable**". Refusing silently satisfied only the first
    half: the machine knew something had tried to walk an impossible path and
    said nothing, so the one class of bug this model exists to catch left no
    trace. ``source`` is an optional caller label for the record.
    """

    current_state = _coerce(current)
    next_state = _coerce(nxt)
    if can_transition(current_state, next_state):
        return next_state
    _record_refusal(current_state, next_state, source)
    return current_state


# Refused transitions, newest last. Bounded so a runaway caller cannot grow it
# without limit, and counted separately so the total survives truncation — a
# capped list that silently drops the earliest evidence would recreate the
# blindness this exists to remove.
_MAX_REFUSALS = 64
_refusals: list[dict[str, Any]] = []
_refusal_count = 0
_refusal_lock = threading.Lock()


def _record_refusal(current: RunState, nxt: RunState, source: str) -> None:
    global _refusal_count
    entry = {
        "from": current.value,
        "to": nxt.value,
        # A closed label, never free text from a provider or a prompt: this
        # record is read by diagnostics and must not become a place a secret
        # can land.
        "source": "".join(
            ch for ch in str(source or "unknown").lower() if ch.isalnum() or ch in "._-"
        )[:64]
        or "unknown",
        "at": time.time(),
    }
    with _refusal_lock:
        _refusal_count += 1
        _refusals.append(entry)
        if len(_refusals) > _MAX_REFUSALS:
            del _refusals[0]


def illegal_transitions() -> dict[str, Any]:
    """Every refused transition this process has seen.

    ``count`` is the true total; ``recent`` is the bounded tail. A non-zero
    count is a defect report, not a metric to be tolerated — gate 7 requires it
    to be zero across the release matrix.
    """
    with _refusal_lock:
        return {"count": _refusal_count, "recent": [dict(item) for item in _refusals]}


def reset_illegal_transitions() -> None:
    """Clear the record. For tests and for a fresh diagnostic window only."""
    global _refusal_count
    with _refusal_lock:
        _refusal_count = 0
        _refusals.clear()


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

# `opaihub.agent_runtime.RuntimePhase` is the engine's *working* vocabulary: a
# finer, workflow-shaped account of what a run is doing (planning, testing,
# preparing a PR). It is a legitimate refinement — but #295 allows exactly one
# lifecycle, so every phase must declare which canonical state it refines.
#
# Fourteen of the eighteen phases had no canonical mapping at all, which is the
# semantic drift Phase 0 of the epic exists to freeze: two engine-side
# vocabularies, one canonical but unused for live transitions, one used but
# parallel. Keyed by value rather than by the enum so this module stays
# import-free; `test_runtime_phase_parity` asserts the mapping is total, so a
# new phase cannot be added without deciding what it means here.
_RUNTIME_PHASE_TO_CANONICAL: dict[str, RunState] = {
    # Nothing has started yet.
    "idle": RunState.QUEUED,
    # Working out what to do and what it applies to — all before execution.
    "intent_resolved": RunState.PREPARING,
    "repo_resolved": RunState.PREPARING,
    "issue_selected": RunState.PREPARING,
    "context_gathering": RunState.PREPARING,
    "planning": RunState.PREPARING,
    # Stopped to ask. The canonical state this refines is the one added for
    # exactly this case (#295): waiting on the user is not an ending.
    "awaiting_approval": RunState.AWAITING_INPUT,
    # Doing the work. `repairing` is a second attempt at it, not a phase of
    # judging evidence, so it refines RUNNING rather than VERIFYING.
    "implementing": RunState.RUNNING,
    "testing": RunState.RUNNING,
    "repairing": RunState.RUNNING,
    # Judging what the work produced.
    "reviewing_diff": RunState.VERIFYING,
    # Delivery is still active work. #295 reserves a `delivering` state for it,
    # but nothing emits one yet and a state with no producer is decoration —
    # these refine RUNNING until that workstream lands.
    "preparing_pr": RunState.RUNNING,
    "pr_created": RunState.RUNNING,
    "merge_check_running": RunState.RUNNING,
    "merged": RunState.RUNNING,
    # Terminals, which already share their words with the canonical set.
    "blocked": RunState.BLOCKED,
    "failed": RunState.FAILED,
    "completed": RunState.COMPLETED,
}


def canonical_for_runtime_phase(phase: Any) -> RunState:
    """The canonical run state a ``RuntimePhase`` refines.

    Raises ``ValueError`` for a phase with no declared mapping — an unmapped
    phase is a second lifecycle in disguise, never a silently-tolerated unknown.
    """
    key = str(getattr(phase, "value", phase) or "").strip().lower()
    mapped = _RUNTIME_PHASE_TO_CANONICAL.get(key)
    if mapped is None:
        raise ValueError(f"runtime phase {key!r} has no canonical run state")
    return mapped


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
