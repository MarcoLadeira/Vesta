"""Typed deadline policy and timeout evidence shared by provider routes (#683)."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

SCHEMA_VERSION = 1
DEADLINE_POLICY_VERSION = 1

PROVIDER_CONNECT_TIMEOUT = "provider_connect_timeout"
PROVIDER_FIRST_RESPONSE_TIMEOUT = "provider_first_response_timeout"
PROVIDER_IDLE_TIMEOUT = "provider_idle_timeout"
PROVIDER_TURN_TIMEOUT = "provider_turn_timeout"
TASK_DEADLINE = "task_deadline"
TOOL_TIMEOUT = "tool_timeout"
VERIFICATION_TIMEOUT = "verification_timeout"
DELIVERY_TIMEOUT = "delivery_timeout"
CANCEL_TEARDOWN_TIMEOUT = "cancel_teardown_timeout"
UNKNOWN_TIMEOUT = "unknown_timeout"

TIMEOUT_ORIGINS = frozenset(
    {
        PROVIDER_CONNECT_TIMEOUT,
        PROVIDER_FIRST_RESPONSE_TIMEOUT,
        PROVIDER_IDLE_TIMEOUT,
        PROVIDER_TURN_TIMEOUT,
        TASK_DEADLINE,
        TOOL_TIMEOUT,
        VERIFICATION_TIMEOUT,
        DELIVERY_TIMEOUT,
        CANCEL_TEARDOWN_TIMEOUT,
        UNKNOWN_TIMEOUT,
    }
)

RECONCILE_BEFORE_RETRY = "reconcile_before_retry"
AUTOMATIC_RETRY_ALLOWED = "automatic_retry_allowed"
NO_AUTOMATIC_RETRY = "no_automatic_retry"
_RETRY_SAFETY = frozenset(
    {RECONCILE_BEFORE_RETRY, AUTOMATIC_RETRY_ALLOWED, NO_AUTOMATIC_RETRY}
)


def _finite_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return round(max(0.0, number), 3)


def _positive(value: Any, field_name: str, *, optional: bool = False) -> float | None:
    number = _finite_or_none(value)
    if number is None:
        if optional:
            return None
        raise ValueError(f"{field_name} must be a finite positive number")
    if number <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return number


def _bounded(value: Any, limit: int = 256) -> str:
    return str(value or "").strip()[:limit]


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


@dataclass(frozen=True)
class DeadlineBudget:
    """One route's explicit task and provider-inactivity clocks.

    The budget is immutable and replayable. Account runners must receive both
    values from this policy boundary rather than inheriting a hidden idle
    default from whichever adapter happened to run the task.
    """

    task_deadline_seconds: float
    provider_idle_timeout_seconds: float | None
    lane: str = "stable"
    owner: str = "message_contract"
    policy_version: int = DEADLINE_POLICY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "task_deadline_seconds",
            _positive(self.task_deadline_seconds, "task_deadline_seconds"),
        )
        object.__setattr__(
            self,
            "provider_idle_timeout_seconds",
            _positive(
                self.provider_idle_timeout_seconds,
                "provider_idle_timeout_seconds",
                optional=True,
            ),
        )
        if (
            isinstance(self.policy_version, bool)
            or not isinstance(self.policy_version, int)
            or self.policy_version < 1
        ):
            raise ValueError("policy_version must be a positive integer")
        object.__setattr__(self, "lane", _bounded(self.lane, 64) or "stable")
        object.__setattr__(self, "owner", _bounded(self.owner, 64) or "unknown")

    @property
    def budget_id(self) -> str:
        return _stable_id("deadline", self.to_dict(include_id=False))

    def to_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": self.policy_version,
            "owner": self.owner,
            "lane": self.lane,
            "task_deadline_seconds": self.task_deadline_seconds,
            "provider_idle_timeout_seconds": self.provider_idle_timeout_seconds,
        }
        if include_id:
            payload["deadline_budget_id"] = self.budget_id
        return payload


@dataclass
class DeadlineClocks:
    """Independent task-duration and provider-inactivity clocks."""

    started_at: float
    task_deadline_seconds: float
    provider_idle_timeout_seconds: float | None
    first_provider_activity_at: float | None = None
    last_provider_activity_at: float | None = None

    def __post_init__(self) -> None:
        self.started_at = self._clock_value(self.started_at, "started_at")
        task_deadline = _positive(self.task_deadline_seconds, "task_deadline_seconds")
        if task_deadline is None:  # pragma: no cover - _positive raises first
            raise ValueError("task_deadline_seconds must be greater than zero")
        self.task_deadline_seconds = task_deadline
        self.provider_idle_timeout_seconds = _positive(
            self.provider_idle_timeout_seconds,
            "provider_idle_timeout_seconds",
            optional=True,
        )

    @staticmethod
    def _clock_value(value: Any, field_name: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{field_name} must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{field_name} must be a finite number")
        return number

    def note_provider_activity(self, observed_at: float) -> None:
        observed = self._clock_value(observed_at, "observed_at")
        if observed < self.started_at:
            raise ValueError("provider activity time precedes the task start")
        if self.first_provider_activity_at is None:
            self.first_provider_activity_at = observed
        if self.last_provider_activity_at is None:
            self.last_provider_activity_at = observed
        else:
            self.last_provider_activity_at = max(
                self.last_provider_activity_at, observed
            )

    def elapsed_at(self, observed_at: float) -> float:
        observed = self._clock_value(observed_at, "observed_at")
        return max(0.0, observed - self.started_at)

    def last_activity_age_at(self, observed_at: float) -> float:
        observed = self._clock_value(observed_at, "observed_at")
        baseline = self.last_provider_activity_at
        if baseline is None:
            baseline = self.started_at
        return max(0.0, observed - baseline)

    def provider_responsive_at(self, observed_at: float) -> bool:
        if self.last_provider_activity_at is None:
            return False
        idle = self.provider_idle_timeout_seconds
        return idle is None or self.last_activity_age_at(observed_at) < idle

    def expired_origin(
        self,
        observed_at: float,
        *,
        provider_work_in_flight: bool = False,
    ) -> str | None:
        """Return the clock that expired, giving the hard deadline priority."""

        if self.elapsed_at(observed_at) >= self.task_deadline_seconds:
            return TASK_DEADLINE
        idle = self.provider_idle_timeout_seconds
        if (
            idle is not None
            and not provider_work_in_flight
            and self.last_activity_age_at(observed_at) >= idle
        ):
            return PROVIDER_IDLE_TIMEOUT
        return None


def _origin(value: Any) -> str:
    clean = _bounded(value, 64).lower().replace("-", "_").replace(" ", "_")
    return clean if clean in TIMEOUT_ORIGINS else UNKNOWN_TIMEOUT


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
    budget: DeadlineBudget | None = None,
    operation_id: str | None = None,
    route_id: str | None = None,
    progress_observed: bool | None = None,
    external_effect_possible: bool | None = None,
    teardown_state: str | None = None,
    cost_state: str | None = None,
    verification_state: str | None = None,
    retry_safety: str = RECONCILE_BEFORE_RETRY,
) -> dict[str, Any]:
    """Build a bounded, deterministic, JSON-safe timeout record.

    A timeout is a cause, not a boolean. In particular, a Vesta task deadline
    expiring while a provider is active must not be rendered as provider
    silence or declared retry-safe before the prior operation is reconciled.
    """

    if provider_responsive is True:
        condition = "responsive"
    elif provider_responsive is False:
        condition = "idle"
    else:
        condition = "unknown"
    safety = _bounded(retry_safety, 64).lower()
    if safety not in _RETRY_SAFETY:
        safety = RECONCILE_BEFORE_RETRY
    resolved_origin = _origin(origin)
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": budget.policy_version if budget else DEADLINE_POLICY_VERSION,
        "timeout_origin": resolved_origin,
        "owner": _bounded(owner, 64) or "unknown",
        "configured_seconds": _finite_or_none(configured_seconds),
        "elapsed_seconds": _finite_or_none(elapsed_seconds),
        "provider_condition": condition,
        "retry_safety": (
            RECONCILE_BEFORE_RETRY if resolved_origin == TASK_DEADLINE else safety
        ),
    }
    if budget is not None:
        event["deadline_budget_id"] = budget.budget_id
        event["task_deadline_seconds"] = budget.task_deadline_seconds
        event["provider_idle_timeout_seconds"] = budget.provider_idle_timeout_seconds
        event["lane"] = budget.lane
    elif lane:
        event["lane"] = _bounded(lane, 64)
    optional_numbers = {
        "last_activity_seconds_ago": last_activity_seconds_ago,
    }
    for key, value in optional_numbers.items():
        number = _finite_or_none(value)
        if number is not None:
            event[key] = number
    optional_text = {
        "phase": phase,
        "operation_id": operation_id,
        "route_id": route_id,
        "teardown_state": teardown_state,
        "cost_state": cost_state,
        "verification_state": verification_state,
    }
    for key, value in optional_text.items():
        clean = _bounded(value, 256)
        if clean:
            event[key] = clean
    optional_bools = {
        "progress_observed": progress_observed,
        "external_effect_possible": external_effect_possible,
    }
    for key, value in optional_bools.items():
        if isinstance(value, bool):
            event[key] = value
    event["timeout_id"] = _stable_id("timeout", event)
    return event


def enrich_timeout_event(event: Any, **context: Any) -> dict[str, Any]:
    """Attach durable run context without retaining provider output."""

    payload = dict(event) if isinstance(event, Mapping) else {}
    payload.pop("timeout_id", None)
    for key in ("task_id", "run_id", "checkpoint_id"):
        clean = _bounded(context.get(key), 256)
        if clean:
            payload[key] = clean
    payload["timeout_id"] = _stable_id("timeout", payload)
    return payload


def is_task_deadline(event: Any) -> bool:
    return isinstance(event, Mapping) and event.get("timeout_origin") == TASK_DEADLINE
