import json
import math
import tempfile
import unittest
from pathlib import Path

from opaihub.budget import (
    _backup_path,
    budget_gate,
    budget_path,
    budget_state,
    budget_status,
    load_budget,
    set_budget,
)
from opaihub.ledger import record_model_call, record_route_decision, rollup_ledger


class BudgetCorruptionRecoveryTests(unittest.TestCase):
    """#470: a corrupt budget.json must not silently drop the user's caps."""

    def test_set_budget_keeps_a_last_known_good_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=1.0, monthly_usd=20.0)
            self.assertTrue(_backup_path(root).exists())
            self.assertEqual(budget_state(root)["state"], "ok")

    def test_corrupt_primary_recovers_the_configured_cap_from_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=1.0, monthly_usd=20.0)
            budget_path(root).write_text("{ this is not json", encoding="utf-8")
            caps = load_budget(root)
            # The user's cap survives the corruption instead of vanishing.
            self.assertEqual(caps["daily_usd_limit"], 1.0)
            self.assertEqual(budget_state(root)["state"], "recovered")

    def test_corrupt_primary_and_backup_fails_closed_on_paid_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=1.0)
            budget_path(root).write_text("{corrupt", encoding="utf-8")
            _backup_path(root).write_text("also corrupt {", encoding="utf-8")
            self.assertEqual(budget_state(root)["state"], "unreadable")
            paid = budget_gate(root, next_cost_usd=0.5, tier="L3")
            self.assertTrue(paid["denied"])
            self.assertEqual(paid["budget_state"], "unreadable")
            # A local/free route is not blocked by an unreadable *budget*.
            local = budget_gate(root, next_cost_usd=0.0, tier="L1")
            self.assertFalse(local["denied"])

    def test_set_budget_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=2.0)
            leftovers = list(budget_path(root).parent.glob("*.tmp"))
            self.assertEqual(leftovers, [])


class BudgetConfigTests(unittest.TestCase):
    def test_set_and_load_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=1.5, monthly_usd=20.0, per_task_usd=0.5)
            caps = load_budget(root)
        self.assertEqual(caps["daily_usd_limit"], 1.5)
        self.assertEqual(caps["monthly_usd_limit"], 20.0)


class BudgetCapValidationTests(unittest.TestCase):
    """#469: a NaN/infinite/negative cap must never reach a comparison — every
    ``spent + cost > NaN`` is false, silently disabling the ceiling."""

    def test_set_budget_rejects_non_finite_and_negative_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for bad in (float("nan"), float("inf"), float("-inf"), -0.01):
                for kwarg in ("daily_usd", "monthly_usd", "per_task_usd"):
                    with self.assertRaises(ValueError):
                        set_budget(root, **{kwarg: bad})
            # A rejected set never wrote a budget file.
            self.assertFalse(budget_path(root).exists())

    def test_persisted_budget_json_is_never_nan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=2.0, monthly_usd=30.0)
            raw = budget_path(root).read_text(encoding="utf-8")
        self.assertNotIn("NaN", raw)
        self.assertNotIn("Infinity", raw)
        # And it round-trips as strict JSON.
        json.loads(raw)  # would raise on NaN/Infinity tokens under a strict parser

    def test_corrupt_nan_cap_on_disk_fails_closed_not_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = budget_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            # A hand-edited/legacy file with a NaN ceiling (default json emits NaN).
            path.write_text(
                json.dumps({"daily_usd_limit": float("nan"), "monthly_usd_limit": 100}),
                encoding="utf-8",
            )
            caps = load_budget(root)
            self.assertTrue(math.isfinite(caps["daily_usd_limit"]))
            self.assertEqual(caps["daily_usd_limit"], 0.0)  # coerced to block
            gate = budget_gate(root, next_cost_usd=0.5, tier="L3")
        # The corrupt ceiling denies the paid call instead of failing open.
        self.assertTrue(gate["denied"])

    def test_status_reports_spend_and_remaining(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=1.0)
            record_model_call(
                root,
                "x",
                model_tier="L3",
                provider_type="cloud",
                tokens=1000,
                confirmed=True,
            )
            status = budget_status(root)
        self.assertGreater(status["spent"]["today_usd"], 0.0)
        self.assertLess(status["remaining"]["today_usd"], 1.0)

    def test_route_comparison_estimate_is_not_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(root, "route", model_tier="L2")
            before_call = budget_status(root)
            record_model_call(
                root,
                "call",
                model_tier="L2",
                provider_type="free_api",
                tokens=100,
                confirmed=True,
            )
            after_call = budget_status(root)

        self.assertEqual(before_call["spent"]["today_usd"], 0.0)
        self.assertGreater(after_call["spent"]["today_usd"], 0.0)


class BudgetGateTests(unittest.TestCase):
    def test_local_route_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = budget_gate(Path(tmp), tier="L0", provider_type="local")
        self.assertTrue(gate["allowed"])
        self.assertEqual(gate["exit_code"], 0)

    def test_panic_blocks_cloud_but_allows_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, panic=True)
            cloud = budget_gate(
                root, tier="L3", provider_type="cloud", next_cost_usd=0.07
            )
            local = budget_gate(root, tier="L0", provider_type="local")
        self.assertTrue(cloud["denied"])
        self.assertEqual(cloud["exit_code"], 1)
        self.assertTrue(local["allowed"])

    def test_daily_budget_overflow_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=0.01, monthly_usd=100.0)
            gate = budget_gate(
                root,
                tier="L3",
                provider_type="cloud",
                next_cost_usd=5.0,
                estimated_tokens=6000,
            )
        self.assertTrue(gate["denied"])
        self.assertIn("Daily budget exceeded", " ".join(gate["reasons"]))

    def test_monthly_budget_overflow_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=100.0, monthly_usd=0.01)
            gate = budget_gate(
                root,
                tier="L3",
                provider_type="cloud",
                next_cost_usd=5.0,
            )
        self.assertTrue(gate["denied"])

    def test_cloud_within_budget_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_budget(root, daily_usd=100.0, monthly_usd=100.0)
            gate = budget_gate(
                root,
                tier="L2",
                provider_type="cloud",
                next_cost_usd=0.01,
            )
        # Cloud always needs confirmation (policy), even within budget.
        self.assertTrue(gate["requires_confirmation"] or gate["denied"])


class RollupTests(unittest.TestCase):
    def test_rollups_bucket_by_agent_repo_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(
                root, "a", model_tier="L0", agent="claude", repo="api"
            )
            record_route_decision(
                root,
                "b",
                model_tier="L1",
                agent="cursor",
                repo="api",
                source="benchmark",
            )
            rollups = rollup_ledger(root)
        self.assertEqual(rollups["by_agent"]["claude"]["routes"], 1)
        self.assertEqual(rollups["by_repo"]["api"]["routes"], 2)
        self.assertIn("benchmark", rollups["by_source"])
        self.assertEqual(len(rollups["by_day"]), 1)
        self.assertGreater(rollups["totals"]["estimated_savings_usd"], 0.0)

    def test_benchmark_and_route_share_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            route = record_route_decision(root, "x", model_tier="L0", source="route")
            bench = record_route_decision(
                root, "y", model_tier="L0", source="benchmark"
            )
        self.assertEqual(set(route) - {"agent", "repo"}, set(bench) - {"agent", "repo"})
        self.assertEqual(route["event_type"], bench["event_type"])


if __name__ == "__main__":
    unittest.main()
