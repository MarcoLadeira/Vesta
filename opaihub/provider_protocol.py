"""Immutable, provider-neutral v1 adapter protocol boundary.

This module accepts only transport observations.  It deliberately does not
produce completion, cost, authority, or verification truth; those are owned by
the layers that observe and evaluate an OPai run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .completion import CompletionState
from .provider_catalog import PROTOCOL_VERSION, provider_record
from opai.provider_contract import ERROR_CODES


class ProtocolViolation(ValueError):
    """Raised when provider transport data violates the v1 boundary."""


class CapabilityStatus(str, Enum):
    """The only capability statuses an adapter may report."""

    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class EventKind(str, Enum):
    """Provider-neutral event kinds emitted during one adapter attempt."""

    STARTED = "started"
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    STRUCTURED_OUTPUT = "structured_output"
    USAGE = "usage"
    CANCEL_ACK = "cancel_ack"
    ERROR = "error"
    TERMINAL = "terminal"


class UsageMeasurement(str, Enum):
    """How an observed usage value was measured."""

    ACTUAL = "actual"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


_CAPABILITY_NAMES = frozenset(
    {
        "cancellation",
        "chat",
        "code_execution",
        "repo_read",
        "repo_editing",
        "run_tests",
        "streaming",
        "tool_calling",
        "structured_output",
    }
)
MAX_EVENTS = 256
MAX_PAYLOAD_DEPTH = 16
MAX_PAYLOAD_ITEMS = 512
MAX_REQUESTED_CAPABILITIES = 32
_FORBIDDEN_PAYLOAD_FIELDS = frozenset(
    {
        "completion_state",
        "completion_verdict",
        "authority",
        "verification",
        "authorised",
        "authorized",
    }
)
_COMPLETION_TRUTH_FIELDS = frozenset({"done", "is_complete", "success", "finished"})
_AUTHORITY_TRUTH_TOKENS = frozenset(
    {"authority", "authorization", "authorisation", "authorized", "authorised"}
)
_VERIFICATION_TRUTH_TOKENS = frozenset({"verification", "verified"})
_COST_TRUTH_TOKENS = frozenset({"cost", "price", "pricing", "charge"})
_STATE_MAPPING_FIELDS = frozenset(
    {
        "state",
        "status",
        "final",
        "final_state",
        "final_status",
        "mapped_state",
        "failure_state",
        "cancellation_state",
        "cancel_state",
        "timeout_state",
        "terminal_state",
        "terminal_status",
        "outcome_state",
        "outcome_status",
        "completion",
        "result",
        "result_state",
        "result_status",
    }
)
_TERMINAL_PAYLOAD_FIELDS = _STATE_MAPPING_FIELDS | frozenset(
    {"reason_code", "error_code", "stop_reason"}
)
_FAILURE_TERMINAL_STATES = frozenset(
    {
        CompletionState.FAILED,
        CompletionState.RETRYABLE_PROVIDER_ERROR,
        CompletionState.PROVIDER_BLOCKED,
        CompletionState.STUCK_NO_PROGRESS,
    }
)
_CANONICAL_ERROR_CODES = frozenset(ERROR_CODES)
_USAGE_FIELDS = frozenset(
    {"measurement", "provenance", "input_tokens", "output_tokens", "total_tokens"}
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CANONICAL_FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*")


def _fail(message: str) -> None:
    raise ProtocolViolation(message)


def _normalised_field_name(value: str) -> str:
    separated = _CAMEL_BOUNDARY.sub("_", value.strip())
    return re.sub(r"_+", "_", separated.lower().replace("-", "_").replace(" ", "_"))


def _provider_id(value: Any) -> str:
    provider_id = str(value or "").strip().lower()
    if not provider_id or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", provider_id):
        _fail("provider_id must be a normalized non-empty provider identifier")
    return provider_id


def _catalog_provider_id(value: Any) -> str:
    provider_id = _provider_id(value)
    try:
        provider_record(provider_id)
    except ValueError as exc:
        raise ProtocolViolation(
            f"provider_id is not present in the pinned catalog: {provider_id!r}"
        ) from exc
    return provider_id


def _is_catalog_provider(provider_id: str) -> bool:
    try:
        provider_record(provider_id)
    except ValueError:
        return False
    return True


def _request_id(value: Any) -> str:
    request_id = str(value or "").strip()
    if not request_id or len(request_id) > 256:
        _fail("request_id must be a non-empty value no longer than 256 characters")
    return request_id


def _finite_number(value: Any, *, field_name: str, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        _fail(f"{field_name} must be a finite number")
    if non_negative and number < 0:
        _fail(f"{field_name} must be non-negative")
    return number


def _positive_seconds(value: Any, *, field_name: str) -> float:
    seconds = _finite_number(value, field_name=field_name)
    if seconds <= 0:
        _fail(f"{field_name} must be greater than zero")
    return seconds


def _is_exact_protocol_version(value: Any, expected: int = PROTOCOL_VERSION) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _require_protocol_version(value: Any, *, field_name: str) -> int:
    if not _is_exact_protocol_version(value):
        _fail(f"{field_name} must be the exact non-bool integer {PROTOCOL_VERSION}")
    return value


def _normalised_capability(value: Any) -> str:
    if not isinstance(value, str):
        _fail("capability names must be strings")
    capability = _normalised_field_name(value.strip())
    if capability not in _CAPABILITY_NAMES:
        _fail(f"unknown capability: {value!r}")
    return capability


def _freeze_json(value: Any, *, path: str = "payload") -> Any:
    """Boundedly copy finite JSON transport data into immutable values."""

    holder: list[Any] = [None]
    stack: list[tuple[Any, ...]] = [("visit", value, 0, holder, 0, frozenset(), path)]
    item_count = 0
    while stack:
        frame = stack.pop()
        operation = frame[0]
        if operation == "finish_mapping":
            _, output, parent, slot = frame
            parent[slot] = MappingProxyType(output)
            continue
        if operation == "finish_list":
            _, output, parent, slot = frame
            parent[slot] = tuple(output)
            continue

        _, current, depth, parent, slot, ancestors, current_path = frame
        if depth > MAX_PAYLOAD_DEPTH:
            _fail(f"{current_path} exceeds MAX_PAYLOAD_DEPTH")
        if current is None or isinstance(current, (str, bool, int)):
            parent[slot] = current
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                _fail(f"{current_path} contains a non-finite number")
            parent[slot] = current
            continue
        identity = id(current)
        if identity in ancestors:
            _fail(f"{current_path} contains a reference cycle")
        next_ancestors = ancestors | frozenset({identity})
        if isinstance(current, Mapping):
            entries: list[tuple[str, Any]] = []
            for key, item in current.items():
                item_count += 1
                if item_count > MAX_PAYLOAD_ITEMS:
                    _fail(f"{current_path} exceeds MAX_PAYLOAD_ITEMS")
                if not isinstance(key, str):
                    _fail(f"{current_path} has a non-string JSON object key")
                _validate_transport_field(key, item, path=current_path)
                entries.append((key, item))
            output: dict[str, Any] = {}
            stack.append(("finish_mapping", output, parent, slot))
            for key, item in reversed(entries):
                stack.append(
                    (
                        "visit",
                        item,
                        depth + 1,
                        output,
                        key,
                        next_ancestors,
                        f"{current_path}.{key}",
                    )
                )
            continue
        if isinstance(current, (list, tuple)):
            entries = []
            for item in current:
                item_count += 1
                if item_count > MAX_PAYLOAD_ITEMS:
                    _fail(f"{current_path} exceeds MAX_PAYLOAD_ITEMS")
                entries.append(item)
            output = [None] * len(entries)
            stack.append(("finish_list", output, parent, slot))
            for index in range(len(entries) - 1, -1, -1):
                stack.append(
                    (
                        "visit",
                        entries[index],
                        depth + 1,
                        output,
                        index,
                        next_ancestors,
                        f"{current_path}[]",
                    )
                )
            continue
        _fail(f"{current_path} must be JSON-compatible")
    return holder[0]


def _validate_transport_field(key: str, value: Any, *, path: str) -> None:
    normalized = _normalised_field_name(key)
    if key != normalized or _CANONICAL_FIELD_NAME.fullmatch(key) is None:
        _fail(f"{path}.{key} must use a canonical lower_snake_case field name")
    tokens = frozenset(part for part in normalized.split("_") if part)
    if (
        normalized in _FORBIDDEN_PAYLOAD_FIELDS
        or normalized in _COMPLETION_TRUTH_FIELDS
        or "completion" in tokens
        or tokens & _AUTHORITY_TRUTH_TOKENS
        or tokens & _VERIFICATION_TRUTH_TOKENS
    ):
        _fail(f"{path}.{key} cannot assert canonical truth")
    if tokens & _COST_TRUTH_TOKENS:
        _fail(f"{path}.{key} cannot assert cost")
    if normalized in _STATE_MAPPING_FIELDS:
        _validate_state_mapping(value, field_name=f"{path}.{key}")


def _validate_terminal_payload(payload: Mapping[str, Any]) -> None:
    states: list[CompletionState] = []
    for key in payload:
        normalized = _normalised_field_name(key)
        if normalized not in _TERMINAL_PAYLOAD_FIELDS:
            _fail(f"terminal payload has an unknown field: {key!r}")
        if normalized in _STATE_MAPPING_FIELDS:
            states.append(_validate_state_mapping(payload[key], field_name=key))
    if len(states) != 1:
        _fail("terminal payload must contain exactly one non-success outcome state")
    state = states[0]
    if "error_code" not in payload:
        return
    if state not in _FAILURE_TERMINAL_STATES:
        _fail("error_code is only valid for a terminal failure state")
    error_code = payload["error_code"]
    if not isinstance(error_code, str) or error_code not in _CANONICAL_ERROR_CODES:
        _fail("terminal error_code must use the canonical provider error vocabulary")


def _validate_state_mapping(value: Any, *, field_name: str) -> CompletionState:
    raw_value = getattr(value, "value", value)
    if not isinstance(raw_value, str):
        _fail(f"{field_name} must use CompletionState vocabulary")
    try:
        state = CompletionState(raw_value)
    except ValueError as exc:
        raise ProtocolViolation(
            f"{field_name} must use existing CompletionState vocabulary"
        ) from exc
    if state is CompletionState.COMPLETED:
        _fail(f"{field_name} cannot claim completed")
    return state


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class AdapterSLO:
    """Bounded timing guarantees for one adapter attempt.

    Event timestamps are elapsed monotonic seconds from the attempt start.
    """

    first_event_seconds: float = 30.0
    cancel_ack_seconds: float = 2.0
    terminal_after_cancel_seconds: float = 10.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "first_event_seconds",
            _positive_seconds(
                self.first_event_seconds,
                field_name="first_event_seconds",
            ),
        )
        object.__setattr__(
            self,
            "cancel_ack_seconds",
            _positive_seconds(
                self.cancel_ack_seconds,
                field_name="cancel_ack_seconds",
            ),
        )
        object.__setattr__(
            self,
            "terminal_after_cancel_seconds",
            _positive_seconds(
                self.terminal_after_cancel_seconds,
                field_name="terminal_after_cancel_seconds",
            ),
        )

    @property
    def first_observable_event_seconds(self) -> float:
        """Compatibility spelling for the first-observation bound."""

        return self.first_event_seconds

    @property
    def cancellation_acknowledgement_seconds(self) -> float:
        """Compatibility spelling for the cancellation-ack bound."""

        return self.cancel_ack_seconds

    @property
    def terminal_after_cancel_ack_seconds(self) -> float:
        """Compatibility spelling for the post-ack terminal bound."""

        return self.terminal_after_cancel_seconds

    @property
    def terminal_after_ack_seconds(self) -> float:
        """Compatibility spelling for the post-ack terminal bound."""

        return self.terminal_after_cancel_seconds

    def to_dict(self) -> dict[str, float]:
        return {
            "first_event_seconds": self.first_event_seconds,
            "cancel_ack_seconds": self.cancel_ack_seconds,
            "terminal_after_cancel_seconds": self.terminal_after_cancel_seconds,
        }


@dataclass(frozen=True)
class AdapterRequest:
    """One normalized adapter request, without credentials or completion claims."""

    provider_id: str
    request_id: str
    requested_capabilities: tuple[str, ...] = ()
    cancel_requested_at: float | None = None
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _catalog_provider_id(self.provider_id))
        object.__setattr__(self, "request_id", _request_id(self.request_id))
        if isinstance(self.requested_capabilities, str):
            _fail("requested_capabilities must be an iterable of capability names")
        try:
            iterator = iter(self.requested_capabilities)
        except TypeError as exc:
            raise ProtocolViolation(
                "requested_capabilities must be an iterable of capability names"
            ) from exc
        requested: list[str] = []
        seen: set[str] = set()
        raw_count = 0
        for _ in range(MAX_REQUESTED_CAPABILITIES + 1):
            try:
                raw_capability = next(iterator)
            except StopIteration:
                break
            raw_count += 1
            if raw_count > MAX_REQUESTED_CAPABILITIES:
                _fail("requested_capabilities exceeds MAX_REQUESTED_CAPABILITIES")
            capability = _normalised_capability(raw_capability)
            if capability not in seen:
                seen.add(capability)
                requested.append(capability)
        object.__setattr__(self, "requested_capabilities", tuple(requested))
        if self.cancel_requested_at is not None:
            object.__setattr__(
                self,
                "cancel_requested_at",
                _finite_number(
                    self.cancel_requested_at,
                    field_name="cancel_requested_at",
                    non_negative=True,
                ),
            )
        if (
            isinstance(self.protocol_version, bool)
            or not isinstance(self.protocol_version, int)
            or self.protocol_version < 1
        ):
            _fail("protocol_version must be a positive integer")

    @property
    def correlation_id(self) -> str:
        """The request ID is the stable correlation ID for this protocol."""

        return self.request_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "request_id": self.request_id,
            "requested_capabilities": list(self.requested_capabilities),
            "cancel_requested_at": self.cancel_requested_at,
            "protocol_version": self.protocol_version,
        }


@dataclass(frozen=True)
class ProviderReadiness:
    """Separate readiness facts; no one boolean is allowed to hide their state."""

    provider_id: str
    installed: bool | None = None
    configured: bool | None = None
    authenticated: bool | None = None
    authorised: bool | None = None
    healthy: bool | None = None
    degraded_reason: str | None = None
    next_action: str | None = None
    protocol_version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        provider_id = _provider_id(self.provider_id)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(
            self,
            "protocol_version",
            _require_protocol_version(
                self.protocol_version,
                field_name="protocol_version",
            ),
        )
        if not _is_catalog_provider(provider_id):
            if any(
                value is not None
                for value in (
                    self.installed,
                    self.configured,
                    self.authenticated,
                    self.authorised,
                    self.healthy,
                    self.degraded_reason,
                    self.next_action,
                )
            ):
                _fail("unlisted providers cannot assert readiness facts")
            object.__setattr__(self, "healthy", False)
            object.__setattr__(self, "degraded_reason", "provider_not_in_catalog")
            object.__setattr__(
                self,
                "next_action",
                "Choose a provider listed in the pinned capability catalog.",
            )
            return
        for name in (
            "installed",
            "configured",
            "authenticated",
            "authorised",
            "healthy",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                _fail(f"{name} must be true, false, or unknown")
        reason = _optional_action_text(
            self.degraded_reason, field_name="degraded_reason"
        )
        action = _optional_action_text(self.next_action, field_name="next_action")
        object.__setattr__(self, "degraded_reason", reason)
        object.__setattr__(self, "next_action", action)
        if self.healthy is False and (not reason or not action):
            _fail("degraded readiness requires both a reason and a next_action")
        if (reason is None) != (action is None):
            _fail("degraded_reason and next_action must be supplied together")
        if self.healthy is True and reason is not None:
            _fail("healthy readiness cannot carry a degraded reason")

    @property
    def authorized(self) -> bool | None:
        """US spelling for callers; transport payloads must never use either spelling."""

        return self.authorised

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "installed": self.installed,
            "configured": self.configured,
            "authenticated": self.authenticated,
            "authorised": self.authorised,
            "healthy": self.healthy,
            "degraded_reason": self.degraded_reason,
            "next_action": self.next_action,
            "protocol_version": self.protocol_version,
        }


def _optional_action_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(f"{field_name} must be a string or None")
    text = value.strip()
    if not text:
        _fail(f"{field_name} must be non-empty when supplied")
    return text[:1_000]


@dataclass(frozen=True)
class ProviderEvent:
    """One immutable transport observation from a provider adapter."""

    sequence: int
    timestamp: float
    kind: EventKind
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            _fail("event sequence must be an integer")
        if self.sequence < 0:
            _fail("event sequence must be non-negative")
        object.__setattr__(
            self,
            "timestamp",
            _finite_number(
                self.timestamp,
                field_name="event timestamp",
                non_negative=True,
            ),
        )
        try:
            kind = (
                self.kind if isinstance(self.kind, EventKind) else EventKind(self.kind)
            )
        except (TypeError, ValueError) as exc:
            raise ProtocolViolation(
                f"unknown provider event kind: {self.kind!r}"
            ) from exc
        object.__setattr__(self, "kind", kind)
        if not isinstance(self.payload, Mapping):
            _fail("event payload must be a JSON object")
        object.__setattr__(self, "payload", _freeze_json(self.payload))
        if self.kind is EventKind.TERMINAL:
            _validate_terminal_payload(self.payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "kind": self.kind.value,
            "payload": _thaw_json(self.payload),
        }


@dataclass(frozen=True)
class UsageObservation:
    """Usage data with explicit measurement provenance and no fabricated zeros."""

    measurement: UsageMeasurement
    provenance: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        try:
            measurement = (
                self.measurement
                if isinstance(self.measurement, UsageMeasurement)
                else UsageMeasurement(self.measurement)
            )
        except (TypeError, ValueError) as exc:
            raise ProtocolViolation("usage measurement is invalid") from exc
        object.__setattr__(self, "measurement", measurement)
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            _fail("usage provenance is required")
        object.__setattr__(self, "provenance", self.provenance.strip()[:500])
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                _fail(f"{name} must be a non-negative integer or None")
        values = (self.input_tokens, self.output_tokens, self.total_tokens)
        if measurement is UsageMeasurement.UNAVAILABLE:
            if any(value is not None for value in values):
                _fail("unavailable usage cannot include token values")
            return
        if all(value is None for value in values):
            _fail("measured usage requires at least one observed token value")
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens is not None
            and self.total_tokens != self.input_tokens + self.output_tokens
        ):
            _fail("total_tokens must equal input_tokens plus output_tokens")
        known_components = sum(
            value
            for value in (self.input_tokens, self.output_tokens)
            if value is not None
        )
        if self.total_tokens is not None and self.total_tokens < known_components:
            _fail("total_tokens cannot be smaller than observed token components")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> UsageObservation:
        if not isinstance(payload, Mapping):
            _fail("usage payload must be a JSON object")
        if set(payload) - _USAGE_FIELDS:
            _fail("usage payload has an unknown field")
        if "measurement" not in payload or "provenance" not in payload:
            _fail("usage payload requires measurement and provenance")
        return cls(
            measurement=payload["measurement"],
            provenance=payload["provenance"],
            input_tokens=payload.get("input_tokens"),
            output_tokens=payload.get("output_tokens"),
            total_tokens=payload.get("total_tokens"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "measurement": self.measurement.value,
            "provenance": self.provenance,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


def protocol_readiness(provider_id: str, protocol_version: Any) -> ProviderReadiness:
    """Return an actionable degradation for incompatible protocol versions."""

    normalized_provider_id = _provider_id(provider_id)
    if not _is_catalog_provider(normalized_provider_id):
        _require_protocol_version(protocol_version, field_name="protocol_version")
        return ProviderReadiness(provider_id=normalized_provider_id)
    if _is_exact_protocol_version(protocol_version):
        return ProviderReadiness(provider_id=normalized_provider_id)
    return ProviderReadiness(
        provider_id=normalized_provider_id,
        healthy=False,
        degraded_reason="protocol_version_incompatible",
        next_action=(
            "Update the provider adapter to protocol version "
            f"{PROTOCOL_VERSION}, then retry."
        ),
        protocol_version=PROTOCOL_VERSION,
    )


def negotiate_capabilities(
    request: AdapterRequest,
    capabilities: Mapping[str, CapabilityStatus | str],
    *,
    protocol_version: Any | None = None,
    provider_protocol_version: Any | None = None,
) -> ProviderReadiness:
    """Fail closed unless every requested capability is explicitly supported.

    Incompatibility is a readiness result rather than a fallback: callers can
    show its repair action without executing an adapter under an unknown schema.
    """

    if not isinstance(request, AdapterRequest):
        _fail("capability negotiation requires an AdapterRequest")
    if protocol_version is not None and provider_protocol_version is not None:
        _fail("supply only one provider protocol version")
    catalog = provider_record(request.provider_id)
    catalog_version = catalog["protocol_version"]
    if not _is_exact_protocol_version(request.protocol_version, catalog_version):
        return protocol_readiness(request.provider_id, request.protocol_version)
    if protocol_version is not None and not _is_exact_protocol_version(
        protocol_version, catalog_version
    ):
        return protocol_readiness(request.provider_id, protocol_version)
    if provider_protocol_version is not None and not _is_exact_protocol_version(
        provider_protocol_version, catalog_version
    ):
        return protocol_readiness(request.provider_id, provider_protocol_version)
    readiness = protocol_readiness(request.provider_id, catalog_version)
    if readiness.degraded_reason is not None:
        return readiness
    if not isinstance(capabilities, Mapping):
        _fail("capabilities must be a mapping")

    advertised: dict[str, CapabilityStatus] = {}
    for raw_capability, raw_status in capabilities.items():
        capability = _normalised_capability(raw_capability)
        if capability in advertised:
            _fail(f"duplicate capability: {capability}")
        try:
            advertised[capability] = (
                raw_status
                if isinstance(raw_status, CapabilityStatus)
                else CapabilityStatus(raw_status)
            )
        except (TypeError, ValueError) as exc:
            raise ProtocolViolation(
                f"invalid capability status for {capability}: {raw_status!r}"
            ) from exc

    expected = {
        capability: CapabilityStatus(status)
        for capability, status in catalog["capabilities"].items()
    }
    if set(advertised) != set(expected):
        _fail("advertised capabilities must exactly match the pinned catalog")
    for capability, expected_status in expected.items():
        if advertised[capability] is not expected_status:
            _fail(
                "advertised capability status does not match the pinned catalog: "
                f"{capability}"
            )

    for capability in request.requested_capabilities:
        status = advertised.get(capability, CapabilityStatus.UNKNOWN)
        if status is not CapabilityStatus.SUPPORTED:
            _fail(f"requested capability is not explicitly supported: {capability}")
    return ProviderReadiness(provider_id=request.provider_id)


def validate_event_stream(
    events: Iterable[ProviderEvent],
    *,
    slo: AdapterSLO | None = None,
    cancel_requested_at: float | None = None,
    request: AdapterRequest | None = None,
) -> tuple[ProviderEvent, ...]:
    """Validate an ordered, immutable provider event stream without side effects."""

    active_slo = AdapterSLO() if slo is None else slo
    if not isinstance(active_slo, AdapterSLO):
        _fail("slo must be an AdapterSLO")
    if request is not None and not isinstance(request, AdapterRequest):
        _fail("request must be an AdapterRequest or None")
    requested_cancel_at = None if request is None else request.cancel_requested_at
    if cancel_requested_at is not None:
        explicit_cancel_at = _finite_number(
            cancel_requested_at,
            field_name="cancel_requested_at",
            non_negative=True,
        )
        if (
            requested_cancel_at is not None
            and explicit_cancel_at != requested_cancel_at
        ):
            _fail("cancel_requested_at must match the AdapterRequest")
        requested_cancel_at = explicit_cancel_at

    try:
        iterator = iter(events)
    except TypeError as exc:
        raise ProtocolViolation(
            "events must be an iterable of ProviderEvent values"
        ) from exc
    stream: list[ProviderEvent] = []
    for _ in range(MAX_EVENTS + 1):
        try:
            event = next(iterator)
        except StopIteration:
            break
        if len(stream) == MAX_EVENTS:
            _fail("event stream exceeds MAX_EVENTS")
        stream.append(event)
    stream = tuple(stream)
    if not stream:
        _fail("event stream cannot be empty")
    if any(not isinstance(event, ProviderEvent) for event in stream):
        _fail("event stream contains an unknown provider event")
    if stream[0].kind is not EventKind.STARTED:
        _fail("the first provider event must be started")
    if stream[0].sequence != 1:
        _fail("the first provider event sequence must be 1")
    if stream[0].timestamp > active_slo.first_event_seconds:
        _fail("first observable event exceeded its SLO")

    terminal_indexes: list[int] = []
    cancel_acks: list[ProviderEvent] = []
    previous = stream[0]
    for index, event in enumerate(stream):
        if not isinstance(event.kind, EventKind):
            _fail("event stream contains an unknown provider event kind")
        if index:
            if event.sequence != previous.sequence + 1:
                _fail("event sequences must be contiguous and increasing")
            if event.timestamp < previous.timestamp:
                _fail("event timestamps must be non-decreasing")
            previous = event
        if event.kind is EventKind.TERMINAL:
            terminal_indexes.append(index)
        if event.kind is EventKind.CANCEL_ACK:
            cancel_acks.append(event)
        if event.kind is EventKind.USAGE:
            UsageObservation.from_payload(event.payload)

    if terminal_indexes != [len(stream) - 1]:
        _fail(
            "event stream must contain exactly one terminal event, and it must be last"
        )

    if requested_cancel_at is None:
        if cancel_acks:
            _fail("cancel_ack cannot appear without a cancellation request")
        return stream

    requested_at = requested_cancel_at
    if len(cancel_acks) != 1:
        _fail("a cancellation request requires exactly one cancel_ack")
    acknowledgement = cancel_acks[0]
    if acknowledgement.timestamp < requested_at:
        _fail("cancel_ack cannot precede its cancellation request")
    if acknowledgement.timestamp - requested_at > active_slo.cancel_ack_seconds:
        _fail("cancellation acknowledgement exceeded its SLO")
    terminal = stream[-1]
    if terminal.timestamp < acknowledgement.timestamp:
        _fail("terminal cannot precede cancel_ack")
    if (
        terminal.timestamp - acknowledgement.timestamp
        > active_slo.terminal_after_cancel_seconds
    ):
        _fail("terminal after cancel_ack exceeded its SLO")
    return stream


# Intentional aliases make the boundary easy to discover without creating a
# second protocol or duplicate models.
ProviderEventKind = EventKind
AttemptSLO = AdapterSLO
ProviderAttemptSLO = AdapterSLO
validate_capability_negotiation = negotiate_capabilities
validate_protocol_version = protocol_readiness


__all__ = [
    "PROTOCOL_VERSION",
    "MAX_EVENTS",
    "MAX_PAYLOAD_DEPTH",
    "MAX_PAYLOAD_ITEMS",
    "MAX_REQUESTED_CAPABILITIES",
    "AdapterRequest",
    "AdapterSLO",
    "AttemptSLO",
    "CapabilityStatus",
    "EventKind",
    "ProtocolViolation",
    "ProviderAttemptSLO",
    "ProviderEvent",
    "ProviderEventKind",
    "ProviderReadiness",
    "UsageMeasurement",
    "UsageObservation",
    "negotiate_capabilities",
    "protocol_readiness",
    "validate_capability_negotiation",
    "validate_event_stream",
    "validate_protocol_version",
]
