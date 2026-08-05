"""DeepSeek provider integration (#673 Phase 0+1: registration, credentials,
real cost accounting, dispatch routing). Hermetic — no network, no real key.

Explicitly NOT covered here (see PR body / follow-up issues): thinking-mode
``reasoning_content`` continuity (A4), Cursor catalogue parity (workstream
B), OPai Auto shadow routing (D1), the 120-scenario bench (#657-adjacent).
"""

from __future__ import annotations

import unittest
import unittest.mock as mock

from opaihub import deepseek_pricing
from opaihub.credentials import CredentialStore, PROVIDER_ENV
from opaihub.local_runner import FreeAPIRunner, PaidAPIRunner, runner_for_model
from opaihub.paid_api_models import list_paid_api_models, spec_for_model_id
from opaihub.provider_adapters import FREE_PROVIDERS, PAID_DIRECT_API_PROVIDERS
from opaihub.provider_adapters import (
    test_free_provider_connection as check_direct_provider_connection,
)
from opaihub.provider_catalog import provider_ids, provider_record


class CredentialTests(unittest.TestCase):
    def test_deepseek_env_key_is_registered(self):
        self.assertEqual(PROVIDER_ENV["deepseek"], "DEEPSEEK_API_KEY")

    def test_env_var_precedence_over_keychain(self):
        store = CredentialStore(backend=None, environ={"DEEPSEEK_API_KEY": "from-env"})
        self.assertEqual(store.get("deepseek"), "from-env")
        self.assertEqual(store.status("deepseek")["source"], "environment")

    def test_unconfigured_deepseek_reports_not_configured(self):
        store = CredentialStore(backend=None, environ={})
        self.assertIsNone(store.get("deepseek"))
        self.assertFalse(store.status("deepseek")["configured"])


class PricingTests(unittest.TestCase):
    """Real, sourced numbers — see deepseek_pricing.SOURCE/OBSERVED_AT."""

    def test_known_model_prices_are_positive_and_pro_costs_more_than_flash(self):
        flash_cost, _ = deepseek_pricing.estimate_cost_usd(
            "deepseek-v4-flash", input_tokens=1_000_000, output_tokens=1_000_000
        )
        pro_cost, _ = deepseek_pricing.estimate_cost_usd(
            "deepseek-v4-pro", input_tokens=1_000_000, output_tokens=1_000_000
        )
        self.assertGreater(flash_cost, 0)
        self.assertGreater(pro_cost, flash_cost)

    def test_unknown_model_returns_none_not_zero(self):
        cost, measurement = deepseek_pricing.estimate_cost_usd(
            "deepseek-v3", input_tokens=100, output_tokens=100
        )
        self.assertIsNone(cost)
        self.assertEqual(measurement, "unavailable")

    def test_zero_usage_is_a_real_zero_not_unavailable(self):
        cost, measurement = deepseek_pricing.estimate_cost_usd(
            "deepseek-v4-flash", input_tokens=0, output_tokens=0
        )
        self.assertEqual(cost, 0.0)
        self.assertEqual(measurement, "estimated")

    def test_cache_miss_only_estimate_never_understates(self):
        # No cache_hit_tokens given: every input token priced at the higher
        # cache-miss rate, so this can only ever over-, never under-, charge.
        cost, measurement = deepseek_pricing.estimate_cost_usd(
            "deepseek-v4-flash", input_tokens=1000, output_tokens=0
        )
        price = deepseek_pricing.PRICES["deepseek-v4-flash"]
        self.assertAlmostEqual(cost, 1000 * price.input_cache_miss)
        self.assertEqual(measurement, "estimated")

    def test_known_cache_split_prices_each_portion_at_its_own_rate(self):
        cost, measurement = deepseek_pricing.estimate_cost_usd(
            "deepseek-v4-flash",
            input_tokens=1000,
            output_tokens=0,
            cache_hit_tokens=400,
        )
        price = deepseek_pricing.PRICES["deepseek-v4-flash"]
        expected = 400 * price.input_cache_hit + 600 * price.input_cache_miss
        self.assertAlmostEqual(cost, expected)
        self.assertEqual(measurement, "derived")
        # And a real split must always undercut the pessimistic all-miss estimate.
        miss_only, _ = deepseek_pricing.estimate_cost_usd(
            "deepseek-v4-flash", input_tokens=1000, output_tokens=0
        )
        self.assertLess(cost, miss_only)

    def test_stale_snapshot_still_returns_a_real_number_labelled_stale(self):
        cost, measurement = deepseek_pricing.estimate_cost_usd(
            "deepseek-v4-flash", input_tokens=1000, output_tokens=1000
        )
        self.assertIsNotNone(cost)  # sanity: fresh snapshot is a real number
        with mock.patch("opaihub.deepseek_pricing.is_expired", lambda **_: True):
            stale_cost, stale_measurement = deepseek_pricing.estimate_cost_usd(
                "deepseek-v4-flash", input_tokens=1000, output_tokens=1000
            )
        self.assertEqual(stale_cost, cost)  # same arithmetic, not zeroed out
        self.assertEqual(stale_measurement, "estimated_stale")
        self.assertNotEqual(measurement, stale_measurement)


class PaidApiModelSpecTests(unittest.TestCase):
    def test_both_v4_models_are_registered(self):
        ids = {s["id"] for s in list_paid_api_models()}
        self.assertIn("paid:deepseek:deepseek-v4-flash", ids)
        self.assertIn("paid:deepseek:deepseek-v4-pro", ids)

    def test_every_entry_is_marked_paid(self):
        for entry in list_paid_api_models():
            with self.subTest(id=entry["id"]):
                self.assertTrue(entry["paid"])

    def test_unconfigured_model_is_unavailable_with_setup_hint(self):
        with mock.patch(
            "opaihub.paid_api_models.CredentialStore",
            lambda: CredentialStore(backend=None, environ={}),
        ):
            entries = list_paid_api_models()
        flash = next(e for e in entries if e["model"] == "deepseek-v4-flash")
        self.assertFalse(flash["available"])
        self.assertIn("DEEPSEEK_API_KEY", flash["disabled_reason"])

    def test_configured_model_is_available(self):
        with mock.patch(
            "opaihub.paid_api_models.CredentialStore",
            lambda: CredentialStore(backend=None, environ={"DEEPSEEK_API_KEY": "k"}),
        ):
            entries = list_paid_api_models()
        flash = next(e for e in entries if e["model"] == "deepseek-v4-flash")
        self.assertTrue(flash["available"])
        self.assertIsNone(flash["disabled_reason"])

    def test_spec_for_model_id_round_trips(self):
        spec = spec_for_model_id("paid:deepseek:deepseek-v4-flash")
        self.assertEqual(spec["model_id"], "deepseek-v4-flash")
        self.assertIsNone(spec_for_model_id("paid:deepseek:nonexistent"))
        self.assertIsNone(spec_for_model_id(""))


class PaidApiRunnerTests(unittest.TestCase):
    def test_free_runner_cost_is_always_zero_and_actual(self):
        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "m", "key")
        cost, measurement = runner._cost_for(
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        )
        self.assertEqual(cost, 0.0)
        self.assertEqual(measurement, "actual")

    def test_paid_runner_computes_real_cost_from_usage(self):
        runner = PaidAPIRunner(
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            "key",
            pricing_model_id="deepseek-v4-flash",
        )
        cost, measurement = runner._cost_for(
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        )
        self.assertGreater(cost, 0.0)
        self.assertIn(measurement, {"estimated", "derived", "estimated_stale"})

    def test_paid_runner_name_never_reads_free(self):
        # Truth in telemetry: a paid call must never be labelled "free-api".
        runner = PaidAPIRunner(
            "https://api.deepseek.com", "m", "key", pricing_model_id="m"
        )
        self.assertNotEqual(runner.name, FreeAPIRunner.name)

    def test_unknown_pricing_model_id_is_unresolved_not_zero(self):
        runner = PaidAPIRunner(
            "https://api.deepseek.com",
            "some-future-model",
            "key",
            pricing_model_id="some-future-model",
        )
        cost, measurement = runner._cost_for(
            {"input_tokens": 100, "output_tokens": 100}
        )
        self.assertIsNone(cost)
        self.assertEqual(measurement, "unknown")

    def test_paid_runner_rejects_non_https(self):
        with self.assertRaises(ValueError):
            PaidAPIRunner("http://api.deepseek.com", "m", "key", pricing_model_id="m")


class RunnerFactoryTests(unittest.TestCase):
    def test_paid_prefix_builds_a_paid_api_runner(self):
        with mock.patch(
            "opaihub.credentials.CredentialStore.get", return_value="fake-key"
        ):
            runner = runner_for_model("paid:deepseek:deepseek-v4-flash", None)
        self.assertIsInstance(runner, PaidAPIRunner)
        self.assertEqual(runner.model, "deepseek-v4-flash")
        self.assertTrue(runner.available())

    def test_paid_prefix_unknown_model_returns_none(self):
        runner = runner_for_model("paid:deepseek:nonexistent", None)
        self.assertIsNone(runner)

    def test_free_prefix_still_builds_a_free_api_runner_unchanged(self):
        # Regression guard: adding the paid branch must not touch free:'s path.
        with mock.patch(
            "opaihub.credentials.CredentialStore.get", return_value="fake-key"
        ):
            runner = runner_for_model("free:groq:openai/gpt-oss-120b", None)
        self.assertIsInstance(runner, FreeAPIRunner)
        self.assertNotIsInstance(runner, PaidAPIRunner)


class ProviderClassificationTests(unittest.TestCase):
    """The concrete bug found while wiring this in: the catalog's
    requirements-shape classifier cannot tell free from paid (identical
    requirements), so deepseek silently landed in FREE_PROVIDERS and crashed
    test_free_provider_connection's `next()` over FREE_MODEL_SPECS."""

    def test_deepseek_is_not_classified_as_free(self):
        self.assertNotIn("deepseek", FREE_PROVIDERS)

    def test_deepseek_is_classified_as_paid_direct(self):
        self.assertIn("deepseek", PAID_DIRECT_API_PROVIDERS)

    def test_existing_free_providers_are_unaffected(self):
        for provider in ("groq", "mistral", "kimi", "gemini"):
            with self.subTest(provider=provider):
                self.assertIn(provider, FREE_PROVIDERS)
                self.assertNotIn(provider, PAID_DIRECT_API_PROVIDERS)

    def test_connection_test_does_not_crash_for_deepseek(self):
        store = CredentialStore(backend=None, environ={"DEEPSEEK_API_KEY": "fake-key"})

        class _FakeResponse:
            status = 200

            def read(self, _n):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        result = check_direct_provider_connection(
            "deepseek", store=store, opener=lambda *_a, **_k: _FakeResponse()
        )
        self.assertTrue(result["connected"])

    def test_connection_test_reports_missing_key_without_crashing(self):
        store = CredentialStore(backend=None, environ={})
        result = check_direct_provider_connection("deepseek", store=store)
        self.assertFalse(result["connected"])
        self.assertFalse(result["configured"])


class CatalogTests(unittest.TestCase):
    def test_deepseek_is_registered(self):
        self.assertIn("deepseek", provider_ids())

    def test_deepseek_record_is_honest_about_phase_1_scope(self):
        record = provider_record("deepseek")
        # Real capabilities of the shipped Phase-1 runner (inherited
        # complete_with_tools / _chat), not aspirational.
        self.assertEqual(record["capabilities"]["chat"], "supported")
        self.assertEqual(record["capabilities"]["tool_calling"], "supported")
        self.assertEqual(record["capabilities"]["streaming"], "supported")

    def test_deepseek_pricing_is_never_routing_eligible_yet(self):
        # Matches every other record: the catalog's price_usd is
        # provider-level and cannot represent DeepSeek's two models at three
        # rates each — real numbers live in deepseek_pricing.py instead.
        record = provider_record("deepseek")
        self.assertFalse(record["pricing"]["routing_eligible"])


if __name__ == "__main__":
    unittest.main()
