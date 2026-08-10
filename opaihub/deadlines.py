"""Typed timeout/deadline evidence shared by provider routes (#683)."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1

TASK_DEADLINE = "task_deadline"
PROVIDER_IDLE_TIMEOUT = "provider_idle_timeout"
PROVIDER_TURN_TIMEOUT = "provider_turn_timeout"
UNKNOWN_TIMEOUT = "unknown_timeout"


def timeout_event(
    *,
    origin: str,
    owner: str,
    configured_seconds: float | None,
    elapsed_seconds: float | None = None,
    provider_responsive: bool | None = None,
    last_activity_seconds_ago: float | None = None,
    lane: str | None = None,
    phase: str | None = None,
) -> dict[str, Any]:
    """Build a small, JSON-safe timeout record.

    A timeout is a cause, not just a boolean. In particular, an OPai task
    deadline expiring while a provider is active must not be rendered as
    provider silence.
    """
    if provider_responsive is True:
        condition = "responsive"
    elif provider_responsive is False:
        condition = "idle"
    else:
        condition = "unknown"
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timeout_origin": _origin(origin),
        "owner": str(owner or "unknown"),
        "configured_seconds": _finite_or_none(configured_seconds),
        "elapsed_seconds": _finite_or_none(elapsed_seconds),
        "provider_condition": condition,
        "retry_safety": "reconcile_before_retry",
    }
    last_activity = _finite_or_none(last_activity_seconds_ago)
    if last_activity is not None:
        event["last_activity_seconds_ago"] = last_activity
    if lane:
        event["lane"] = str(lane)
    if phase:
        event["phase"] = str(phase)
    return event


def _origin(value: str) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean else UNKNOWN_TIMEOUT


def _finite_or_none(value: float | None) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return round(max(0.0, number), 3)


def is_task_deadline(event: Any) -> bool:
    return isinstance(event, dict) and event.get("timeout_origin") == TASK_DEADLINE
