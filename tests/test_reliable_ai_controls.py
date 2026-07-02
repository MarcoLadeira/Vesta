from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class _MemoryKeyring:
    priority = 1

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class ProviderAdapterRegressionTests(unittest.TestCase):
    def test_adapter_registry_covers_every_supported_execution_surface(self) -> None:
        from opaihub.provider_adapters import adapter_for

        providers = {
            "claude",
            "codex",
            "copilot",
            "gemini",
            "groq",
            "mistral",
            "ollama",
            "openai-compatible",
        }
        adapters = {provider: adapter_for(provider) for provider in providers}
        self.assertEqual(set(adapters), providers)
        self.assertTrue(all(callable(adapter.probe) for adapter in adapters.values()))
        self.assertTrue(
            all(callable(adapter.extract_usage) for adapter in adapters.values())
        )

    def test_free_provider_connection_probe_sends_no_prompt_or_secret_back(
        self,
    ) -> None:
        from opaihub.provider_adapters import test_free_provider_connection

        class Store:
            def get(self, provider):
                return "private-key"

            def status(self, provider):
                return {"provider": provider, "configured": True, "source": "keychain"}

        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        response.__enter__.return_value.read.return_value = b'{"data": []}'
        opener = mock.Mock(return_value=response)

        result = test_free_provider_connection("groq", store=Store(), opener=opener)

        request = opener.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertTrue(request.full_url.endswith("/models"))
        self.assertNotIn("prompt", request.full_url)
        self.assertTrue(result["connected"])
        self.assertNotIn("private-key", repr(result))

    def test_codex_global_approval_flag_precedes_exec(self) -> None:
        from opaihub.accounts import AccountRunner

        command = AccountRunner("codex", "codex", model="gpt-5.4-mini").build_command(
            "explain", mode="ask", out_file="answer.txt"
        )

        self.assertLess(command.index("--ask-for-approval"), command.index("exec"))
        self.assertEqual(command[command.index("--ask-for-approval") + 1], "on-request")
        self.assertIn("--output-last-message", command)

    def test_codex_config_repair_backs_up_and_removes_only_invalid_tier(self) -> None:
        from opaihub.accounts import codex_config_issue, repair_codex_config

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".codex" / "config.toml"
            config.parent.mkdir()
            config.write_text(
                'model = "gpt-5.4"\nservice_tier = "default"\napproval_policy = "never"\n',
                encoding="utf-8",
            )
            issue = codex_config_issue(home)
            repaired = repair_codex_config(home)

            self.assertTrue(issue["repairable"])
            self.assertTrue(Path(repaired["backupPath"]).exists())
            content = config.read_text(encoding="utf-8")

        self.assertNotIn('service_tier = "default"', content)
        self.assertIn('model = "gpt-5.4"', content)
        self.assertIn('approval_policy = "never"', content)

    def test_safe_auth_probe_is_cached_and_force_refreshable(self) -> None:
        from opaihub.accounts import test_account_connection

        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth = home / ".claude" / ".credentials.json"
            auth.parent.mkdir()
            auth.write_text("{}", encoding="utf-8")
            with (
                mock.patch("opaihub.accounts._which", return_value="claude"),
                mock.patch(
                    "opaihub.accounts._hidden_run", return_value=completed
                ) as run,
            ):
                test_account_connection("claude", home=home)
                test_account_connection("claude", home=home)
                self.assertEqual(run.call_count, 1)
                test_account_connection("claude", home=home, force=True)
                self.assertEqual(run.call_count, 2)

    def test_free_provider_failure_is_normalized_and_secret_safe(self) -> None:
        from opai.app_state import ask

        class Runner:
            def available(self):
                return True

            def complete(self, prompt, *, system=None, timeout=60):
                raise RuntimeError("401 Invalid authentication token=private-secret")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "opaihub.local_runner.runner_for_model", return_value=Runner()
            ):
                result = ask(
                    Path(tmp),
                    "hello",
                    model_choice="free:groq:openai/gpt-oss-120b",
                    allow_cloud=True,
                )

        self.assertEqual(result["status"], "runner_error")
        self.assertEqual(result["error"]["code"], "AUTH_INVALID")
        self.assertNotIn("private-secret", repr(result))

    def test_free_provider_structured_error_reaches_gui_contract(self) -> None:
        from opaihub.gui_pipeline import handle_gui_message

        error = {
            "code": "PROVIDER_RATE_LIMITED",
            "title": "OPai is being rate limited.",
            "userMessage": "Wait, then retry.",
            "recoveryActions": ["retry"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "opai.app_state.ask",
                return_value={
                    "status": "runner_error",
                    "answer": "Wait, then retry.",
                    "error": error,
                },
            ):
                result = handle_gui_message(
                    Path(tmp),
                    "hello",
                    model_id="free:groq:openai/gpt-oss-120b",
                    mode="ask",
                    allow_cloud=True,
                )

        self.assertEqual(result["error"]["code"], "PROVIDER_RATE_LIMITED")


class CredentialStoreTests(unittest.TestCase):
    def test_environment_wins_and_status_never_contains_secret(self) -> None:
        from opaihub.credentials import CredentialStore

        backend = _MemoryKeyring()
        backend.set_password("OPai/free-model-api", "groq", "keychain-secret")
        store = CredentialStore(
            backend=backend,
            environ={"GROQ_API_KEY": "environment-secret"},
        )

        self.assertEqual(store.get("groq"), "environment-secret")
        status = store.status("groq")
        self.assertEqual(status["source"], "environment")
        self.assertTrue(status["configured"])
        self.assertNotIn("environment-secret", repr(status))
        self.assertNotIn("keychain-secret", repr(status))

    def test_keychain_round_trip_and_delete(self) -> None:
        from opaihub.credentials import CredentialStore

        store = CredentialStore(backend=_MemoryKeyring(), environ={})
        saved = store.set("mistral", "  private-value  ")
        self.assertTrue(saved["configured"])
        self.assertEqual(saved["source"], "keychain")
        self.assertEqual(store.get("mistral"), "private-value")
        self.assertFalse(store.delete("mistral")["configured"])

    def test_insecure_or_missing_backend_fails_closed(self) -> None:
        from opaihub.credentials import CredentialStore, CredentialStoreUnavailable

        backend = _MemoryKeyring()
        backend.priority = 0
        store = CredentialStore(backend=backend, environ={})
        with self.assertRaises(CredentialStoreUnavailable):
            store.set("gemini", "secret")

    def test_free_registry_and_runner_read_keychain_credentials(self) -> None:
        from opaihub.free_models import list_free_models
        from opaihub.local_runner import runner_for_model

        with (
            mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}),
            mock.patch(
                "opaihub.credentials.CredentialStore.get",
                return_value="keychain-secret",
            ),
        ):
            groq = next(
                model for model in list_free_models() if model["provider"] == "groq"
            )
            runner = runner_for_model(groq["id"])

        self.assertTrue(groq["available"])
        self.assertTrue(runner.available())
        self.assertNotIn("keychain-secret", repr(groq))


class UsageSnapshotTests(unittest.TestCase):
    def test_model_call_metadata_rolls_up_without_breaking_old_events(self) -> None:
        from opaihub.ledger import record_event, record_model_call
        from opaihub.usage import build_usage_snapshots

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_event(
                root,
                "model_call",
                model_tier="L1",
                provider_type="local",
                tokens=7,
            )
            record_model_call(
                root,
                "hello",
                model_tier="L2",
                provider_type="cloud",
                tokens=30,
                input_tokens=20,
                output_tokens=10,
                confirmed=True,
                model_id="free:groq:openai/gpt-oss-120b",
                provider_id="groq",
                measurement="provider",
                quota_snapshot={
                    "metric": "requests",
                    "limit": 1000,
                    "remaining": 749,
                    "window": "day",
                },
            )

            snapshots = build_usage_snapshots(
                root,
                [
                    {
                        "id": "free:groq:openai/gpt-oss-120b",
                        "provider": "groq",
                    },
                    {"id": "auto", "provider": "opai"},
                ],
                limits={"auto": {"metric": "tokens", "limit": 10, "window": "month"}},
            )

        groq = snapshots[0]
        self.assertEqual(groq["source"], "provider")
        self.assertEqual(groq["used"], 251)
        self.assertEqual(groq["percent"], 25.1)
        self.assertEqual(groq["confidence"], "provider-reported")
        auto = snapshots[1]
        self.assertEqual(auto["source"], "opai")
        self.assertEqual(auto["used"], 0)
        self.assertEqual(auto["limit"], 10)
        self.assertFalse(auto["requiresConfirmation"])

    def test_free_runner_extracts_exact_usage_and_groq_quota_headers(self) -> None:
        from opaihub.local_runner import FreeAPIRunner

        class Payload(dict):
            response_headers = {
                "x-ratelimit-limit-requests": "1000",
                "x-ratelimit-remaining-requests": "749",
                "x-ratelimit-reset-requests": "4h",
            }

        payload = Payload(
            choices=[{"message": {"content": "answer"}}],
            usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        )
        runner = FreeAPIRunner("https://api.groq.com/openai/v1", "model", "secret")
        with mock.patch("opaihub.local_runner._http_json", return_value=payload):
            self.assertEqual(runner.complete("hello"), "answer")

        self.assertEqual(runner.last_usage["input_tokens"], 12)
        self.assertEqual(runner.last_usage["output_tokens"], 8)
        self.assertEqual(runner.last_usage["measurement"], "provider")
        self.assertEqual(runner.last_usage["quota_snapshot"]["remaining"], 749)
        self.assertEqual(runner.last_usage["quota_snapshot"]["resetsAt"], "4h")

    def test_limit_gate_requires_confirmation_at_one_hundred_percent(self) -> None:
        from opaihub.ledger import record_model_call
        from opaihub.usage import usage_limit_gate

        model_id = "account:claude:haiku"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_model_call(
                root,
                "hello",
                model_tier="L3",
                provider_type="cloud",
                tokens=100,
                confirmed=True,
                model_id=model_id,
            )
            gate = usage_limit_gate(
                root,
                model_id,
                provider="claude",
                limits={
                    model_id: {"metric": "tokens", "limit": 100, "window": "month"}
                },
            )

        self.assertTrue(gate["requiresConfirmation"])
        self.assertEqual(gate["percent"], 100.0)

    def test_confirmed_free_call_records_model_and_exact_usage(self) -> None:
        from opai.app_state import ask
        from opaihub.ledger import read_events

        class Runner:
            last_usage = {
                "tokens": 20,
                "input_tokens": 12,
                "output_tokens": 8,
                "measurement": "provider",
                "quota_snapshot": {
                    "metric": "requests",
                    "limit": 100,
                    "remaining": 90,
                    "window": "day",
                },
            }

            def available(self):
                return True

        model_id = "free:groq:openai/gpt-oss-120b"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch(
                    "opaihub.local_runner.runner_for_model", return_value=Runner()
                ),
                mock.patch(
                    "opaihub.ask.run_ask",
                    return_value={"status": "answered_locally", "answer": "ok"},
                ),
            ):
                result = ask(root, "hello", model_choice=model_id, allow_cloud=True)
            calls = [
                event
                for event in read_events(root)
                if event.get("event_type") == "model_call"
            ]

        self.assertEqual(result["status"], "answered_by_free_api")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model_id"], model_id)
        self.assertEqual(calls[0]["input_tokens"], 12)
        self.assertEqual(calls[0]["measurement"], "provider")

    def test_soft_limit_only_counts_events_inside_its_window(self) -> None:
        from opaihub.ledger import record_event
        from opaihub.usage import build_usage_snapshots

        model_id = "account:codex:gpt-5.4-mini"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_event(
                root,
                "model_call",
                model_id=model_id,
                tokens=900,
                created_at="2020-01-01T00:00:00+00:00",
            )
            snapshot = build_usage_snapshots(
                root,
                [{"id": model_id, "provider": "codex"}],
                limits={
                    model_id: {"metric": "tokens", "limit": 1000, "window": "month"}
                },
            )[0]

        self.assertEqual(snapshot["used"], 0)
        self.assertFalse(snapshot["requiresConfirmation"])

    def test_request_limits_count_calls_not_tokens(self) -> None:
        from opaihub.ledger import record_model_call
        from opaihub.usage import build_usage_snapshots

        model_id = "account:copilot:gpt-5.2"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for task in ("one", "two"):
                record_model_call(
                    root,
                    task,
                    model_tier="L3",
                    provider_type="cloud",
                    tokens=500,
                    confirmed=True,
                    model_id=model_id,
                )
            snapshot = build_usage_snapshots(
                root,
                [{"id": model_id, "provider": "copilot"}],
                limits={model_id: {"metric": "requests", "limit": 10, "window": "day"}},
            )[0]

        self.assertEqual(snapshot["used"], 2)


class FastPayloadTests(unittest.TestCase):
    def test_boot_and_settings_do_not_probe_local_endpoints(self) -> None:
        from opai.gui_web import boot_payload, settings_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "opaihub.local_runner.list_local_models",
                side_effect=AssertionError("synchronous local discovery"),
            ):
                boot = boot_payload(root)
                settings = settings_payload(root)

        self.assertIn("models", boot)
        self.assertIn("usage", settings)

    def test_local_discovery_uses_a_short_nonblocking_timeout(self) -> None:
        from opaihub.local_runner import list_local_models

        timeouts: list[float] = []

        def unavailable(url: str, **kwargs):
            timeouts.append(float(kwargs["timeout"]))
            raise OSError("offline")

        with mock.patch("opaihub.local_runner._http_json", side_effect=unavailable):
            self.assertEqual(list_local_models(Path.cwd()), [])

        self.assertTrue(timeouts)
        self.assertLessEqual(max(timeouts), 0.25)

    def test_fast_catalog_reuses_background_local_discovery(self) -> None:
        from opai.app_state import available_models
        from opaihub.local_runner import cache_local_models

        local = {
            "id": "ollama:qwen",
            "provider": "ollama",
            "model": "qwen",
            "endpoint": "http://127.0.0.1:11434",
        }
        cache_local_models([local])
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch(
                    "opaihub.local_runner.list_local_models",
                    side_effect=AssertionError("fast catalog probed network"),
                ):
                    catalog = available_models(Path(tmp), discover_local=False)
        finally:
            cache_local_models([])

        self.assertIn("ollama:qwen", {model["id"] for model in catalog["models"]})


class PreferenceLimitTests(unittest.TestCase):
    def test_usage_limit_preferences_migrate_and_validate(self) -> None:
        from opaihub.gui_preferences import (
            load_gui_preferences,
            save_gui_preferences,
            save_usage_limit,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_gui_preferences(root, {"default_model": "auto"})
            prefs = save_usage_limit(
                root,
                "account:claude:haiku",
                metric="tokens",
                limit=5000,
                window="month",
            )
            loaded = load_gui_preferences(root)

        self.assertGreaterEqual(prefs["schema_version"], 2)
        self.assertEqual(loaded["usage_limits"]["account:claude:haiku"]["limit"], 5000)
        self.assertEqual(loaded["default_model"], "auto")


class AutoFallbackTests(unittest.TestCase):
    def test_auto_requests_named_confirmation_before_cloud_fallback(self) -> None:
        from opaihub.gui_pipeline import handle_gui_message

        models = {
            "models": [
                {
                    "id": "free:groq:openai/gpt-oss-120b",
                    "label": "Groq · GPT-OSS 120B (free tier)",
                    "provider": "groq",
                    "kind": "free",
                    "available": True,
                }
            ]
        }
        no_local = {"status": "no_local_model", "hint": "none"}
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("opaihub.ask.run_ask", return_value=no_local),
                mock.patch("opai.app_state.available_models", return_value=models),
            ):
                result = handle_gui_message(
                    Path(tmp), "explain", model_id="auto", mode="ask"
                )

        self.assertEqual(result["status"], "needs_auto_confirmation")
        self.assertEqual(result["fallbackModelId"], models["models"][0]["id"])
        self.assertIn("Groq", result["answer"])
        self.assertFalse(result["cloudStarted"])

    def test_reaching_soft_limit_requires_confirmation_before_provider_call(
        self,
    ) -> None:
        from opaihub.gui_pipeline import handle_gui_message
        from opaihub.gui_preferences import save_usage_limit
        from opaihub.ledger import record_model_call

        model_id = "account:claude:haiku"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_usage_limit(root, model_id, metric="tokens", limit=10, window="month")
            record_model_call(
                root,
                "earlier",
                model_tier="L3",
                provider_type="cloud",
                tokens=10,
                confirmed=True,
                model_id=model_id,
                provider_id="claude",
            )
            with mock.patch("opai.app_state.ask") as provider_call:
                result = handle_gui_message(
                    root, "continue", model_id=model_id, mode="ask"
                )
                provider_call.assert_not_called()

        self.assertEqual(result["status"], "needs_limit_confirmation")
        self.assertEqual(result["usage"]["percent"], 100.0)


if __name__ == "__main__":
    unittest.main()
