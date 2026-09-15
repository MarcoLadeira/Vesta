from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from vesta.app_state import available_models


class ModelConnectionTruthTests(TestCase):
    def test_picker_connections_use_the_same_verified_snapshot_as_model_health(self):
        account = {
            "id": "codex",
            "label": "Codex",
            "vendor": "OpenAI Codex CLI",
            "cli": "codex",
            "cli_path": "/bin/codex",
            "cli_present": True,
            "authenticated": True,
            "connected": True,
            "login_hint": "Sign in to Codex",
        }
        verified = {
            "providerId": "codex",
            "displayName": "Codex",
            "authStatus": "connected",
            "accountType": "chatgpt",
            "credentialSource": "user_account",
            "lastCheckedAt": 1_700_000_000,
            "safeDiagnostic": "Sign-in verified locally.",
            "cliPresent": True,
            "detected": True,
            "loginHint": "Sign in to Codex",
            "envOverridesRemoved": [],
        }

        with TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "vestahub.accounts.list_connected_accounts", return_value=[account]
                ),
                mock.patch(
                    "vestahub.accounts.provider_connection_doctor",
                    return_value=[verified],
                ),
                mock.patch("vestahub.local_runner.list_local_models", return_value=[]),
            ):
                payload = available_models(Path(tmp))

        connection = payload["connections"][0]
        self.assertEqual(connection["providerId"], "codex")
        self.assertEqual(connection["authStatus"], "connected")
        self.assertEqual(connection["accountType"], "chatgpt")
        self.assertEqual(connection["lastCheckedAt"], 1_700_000_000)
        self.assertEqual(connection["safeDiagnostic"], "Sign-in verified locally.")
