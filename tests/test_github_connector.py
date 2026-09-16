"""GitHub connector: PAT auth, consent, slug parsing, PR creation.

Hermetic: HTTP transport is injected, the keychain is faked, and the config
file lives under an isolated home. No network, no real credentials.
"""

from __future__ import annotations

import contextlib
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from vestahub import github_connector as gc

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
        gc.set_public_read_allowed(True)
        result = gc.disconnect_github()
        self.assertTrue(result["disconnected"])
        self.assertFalse(gc.push_allowed())
        self.assertFalse(gc.public_read_allowed())
        self.assertFalse(gc.github_status()["connected"])

    def test_public_read_consent_is_persisted_and_reported(self):
        enabled = gc.set_public_read_allowed(True)

        self.assertTrue(enabled["allow_public_read"])
        self.assertTrue(gc.public_read_allowed())
        self.assertTrue(gc.github_status()["allow_public_read"])

    def test_verify_without_token_explains_the_gap(self):
        # Bug 5: a doctor test with no token must give a concrete reason, not a
        # bare failure.
        result = gc.verify_github_connection(http=_http_ok())
        self.assertFalse(result["connected"])
        self.assertEqual(result["authStatus"], "not_configured")
        self.assertIn("No GitHub token", result["safeDiagnostic"])
        self.assertIn("lastCheckedAt", result)

    def test_verify_with_valid_token_reports_login(self):
        gc.connect_github("ghp_test", http=_http_ok("frist"))
        result = gc.verify_github_connection(http=_http_ok("frist"))
        self.assertTrue(result["connected"])
        self.assertEqual(result["authStatus"], "connected")
        self.assertIn("frist", result["safeDiagnostic"])
        # The token value must never leak into the diagnostic.
        self.assertNotIn("ghp_test", result["safeDiagnostic"])

    def test_verify_with_rejected_token_names_the_cause(self):
        gc.connect_github("ghp_test", http=_http_ok())
        result = gc.verify_github_connection(
            http=lambda m, u, t, p: (401, {"message": "Bad credentials"})
        )
        self.assertFalse(result["connected"])
        self.assertEqual(result["authStatus"], "invalid")
        self.assertIn("rejected", result["safeDiagnostic"])

    def test_verify_survives_a_network_error(self):
        gc.connect_github("ghp_test", http=_http_ok())

        def boom(method, url, token, payload):
            raise OSError("no network")

        result = gc.verify_github_connection(http=boom)
        self.assertFalse(result["connected"])
        self.assertEqual(result["authStatus"], "provider_unavailable")
        self.assertIn("api.github.com", result["safeDiagnostic"])

        disabled = gc.set_public_read_allowed(False)
        self.assertFalse(disabled["allow_public_read"])
        self.assertFalse(gc.public_read_allowed())

    def test_env_token_wins_over_keychain(self):
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "env_tok"}):
            token, source = gc.stored_github_token()
        self.assertEqual((token, source), ("env_tok", "env"))


class ReadinessTests(unittest.TestCase):
    """Consent alone is inert: pushes need a connected token AND consent, and the
    user must be told the *actual* missing piece (the reported bug)."""

    def setUp(self):
        _FakeStore.saved = {}
        self._home = contextlib.ExitStack()
        self._home.enter_context(isolated_home())
        self._home.enter_context(mock.patch.object(gc, "CredentialStore", _FakeStore))
        # No ambient token from the developer's own shell.
        self._home.enter_context(
            mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""})
        )
        self.addCleanup(self._home.close)

    def test_consent_on_without_token_is_not_ready_and_names_the_token(self):
        gc.set_push_allowed(True)
        readiness = gc.github_readiness()
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["reason"], "no_token")
        self.assertIn("token", readiness["next_step"].lower())
        # And it must NOT tell the user to just re-run allow-push.
        self.assertIn("connect", readiness["next_step"].lower())

    def test_allow_push_on_result_is_honest_without_a_token(self):
        result = gc.set_push_allowed(True)
        self.assertTrue(result["allow_push"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "no_token")

    def test_token_without_consent_names_consent(self):
        _FakeStore.saved = {"github": "ghp_test"}
        gc.set_push_allowed(False)
        readiness = gc.github_readiness()
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["reason"], "consent_off")
        self.assertIn("allow-push", readiness["next_step"])

    def test_token_and_consent_is_ready(self):
        _FakeStore.saved = {"github": "ghp_test"}
        gc.set_push_allowed(True)
        readiness = gc.github_readiness()
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["reason"], "ready")
        self.assertEqual(readiness["next_step"], "")

    def test_nothing_configured_names_both(self):
        readiness = gc.github_readiness()
        self.assertEqual(readiness["reason"], "no_token_and_consent_off")

    def test_status_exposes_ready_for_push(self):
        _FakeStore.saved = {"github": "ghp_test"}
        gc.set_push_allowed(True)
        status = gc.github_status()
        self.assertTrue(status["ready_for_push"])
        self.assertEqual(status["readiness_reason"], "ready")

    def test_pr_without_token_blames_the_token_not_consent(self):
        # The user's exact scenario: consent on, no token.
        gc.set_push_allowed(True)
        with mock.patch.object(gc, "repo_slug", return_value="o/r"):
            result = gc.create_pull_request(
                Path("."), title="T", body="", head="b", http=_http_ok()
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_token")
        self.assertIn("token", result["error"].lower())


class ReadToolTests(unittest.TestCase):
    """Read-only PR/issue calls need a token but NOT push consent (#300)."""

    def setUp(self):
        _FakeStore.saved = {"github": "ghp_test"}
        self._home = contextlib.ExitStack()
        self._home.enter_context(isolated_home())
        self._home.enter_context(mock.patch.object(gc, "CredentialStore", _FakeStore))
        self._home.enter_context(
            mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""})
        )
        self._home.enter_context(mock.patch.object(gc, "repo_slug", return_value="o/r"))
        self.addCleanup(self._home.close)

    def test_pr_status_summarizes_checks(self):
        def http(method, url, token, payload):
            if url.endswith("/pulls/7"):
                return 200, {
                    "number": 7,
                    "state": "open",
                    "merged": False,
                    "mergeable_state": "clean",
                    "title": "Add x",
                    "html_url": "https://github.com/o/r/pull/7",
                    "head": {"sha": "abc123"},
                }
            if url.endswith("/commits/abc123/check-runs"):
                return 200, {
                    "check_runs": [
                        {"status": "completed", "conclusion": "success"},
                        {"status": "completed", "conclusion": "failure"},
                        {"status": "in_progress"},
                    ]
                }
            return 404, {}

        result = gc.pull_request_status(Path("."), 7, http=http)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "open")
        self.assertEqual(
            result["checks"],
            {"total": 3, "success": 1, "failed": 1, "pending": 1},
        )

    def test_pr_status_requires_a_token(self):
        _FakeStore.saved = {}
        result = gc.pull_request_status(Path("."), 7, http=_http_ok())
        self.assertFalse(result["ok"])
        self.assertIn("token", result["error"].lower())

    def test_get_issue_reads_fields_and_redacts(self):
        def http(method, url, token, payload):
            return 200, {
                "number": 5,
                "state": "open",
                "title": "Fix the widget",
                "body": "reproduce with token=sk-abcdef1234567890abcd",
                "labels": [{"name": "bug"}, {"name": "p1"}],
                "html_url": "https://github.com/o/r/issues/5",
            }

        result = gc.get_issue(Path("."), 5, http=http)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["title"], "Fix the widget")
        self.assertEqual(result["labels"], ["bug", "p1"])
        # A secret pasted into an issue body must be redacted before it reaches
        # the model.
        self.assertNotIn("sk-abcdef1234567890abcd", result["body"])
        # #540: an issue body is attacker-influenceable (anyone can open one on
        # a public repo), so it must carry the same untrusted-data marker
        # search_issues already does.
        self.assertEqual(result["content_trust"], "untrusted_quoted_data")

    def test_get_issue_without_comments_never_calls_comments_endpoint(self):
        calls = []

        def http(method, url, token, payload):
            calls.append(url)
            return 200, {"number": 5, "state": "open", "title": "T"}

        result = gc.get_issue(Path("."), 5, http=http)
        self.assertTrue(result["ok"], result)
        self.assertNotIn("comments", result)
        self.assertEqual(len(calls), 1)

    def test_get_issue_can_include_redacted_comments(self):
        def http(method, url, token, payload):
            if url.endswith("/issues/5"):
                return 200, {
                    "number": 5,
                    "state": "open",
                    "title": "Fix the widget",
                    "body": "steps",
                    "labels": [],
                    "html_url": "https://github.com/o/r/issues/5",
                }
            if "/issues/5/comments" in url:
                return 200, [
                    {
                        "user": {"login": "marco"},
                        "created_at": "2026-07-17T14:00:00Z",
                        "body": "repro with token=sk-abcdef1234567890abcd",
                    },
                    "not-a-dict-entry",
                ]
            return 404, {}

        result = gc.get_issue(Path("."), 5, include_comments=True, http=http)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["comments"]), 1)
        comment = result["comments"][0]
        self.assertEqual(comment["author"], "marco")
        self.assertEqual(comment["created_at"], "2026-07-17T14:00:00Z")
        # Comment bodies get the same redaction as the issue body (F19).
        self.assertNotIn("sk-abcdef1234567890abcd", comment["body"])

    def test_get_issue_comments_fetch_failure_is_an_error(self):
        def http(method, url, token, payload):
            if url.endswith("/issues/5"):
                return 200, {"number": 5, "state": "open", "title": "T"}
            return 500, {}

        result = gc.get_issue(Path("."), 5, include_comments=True, http=http)
        self.assertFalse(result["ok"])
        self.assertIn("comments", result["error"])
        self.assertIn("500", result["error"])

    def test_issue_search_is_origin_scoped_encoded_paginated_and_excludes_prs(self):
        calls = []

        def http(method, url, token, payload):
            calls.append((method, url, token, payload))
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            if page == 1:
                return 200, {
                    "items": [
                        {
                            "number": 6,
                            "title": "A pull request",
                            "pull_request": {"url": "https://api.github.test/pulls/6"},
                        },
                        {
                            "number": 7,
                            "title": "Fix token=sk-abcdefghijklmnopqrst",
                            "body": "x" * 5000,
                            "labels": [{"name": "bug"}],
                            "state": "open",
                            "html_url": "https://github.com/o/r/issues/7",
                        },
                    ]
                }
            return 200, {
                "items": [
                    {
                        "number": 9,
                        "title": "Second issue",
                        "body": "bounded",
                        "labels": [{"name": "good first issue"}],
                        "state": "open",
                        "html_url": "https://github.com/o/r/issues/9",
                    }
                ]
            }

        result = gc.search_issues(
            Path("."),
            query='good first issue repo:attacker/other "escape"',
            state="open",
            labels=("good first issue", "bug/security"),
            limit=2,
            http=http,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual([item["number"] for item in result["issues"]], [7, 9])
        self.assertEqual(result["content_trust"], "untrusted_quoted_data")
        self.assertNotIn("pull_request", str(result["issues"]))
        self.assertNotIn("sk-abcdefghijklmnopqrst", result["issues"][0]["title"])
        self.assertLessEqual(len(result["issues"][0]["excerpt"]), 1000)
        self.assertEqual(len(calls), 2)
        for method, url, token, payload in calls:
            self.assertEqual((method, token, payload), ("GET", "ghp_test", None))
            parsed = parse_qs(urlsplit(url).query)
            query = parsed["q"][0]
            self.assertIn("repo:o/r", query)
            self.assertIn("is:issue", query)
            self.assertIn('label:"good first issue"', query)
            self.assertTrue(
                query.endswith('"good first issue repo:attacker/other escape"'), query
            )
            self.assertNotIn(
                "repo:attacker/other",
                query[: -len('"good first issue repo:attacker/other escape"')],
            )
            self.assertLessEqual(int(parsed["per_page"][0]), 30)

    def test_issue_search_requires_token_or_explicit_public_read_consent(self):
        _FakeStore.saved = {}
        calls = []

        def http(method, url, token, payload):
            calls.append((method, url, token, payload))
            return 200, {"items": []}

        blocked = gc.search_issues(Path("."), http=http)
        public = gc.search_issues(Path("."), http=http, allow_public=True)

        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason"], "consent_required")
        self.assertTrue(public["ok"], public)
        self.assertEqual(calls[0][2], "")

    def test_issue_search_returns_typed_origin_auth_and_rate_limit_failures(self):
        with mock.patch.object(gc, "repo_slug", return_value=""):
            wrong_origin = gc.search_issues(Path("."), http=_http_ok())
        unauthorized = gc.search_issues(
            Path("."), http=lambda m, u, t, p: (401, {"message": "Bad credentials"})
        )
        limited = gc.search_issues(
            Path("."),
            http=lambda m, u, t, p: (403, {"message": "API rate limit exceeded"}),
        )

        self.assertEqual(wrong_origin["reason"], "not_github")
        self.assertEqual(unauthorized["reason"], "auth")
        self.assertEqual(limited["reason"], "rate_limit")


class WriteToolTests(unittest.TestCase):
    """Comment / request-review are outward: need a token AND push consent (#300)."""

    def setUp(self):
        _FakeStore.saved = {"github": "ghp_test"}
        self._home = contextlib.ExitStack()
        self._home.enter_context(isolated_home())
        self._home.enter_context(mock.patch.object(gc, "CredentialStore", _FakeStore))
        self._home.enter_context(
            mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""})
        )
        self._home.enter_context(mock.patch.object(gc, "repo_slug", return_value="o/r"))
        gc.set_push_allowed(True)
        self.addCleanup(self._home.close)

    def test_comment_posts_and_redacts_body(self):
        seen = {}

        def http(method, url, token, payload):
            seen["url"] = url
            seen["body"] = payload["body"]
            return 201, {"html_url": "https://github.com/o/r/issues/5#c1", "id": 1}

        result = gc.add_comment(
            Path("."), 5, "see token=sk-abcdef1234567890abcd", http=http
        )
        self.assertTrue(result["ok"], result)
        self.assertIn("/issues/5/comments", seen["url"])
        self.assertNotIn("sk-abcdef1234567890abcd", seen["body"])

    def test_comment_blocked_without_consent(self):
        gc.set_push_allowed(False)
        result = gc.add_comment(Path("."), 5, "hi", http=_http_ok())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "consent_off")

    def test_request_reviewers_sends_logins(self):
        seen = {}

        def http(method, url, token, payload):
            seen["url"] = url
            seen["reviewers"] = payload["reviewers"]
            return 201, {}

        result = gc.request_reviewers(Path("."), 7, ["alice", "", "bob"], http=http)
        self.assertTrue(result["ok"], result)
        self.assertIn("/pulls/7/requested_reviewers", seen["url"])
        self.assertEqual(seen["reviewers"], ["alice", "bob"])

    def test_request_reviewers_requires_a_login(self):
        result = gc.request_reviewers(Path("."), 7, [], http=_http_ok())
        self.assertFalse(result["ok"])
        self.assertIn("reviewer", result["error"].lower())


class SlugTests(unittest.TestCase):
    def _slug_for(self, url: str) -> str:
        completed = mock.Mock(stdout=url + "\n", returncode=0)
        with (
            mock.patch.object(gc.subprocess, "run", return_value=completed),
            mock.patch.object(
                gc,
                "resolve_trusted_git_executable",
                return_value=str(Path("C:/trusted/git.exe")),
            ),
        ):
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
