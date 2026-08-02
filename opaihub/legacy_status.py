"""Explicit import/output adapters for temporary legacy status strings.

Legacy strings have no transition, retry, policy, or completion authority
outside this module. Removal is allowed only after telemetry records zero
authoritative legacy reads and writes for one supported release.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from threading import Lock
from typing import Any, Mapping

from .generated_lifecycle import LEGACY_STATUS_MAP, TERMINAL_STATE_IDS
from .run_result import RunResult


REMOVAL_GATE = "zero authoritative legacy reads and writes for one supported release"

_COUNTERS = {
    "imports": 0,
    "exports": 0,
    "authoritative_reads": 0,
    "authoritative_writes": 0,
}
_COUNTER_LOCK = Lock()

_ANSWERED = frozenset(
    {
        "answered",
        "answered_by_account",
        "answered_by_free_api",
        "answered_locally",
        "cache_hit",
        "completed",
        "done",
    }
)
_CANCELLED = frozenset({"aborted", "canceled", "cancelled", "user_cancelled"})
_USER_INPUT = frozenset({"needs_input", "needs_user_input", "question"})
_CONSENT = frozenset(
    {
        "needs_auto_confirmation",
        "needs_command_approval",
        "needs_confirmation",
        "needs_consent",
        "needs_edit_approval",
        "needs_free_confirmation",
        "needs_limit_confirmation",
        "needs_paid_confirmation",
        "read_only",
    }
)
_RETRYABLE = frozenset(
    {
        "provider_unavailable",
        "retryable_provider_error",
        "temporarily_unavailable",
        "timeout",
    }
)
_BLOCKED = frozenset({"blocked", "provider_blocked"})
_STUCK = frozenset({"incomplete", "stuck", "stuck_no_progress"})

_CANCELLED_REASONS = _CANCELLED | {"cancel_requested"}
_USER_INPUT_REASONS = _USER_INPUT
_CONSENT_REASONS = _CONSENT | {"approval_required", "consent_required"}
_RETRYABLE_REASONS = _RETRYABLE | {
    "connection_error",
    "network_error",
    "transient_error",
}
_BLOCKED_REASONS = frozenset(
    {
        "auth",
        "billing",
        "daily_cap",
        "monthly_cap",
        "panic",
        "quota",
        "rate_limit",
        "task_cap",
    }
)
_STUCK_REASONS = _STUCK | {
    "controller_timeout",
    "exploration_limit",
    "no_progress",
    "repeated_failure",
    "repeated_success",
    "tool_budget_exhausted",
}

_LEGACY_OUTPUT_BY_STATE = {
    "awaiting_input": "needs_user_input",
    "blocked": "provider_blocked",
    "cancelled": "cancelled",
    "completed": "answered",
    "failed": "failed",
    "needs_attention": "failed",
    "partial": "incomplete",
    "timeout": "timeout",
    # Existing CompletionState vocabulary during the migration window.
    "needs_consent": "needs_confirmation",
    "needs_user_input": "needs_user_input",
    "provider_blocked": "provider_blocked",
    "retryable_provider_error": "retryable_provider_error",
    "stuck_no_progress": "incomplete",
}


def _normalized(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower().replace(
        "-", "_"
    ).replace(" ", "_")


def _count(kind: str) -> None:
    with _COUNTER_LOCK:
        _COUNTERS[kind] += 1
        authority = "authoritative_reads" if kind == "imports" else "authoritative_writes"
        _COUNTERS[authority] += 1


def legacy_status_usage() -> dict[str, Any]:
    """Return an in-process telemetry snapshot and the removal gate."""

    with _COUNTER_LOCK:
        counters = dict(_COUNTERS)
    counters["removal_gate"] = REMOVAL_GATE
    return counters


def _timestamp(payload: Mapping[str, Any]) -> str:
    for key in ("final_transition_at", "finished_at", "updated_at", "timestamp"):
        value = str(payload.get(key) or "").strip()
        if value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return "1970-01-01T00:00:00Z"
            return value if parsed.tzinfo is not None else "1970-01-01T00:00:00Z"
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    identity = payload.get("identity")
    result = dict(identity) if isinstance(identity, Mapping) else {}
    for key in ("run_id", "attempt_id", "request_id"):
        if key in payload and key not in result:
            result[key] = payload[key]
    return result


def _safe_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    identity = _identity(payload)
    try:
        json.dumps(identity, allow_nan=False)
    except (TypeError, ValueError):
        return {}
    return identity


def _schema_marker(value: Any) -> Any:
    if value is None or isinstance(value, (str, int)) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return type(value).__name__


def _degraded_legacy_result(
    payload: Mapping[str, Any], *, status: str, compatibility: str, detail: str
) -> RunResult:
    return RunResult.from_payload(
        state="needs_attention",
        reason_detail=detail,
        final_transition_at=_timestamp(payload),
        reconciled=True,
        identity=_safe_identity(payload),
        recovery={"automatic_retry": False, "reason": "manual_review"},
        compatibility={
            "state": compatibility,
            "source_schema_version": _schema_marker(payload.get("schema_version", 0)),
            "legacy_status": status,
            "automatic_retry": False,
        },
    )


def legacy_status_to_result(payload: Mapping[str, Any]) -> RunResult:
    """Import one legacy terminal payload without granting its status authority.

    Unknown status values become incompatible ``needs_attention``. A legacy
    success value is accepted as completed only when the same payload carries
    the verification, delivery, and cost-integrity evidence the canonical
    envelope requires.
    """

    if not isinstance(payload, Mapping):
        raise TypeError("legacy status payload must be a mapping")
    _count("imports")
    status = _normalized(payload.get("status"))
    state = LEGACY_STATUS_MAP.get(status)
    if state is None:
        return _degraded_legacy_result(
            payload,
            status=status,
            compatibility="incompatible",
            detail="Legacy status is incompatible with this lifecycle schema.",
        )
    if state not in TERMINAL_STATE_IDS:
        return _degraded_legacy_result(
            payload,
            status=status,
            compatibility="degraded",
            detail="Legacy status is non-terminal and cannot form a RunResult.",
        )

    authority = payload.get("authority")
    mutating = bool(
        payload.get("mutating")
        or (authority.get("mutating") if isinstance(authority, Mapping) else False)
    )
    try:
        return RunResult.from_payload(
            state=state,
            reason_detail=f"Imported reconciled legacy status '{status}'.",
            final_transition_at=_timestamp(payload),
            reconciled=True,
            mutating=mutating,
            identity=_identity(payload),
            provider=(payload.get("provider") if isinstance(payload.get("provider"), Mapping) else None),
            recovery={"automatic_retry": False, "reason": "none"},
            verification=(payload.get("verification") if isinstance(payload.get("verification"), Mapping) else None),
            delivery=(payload.get("delivery") if isinstance(payload.get("delivery"), Mapping) else None),
            economics=(
                payload.get("economics")
                if isinstance(payload.get("economics"), Mapping)
                else payload.get("cost")
                if isinstance(payload.get("cost"), Mapping)
                else None
            ),
            authority=(authority if isinstance(authority, Mapping) else None),
            diagnostics=(payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else None),
            compatibility={
                "state": "legacy_import",
                "source_schema_version": payload.get("schema_version", 0),
                "legacy_status": status,
                "automatic_retry": False,
            },
        )
    except (TypeError, ValueError) as exc:
        return _degraded_legacy_result(
            payload,
            status=status,
            compatibility="degraded",
            detail=f"Legacy completion evidence is incomplete: {exc}",
        )


def legacy_completion_state(payload: Mapping[str, Any] | None) -> str:
    """Import the pre-RunResult CompletionState vocabulary at one boundary."""

    _count("imports")
    data = payload or {}
    reason = _normalized(data.get("stopped_reason"))
    if reason:
        if reason in _CANCELLED_REASONS:
            return "cancelled"
        if reason in _USER_INPUT_REASONS:
            return "needs_user_input"
        if reason in _CONSENT_REASONS:
            return "needs_consent"
        if reason in _BLOCKED_REASONS:
            return "provider_blocked"
        if reason in _RETRYABLE_REASONS:
            return "retryable_provider_error"
        if reason in _STUCK_REASONS:
            return "stuck_no_progress"
        return "failed"

    explicit = _normalized(data.get("completion_state"))
    if explicit in _LEGACY_OUTPUT_BY_STATE:
        return explicit
    if explicit:
        return "failed"

    status = _normalized(data.get("status"))
    if status in _ANSWERED:
        return "completed"
    if status in _CANCELLED:
        return "cancelled"
    if status in _USER_INPUT:
        return "needs_user_input"
    if status in _CONSENT:
        return "needs_consent"
    if status in _RETRYABLE:
        return "retryable_provider_error"
    if status in _BLOCKED:
        return "provider_blocked"
    if status in _STUCK:
        return "stuck_no_progress"
    return "failed"


def legacy_status_for_completion_state(
    state: Any, *, completed_status: str = "answered"
) -> str:
    """Write one output-only status string and record the compatibility write."""

    _count("exports")
    canonical = _normalized(state)
    if canonical == "completed":
        preferred = _normalized(completed_status)
        return completed_status if preferred in _ANSWERED else "answered"
    return _LEGACY_OUTPUT_BY_STATE.get(canonical, "failed")
