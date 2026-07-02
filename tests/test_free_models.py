"""Tests for the free API model registry and picker options."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from opaihub.free_models import (
    FREE_MODEL_SPECS,
    list_free_models,
    spec_for_model_id,
)


class FreeModelSpecsTests(unittest.TestCase):
    def test_spec_ids_are_unique(self):
        ids = [s["id"] for s in FREE_MODEL_SPECS]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate spec IDs detected")

    def test_all_specs_have_required_fields(self):
        required = {
            "id", "label", "advanced_label", "provider", "model_id",
            "api_base", "env_key", "group", "setup_hint",
        }
        for spec in FREE_MODEL_SPECS:
            missing = required - spec.keys()
            self.assertFalse(missing, f"Spec '{spec.get('id')}' missing: {missing}")

    def test_all_specs_have_free_group(self):
        for spec in FREE_MODEL_SPECS:
            self.assertEqual(spec["group"], "free", f"Spec '{spec.get('id')}' has wrong group")

    def test_five_or_more_free_models_defined(self):
        self.assertGreaterEqual(len(FREE_MODEL_SPECS), 5)

    def test_deepseek_specs_present(self):
        ids = {s["id"] for s in FREE_MODEL_SPECS}
        self.assertIn("free:deepseek:deepseek-chat", ids)
        self.assertIn("free:deepseek:deepseek-reasoner", ids)

    def test_gemini_groq_mistral_present(self):
        ids = {s["id"] for s in FREE_MODEL_SPECS}
        self.assertIn("free:gemini:gemini-2.0-flash", ids)
        self.assertIn("free:groq:llama-3.3-70b-versatile", ids)
        self.assertIn("free:mistral:mistral-small-latest", ids)

    def test_spec_for_known_id(self):
        spec = spec_for_model_id("free:deepseek:deepseek-chat")
        self.assertIsNotNone(spec)
        self.assertEqual(spec["provider"], "deepseek")
        self.assertEqual(spec["model_id"], "deepseek-chat")

    def test_spec_for_unknown_id_returns_none(self):
        self.assertIsNone(spec_for_model_id("free:unknown:model"))
        self.assertIsNone(spec_for_model_id(""))
        self.assertIsNone(spec_for_model_id(None))


class ListFreeModelsTests(unittest.TestCase):
    def _no_keys(self):
        """Env patch that clears all free model API keys."""
        return {s["env_key"]: "" for s in FREE_MODEL_SPECS}

    def test_list_returns_all_specs_as_options(self):
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        self.assertEqual(len(models), len(FREE_MODEL_SPECS))

    def test_grayed_without_api_key(self):
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            with self.subTest(model=model["id"]):
                self.assertFalse(model["available"])
                self.assertIsNotNone(model["disabled_reason"])
                self.assertTrue(model["disabled_reason"])

    def test_enabled_with_api_key(self):
        spec = FREE_MODEL_SPECS[0]  # deepseek-chat
        env = self._no_keys()
        env[spec["env_key"]] = "test-key-abc"
        with mock.patch.dict(os.environ, env):
            models = list_free_models()
        provider_models = [m for m in models if m["provider"] == spec["provider"]]
        self.assertTrue(
            any(m["available"] for m in provider_models),
            "At least one model for the provider should be enabled when key is set",
        )

    def test_group_is_always_free(self):
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            self.assertEqual(model["group"], "free")

    def test_all_picker_fields_present(self):
        required = {
            "id", "label", "advanced_label", "provider", "model",
            "kind", "group", "paid", "available", "disabled_reason",
        }
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            missing = required - model.keys()
            self.assertFalse(missing, f"'{model.get('id')}' missing picker fields: {missing}")

    def test_paid_is_false_for_all_free_models(self):
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            self.assertFalse(model["paid"])

    def test_kind_is_free(self):
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            self.assertEqual(model["kind"], "free")


class FreeAPIRunnerTests(unittest.TestCase):
    def test_runner_available_with_key(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.deepseek.com/v1", "deepseek-chat", "test-key")
        self.assertTrue(runner.available())

    def test_runner_unavailable_without_key(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.deepseek.com/v1", "deepseek-chat", "")
        self.assertFalse(runner.available())

    def test_runner_unavailable_with_whitespace_key(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.deepseek.com/v1", "deepseek-chat", "   ")
        self.assertFalse(runner.available())

    def test_runner_for_free_model_id_with_key(self):
        from opaihub.local_runner import FreeAPIRunner, runner_for_model

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key-abc"}):
            runner = runner_for_model("free:deepseek:deepseek-chat")

        self.assertIsInstance(runner, FreeAPIRunner)
        self.assertTrue(runner.available())

    def test_runner_for_free_model_id_without_key(self):
        from opaihub.local_runner import FreeAPIRunner, runner_for_model

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            runner = runner_for_model("free:deepseek:deepseek-chat")

        self.assertIsInstance(runner, FreeAPIRunner)
        self.assertFalse(runner.available())

    def test_runner_for_unknown_free_model_returns_none(self):
        from opaihub.local_runner import runner_for_model

        runner = runner_for_model("free:unknown:nonexistent")
        self.assertIsNone(runner)

    def test_runner_complete_sends_auth_header(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.deepseek.com/v1", "deepseek-chat", "sk-test-123")
        mock_response = {"choices": [{"message": {"content": "Test response"}}]}
        captured_headers: dict = {}

        def fake_http(url, *, method="GET", payload=None, timeout=60.0, extra_headers=None):
            captured_headers.update(extra_headers or {})
            return mock_response

        with mock.patch("opaihub.local_runner._http_json", side_effect=fake_http):
            result = runner.complete("What is 2+2?")

        self.assertEqual(result, "Test response")
        self.assertIn("Authorization", captured_headers)
        self.assertEqual(captured_headers["Authorization"], "Bearer sk-test-123")

    def test_runner_complete_with_system_prompt(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "key"
        )
        mock_response = {"choices": [{"message": {"content": "OK"}}]}
        captured_payload: dict = {}

        def fake_http(url, *, method="GET", payload=None, timeout=60.0, extra_headers=None):
            captured_payload.update(payload or {})
            return mock_response

        with mock.patch("opaihub.local_runner._http_json", side_effect=fake_http):
            runner.complete("Hello", system="You are a coding assistant.")

        messages = captured_payload.get("messages", [])
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "You are a coding assistant.")


class AskFreeModelTests(unittest.TestCase):
    """Tests for the ask() → _ask_free_model() dispatch path."""

    def test_ask_free_requires_confirmation(self):
        """ask() with a free: model returns confirmation_required without allow_cloud."""
        from pathlib import Path
        from opai.app_state import ask

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
            result = ask(
                Path("/tmp"),
                "What is 2+2?",
                model_choice="free:deepseek:deepseek-chat",
                allow_cloud=False,
            )

        self.assertEqual(result["status"], "confirmation_required")
        self.assertIn("model_id", result)
        self.assertEqual(result["model_id"], "free:deepseek:deepseek-chat")

    def test_ask_free_dispatches_with_allow_cloud(self):
        """ask() with allow_cloud=True dispatches through FreeAPIRunner."""
        from pathlib import Path
        from opai.app_state import ask

        fake_result = {"status": "ok", "response": "4"}
        with (
            mock.patch("opai.app_state._ask_free_model", return_value=fake_result) as m,
            mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}),
        ):
            result = ask(
                Path("/tmp"),
                "What is 2+2?",
                model_choice="free:deepseek:deepseek-chat",
                allow_cloud=True,
            )

        m.assert_called_once()
        self.assertEqual(result, fake_result)

    def test_ask_free_confirmation_message_mentions_provider(self):
        """Confirmation message must name the provider, not a generic label."""
        from pathlib import Path
        from opai.app_state import ask

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
            result = ask(
                Path("/tmp"),
                "task",
                model_choice="free:deepseek:deepseek-chat",
                allow_cloud=False,
            )

        # Message must reference DeepSeek by name (from spec_for_model_id)
        self.assertIn("DeepSeek", result["message"])


if __name__ == "__main__":
    unittest.main()
