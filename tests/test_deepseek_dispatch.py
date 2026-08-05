"""ask() -> _ask_direct_api_model() dispatch for the paid tier (#673).

Mirrors tests/test_free_models.py's AskFreeModelTests for the free path;
the key new behavior under test is that a paid call gets a real, non-zero,
non-tier-fallback cost recorded — the whole reason #673's paid tier exists
as a module separate from the free one.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock as mock
from pathlib import Path


class AskDeepSeekTests(unittest.TestCase):
    def test_ask_paid_requires_confirmation_with_billing_language(self):
        from opai.app_state import ask

        with mock.patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "sk-test"},  # pragma: allowlist secret
        ):
            result = ask(
                Path("/tmp"),
                "What is 2+2?",
                model_choice="paid:deepseek:deepseek-v4-flash",
                allow_cloud=False,
            )
        self.assertEqual(result["status"], "confirmation_required")
        self.assertEqual(result["model_id"], "paid:deepseek:deepseek-v4-flash")
        # #673 "product truth": a known-paid provider must not get the
        # hedged free-tier "may apply" wording — it is stated as a fact.
        self.assertIn("bills per token", result["message"])
        self.assertNotIn("may apply", result["message"])

    def test_free_confirmation_wording_is_unchanged(self):
        # Regression guard for the shared function's other branch.
        from opai.app_state import ask

        with mock.patch.dict(
            os.environ,
            {"GOOGLE_API_KEY": "sk-test"},  # pragma: allowlist secret
        ):
            result = ask(
                Path("/tmp"),
                "What is 2+2?",
                model_choice="free:gemini:gemini-3.1-flash-lite",
                allow_cloud=False,
            )
        self.assertIn("quota or billing", result["message"])
        self.assertNotIn("bills per token", result["message"])

    def test_ask_paid_dispatches_and_labels_status_correctly(self):
        from opai.app_state import ask

        fake_result = {"status": "answered_locally", "answer": "4", "source": "x"}
        with (
            mock.patch(
                "opaihub.ask.run_explicit_model", return_value=fake_result
            ) as run_explicit,
            mock.patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "sk-test"},  # pragma: allowlist secret
            ),
        ):
            result = ask(
                Path("/tmp"),
                "What is 2+2?",
                model_choice="paid:deepseek:deepseek-v4-flash",
                allow_cloud=True,
            )
        # Never "free" for a call that cost real money (#673 product truth).
        self.assertEqual(result["status"], "answered_by_paid_api")
        self.assertEqual(result["source"], "paid_api")
        self.assertFalse(result["free_tier"])
        self.assertEqual(
            run_explicit.call_args.kwargs["selected_model_id"],
            "paid:deepseek:deepseek-v4-flash",
        )
        self.assertEqual(run_explicit.call_args.kwargs["provider_id"], "deepseek")

    def test_free_dispatch_status_labelling_is_unchanged(self):
        # Regression guard: the free branch's free_tier flag must stay True.
        from opai.app_state import ask

        fake_result = {"status": "answered_locally", "answer": "4", "source": "x"}
        with (
            mock.patch("opaihub.ask.run_explicit_model", return_value=fake_result),
            mock.patch.dict(
                os.environ,
                {"GOOGLE_API_KEY": "sk-test"},  # pragma: allowlist secret
            ),
        ):
            result = ask(
                Path("/tmp"),
                "What is 2+2?",
                model_choice="free:gemini:gemini-3.1-flash-lite",
                allow_cloud=True,
            )
        self.assertEqual(result["status"], "answered_by_free_api")
        self.assertEqual(result["source"], "free_api")
        self.assertTrue(result["free_tier"])

    def test_a_paid_answer_records_a_real_nonzero_cost_not_a_tier_fallback(self):
        """The actual point of #673's paid tier: record_model_call must see
        a real dollar figure computed from real usage, never fall through to
        the L2 tier rate (calibrated for genuinely-free providers, so it
        would silently under-report a real DeepSeek charge)."""
        from opai.app_state import ask
        from opaihub.local_runner import PaidAPIRunner

        runner = PaidAPIRunner(
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            "sk-test",
            pricing_model_id="deepseek-v4-flash",
        )
        runner.last_usage = {
            "input_tokens": 100_000,
            "output_tokens": 50_000,
            "tokens": 150_000,
            "measurement": "provider",
        }
        fake_result = {
            "status": "answered_locally",
            "answer": "Some real answer.",
            "source": "x",
            # No ledger_recorded_per_turn: exercises the legacy aggregate
            # recording path this test is actually about.
        }
        recorded: dict = {}

        def _capture_record_model_call(*_args, **kwargs):
            recorded.update(kwargs)
            return {}

        with (
            mock.patch("opaihub.local_runner.runner_for_model", return_value=runner),
            mock.patch("opaihub.ask.run_explicit_model", return_value=fake_result),
            mock.patch(
                "opaihub.ledger.record_model_call",
                side_effect=_capture_record_model_call,
            ),
            mock.patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "sk-test"},  # pragma: allowlist secret
            ),
        ):
            ask(
                Path("/tmp"),
                "A real coding task",
                model_choice="paid:deepseek:deepseek-v4-flash",
                allow_cloud=True,
            )

        self.assertIn("real_cost_usd", recorded)
        self.assertIsNotNone(recorded["real_cost_usd"])
        self.assertGreater(recorded["real_cost_usd"], 0.0)
        self.assertEqual(recorded["provider_type"], "paid_api")
        # Sanity bound: at deepseek-v4-flash's real per-token rate, 100k
        # input + 50k output tokens is a few cents, not a tier-rate accident
        # that happens to be nonzero for an unrelated reason.
        self.assertLess(recorded["real_cost_usd"], 1.0)

    def test_a_free_answer_still_passes_no_real_cost_unchanged(self):
        """Regression guard: the free path's record_model_call call must not
        gain a real_cost_usd it never had — None still means "use the L2
        tier fallback", which is correctly $0-ish for a genuinely free call.
        """
        from opai.app_state import ask
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "model", "key")
        runner.last_usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "tokens": 150,
            "measurement": "provider",
        }
        fake_result = {"status": "answered_locally", "answer": "ok", "source": "x"}
        recorded: dict = {}

        def _capture_record_model_call(*_args, **kwargs):
            recorded.update(kwargs)
            return {}

        with (
            mock.patch("opaihub.local_runner.runner_for_model", return_value=runner),
            mock.patch("opaihub.ask.run_explicit_model", return_value=fake_result),
            mock.patch(
                "opaihub.ledger.record_model_call",
                side_effect=_capture_record_model_call,
            ),
            mock.patch.dict(
                os.environ,
                {"GROQ_API_KEY": "sk-test"},  # pragma: allowlist secret
            ),
        ):
            ask(
                Path("/tmp"),
                "Explain this",
                model_choice="free:groq:openai/gpt-oss-120b",
                allow_cloud=True,
            )

        self.assertIsNone(recorded.get("real_cost_usd"))
        self.assertEqual(recorded["provider_type"], "free_api")


if __name__ == "__main__":
    unittest.main()
