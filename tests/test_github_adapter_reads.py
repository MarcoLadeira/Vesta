"""GitHubAdapter read-path honesty (F19): empty stdout is an error, not success.

`gh issue view` can exit 0 with an empty body in broken environments; feeding
that to the model as a green "ran" caused issue-solving loops. These tests
pin the new contract: reads must produce output or raise, mutations that are
legitimately silent stay silent-tolerant, and ``issue_view`` always uses the
reliable ``--json`` form with a malformed/empty guard. All hermetic.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from opaihub.github_workflow import GitHubAdapter


def _completed(argv: list[str], stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class RunEmptyStdoutTests(unittest.TestCase):
    def test_read_command_with_empty_stdout_is_an_error(self):
        def fake_run(argv, **kwargs):
            return _completed(argv, "")

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        with self.assertRaises(RuntimeError) as ctx:
            adapter.list_open_issues()
        self.assertIn("no output", str(ctx.exception))

    def test_empty_stdout_error_mentions_the_command(self):
        def fake_run(argv, **kwargs):
            return _completed(argv, "")

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        with self.assertRaises(RuntimeError) as ctx:
            adapter.issue_view(219)
        message = str(ctx.exception)
        self.assertIn("issue", message)
        self.assertIn("view", message)

    def test_silent_mutations_tolerate_empty_stdout(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return _completed(argv, "")

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        # pr merge reports success via exit code only; empty stdout is fine.
        self.assertEqual(adapter.merge_pr(12, method="squash"), "")
        self.assertEqual(calls[0][1:3], ["pr", "merge"])

    def test_explicit_expect_output_override(self):
        def fake_run(argv, **kwargs):
            return _completed(argv, "")

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        # A caller that knows a command may be silent can opt out explicitly.
        self.assertEqual(adapter._run(["issue", "list"], expect_output=False), "")
        with self.assertRaises(RuntimeError):
            adapter._run(["pr", "merge", "5"], expect_output=True)

    def test_nonzero_exit_with_stderr_still_raises(self):
        def fake_run(argv, **kwargs):
            return _completed(argv, "", returncode=1, stderr="boom")

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        with self.assertRaises(RuntimeError) as ctx:
            adapter.list_open_issues()
        self.assertIn("boom", str(ctx.exception))

    def test_nonzero_allowed_exit_with_output_still_returns(self):
        def fake_run(argv, **kwargs):
            return _completed(argv, '[{"name":"ci","bucket":"pass"}]', returncode=1)

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        checks = adapter.pr_checks(5)
        self.assertEqual(checks[0]["name"], "ci")


class IssueViewTests(unittest.TestCase):
    ISSUE_JSON = (
        '{"number": 219, "title": "Epic", "body": "do things", "state": "OPEN",'
        ' "labels": [{"name": "epic"}],'
        ' "comments": [{"body": "context", "author": {"login": "marco"}}],'
        ' "url": "https://github.test/issues/219"}'
    )

    def test_issue_view_uses_json_form_and_parses(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return _completed(argv, self.ISSUE_JSON)

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        data = adapter.issue_view(219)

        argv = calls[0]
        self.assertEqual(argv[1:4], ["issue", "view", "219"])
        self.assertIn("--json", argv)
        json_fields = argv[argv.index("--json") + 1]
        for field in ("title", "body", "comments", "labels"):
            self.assertIn(field, json_fields)
        self.assertEqual(data["number"], 219)
        self.assertEqual(data["comments"][0]["body"], "context")

    def test_issue_view_passes_repo_through(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return _completed(argv, self.ISSUE_JSON)

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        adapter.issue_view(219, repo="MarcoLadeira/OPai")
        argv = calls[0]
        self.assertEqual(argv[argv.index("--repo") + 1], "MarcoLadeira/OPai")

    def test_issue_view_rejects_malformed_json(self):
        def fake_run(argv, **kwargs):
            return _completed(argv, "not json at all")

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        with self.assertRaises(RuntimeError) as ctx:
            adapter.issue_view(219)
        self.assertIn("malformed JSON", str(ctx.exception))

    def test_issue_view_rejects_empty_object(self):
        def fake_run(argv, **kwargs):
            return _completed(argv, "{}")

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        with self.assertRaises(RuntimeError) as ctx:
            adapter.issue_view(219)
        self.assertIn("no issue data", str(ctx.exception))

    def test_issue_view_rejects_empty_stdout(self):
        def fake_run(argv, **kwargs):
            return _completed(argv, "")

        adapter = GitHubAdapter(Path("C:/repo"), run=fake_run)
        with self.assertRaises(RuntimeError) as ctx:
            adapter.issue_view(219)
        self.assertIn("no output", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
