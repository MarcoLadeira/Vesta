"""Merging a pull request end to end, with the HTTP layer mocked.

The connector could open a PR, comment on it and read its checks, but had no
way to *finish* one -- so "land this branch" always ended with a human clicking
Merge, no matter how much autonomy the run had been given. These pin the
behaviour of the merge path that closes that gap.

Nothing here touches the network: ``http`` is injected, and the executor tests
stub the connector call outright.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from opaihub import github_connector
from opaihub.github_connector import merge_pull_request


class _Http:
    """Records calls and replays a queued (status, body) response."""

    def __init__(self, *responses: tuple[int, Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, method: str, url: str, token: str, payload: Any) -> Any:
        self.calls.append((method, url, payload))
        return self.responses.pop(0) if self.responses else (500, {})


class MergePullRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        ready = mock.patch.object(
            github_connector,
            "github_readiness",
            return_value={"ready": True, "next_step": "", "reason": ""},
        )
        token = mock.patch.object(
            github_connector, "stored_github_token", return_value=("t0ken", "test")
        )
        slug = mock.patch.object(
            github_connector, "repo_slug", return_value="owner/repo"
        )
        for patcher in (ready, token, slug):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_successful_squash_merge(self) -> None:
        http = _Http(
            (200, {"sha": "abc123", "message": "Pull Request successfully merged"})
        )
        result = merge_pull_request(self.root, 42, method="squash", http=http)

        self.assertTrue(result["ok"])
        self.assertTrue(result["merged"])
        self.assertEqual(result["sha"], "abc123")
        method, url, payload = http.calls[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(url.rsplit("/repos/", 1)[1], "owner/repo/pulls/42/merge")
        self.assertEqual(payload["merge_method"], "squash")

    def test_the_merge_method_is_validated_before_any_request(self) -> None:
        http = _Http()
        result = merge_pull_request(self.root, 1, method="fast-forward", http=http)

        self.assertFalse(result["ok"])
        self.assertIn("fast-forward", result["error"])
        self.assertEqual(http.calls, [], "no request may be sent for a bad method")

    def test_a_not_mergeable_pr_is_explained_not_just_coded(self) -> None:
        # 405 covers conflicts, failing checks, required reviews and protected
        # branches; a bare "HTTP 405" would leave the user guessing which.
        http = _Http((405, {"message": "Required status check is failing"}))
        result = merge_pull_request(self.root, 7, http=http)

        self.assertFalse(result["ok"])
        self.assertTrue(result["not_mergeable"])
        self.assertIn("Required status check", result["error"])

    def test_a_moved_head_is_reported_distinctly(self) -> None:
        http = _Http((409, {"message": "Head branch was modified"}))
        result = merge_pull_request(
            self.root, 7, expected_head_sha="deadbee", http=http
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["head_moved"])
        self.assertEqual(http.calls[0][2]["sha"], "deadbee")

    def test_a_lost_response_is_uncertain_rather_than_failed(self) -> None:
        # The merge may have landed; a plain failure would invite a blind retry.
        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise OSError("connection reset")

        result = merge_pull_request(self.root, 9, http=_boom)

        self.assertFalse(result["ok"])
        self.assertTrue(result["uncertain"])

    def test_a_missing_token_never_reaches_the_network(self) -> None:
        http = _Http()
        with mock.patch.object(
            github_connector,
            "github_readiness",
            return_value={
                "ready": False,
                "next_step": "Connect GitHub",
                "reason": "no token",
            },
        ):
            result = merge_pull_request(self.root, 1, http=http)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Connect GitHub")
        self.assertEqual(http.calls, [])


class MergeToolAutonomyTests(unittest.TestCase):
    """The tool stops or proceeds according to the run's autonomy level."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def executor(self, autonomy: str) -> Any:
        from opaihub.provider_tools import RepositoryToolExecutor

        return RepositoryToolExecutor(
            self.root,
            allow_edits=True,
            allow_github_write=True,
            autonomy=autonomy,
        )

    def test_the_tool_is_offered_when_github_writes_are_allowed(self) -> None:
        names = {s["function"]["name"] for s in self.executor("full-auto").schemas()}
        self.assertIn("github_merge_pr", names)

    def test_lower_modes_stop_for_approval_before_merging(self) -> None:
        executor = self.executor("safe-auto")
        with mock.patch("opaihub.github_connector.merge_pull_request") as merged:
            result = executor.invoke("github_merge_pr", {"number": 5})

        self.assertEqual(result["error_code"], "COMMAND_NEEDS_APPROVAL")
        merged.assert_not_called()

    def test_bypass_merges_without_asking(self) -> None:
        executor = self.executor("full-auto")
        with mock.patch(
            "opaihub.github_connector.merge_pull_request",
            return_value={"ok": True, "merged": True, "sha": "f00", "message": "ok"},
        ) as merged:
            result = executor.invoke("github_merge_pr", {"number": 5})

        self.assertTrue(result["ok"], result)
        merged.assert_called_once()

    def test_an_invalid_method_is_refused_before_the_connector(self) -> None:
        executor = self.executor("full-auto")
        with mock.patch("opaihub.github_connector.merge_pull_request") as merged:
            result = executor.invoke(
                "github_merge_pr", {"number": 5, "method": "cherry-pick"}
            )

        self.assertEqual(result["error_code"], "INVALID_TOOL_ARGUMENTS")
        merged.assert_not_called()


if __name__ == "__main__":
    unittest.main()
