import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from opai.cli import main as opai_main
from opaihub.analytics import build_analytics_summary
from opaihub.cost_model import (
    estimate_route_savings,
    estimate_tokens,
    estimate_tokens_for_chars,
    load_cost_model,
)
from opaihub.ledger import (
    MODEL_CALL_SCHEMA_VERSION,
    ledger_path,
    read_events,
    record_model_call,
    record_model_call_finalized,
    record_model_call_started,
    record_route_decision,
    summarize_ledger,
    task_fingerprint,
)
from opaihub.usage_report import ProviderTurnUsage
from opaihub.savings import build_savings_report, render_savings_markdown


class CostModelTests(unittest.TestCase):
    def test_local_tiers_have_zero_cost_and_save_versus_baseline(self):
        model = load_cost_model(Path.cwd())
        savings = estimate_route_savings("L0", task_tokens=10000, model=model)
        self.assertEqual(savings["estimated_actual_usd"], 0.0)
        self.assertGreater(savings["estimated_baseline_usd"], 0.0)
        self.assertGreater(savings["estimated_savings_usd"], 0.0)
        self.assertTrue(savings["cloud_call_avoided"])

    def test_baseline_tier_saves_nothing_against_itself(self):
        model = load_cost_model(Path.cwd())
        savings = estimate_route_savings(
            model["baseline_tier"], task_tokens=5000, model=model
        )
        self.assertEqual(savings["estimated_savings_usd"], 0.0)
        self.assertFalse(savings["cloud_call_avoided"])

    def test_token_estimate_uses_chars_per_token(self):
        self.assertEqual(estimate_tokens("x" * 400), 100)
        self.assertEqual(estimate_tokens(""), 0)

    def test_token_estimate_accepts_a_count_without_allocating_text(self):
        model = {"chars_per_token": 5}
        self.assertEqual(estimate_tokens_for_chars(10_000_000, model), 2_000_000)
        self.assertEqual(estimate_tokens_for_chars(0, model), 0)


class LedgerTests(unittest.TestCase):
    def test_legacy_writer_remains_schema_one_and_sequenced(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = record_model_call(
                Path(tmp),
                "legacy-compatible",
                model_tier="L2",
                provider_type="free_api",
                tokens=10,
                confirmed=True,
                model_id="account:claude:opus-4.8",
            )

        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(event["ledger_sequence"], 1)
        self.assertEqual(event["model_id"], "account:claude:opus-4.8")
        self.assertEqual(event["canonical_model_id"], "account:claude:opus")

    def test_v2_provider_turn_is_counted_once_with_reported_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_model_call_started(
                root,
                "task",
                call_id="run:1",
                run_id="run",
                turn_index=1,
                model_id="account:claude:opus",
                provider_id="claude",
                model_tier="L3",
                provider_type="account",
                confirmed=True,
            )
            event = record_model_call_finalized(
                root,
                "task",
                call_id="run:1",
                usage=ProviderTurnUsage.from_provider(
                    turn_index=1,
                    total=120,
                    cost_usd=0.25,
                    cost_provenance="actual",
                ),
            )
            summary = summarize_ledger(root)

        self.assertEqual(event["schema_version"], MODEL_CALL_SCHEMA_VERSION)
        self.assertEqual(summary["model_call_count"], 1)
        self.assertEqual(summary["estimated_actual_spend_usd"], 0.25)

    def test_route_event_records_savings_without_storing_raw_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(
                root,
                "deploy production with token=sk-abcdef1234567890abcdef",
                model_tier="L0",
                workflow="release_prepare",
                full_context_chars=4000,
                compact_context_chars=900,
            )
            raw = ledger_path(root).read_text(encoding="utf-8")

        self.assertNotIn("deploy", raw)
        self.assertNotIn("production", raw)
        self.assertNotIn("sk-abcdef1234567890abcdef", raw)
        event = json.loads(raw.strip())
        self.assertEqual(event["model_tier"], "L0")
        self.assertEqual(
            event["task_hash"],
            task_fingerprint("deploy production with token=sk-abcdef1234567890abcdef"),
        )
        self.assertEqual(event["context_chars_saved"], 3100)
        self.assertGreater(event["estimated_savings_usd"], 0.0)

    def test_store_summary_is_redacted_when_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(
                root,
                "fix bug api_key=sk-deadbeefdeadbeef1234",
                model_tier="L1",
                store_summary=True,
            )
            event = read_events(root)[0]
        self.assertIn("task_summary_redacted", event)
        self.assertNotIn("sk-deadbeefdeadbeef1234", event["task_summary_redacted"])

    def test_summarize_aggregates_tiers_and_cloud_avoidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(root, "show git status", model_tier="L0")
            record_route_decision(root, "fix failing tests", model_tier="L1")
            record_route_decision(root, "compare free API route", model_tier="L2")
            record_model_call(
                root,
                "confirmed cloud call",
                model_tier="L2",
                provider_type="cloud",
                tokens=2000,
                confirmed=True,
            )
            summary = summarize_ledger(root)
            expected_spend = sum(
                event.get("estimated_actual_usd", 0.0)
                for event in read_events(root)
                if event.get("event_type") == "model_call"
            )

        self.assertEqual(summary["route_count"], 3)
        self.assertEqual(summary["model_call_count"], 1)
        self.assertEqual(summary["cloud_calls_avoided"], 2)
        self.assertEqual(summary["routes_by_tier"], {"L0": 1, "L1": 1, "L2": 1})
        self.assertGreater(summary["estimated_savings_usd"], 0.0)
        self.assertGreater(summary["route_estimated_actual_usd"], 0.0)
        self.assertAlmostEqual(
            summary["estimated_actual_spend_usd"], expected_spend, places=6
        )
        self.assertGreater(summary["estimated_actual_spend_usd"], 0.0)

    def test_empty_ledger_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = summarize_ledger(Path(tmp))
        self.assertEqual(summary["event_count"], 0)
        self.assertEqual(summary["estimated_savings_usd"], 0.0)

    def test_summary_ignores_valid_json_rows_that_are_not_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(root, "show git status", model_tier="L0")
            with ledger_path(root).open("a", encoding="utf-8") as handle:
                handle.write("null\n")
                handle.write('"not an event"\n')
                handle.write("[]\n")

            summary = summarize_ledger(root)

        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["route_count"], 1)

    def test_boolean_cost_is_not_counted_as_one_dollar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event = record_model_call(
                root,
                "call",
                model_tier="L3",
                provider_type="cloud",
                tokens=1000,
                confirmed=True,
            )
            event["estimated_actual_usd"] = True
            ledger_path(root).write_text(json.dumps(event) + "\n", encoding="utf-8")

            summary = summarize_ledger(root)

        self.assertEqual(summary["estimated_actual_spend_usd"], 0.0)

    def test_nonfinite_cost_is_ignored_instead_of_poisoning_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event = record_model_call(
                root,
                "call",
                model_tier="L3",
                provider_type="cloud",
                tokens=1000,
                confirmed=True,
            )
            event["estimated_actual_usd"] = float("nan")
            ledger_path(root).write_text(json.dumps(event) + "\n", encoding="utf-8")

            summary = summarize_ledger(root)

        self.assertEqual(summary["estimated_actual_spend_usd"], 0.0)


class RouteReadOnlyTests(unittest.TestCase):
    """Issue #12: route is read-only unless --record is passed."""

    def _snapshot(self, root: Path) -> set:
        return {p for p in root.rglob("*")}

    def test_route_without_record_writes_no_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Marker makes this dir an unambiguous project root for discovery.
            (root / "pyproject.toml").write_text("[project]\nname='x'\n")
            before = self._snapshot(root)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = opai_main(["route", "show git status", "--project", str(root)])
            after = self._snapshot(root)
        self.assertEqual(code, 0)
        self.assertFalse(ledger_path(root).exists())
        self.assertEqual(before, after)

    def test_route_with_record_writes_exactly_one_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\n")
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                opai_main(["route", "fix a bug", "--record", "--project", str(root)])
            self.assertTrue(ledger_path(root).exists())
            self.assertEqual(len(read_events(root)), 1)


class SavingsReportTests(unittest.TestCase):
    def test_report_headline_prompts_recording_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_savings_report(Path(tmp))
        self.assertFalse(report["has_data"])
        self.assertIn("--record", report["headline"])

    def test_report_and_markdown_render_after_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(
                root,
                "fix bug",
                model_tier="L0",
                full_context_chars=5000,
                compact_context_chars=500,
            )
            report = build_savings_report(root)
            markdown = render_savings_markdown(report)

        self.assertTrue(report["has_data"])
        self.assertEqual(report["totals"]["routed_tasks"], 1)
        self.assertGreater(report["totals"]["estimated_savings_usd"], 0.0)
        self.assertIn("Vesta Savings Report", markdown)
        self.assertIn("Cloud calls avoided", markdown)

    def test_analytics_summary_reads_real_ledger_not_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_model_call(
                root,
                "cloud",
                model_tier="L3",
                provider_type="cloud",
                tokens=1000,
                confirmed=True,
            )
            summary = build_analytics_summary(root)
        # Was hard-coded 0.0 before issue #17; now reflects ledger events.
        self.assertGreater(summary["estimated_spend_usd"], 0.0)
        self.assertIn("estimated_savings_usd", summary)
        self.assertEqual(summary["ledger_event_count"], 1)


if __name__ == "__main__":
    unittest.main()
