"""Provider-neutral v1 protocol boundary tests."""

import math
from types import MappingProxyType
import unittest

from hypothesis import given, strategies as st

from opaihub.completion import CompletionState
from opaihub.provider_catalog import provider_record
from opaihub.provider_protocol import (
    MAX_EVENTS,
    MAX_PAYLOAD_DEPTH,
    MAX_PAYLOAD_ITEMS,
    PROTOCOL_VERSION,
    AdapterRequest,
    AdapterSLO,
    EventKind,
    ProtocolViolation,
    ProviderEvent,
    ProviderReadiness,
    UsageMeasurement,
    UsageObservation,
    negotiate_capabilities,
    protocol_readiness,
    validate_event_stream,
)


def _event(
    sequence: int,
    timestamp: float,
    kind: EventKind | str,
    payload: dict | None = None,
) -> ProviderEvent:
    if payload is None and EventKind(kind) is EventKind.TERMINAL:
        payload = {"state": "failed"}
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


def _alias_key(alias: str, convention: str) -> str:
    parts = alias.split("_")
    if convention == "camel":
        return parts[0] + "".join(part.title() for part in parts[1:])
    if convention == "hyphen":
        return "-".join(parts)
    if convention == "upper":
        return alias.upper()
    return alias


def _catalog_capabilities(provider_id: str) -> dict[str, str]:
    return dict(provider_record(provider_id)["capabilities"])


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

    def test_request_rejects_providers_missing_from_the_pinned_catalog(self):
        with self.assertRaises(ProtocolViolation):
            AdapterRequest("not-a-catalog-provider", "request-1", ())

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

    def test_payload_field_names_reject_whitespace_and_punctuation_aliases(self):
        for field in ("result ", " state", "status!", "state\t"):
            with self.subTest(field=field), self.assertRaises(ProtocolViolation):
                _event(1, 0.0, EventKind.STARTED, {field: "failed"})

    def test_semantic_truth_aliases_and_unknown_terminal_fields_fail_closed(self):
        aliases = {
            "final_state": "completed",
            "authorization": True,
            "verification_status": "verified",
            "estimated_cost": 1,
            "price_usd": 1,
            "charge_amount": 1,
            "provider_completion_state": "completed",
            "sdk_completion_verdict": "completed",
        }
        for field, value in aliases.items():
            with self.subTest(field=field), self.assertRaises(ProtocolViolation):
                _event(1, 0.0, EventKind.STARTED, {"nested": {field: value}})

        with self.assertRaises(ProtocolViolation):
            _event(3, 0.2, EventKind.TERMINAL, {"provider_result": "failed"})

    def test_terminal_requires_one_non_success_state_and_canonical_error_codes(self):
        invalid_payloads = (
            {},
            {"state": "completed"},
            {"state": "failed", "status": "failed"},
            {"state": "failed", "error_code": "not_a_canonical_error"},
            {"state": "cancelled", "error_code": "NETWORK_ERROR"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ProtocolViolation):
                _event(3, 0.2, EventKind.TERMINAL, payload)

        terminal = _event(
            3,
            0.2,
            EventKind.TERMINAL,
            {"state": "failed", "error_code": "NETWORK_ERROR"},
        )
        self.assertEqual(terminal.payload["state"], "failed")

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

    def test_adapter_slo_uses_exact_public_fields_and_validator_limits(self):
        slo = AdapterSLO(
            first_event_seconds=0.5,
            cancel_ack_seconds=0.2,
            terminal_after_cancel_seconds=0.5,
        )
        self.assertEqual(slo.first_event_seconds, 0.5)
        self.assertEqual(slo.cancel_ack_seconds, 0.2)
        self.assertEqual(slo.terminal_after_cancel_seconds, 0.5)

        with self.assertRaises(ProtocolViolation):
            validate_event_stream(
                (
                    _event(1, 0.51, EventKind.STARTED),
                    _event(2, 0.52, EventKind.TERMINAL),
                ),
                slo=slo,
            )
        with self.assertRaises(ProtocolViolation):
            validate_event_stream(
                (
                    _event(1, 0.0, EventKind.STARTED),
                    _event(2, 1.1, EventKind.CANCEL_ACK),
                    _event(3, 1.61, EventKind.TERMINAL),
                ),
                slo=slo,
                cancel_requested_at=1.0,
            )

    def test_stream_sequence_must_start_at_one(self):
        events = (
            _event(0, 0.0, EventKind.STARTED),
            _event(1, 0.1, EventKind.TERMINAL),
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

    def test_request_cancellation_timestamp_is_derived_by_stream_validation(self):
        request = AdapterRequest("codex", "request-1", (), cancel_requested_at=1.0)
        acknowledged = (
            _event(1, 0.0, EventKind.STARTED),
            _event(2, 1.1, EventKind.CANCEL_ACK),
            _event(3, 1.2, EventKind.TERMINAL, {"state": "cancelled"}),
        )
        self.assertEqual(
            validate_event_stream(acknowledged, request=request), acknowledged
        )

        without_ack = (
            _event(1, 0.0, EventKind.STARTED),
            _event(2, 1.1, EventKind.TERMINAL, {"state": "cancelled"}),
        )
        with self.assertRaises(ProtocolViolation):
            validate_event_stream(without_ack, request=request)
        with self.assertRaises(ProtocolViolation):
            validate_event_stream(
                acknowledged,
                request=request,
                cancel_requested_at=1.1,
            )

    def test_capability_negotiation_accepts_only_explicit_support(self):
        request = AdapterRequest("codex", "request-1", ("chat", "streaming"))
        capabilities = _catalog_capabilities("codex")
        readiness = negotiate_capabilities(
            request,
            capabilities,
        )
        self.assertIsNone(readiness.degraded_reason)
        self.assertIsNone(readiness.next_action)

        for status in ("partial", "unsupported", "unknown"):
            with self.subTest(status=status), self.assertRaises(ProtocolViolation):
                mismatch = _catalog_capabilities("codex")
                mismatch["streaming"] = status
                negotiate_capabilities(request, mismatch)
        with self.assertRaises(ProtocolViolation):
            unknown = _catalog_capabilities("codex")
            unknown["imaginary"] = "supported"
            negotiate_capabilities(request, unknown)
        with self.assertRaises(ProtocolViolation):
            negotiate_capabilities(request, {"chat": "supported"})

    def test_version_incompatibility_returns_actionable_degraded_readiness(self):
        request = AdapterRequest(
            "codex", "request-1", ("chat",), protocol_version=PROTOCOL_VERSION + 1
        )

        readiness = negotiate_capabilities(request, {"chat": "supported"})

        self.assertFalse(readiness.healthy)
        self.assertEqual(readiness.degraded_reason, "protocol_version_incompatible")
        self.assertIn(str(PROTOCOL_VERSION), readiness.next_action)

    def test_only_an_exact_non_bool_integer_protocol_version_is_compatible(self):
        for version in (0, 2, -1, 1.0, True, "1", None):
            with self.subTest(version=version):
                readiness = protocol_readiness("codex", version)

                self.assertFalse(readiness.healthy)
                self.assertEqual(
                    readiness.degraded_reason, "protocol_version_incompatible"
                )

    def test_stream_and_payload_resource_limits_fail_closed(self):
        events = [_event(1, 0.0, EventKind.STARTED)]
        events.extend(
            _event(sequence, 0.0, EventKind.TEXT_DELTA)
            for sequence in range(2, MAX_EVENTS + 1)
        )
        events.append(_event(MAX_EVENTS + 1, 0.0, EventKind.TERMINAL))
        with self.assertRaises(ProtocolViolation):
            validate_event_stream(events)

        nested: dict[str, object] = {}
        cursor = nested
        for _ in range(MAX_PAYLOAD_DEPTH + 1):
            child: dict[str, object] = {}
            cursor["nested"] = child
            cursor = child
        with self.assertRaises(ProtocolViolation):
            _event(1, 0.0, EventKind.STARTED, nested)

        deep_leaf: dict[str, object] = {}
        cursor = deep_leaf
        for _ in range(MAX_PAYLOAD_DEPTH):
            child = {}
            cursor["nested"] = child
            cursor = child
        cursor["leaf"] = "too deep"
        with self.assertRaises(ProtocolViolation):
            _event(1, 0.0, EventKind.STARTED, deep_leaf)

        oversized = {f"item_{index}": index for index in range(MAX_PAYLOAD_ITEMS + 1)}
        with self.assertRaises(ProtocolViolation):
            _event(1, 0.0, EventKind.STARTED, oversized)


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

    @given(st.integers(min_value=0, max_value=30))
    def test_generated_duplicate_terminals_are_rejected(self, timestamp):
        events = (
            _event(1, 0.0, EventKind.STARTED),
            _event(2, float(timestamp), EventKind.TERMINAL),
            _event(3, float(timestamp), EventKind.TERMINAL),
        )

        with self.assertRaises(ProtocolViolation):
            validate_event_stream(events)

    @given(
        st.sampled_from(
            (
                "missing_measurement",
                "missing_provenance",
                "blank_provenance",
                "invalid_tokens",
                "unavailable_with_total",
                "unknown_field",
            )
        ),
        st.one_of(
            st.none(), st.booleans(), st.integers(max_value=-1), st.text(max_size=20)
        ),
    )
    def test_generated_malformed_usage_payloads_are_rejected(self, shape, value):
        payload = {"measurement": "actual", "provenance": "provider"}
        if shape == "missing_measurement":
            payload = {"provenance": "provider"}
        elif shape == "missing_provenance":
            payload = {"measurement": "actual"}
        elif shape == "blank_provenance":
            payload["provenance"] = " "
        elif shape == "invalid_tokens":
            payload["input_tokens"] = value
        elif shape == "unavailable_with_total":
            payload.update(measurement="unavailable", total_tokens=0)
        else:
            payload["unexpected"] = value
        events = (
            _event(1, 0.0, EventKind.STARTED),
            _event(2, 0.1, EventKind.USAGE, payload),
            _event(3, 0.2, EventKind.TERMINAL),
        )

        with self.assertRaises(ProtocolViolation):
            validate_event_stream(events)

    @given(
        st.floats(min_value=0, max_value=20, allow_nan=False, allow_infinity=False),
        st.floats(min_value=0, max_value=30, allow_nan=False, allow_infinity=False),
    )
    def test_generated_cancellation_ack_races_follow_the_slo(self, ack_at, terminal_at):
        events = (
            _event(1, 0.0, EventKind.STARTED),
            _event(2, ack_at, EventKind.CANCEL_ACK),
            _event(3, terminal_at, EventKind.TERMINAL),
        )
        valid = (
            1.0 <= ack_at <= 3.0
            and terminal_at >= ack_at
            and terminal_at - ack_at <= 10.0
        )

        if valid:
            self.assertEqual(
                validate_event_stream(events, cancel_requested_at=1.0), events
            )
        else:
            with self.assertRaises(ProtocolViolation):
                validate_event_stream(events, cancel_requested_at=1.0)

    @given(st.sampled_from(("partial", "unsupported", "unknown")))
    def test_generated_non_supported_requested_capabilities_are_rejected(self, status):
        request = AdapterRequest("codex", "request-1", ("chat",))
        capabilities = _catalog_capabilities("codex")
        capabilities["chat"] = status

        with self.assertRaises(ProtocolViolation):
            negotiate_capabilities(request, capabilities)

    @given(st.text(max_size=20))
    def test_generated_unknown_capability_fields_are_rejected(self, suffix):
        request = AdapterRequest("codex", "request-1", ("chat",))
        capabilities = _catalog_capabilities("codex")
        unknown_capability = f"unknown_{suffix}"
        capabilities[unknown_capability] = "supported"

        with self.assertRaises(ProtocolViolation):
            negotiate_capabilities(request, capabilities)

    @given(st.sampled_from((math.nan, math.inf, -math.inf)))
    def test_generated_non_finite_inputs_are_rejected(self, value):
        with self.assertRaises(ProtocolViolation):
            _event(1, value, EventKind.STARTED)
        with self.assertRaises(ProtocolViolation):
            AdapterRequest("codex", "request-1", (), cancel_requested_at=value)

    @given(
        st.recursive(
            st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=20)),
            lambda children: st.one_of(
                st.lists(children, max_size=4),
                st.dictionaries(
                    st.sampled_from(("data", "items", "nested")), children, max_size=3
                ),
            ),
            max_leaves=10,
        )
    )
    def test_generated_json_payloads_are_copied_and_immutable(self, value):
        original = {"data": value}

        event = _event(1, 0.0, EventKind.STARTED, original)
        expected = event.to_dict()["payload"]
        original["later"] = "mutation"

        self.assertEqual(event.to_dict()["payload"], expected)
        with self.assertRaises(TypeError):
            event.payload["later"] = "mutation"

    @given(
        st.sampled_from(
            (
                "final_state",
                "authorization",
                "verification_status",
                "estimated_cost",
                "price_usd",
                "charge_amount",
                "provider_completion_state",
                "sdk_completion_verdict",
            )
        ),
        st.sampled_from(("snake", "camel", "hyphen", "upper")),
    )
    def test_generated_truth_aliases_are_rejected(self, alias, convention):
        value = (
            "completed"
            if "completion" in alias or alias == "final_state"
            else "claimed"
        )

        with self.assertRaises(ProtocolViolation):
            _event(1, 0.0, EventKind.STARTED, {_alias_key(alias, convention): value})

    @given(
        st.one_of(
            st.booleans(),
            st.integers(max_value=0),
            st.integers(min_value=2),
            st.floats(allow_nan=True, allow_infinity=True),
            st.text(max_size=10),
            st.none(),
        )
    )
    def test_generated_non_integer_versions_are_degraded(self, version):
        readiness = protocol_readiness("codex", version)

        self.assertFalse(readiness.healthy)
        self.assertEqual(readiness.degraded_reason, "protocol_version_incompatible")

    @given(st.sampled_from(("result", "state", "status")), st.sampled_from((" ", "\t")))
    def test_generated_noncanonical_field_aliases_are_rejected(self, field, suffix):
        with self.assertRaises(ProtocolViolation):
            _event(1, 0.0, EventKind.STARTED, {field + suffix: "failed"})

    @given(st.integers(min_value=MAX_EVENTS + 1, max_value=MAX_EVENTS + 3))
    def test_generated_event_limits_are_rejected(self, count):
        events = [_event(1, 0.0, EventKind.STARTED)]
        events.extend(
            _event(sequence, 0.0, EventKind.TEXT_DELTA) for sequence in range(2, count)
        )
        events.append(_event(count, 0.0, EventKind.TERMINAL))

        with self.assertRaises(ProtocolViolation):
            validate_event_stream(events)

    @given(
        st.integers(min_value=MAX_PAYLOAD_DEPTH + 1, max_value=MAX_PAYLOAD_DEPTH + 3)
    )
    def test_generated_payload_depth_limits_are_rejected(self, depth):
        payload: dict[str, object] = {}
        cursor = payload
        for _ in range(depth):
            child: dict[str, object] = {}
            cursor["nested"] = child
            cursor = child

        with self.assertRaises(ProtocolViolation):
            _event(1, 0.0, EventKind.STARTED, payload)


if __name__ == "__main__":
    unittest.main()
