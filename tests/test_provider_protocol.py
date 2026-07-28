"""Provider-neutral v1 protocol boundary tests."""

import math
from types import MappingProxyType
import unittest

from hypothesis import given, strategies as st

from opaihub.completion import CompletionState
from opaihub.provider_protocol import (
    PROTOCOL_VERSION,
    AdapterRequest,
    CapabilityStatus,
    EventKind,
    ProtocolViolation,
    ProviderEvent,
    ProviderReadiness,
    UsageMeasurement,
    UsageObservation,
    negotiate_capabilities,
    validate_event_stream,
)


def _event(
    sequence: int,
    timestamp: float,
    kind: EventKind | str,
    payload: dict | None = None,
) -> ProviderEvent:
    return ProviderEvent(
        sequence=sequence,
        timestamp=timestamp,
        kind=kind,
        payload={} if payload is None else payload,
    )


def _valid_events() -> tuple[ProviderEvent, ...]:
    return (
        _event(1, 0.0, EventKind.STARTED),
        _event(2, 0.1, EventKind.TEXT_DELTA, {"text": "hello"}),
        _event(3, 0.2, EventKind.TERMINAL),
    )


class ProviderProtocolTests(unittest.TestCase):
    def test_protocol_version_is_the_catalog_v1_version(self):
        self.assertEqual(PROTOCOL_VERSION, 1)

    def test_protocol_violation_is_a_value_error(self):
        self.assertIsInstance(ProtocolViolation("bad protocol"), ValueError)

    def test_request_normalizes_identifiers_and_keeps_requested_capabilities_immutable(
        self,
    ):
        capabilities = ["chat", "streaming"]

        request = AdapterRequest(
            provider_id="  OpenAI-Compatible  ",
            request_id=" request-42 ",
            requested_capabilities=capabilities,
            cancel_requested_at=1.5,
        )

        capabilities.append("tool_calling")
        self.assertEqual(request.provider_id, "openai-compatible")
        self.assertEqual(request.request_id, "request-42")
        self.assertEqual(request.correlation_id, "request-42")
        self.assertEqual(request.requested_capabilities, ("chat", "streaming"))
        self.assertEqual(request.cancel_requested_at, 1.5)

    def test_request_rejects_unknown_capabilities_and_non_finite_cancellation_time(
        self,
    ):
        with self.assertRaises(ProtocolViolation):
            AdapterRequest("codex", "request-1", ("imaginary",))
        with self.assertRaises(ProtocolViolation):
            AdapterRequest("codex", "request-1", (), cancel_requested_at=math.nan)

    def test_readiness_keeps_each_fact_separate_and_requires_actionable_degradation(
        self,
    ):
        readiness = ProviderReadiness(
            provider_id="codex",
            installed=True,
            configured=False,
            authenticated=None,
            authorised=None,
            healthy=False,
            degraded_reason="configuration_missing",
            next_action="Configure the provider, then retry.",
        )

        self.assertTrue(readiness.installed)
        self.assertFalse(readiness.configured)
        self.assertIsNone(readiness.authenticated)
        self.assertIsNone(readiness.authorised)
        self.assertFalse(readiness.healthy)
        with self.assertRaises(ProtocolViolation):
            ProviderReadiness(provider_id="codex", healthy=False)

    def test_event_freezes_a_json_safe_copy_of_payload(self):
        original = {"nested": {"items": [1, {"ok": True}]}}

        event = _event(1, 0.0, EventKind.STARTED, original)
        original["nested"]["items"].append(2)

        self.assertIsInstance(event.payload, MappingProxyType)
        self.assertEqual(
            event.payload["nested"]["items"], (1, MappingProxyType({"ok": True}))
        )
        with self.assertRaises(TypeError):
            event.payload["other"] = "nope"
        with self.assertRaises(ProtocolViolation):
            _event(1, math.inf, EventKind.STARTED)
        with self.assertRaises(ProtocolViolation):
            _event(1, 0.0, EventKind.STARTED, {"bad": math.nan})

    def test_provider_payload_cannot_inject_canonical_truth_or_cost_fields(self):
        forbidden = (
            "completion_state",
            "completionState",
            "completion_verdict",
            "cost",
            "authority",
            "verification",
            "authorised",
            "authorized",
        )
        for field in forbidden:
            with self.subTest(field=field), self.assertRaises(ProtocolViolation):
                _event(1, 0.0, EventKind.STARTED, {"nested": {field: "claimed"}})

        with self.assertRaises(ProtocolViolation):
            _event(3, 0.2, EventKind.TERMINAL, {"status": "completed"})
        with self.assertRaises(ProtocolViolation):
            _event(3, 0.2, EventKind.TERMINAL, {"status": "provider_done"})
        allowed = _event(3, 0.2, EventKind.TERMINAL, {"status": "failed"})
        self.assertEqual(allowed.payload["status"], CompletionState.FAILED.value)

    def test_usage_observation_validates_provenance_without_inventing_zeroes(self):
        observation = UsageObservation(
            measurement=UsageMeasurement.ACTUAL,
            provenance="provider_reported",
            input_tokens=3,
            output_tokens=4,
            total_tokens=7,
        )
        self.assertEqual(observation.total_tokens, 7)
        self.assertIsNone(
            UsageObservation(
                measurement="derived",
                provenance="adapter_formula",
                input_tokens=3,
            ).output_tokens
        )

        invalid = (
            {"measurement": "actual", "provenance": "", "input_tokens": 1},
            {
                "measurement": "actual",
                "provenance": "provider",
                "input_tokens": 3,
                "output_tokens": 4,
                "total_tokens": 8,
            },
            {
                "measurement": "unavailable",
                "provenance": "not_reported",
                "total_tokens": 0,
            },
            {"measurement": "estimated", "provenance": "model"},
        )
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ProtocolViolation):
                UsageObservation(**fields)

    def test_usage_events_are_validated_in_a_stream(self):
        events = (
            _event(1, 0.0, EventKind.STARTED),
            _event(
                2,
                0.1,
                EventKind.USAGE,
                {
                    "measurement": "actual",
                    "provenance": "provider_reported",
                    "input_tokens": 2,
                    "output_tokens": 3,
                    "total_tokens": 5,
                },
            ),
            _event(3, 0.2, EventKind.TERMINAL),
        )
        self.assertEqual(validate_event_stream(events), events)

        malformed = (
            _event(1, 0.0, EventKind.STARTED),
            _event(2, 0.1, EventKind.USAGE, {"measurement": "actual"}),
            _event(3, 0.2, EventKind.TERMINAL),
        )
        with self.assertRaises(ProtocolViolation):
            validate_event_stream(malformed)

    def test_stream_requires_started_first_contiguous_order_and_one_last_terminal(self):
        invalid_streams = (
            (_event(1, 0.0, EventKind.TEXT_DELTA), _event(2, 0.1, EventKind.TERMINAL)),
            (_event(1, 0.0, EventKind.STARTED), _event(3, 0.1, EventKind.TERMINAL)),
            (_event(1, 0.1, EventKind.STARTED), _event(2, 0.0, EventKind.TERMINAL)),
            (
                _event(1, 0.0, EventKind.STARTED),
                _event(2, 0.1, EventKind.TERMINAL),
                _event(3, 0.2, EventKind.TEXT_DELTA),
            ),
            (
                _event(1, 0.0, EventKind.STARTED),
                _event(2, 0.1, EventKind.TERMINAL),
                _event(3, 0.2, EventKind.TERMINAL),
            ),
        )
        for events in invalid_streams:
            with self.subTest(events=events), self.assertRaises(ProtocolViolation):
                validate_event_stream(events)

    def test_stream_enforces_first_observable_event_slo(self):
        events = (
            _event(1, 30.01, EventKind.STARTED),
            _event(2, 30.02, EventKind.TERMINAL),
        )
        with self.assertRaises(ProtocolViolation):
            validate_event_stream(events)

    def test_cancellation_requires_timely_ack_then_terminal(self):
        cancellation_time = 1.0
        valid = (
            _event(1, 0.0, EventKind.STARTED),
            _event(2, 1.5, EventKind.CANCEL_ACK),
            _event(3, 2.0, EventKind.TERMINAL),
        )
        self.assertEqual(
            validate_event_stream(valid, cancel_requested_at=cancellation_time), valid
        )

        races = (
            (_event(1, 0.0, EventKind.STARTED), _event(2, 1.0, EventKind.TERMINAL)),
            (
                _event(1, 0.0, EventKind.STARTED),
                _event(2, 3.01, EventKind.CANCEL_ACK),
                _event(3, 3.02, EventKind.TERMINAL),
            ),
            (
                _event(1, 0.0, EventKind.STARTED),
                _event(2, 0.9, EventKind.CANCEL_ACK),
                _event(3, 1.0, EventKind.TERMINAL),
            ),
            (
                _event(1, 0.0, EventKind.STARTED),
                _event(2, 1.2, EventKind.CANCEL_ACK),
                _event(3, 11.21, EventKind.TERMINAL),
            ),
        )
        for events in races:
            with self.subTest(events=events), self.assertRaises(ProtocolViolation):
                validate_event_stream(events, cancel_requested_at=cancellation_time)

    def test_capability_negotiation_accepts_only_explicit_support(self):
        request = AdapterRequest("codex", "request-1", ("chat", "streaming"))
        readiness = negotiate_capabilities(
            request,
            {"chat": "supported", "streaming": CapabilityStatus.SUPPORTED},
        )
        self.assertIsNone(readiness.degraded_reason)
        self.assertIsNone(readiness.next_action)

        for status in ("partial", "unsupported", "unknown"):
            with self.subTest(status=status), self.assertRaises(ProtocolViolation):
                negotiate_capabilities(
                    request, {"chat": "supported", "streaming": status}
                )
        with self.assertRaises(ProtocolViolation):
            negotiate_capabilities(
                request, {"chat": "supported", "imaginary": "supported"}
            )

    def test_version_incompatibility_returns_actionable_degraded_readiness(self):
        request = AdapterRequest(
            "codex", "request-1", ("chat",), protocol_version=PROTOCOL_VERSION + 1
        )

        readiness = negotiate_capabilities(request, {"chat": "supported"})

        self.assertFalse(readiness.healthy)
        self.assertEqual(readiness.degraded_reason, "protocol_version_incompatible")
        self.assertIn(str(PROTOCOL_VERSION), readiness.next_action)


class ProviderProtocolPropertyTests(unittest.TestCase):
    @given(st.lists(st.integers(min_value=0, max_value=30), min_size=2, max_size=8))
    def test_non_decreasing_stream_timestamps_validate(self, timestamps):
        ordered = sorted(timestamps)
        events = [_event(1, 0.0, EventKind.STARTED)]
        for sequence, timestamp in enumerate(ordered[:-1], start=2):
            events.append(_event(sequence, float(timestamp), EventKind.TEXT_DELTA))
        events.append(_event(len(events) + 1, float(ordered[-1]), EventKind.TERMINAL))

        self.assertEqual(validate_event_stream(tuple(events)), tuple(events))

    @given(st.integers(min_value=1, max_value=20))
    def test_sequence_gaps_are_rejected(self, gap):
        events = (
            _event(1, 0.0, EventKind.STARTED),
            _event(1 + gap + 1, 0.1, EventKind.TERMINAL),
        )

        with self.assertRaises(ProtocolViolation):
            validate_event_stream(events)


if __name__ == "__main__":
    unittest.main()
