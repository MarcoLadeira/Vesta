"""GitHub connector: PAT auth, consent, slug parsing, PR creation.

Hermetic: HTTP transport is injected, the keychain is faked, and the config
file lives under an isolated home. No network, no real credentials.
"""

from __future__ import annotations

import contextlib
import unittest
from pathlib import Path
from unittest import mock

from opaihub import github_connector as gc

from tests._helpers import isolated_home


class _FakeStore:
    """In-memory CredentialStore double."""

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


class ConnectTests(unittest.TestCase):
    def setUp(self):
        _FakeStore.saved = {}
        self._home = contextlib.ExitStack()
        self._home.enter_context(isolated_home())
        self._home.enter_context(mock.patch.object(gc, "CredentialStore", _FakeStore))
        self.addCleanup(self._home.close)

    def test_connect_validates_and_stores_token(self):
        result = gc.connect_github("ghp_test", http=_http_ok("frist"))
        self.assertTrue(result["connected"])
        self.assertEqual(result["login"], "frist")
        self.assertEqual(_FakeStore.saved["github"], "ghp_test")
        # Consent stays off until explicitly enabled.
        self.assertFalse(result["allow_push"])
        self.assertFalse(gc.push_allowed())

    def test_rejected_token_is_never_stored(self):
        result = gc.connect_github(
            "bad", http=lambda m, u, t, p: (401, {"message": "Bad credentials"})
        )
        self.assertFalse(result["connected"])
        self.assertIn("rejected", result["error"])
        self.assertNotIn("github", _FakeStore.saved)

    def test_empty_token_is_an_error(self):
        result = gc.connect_github("   ")
        self.assertFalse(result["connected"])

    def test_status_reports_source_and_consent(self):
        gc.connect_github("ghp_test", http=_http_ok())
        gc.set_push_allowed(True)
        status = gc.github_status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["token_source"], "keychain")
        self.assertEqual(status["login"], "octocat")
        self.assertTrue(status["allow_push"])

    def test_disconnect_removes_token_and_revokes_consent(self):
        gc.connect_github("ghp_test", http=_http_ok())
        gc.set_push_allowed(True)
        result = gc.disconnect_github()
        self.assertTrue(result["disconnected"])
        self.assertFalse(gc.push_allowed())
        self.assertFalse(gc.github_status()["connected"])

    def test_env_token_wins_over_keychain(self):
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "env_tok"}):
            token, source = gc.stored_github_token()
        self.assertEqual((token, source), ("env_tok", "env"))


class SlugTests(unittest.TestCase):
    def _slug_for(self, url: str) -> str:
        completed = mock.Mock(stdout=url + "\n", returncode=0)
        with mock.patch.object(gc.subprocess, "run", return_value=completed):
            return gc.repo_slug(Path("."))

    def test_https_url(self):
        self.assertEqual(self._slug_for("https://github.com/o/r.git"), "o/r")

    def test_https_url_without_git_suffix(self):
        self.assertEqual(self._slug_for("https://github.com/o/r"), "o/r")

    def test_ssh_url(self):
        self.assertEqual(self._slug_for("git@github.com:o/r.git"), "o/r")

    def test_non_github_remote_is_empty(self):
        self.assertEqual(self._slug_for("https://gitlab.com/o/r.git"), "")


class PullRequestTests(unittest.TestCase):
    def setUp(self):
        _FakeStore.saved = {"github": "ghp_test"}
        self._home = contextlib.ExitStack()
        self._home.enter_context(isolated_home())
        self._home.enter_context(mock.patch.object(gc, "CredentialStore", _FakeStore))
        self._home.enter_context(mock.patch.object(gc, "repo_slug", return_value="o/r"))
        self.addCleanup(self._home.close)

    def test_pr_requires_consent(self):
        gc.set_push_allowed(False)
        result = gc.create_pull_request(
            Path("."), title="T", body="", head="b", http=_http_ok()
        )
        self.assertFalse(result["ok"])
        self.assertIn("allow-push", result["error"])

    def test_pr_created_with_consent(self):
        gc.set_push_allowed(True)

        def http(method, url, token, payload):
            assert method == "POST"
            assert url == "https://api.github.com/repos/o/r/pulls"
            assert payload["head"] == "feat/x"
            return 201, {"html_url": "https://github.com/o/r/pull/7", "number": 7}

        result = gc.create_pull_request(
            Path("."), title="Add x", body="body", head="feat/x", http=http
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["number"], 7)
        self.assertIn("/pull/7", result["url"])

    def test_pr_failure_is_reported_with_github_message(self):
        gc.set_push_allowed(True)
        result = gc.create_pull_request(
            Path("."),
            title="Add x",
            body="",
            head="feat/x",
            http=lambda m, u, t, p: (
                422,
                {"message": "Validation Failed", "errors": [{"message": "exists"}]},
            ),
        )
        self.assertFalse(result["ok"])
        self.assertIn("422", result["error"])


if __name__ == "__main__":
    unittest.main()
