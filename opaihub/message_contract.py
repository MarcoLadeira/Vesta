"""The message contract: one routing decision, made before any model runs.

Implements the central recommendation of the Cursor-consistency research: define
a **message contract before any model is invoked**, so that runtime behaviour is
governed state rather than a side effect of how a request happened to be worded.

The failure this attacks is *message-shape variance* — semantically similar
requests succeeding or failing depending on wording, length, or which provider
happened to pick them up. OPai already classified intent (`agent_policy`) and
task type (`model_intelligence.classify_task`) and ordered providers by cost and
reliability (`auto_router`), but nothing bound those into a single decision, and
nothing said which runtime *policies* followed from it. The result was that two
phrasings of the same request could take different tool budgets, different
fallback behaviour, and different amounts of context.

A contract assigns every message to exactly one **lane**, and the lane fixes the
policy:

``stable``
    Routine, bounded work — the common case. Modest tool budget, provider
    fallback allowed, transport blips retried. Predictable above all.

``explore``
    Discovery and research ("find me something to fix"). Read-only by
    construction, so it is cheap to let it roam: a wider tool budget, and its
    context is isolated so a long exploration cannot pollute the next request.

``long_horizon``
    Multi-file features, refactors, architecture. The budgets that a short task
    does not need and a long one silently dies without: more tool calls and
    more wall-clock.

``governed``
    Destructive, release, or credential-touching work. The one lane that
    **never silently changes provider**: rerouting "publish this release" to a
    different model after a failure is not a recovery, it is a second attempt at
    an irreversible action the user approved once, for one route. Fallback is
    refused; the user is asked instead.

Two properties matter as much as the lanes themselves:

* **Deterministic.** The same message and mode always produce the same contract.
  There is no clock, no randomness, and no provider state in this decision — so
  it can be replayed, asserted on, and shown to the user without caveat.
* **Transparent.** Every contract carries a plain-language ``reason``, and
  ``to_dict()`` is shaped for display. Route quality and route *trust* are
  separate problems; a router the user cannot see is one they cannot learn.

This module contains no I/O and no provider calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_policy import AgentMode, is_discovery_request, is_smalltalk_request

# Lane ids, ordered from least to most constrained. The order is meaningful:
# `_LANE_ORDER.index` resolves collisions when a message qualifies for more than
# one lane, and the most constrained lane always wins.
STABLE = "stable"
EXPLORE = "explore"
LONG_HORIZON = "long_horizon"
GOVERNED = "governed"

_LANE_ORDER = (EXPLORE, STABLE, LONG_HORIZON, GOVERNED)

# Task types (from hub/model-intelligence/task_taxonomy.yaml) whose work is
# inherently multi-file and long-running. A short-task budget is why these
# quietly stop half-finished.
_LONG_HORIZON_TASK_TYPES = frozenset({"feature_build", "architecture_design"})

# Task types that touch something irreversible or credential-bearing. Combined
# with AgentMode.DANGEROUS and AgentMode.SHIP below.
_GOVERNED_TASK_TYPES = frozenset({"release_security"})


@dataclass(frozen=True)
class MessageContract:
    """The complete pre-generation decision for one message.

    Frozen because a contract is a record of a decision, not a mutable
    scratchpad: anything that wants different policy must produce a new
    contract, which keeps the decision auditable.
    """

    lane: str
    reason: str
    task_type: str
    agent_mode: str
    # --- provider policy -------------------------------------------------
    # Whether a failure may be recovered by moving to a different provider.
    # False in the governed lane: re-running an irreversible action somewhere
    # else is not a recovery the user consented to.
    allow_provider_fallback: bool
    # Whether a transport blip earns a silent same-provider re-attempt.
    max_transient_retries: int
    # --- execution budgets ------------------------------------------------
    max_tool_calls: int
    max_active_seconds: float
    # NOTE: the context-compaction threshold is deliberately *not* a lane knob.
    # Raising it for long tasks is tempting — more history in view — but the
    # tool loop only clamps it against the provider's real context window when
    # `provider_context_chars` is known, and OPai does not know it for every
    # local model. A raised threshold would silently overflow a small-context
    # model's window. The shared conservative default stays until OPai can read
    # the true window per provider.
    # --- context policy ---------------------------------------------------
    # Keep this turn's exploration out of the main thread's context.
    isolate_context: bool
    # --- safety -----------------------------------------------------------
    requires_confirmation: bool
    matched_signals: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """A display- and log-shaped view. No secrets by construction."""
        return {
            "lane": self.lane,
            "laneLabel": lane_label(self.lane),
            "reason": self.reason,
            "taskType": self.task_type,
            "agentMode": self.agent_mode,
            "allowProviderFallback": self.allow_provider_fallback,
            "maxTransientRetries": self.max_transient_retries,
            "maxToolCalls": self.max_tool_calls,
            "maxActiveSeconds": self.max_active_seconds,
            "isolateContext": self.isolate_context,
            "requiresConfirmation": self.requires_confirmation,
            "matchedSignals": list(self.matched_signals),
        }


_LANE_LABELS = {
    STABLE: "Stable",
    EXPLORE: "Explore",
    LONG_HORIZON: "Long task",
    GOVERNED: "Governed",
}

# Per-lane runtime policy. Everything a lane changes lives here, so the policy
# is reviewable in one place instead of scattered through the pipeline.
_LANE_POLICY: dict[str, dict[str, Any]] = {
    STABLE: {
        "allow_provider_fallback": True,
        "max_transient_retries": 1,
        "max_tool_calls": 12,
        "max_active_seconds": 600.0,
        "isolate_context": False,
    },
    EXPLORE: {
        # Read-only by construction, so roaming is cheap and safe.
        "allow_provider_fallback": True,
        "max_transient_retries": 1,
        "max_tool_calls": 20,
        "max_active_seconds": 600.0,
        # A long search must not become the next request's baggage.
        "isolate_context": True,
    },
    LONG_HORIZON: {
        "allow_provider_fallback": True,
        "max_transient_retries": 2,
        "max_tool_calls": 40,
        "max_active_seconds": 1_800.0,
        "isolate_context": False,
    },
    GOVERNED: {
        # The defining rule of this lane. See the module docstring.
        "allow_provider_fallback": False,
        "max_transient_retries": 0,
        # Deliberately the same allowance as `stable`: this lane's safety comes
        # from refusing fallback and requiring confirmation, not from a tighter
        # budget that could strand a legitimate release half-finished.
        "max_tool_calls": 12,
        "max_active_seconds": 600.0,
        "isolate_context": False,
    },
}


def lane_label(lane: str) -> str:
    """The user-facing name for a lane."""
    return _LANE_LABELS.get(str(lane or ""), "Stable")


def lane_description(lane: str) -> str:
    """One sentence a user can act on, for the transparency surface."""
    return {
        STABLE: "Predictable routing with automatic recovery.",
        EXPLORE: "Read-only search, kept out of this chat's context.",
        LONG_HORIZON: "Extra tool and time budget for multi-file work.",
        GOVERNED: "Irreversible work — OPai will ask rather than reroute.",
    }.get(str(lane or ""), _LANE_LABELS[STABLE])


def _pick_lane(
    *,
    agent_mode: AgentMode,
    task_type: str,
    requires_confirmation: bool,
    discovery: bool,
) -> tuple[str, str]:
    """Return ``(lane, reason)``. Most constrained qualifying lane wins."""
    candidates: list[tuple[str, str]] = []

    if agent_mode is AgentMode.DANGEROUS:
        candidates.append(
            (GOVERNED, "This request can destroy or overwrite work.")
        )
    if agent_mode is AgentMode.SHIP or task_type in _GOVERNED_TASK_TYPES:
        candidates.append(
            (GOVERNED, "This request publishes, releases, or touches credentials.")
        )
    if requires_confirmation:
        candidates.append((GOVERNED, "This kind of task always needs confirmation."))
    if discovery:
        candidates.append((EXPLORE, "This request searches for work rather than doing it."))
    if task_type in _LONG_HORIZON_TASK_TYPES:
        candidates.append(
            (LONG_HORIZON, "Multi-file work needs a longer tool and time budget.")
        )
    if not candidates:
        return STABLE, "Routine request on the predictable route."
    return max(candidates, key=lambda item: _LANE_ORDER.index(item[0]))


def resolve_message_contract(
    project_root: Path,
    message: str,
    *,
    agent_mode: AgentMode,
    selected_mode: str = "",
) -> MessageContract:
    """Decide everything about how this message will run, before it runs.

    Deterministic: same message and mode in, same contract out. ``project_root``
    is read only for the on-disk task taxonomy, which is static configuration.
    """
    from .model_intelligence import classify_task

    text = str(message or "")
    try:
        classification = classify_task(project_root, text)
    except (OSError, ValueError):
        # A missing or unreadable taxonomy must degrade to the predictable
        # lane, never break the turn.
        classification = {}
    task_type = str(classification.get("task_type") or "general_coding")
    requires_confirmation = bool(classification.get("requires_confirmation"))
    matched = tuple(str(s) for s in (classification.get("matched_signals") or []))

    # Smalltalk is not a coding task at all; treating "hi" as stable-lane work
    # is what makes a greeting spend a tool budget.
    discovery = is_discovery_request(text) and not is_smalltalk_request(text)

    lane, reason = _pick_lane(
        agent_mode=agent_mode,
        task_type=task_type,
        requires_confirmation=requires_confirmation,
        discovery=discovery,
    )
    policy = _LANE_POLICY[lane]
    return MessageContract(
        lane=lane,
        reason=reason,
        task_type=task_type,
        agent_mode=agent_mode.value,
        allow_provider_fallback=bool(policy["allow_provider_fallback"]),
        max_transient_retries=int(policy["max_transient_retries"]),
        max_tool_calls=int(policy["max_tool_calls"]),
        max_active_seconds=float(policy["max_active_seconds"]),
        isolate_context=bool(policy["isolate_context"]),
        requires_confirmation=requires_confirmation
        or agent_mode is AgentMode.DANGEROUS,
        matched_signals=matched,
    )
