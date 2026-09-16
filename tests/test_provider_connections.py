import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vesta.app_state import available_models

# Aliased: the production name starts with `test_`, and importing it unaliased
# makes pytest collect it as a test function (fixture 'account_id' not found).
from vestahub.accounts import (
    account_models,
    connection_for_account,
    test_account_connection as check_account_connection,
)
from vestahub import accounts


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ProviderConnectionTests(unittest.TestCase):
    def test_codex_candidates_include_the_windows_desktop_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_app_data = Path(tmp)
            desktop_cli = (
                local_app_data / "OpenAI" / "Codex" / "bin" / "build" / "codex.exe"
            )
            desktop_cli.parent.mkdir(parents=True)
            desktop_cli.write_text("binary", encoding="utf-8")
            with (
                mock.patch("vestahub.accounts._which", return_value=None),
                mock.patch.dict(
                    "os.environ",
                    {"LOCALAPPDATA": str(local_app_data), "PATH": ""},
                    clear=False,
                ),
            ):
                candidates = accounts._codex_cli_candidates(Path(tmp) / "home")

        self.assertIn(str(desktop_cli.resolve()), candidates)

    def test_auth_artifact_is_detected_but_not_verified(self):
        connection = connection_for_account(
            {
                "id": "claude",
                "label": "Claude",
                "cli_present": True,
                "authenticated": True,
                "connected": True,
            }
        )

        self.assertEqual(connection["authStatus"], "unknown")
        self.assertEqual(connection["credentialSource"], "user_account")
        self.assertEqual(connection["userFacingName"], "Vesta")
        self.assertIn("detected", connection["safeDiagnostic"].lower())

    def test_missing_cli_is_misconfigured(self):
        connection = connection_for_account(
            {
                "id": "codex",
                "label": "Codex",
                "cli_present": False,
                "authenticated": True,
                "connected": False,
            }
        )

        self.assertEqual(connection["authStatus"], "misconfigured")

    def test_missing_auth_is_not_configured(self):
        connection = connection_for_account(
            {
                "id": "claude",
                "label": "Claude",
                "cli_present": True,
                "authenticated": False,
                "connected": False,
            }
        )

        self.assertEqual(connection["authStatus"], "not_configured")

    def test_claude_status_success_is_connected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / ".credentials.json").touch()
            with mock.patch("vestahub.accounts._which", return_value="/bin/claude"):
                connection = check_account_connection(
                    "claude", home=home, run=lambda argv: _Completed(0, "logged in")
                )

        self.assertEqual(connection["authStatus"], "connected")
        self.assertIsNotNone(connection["lastCheckedAt"])
        self.assertIn("sign-in verified locally", connection["safeDiagnostic"].lower())
        self.assertIn("request", connection["safeDiagnostic"].lower())

    def test_claude_status_failure_maps_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / ".credentials.json").touch()
            with mock.patch("vestahub.accounts._which", return_value="/bin/claude"):
                connection = check_account_connection(
                    "claude",
                    home=home,
                    run=lambda argv: _Completed(
                        1, "", "401 Invalid authentication credentials"
                    ),
                )

        self.assertEqual(connection["authStatus"], "invalid")
        self.assertEqual(connection["lastErrorCode"], "AUTH_INVALID")
        self.assertNotIn("401", connection["displayName"])

    def test_zero_exit_not_logged_in_is_not_connected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / ".credentials.json").touch()
            with mock.patch("vestahub.accounts._which", return_value="/bin/claude"):
                connection = check_account_connection(
                    "claude",
                    home=home,
                    run=lambda argv: _Completed(0, '{"loggedIn":false}'),
                )

        self.assertNotEqual(connection["authStatus"], "connected")
        self.assertEqual(connection["lastErrorCode"], "AUTH_INVALID")

    def test_logged_in_json_does_not_classify_identifier_digits_as_http_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / ".credentials.json").touch()
            payload = '{"loggedIn":true,"orgId":"org-401-example"}'
            with mock.patch("vestahub.accounts._which", return_value="/bin/claude"):
                connection = check_account_connection(
                    "claude", home=home, run=lambda argv: _Completed(0, payload)
                )

        self.assertEqual(connection["authStatus"], "connected")
        self.assertIsNone(connection["lastErrorCode"])

    def test_codex_success_status_on_stderr_remains_connected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex").mkdir()
            (home / ".codex" / "auth.json").touch()
            with (
                mock.patch("vestahub.accounts._which", return_value="/bin/codex"),
                mock.patch(
                    "vestahub.accounts._codex_cli_candidates",
                    return_value=["/bin/codex"],
                ),
            ):
                connection = check_account_connection(
                    "codex",
                    home=home,
                    run=lambda argv: _Completed(0, "", "Logged in using ChatGPT"),
                )

        self.assertEqual(connection["authStatus"], "connected")

    def test_forced_codex_check_selects_the_best_authenticated_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            first = home / "first-codex.cmd"
            second = home / "second-codex.cmd"
            first.write_text("old", encoding="utf-8")
            second.write_text("current", encoding="utf-8")
            (home / ".codex").mkdir()
            (home / ".codex" / "auth.json").touch()
            calls: list[list[str]] = []

            def run(argv):
                calls.append(argv)
                if "--version" in argv:
                    return _Completed(
                        0,
                        "codex-cli 0.151.0"
                        if argv[0] == str(second)
                        else "codex-cli 0.128.0",
                    )
                return _Completed(0, "Logged in using ChatGPT")

            with (
                mock.patch("vestahub.accounts._which", return_value=str(first)),
                mock.patch(
                    "vestahub.accounts._codex_cli_candidates",
                    return_value=[str(first), str(second)],
                ),
            ):
                connection = check_account_connection(
                    "codex", home=home, run=run, force=True
                )
            stored = json.loads(
                (home / ".vesta" / "connection_history.json").read_text(encoding="utf-8")
            )

        self.assertEqual(connection["authStatus"], "connected")
        self.assertEqual(
            stored["connections"]["codex"]["selectedCliPath"], str(second.resolve())
        )
        self.assertIn([str(first), "login", "status"], calls)
        self.assertIn([str(second), "login", "status"], calls)

    def test_outdated_codex_cli_keeps_its_verified_sign_in(self):
        calls: list[list[str]] = []

        def run(argv):
            calls.append(argv)
            if "--version" in argv:
                return _Completed(0, "codex-cli 0.128.0")
            return _Completed(0, "Logged in using ChatGPT")

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex").mkdir()
            (home / ".codex" / "auth.json").touch()
            with (
                mock.patch("vestahub.accounts._which", return_value="/bin/codex"),
                mock.patch(
                    "vestahub.accounts._codex_cli_candidates",
                    return_value=["/bin/codex"],
                ),
            ):
                connection = check_account_connection(
                    "codex", home=home, run=run, force=True
                )

        self.assertEqual(connection["authStatus"], "connected")
        self.assertIsNone(connection["lastErrorCode"])
        self.assertEqual(
            calls,
            [
                ["/bin/codex", "login", "status"],
                ["/bin/codex", "--version"],
            ],
        )

    def test_codex_account_type_is_derived_from_safe_status_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex").mkdir()
            (home / ".codex" / "auth.json").touch()
            with mock.patch("vestahub.accounts._which", return_value="/bin/codex"):
                chatgpt = check_account_connection(
                    "codex",
                    home=home,
                    run=lambda argv: _Completed(0, "Logged in using ChatGPT"),
                )
                api_key = check_account_connection(
                    "codex",
                    home=home,
                    run=lambda argv: _Completed(0, "Logged in using API key"),
                    force=True,
                )
                api_key_hyphenated = check_account_connection(
                    "codex",
                    home=home,
                    run=lambda argv: _Completed(0, "Logged in using API-key"),
                    force=True,
                )

        self.assertEqual(chatgpt["accountType"], "chatgpt")
        self.assertEqual(api_key["accountType"], "api_key")
        self.assertEqual(api_key_hyphenated["accountType"], "api_key")

    def test_codex_picker_uses_cli_default_for_chatgpt_account(self):
        account = {
            "id": "codex",
            "label": "Codex",
            "vendor": "OpenAI Codex CLI",
            "cli": "codex",
            "cli_path": "/bin/codex",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "",
        }

        chatgpt = account_models(accounts=[account], account_types={"codex": "chatgpt"})
        api_key = account_models(accounts=[account], account_types={"codex": "api_key"})

        self.assertEqual([option["id"] for option in chatgpt], ["account:codex"])
        self.assertIn("account:codex:gpt-5.6-sol", [option["id"] for option in api_key])

    def test_codex_picker_disables_a_known_outdated_cli(self):
        account = {
            "id": "codex",
            "label": "Codex",
            "vendor": "OpenAI Codex CLI",
            "cli": "codex",
            "cli_path": "/bin/codex",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "",
        }

        with mock.patch(
            "vestahub.accounts._account_cli_version",
            return_value="codex-cli 0.128.0",
        ):
            options = account_models(
                accounts=[account],
                account_types={"codex": "chatgpt"},
                inspect_cli_capabilities=True,
            )

        self.assertEqual(len(options), 1)
        self.assertFalse(options[0]["available"])
        self.assertIn("Update Codex CLI", options[0]["disabled_reason"])

    def test_codex_picker_treats_unrecognized_account_type_as_unknown(self):
        account = {
            "id": "codex",
            "label": "Codex",
            "vendor": "OpenAI Codex CLI",
            "cli": "codex",
            "cli_path": "/bin/codex",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "",
        }

        options = account_models(
            accounts=[account], account_types={"codex": "unrecognized"}
        )

        self.assertEqual([option["id"] for option in options], ["account:codex"])

    def test_available_models_applies_cached_codex_account_type(self):
        account = {
            "id": "codex",
            "label": "Codex",
            "vendor": "OpenAI Codex CLI",
            "cli": "codex",
            "cli_path": "/bin/codex",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "vestahub.accounts.list_connected_accounts", return_value=[account]
                ),
                mock.patch("vestahub.local_runner.list_local_models", return_value=[]),
                mock.patch(
                    "vestahub.accounts.provider_connection_doctor",
                    return_value=[
                        {
                            "providerId": "codex",
                            "authStatus": "connected",
                            "accountType": "chatgpt",
                        }
                    ],
                ),
            ):
                payload = available_models(Path(tmp))

        codex_ids = [
            model["id"]
            for model in payload["models"]
            if model.get("provider") == "codex"
        ]
        self.assertEqual(codex_ids, ["account:codex"])
        codex = next(
            model for model in payload["models"] if model.get("id") == "account:codex"
        )
        self.assertIn("ChatGPT", codex["advanced_label"])

    def test_discovery_publishes_the_signed_in_codex_cli_catalog(self):
        account = {
            "id": "codex",
            "label": "Codex",
            "vendor": "OpenAI Codex CLI",
            "cli": "codex",
            "cli_path": "/bin/codex",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "",
        }
        discovered = [
            ("gpt-5.6-sol", "GPT-5.6 Sol", "best"),
            ("gpt-5.6-terra", "GPT-5.6 Terra", "balanced"),
            ("gpt-5.6-luna", "GPT-5.6 Luna", "fast"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "vestahub.accounts.list_connected_accounts", return_value=[account]
                ),
                mock.patch("vestahub.local_runner.list_local_models", return_value=[]),
                mock.patch(
                    "vestahub.accounts.provider_connection_doctor",
                    return_value=[
                        {
                            "providerId": "codex",
                            "authStatus": "connected",
                            "accountType": "chatgpt",
                        }
                    ],
                ),
                mock.patch(
                    "vestahub.accounts.test_account_connection",
                    return_value={
                        "providerId": "codex",
                        "authStatus": "connected",
                        "accountType": "chatgpt",
                    },
                ) as connection_probe,
                mock.patch(
                    "vestahub.accounts._account_cli_version",
                    return_value="codex-cli 0.151.0",
                ),
                mock.patch(
                    "vestahub.accounts._codex_cli_models", return_value=discovered
                ) as catalog_probe,
            ):
                payload = available_models(Path(tmp), discover_accounts=True)

        connection_probe.assert_called_once_with("codex", force=True)
        catalog_probe.assert_called_once()
        self.assertEqual(
            [
                model["id"]
                for model in payload["models"]
                if model.get("provider") == "codex"
            ],
            [
                "account:codex:gpt-5.6-sol",
                "account:codex:gpt-5.6-terra",
                "account:codex:gpt-5.6-luna",
            ],
        )

    def test_available_models_is_conservative_until_codex_type_is_verified(self):
        account = {
            "id": "codex",
            "label": "Codex",
            "vendor": "OpenAI Codex CLI",
            "cli": "codex",
            "cli_path": "/bin/codex",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "vestahub.accounts.list_connected_accounts", return_value=[account]
                ),
                mock.patch("vestahub.local_runner.list_local_models", return_value=[]),
                mock.patch(
                    "vestahub.accounts.provider_connection_doctor",
                    return_value=[{"providerId": "codex", "authStatus": "connected"}],
                ),
            ):
                payload = available_models(Path(tmp))

        codex_ids = [
            model["id"]
            for model in payload["models"]
            if model.get("provider") == "codex"
        ]
        self.assertEqual(codex_ids, ["account:codex"])
        codex = next(
            model for model in payload["models"] if model.get("id") == "account:codex"
        )
        self.assertNotIn("ChatGPT", codex["advanced_label"])

    def test_copilot_presence_remains_unverified_without_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".copilot").mkdir()
            (home / ".copilot" / "config.json").touch()
            run = mock.Mock()
            with mock.patch("vestahub.accounts._which", return_value="/bin/copilot"):
                connection = check_account_connection("copilot", home=home, run=run)

        run.assert_not_called()
        self.assertEqual(connection["authStatus"], "unknown")
        self.assertIn("no safe status command", connection["safeDiagnostic"].lower())

    def test_model_payload_exposes_normalized_connections(self):
        accounts = [
            {
                "id": "claude",
                "label": "Claude",
                "vendor": "Anthropic Claude Code",
                "cli": "claude",
                "cli_path": "/bin/claude",
                "cli_present": True,
                "authenticated": True,
                "connected": True,
                "login_hint": "Sign in",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "vestahub.accounts.list_connected_accounts", return_value=accounts
                ),
                mock.patch("vestahub.local_runner.list_local_models", return_value=[]),
            ):
                payload = available_models(Path(tmp))

        self.assertEqual(payload["connections"][0]["providerId"], "claude")
        self.assertEqual(payload["connections"][0]["authStatus"], "unknown")

    def test_known_failed_account_is_not_offered_as_available_model(self):
        accounts = [
            {
                "id": "claude",
                "label": "Claude",
                "vendor": "Anthropic Claude Code",
                "cli": "claude",
                "cli_path": "/bin/claude",
                "cli_present": True,
                "authenticated": True,
                "connected": True,
                "login_hint": "Sign in",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "vestahub.accounts.list_connected_accounts", return_value=accounts
                ),
                mock.patch("vestahub.local_runner.list_local_models", return_value=[]),
                mock.patch(
                    "vestahub.accounts.provider_connection_doctor",
                    return_value=[
                        {
                            "providerId": "claude",
                            "authStatus": "expired",
                            "safeDiagnostic": "Sign-in expired.",
                        }
                    ],
                ),
            ):
                payload = available_models(Path(tmp))

        claude = [
            model
            for model in payload["models"]
            if model["id"] == "account:claude:haiku"
        ][0]
        self.assertFalse(claude["available"])
        self.assertEqual(claude["disabled_reason"], "Sign-in expired.")

    def test_account_picker_has_provider_prefixed_labels(self):
        account = {
            "id": "claude",
            "label": "Claude",
            "vendor": "Anthropic Claude Code",
            "cli": "claude",
            "cli_path": "/bin/claude",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "Sign in",
        }
        with mock.patch(
            "vestahub.accounts.list_connected_accounts", return_value=[account]
        ):
            options = account_models()

        # Labels now use provider-prefixed format, not Vesta generic labels
        self.assertTrue(
            all(option["label"].startswith("Claude ·") for option in options),
            f"Expected all labels to start with 'Claude ·', got: {[o['label'] for o in options]}",
        )
        self.assertIn("Claude", options[0]["advanced_label"])

    def test_account_options_include_group_field(self):
        account = {
            "id": "claude",
            "label": "Claude",
            "vendor": "Anthropic Claude Code",
            "cli": "claude",
            "cli_path": "/bin/claude",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "Sign in",
        }
        with mock.patch(
            "vestahub.accounts.list_connected_accounts", return_value=[account]
        ):
            options = account_models()

        for opt in options:
            self.assertEqual(
                opt.get("group"),
                "claude",
                f"Option '{opt['id']}' missing group='claude'",
            )

    def test_codex_options_have_codex_group(self):
        account = {
            "id": "codex",
            "label": "Codex",
            "vendor": "OpenAI Codex CLI",
            "cli": "codex",
            "cli_path": "/bin/codex",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "Sign in",
        }
        with mock.patch(
            "vestahub.accounts.list_connected_accounts", return_value=[account]
        ):
            options = account_models()

        for opt in options:
            self.assertEqual(opt.get("group"), "codex")

    def test_copilot_options_have_copilot_group(self):
        account = {
            "id": "copilot",
            "label": "Copilot",
            "vendor": "GitHub Copilot CLI",
            "cli": "copilot",
            "cli_path": "/bin/copilot",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "Sign in",
        }
        with mock.patch(
            "vestahub.accounts.list_connected_accounts", return_value=[account]
        ):
            options = account_models()

        for opt in options:
            self.assertEqual(opt.get("group"), "copilot")

    def test_available_models_includes_free_group(self):
        """available_models() must include free models with group='free'."""
        accounts = [
            {
                "id": "claude",
                "label": "Claude",
                "vendor": "Anthropic Claude Code",
                "cli": "claude",
                "cli_path": "/bin/claude",
                "cli_present": True,
                "authenticated": True,
                "connected": True,
                "login_hint": "Sign in",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "vestahub.accounts.list_connected_accounts", return_value=accounts
                ),
                mock.patch("vestahub.local_runner.list_local_models", return_value=[]),
            ):
                payload = available_models(Path(tmp))

        all_models = payload["models"]
        free_models = [m for m in all_models if m.get("group") == "free"]
        self.assertGreater(
            len(free_models), 0, "No free models found in available_models()"
        )
        # All free models must have kind='free'
        for m in free_models:
            self.assertEqual(m.get("kind"), "free", f"{m['id']} has wrong kind")

    def test_available_models_auto_has_routing_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("vestahub.accounts.list_connected_accounts", return_value=[]),
                mock.patch("vestahub.local_runner.list_local_models", return_value=[]),
            ):
                payload = available_models(Path(tmp))

        auto_opts = [m for m in payload["models"] if m.get("id") == "auto"]
        self.assertEqual(len(auto_opts), 1)
        self.assertEqual(auto_opts[0].get("group"), "routing")

    def test_available_models_local_has_local_group(self):
        fake_local = [
            {
                "id": "ollama:llama3",
                "model": "llama3",
                "provider": "ollama",
                "endpoint": "http://localhost:11434",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("vestahub.accounts.list_connected_accounts", return_value=[]),
                mock.patch(
                    "vestahub.local_runner.list_local_models", return_value=fake_local
                ),
            ):
                payload = available_models(Path(tmp))

        local_opts = [m for m in payload["models"] if m.get("group") == "local"]
        self.assertEqual(len(local_opts), 1)
        self.assertEqual(local_opts[0]["id"], "ollama:llama3")


if __name__ == "__main__":
    unittest.main()
