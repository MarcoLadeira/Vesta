"""Paid direct-API tier / model-registry consistency (#673).

Mirrors ``test_free_model_registry.py``'s pinning discipline for the new
paid-direct tier: every picker-visible paid model must be registered — with
honest capability metadata — and the paid tier must never overlap the
account or free tiers (a provider counted as both "free" and "paid" would
make its cost meaning ambiguous by construction).
"""

from __future__ import annotations

import unittest

from vesta import model_registry as reg
from vestahub.paid_api_models import PAID_MODEL_SPECS


class PaidProviderRegistrationTests(unittest.TestCase):
    def test_every_paid_spec_provider_is_registered(self):
        for spec in PAID_MODEL_SPECS:
            with self.subTest(provider=spec["provider"]):
                self.assertIn(spec["provider"], reg.paid_direct_providers())
                self.assertIn(spec["provider"], reg._REGISTRY)

    def test_registered_paid_providers_match_paid_specs_exactly(self):
        spec_providers = {spec["provider"] for spec in PAID_MODEL_SPECS}
        self.assertEqual(set(reg.paid_direct_providers()), spec_providers)

    def test_every_paid_spec_is_actually_paid(self):
        # The whole reason this tier exists separately from free_models.py.
        for spec in PAID_MODEL_SPECS:
            with self.subTest(id=spec["id"]):
                self.assertTrue(spec["paid"])

    def test_account_free_and_paid_tiers_are_pairwise_disjoint(self):
        account = set(reg.providers())
        free = set(reg.free_providers())
        paid = set(reg.paid_direct_providers())
        self.assertFalse(account & free)
        self.assertFalse(account & paid)
        self.assertFalse(free & paid, "a provider cannot be both free and paid")


class PaidModelConsistencyTests(unittest.TestCase):
    def test_every_picker_visible_paid_model_is_registered(self):
        for spec in PAID_MODEL_SPECS:
            with self.subTest(id=spec["id"]):
                found = reg.find(spec["provider"], spec["model_id"])
                self.assertIsNotNone(
                    found,
                    f"picker-visible paid model '{spec['id']}' is missing "
                    "from model_registry",
                )
                self.assertEqual(found.id, spec["model_id"])

    def test_picker_id_format_matches_registered_provider_and_model(self):
        for spec in PAID_MODEL_SPECS:
            with self.subTest(id=spec["id"]):
                self.assertEqual(
                    spec["id"], f"paid:{spec['provider']}:{spec['model_id']}"
                )

    def test_no_extra_registered_paid_models_without_operational_spec(self):
        operational = {(s["provider"], s["model_id"]) for s in PAID_MODEL_SPECS}
        for provider in reg.paid_direct_providers():
            for model_spec in reg.models_for(provider):
                with self.subTest(provider=provider, model=model_spec.id):
                    self.assertIn(
                        (provider, model_spec.id),
                        operational,
                        f"registered paid model {provider}/{model_spec.id} has "
                        "no operational spec in vestahub/paid_api_models.py",
                    )

    def test_paid_specs_are_well_formed(self):
        for provider in reg.paid_direct_providers():
            specs = reg.models_for(provider)
            self.assertTrue(specs, f"{provider} has no registered models")
            ids = [spec.id for spec in specs]
            self.assertEqual(len(ids), len(set(ids)))
            for spec in specs:
                self.assertTrue(spec.id and spec.display and spec.full)
                self.assertIn(spec.capability, reg.CAPABILITIES)

    def test_every_paid_model_id_has_a_real_price(self):
        # A registered paid model with no pricing entry would be sellable
        # through the picker but unable to bill honestly.
        from vestahub.deepseek_pricing import PRICES

        for spec in PAID_MODEL_SPECS:
            with self.subTest(id=spec["id"]):
                self.assertIn(spec["model_id"], PRICES)


class PaidValidationPathTests(unittest.TestCase):
    def test_validate_accepts_registered_paid_model(self):
        result = reg.validate("deepseek", "deepseek-v4-flash")
        self.assertTrue(result["valid"])
        self.assertEqual(result["canonical"], "deepseek-v4-flash")

    def test_validate_flags_retired_legacy_alias(self):
        # #673: deepseek-chat/deepseek-reasoner are retired, not silently
        # current — confirmed against official docs at implementation time.
        result = reg.validate("deepseek", "deepseek-chat")
        self.assertFalse(result["valid"])
        self.assertEqual(result["fallback"], "deepseek-v4-flash")

    def test_paid_default_models_are_registered_ids(self):
        for provider in reg.paid_direct_providers():
            with self.subTest(provider=provider):
                default = reg.default_model(provider)
                self.assertIsNotNone(default)
                self.assertIsNotNone(reg.find(provider, default.id))

    def test_deepseek_default_is_the_cheap_flash_model(self):
        # A bare provider="deepseek" lookup must prefer the cheaper model —
        # silently defaulting to Pro would be a real cost surprise.
        default = reg.default_model("deepseek")
        self.assertEqual(default.id, "deepseek-v4-flash")

    def test_account_catalog_stays_account_only(self):
        self.assertEqual(set(reg.catalog()), set(reg.providers()))


if __name__ == "__main__":
    unittest.main()
