"""Centralized model registry (#170): one validated source of truth for
provider model ids/aliases/display names, with the scattered tables derived
from it and a validation path for 'model not found' drift."""

from __future__ import annotations

import unittest

from vesta import model_registry as reg


class RegistryShapeTests(unittest.TestCase):
    def test_known_providers(self):
        self.assertEqual(set(reg.providers()), {"claude", "codex", "copilot"})

    def test_every_spec_is_well_formed(self):
        for provider in reg.providers():
            specs = reg.models_for(provider)
            self.assertTrue(specs, f"{provider} has no models")
            for spec in specs:
                self.assertTrue(spec.id and spec.display and spec.full)
                self.assertIn(spec.capability, reg.CAPABILITIES)

    def test_ids_are_unique_within_a_provider(self):
        for provider in reg.providers():
            ids = [s.id for s in reg.models_for(provider)]
            self.assertEqual(len(ids), len(set(ids)))

    def test_unknown_provider_is_empty_not_error(self):
        self.assertEqual(reg.models_for("nope"), ())
        self.assertEqual(reg.models_for(None), ())


class ResolveAndDisplayTests(unittest.TestCase):
    def test_canonical_ids_resolve_to_themselves(self):
        self.assertEqual(reg.resolve_id("claude", "opus"), "opus")
        self.assertEqual(reg.resolve_id("codex", "gpt-5.5"), "gpt-5.5")

    def test_aliases_resolve_to_canonical_ids(self):
        self.assertEqual(reg.resolve_id("claude", "claude-opus"), "opus")
        self.assertEqual(reg.resolve_id("claude", "opus-4.8"), "opus")
        self.assertEqual(reg.resolve_id("codex", "spark"), "gpt-5.3-codex-spark")

    def test_resolution_is_case_insensitive(self):
        self.assertEqual(reg.resolve_id("Claude", "OPUS"), "opus")

    def test_unknown_model_resolves_to_none(self):
        self.assertIsNone(reg.resolve_id("claude", "gpt-4"))
        self.assertIsNone(reg.resolve_id("claude", None))

    def test_display_map_matches_picker_labels(self):
        self.assertEqual(
            reg.display_map("claude"),
            {
                "sonnet": "Sonnet 4.6",
                "opus": "Opus 4.8",
                "claude-sonnet-5": "Sonnet 5",
                "haiku": "Haiku 4.5",
                # The registry topped out at Opus 4.8 while Opus 5 was current,
                # so the picker could not reach the flagship at all.
                "claude-opus-5": "Opus 5",
                "claude-fable-5": "Fable 5",
            },
        )
        # Canonical ids are the CLI-accepted full ids; short forms are aliases
        # that resolve to them (#307 fix — "sonnet-5" was not a valid CLI id).
        self.assertEqual(reg.resolve_id("claude", "sonnet-5"), "claude-sonnet-5")
        self.assertEqual(reg.resolve_id("claude", "fable"), "claude-fable-5")
        self.assertEqual(reg.resolve_id("claude", "claude-sonnet-5"), "claude-sonnet-5")
        self.assertEqual(reg.resolve_id("codex", "gpt-5.6"), "gpt-5.6-sol")
        self.assertEqual(reg.display_map("codex")["gpt-5.3-codex-spark"], "Spark")
        self.assertEqual(
            reg.display_map("copilot")["claude-sonnet-4.6"], "Claude Sonnet"
        )
        self.assertEqual(reg.display_map("copilot")["gpt-5.4"], "GPT-5.4")
        self.assertNotIn("gpt-5.2", reg.display_map("copilot"))


class ValidationTests(unittest.TestCase):
    def test_valid_model(self):
        result = reg.validate("claude", "opus")
        self.assertTrue(result["valid"])
        self.assertEqual(result["canonical"], "opus")
        self.assertEqual(result["reason"], "")

    def test_alias_is_valid_and_canonicalized(self):
        result = reg.validate("claude", "claude-opus")
        self.assertTrue(result["valid"])
        self.assertEqual(result["canonical"], "opus")

    def test_unknown_model_flags_a_safe_fallback(self):
        result = reg.validate("claude", "gpt-4-turbo")
        self.assertFalse(result["valid"])
        self.assertIsNone(result["canonical"])
        self.assertEqual(result["fallback"], "sonnet")  # balanced default
        self.assertIn("no longer lists", result["reason"])

    def test_unknown_provider_is_invalid(self):
        result = reg.validate("bard", "anything")
        self.assertFalse(result["valid"])
        self.assertIsNone(result["fallback"])
        self.assertIn("Unknown provider", result["reason"])

    def test_default_model_prefers_balanced(self):
        # Proven Sonnet 4.6 is the balanced default; the Claude 5 family is
        # opt-in so an unavailable new model never breaks the default (#307).
        self.assertEqual(reg.default_model("claude").id, "sonnet")
        self.assertEqual(reg.default_model("codex").id, "gpt-5.6-terra")
        self.assertEqual(reg.default_model("copilot").id, "claude-sonnet-4.6")

    def test_catalog_is_plain_serializable_data(self):
        cat = reg.catalog()
        self.assertEqual(set(cat), {"claude", "codex", "copilot"})
        first = cat["claude"][0]
        self.assertEqual(set(first), {"id", "display", "capability"})


class DerivedTablesStayInSyncTests(unittest.TestCase):
    """The whole point of #170: the previously-drifting tables now derive from
    the registry, so they can never disagree again."""

    def test_accounts_tuples_match_the_registry(self):
        from vestahub.accounts import CLAUDE_MODELS, CODEX_MODELS, COPILOT_MODELS

        self.assertEqual(
            CLAUDE_MODELS, [(s.id, s.full) for s in reg.models_for("claude")]
        )
        self.assertEqual(
            CODEX_MODELS,
            [(s.id, s.full, s.capability) for s in reg.models_for("codex")],
        )
        self.assertEqual(
            COPILOT_MODELS,
            [(s.id, s.full, s.capability) for s in reg.models_for("copilot")],
        )

    def test_provider_contract_display_matches_the_registry(self):
        from vesta.provider_contract import (
            _CLAUDE_DISPLAY,
            _CODEX_DISPLAY,
            _COPILOT_DISPLAY,
        )

        self.assertEqual(_CLAUDE_DISPLAY, reg.display_map("claude"))
        self.assertEqual(_CODEX_DISPLAY, reg.display_map("codex"))
        self.assertEqual(_COPILOT_DISPLAY, reg.display_map("copilot"))

    def test_picker_labels_are_unchanged_end_to_end(self):
        # The user-visible labels must be byte-identical to before #170.
        from vesta.provider_contract import provider_display_name

        self.assertEqual(provider_display_name("claude", "opus"), "Claude · Opus 4.8")
        self.assertEqual(
            provider_display_name("codex", "gpt-5.3-codex-spark"), "Codex · Spark"
        )
        self.assertEqual(
            provider_display_name("copilot", "claude-sonnet-4.6"),
            "Copilot · Claude Sonnet",
        )


class DoctorWiringTests(unittest.TestCase):
    """`vesta doctor` surfaces the one true catalog and flags a stale default."""

    def test_model_check_passes_for_a_registry_model(self):
        from unittest import mock

        from vesta.cli import _doctor_model_check

        with mock.patch(
            "vestahub.gui_preferences.load_gui_preferences",
            return_value={"default_model": "account:claude:opus"},
        ):
            result = _doctor_model_check(_FAKE_ROOT, reg.validate)
        self.assertTrue(result["checked"])
        self.assertTrue(result["valid"])

    def test_model_check_flags_a_stale_default_with_the_fallback(self):
        from unittest import mock

        from vesta.cli import _doctor_model_check

        with mock.patch(
            "vestahub.gui_preferences.load_gui_preferences",
            return_value={"default_model": "account:claude:gpt-4-turbo"},
        ):
            result = _doctor_model_check(_FAKE_ROOT, reg.validate)
        self.assertTrue(result["checked"])
        self.assertFalse(result["valid"])
        self.assertEqual(result["fallback"], "sonnet")

    def test_model_check_skips_non_account_models(self):
        from unittest import mock

        from vesta.cli import _doctor_model_check

        for model in ("auto", "free:gemini:flash", "ollama:qwen2.5-coder"):
            with mock.patch(
                "vestahub.gui_preferences.load_gui_preferences",
                return_value={"default_model": model},
            ):
                result = _doctor_model_check(_FAKE_ROOT, reg.validate)
            self.assertTrue(result["valid"], f"{model} should not be flagged")

    def test_model_check_never_crashes_on_a_bad_prefs_file(self):
        from unittest import mock

        from vesta.cli import _doctor_model_check

        with mock.patch(
            "vestahub.gui_preferences.load_gui_preferences",
            side_effect=ValueError("corrupt"),
        ):
            result = _doctor_model_check(_FAKE_ROOT, reg.validate)
        self.assertFalse(result["checked"])


_FAKE_ROOT = __import__("pathlib").Path(".")


if __name__ == "__main__":
    unittest.main()
