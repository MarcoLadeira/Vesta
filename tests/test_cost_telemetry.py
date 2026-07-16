"""Provider cost telemetry (#178): normalized, redacted, honestly labelled."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub.cost_telemetry import (
    CostTelemetry,
    estimated_telemetry,
    normalize_account_result,
    normalize_api_usage,
    quota_from_headers,
    read_cost_events,
    record_workflow_cost,
    summarize_cost_telemetry,
    usage_report_to_cost_telemetry,
)
from opaihub.usage_report import ProviderTurnUsage, UsageReport, UsageValue

from tests._helpers import FakeAccountRunner, make_repo


class NormalizationTests(unittest.TestCase):
    def test_usage_report_is_the_single_truth_for_compatibility_telemetry(self):
        report = UsageReport(
            schema_version=1,
            run_id="run-1",
            model_id="account:claude:opus-4.8",
            provider_id="claude",
            turns=(
                ProviderTurnUsage.from_provider(
                    turn_index=1,
                    input_tokens=80,
                    output_tokens=20,
                    total=120,
                    cost_usd=0.1,
                    cost_provenance="actual",
                    provider_quota={"remaining": "9"},
                ),
            ),
        )

        telemetry = usage_report_to_cost_telemetry(report)

        self.assertEqual(telemetry.input_tokens, 80)
        self.assertEqual(telemetry.output_tokens, 20)
        self.assertEqual(telemetry.total_tokens, 120)
        self.assertEqual(telemetry.tokens_measurement, "provider")
        self.assertEqual(telemetry.cost_usd, 0.1)
        self.assertEqual(telemetry.cost_measurement, "actual")
        self.assertEqual(telemetry.quota, {"remaining": "9"})
        self.assertEqual(telemetry.source, "usage_report")

    def test_usage_report_cost_precision_is_independent_from_token_precision(self):
        report = UsageReport(
            schema_version=1,
            run_id="run-1",
            model_id="free:gemini:flash",
            provider_id="gemini",
            turns=(
                ProviderTurnUsage(
                    turn_index=1,
                    input_tokens=UsageValue(80, "estimated"),
                    output_tokens=UsageValue(20, "estimated"),
                    total_tokens=UsageValue(100, "estimated"),
                    cached_input_tokens=UsageValue(None, "unknown"),
                    reasoning_tokens=UsageValue(None, "unknown"),
                    cost_usd=UsageValue(0.0, "actual"),
                ),
            ),
        )

        telemetry = usage_report_to_cost_telemetry(report)

        self.assertEqual(telemetry.tokens_measurement, "estimated")
        self.assertEqual(telemetry.cost_measurement, "actual")

    def test_usage_report_missing_components_remain_unknown_in_telemetry(self):
        report = UsageReport(
            schema_version=1,
            run_id="run-1",
            model_id="free:gemini:flash",
            provider_id="gemini",
            turns=(ProviderTurnUsage.from_provider(turn_index=1, total=42),),
        )

        telemetry = usage_report_to_cost_telemetry(report)

        self.assertIsNone(telemetry.input_tokens)
        self.assertIsNone(telemetry.output_tokens)
        self.assertEqual(telemetry.total_tokens, 42)

    def test_usage_report_quota_drops_arbitrary_secret_bearing_fields(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz"
        report = UsageReport(
            schema_version=1,
            run_id="run-1",
            model_id="free:gemini:flash",
            provider_id="gemini",
            turns=(
                ProviderTurnUsage.from_provider(
                    turn_index=1,
                    total=42,
                    provider_quota={
                        "remaining": 9,
                        "authorization": f"Bearer {secret}",
                        "x-ratelimit-note": secret,
                    },
                ),
            ),
        )

        telemetry = usage_report_to_cost_telemetry(report)

        self.assertEqual(telemetry.quota["remaining"], "9")
        self.assertNotIn("authorization", telemetry.quota)
        self.assertNotIn(secret, str(telemetry.quota))

    def test_claude_reported_dollars_are_actual(self):
        telemetry = normalize_account_result(
            "claude", {"cost_usd": 0.0421}, model="account:claude:sonnet"
        )
        self.assertEqual(telemetry.cost_usd, 0.0421)
        self.assertEqual(telemetry.cost_measurement, "actual")
        self.assertEqual(telemetry.source, "account_cli")

    def test_codex_without_dollars_stays_estimated_never_zero(self):
        telemetry = normalize_account_result("codex", {"cost_usd": None})
        self.assertIsNone(telemetry.cost_usd)
        self.assertEqual(telemetry.cost_measurement, "estimated")

    def test_boolean_cost_is_not_mistaken_for_a_real_spend(self):
        telemetry = normalize_account_result("claude", {"cost_usd": True})
        self.assertIsNone(telemetry.cost_usd)
        self.assertEqual(telemetry.cost_measurement, "estimated")

    def test_provider_usage_bodies_yield_provider_tokens_and_quota(self):
        telemetry = normalize_api_usage(
            "groq",
            {"usage": {"prompt_tokens": 100, "completion_tokens": 40}},
            {
                "X-RateLimit-Remaining-Requests": "97",
                "Retry-After": "2",
                "Set-Cookie": "not-quota",
            },
            model="free:groq:llama",
        )
        self.assertEqual(telemetry.input_tokens, 100)
        self.assertEqual(telemetry.output_tokens, 40)
        self.assertEqual(telemetry.total_tokens, 140)
        self.assertEqual(telemetry.tokens_measurement, "provider")
        self.assertEqual(
            telemetry.quota,
            {"x-ratelimit-remaining-requests": "97", "retry-after": "2"},
        )

    def test_rate_over_provider_tokens_is_derived_not_actual(self):
        telemetry = normalize_api_usage(
            "groq",
            {"usage": {"prompt_tokens": 500, "completion_tokens": 500}},
            usd_per_1k_tokens=0.10,
        )
        self.assertEqual(telemetry.cost_usd, 0.1)
        self.assertEqual(telemetry.cost_measurement, "derived")

    def test_missing_usage_never_claims_provider_measurement(self):
        telemetry = normalize_api_usage("mistral", {}, usd_per_1k_tokens=0.10)
        self.assertEqual(telemetry.tokens_measurement, "estimated")
        self.assertIsNone(telemetry.cost_usd)
        self.assertEqual(telemetry.cost_measurement, "estimated")

    def test_unknown_measurements_fail_closed(self):
        with self.assertRaises(ValueError):
            CostTelemetry(provider="claude", cost_measurement="vibes")
        with self.assertRaises(ValueError):
            CostTelemetry(provider="claude", tokens_measurement="vibes")

    def test_quota_header_values_are_bounded(self):
        quota = quota_from_headers({"x-ratelimit-note": "v" * 500})
        self.assertEqual(len(quota["x-ratelimit-note"]), 64)


class LedgerTests(unittest.TestCase):
    def test_recorded_events_are_redacted_and_task_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "sk-abcdefghijklmnopqrstuv"
            record_workflow_cost(
                root,
                "task-1",
                estimated_telemetry("claude", tokens=10, model=f"key {secret}"),
                task=f"deploy with {secret}",
            )
            raw = (root / ".opaihub" / "agent" / "events.jsonl").read_text("utf-8")
            events = read_cost_events(root)

        self.assertNotIn(secret, raw)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["task_id"], "task-1")

    def test_summary_keeps_actual_derived_and_estimated_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_workflow_cost(
                root,
                "t1",
                normalize_account_result("claude", {"cost_usd": 0.5}),
            )
            record_workflow_cost(
                root,
                "t2",
                normalize_api_usage(
                    "groq",
                    {"usage": {"prompt_tokens": 1000, "completion_tokens": 0}},
                    {"x-ratelimit-remaining-tokens": "12000"},
                    usd_per_1k_tokens=0.2,
                ),
            )
            record_workflow_cost(
                root, "t3", estimated_telemetry("mistral", tokens=50, cost_usd=0.01)
            )
            summary = summarize_cost_telemetry(root)

        self.assertTrue(summary["has_data"])
        self.assertEqual(summary["calls"], 3)
        self.assertEqual(summary["actual_usd"], 0.5)
        self.assertEqual(summary["derived_usd"], 0.2)
        self.assertEqual(summary["estimated_usd"], 0.01)
        self.assertEqual(summary["total_tokens"], 1050)
        self.assertEqual(
            summary["by_provider"]["groq"]["last_quota"],
            {"x-ratelimit-remaining-tokens": "12000"},
        )
        self.assertEqual(summary["by_provider"]["claude"]["calls"], 1)

    def test_empty_summary_is_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = summarize_cost_telemetry(Path(tmp))
        self.assertFalse(summary["has_data"])
        self.assertEqual(summary["actual_usd"], 0.0)


class PipelineTelemetryTests(unittest.TestCase):
    def test_account_turn_records_actual_telemetry_into_workflow_state(self):
        from opaihub.gui_pipeline import handle_gui_message

        runner = FakeAccountRunner(text="done", cost=0.0421)
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "Explain the routing flow only.",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=runner,
            )
            events = read_cost_events(root)

        telemetry = result["cost_telemetry"]
        self.assertEqual(telemetry["cost_measurement"], "actual")
        self.assertEqual(telemetry["cost_usd"], 0.0421)
        self.assertEqual(telemetry["provider"], "claude")
        self.assertEqual(result["workflow"]["cost"]["telemetry"], telemetry)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["metadata"]["cost_measurement"], "actual")

    def test_codex_style_missing_cost_stays_estimated_in_workflow_state(self):
        from opaihub.gui_pipeline import handle_gui_message

        runner = FakeAccountRunner(account_id="codex", text="done", cost=None)
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            result = handle_gui_message(
                root,
                "Explain the routing flow only.",
                model_id="account:codex:gpt",
                mode="ask",
                account_runner=runner,
            )

        telemetry = result["cost_telemetry"]
        self.assertEqual(telemetry["cost_measurement"], "estimated")
        self.assertIsNone(telemetry["cost_usd"])

    def test_confirmation_limits_still_block_before_any_telemetry(self):
        from opaihub.gui_pipeline import handle_gui_message
        from opaihub.gui_preferences import save_usage_limit
        from opaihub.ledger import record_model_call

        model_id = "account:claude:haiku"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_usage_limit(root, model_id, metric="tokens", limit=10, window="month")
            record_model_call(
                root,
                "earlier",
                model_tier="L3",
                provider_type="cloud",
                tokens=10,
                confirmed=True,
                model_id=model_id,
                provider_id="claude",
            )
            with mock.patch("opai.app_state.ask") as provider_call:
                result = handle_gui_message(
                    root, "continue", model_id=model_id, mode="ask"
                )
                provider_call.assert_not_called()
            events = read_cost_events(root)

        self.assertEqual(result["status"], "needs_limit_confirmation")
        self.assertEqual(events, [])


class CockpitTelemetryTests(unittest.TestCase):
    def test_cockpit_surfaces_actual_vs_estimated_spend(self):
        from opai.cockpit import build_cockpit, render_cockpit

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_workflow_cost(
                root, "t1", normalize_account_result("claude", {"cost_usd": 1.25})
            )
            payload = build_cockpit(root)
            text = render_cockpit(payload)

        telemetry = payload["cost_telemetry"]
        self.assertTrue(telemetry["has_data"])
        self.assertEqual(telemetry["actual_usd"], 1.25)
        self.assertIn("Provider telemetry", text)
        self.assertIn("$1.2500 actual", text)

    def test_cockpit_without_telemetry_says_so(self):
        from opai.cockpit import build_cockpit, render_cockpit

        with tempfile.TemporaryDirectory() as tmp:
            text = render_cockpit(build_cockpit(Path(tmp)))
        self.assertIn("no provider calls recorded yet", text)


if __name__ == "__main__":
    unittest.main()
