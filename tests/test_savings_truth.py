from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    from _helpers import FakeAccountRunner, make_repo
except ModuleNotFoundError:  # direct: python -m unittest tests.test_savings_truth
    from tests._helpers import FakeAccountRunner, make_repo
from opai.gui_desktop import run_once
from opaihub.budget import budget_status
from opaihub.dashboard_html import build_dashboard_html
from opaihub.gui_pipeline import build_savings_receipt, handle_gui_message
from opaihub.ledger import (
    EVENT_MODEL_CALL,
    record_model_call,
    record_route_decision,
    summarize_ledger,
)
from opaihub.proof import build_proof_bundle, render_proof_markdown
from opaihub.savings import build_savings_report, render_savings_markdown


class SavingsTruthLedgerTests(unittest.TestCase):
    def test_l3_model_call_records_nonzero_estimated_spend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            event = record_model_call(
                root,
                "paid account task",
                model_tier="L3",
                provider_type="cloud",
                tokens=6000,
                confirmed=True,
            )

        self.assertEqual(event["event_type"], EVENT_MODEL_CALL)
        self.assertGreater(event["estimated_actual_usd"], 0.0)
        self.assertEqual(event["cost_confidence"], "estimated")

    def test_real_cost_overrides_estimate_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            event = record_model_call(
                root,
                "paid account task",
                model_tier="L3",
                provider_type="cloud",
                tokens=6000,
                confirmed=True,
                real_cost_usd=0.042,
            )

        self.assertEqual(event["estimated_actual_usd"], 0.042)
        self.assertEqual(event["cost_confidence"], "actual")

    def test_paid_model_call_never_counts_as_avoided_cloud_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            record_model_call(
                root,
                "paid account task",
                model_tier="L3",
                provider_type="cloud",
                tokens=6000,
                confirmed=True,
                real_cost_usd=0.042,
            )
            summary = summarize_ledger(root)

        self.assertEqual(summary["model_call_count"], 1)
        self.assertEqual(summary["cloud_calls_avoided"], 0)
        self.assertEqual(summary["estimated_savings_usd"], 0.0)
        self.assertEqual(summary["estimated_actual_spend_usd"], 0.042)


class SavingsTruthReceiptTests(unittest.TestCase):
    def test_paid_account_receipt_shows_spend_and_zero_savings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            receipt = build_savings_receipt(
                root,
                task="review these files without leaking SECRET token=sk-test",
                selected_model="account:claude:sonnet",
                selected_mode="ask",
                chosen_tier="L3",
                actual_cost_usd=0.042,
                confidence="actual",
            )
            blob = json.dumps(receipt, sort_keys=True)

        self.assertEqual(receipt["provider"], "claude")
        self.assertEqual(receipt["route_kind"], "paid_account")
        self.assertTrue(receipt["paid"])
        self.assertEqual(receipt["spend_usd"], 0.042)
        self.assertEqual(receipt["spend_confidence"], "actual")
        self.assertEqual(receipt["savings_usd"], 0.0)
        self.assertEqual(receipt["savings_confidence"], "not_applicable")
        self.assertFalse(receipt["paid_call_avoided"])
        self.assertIn("selected paid account model", receipt["reason"])
        self.assertIn("task_hash", receipt)
        self.assertNotIn("review these files", blob)
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("sk-test", blob)

    def test_unknown_cost_paid_account_receipt_uses_estimated_spend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            receipt = build_savings_receipt(
                root,
                task="answer with paid account",
                selected_model="account:codex:gpt-5.4",
                selected_mode="ask",
                chosen_tier="L3",
            )

        self.assertTrue(receipt["paid"])
        self.assertGreater(receipt["spend_usd"], 0.0)
        self.assertEqual(receipt["spend_confidence"], "estimated")
        self.assertEqual(receipt["savings_usd"], 0.0)
        self.assertEqual(receipt["estimated_savings_usd"], 0.0)

    def test_local_route_receipt_shows_estimated_savings_and_paid_call_avoided(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            receipt = build_savings_receipt(
                root,
                task="summarize git status locally",
                selected_model="auto",
                selected_mode="safe-auto",
                chosen_tier="L1",
            )

        self.assertFalse(receipt["paid"])
        self.assertEqual(receipt["route_kind"], "local_or_deterministic")
        self.assertEqual(receipt["spend_usd"], receipt["estimated_actual_usd"])
        self.assertEqual(receipt["savings_usd"], receipt["estimated_savings_usd"])
        self.assertGreater(receipt["savings_usd"], 0.0)
        self.assertEqual(receipt["savings_confidence"], "estimated")
        self.assertTrue(receipt["paid_call_avoided"])


class SavingsTruthPipelineTests(unittest.TestCase):
    def test_paid_gui_message_records_real_spend_and_no_savings_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            result = handle_gui_message(
                root,
                "please answer using my paid account",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(text="done", cost=0.055),
            )
            summary = summarize_ledger(root)
            spent = budget_status(root)["spent"]["today_usd"]
            receipt = result["receipt"]

        self.assertEqual(result["status"], "answered")
        self.assertEqual(receipt["spend_usd"], 0.055)
        self.assertEqual(receipt["savings_usd"], 0.0)
        self.assertFalse(receipt["paid_call_avoided"])
        self.assertEqual(summary["cloud_calls_avoided"], 0)
        self.assertGreater(spent, 0.0)

    def test_gui_once_exposes_latest_spend_savings_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            handle_gui_message(
                root,
                "paid help",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(text="done", cost=0.033),
            )
            summary = run_once(root)
            receipt = summary["last_savings_receipt"]

        self.assertIsInstance(receipt, dict)
        self.assertEqual(receipt["spend_usd"], 0.033)
        self.assertEqual(receipt["savings_usd"], 0.0)
        self.assertIn("receipt_id", receipt)
        self.assertIn("task_hash", receipt)
        self.assertIn("estimated_actual_usd", receipt)

    def test_receipts_and_ledger_do_not_store_raw_prompt_or_secret(self) -> None:
        prompt = "fix checkout flow using SECRET token=sk-abcdef1234567890"
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            result = handle_gui_message(
                root,
                prompt,
                model_id="account:codex:gpt-5.4",
                mode="ask",
                account_runner=FakeAccountRunner(account_id="codex", cost=0.01),
            )
            ledger_blob = (root / ".opaihub" / "ledger" / "usage.jsonl").read_text(
                encoding="utf-8"
            )
            receipt_blob = json.dumps(result["receipt"], sort_keys=True)

        combined = ledger_blob + receipt_blob
        self.assertNotIn("fix checkout flow", combined)
        self.assertNotIn("SECRET", combined)
        self.assertNotIn("sk-abcdef1234567890", combined)


class SavingsTruthProofSurfaceTests(unittest.TestCase):
    def test_dashboard_html_splits_saved_spent_and_latest_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            record_route_decision(root, "local route", model_tier="L1")
            handle_gui_message(
                root,
                "paid help",
                model_id="account:claude:sonnet",
                mode="ask",
                account_runner=FakeAccountRunner(cost=0.025),
            )
            html_path = build_dashboard_html(root)
            html = html_path.read_text(encoding="utf-8")

        self.assertIn("Saved total", html)
        self.assertIn("Spent today", html)
        self.assertIn("Paid calls avoided", html)
        self.assertIn("Context reduced", html)
        self.assertIn("Latest receipt", html)
        self.assertIn("Savings receipt", html)
        self.assertIn("selected paid account model", html)

    def test_savings_markdown_splits_spend_from_savings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            record_route_decision(root, "local route", model_tier="L1")
            record_model_call(
                root,
                "paid account task",
                model_tier="L3",
                provider_type="cloud",
                tokens=6000,
                confirmed=True,
                real_cost_usd=0.042,
            )
            markdown = render_savings_markdown(build_savings_report(root))

        self.assertIn("Spend and savings are separate", markdown)
        self.assertIn("Estimated actual spend", markdown)
        self.assertIn("Estimated savings", markdown)

    def test_proof_markdown_puts_local_benchmark_caveat_near_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            bundle = build_proof_bundle(root, sign=False, allow_benchmark_run=True)
            markdown = render_proof_markdown(bundle)

        self.assertIn("Local OPai benchmark suite result", markdown)
        self.assertIn("not an official SWE-bench", markdown)


if __name__ == "__main__":
    unittest.main()
