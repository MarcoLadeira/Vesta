"""The one canonical task/run state machine, shared end to end (#379).

Every surface — engine, GUI store, CLI renderer, receipt, ledger — derives its
state word from this module and never invents its own. Divergent state language
is how "it said X but did Y" bugs are born; one contract kills that class.

The lifecycle is deliberately coarse and honest::

    queued -> preparing -> running -> verifying -> <terminal>

with completion verdicts projecting directly to same-named terminals and an
additional ``needs_attention`` terminal for incompatible canonical data.

Presentation-layer detail (the Calm Stream phases in ``activity.py`` —
authenticating/streaming/tool_call/…) stays underneath this model; it refines
"running", it never contradicts the canonical state.
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

from .completion import CompletionVerdict
from .generated_lifecycle import (
    DEGRADED_INPUTS,
    EXIT_CODES,
    LEGACY_STATE_MAP,
    LEGACY_STATUS_MAP,
    STATE_IDS,
    STATE_SPECS,
    TERMINAL_STATE_IDS,
    transition_spec,
)
from .legacy_alias_telemetry import STATE, observe as _observe_alias


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
    # Typed degraded/incompatible terminal for canonical data this version
    # cannot safely interpret.
    NEEDS_ATTENTION = "needs_attention"


if {state.value for state in RunState} != set(STATE_IDS):
    raise RuntimeError(
        "RunState facade is stale relative to generated lifecycle states"
    )

TERMINAL_STATES: frozenset[RunState] = frozenset(
    RunState(state_id) for state_id in TERMINAL_STATE_IDS
)
NON_TERMINAL_STATES: frozenset[RunState] = frozenset(RunState) - TERMINAL_STATES


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
    return EXIT_CODES[run_state.value]


def is_terminal(state: RunState | str) -> bool:
    """True when ``state`` is a terminal (immutable) run state."""

    return _coerce(state) in TERMINAL_STATES


def can_transition(current: RunState | str, nxt: RunState | str) -> bool:
    """True when moving ``current -> nxt`` is a legal transition.

    A terminal state is immutable (no outbound transition), and a cancel is
    legal from any non-terminal state (see :func:`cancel`).
    """

    prior, target = _coerce(current), _coerce(nxt)
    return transition_spec(prior.value, target.value) is not None


def transition(
    current: RunState | str,
    nxt: RunState | str,
    *,
    source: str = "",
    project_root: Path | None = None,
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

    current_state, unknown_current = _coerce_transition(current)
    next_state, unknown_next = _coerce_transition(nxt)
    unknown_input = unknown_current or unknown_next
    if (
        not unknown_input
        and transition_spec(current_state.value, next_state.value) is not None
    ):
        return next_state
    _record_refusal(current_state, next_state, source)
    if project_root is not None:
        from .lifecycle_diagnostics import record_illegal_transition

        record_illegal_transition(
            Path(project_root),
            current_state.value,
            next_state.value,
            source,
        )
    return RunState.NEEDS_ATTENTION if unknown_input else current_state


# Refused transitions, newest last. Bounded so a runaway caller cannot grow it
# without limit, and counted separately so the total survives truncation — a
# capped list that silently drops the earliest evidence would recreate the
# blindness this exists to remove.
_MAX_REFUSALS = 64
_refusals: list[dict[str, Any]] = []
_refusal_count = 0
_refusal_lock = threading.Lock()


def _record_refusal(current: RunState, nxt: RunState, source: str) -> None:
    from .lifecycle_diagnostics import source_label

    global _refusal_count
    entry = {
        "from": current.value,
        "to": nxt.value,
        # A closed label, never free text from a provider or a prompt: this
        # record is read by diagnostics and must not become a place a secret
        # can land.
        "source": source_label(source),
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
    """Acknowledge cancellation without claiming teardown is reconciled."""

    current_state = _coerce(current)
    if current_state in TERMINAL_STATES:
        return current_state
    return RunState.CANCEL_REQUESTED


def run_state_for_verdict(verdict: CompletionVerdict | str) -> RunState:
    """Map a completion verdict to its same-named canonical terminal state."""

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
    alias: RunState(canonical) for alias, canonical in LEGACY_STATE_MAP.items()
}

# `vestahub.agent_runtime.RuntimePhase` is the engine's *working* vocabulary: a
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


_CANCEL_PHASE_TO_CANONICAL: dict[str, RunState] = {
    # These phases all describe a stop that is still in progress. They refine
    # CANCEL_REQUESTED and must never make a surface claim the run is over.
    "requested": RunState.CANCEL_REQUESTED,
    "acknowledged": RunState.CANCEL_REQUESTED,
    "draining": RunState.CANCEL_REQUESTED,
    "force_terminating": RunState.CANCEL_REQUESTED,
    # Only an observed end is allowed to project to the terminal state.
    "terminated": RunState.CANCELLED,
}


def canonical_for_cancel_phase(phase: Any) -> RunState:
    """The canonical run state a cancellation lifecycle phase refines."""

    key = str(getattr(phase, "value", phase) or "").strip().lower()
    mapped = _CANCEL_PHASE_TO_CANONICAL.get(key)
    if mapped is None:
        raise ValueError(f"cancel phase {key!r} has no canonical run state")
    return mapped


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
    status
    for status, canonical in LEGACY_STATUS_MAP.items()
    if canonical == RunState.AWAITING_INPUT.value
)


def is_awaiting_input(status: str) -> bool:
    """True when a pipeline status means "Vesta handed control back to you"."""

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
    if mapped is None:
        return RunState(key)
    # #612 AC9: record that a compatibility alias was actually reached, so the
    # deletion plan for these mappings has evidence instead of assuming every
    # one is live forever. Names only, in-process, never raises.
    _observe_alias(STATE, key)
    return mapped


def label(state: RunState | str) -> str:
    """The one user-facing label for a run state, shared by every surface."""

    run_state = _coerce(state)
    return str(STATE_SPECS[run_state.value]["label"])


def _coerce(state: Any) -> RunState:
    if isinstance(state, RunState):
        return state
    return RunState(str(getattr(state, "value", state) or "").strip().lower())


def _coerce_transition(state: Any) -> tuple[RunState, bool]:
    try:
        return _coerce(state), False
    except ValueError:
        degraded = DEGRADED_INPUTS["unknown_state"]["state"]
        return RunState(str(degraded)), True
