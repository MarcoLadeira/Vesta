"""Tests for the free API model registry and picker options."""

from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from opaihub.free_models import (
    FREE_MODEL_SPECS,
    list_free_models,
    spec_for_model_id,
)
from tests._helpers import make_repo


@pytest.fixture(autouse=True)
def _clean_broken_git_config_env():
    """Scrub the broken inherited GIT_CONFIG_* header before each test.

    The dev shell exports ``GIT_CONFIG_COUNT=2`` with an EMPTY
    ``GIT_CONFIG_VALUE_0``. Any ``mock.patch.dict(os.environ, ...)`` in this
    module round-trips that empty value through ``putenv``, which on Windows
    deletes it — leaving a config header git rejects ("missing config value
    GIT_CONFIG_VALUE_0") so every later ``git init`` in ``make_repo`` exits
    128. The header is junk for these tests; removing it is safe.
    """
    for name in list(os.environ):
        if name == "GIT_TERMINAL_PROMPT" or name.startswith("GIT_CONFIG_"):
            os.environ.pop(name, None)
    yield


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

    def test_explicit_external_ceiling_stops_recoverably_without_slicing(self):
        # Task 5: 12 is no longer a terminal budget. An explicit, deprecated
        # external ceiling is honoured only when set (the GUI never sets it), and
        # a batch that would cross it stops recoverably — never a partial turn,
        # never a fake completion.
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
                result = runner.complete_with_tools(
                    "Read app.py",
                    project_root=root,
                    allow_edits=True,
                    max_tool_calls=1,
                )
            self.assertEqual(result["stopped_reason"], "external_ceiling")
            self.assertEqual(result["completion_state"], "stuck_no_progress")
            self.assertEqual(result["tool_trace"], [])
            self.assertIn("ceiling", result["text"].lower())
            # No partial, half-applied turn ran.
            self.assertEqual((root / "app.py").read_text(), "value = 1\n")

    def test_loop_stops_after_repeated_identical_failures(self):
        # #311: a model that keeps making the same failing call is not making
        # progress — the loop stops with an honest terminal instead of thrashing.
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "model", "key")
        failing = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "apply_patch",
                                    "arguments": '{"patch":"not a real patch"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opaihub.local_runner._http_json_cancellable", return_value=failing
            ):
                result = runner.complete_with_tools(
                    "Fix it", project_root=root, allow_edits=True
                )
        self.assertEqual(result["stopped_reason"], "repeated_failure")
        # A thrashing run is reported honestly — never "OPai completed".
        self.assertEqual(result["completion_state"], "stuck_no_progress")
        self.assertTrue(result["last_error"])
        # Stopped at the repeat threshold, not after burning the whole budget.
        self.assertEqual(len(result["tool_trace"]), 3)
        self.assertTrue(all(not item["ok"] for item in result["tool_trace"]))

    def test_loop_self_corrects_after_a_tool_error(self):
        # #311: a failed tool call is fed back; a correct follow-up completes the
        # task and the run ends cleanly (no stopped_reason).
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "model", "key")

        def _call(cid, name, arguments):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": cid,
                                    "function": {"name": name, "arguments": arguments},
                                }
                            ],
                        }
                    }
                ]
            }

        responses = [
            _call("miss", "read_file", '{"path":"does-not-exist.py"}'),  # fails
            _call(
                "fix", "apply_patch", '{"patch":' + json.dumps(PATCH_ONE_TO_TWO) + "}"
            ),  # the correction succeeds
            {"choices": [{"message": {"content": "Fixed the value."}}]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opaihub.local_runner._http_json_cancellable", side_effect=responses
            ):
                result = runner.complete_with_tools(
                    "Set value to two.", project_root=root, allow_edits=True
                )
            self.assertEqual(result["stopped_reason"], "")
            self.assertEqual(result["completion_state"], "completed")
            self.assertEqual(result["text"], "Fixed the value.")
            trace = result["tool_trace"]
            self.assertFalse(trace[0]["ok"])  # first attempt failed
            self.assertTrue(trace[1]["ok"])  # correction applied
            self.assertEqual((root / "app.py").read_text(), "value = 2\n")

    def test_guard_runs_before_every_provider_turn(self):
        # Task 6: the injected guard is consulted before each provider turn of a
        # continuous run — the first and every continuation.
        from opaihub.execution_guard import GuardDecision, GuardOutcome
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "model", "key")

        class RecordingGuard:
            def __init__(self):
                self.checked_turns = []

            def __call__(self, turn_index):
                self.checked_turns.append(turn_index)
                return GuardDecision(GuardOutcome.ALLOW)

        def _read(cid):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": cid,
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"app.py"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

        responses = [
            _read("r1"),
            _read("r2"),
            {"choices": [{"message": {"content": "Explained."}}]},
        ]
        guard = RecordingGuard()
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch(
                "opaihub.local_runner._http_json_cancellable", side_effect=responses
            ):
                runner.complete_with_tools(
                    "Explain app.py",
                    project_root=root,
                    allow_edits=False,
                    tool_calling_enabled=True,
                    guard=guard,
                )
        self.assertEqual(guard.checked_turns, [1, 2, 3])

    def test_guard_block_reports_provider_blocked_not_completion(self):
        # A guard that blocks (e.g. panic/cap) stops the run honestly, with no
        # provider call and no fake completion.
        from opaihub.completion import ProviderBlockedReason
        from opaihub.execution_guard import GuardDecision, GuardOutcome
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "model", "key")

        def guard(turn_index):
            return GuardDecision(
                GuardOutcome.BLOCKED, ProviderBlockedReason.PANIC, detail="panic"
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch("opaihub.local_runner._http_json_cancellable") as http:
                result = runner.complete_with_tools(
                    "Fix app.py",
                    project_root=root,
                    allow_edits=True,
                    guard=guard,
                )
            http.assert_not_called()  # blocked before any provider call
        self.assertEqual(result["completion_state"], "provider_blocked")
        self.assertEqual(result["blocked_reason"], "panic")

    # -- #219: an HTTP error status must never be silently decoded as an -----
    # -- empty successful completion (the "check GOOGLE_API_KEY" bug). -------

    def _fake_https_connection(self, status, body_bytes, headers=None):
        """A minimal ``http.client.HTTPSConnection`` stand-in for one request."""

        class _FakeResponse:
            def __init__(self):
                self.status = status

            def read(self):
                return body_bytes

            def getheaders(self):
                return list((headers or {}).items())

        class _FakeConnection:
            def __init__(self, *args, **kwargs):
                pass

            def request(self, method, path, body=None, headers=None):
                pass

            def getresponse(self):
                return _FakeResponse()

            def close(self):
                pass

        return _FakeConnection

    def test_transport_raises_on_401_blocking_path(self):
        from opaihub.local_runner import _http_json_cancellable

        body = json.dumps(
            {"error": {"code": 401, "message": "API key not valid.", "status": "UNAUTHENTICATED"}}
        ).encode("utf-8")
        fake_conn = self._fake_https_connection(401, body)
        with mock.patch("http.client.HTTPSConnection", fake_conn):
            with self.assertRaises(RuntimeError) as ctx:
                _http_json_cancellable(
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    payload={"model": "m", "messages": []},
                )
        self.assertIn("401", str(ctx.exception))
        self.assertIn("API key not valid", str(ctx.exception))

    def test_transport_raises_on_429_cancellable_path(self):
        from opaihub.local_runner import _http_json_cancellable
        import threading

        body = json.dumps(
            {"error": {"message": "Resource has been exhausted (quota).", "status": "RESOURCE_EXHAUSTED"}}
        ).encode("utf-8")
        fake_conn = self._fake_https_connection(429, body)
        with mock.patch("http.client.HTTPSConnection", fake_conn):
            with self.assertRaises(RuntimeError) as ctx:
                _http_json_cancellable(
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    payload={"model": "m", "messages": []},
                    cancel=threading.Event(),
                )
        self.assertIn("429", str(ctx.exception))

    def test_transport_raises_model_not_found_on_404(self):
        from opaihub.local_runner import _http_json_cancellable

        body = json.dumps({"error": {"message": "models/x is not found."}}).encode("utf-8")
        fake_conn = self._fake_https_connection(404, body)
        with mock.patch("http.client.HTTPSConnection", fake_conn):
            with self.assertRaises(RuntimeError) as ctx:
                _http_json_cancellable(
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    payload={"model": "m", "messages": []},
                )
        self.assertIn("model not found", str(ctx.exception))

    def test_transport_still_decodes_success_response(self):
        from opaihub.local_runner import _http_json_cancellable

        body = json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode("utf-8")
        fake_conn = self._fake_https_connection(200, body)
        with mock.patch("http.client.HTTPSConnection", fake_conn):
            result = _http_json_cancellable(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                payload={"model": "m", "messages": []},
            )
        self.assertEqual(result["choices"][0]["message"]["content"], "hi")

    def test_runner_complete_propagates_real_http_error(self):
        # Once the transport raises (fixed above), FreeAPIRunner.complete()
        # must let that propagate rather than swallowing it into "".
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-3.1-flash-lite",
            "bad-key",
        )
        with mock.patch(
            "opaihub.local_runner._http_json_cancellable",
            side_effect=RuntimeError("HTTP 401: API key not valid."),
        ):
            with self.assertRaises(RuntimeError):
                runner.complete("What is 2+2?")

    def test_runner_complete_raises_on_safety_block(self):
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-3.1-flash-lite",
            "test-key",
        )
        blocked_response = {
            "choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]
        }
        with mock.patch(
            "opaihub.local_runner._http_json_cancellable",
            return_value=blocked_response,
        ):
            with self.assertRaisesRegex(RuntimeError, "safety filter"):
                runner.complete("Some prompt")

    def test_runner_complete_empty_stop_still_returns_empty_string(self):
        # A genuinely empty-but-clean completion (finish_reason "stop") is not
        # a safety block — preserve the prior behaviour of returning "".
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-3.1-flash-lite",
            "test-key",
        )
        empty_response = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
        with mock.patch(
            "opaihub.local_runner._http_json_cancellable",
            return_value=empty_response,
        ):
            self.assertEqual(runner.complete("Some prompt"), "")

    def test_run_explicit_model_reports_runner_error_with_real_cause(self):
        # End-to-end: run_explicit_model must classify a real transport
        # failure as runner_error carrying the real HTTP detail, not a blank
        # answer (#219 - this is what let the generic "no answer" message
        # mask every real Gemini failure).
        from opaihub.ask import run_explicit_model
        from opaihub.local_runner import FreeAPIRunner

        runner = FreeAPIRunner(
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-3.1-flash-lite",
            "bad-key",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            with mock.patch(
                "opaihub.local_runner._http_json_cancellable",
                side_effect=RuntimeError("HTTP 401: API key not valid."),
            ):
                result = run_explicit_model(
                    root,
                    "What is 2+2?",
                    runner=runner,
                    selected_model_id="free:gemini:gemini-3.1-flash-lite",
                    allow_edits=False,
                    tool_calling_enabled=False,
                    record=False,
                )
        self.assertEqual(result["status"], "runner_error")
        self.assertIn("401", result["error"])


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


class FreeToolCallingTests(unittest.TestCase):
    """F6/F7: free models get a real (read-only when edits are off) tool loop."""

    def test_free_read_only_run_offers_read_tools_and_loops(self):
        """tool_calling_enabled=True + allow_edits=False drives complete_with_tools
        with read-only authority — the model loops instead of narrating."""
        from opaihub.ask import run_explicit_model

        class RecordingLoopRunner:
            name = "free-api"
            model = "gemini-3.1-flash-lite"
            last_usage = {}

            def __init__(self):
                self.calls: list[dict] = []

            def available(self):
                return True

            def complete_with_tools(self, prompt, **kwargs):
                self.calls.append(kwargs)
                return {
                    "text": "Explained after reading the files.",
                    "tool_trace": [{"tool": "read_file", "ok": True, "data": {}}],
                    "completion_state": "completed",
                }

        runner = RecordingLoopRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = run_explicit_model(
                root,
                "Explain app.py",
                runner=runner,
                selected_model_id="free:gemini:gemini-3.1-flash-lite",
                allow_edits=False,
                tool_calling_enabled=True,
                record=False,
            )

        # The loop actually ran — no single-shot prose narration.
        self.assertEqual(len(runner.calls), 1)
        # Read tools are offered (tool calling on) while writes stay gated.
        self.assertTrue(runner.calls[0]["tool_calling_enabled"])
        self.assertFalse(runner.calls[0]["allow_edits"])
        self.assertEqual(result["tool_trace"][0]["tool"], "read_file")
        self.assertEqual(result["completion_state"], "completed")

    def test_free_tool_loop_keeps_write_tools_gated_on_allow_edits(self):
        """allow_edits=False must reach the loop unchanged — write tools are
        derived from it inside the runner/executor."""
        from opaihub.ask import run_explicit_model

        class RecordingLoopRunner:
            name = "free-api"
            model = "m"
            last_usage = {}

            def __init__(self):
                self.calls: list[dict] = []

            def available(self):
                return True

            def complete_with_tools(self, prompt, **kwargs):
                self.calls.append(kwargs)
                return {"text": "ok", "tool_trace": [], "completion_state": "completed"}

        runner = RecordingLoopRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            run_explicit_model(
                root,
                "Explain app.py",
                runner=runner,
                selected_model_id="free:gemini:gemini-3.1-flash-lite",
                allow_edits=False,
                tool_calling_enabled=True,
                record=False,
            )

        self.assertFalse(runner.calls[0]["allow_edits"])

    def test_no_tool_free_path_is_not_completed_when_nothing_happened(self):
        """F8: a single-shot free run with an empty answer used to default to
        'completed' — it must now report an honest non-completed state."""
        from opaihub.ask import run_explicit_model
        from opaihub.completion import result_is_completed

        class ProseOnlyRunner:
            name = "free-api"
            model = "m"

            def available(self):
                return True

            def complete(self, prompt, **kwargs):
                return ""

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = run_explicit_model(
                root,
                "hi",
                runner=ProseOnlyRunner(),
                selected_model_id="free:gemini:gemini-3.1-flash-lite",
                record=False,
            )

        self.assertNotEqual(result["completion_state"], "completed")
        self.assertFalse(result_is_completed(result))

    def test_no_tool_free_path_with_a_real_answer_still_completes(self):
        from opaihub.ask import run_explicit_model
        from opaihub.completion import result_is_completed

        class ProseOnlyRunner:
            name = "free-api"
            model = "m"

            def available(self):
                return True

            def complete(self, prompt, **kwargs):
                return "A real answer."

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = run_explicit_model(
                root,
                "hi",
                runner=ProseOnlyRunner(),
                selected_model_id="free:gemini:gemini-3.1-flash-lite",
                record=False,
            )

        self.assertEqual(result["completion_state"], "completed")
        self.assertTrue(result_is_completed(result))

    def test_ask_free_threads_tool_calling_enabled_to_explicit_run(self):
        """app_state.ask must pass the pipeline's tool authority down (F6/F7)."""
        from opai.app_state import ask

        fake_result = {
            "status": "answered_locally",
            "answer": "Explained.",
            "source": "explicit_model",
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
                "Explain this project",
                model_choice="free:gemini:gemini-3.1-flash-lite",
                allow_cloud=True,
                allow_edits=False,
                tool_calling_enabled=True,
            )

        self.assertEqual(result["status"], "answered_by_free_api")
        self.assertTrue(run_explicit.call_args.kwargs["tool_calling_enabled"])
        self.assertFalse(run_explicit.call_args.kwargs["allow_edits"])

    def test_gui_pipeline_threads_tool_authority_to_free_model(self):
        """The pipeline computes RequestToolAuthority and passes it down."""
        from opaihub.gui_pipeline import handle_gui_message

        selected = "free:gemini:gemini-3.1-flash-lite"
        fake_result = {
            "status": "answered_by_free_api",
            "answer": "Free-tier answer",
            "source": "free_api",
            "model_id": selected,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            with mock.patch("opai.app_state.ask", return_value=fake_result) as ask_mock:
                handle_gui_message(
                    root,
                    "Explain this project",
                    model_id=selected,
                    mode="ask",
                    allow_cloud=True,
                )

        self.assertTrue(ask_mock.call_args.kwargs["tool_calling_enabled"])
        self.assertFalse(ask_mock.call_args.kwargs["allow_edits"])

    def test_explicit_run_threads_one_shot_allow_command_to_tool_loop(self):
        """F17/F9: the exact approved command reaches complete_with_tools."""
        from opaihub.ask import run_explicit_model

        class RecordingLoopRunner:
            name = "free-api"
            model = "m"
            last_usage = {}

            def __init__(self):
                self.calls: list[dict] = []

            def available(self):
                return True

            def complete_with_tools(self, prompt, **kwargs):
                self.calls.append(kwargs)
                return {"text": "ok", "tool_trace": [], "completion_state": "completed"}

        runner = RecordingLoopRunner()
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            run_explicit_model(
                root,
                "Fetch the issue",
                runner=runner,
                selected_model_id="free:gemini:gemini-3.1-flash-lite",
                allow_edits=True,
                tool_calling_enabled=True,
                record=False,
                allow_command="gh issue view 219",
            )

        self.assertEqual(runner.calls[0]["allow_command"], "gh issue view 219")

    def test_allow_command_survives_runners_without_the_parameter(self):
        """Older runners that lack allow_command still run — the grant is additive."""
        from opaihub.ask import run_explicit_model

        class LegacyLoopRunner:
            name = "free-api"
            model = "m"
            last_usage = {}

            def available(self):
                return True

            def complete_with_tools(
                self,
                prompt,
                *,
                project_root,
                allow_edits,
                system=None,
                timeout=60.0,
                cancel=None,
                max_tool_calls=None,
                tool_calling_enabled=True,
                guard=None,
            ):
                return {"text": "ok", "tool_trace": [], "completion_state": "completed"}

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            result = run_explicit_model(
                root,
                "Fetch the issue",
                runner=LegacyLoopRunner(),
                selected_model_id="free:gemini:gemini-3.1-flash-lite",
                allow_edits=True,
                tool_calling_enabled=True,
                record=False,
                allow_command="gh issue view 219",
            )

        self.assertEqual(result["completion_state"], "completed")


if __name__ == "__main__":
    unittest.main()
