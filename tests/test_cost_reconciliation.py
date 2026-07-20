"""Cost/savings numbers reconcile to the cent across every surface (#390).

The information architecture has three levels — run (receipt), period (dashboard
/ overview), and limits (cost firewall / budget) — but they must tell one story.
Savings and spend flow through a multi-layer wrapper chain
(summarize_ledger -> build_savings_report -> build_cockpit -> overview, plus
budget_status and the CLI report), and a refactor could silently split them.
These lock the single-source invariant: the same ledger yields identical numbers
on every surface a user reads.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from opai.app_state import overview
from opai.cockpit import build_cockpit
from opaihub.budget import budget_status
from opaihub.ledger import (
    record_model_call,
    record_route_decision,
    summarize_ledger,
)
from opaihub.savings import build_savings_report


class CostReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        # A paid cloud call (real spend) and a local route (real savings) so both
        # figures are non-zero and a divergence would actually show.
        record_model_call(
            self.root,
            "paid task",
            model_tier="L3",
            provider_type="cloud",
            tokens=1000,
            confirmed=True,
            real_cost_usd=0.05,
        )
        record_route_decision(
            self.root, "local task", model_tier="L1", workflow="gui_message"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_savings_is_identical_on_every_surface(self):
        ledger = summarize_ledger(self.root)["estimated_savings_usd"]
        report = build_savings_report(self.root)["totals"]["estimated_savings_usd"]
        cockpit = build_cockpit(self.root)["savings"]["estimated_savings_usd"]
        home = overview(self.root)["savings"]["estimated_savings_usd"]

        self.assertGreater(ledger, 0.0)  # the local route earned real savings
        self.assertEqual(ledger, report)
        self.assertEqual(ledger, cockpit)
        self.assertEqual(ledger, home)

    def test_spend_is_identical_on_ledger_report_and_budget(self):
        ledger = summarize_ledger(self.root)["estimated_actual_spend_usd"]
        report = build_savings_report(self.root)["totals"]["estimated_actual_spend_usd"]
        spent_today = budget_status(self.root)["spent"]["today_usd"]

        self.assertEqual(round(ledger, 6), 0.05)
        self.assertEqual(round(ledger, 6), round(report, 6))
        self.assertEqual(round(ledger, 6), round(spent_today, 6))

    def test_routed_task_count_reconciles_across_surfaces(self):
        # The ledger's authoritative route_count is the same routed_tasks figure
        # the period surfaces (report / cockpit / overview) present.
        ledger = summarize_ledger(self.root)["route_count"]
        report = build_savings_report(self.root)["totals"]["routed_tasks"]
        cockpit = build_cockpit(self.root)["savings"]["routed_tasks"]
        home = overview(self.root)["savings"]["routed_tasks"]

        self.assertEqual(ledger, 1)
        self.assertEqual(report, ledger)
        self.assertEqual(cockpit, ledger)
        self.assertEqual(home, ledger)


if __name__ == "__main__":
    unittest.main()
