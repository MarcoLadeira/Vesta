"""Canonical, provider-independent completion truth.

Legacy runner and UI status strings remain supported during the alpha, but
only :class:`CompletionState.COMPLETED` is allowed to map to an answered
status.  In particular, a legacy payload carrying a stop reason is never
silently upgraded to success.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping


class CompletionState(str, Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NEEDS_USER_INPUT = "needs_user_input"
    NEEDS_CONSENT = "needs_consent"
    RETRYABLE_PROVIDER_ERROR = "retryable_provider_error"
    PROVIDER_BLOCKED = "provider_blocked"
    STUCK_NO_PROGRESS = "stuck_no_progress"
    FAILED = "failed"


class ProviderBlockedReason(str, Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    BILLING = "billing"
    PANIC = "panic"
    DAILY_CAP = "daily_cap"
    MONTHLY_CAP = "monthly_cap"
    TASK_CAP = "task_cap"


_ANSWERED_STATUSES = {
    "answered",
    "answered_by_account",
    "answered_by_free_api",
    "answered_locally",
    "cache_hit",
    "done",
    "completed",
}
_CANCELLED_STATUSES = {"cancelled", "canceled", "user_cancelled", "aborted"}
_USER_INPUT_STATUSES = {"needs_user_input", "needs_input", "question"}
_CONSENT_STATUSES = {
    "needs_confirmation",
    "needs_paid_confirmation",
    "needs_consent",
    "read_only",
}
_RETRYABLE_STATUSES = {
    "retryable_provider_error",
    "provider_unavailable",
    "temporarily_unavailable",
    "timeout",
}
_BLOCKED_STATUSES = {"provider_blocked", "blocked"}
_STUCK_STATUSES = {"incomplete", "stuck", "stuck_no_progress"}

_CANCELLED_REASONS = _CANCELLED_STATUSES | {"cancel_requested"}
_USER_INPUT_REASONS = _USER_INPUT_STATUSES
_CONSENT_REASONS = _CONSENT_STATUSES | {"consent_required", "approval_required"}
_RETRYABLE_REASONS = _RETRYABLE_STATUSES | {
    "transient_error",
    "network_error",
    "connection_error",
}
_BLOCKED_REASONS = {reason.value for reason in ProviderBlockedReason}
_STUCK_REASONS = _STUCK_STATUSES | {
    "tool_budget_exhausted",
    "repeated_failure",
    "repeated_success",
    "no_progress",
    "exploration_limit",
    "controller_timeout",
}


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError("checkpoint values must be JSON-compatible")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _state_for_stop_reason(reason: str) -> CompletionState:
    if reason in _CANCELLED_REASONS:
        return CompletionState.CANCELLED
    if reason in _USER_INPUT_REASONS:
        return CompletionState.NEEDS_USER_INPUT
    if reason in _CONSENT_REASONS:
        return CompletionState.NEEDS_CONSENT
    if reason in _BLOCKED_REASONS:
        return CompletionState.PROVIDER_BLOCKED
    if reason in _RETRYABLE_REASONS:
        return CompletionState.RETRYABLE_PROVIDER_ERROR
    if reason in _STUCK_REASONS:
        return CompletionState.STUCK_NO_PROGRESS
    return CompletionState.FAILED


def completion_state_from_legacy(result: Mapping[str, Any] | None) -> CompletionState:
    """Read a typed or legacy result without ever inferring false success.

    A non-empty ``stopped_reason`` wins even if an older layer labelled the
    payload as answered.  Unknown legacy states fail closed.
    """

    payload = result or {}
    stopped_reason = _normalized(payload.get("stopped_reason"))
    if stopped_reason:
        return _state_for_stop_reason(stopped_reason)

    explicit = _normalized(payload.get("completion_state"))
    if explicit:
        try:
            return CompletionState(explicit)
        except ValueError:
            return CompletionState.FAILED

    status = _normalized(payload.get("status"))
    if status in _ANSWERED_STATUSES:
        return CompletionState.COMPLETED
    if status in _CANCELLED_STATUSES:
        return CompletionState.CANCELLED
    if status in _USER_INPUT_STATUSES:
        return CompletionState.NEEDS_USER_INPUT
    if status in _CONSENT_STATUSES:
        return CompletionState.NEEDS_CONSENT
    if status in _RETRYABLE_STATUSES:
        return CompletionState.RETRYABLE_PROVIDER_ERROR
    if status in _BLOCKED_STATUSES:
        return CompletionState.PROVIDER_BLOCKED
    if status in _STUCK_STATUSES:
        return CompletionState.STUCK_NO_PROGRESS
    return CompletionState.FAILED


_LEGACY_STATUS_BY_STATE = {
    CompletionState.CANCELLED: "cancelled",
    CompletionState.NEEDS_USER_INPUT: "needs_user_input",
    CompletionState.NEEDS_CONSENT: "needs_confirmation",
    CompletionState.RETRYABLE_PROVIDER_ERROR: "retryable_provider_error",
    CompletionState.PROVIDER_BLOCKED: "provider_blocked",
    CompletionState.STUCK_NO_PROGRESS: "incomplete",
    CompletionState.FAILED: "failed",
}


def result_is_completed(result: Mapping[str, Any] | None) -> bool:
    """True only when a runner/pipeline result genuinely completed.

    The single honest gate for "may this surface say the task finished?".  It
    reads the canonical completion truth (``completion_state`` / ``stopped_reason``
    win over any legacy ``status``), so a stuck, blocked, cancelled, or
    needs-input run is never reported as done even when an older layer left its
    status as "answered".
    """

    return completion_state_from_legacy(result) is CompletionState.COMPLETED


def legacy_status_for_completion(
    state: CompletionState | str,
    *,
    completed_status: str = "answered",
) -> str:
    """Return the temporary compatibility status for a canonical state."""

    try:
        canonical = (
            state if isinstance(state, CompletionState) else CompletionState(state)
        )
    except ValueError:
        return "failed"
    if canonical is CompletionState.COMPLETED:
        candidate = _normalized(completed_status)
        return completed_status if candidate in _ANSWERED_STATUSES else "answered"
    return _LEGACY_STATUS_BY_STATE[canonical]


@dataclass(frozen=True)
class CompletionResult:
    """Small immutable result shared by runners, persistence, and surfaces."""

    state: CompletionState
    answer: str = ""
    user_question: str = ""
    stopped_reason: str = ""
    blocked_reason: ProviderBlockedReason | None = None
    recovery_action: str = ""
    error: str = ""
    checkpoint: Mapping[str, Any] | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.state, CompletionState):
            object.__setattr__(self, "state", CompletionState(str(self.state)))
        if self.blocked_reason is not None and not isinstance(
            self.blocked_reason, ProviderBlockedReason
        ):
            object.__setattr__(
                self,
                "blocked_reason",
                ProviderBlockedReason(str(self.blocked_reason)),
            )
        if (
            self.state is CompletionState.PROVIDER_BLOCKED
            and self.blocked_reason is None
        ):
            raise ValueError("provider_blocked completion requires blocked_reason")
        if (
            self.state is not CompletionState.PROVIDER_BLOCKED
            and self.blocked_reason is not None
        ):
            raise ValueError("blocked_reason is only valid for provider_blocked")
        if self.state is CompletionState.COMPLETED and self.stopped_reason:
            raise ValueError("completed result cannot carry a stopped_reason")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer")
        if self.checkpoint is not None:
            if not isinstance(self.checkpoint, Mapping):
                raise TypeError("checkpoint must be a mapping or None")
            object.__setattr__(self, "checkpoint", _freeze(self.checkpoint))

    def to_dict(self) -> dict[str, Any]:
        return {
            "completion_schema_version": int(self.schema_version),
            "completion_state": self.state.value,
            "answer": self.answer,
            "user_question": self.user_question,
            "stopped_reason": self.stopped_reason,
            "provider_blocked_reason": (
                self.blocked_reason.value if self.blocked_reason is not None else None
            ),
            "recovery_action": self.recovery_action,
            "error": self.error,
            "checkpoint": _thaw(self.checkpoint),
            "status": legacy_status_for_completion(self.state),
        }
