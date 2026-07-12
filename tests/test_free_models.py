"""Tests for the free API model registry and picker options."""

from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub.free_models import (
    FREE_MODEL_SPECS,
    list_free_models,
    spec_for_model_id,
)
from tests._helpers import make_repo


PATCH_ONE_TO_TWO = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""


class FreeModelSpecsTests(unittest.TestCase):
    def test_spec_ids_are_unique(self):
        ids = [s["id"] for s in FREE_MODEL_SPECS]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate spec IDs detected")

    def test_all_specs_have_required_fields(self):
        required = {
            "id",
            "label",
            "advanced_label",
            "provider",
            "model_id",
            "api_base",
            "env_key",
            "group",
            "setup_hint",
        }
        for spec in FREE_MODEL_SPECS:
            missing = required - spec.keys()
            self.assertFalse(missing, f"Spec '{spec.get('id')}' missing: {missing}")

    def test_all_specs_have_free_group(self):
        for spec in FREE_MODEL_SPECS:
            self.assertEqual(
                spec["group"], "free", f"Spec '{spec.get('id')}' has wrong group"
            )

    def test_registry_only_contains_verified_free_tier_models(self):
        ids = {s["id"] for s in FREE_MODEL_SPECS}
        self.assertIn("free:gemini:gemini-3.1-flash-lite", ids)
        self.assertIn("free:groq:openai/gpt-oss-120b", ids)
        self.assertIn("free:mistral:mistral-small-latest", ids)
        self.assertFalse(
            any(s["provider"] == "deepseek" for s in FREE_MODEL_SPECS),
            "DeepSeek's hosted API is usage-priced, not a free tier",
        )
        self.assertNotIn("free:gemini:gemini-2.0-flash", ids)
        self.assertNotIn("free:groq:llama-3.3-70b-versatile", ids)

    def test_spec_for_known_id(self):
        spec = spec_for_model_id("free:gemini:gemini-3.1-flash-lite")
        self.assertIsNotNone(spec)
        self.assertEqual(spec["provider"], "gemini")
        self.assertEqual(spec["model_id"], "gemini-3.1-flash-lite")

    def test_spec_for_unknown_id_returns_none(self):
        self.assertIsNone(spec_for_model_id("free:unknown:model"))
        self.assertIsNone(spec_for_model_id(""))
        self.assertIsNone(spec_for_model_id(None))


class ListFreeModelsTests(unittest.TestCase):
    def setUp(self):
        # Hermetic: never consult the developer's real OS keyring — a stored
        # Gemini/Groq key on the machine must not flip "no key" assertions.
        patcher = mock.patch("opaihub.credentials._default_backend", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

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
        spec = FREE_MODEL_SPECS[0]
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
            "id",
            "label",
            "advanced_label",
            "provider",
            "model",
            "kind",
            "group",
            "paid",
            "available",
            "disabled_reason",
        }
        with mock.patch.dict(os.environ, self._no_keys()):
            models = list_free_models()
        for model in models:
            missing = required - model.keys()
            self.assertFalse(
                missing, f"'{model.get('id')}' missing picker fields: {missing}"
            )

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
    def setUp(self):
        # Hermetic: keep the real OS keyring out of runner_for_model key lookup.
        patcher = mock.patch("opaihub.credentials._default_backend", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_runner_rejects_non_https_api_endpoint(self):
        from opaihub.local_runner import FreeAPIRunner

        with self.assertRaisesRegex(ValueError, "HTTPS"):
            FreeAPIRunner("http://api.example.test/v1", "example-model", "test-key")

    def test_runner_available_with_key(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-3.1-flash-lite",
            "test-key",
        )
        self.assertTrue(runner.available())

    def test_runner_unavailable_without_key(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-3.1-flash-lite",
            "",
        )
        self.assertFalse(runner.available())

    def test_runner_unavailable_with_whitespace_key(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-3.1-flash-lite",
            "   ",
        )
        self.assertFalse(runner.available())

    def test_runner_for_free_model_id_with_key(self):
        from opaihub.local_runner import FreeAPIRunner, runner_for_model

        with mock.patch.dict(
            os.environ,
            {"GOOGLE_API_KEY": "test-key-abc"},  # pragma: allowlist secret
        ):
            runner = runner_for_model("free:gemini:gemini-3.1-flash-lite")

        self.assertIsInstance(runner, FreeAPIRunner)
        self.assertTrue(runner.available())

    def test_runner_for_free_model_id_without_key(self):
        from opaihub.local_runner import FreeAPIRunner, runner_for_model

        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": ""}):
            runner = runner_for_model("free:gemini:gemini-3.1-flash-lite")

        self.assertIsInstance(runner, FreeAPIRunner)
        self.assertFalse(runner.available())

    def test_runner_for_unknown_free_model_returns_none(self):
        from opaihub.local_runner import runner_for_model

        runner = runner_for_model("free:unknown:nonexistent")
        self.assertIsNone(runner)

    def test_runner_complete_sends_auth_header(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-3.1-flash-lite",
            "sk-test-123",
        )
        mock_response = {"choices": [{"message": {"content": "Test response"}}]}
        captured_headers: dict = {}

        def fake_http(
            url,
            *,
            method="POST",
            payload=None,
            timeout=60.0,
            cancel=None,
            extra_headers=None,
        ):
            captured_headers.update(extra_headers or {})
            return mock_response

        with mock.patch(
            "opaihub.local_runner._http_json_cancellable", side_effect=fake_http
        ):
            result = runner.complete("What is 2+2?")

        self.assertEqual(result, "Test response")
        self.assertIn("Authorization", captured_headers)
        self.assertEqual(captured_headers["Authorization"], "Bearer sk-test-123")

    def test_runner_complete_with_system_prompt(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "key"
        )
        mock_response = {"choices": [{"message": {"content": "OK"}}]}
        captured_payload: dict = {}

        def fake_http(
            url,
            *,
            method="POST",
            payload=None,
            timeout=60.0,
            cancel=None,
            extra_headers=None,
        ):
            captured_payload.update(payload or {})
            return mock_response

        with mock.patch(
            "opaihub.local_runner._http_json_cancellable", side_effect=fake_http
        ):
            runner.complete("Hello", system="You are a coding assistant.")

        messages = captured_payload.get("messages", [])
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "You are a coding assistant.")

    def test_runner_tool_loop_reads_and_edits_real_repository(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-3.1-flash-lite",
            "key",
        )
        responses = [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "read-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"app.py"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "patch-1",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_patch",
                                        "arguments": '{"patch":'
                                        + json.dumps(PATCH_ONE_TO_TWO)
                                        + "}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {"choices": [{"message": {"content": "Implemented the fix."}}]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opaihub.local_runner._http_json_cancellable",
                side_effect=responses,
            ) as http:
                result = runner.complete_with_tools(
                    "Set value to two.",
                    project_root=root,
                    allow_edits=True,
                    system="You are a coding agent.",
                )

            self.assertEqual(result["text"], "Implemented the fix.")
            self.assertEqual(
                [item["tool"] for item in result["tool_trace"]],
                ["read_file", "apply_patch"],
            )
            self.assertTrue(result["tool_trace"][1]["ok"])
            self.assertEqual((root / "app.py").read_text(), "value = 2\n")
            first_payload = http.call_args_list[0].kwargs["payload"]
            tool_names = [item["function"]["name"] for item in first_payload["tools"]]
            self.assertIn("apply_patch", tool_names)

    def test_read_only_tool_loop_does_not_expose_patch_tool(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "model", "key")
        response = {"choices": [{"message": {"content": "Explanation"}}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opaihub.local_runner._http_json_cancellable", return_value=response
            ) as http:
                result = runner.complete_with_tools(
                    "Explain app.py", project_root=root, allow_edits=False
                )

        self.assertEqual(result["text"], "Explanation")
        names = [
            item["function"]["name"]
            for item in http.call_args.kwargs["payload"]["tools"]
        ]
        self.assertNotIn("apply_patch", names)

    def test_tool_loop_enforces_limit_across_batched_calls(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "model", "key")
        response = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "read-1",
                                "function": {
                                    "name": "apply_patch",
                                    "arguments": '{"patch":'
                                    + json.dumps(PATCH_ONE_TO_TWO)
                                    + "}",
                                },
                            },
                            {
                                "id": "read-2",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"app.py"}',
                                },
                            },
                        ],
                    }
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opaihub.local_runner._http_json_cancellable", return_value=response
            ):
                with self.assertRaisesRegex(RuntimeError, "tool-call limit"):
                    runner.complete_with_tools(
                        "Read app.py",
                        project_root=root,
                        allow_edits=True,
                        max_tool_calls=1,
                    )
            self.assertEqual((root / "app.py").read_text(), "value = 1\n")


class AskFreeModelTests(unittest.TestCase):
    """Tests for the ask() → _ask_free_model() dispatch path."""

    def test_ask_free_requires_confirmation(self):
        """ask() with a free: model returns confirmation_required without allow_cloud."""
        from pathlib import Path
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

        self.assertEqual(result["status"], "confirmation_required")
        self.assertIn("model_id", result)
        self.assertEqual(result["model_id"], "free:gemini:gemini-3.1-flash-lite")
        self.assertIn("quota or billing", result["message"])

    def test_editable_free_confirmation_discloses_repository_file_transfer(self):
        from opai.app_state import ask

        with mock.patch.dict(
            os.environ,
            {"GOOGLE_API_KEY": "sk-test"},  # pragma: allowlist secret
        ):
            result = ask(
                Path("/tmp"),
                "Fix app.py",
                model_choice="free:gemini:gemini-3.1-flash-lite",
                allow_cloud=False,
                allow_edits=True,
                mode="safe-auto",
            )

        self.assertIn("repository file contents", result["message"].lower())

    def test_ask_free_dispatches_with_allow_cloud(self):
        """ask() with allow_cloud=True dispatches through FreeAPIRunner."""
        from pathlib import Path
        from opai.app_state import ask

        fake_result = {
            "status": "answered_locally",
            "answer": "4",
            "source": "local_model",
        }
        with (
            mock.patch(
                "opaihub.ask.run_explicit_model", return_value=fake_result
            ) as run_explicit,
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
        self.assertEqual(result["model_id"], "free:gemini:gemini-3.1-flash-lite")
        self.assertEqual(
            run_explicit.call_args.kwargs["selected_model_id"],
            "free:gemini:gemini-3.1-flash-lite",
        )
        self.assertTrue(run_explicit.call_args.kwargs["record"])

    def test_editable_explicit_free_model_bypasses_auto_route_and_cache(self):
        from opai.app_state import ask

        class AgenticRunner:
            name = "free-api"
            model = "gemini-3.1-flash-lite"
            last_usage = {}

            def available(self):
                return True

            def complete_with_tools(self, prompt, **kwargs):
                return {
                    "text": "Implemented.",
                    "tool_trace": [{"tool": "apply_patch", "ok": True, "data": {}}],
                }

        selected = "free:gemini:gemini-3.1-flash-lite"
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with (
                mock.patch(
                    "opaihub.local_runner.runner_for_model",
                    return_value=AgenticRunner(),
                ),
                mock.patch(
                    "opaihub.ask.recommend_model",
                    side_effect=AssertionError("explicit provider must not reroute"),
                ),
                mock.patch(
                    "opaihub.ask.result_cache.lookup",
                    side_effect=AssertionError("mutating result cache is unsafe"),
                ),
                mock.patch(
                    "opaihub.ask.collect_evidence",
                    side_effect=AssertionError("GUI already built the task packet"),
                ),
            ):
                result = ask(
                    root,
                    "Fix app.py",
                    model_choice=selected,
                    allow_cloud=True,
                    allow_edits=True,
                    mode="safe-auto",
                )

        self.assertEqual(result["status"], "answered_by_free_api")
        self.assertEqual(result["tool_trace"][0]["tool"], "apply_patch")

    def test_ask_free_confirmation_message_mentions_provider(self):
        """Confirmation message must name the provider, not a generic label."""
        from pathlib import Path
        from opai.app_state import ask

        with mock.patch.dict(
            os.environ,
            {"GOOGLE_API_KEY": "sk-test"},  # pragma: allowlist secret
        ):
            result = ask(
                Path("/tmp"),
                "task",
                model_choice="free:gemini:gemini-3.1-flash-lite",
                allow_cloud=False,
            )

        self.assertIn("Gemini", result["message"])

    def test_gui_pipeline_dispatches_confirmed_free_model(self):
        from opaihub.gui_pipeline import handle_gui_message

        selected = "free:gemini:gemini-3.1-flash-lite"
        fake_result = {
            "status": "answered_by_free_api",
            "answer": "Free-tier answer",
            "source": "free_api",
            "model_id": selected,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("opai.app_state.ask", return_value=fake_result) as ask_mock:
                result = handle_gui_message(
                    Path(tmp),
                    "Explain this project",
                    model_id=selected,
                    mode="ask",
                    allow_cloud=True,
                )

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["answer"], "Free-tier answer")
        self.assertTrue(ask_mock.call_args.kwargs["allow_cloud"])
        self.assertFalse(ask_mock.call_args.kwargs["record_route"])

    def test_gui_pipeline_passes_edit_authority_to_free_implementation(self):
        from opaihub.gui_pipeline import handle_gui_message

        selected = "free:gemini:gemini-3.1-flash-lite"
        fake_result = {
            "status": "answered_by_free_api",
            "answer": "Implemented.",
            "source": "free_api",
            "model_id": selected,
            "tool_trace": [{"tool": "apply_patch", "ok": True}],
            "changed_files": [" M app.py"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch("opai.app_state.ask", return_value=fake_result) as ask_mock:
                result = handle_gui_message(
                    root,
                    "Fix app.py and run tests.",
                    model_id=selected,
                    mode="safe-auto",
                    allow_cloud=True,
                )

        self.assertTrue(ask_mock.call_args.kwargs["allow_edits"])
        self.assertEqual(result["changed_files"], [" M app.py"])
        self.assertEqual(result["tool_trace"][-1]["tool"], "apply_patch")

    def test_gui_pipeline_cannot_bypass_free_model_confirmation(self):
        from opaihub.gui_pipeline import handle_gui_message

        selected = "free:gemini:gemini-3.1-flash-lite"
        with tempfile.TemporaryDirectory() as tmp:
            result = handle_gui_message(
                Path(tmp),
                "Explain this project",
                model_id=selected,
                mode="ask",
                allow_cloud=False,
            )

        self.assertEqual(result["status"], "needs_free_confirmation")


if __name__ == "__main__":
    unittest.main()
