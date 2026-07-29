"""Offline, deterministic provider-adapter conformance fixtures for #533.

This is deliberately test-only code.  A scripted adapter reads the pinned
catalog and creates transport observations in memory; it never loads a
provider CLI, credential store, network client, or repository file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from opaihub.provider_catalog import PROTOCOL_VERSION, provider_record
from opaihub.provider_protocol import (
    AdapterRequest,
    AdapterSLO,
    EventKind,
    ProtocolViolation,
    ProviderEvent,
    negotiate_capabilities,
    validate_event_stream,
)


class Scenario(str, Enum):
    """Required deterministic traces plus adversarial conformance fixtures."""

    SUCCESS_STREAM = "success_stream"
    PARTIAL_STREAM = "partial_stream"
    CANCEL_BEFORE_OUTPUT = "cancel_before_output"
    CANCEL_DURING_STREAM = "cancel_during_stream"
    TIMEOUT = "timeout"
    AUTH_FAILURE = "auth_failure"
    QUOTA_FAILURE = "quota_failure"
    RATE_LIMIT = "rate_limit"
    PROVIDER_OUTAGE = "provider_outage"
    MALFORMED_OUTPUT = "malformed_output"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    MALFORMED_USAGE = "malformed_usage"
    DUPLICATE_TERMINAL = "duplicate_terminal"
    CANCELLATION_RACE = "cancellation_race"
    UNKNOWN_CAPABILITY = "unknown_capability"
    INCOMPATIBLE_VERSION = "incompatible_version"


@dataclass(frozen=True)
class ScriptedTrace:
    """One provider-neutral request, advertised capability map, and event trace."""

    request: AdapterRequest
    advertised_capabilities: Mapping[str, str]
    provider_protocol_version: int
    events: tuple[ProviderEvent, ...]
    slo: AdapterSLO


def _event(
    sequence: int,
    timestamp: float,
    kind: EventKind,
    payload: Mapping[str, object] | None = None,
) -> ProviderEvent:
    return ProviderEvent(
        sequence=sequence,
        timestamp=timestamp,
        kind=kind,
        payload={} if payload is None else payload,
    )


def _terminal(
    sequence: int,
    timestamp: float,
    state: str,
    error_code: str | None = None,
) -> ProviderEvent:
    payload: dict[str, object] = {"state": state}
    if error_code is not None:
        payload["error_code"] = error_code
    return _event(sequence, timestamp, EventKind.TERMINAL, payload)


def _first_unsupported_capability(record: Mapping[str, object]) -> str:
    capabilities = record["capabilities"]
    assert isinstance(capabilities, Mapping)
    return next(
        capability
        for capability, status in capabilities.items()
        if status != "supported"
    )


class MockProviderAdapter:
    """Replay catalog-backed traces without invoking provider implementation code."""

    def __init__(self, provider_id: str) -> None:
        record = provider_record(provider_id)
        self._record = record
        self.provider_id = str(record["provider_id"])

    @property
    def advertised_capabilities(self) -> dict[str, str]:
        return dict(self._record["capabilities"])

    @property
    def slo(self) -> AdapterSLO:
        cancellation = self._record["cancellation"]
        assert isinstance(cancellation, Mapping)
        return AdapterSLO(cancel_ack_seconds=float(cancellation["slo_seconds"]))

    def replay(self, scenario: Scenario | str) -> ScriptedTrace:
        """Return a deterministic trace for one required adapter scenario."""

        selected = scenario if isinstance(scenario, Scenario) else Scenario(scenario)
        requested_capabilities: tuple[str, ...] = ("chat",)
        cancel_requested_at: float | None = None
        protocol_version = PROTOCOL_VERSION
        capabilities = self.advertised_capabilities

        if selected is Scenario.UNSUPPORTED_CAPABILITY:
            requested_capabilities = (_first_unsupported_capability(self._record),)
        elif selected in {
            Scenario.CANCEL_BEFORE_OUTPUT,
            Scenario.CANCEL_DURING_STREAM,
            Scenario.CANCELLATION_RACE,
        }:
            cancel_requested_at = 0.05
        elif selected is Scenario.INCOMPATIBLE_VERSION:
            protocol_version = PROTOCOL_VERSION + 1
        elif selected is Scenario.UNKNOWN_CAPABILITY:
            capabilities["not_in_catalog"] = "supported"

        request = AdapterRequest(
            provider_id=self.provider_id,
            request_id=f"mock-{self.provider_id}-{selected.value}",
            requested_capabilities=requested_capabilities,
            cancel_requested_at=cancel_requested_at,
            protocol_version=protocol_version,
        )
        events = self._events_for(selected)
        return ScriptedTrace(
            request=request,
            advertised_capabilities=capabilities,
            provider_protocol_version=PROTOCOL_VERSION,
            events=events,
            slo=self.slo,
        )

    def _events_for(self, scenario: Scenario) -> tuple[ProviderEvent, ...]:
        usage = {
            "measurement": "actual",
            "provenance": "deterministic_mock",
            "input_tokens": 3,
            "output_tokens": 2,
            "total_tokens": 5,
        }
        if scenario is Scenario.SUCCESS_STREAM:
            return (
                _event(1, 0.0, EventKind.STARTED),
                _event(2, 0.1, EventKind.TEXT_DELTA, {"text": "observed output"}),
                _event(3, 0.2, EventKind.USAGE, usage),
                # Provider protocol must not manufacture a completed verdict;
                # a run owner decides that from independently observed evidence.
                _terminal(4, 0.3, "needs_user_input"),
            )
        if scenario is Scenario.PARTIAL_STREAM:
            return (
                _event(1, 0.0, EventKind.STARTED),
                _event(2, 0.1, EventKind.TEXT_DELTA, {"text": "partial output"}),
                _event(3, 0.2, EventKind.ERROR, {"message": "stream closed"}),
                _terminal(4, 0.3, "stuck_no_progress"),
            )
        if scenario is Scenario.CANCEL_BEFORE_OUTPUT:
            return (
                _event(1, 0.0, EventKind.STARTED),
                _event(2, 0.1, EventKind.CANCEL_ACK),
                _terminal(3, 0.2, "cancelled"),
            )
        if scenario is Scenario.CANCEL_DURING_STREAM:
            return (
                _event(1, 0.0, EventKind.STARTED),
                _event(2, 0.04, EventKind.TEXT_DELTA, {"text": "partial output"}),
                _event(3, 0.1, EventKind.CANCEL_ACK),
                _terminal(4, 0.2, "cancelled"),
            )
        if scenario is Scenario.TIMEOUT:
            return self._failure_events("PROVIDER_TIMEOUT")
        if scenario is Scenario.AUTH_FAILURE:
            return self._failure_events("AUTH_INVALID", state="provider_blocked")
        if scenario is Scenario.QUOTA_FAILURE:
            return self._failure_events(
                "PROVIDER_QUOTA_EXHAUSTED", state="provider_blocked"
            )
        if scenario is Scenario.RATE_LIMIT:
            return self._failure_events("PROVIDER_RATE_LIMITED")
        if scenario is Scenario.PROVIDER_OUTAGE:
            return self._failure_events("PROVIDER_UNAVAILABLE")
        if scenario is Scenario.MALFORMED_OUTPUT:
            return (
                _event(2, 0.0, EventKind.STARTED),
                _terminal(3, 0.1, "failed"),
            )
        if scenario is Scenario.MALFORMED_USAGE:
            return (
                _event(1, 0.0, EventKind.STARTED),
                _event(
                    2,
                    0.1,
                    EventKind.USAGE,
                    {
                        "measurement": "actual",
                        "provenance": "deterministic_mock",
                        "input_tokens": -1,
                    },
                ),
                _terminal(3, 0.2, "failed"),
            )
        if scenario is Scenario.DUPLICATE_TERMINAL:
            return (
                _event(1, 0.0, EventKind.STARTED),
                _terminal(2, 0.1, "failed"),
                _terminal(3, 0.2, "failed"),
            )
        if scenario is Scenario.CANCELLATION_RACE:
            return (
                _event(1, 0.0, EventKind.STARTED),
                _event(2, 5.06, EventKind.CANCEL_ACK),
                _terminal(3, 5.07, "cancelled"),
            )
        if scenario in {
            Scenario.UNSUPPORTED_CAPABILITY,
            Scenario.UNKNOWN_CAPABILITY,
            Scenario.INCOMPATIBLE_VERSION,
        }:
            return ()
        raise AssertionError(f"Missing scripted trace for {scenario.value}")

    @staticmethod
    def _failure_events(
        error_code: str,
        *,
        state: str = "retryable_provider_error",
    ) -> tuple[ProviderEvent, ...]:
        return (
            _event(1, 0.0, EventKind.STARTED),
            _event(2, 0.1, EventKind.ERROR, {"message": "provider reported error"}),
            _terminal(3, 0.2, state, error_code),
        )


def assert_conformant_trace(trace: ScriptedTrace) -> tuple[ProviderEvent, ...]:
    """Validate a scripted adapter trace with the production protocol boundary."""

    if not isinstance(trace, ScriptedTrace):
        raise TypeError("trace must be a ScriptedTrace")
    readiness = negotiate_capabilities(
        trace.request,
        trace.advertised_capabilities,
        provider_protocol_version=trace.provider_protocol_version,
    )
    if readiness.degraded_reason is not None:
        raise ProtocolViolation(readiness.degraded_reason)
    return validate_event_stream(
        trace.events,
        slo=trace.slo,
        request=trace.request,
    )


__all__ = [
    "MockProviderAdapter",
    "Scenario",
    "ScriptedTrace",
    "assert_conformant_trace",
]
