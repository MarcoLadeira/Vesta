"""OPai credits itself on commits it writes.

GitHub reads ``Co-Authored-By`` trailers and attributes the commit to that
identity — it is why an assistant appears in a repository's contributor list and
on its pull requests. OPai wrote commits with no trailer, so work it did was
indistinguishable from work the user typed by hand.

Every assertion about the trailer being *valid* is made against ``git
interpret-trailers --parse`` rather than string matching. A trailer git does not
parse is not a trailer, however correct it looks — and that distinction caught a
real bug: a conventional-commit subject ("fix: parser crash") matches the trailer
shape, so the credit was appended with a single newline and git read no trailers
at all. That is the dominant commit style in this repository, so the feature
would have silently done nothing on most commits while appearing to work.
"""

from __future__ import annotations

import subprocess
import unittest

from opai.authorship import (
    COAUTHOR_TRAILER,
    OPAI_EMAIL,
    has_opai_trailer,
    with_coauthor,
)


def _git_parsed_trailers(message: str) -> str:
    """What git itself reads as the trailer block — the only opinion that counts."""
    result = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
        ["git", "interpret-trailers", "--parse"],
        input=message,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


class TrailerIsRealTests(unittest.TestCase):
    """Git must actually parse what we append."""

    def test_a_conventional_commit_subject_still_yields_a_parsed_trailer(self) -> None:
        # The regression. "fix: ..." looks exactly like a trailer, and treating
        # it as one produced a credit git could not read.
        parsed = _git_parsed_trailers(with_coauthor("fix: parser crash"))
        self.assertIn(OPAI_EMAIL, parsed)

    def test_a_plain_subject_yields_a_parsed_trailer(self) -> None:
        parsed = _git_parsed_trailers(with_coauthor("Fix the parser"))
        self.assertIn(OPAI_EMAIL, parsed)

    def test_a_subject_and_body_yields_a_parsed_trailer(self) -> None:
        parsed = _git_parsed_trailers(with_coauthor("feat: thing\n\nWhy it matters."))
        self.assertIn(OPAI_EMAIL, parsed)

    def test_an_existing_trailer_block_is_joined_not_restarted(self) -> None:
        # Git only parses the *last* trailer block, so starting a second one
        # would orphan the first — the user's Signed-off-by would vanish.
        message = with_coauthor("feat: thing\n\nSigned-off-by: Marco <m@example.dev>")
        parsed = _git_parsed_trailers(message)
        self.assertIn("Signed-off-by: Marco <m@example.dev>", parsed)
        self.assertIn(OPAI_EMAIL, parsed)


class TrailerHygieneTests(unittest.TestCase):
    def test_it_is_not_appended_twice(self) -> None:
        once = with_coauthor("fix: thing")
        twice = with_coauthor(once)
        self.assertEqual(twice.count(OPAI_EMAIL), 1)

    def test_a_differently_capitalised_trailer_still_counts_as_present(self) -> None:
        # Git and GitHub treat these as the same trailer; a naive string
        # comparison does not, and would duplicate the credit.
        existing = f"fix: thing\n\nco-authored-by: OPai <{OPAI_EMAIL}>"
        self.assertTrue(has_opai_trailer(existing))
        self.assertEqual(with_coauthor(existing).count(OPAI_EMAIL), 1)

    def test_an_empty_message_is_left_alone(self) -> None:
        # A bare trailer with no subject is not a commit message. Manufacturing
        # one would hide the caller's real problem.
        for empty in ("", "   ", "\n\n"):
            with self.subTest(message=repr(empty)):
                self.assertNotIn(OPAI_EMAIL, with_coauthor(empty))

    def test_the_user_remains_the_author(self) -> None:
        # Co-authorship adds; it must never rewrite whose commit this is.
        message = with_coauthor("fix: thing")
        self.assertNotIn("Author:", message)
        self.assertIn("Co-Authored-By:", message)


class RealCommitTests(unittest.TestCase):
    """End-to-end: a commit OPai makes is attributed by git."""

    def test_a_commit_made_through_the_tool_carries_the_trailer(self) -> None:
        import tempfile
        from pathlib import Path

        from _helpers import make_repo

        from opaihub.provider_tools import RepositoryToolExecutor

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            repo = make_repo(root, files={"app.py": "x = 1\n"}, commit=True)
            executor = RepositoryToolExecutor(
                repo, allow_edits=True, allow_git_ops=True
            )
            executor._write_file({"path": "app.py", "content": "x = 2\n"})
            result = executor._git_commit(
                {"message": "fix: bump the value", "paths": ["app.py"]}
            )
            self.assertTrue(result.get("ok"), result)

            body = subprocess.run(  # nosec B603 B607 - fixed argv, test repo
                ["git", "log", "-1", "--pretty=%B"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            ).stdout

        self.assertIn(COAUTHOR_TRAILER, body)
        self.assertIn(OPAI_EMAIL, _git_parsed_trailers(body))


class PullRequestAttributionTests(unittest.TestCase):
    """A reviewer should not have to read `git log` to learn who wrote this."""

    def test_the_attribution_is_appended(self) -> None:
        from opai.authorship import PR_ATTRIBUTION, with_pr_attribution

        body = with_pr_attribution("Fixes the parser crash.")
        self.assertIn("Fixes the parser crash.", body)
        self.assertIn(PR_ATTRIBUTION, body)

    def test_it_is_not_appended_twice(self) -> None:
        from opai.authorship import PR_ATTRIBUTION, with_pr_attribution

        once = with_pr_attribution("body")
        self.assertEqual(with_pr_attribution(once).count(PR_ATTRIBUTION), 1)

    def test_an_empty_body_still_gets_attribution(self) -> None:
        from opai.authorship import PR_ATTRIBUTION, with_pr_attribution

        self.assertIn(PR_ATTRIBUTION, with_pr_attribution(""))

    def test_the_attribution_is_ascii_only(self) -> None:
        # It reaches Windows consoles and log files on a cp1252 default
        # encoding, where a decorative emoji raises UnicodeEncodeError.
        from opai.authorship import PR_ATTRIBUTION

        PR_ATTRIBUTION.encode("cp1252")  # must not raise

    def test_the_connector_attributes_the_pull_request_it_opens(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock

        from opai.authorship import PR_ATTRIBUTION
        from opaihub import github_connector

        sent: dict = {}

        def fake_http(method, url, token, payload):
            sent.update(payload)
            return 201, {"html_url": "https://example/pr/1", "number": 1}

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(
                    github_connector,
                    "github_readiness",
                    return_value={"ready": True, "reason": "ready", "next_step": ""},
                ),
                mock.patch.object(
                    github_connector, "stored_github_token", return_value=("t", "env")
                ),
                mock.patch.object(
                    github_connector, "repo_slug", return_value="acme/demo"
                ),
            ):
                result = github_connector.create_pull_request(
                    Path(tmp),
                    title="Fix the parser",
                    body="Fixes the crash.",
                    head="fix/parser",
                    http=fake_http,
                )

        self.assertTrue(result.get("ok"), result)
        self.assertIn(PR_ATTRIBUTION, sent["body"])
        self.assertIn("Fixes the crash.", sent["body"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
