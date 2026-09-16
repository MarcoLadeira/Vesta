"""Per-turn execution guard (Task 6).

Every provider turn — the first and every continuation — passes through
:meth:`ExecutionGuard.check` *before* the request is sent.  It enforces the same
authority Vesta has always enforced at dispatch, but now on each turn of a
continuous run: cancellation, panic mode, paid/cloud consent, provider
auth/rate/quota/billing health, and the task/daily/monthly dollar caps.

Free ($0) and local routes are allowed by default; positive token/request
thresholds are *advisory* and never block dispatch (that is the usage advisory's
job, not the guard's).  A blocked turn yields a typed, honest state — never a
silent stop and never a fake completion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .budget import budget_gate
from .completion import CompletionState, ProviderBlockedReason


class GuardOutcome(str, Enum):
    ALLOW = "allow"
    CANCEL = "cancel"
    NEEDS_CONSENT = "needs_consent"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ExecutionGuardContext:
    """Everything the guard needs to judge one upcoming provider turn."""

    project_root: Path
    turn_index: int
    tier: str = "L2"
    provider_type: str = "free_api"
    is_free: bool = False
    estimated_cost_usd: float = 0.0
    estimated_tokens: int = 0
    allow_cloud: bool = False
    destructive: bool = False
    cancel: Any = None
    # Optional provider health signals (auth/rate_limit/quota/billing -> truthy
    # when blocked).  Sourced from provider capabilities; injected for tests.
    provider_status: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class GuardDecision:
    """The guard's verdict for one turn."""

    outcome: GuardOutcome
    blocked_reason: ProviderBlockedReason | None = None
    detail: str = ""

    @property
    def allowed(self) -> bool:
        return self.outcome is GuardOutcome.ALLOW

    def to_completion_state(self) -> CompletionState:
        if self.outcome is GuardOutcome.CANCEL:
            return CompletionState.CANCELLED
        if self.outcome is GuardOutcome.NEEDS_CONSENT:
            return CompletionState.NEEDS_CONSENT
        if self.outcome is GuardOutcome.BLOCKED:
            return CompletionState.PROVIDER_BLOCKED
        raise ValueError("an allowed decision has no non-success completion state")


_PROVIDER_SIGNALS: tuple[tuple[str, ProviderBlockedReason], ...] = (
    ("auth", ProviderBlockedReason.AUTH),
    ("rate_limit", ProviderBlockedReason.RATE_LIMIT),
    ("quota", ProviderBlockedReason.QUOTA),
    ("billing", ProviderBlockedReason.BILLING),
)

BudgetGate = Callable[..., Mapping[str, Any]]


class ExecutionGuard:
    """Runs the financial/permission/provider checks before every provider turn."""

    def __init__(self, *, gate: BudgetGate = budget_gate) -> None:
        self._gate = gate

    def check(self, context: ExecutionGuardContext) -> GuardDecision:
        if _cancelled(context.cancel):
            return GuardDecision(
                GuardOutcome.CANCEL, detail="cancelled before dispatch"
            )

        # Provider health blocks the turn before any spend is attempted.
        status = context.provider_status or {}
        for signal, reason in _PROVIDER_SIGNALS:
            if status.get(signal):
                return GuardDecision(
                    GuardOutcome.BLOCKED, reason, detail=f"provider {signal}"
                )

        gate = self._gate(
            context.project_root,
            next_cost_usd=float(context.estimated_cost_usd or 0.0),
            tier=context.tier,
            provider_type=context.provider_type,
            estimated_tokens=int(context.estimated_tokens or 0),
            destructive=bool(context.destructive),
        )
        if gate.get("denied"):
            return GuardDecision(
                GuardOutcome.BLOCKED,
                _blocked_reason_from_gate(gate),
                detail="; ".join(gate.get("reasons") or []),
            )
        if gate.get("requires_confirmation") and not context.allow_cloud:
            return GuardDecision(
                GuardOutcome.NEEDS_CONSENT,
                detail="; ".join(gate.get("reasons") or []),
            )
        return GuardDecision(GuardOutcome.ALLOW)


def _blocked_reason_from_gate(gate: Mapping[str, Any]) -> ProviderBlockedReason:
    if gate.get("panic"):
        return ProviderBlockedReason.PANIC
    reasons = " ".join(gate.get("reasons") or []).lower()
    if "daily budget" in reasons:
        return ProviderBlockedReason.DAILY_CAP
    if "monthly budget" in reasons:
        return ProviderBlockedReason.MONTHLY_CAP
    # Remaining policy denials (per-task budget, tier/destructive policy) map to
    # the task cap — the tightest, most local financial ceiling.
    return ProviderBlockedReason.TASK_CAP


def _cancelled(cancel: Any) -> bool:
    return bool(cancel is not None and getattr(cancel, "is_set", lambda: False)())
