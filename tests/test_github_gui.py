"""GUI GitHub connect + consent bridge payloads (#300).

The Bridge is closure-local, so the Qt-free payload helpers are the test seam —
same pattern as scaffold_app_payload. Hermetic: HTTP is injected, the keychain
is faked, and HOME is isolated. The token must never appear in any payload.
"""

from __future__ import annotations

import contextlib
import json
import unittest
from unittest import mock

from opaihub import github_connector as gc
from opai.gui_web import (
    github_connect_payload,
    github_disconnect_payload,
    github_set_push_payload,
    github_status_payload,
)

from tests._helpers import isolated_home


class _FakeStore:
    saved: dict[str, str] = {}

    def get(self, provider):
        return self.saved.get(provider)

    def set(self, provider, secret):
        self.saved[provider] = secret
        return {"stored": True}

    def delete(self, provider):
        return {"deleted": self.saved.pop(provider, None) is not None}


def _http_ok(login="octocat"):
    def http(method, url, token, payload):
        assert url.startswith("https://api.github.com/")
        return 200, {"login": login}

    return http


class GithubGuiPayloadTests(unittest.TestCase):
    def setUp(self):
        _FakeStore.saved = {}
        self._ctx = contextlib.ExitStack()
        self._ctx.enter_context(isolated_home())
        self._ctx.enter_context(mock.patch.object(gc, "CredentialStore", _FakeStore))
        self._ctx.enter_context(
            mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""})
        )
        self.addCleanup(self._ctx.close)

    def test_status_payload_reports_readiness(self):
        payload = github_status_payload()
        self.assertFalse(payload["connected"])
        self.assertIn("ready_for_push", payload)
        self.assertFalse(payload["ready_for_push"])

    def test_connect_validates_and_never_echoes_the_token(self):
        payload = github_connect_payload("ghp_secret_value", http=_http_ok("frist"))
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["login"], "frist")
        # Connected but consent still off -> not push-ready, and it says so.
        self.assertFalse(payload["ready_for_push"])
        self.assertEqual(payload["readiness_reason"], "consent_off")
        # The secret must never travel back to the page.
        self.assertNotIn("ghp_secret_value", json.dumps(payload))
        # ...and it really was stored.
        self.assertEqual(_FakeStore.saved["github"], "ghp_secret_value")

    def test_connect_rejects_a_bad_token_without_leaking_it(self):
        payload = github_connect_payload(
            "ghp_bad", http=lambda m, u, t, p: (401, {"message": "Bad credentials"})
        )
        self.assertFalse(payload["connected"])
        self.assertIn("error", payload)
        self.assertNotIn("ghp_bad", json.dumps(payload))
        self.assertNotIn("github", _FakeStore.saved)

    def test_set_push_is_honest_without_a_token(self):
        result = github_set_push_payload(True)
        self.assertTrue(result["allow_push"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "no_token")

    def test_connect_then_enable_is_ready(self):
        github_connect_payload("ghp_x", http=_http_ok())
        result = github_set_push_payload(True)
        self.assertTrue(result["ready"])
        self.assertEqual(result["reason"], "ready")
        self.assertTrue(github_status_payload()["ready_for_push"])

    def test_disconnect_revokes_everything(self):
        github_connect_payload("ghp_x", http=_http_ok())
        github_set_push_payload(True)
        github_disconnect_payload()
        status = github_status_payload()
        self.assertFalse(status["connected"])
        self.assertFalse(status["allow_push"])


class SettingsGithubBlockTests(unittest.TestCase):
    def test_settings_payload_includes_github(self):
        from pathlib import Path
        import tempfile

        from _helpers import make_repo
        from opai.gui_web import settings_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            with mock.patch(
                "opaihub.accounts._account_cli_version",
                side_effect=AssertionError("synchronous CLI version lookup"),
            ):
                payload = settings_payload(root)
        self.assertIn("github", payload)
        self.assertIn("ready_for_push", payload["github"])
        json.dumps(payload)


if __name__ == "__main__":
    unittest.main()
