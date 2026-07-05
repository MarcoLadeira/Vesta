from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opaihub.accounts import (
    disconnect_account,
    interactive_provider_login,
    provider_connection_doctor,
    test_account_connection as check_account_connection,
)


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _LoginProcess:
    returncode = 0

    def __init__(self):
        self.wait_timeout = None

    def wait(self, timeout=None):
        self.wait_timeout = timeout
        return self.returncode


def account(
    provider="claude", *, installed=True, authenticated=True, cli_path="/bin/claude"
):
    return {
        "id": provider,
        "label": provider.title(),
        "vendor": "Test vendor",
        "cli": provider,
        "cli_path": cli_path if installed else None,
        "cli_present": installed,
        "authenticated": authenticated,
        "connected": installed and authenticated,
        "login_hint": f"Sign in to {provider}",
    }


class ConnectionDoctorTests(unittest.TestCase):
    def test_doctor_aggregates_account_and_api_diagnostics_without_secret_values(self):
        secret = "must-" + "never-render"
        entries = provider_connection_doctor(
            accounts=[account()],
            connections=[
                {
                    "providerId": "claude",
                    "displayName": "Claude",
                    "authStatus": "invalid",
                    "credentialSource": "user_account",
                    "lastCheckedAt": 1234,
                    "lastError": "Session expired.",
                    "lastErrorCode": "AUTH_EXPIRED",
                    "error": {
                        "recoveryActions": ["reconnect", "open_settings"],
                        "technicalMessage": "to" + f"ken={secret}",
                    },
                    "safeDiagnostic": "Sign-in expired.",
                    "cliPresent": True,
                    "detected": True,
                    "loginHint": "Sign in to Claude",
                    "envOverridesRemoved": ["ANTHROPIC_API_KEY", "CLAUDECODE"],
                }
            ],
            credentials=[
                {
                    "provider": "groq",
                    "configured": True,
                    "source": "environment",
                    "envKey": "GROQ_API_KEY",
                    "keychainAvailable": True,
                    "value": secret,
                }
            ],
            version_run=lambda argv: _Completed(stdout="Claude Code 9.8.7\n"),
        )

        claude = next(item for item in entries if item["providerId"] == "claude")
        groq = next(item for item in entries if item["providerId"] == "groq")
        self.assertEqual(claude["health"], "failed")
        self.assertEqual(claude["cliVersion"], "Claude Code 9.8.7")
        self.assertEqual(claude["credentialSourceLabel"], "Subscription sign-in")
        self.assertEqual(
            claude["envOverridesRemoved"], ["ANTHROPIC_API_KEY", "CLAUDECODE"]
        )
        self.assertEqual(
            claude["recoveryActions"], ["reconnect", "open_settings", "sign_in"]
        )
        self.assertEqual(groq["health"], "detected")
        self.assertEqual(groq["credentialSourceLabel"], "Environment variable")
        self.assertEqual(groq["credentialEnvironmentName"], "GROQ_API_KEY")
        rendered = json.dumps(entries)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("cli_path", rendered)

    def test_doctor_maps_install_and_detection_states(self):
        entries = provider_connection_doctor(
            accounts=[
                account("claude", installed=False, authenticated=False),
                account(
                    "codex", installed=True, authenticated=True, cli_path="/bin/codex"
                ),
            ],
            connections=[
                {
                    "providerId": "claude",
                    "authStatus": "not_configured",
                    "cliPresent": False,
                },
                {
                    "providerId": "codex",
                    "authStatus": "unknown",
                    "cliPresent": True,
                    "detected": True,
                },
            ],
            credentials=[],
            version_run=lambda argv: _Completed(stdout="codex 1.2.3"),
        )

        by_id = {item["providerId"]: item for item in entries}
        self.assertEqual(by_id["claude"]["health"], "not_installed")
        self.assertEqual(by_id["codex"]["health"], "detected")
        self.assertEqual(by_id["codex"]["cliVersion"], "codex 1.2.3")

    def test_latest_failed_check_is_available_to_doctor_without_reprobing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / ".credentials.json").touch()
            with mock.patch("opaihub.accounts._which", return_value="/bin/claude"):
                check_account_connection(
                    "claude",
                    home=home,
                    run=lambda argv: _Completed(
                        1, stderr="401 Invalid authentication credentials"
                    ),
                )
                entries = provider_connection_doctor(
                    home=home,
                    accounts=[account()],
                    connections=[
                        {
                            "providerId": "claude",
                            "authStatus": "unknown",
                            "cliPresent": True,
                            "detected": True,
                        }
                    ],
                    version_run=lambda argv: _Completed(stdout="claude 1.0"),
                    include_history=True,
                )

        claude = next(item for item in entries if item["providerId"] == "claude")
        self.assertEqual(claude["lastErrorCode"], "AUTH_INVALID")
        self.assertEqual(claude["health"], "failed")
        self.assertTrue(claude["lastCheckedAt"])

    def test_successful_disconnect_clears_stale_failure_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / ".credentials.json").touch()
            with (
                mock.patch("opaihub.accounts._which", return_value="/bin/claude"),
                mock.patch(
                    "opaihub.accounts._hidden_run",
                    return_value=_Completed(returncode=0),
                ),
            ):
                check_account_connection(
                    "claude",
                    home=home,
                    run=lambda argv: _Completed(
                        1, stderr="401 Invalid authentication credentials"
                    ),
                )
                self.assertTrue(disconnect_account("claude", home=home)["disconnected"])
                entries = provider_connection_doctor(
                    home=home,
                    version_run=lambda argv: _Completed(stdout="claude 1.0"),
                )

        claude = next(item for item in entries if item["providerId"] == "claude")
        self.assertNotEqual(claude["health"], "failed")
        self.assertEqual(claude["lastErrorCode"], "")

    def test_changed_local_credential_evidence_supersedes_stale_history_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch("opaihub.accounts._which", return_value="/bin/claude"):
                check_account_connection("claude", home=home)
                (home / ".claude").mkdir()
                (home / ".claude" / ".credentials.json").touch()
                entries = provider_connection_doctor(
                    home=home,
                    version_run=lambda argv: _Completed(stdout="claude 1.0"),
                )

        claude = next(item for item in entries if item["providerId"] == "claude")
        self.assertEqual(claude["health"], "detected")
        self.assertEqual(claude["authStatus"], "unknown")


class InteractiveProviderLoginTests(unittest.TestCase):
    def test_windows_login_uses_fixed_argv_sanitized_env_and_forced_probe(self):
        process = _LoginProcess()
        launched = []
        probes = []

        def popen(argv, **kwargs):
            launched.append((argv, kwargs))
            return process

        def probe(provider, *, home=None, force=False):
            probes.append((provider, home, force))
            return {"providerId": provider, "authStatus": "connected", "detected": True}

        with mock.patch(
            "opaihub.accounts.provider_child_env",
            return_value=({"PATH": "safe"}, ["ANTHROPIC_API_KEY", "CLAUDECODE"]),
        ):
            result = interactive_provider_login(
                "claude",
                popen=popen,
                probe=probe,
                platform_name="win32",
                which=lambda name: "C:/tools/claude.exe",
                timeout=321,
            )

        argv, kwargs = launched[0]
        self.assertEqual(argv, ["C:/tools/claude.exe", "auth", "login"])
        self.assertEqual(kwargs["env"], {"PATH": "safe"})
        self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NEW_CONSOLE)
        self.assertNotIn("stdout", kwargs)
        self.assertEqual(process.wait_timeout, 321)
        self.assertEqual(probes, [("claude", None, True)])
        self.assertTrue(result["signedIn"])
        self.assertEqual(
            result["envOverridesRemoved"], ["ANTHROPIC_API_KEY", "CLAUDECODE"]
        )

    def test_each_provider_uses_only_its_registered_login_command(self):
        expected = {
            "claude": ["auth", "login"],
            "codex": ["login"],
            "copilot": ["login"],
        }
        for provider, args in expected.items():
            with self.subTest(provider=provider):
                commands = []
                result = interactive_provider_login(
                    provider,
                    popen=lambda argv, **kwargs: (
                        commands.append(argv) or _LoginProcess()
                    ),
                    probe=lambda *a, **k: {
                        "providerId": provider,
                        "authStatus": "connected",
                        "detected": True,
                    },
                    platform_name="win32",
                    which=lambda name: f"C:/tools/{name}.exe",
                )
                self.assertEqual(commands[0], [f"C:/tools/{provider}.exe", *args])
                self.assertTrue(result["signedIn"])

    def test_unknown_and_missing_providers_fail_before_spawning(self):
        popen = mock.Mock()
        unknown = interactive_provider_login("other", popen=popen)
        missing = interactive_provider_login(
            "claude", popen=popen, which=lambda name: None
        )

        self.assertEqual(unknown["errorCode"], "PROVIDER_UNKNOWN")
        self.assertEqual(missing["errorCode"], "CLI_NOT_INSTALLED")
        popen.assert_not_called()

    def test_unverified_post_login_probe_never_reports_success(self):
        result = interactive_provider_login(
            "codex",
            popen=lambda argv, **kwargs: _LoginProcess(),
            probe=lambda *a, **k: {
                "providerId": "codex",
                "authStatus": "not_configured",
                "detected": False,
            },
            platform_name="win32",
            which=lambda name: "C:/tools/codex.exe",
        )

        self.assertFalse(result["signedIn"])
        self.assertEqual(result["status"], "not_verified")

    def test_verified_probe_wins_when_login_cli_exits_nonzero(self):
        process = _LoginProcess()
        process.returncode = 1
        result = interactive_provider_login(
            "codex",
            popen=lambda argv, **kwargs: process,
            probe=lambda *a, **k: {
                "providerId": "codex",
                "authStatus": "connected",
                "detected": True,
            },
            platform_name="win32",
            which=lambda name: "C:/tools/codex.exe",
        )

        self.assertTrue(result["signedIn"])
        self.assertEqual(result["returncode"], 1)

    def test_cancelling_login_terminates_child_without_probing(self):
        cancel = threading.Event()
        cancel.set()
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        probe = mock.Mock()

        result = interactive_provider_login(
            "claude",
            popen=lambda argv, **kwargs: process,
            probe=probe,
            platform_name="win32",
            which=lambda name: "C:/tools/claude.exe",
            cancel=cancel,
        )

        self.assertEqual(result["status"], "cancelled")
        process.terminate.assert_called_once()
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
