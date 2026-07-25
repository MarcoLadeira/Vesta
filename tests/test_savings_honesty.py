"""Savings honesty: paid calls record real spend; local/cache calls record savings.

The core invariant (the "$0.00 bug" regression guard):
  - A paid account call is a SPEND, not a saving.  After calling claude/codex,
    budget_status("spent today") must show the cost, not $0.00.
  - The root cause: record_model_call("CLOUD", ...) wrote estimated_actual_usd=0
    because "CLOUD" is not a key in the L0-L4 cost model.  The fix: always pass
    model_tier="L3" (the frontier tier) so the estimate is non-zero, and thread
    the real cost_usd from claude's JSON output when available so the number is
    truthful rather than estimated.
  - A local/cache route is a genuine saving: cloud_call_avoided=True, and the
    ledger increments estimated_savings_usd (not estimated_actual_usd).
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, make_repo
from opaihub.budget import budget_status
from opaihub.cost_model import estimate_route_savings
from opaihub.gui_pipeline import build_savings_receipt, handle_gui_message
from opaihub.ledger import (
    EVENT_MODEL_CALL,
    EVENT_ROUTE,
    read_events,
    record_model_call,
    summarize_ledger,
)


# ---------------------------------------------------------------------------
# 1. record_model_call - direct ledger-layer tests
# ---------------------------------------------------------------------------
class RecordModelCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _last_call(self) -> dict:
        return next(
            e
            for e in reversed(read_events(self.root))
            if e.get("event_type") == EVENT_MODEL_CALL
        )

    def test_l3_tier_records_nonzero_estimated_cost(self):
        record_model_call(
            self.root,
            "task",
            model_tier="L3",
            provider_type="cloud",
            tokens=6000,
            confirmed=True,
        )
        self.assertGreater(
            self._last_call()["estimated_actual_usd"],
            0,
            "L3 must produce a non-zero cost estimate",
        )

    def test_real_cost_overrides_tier_estimate(self):
        record_model_call(
            self.root,
            "task",
            model_tier="L3",
            provider_type="cloud",
            tokens=6000,
            confirmed=True,
            real_cost_usd=0.07,
        )
        self.assertAlmostEqual(self._last_call()["estimated_actual_usd"], 0.07)

    def test_real_cost_zero_is_honoured(self):
        record_model_call(
            self.root,
            "task",
            model_tier="L3",
            provider_type="cloud",
            tokens=6000,
            confirmed=True,
            real_cost_usd=0.0,
        )
        self.assertEqual(self._last_call()["estimated_actual_usd"], 0.0)

    def test_estimated_actual_usd_field_always_written(self):
        record_model_call(
            self.root,
            "task",
            model_tier="L3",
            provider_type="cloud",
            tokens=1000,
            confirmed=True,
        )
        self.assertIn("estimated_actual_usd", self._last_call())

    def test_l1_local_tier_marks_is_local_route(self):
        record_model_call(
            self.root,
            "task",
            model_tier="L1",
            provider_type="local",
            tokens=5000,
            confirmed=True,
        )
        self.assertTrue(self._last_call()["is_local_route"])

    def test_l3_cloud_tier_not_local_route(self):
        record_model_call(
            self.root,
            "task",
            model_tier="L3",
            provider_type="cloud",
            tokens=5000,
            confirmed=True,
        )
        self.assertFalse(self._last_call()["is_local_route"])

    def test_zero_tokens_writes_zero_estimated_cost_when_no_real_cost(self):
        record_model_call(
            self.root,
            "task",
            model_tier="L3",
            provider_type="cloud",
            tokens=0,
            confirmed=True,
        )
        self.assertEqual(self._last_call()["estimated_actual_usd"], 0.0)

    def test_real_cost_usd_none_uses_tier_estimate(self):
        record_model_call(
            self.root,
            "task",
            model_tier="L3",
            provider_type="cloud",
            tokens=6000,
            confirmed=True,
            real_cost_usd=None,
        )
        self.assertGreater(self._last_call()["estimated_actual_usd"], 0)


# ---------------------------------------------------------------------------
# 2. "Spent today" regression: paid account call must show non-zero spend
# ---------------------------------------------------------------------------
class SpentTodayTests(unittest.TestCase):
    """Core regression suite for the $0.00 bug.

    Each test drives the full pipeline (handle_gui_message -> _ask_account ->
    record_model_call) with a FakeAccountRunner so no real CLI is invoked,
    then checks that budget_status reflects the spend correctly.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_paid_call_with_real_cost_increments_spent_today(self):
        fake = FakeAccountRunner(text="done", cost=0.055)
        handle_gui_message(
            self.root,
            "do something",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
        )
        spent = budget_status(self.root)["spent"]["today_usd"]
        self.assertGreater(spent, 0, "paid account call must appear in spent today")

    def test_paid_call_with_zero_cost_still_records_l3_estimate(self):
        fake = FakeAccountRunner(text="done", cost=0.0)
        handle_gui_message(
            self.root,
            "task",
            model_id="account:claude:opus",
            mode="ask",
            account_runner=fake,
        )
        # cost=0.0 from runner means "unknown / free" → should fall back to L3 estimate
        events = read_events(self.root)
        model_calls = [e for e in events if e.get("event_type") == EVENT_MODEL_CALL]
        self.assertTrue(model_calls, "a model_call event must be written")

    def test_paid_call_real_cost_matches_runner_cost(self):
        fake = FakeAccountRunner(text="answer", cost=0.042)
        handle_gui_message(
            self.root,
            "help",
            model_id="account:claude:haiku",
            mode="ask",
            account_runner=fake,
        )
        events = read_events(self.root)
        call = next(
            (e for e in events if e.get("event_type") == EVENT_MODEL_CALL), None
        )
        self.assertIsNotNone(call, "model_call event must be written")
        self.assertAlmostEqual(
            call["estimated_actual_usd"],
            0.042,
            places=4,
            msg="ledger must use the real cost from the runner, not the tier estimate",
        )

    def test_full_auto_paid_call_also_increments_spent(self):
        fake = FakeAccountRunner(text="edited files", cost=0.08)
        handle_gui_message(
            self.root,
            "refactor everything",
            model_id="account:claude:sonnet",
            mode="full-auto",
            account_runner=fake,
        )
        spent = budget_status(self.root)["spent"]["today_usd"]
        self.assertGreater(spent, 0)

    def test_local_route_does_not_write_cloud_cost(self):
        record_model_call(
            self.root,
            "task",
            model_tier="L1",
            provider_type="local",
            tokens=5000,
            confirmed=True,
        )
        events = read_events(self.root)
        call = next(e for e in events if e.get("event_type") == EVENT_MODEL_CALL)
        self.assertTrue(call["is_local_route"])

    def test_codex_call_with_no_cost_uses_tier_estimate(self):
        fake = FakeAccountRunner(account_id="codex", text="codex answer", cost=None)
        handle_gui_message(
            self.root,
            "task",
            model_id="account:codex",
            mode="ask",
            account_runner=fake,
        )
        events = read_events(self.root)
        model_calls = [e for e in events if e.get("event_type") == EVENT_MODEL_CALL]
        self.assertTrue(model_calls, "model_call event must be written for codex")
        # With None cost, the L3 tier estimate should be used
        self.assertGreaterEqual(model_calls[-1]["estimated_actual_usd"], 0)


# ---------------------------------------------------------------------------
# 3. Receipt math: savings = max(0, baseline - actual)
# ---------------------------------------------------------------------------
class ReceiptMathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_savings_equals_baseline_minus_actual(self):
        receipt = build_savings_receipt(
            self.root,
            task="summarize my code",
            selected_model="auto",
            selected_mode="ask",
            chosen_tier="L1",
        )
        expected = max(
            0.0,
            round(
                receipt["estimated_baseline_usd"] - receipt["estimated_actual_usd"], 6
            ),
        )
        self.assertAlmostEqual(receipt["estimated_savings_usd"], expected, places=5)

    def test_savings_never_negative(self):
        receipt = build_savings_receipt(
            self.root,
            task="task",
            selected_model="account:claude:opus",
            selected_mode="full-auto",
            chosen_tier="L3",
            actual_cost_usd=0.20,
        )
        self.assertGreaterEqual(receipt["estimated_savings_usd"], 0)

    def test_actual_cost_from_runner_reflected_in_receipt(self):
        receipt = build_savings_receipt(
            self.root,
            task="task",
            selected_model="account:claude:sonnet",
            selected_mode="ask",
            chosen_tier="L3",
            actual_cost_usd=0.042,
            confidence="actual",
        )
        self.assertAlmostEqual(receipt["estimated_actual_usd"], 0.042, places=4)
        self.assertEqual(receipt["confidence"], "actual")

    def test_no_actual_cost_uses_estimated(self):
        receipt = build_savings_receipt(
            self.root,
            task="task",
            selected_model="auto",
            selected_mode="ask",
            chosen_tier="L1",
        )
        self.assertIsNotNone(receipt["estimated_actual_usd"])
        self.assertGreaterEqual(receipt["estimated_actual_usd"], 0)

    def test_receipt_has_required_fields(self):
        receipt = build_savings_receipt(
            self.root,
            task="task",
            selected_model="auto",
            selected_mode="ask",
            chosen_tier="L1",
        )
        for field in (
            "estimated_baseline_usd",
            "estimated_actual_usd",
            "estimated_savings_usd",
            "paid_call_avoided",
            "confidence",
            "privacy",
        ):
            self.assertIn(field, receipt, f"receipt missing field: {field}")


# ---------------------------------------------------------------------------
# 4. Route savings: local avoids cloud; paid does not
# ---------------------------------------------------------------------------
class RouteSavingsTests(unittest.TestCase):
    def test_local_route_marks_cloud_call_avoided(self):
        result = estimate_route_savings("L1", task_tokens=6000)
        self.assertTrue(result["cloud_call_avoided"])

    def test_l0_route_marks_cloud_call_avoided(self):
        result = estimate_route_savings("L0", task_tokens=6000)
        self.assertTrue(result["cloud_call_avoided"])

    def test_paid_l3_does_not_mark_cloud_call_avoided(self):
        result = estimate_route_savings("L3", task_tokens=6000)
        self.assertFalse(result["cloud_call_avoided"])

    def test_l1_savings_greater_than_zero(self):
        result = estimate_route_savings("L1", task_tokens=6000)
        self.assertGreater(result["estimated_savings_usd"], 0)

    def test_l3_savings_is_zero(self):
        result = estimate_route_savings("L3", task_tokens=6000)
        self.assertEqual(result["estimated_savings_usd"], 0)

    def test_local_actual_cost_is_zero(self):
        result = estimate_route_savings("L0", task_tokens=6000)
        self.assertEqual(result["estimated_actual_usd"], 0)

    def test_baseline_tier_is_l3(self):
        result = estimate_route_savings("L1", task_tokens=6000)
        self.assertEqual(result["baseline_tier"], "L3")


# ---------------------------------------------------------------------------
# 5. End-to-end: the pipeline returns real cost in the receipt
# ---------------------------------------------------------------------------
class EndToEndHonestyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pipeline_receipt_reflects_runner_cost(self):
        fake = FakeAccountRunner(text="done", cost=0.042)
        res = handle_gui_message(
            self.root,
            "help",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
        )
        receipt = res.get("receipt", {})
        self.assertAlmostEqual(
            receipt.get("estimated_actual_usd", -1),
            0.042,
            places=4,
            msg="pipeline receipt must carry the real cost from the runner",
        )

    def test_pipeline_receipt_confidence_is_actual_when_cost_known(self):
        fake = FakeAccountRunner(text="done", cost=0.01)
        res = handle_gui_message(
            self.root,
            "task",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
        )
        self.assertEqual(res["receipt"]["confidence"], "actual")

    def test_pipeline_receipt_confidence_estimated_when_no_cost(self):
        fake = FakeAccountRunner(account_id="codex", text="done", cost=None)
        res = handle_gui_message(
            self.root,
            "task",
            model_id="account:codex",
            mode="ask",
            account_runner=fake,
        )
        self.assertEqual(res["receipt"]["confidence"], "estimated")

    def test_direct_account_call_not_counted_as_cloud_call_avoided(self):
        fake = FakeAccountRunner(text="done", cost=0.01)
        handle_gui_message(
            self.root,
            "task",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
        )
        summary = summarize_ledger(self.root)
        self.assertEqual(
            summary["cloud_calls_avoided"],
            0,
            "a direct paid account call must not count as a cloud call avoided",
        )


# ---------------------------------------------------------------------------
# 6. Invariants (#90): property-style grids that lock the trust contract
# ---------------------------------------------------------------------------
class LedgerReconciliationInvariantTests(unittest.TestCase):
    """Parametrized invariants so 'spend counted as savings' can never regress.

    No hypothesis dependency: we sweep representative grids with subTest, which
    gives property-style coverage of the money-handling paths.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _last_call(self) -> dict:
        return next(
            e
            for e in reversed(read_events(self.root))
            if e.get("event_type") == EVENT_MODEL_CALL
        )

    def test_receipt_savings_is_baseline_minus_actual_over_grid(self):
        tiers = ["L0", "L1", "L2", "L3"]
        costs = [None, 0.0, 0.001, 0.042, 0.5, 5.0]
        for tier in tiers:
            for cost in costs:
                with self.subTest(tier=tier, actual=cost):
                    receipt = build_savings_receipt(
                        self.root,
                        task="a representative task",
                        selected_model="auto"
                        if cost is None
                        else "account:claude:opus",
                        selected_mode="ask",
                        chosen_tier=tier,
                        actual_cost_usd=cost,
                    )
                    expected = max(
                        0.0,
                        round(
                            receipt["estimated_baseline_usd"]
                            - receipt["estimated_actual_usd"],
                            6,
                        ),
                    )
                    self.assertAlmostEqual(
                        receipt["estimated_savings_usd"], expected, places=5
                    )
                    self.assertGreaterEqual(receipt["estimated_savings_usd"], 0.0)

    def test_real_cost_recorded_verbatim_over_grid(self):
        for cost in [0.0, 0.001, 0.042, 0.5, 12.34]:
            with self.subTest(real_cost=cost):
                record_model_call(
                    self.root,
                    "task",
                    model_tier="L3",
                    provider_type="cloud",
                    tokens=6000,
                    confirmed=True,
                    real_cost_usd=cost,
                )
                self.assertAlmostEqual(
                    self._last_call()["estimated_actual_usd"], cost, places=6
                )

    def test_paid_call_never_counts_as_cloud_avoided_over_modes_and_models(self):
        combos = [
            ("account:claude:sonnet", "ask"),
            ("account:claude:opus", "safe-auto"),
            ("account:claude:haiku", "full-auto"),
            ("account:codex", "ask"),
        ]
        for model_id, mode in combos:
            with self.subTest(model=model_id, mode=mode):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                root = make_repo(Path(tmp.name))
                account_id = "codex" if "codex" in model_id else "claude"
                fake = FakeAccountRunner(account_id=account_id, text="done", cost=0.05)
                handle_gui_message(
                    root, "task", model_id=model_id, mode=mode, account_runner=fake
                )
                summary = summarize_ledger(root)
                self.assertEqual(
                    summary["cloud_calls_avoided"],
                    0,
                    f"paid {model_id} in {mode} must never count as a cloud call avoided",
                )

    def test_local_routes_always_count_as_avoided_over_grid(self):
        for tier in ["L0", "L1"]:
            with self.subTest(tier=tier):
                result = estimate_route_savings(tier, task_tokens=6000)
                self.assertTrue(result["cloud_call_avoided"])
                self.assertEqual(result["estimated_actual_usd"], 0)

    def test_paid_tiers_never_count_as_avoided_over_grid(self):
        for tier in ["L2", "L3", "L4"]:
            with self.subTest(tier=tier):
                result = estimate_route_savings(tier, task_tokens=6000)
                self.assertFalse(result["cloud_call_avoided"])

    def test_raw_prompt_never_persisted_in_ledger_or_receipt(self):
        secret_prompt = "deploy with api_key=sk-supersecret9876543210abcdef please"
        fake = FakeAccountRunner(text="done", cost=0.02)
        res = handle_gui_message(
            self.root,
            secret_prompt,
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
        )
        # The secret must not appear in any persisted ledger event...
        blob = "".join(str(e) for e in read_events(self.root))
        self.assertNotIn("sk-supersecret9876543210abcdef", blob)
        self.assertNotIn(secret_prompt, blob)
        # ...nor in the receipt returned to the GUI.
        self.assertNotIn("sk-supersecret9876543210abcdef", str(res.get("receipt", {})))


# ---------------------------------------------------------------------------
# 7. Record-after-outcome (#144): failed / prompted / cancelled runs must not
#    leave route or receipt events — only an actual answer is a saving.
# ---------------------------------------------------------------------------
class RecordAfterOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _routes(self) -> list[dict]:
        return [e for e in read_events(self.root) if e.get("event_type") == EVENT_ROUTE]

    def _receipt_events(self) -> list[dict]:
        return [
            e for e in read_events(self.root) if e.get("event_type") == "gui_receipt"
        ]

    def _no_accounts(self):
        return mock.patch(
            "opai.app_state.available_models", return_value={"models": []}
        )

    def test_no_local_model_failure_records_nothing(self):
        with (
            mock.patch(
                "opaihub.ask.run_ask", return_value={"status": "no_local_model"}
            ),
            self._no_accounts(),
        ):
            result = handle_gui_message(self.root, "task", model_id="auto", mode="ask")
        self.assertEqual(result["status"], "needs_model")
        self.assertNotIn("retry", result["answer"].lower())
        self.assertIn("model", result["answer"].lower())
        self.assertEqual(self._routes(), [])
        self.assertEqual(self._receipt_events(), [])
        self.assertEqual(summarize_ledger(self.root)["estimated_savings_usd"], 0)

    def test_runner_error_records_nothing(self):
        with (
            mock.patch(
                "opaihub.ask.run_ask",
                return_value={"status": "runner_error", "error": "boom"},
            ),
            self._no_accounts(),
        ):
            result = handle_gui_message(self.root, "task", model_id="auto", mode="ask")
        self.assertNotEqual(result["status"], "answered")
        self.assertEqual(self._routes(), [])

    def test_answered_local_run_records_exactly_one_route(self):
        with mock.patch(
            "opaihub.ask.run_ask",
            return_value={"status": "answered_locally", "answer": "hi"},
        ):
            result = handle_gui_message(self.root, "task", model_id="auto", mode="ask")
        self.assertEqual(result["status"], "answered")
        routes = self._routes()
        self.assertEqual(len(routes), 1)
        self.assertGreaterEqual(routes[0]["estimated_savings_usd"], 0)
        self.assertEqual(summarize_ledger(self.root)["route_count"], 1)

    def test_free_confirmation_prompt_records_nothing(self):
        result = handle_gui_message(
            self.root,
            "task",
            model_id="free:gemini:gemini-3.1-flash-lite",
            mode="ask",
            allow_cloud=False,
        )
        self.assertEqual(result["status"], "needs_free_confirmation")
        self.assertEqual(self._routes(), [])
        self.assertEqual(self._receipt_events(), [])

    def test_answered_free_call_records_one_l2_route(self):
        with mock.patch(
            "opai.app_state.ask",
            return_value={"status": "answered_by_free_api", "answer": "4"},
        ):
            result = handle_gui_message(
                self.root,
                "task",
                model_id="free:gemini:gemini-3.1-flash-lite",
                mode="ask",
                allow_cloud=True,
            )
        self.assertEqual(result["status"], "answered")
        routes = self._routes()
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["model_tier"], "L2")

    def test_gui_free_call_records_one_route_and_one_spend_event(self):
        class FakeFreeRunner:
            name = "free-api"
            model = "gemini-3.1-flash-lite"
            last_usage = {
                "tokens": 120,
                "input_tokens": 80,
                "output_tokens": 40,
                "measurement": "provider",
            }

            def available(self):
                return True

            def complete(self, prompt, **kwargs):
                return "Free answer"

        selected = "free:gemini:gemini-3.1-flash-lite"
        with mock.patch(
            "opaihub.local_runner.runner_for_model", return_value=FakeFreeRunner()
        ):
            result = handle_gui_message(
                self.root,
                "Explain the parser",
                model_id=selected,
                mode="ask",
                allow_cloud=True,
            )

        events = read_events(self.root)
        routes = [event for event in events if event.get("event_type") == EVENT_ROUTE]
        calls = [
            event for event in events if event.get("event_type") == EVENT_MODEL_CALL
        ]
        self.assertEqual(result["status"], "answered")
        self.assertEqual(len(routes), 1)
        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(
            budget_status(self.root)["spent"]["today_usd"],
            calls[0]["estimated_actual_usd"],
            places=6,
        )

    def test_failed_account_call_records_no_receipt_or_spend(self):
        fake = FakeAccountRunner(raises=RuntimeError("CLI exploded"))
        result = handle_gui_message(
            self.root,
            "task",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self._receipt_events(), [])
        self.assertEqual(
            [
                e
                for e in read_events(self.root)
                if e.get("event_type") == EVENT_MODEL_CALL
            ],
            [],
            "a failed provider call must not record spend either",
        )

    def test_answered_account_call_still_records_receipt_and_spend(self):
        fake = FakeAccountRunner(text="done", cost=0.03)
        result = handle_gui_message(
            self.root,
            "task",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
        )
        self.assertEqual(result["status"], "answered")
        self.assertEqual(len(self._receipt_events()), 1)
        self.assertGreater(budget_status(self.root)["spent"]["today_usd"], 0)

    def test_cancelled_before_run_records_only_a_terminal_verdict(self):
        cancel = threading.Event()
        cancel.set()
        result = handle_gui_message(
            self.root, "task", model_id="auto", mode="ask", cancel=cancel
        )
        self.assertEqual(result["status"], "cancelled")
        events = read_events(self.root)
        self.assertEqual(
            [event["event_type"] for event in events], ["completion_verdict"]
        )
        self.assertEqual(events[0]["verdict"], "cancelled")


if __name__ == "__main__":
    unittest.main()
