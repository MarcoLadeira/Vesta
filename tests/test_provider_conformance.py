"""Deterministic conformance coverage for the pinned provider adapter catalog."""

from __future__ import annotations

from dataclasses import replace
import unittest

from hypothesis import given, strategies as st

from opaihub.provider_catalog import PROTOCOL_VERSION, provider_ids, provider_record
from opaihub.provider_protocol import EventKind, ProtocolViolation, ProviderEvent
from tests.provider_conformance import (
    MockProviderAdapter,
    Scenario,
    assert_conformant_trace,
)


class ProviderConformanceTests(unittest.TestCase):
    def test_every_catalog_provider_replays_every_required_scenario_offline(self):
        """The suite is complete only when every pinned adapter has every trace."""

        for provider_id in provider_ids():
            adapter = MockProviderAdapter(provider_id)
            for scenario in Scenario:
                with self.subTest(provider_id=provider_id, scenario=scenario.value):
                    trace = adapter.replay(scenario)
                    self.assertEqual(trace.request.provider_id, provider_id)
                    if scenario in {
                        Scenario.MALFORMED_OUTPUT,
                        Scenario.MALFORMED_USAGE,
                        Scenario.UNSUPPORTED_CAPABILITY,
                        Scenario.INCOMPATIBLE_VERSION,
                        Scenario.DUPLICATE_TERMINAL,
                        Scenario.CANCELLATION_RACE,
                        Scenario.UNKNOWN_CAPABILITY,
                    }:
                        with self.assertRaises(ProtocolViolation):
                            assert_conformant_trace(trace)
                    else:
                        self.assertEqual(
                            trace.request.protocol_version, PROTOCOL_VERSION
                        )
                        self.assertEqual(assert_conformant_trace(trace), trace.events)

    def test_success_trace_records_observations_without_canonical_truth_claims(self):
        trace = MockProviderAdapter("codex").replay(Scenario.SUCCESS_STREAM)

        transport_payloads = [event.to_dict()["payload"] for event in trace.events]
        forbidden = {
            "completion_state",
            "completion_verdict",
            "cost",
            "price_usd",
            "authority",
            "verification",
        }
        self.assertFalse(
            forbidden & {key for payload in transport_payloads for key in payload},
            transport_payloads,
        )

    def test_malformed_output_is_a_scripted_protocol_failure_not_a_noop(self):
        trace = MockProviderAdapter("codex").replay(Scenario.MALFORMED_OUTPUT)

        with self.assertRaises(ProtocolViolation):
            assert_conformant_trace(trace)

    def test_unsupported_capability_fails_closed_before_any_stream_events(self):
        trace = MockProviderAdapter("ollama").replay(Scenario.UNSUPPORTED_CAPABILITY)

        self.assertEqual(trace.events, ())
        with self.assertRaises(ProtocolViolation):
            assert_conformant_trace(trace)

    def test_typed_failure_scenarios_keep_the_canonical_error_vocabulary(self):
        expected = {
            Scenario.TIMEOUT: "PROVIDER_TIMEOUT",
            Scenario.AUTH_FAILURE: "AUTH_INVALID",
            Scenario.QUOTA_FAILURE: "PROVIDER_QUOTA_EXHAUSTED",
            Scenario.RATE_LIMIT: "PROVIDER_RATE_LIMITED",
            Scenario.PROVIDER_OUTAGE: "PROVIDER_UNAVAILABLE",
        }
        for scenario, error_code in expected.items():
            with self.subTest(scenario=scenario.value):
                trace = MockProviderAdapter("codex").replay(scenario)
                self.assertEqual(trace.events[-1].payload["error_code"], error_code)
                self.assertEqual(assert_conformant_trace(trace), trace.events)

    def test_incompatible_provider_version_degrades_before_any_stream_events(self):
        trace = MockProviderAdapter("codex").replay(Scenario.INCOMPATIBLE_VERSION)

        self.assertEqual(trace.events, ())
        with self.assertRaises(ProtocolViolation):
            assert_conformant_trace(trace)

    def test_catalog_declared_cancellation_slo_is_retained_exactly(self):
        trace = MockProviderAdapter("codex").replay(Scenario.SUCCESS_STREAM)
        declared = provider_record("codex")["cancellation"]["slo_seconds"]

        self.assertEqual(declared, 5)
        self.assertEqual(trace.slo.cancel_ack_seconds, declared)

    @given(st.sampled_from(tuple(provider_ids())))
    def test_property_generated_duplicate_terminal_is_rejected_for_every_provider(
        self, provider_id
    ):
        trace = MockProviderAdapter(provider_id).replay(Scenario.DUPLICATE_TERMINAL)

        with self.assertRaises(ProtocolViolation):
            assert_conformant_trace(trace)

    @given(
        st.sampled_from(tuple(provider_ids())),
        st.integers(min_value=1, max_value=20),
    )
    def test_property_generated_stream_ordering_gap_is_rejected_for_every_provider(
        self, provider_id, gap
    ):
        trace = MockProviderAdapter(provider_id).replay(Scenario.SUCCESS_STREAM)
        malformed = replace(
            trace,
            events=(
                ProviderEvent(1, 0.0, EventKind.STARTED),
                ProviderEvent(
                    gap + 2,
                    0.1,
                    EventKind.TERMINAL,
                    {"state": "failed"},
                ),
            ),
        )

        with self.assertRaises(ProtocolViolation):
            assert_conformant_trace(malformed)

    @given(st.sampled_from(tuple(provider_ids())))
    def test_property_generated_malformed_usage_is_rejected_for_every_provider(
        self, provider_id
    ):
        trace = MockProviderAdapter(provider_id).replay(Scenario.MALFORMED_USAGE)

        with self.assertRaises(ProtocolViolation):
            assert_conformant_trace(trace)

    @given(st.sampled_from(tuple(provider_ids())))
    def test_property_generated_cancellation_race_is_rejected_for_every_provider(
        self, provider_id
    ):
        trace = MockProviderAdapter(provider_id).replay(Scenario.CANCELLATION_RACE)

        with self.assertRaises(ProtocolViolation):
            assert_conformant_trace(trace)

    @given(st.sampled_from(tuple(provider_ids())))
    def test_property_generated_unknown_capability_is_rejected_for_every_provider(
        self, provider_id
    ):
        trace = MockProviderAdapter(provider_id).replay(Scenario.UNKNOWN_CAPABILITY)

        with self.assertRaises(ProtocolViolation):
            assert_conformant_trace(trace)


if __name__ == "__main__":
    unittest.main()
