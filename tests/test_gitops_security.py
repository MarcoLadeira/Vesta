"""Git-ref injection and fail-closed secret scanning (#20)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opcoding.gitops import (
    changed_files,
    git_summary,
    resolve_ref,
    secret_scan_diff,
    validate_ref,
)

from tests._helpers import make_repo


class RefGrammarTests(unittest.TestCase):
    def test_plain_branch_tag_and_commit_names_are_accepted(self):
        for ref in (
            "main",
            "origin/main",
            "feature/x-1",
            "v1.2.3",
            "release-1.0",
            "a" * 40,
            "codex/issue-20-fix",
        ):
            with self.subTest(ref=ref):
                self.assertEqual(validate_ref(ref), ref)

    def test_option_like_and_malicious_refs_are_rejected(self):
        for ref in (
            "-option",
            "--output=/tmp/x",
            "--ext-diff",
            "-Ofile",
            "a b",
            "a;rm -rf /",
            "$(reboot)",
            "`reboot`",
            "a|b",
            "a\nb",
            "a\x00b",
            "",
            None,
            "   ",
        ):
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError):
                    validate_ref(ref)

    def test_revision_expressions_outside_policy_are_rejected(self):
        for ref in (
            "HEAD~1",
            "HEAD^",
            "main@{yesterday}",
            "a..b",
            "a...b",
            "refs/heads/",
            "branch.lock",
            "a//b",
            "branch.",
            ":refname",
            "*glob*",
        ):
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError):
                    validate_ref(ref)

    def test_rejection_message_never_echoes_the_hostile_value(self):
        try:
            validate_ref("--output=/tmp/pwn")
        except ValueError as exc:
            self.assertNotIn("--output", str(exc))
        else:  # pragma: no cover - the assert above must run
            self.fail("expected ValueError")


class CommandConstructionTests(unittest.TestCase):
    def _completed(self, stdout="", returncode=0):
        result = mock.Mock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = ""
        result.timed_out = False
        return result

    def test_option_like_base_never_reaches_git(self):
        with mock.patch("opcoding.gitops.run_command") as run:
            with self.assertRaises(ValueError):
                git_summary(Path("."), base="--output=/tmp/x")
        run.assert_not_called()

    def test_diff_commands_use_argv_terminators_and_no_ext_diff(self):
        with mock.patch("opcoding.gitops.run_command") as run:
            run.return_value = self._completed(stdout="abc123\n")
            git_summary(Path("."), base="main")

        commands = [call.args[0] for call in run.call_args_list]
        # The very first command resolves the ref, before any diff runs.
        self.assertEqual(commands[0][:4], ["git", "rev-parse", "--verify", "--quiet"])
        self.assertEqual(commands[0][4], "main^{commit}")
        for argv in commands:
            self.assertIsInstance(argv, list)
        diffs = [argv for argv in commands if argv[:2] == ["git", "diff"]]
        self.assertTrue(diffs)
        for argv in diffs:
            self.assertIn("--no-ext-diff", argv)
            self.assertEqual(argv[-2:], ["--", "."])
            self.assertNotIn("main", argv)  # only the resolved range form
            if "main...HEAD" in argv:
                self.assertLess(argv.index("--no-ext-diff"), argv.index("main...HEAD"))

    def test_unknown_refs_fail_closed_before_diffing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            with self.assertRaises(ValueError):
                changed_files(root, base="no-such-branch-xyz")

    def test_valid_ref_resolves_against_a_real_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(resolve_ref(root, branch), branch)
            self.assertEqual(changed_files(root, base=branch), [])


class FailClosedSecretScanTests(unittest.TestCase):
    def _completed(self, stdout="", returncode=0, timed_out=False):
        result = mock.Mock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = ""
        result.timed_out = timed_out
        return result

    def test_clean_diff_is_safe(self):
        with mock.patch("opcoding.gitops.run_command") as run:
            run.return_value = self._completed(stdout="+ normal code\n")
            result = secret_scan_diff(Path("."))
        self.assertTrue(result["safe_to_commit"])
        self.assertTrue(result["scanner_ok"])

    def test_secret_findings_block(self):
        with mock.patch("opcoding.gitops.run_command") as run:
            run.return_value = self._completed(
                stdout="+ api_key = 'sk-abcdefghijklmnopqrstuv'\n"
            )
            result = secret_scan_diff(Path("."))
        self.assertFalse(result["safe_to_commit"])
        self.assertTrue(result["hits"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuv", str(result))

    def test_failed_git_diff_blocks(self):
        with mock.patch("opcoding.gitops.run_command") as run:
            run.return_value = self._completed(returncode=128)
            result = secret_scan_diff(Path("."))
        self.assertFalse(result["safe_to_commit"])
        self.assertFalse(result["scanner_ok"])
        self.assertIn("unreadable", result["reason"])

    def test_timed_out_scan_blocks(self):
        with mock.patch("opcoding.gitops.run_command") as run:
            run.return_value = self._completed(timed_out=True)
            result = secret_scan_diff(Path("."))
        self.assertFalse(result["safe_to_commit"])

    def test_scanner_crash_blocks(self):
        with mock.patch("opcoding.gitops.run_command") as run:
            run.return_value = self._completed(stdout="+ code\n")
            with mock.patch(
                "opcoding.gitops.find_secret_hits",
                side_effect=RuntimeError("scanner exploded"),
            ):
                result = secret_scan_diff(Path("."))
        self.assertFalse(result["safe_to_commit"])
        self.assertIn("scanner failed", result["reason"])

    def test_scan_diff_never_uses_external_diff_drivers(self):
        with mock.patch("opcoding.gitops.run_command") as run:
            run.return_value = self._completed()
            secret_scan_diff(Path("."), staged=True)
        argv = run.call_args.args[0]
        self.assertIn("--no-ext-diff", argv)
        self.assertIn("--cached", argv)
        self.assertEqual(argv[-2:], ["--", "."])


class ReviewerAndCliTests(unittest.TestCase):
    def test_review_diff_rejects_malicious_base_before_any_git_call(self):
        from opcoding.reviewer import review_diff

        with mock.patch("opcoding.gitops.run_command") as run:
            with self.assertRaises(ValueError):
                review_diff(Path("."), base="--output=/tmp/x")
        run.assert_not_called()

    def test_review_reports_a_p0_when_the_secret_scan_cannot_run(self):
        from opcoding.reviewer import review_diff

        broken = {
            "hits": [],
            "scanner_ok": False,
            "safe_to_commit": False,
            "reason": "git diff failed or timed out; result is unreadable",
        }
        with mock.patch("opcoding.reviewer.secret_scan_diff", return_value=broken):
            with tempfile.TemporaryDirectory() as tmp:
                root = make_repo(Path(tmp), commit=True)
                result = review_diff(root)
        titles = [item["title"] for item in result["findings"]]
        self.assertIn("Secret scan could not run", titles)
        self.assertEqual(result["findings"][0]["severity"], "P0")

    def test_cli_reports_rejected_refs_as_errors(self):
        import contextlib
        import io
        import json

        from opcoding.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            make_repo(Path(tmp), commit=True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["git", tmp, "summary", "--base=--ext-diff"])
        self.assertEqual(code, 2)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertIn("ref", payload["message"].lower())

    def test_hooks_check_blocks_when_scan_is_unreadable(self):
        from opcoding.hooks import hooks_check

        broken = {
            "hits": [],
            "scanner_ok": False,
            "safe_to_commit": False,
            "reason": "git diff failed",
        }
        with mock.patch("opcoding.hooks.secret_scan_diff", return_value=broken):
            import contextlib
            import io

            with contextlib.redirect_stdout(io.StringIO()):
                code = hooks_check(Path("."))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
