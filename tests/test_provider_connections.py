import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opai.app_state import available_models
from opaihub.accounts import (
    account_models,
    connection_for_account,
    test_account_connection,
)


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ProviderConnectionTests(unittest.TestCase):
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
        self.assertEqual(connection["userFacingName"], "OPai")
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
            with mock.patch("opaihub.accounts._which", return_value="/bin/claude"):
                connection = test_account_connection(
                    "claude", home=home, run=lambda argv: _Completed(0, "logged in")
                )

        self.assertEqual(connection["authStatus"], "connected")
        self.assertIsNotNone(connection["lastCheckedAt"])
        self.assertEqual(connection["safeDiagnostic"], "Connection verified locally.")

    def test_claude_status_failure_maps_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / ".credentials.json").touch()
            with mock.patch("opaihub.accounts._which", return_value="/bin/claude"):
                connection = test_account_connection(
                    "claude",
                    home=home,
                    run=lambda argv: _Completed(
                        1, "", "401 Invalid authentication credentials"
                    ),
                )

        self.assertEqual(connection["authStatus"], "invalid")
        self.assertEqual(connection["lastErrorCode"], "AUTH_INVALID")
        self.assertNotIn("401", connection["displayName"])

    def test_copilot_presence_remains_unverified_without_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".copilot").mkdir()
            (home / ".copilot" / "config.json").touch()
            run = mock.Mock()
            with mock.patch("opaihub.accounts._which", return_value="/bin/copilot"):
                connection = test_account_connection("copilot", home=home, run=run)

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
                    "opaihub.accounts.list_connected_accounts", return_value=accounts
                ),
                mock.patch("opaihub.local_runner.list_local_models", return_value=[]),
            ):
                payload = available_models(Path(tmp))

        self.assertEqual(payload["connections"][0]["providerId"], "claude")
        self.assertEqual(payload["connections"][0]["authStatus"], "unknown")

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
            "opaihub.accounts.list_connected_accounts", return_value=[account]
        ):
            options = account_models()

        # Labels now use provider-prefixed format, not OPai generic labels
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
            "opaihub.accounts.list_connected_accounts", return_value=[account]
        ):
            options = account_models()

        for opt in options:
            self.assertEqual(
                opt.get("group"), "claude",
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
            "opaihub.accounts.list_connected_accounts", return_value=[account]
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
            "opaihub.accounts.list_connected_accounts", return_value=[account]
        ):
            options = account_models()

        for opt in options:
            self.assertEqual(opt.get("group"), "copilot")


if __name__ == "__main__":
    unittest.main()
