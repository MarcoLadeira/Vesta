"""Paid calls are spend, never savings (#76): receipt, ledger, and totals truth."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest import mock

from opaihub.gui_pipeline import (
    _gate_receipt_savings,
    build_savings_receipt,
    handle_gui_message,
)
from opaihub.ledger import (
    read_events,
    record_route_decision,
    summarize_ledger,
)
from opaihub.savings import build_savings_report

from tests._helpers import FakeAccountRunner, FakeLocalRunner, make_repo


class PaidReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _paid(self, cost):
        return build_savings_receipt(
            self.root,
            task="task",
            selected_model="account:claude:sonnet",
            selected_mode="ask",
            chosen_tier="L3",
            actual_cost_usd=cost,
            paid_call=True,
        )

    def test_measured_paid_cost_is_spend_with_zero_savings(self):
        receipt = self._paid(0.042)
        self.assertEqual(receipt["estimated_savings_usd"], 0.0)
        self.assertFalse(receipt["paid_call_avoided"])
        self.assertAlmostEqual(receipt["estimated_actual_usd"], 0.042, places=4)
        self.assertEqual(receipt["confidence"], "actual")
        self.assertTrue(receipt["paid_call"])
        self.assertEqual(
            receipt["savings_basis"], "paid_call_records_spend_not_savings"
        )

    def test_subscription_style_zero_is_unknown_never_free(self):
        receipt = self._paid(0.0)
        self.assertEqual(receipt["confidence"], "unknown")
        self.assertGreater(
            receipt["estimated_actual_usd"],
            0.0,
            "a reported $0.00 must not be presented as $0.00 actual spend",
        )
        self.assertEqual(receipt["estimated_savings_usd"], 0.0)
        self.assertEqual(receipt["measured_cost_usd"], 0.0)

    def test_unreported_paid_cost_is_estimated_spend(self):
        receipt = self._paid(None)
        self.assertEqual(receipt["confidence"], "estimated")
        self.assertGreater(receipt["estimated_actual_usd"], 0.0)
        self.assertEqual(receipt["estimated_savings_usd"], 0.0)
        self.assertIsNone(receipt["measured_cost_usd"])

    def test_boolean_cost_is_not_a_measurement(self):
        receipt = self._paid(True)
        self.assertIsNone(receipt["measured_cost_usd"])
        self.assertEqual(receipt["confidence"], "estimated")

    def test_local_routes_still_report_estimated_avoided_spend(self):
        receipt = build_savings_receipt(
            self.root,
            task="task",
            selected_model="auto",
            selected_mode="ask",
            chosen_tier="L1",
        )
        self.assertGreater(receipt["estimated_savings_usd"], 0.0)
        self.assertTrue(receipt["paid_call_avoided"])
        self.assertFalse(receipt["paid_call"])
        self.assertEqual(receipt["savings_basis"], "estimated_vs_unrouted_baseline")
        self.assertEqual(receipt["confidence"], "estimated")

    def test_new_receipts_are_versioned(self):
        self.assertEqual(self._paid(0.01)["schema"], 2)


class PipelineSpendTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_account_turn_reports_spend_not_savings(self):
        fake = FakeAccountRunner(text="done", cost=0.042)
        result = handle_gui_message(
            self.root,
            "task",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
        )
        receipt = result["receipt"]
        self.assertEqual(receipt["estimated_savings_usd"], 0.0)
        self.assertFalse(receipt["paid_call_avoided"])
        self.assertEqual(receipt["confidence"], "actual")
        self.assertTrue(receipt["paid_call"])

    def test_subscription_zero_account_turn_is_unknown(self):
        fake = FakeAccountRunner(text="done", cost=0.0)
        result = handle_gui_message(
            self.root,
            "task",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
        )
        receipt = result["receipt"]
        self.assertEqual(receipt["confidence"], "unknown")
        self.assertEqual(receipt["estimated_savings_usd"], 0.0)
        self.assertGreater(receipt["estimated_actual_usd"], 0.0)

    def test_cancelled_account_turn_records_no_receipt_or_route(self):
        import threading

        cancel = threading.Event()
        cancel.set()
        result = handle_gui_message(
            self.root,
            "task",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=FakeAccountRunner(text="done", cost=0.042),
            cancel=cancel,
        )
        self.assertEqual(result["status"], "cancelled")
        event_types = {event.get("event_type") for event in read_events(self.root)}
        self.assertNotIn("gui_receipt", event_types)
        self.assertNotIn("route_decision", event_types)

    def test_retry_records_one_spend_per_answered_call_no_double_count(self):
        for _ in range(2):
            handle_gui_message(
                self.root,
                "task",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(text="done", cost=0.01),
            )
        events = read_events(self.root)
        model_calls = [
            event for event in events if event.get("event_type") == "model_call"
        ]
        receipts = [
            event for event in events if event.get("event_type") == "gui_receipt"
        ]
        self.assertEqual(len(model_calls), 2)
        self.assertEqual(len(receipts), 2)
        summary = summarize_ledger(self.root)
        self.assertEqual(summary["estimated_savings_usd"], 0.0)
        self.assertAlmostEqual(summary["estimated_actual_spend_usd"], 0.02, places=4)


class LegacyExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_legacy_cloud_tier_routes_cannot_inflate_savings(self):
        # The old "CLOUD" pseudo-tier had a $0 rate, so its route events
        # claimed the full baseline as savings. They must be visible as
        # legacy and excluded from every trusted total.
        record_route_decision(
            self.root, "legacy paid task", model_tier="CLOUD", workflow="gui_message"
        )
        record_route_decision(
            self.root, "good local task", model_tier="L1", workflow="gui_message"
        )
        summary = summarize_ledger(self.root)

        self.assertEqual(summary["legacy_route_count"], 1)
        self.assertEqual(summary["route_count"], 1)
        self.assertNotIn("CLOUD", summary["routes_by_tier"])
        trusted = summary["estimated_savings_usd"]
        self.assertGreater(trusted, 0.0)  # the L1 route's genuine estimate

        # The legacy event does claim positive savings - and adding another
        # one must not move the trusted total by a cent.
        legacy_claim = record_route_decision(
            self.root, "another legacy", model_tier="CLOUD"
        )["estimated_savings_usd"]
        self.assertGreater(legacy_claim, 0.0)
        self.assertEqual(summarize_ledger(self.root)["estimated_savings_usd"], trusted)


class VerdictGatedSavingsTests(unittest.TestCase):
    """#381: savings are claimed only for a run that met its objective — the
    per-run receipt and the aggregate ledger both honour the completion verdict,
    so no partial/blocked/timeout run can inflate savings on any surface."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _local_receipt(self):
        return build_savings_receipt(
            self.root,
            task="task",
            selected_model="auto",
            selected_mode="ask",
            chosen_tier="L1",
        )

    def test_gate_zeroes_savings_for_a_non_completed_run(self):
        receipt = self._local_receipt()
        self.assertGreater(receipt["estimated_savings_usd"], 0.0)  # a real claim
        gated = _gate_receipt_savings(dict(receipt), completed=False)
        self.assertEqual(gated["estimated_savings_usd"], 0.0)
        self.assertFalse(gated["paid_call_avoided"])
        self.assertEqual(gated["savings_basis"], "savings_claimed_only_for_completed_runs")
        # Actual spend is preserved — the user still sees what the run cost.
        self.assertEqual(gated["estimated_actual_usd"], receipt["estimated_actual_usd"])

    def test_gate_is_a_no_op_for_a_completed_run(self):
        receipt = self._local_receipt()
        gated = _gate_receipt_savings(dict(receipt), completed=True)
        self.assertEqual(gated["estimated_savings_usd"], receipt["estimated_savings_usd"])
        self.assertEqual(gated["paid_call_avoided"], receipt["paid_call_avoided"])

    def test_partial_local_run_claims_no_savings_on_any_surface(self):
        # An edit-intent local run that answers but changes no files is PARTIAL
        # (change_not_verified): no savings on the receipt and none in the
        # aggregate ledger, even though the run "answered".
        selected = FakeLocalRunner(model="qwen2.5-coder:7b", answer="I changed it.")

        def partial_run_ask(root, task, **kwargs):
            return {
                "status": "answered_locally",
                "answer": "I changed it.",
                "changed_files": [],
            }

        with (
            mock.patch(
                "opaihub.local_runner.runner_for_model", return_value=selected
            ),
            mock.patch("opaihub.ask.run_ask", side_effect=partial_run_ask),
        ):
            res = handle_gui_message(
                self.root,
                "fix the bug in parser.py",
                model_id="ollama:qwen2.5-coder:7b",
                mode="full-auto",
            )

        self.assertEqual(res["completion_verdict"]["verdict"], "partial")
        self.assertEqual(res["completion_verdict"]["reason_code"], "change_not_verified")
        self.assertEqual(res["receipt"]["estimated_savings_usd"], 0.0)
        self.assertEqual(
            res["receipt"]["savings_basis"], "savings_claimed_only_for_completed_runs"
        )
        # The aggregate never counted the partial run's savings.
        self.assertEqual(summarize_ledger(self.root)["estimated_savings_usd"], 0.0)

    def test_savings_report_names_the_exclusion(self):
        record_route_decision(self.root, "legacy", model_tier="CLOUD")
        report = build_savings_report(self.root)
        self.assertEqual(report["totals"]["legacy_routes_excluded"], 1)
        self.assertFalse(report["has_data"])  # no trusted routes yet
        self.assertTrue(any("legacy" in item.lower() for item in report["assumptions"]))
        self.assertTrue(
            any(
                "spend with zero implied savings" in item
                for item in report["assumptions"]
            )
        )


if __name__ == "__main__":
    unittest.main()
