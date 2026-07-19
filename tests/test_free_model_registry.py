"""Free-tier / model-registry consistency (F2, QA E2E 2026-07-17).

The GUI picker merges account models (from ``opai.model_registry``) with free
API models (from ``opaihub.free_models``). Before this test, the free models
were a registry blind spot: visible in the picker, absent from the one
validation path. These tests pin the two modules together so every
picker-visible free model is registered — with honest capability metadata —
and no free provider exists in one module without the other.
"""

from __future__ import annotations

import unittest

from opai import model_registry as reg
from opaihub.free_models import FREE_MODEL_SPECS


class FreeProviderRegistrationTests(unittest.TestCase):
    def test_every_free_spec_provider_is_registered(self):
        for spec in FREE_MODEL_SPECS:
            with self.subTest(provider=spec["provider"]):
                self.assertIn(spec["provider"], reg.free_providers())
                self.assertIn(spec["provider"], reg._REGISTRY)

    def test_registered_free_providers_match_free_specs_exactly(self):
        """No free provider in one module without the other."""
        spec_providers = {spec["provider"] for spec in FREE_MODEL_SPECS}
        self.assertEqual(set(reg.free_providers()), spec_providers)

    def test_account_and_free_tiers_are_disjoint(self):
        self.assertFalse(set(reg.providers()) & set(reg.free_providers()))
        self.assertEqual(reg.providers(), ("claude", "codex", "copilot"))


class FreeModelConsistencyTests(unittest.TestCase):
    def test_every_picker_visible_free_model_is_registered(self):
        for spec in FREE_MODEL_SPECS:
            with self.subTest(id=spec["id"]):
                found = reg.find(spec["provider"], spec["model_id"])
                self.assertIsNotNone(
                    found,
                    f"picker-visible free model '{spec['id']}' is missing "
                    "from model_registry",
                )
                self.assertEqual(found.id, spec["model_id"])

    def test_picker_id_format_matches_registered_provider_and_model(self):
        for spec in FREE_MODEL_SPECS:
            with self.subTest(id=spec["id"]):
                self.assertEqual(
                    spec["id"], f"free:{spec['provider']}:{spec['model_id']}"
                )

    def test_no_extra_registered_free_models_without_operational_spec(self):
        operational = {(s["provider"], s["model_id"]) for s in FREE_MODEL_SPECS}
        for provider in reg.free_providers():
            for model_spec in reg.models_for(provider):
                with self.subTest(provider=provider, model=model_spec.id):
                    self.assertIn(
                        (provider, model_spec.id),
                        operational,
                        f"registered free model {provider}/{model_spec.id} has "
                        "no operational spec in opaihub/free_models.py",
                    )

    def test_free_specs_are_well_formed(self):
        for provider in reg.free_providers():
            specs = reg.models_for(provider)
            self.assertTrue(specs, f"{provider} has no registered models")
            ids = [spec.id for spec in specs]
            self.assertEqual(len(ids), len(set(ids)))
            for spec in specs:
                self.assertTrue(spec.id and spec.display and spec.full)
                self.assertIn(spec.capability, reg.CAPABILITIES)


class FreeValidationPathTests(unittest.TestCase):
    """The one validation path now answers for free models too (F2)."""

    def test_validate_accepts_registered_free_model(self):
        result = reg.validate("gemini", "gemini-3.1-flash-lite")
        self.assertTrue(result["valid"])
        self.assertEqual(result["canonical"], "gemini-3.1-flash-lite")
        self.assertEqual(result["reason"], "")

    def test_validate_flags_unknown_free_model_with_fallback(self):
        result = reg.validate("gemini", "gemini-2.0-flash")
        self.assertFalse(result["valid"])
        self.assertIsNone(result["canonical"])
        self.assertEqual(result["fallback"], "gemini-3.1-flash-lite")
        self.assertIn("no longer lists", result["reason"])

    def test_free_default_models_are_registered_ids(self):
        for provider in reg.free_providers():
            with self.subTest(provider=provider):
                default = reg.default_model(provider)
                self.assertIsNotNone(default)
                self.assertIsNotNone(reg.find(provider, default.id))

    def test_resolve_id_is_case_insensitive_for_free_models(self):
        self.assertEqual(
            reg.resolve_id("Gemini", "GEMINI-3.1-FLASH-LITE"),
            "gemini-3.1-flash-lite",
        )

    def test_account_catalog_stays_account_only(self):
        # The doctor/Connection-Doctor catalog is an account contract; free
        # models surface through opaihub/free_models.py instead.
        self.assertEqual(set(reg.catalog()), set(reg.providers()))


if __name__ == "__main__":
    unittest.main()
