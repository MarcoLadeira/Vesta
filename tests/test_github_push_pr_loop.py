"""End-to-end proof that a free (Gemini-class) runner can open a PR (#300).

Drives the real ``FreeAPIRunner.complete_with_tools`` OpenAI-compatible tool loop
through create-branch -> write -> commit -> push -> open_pr. The model responses
are scripted (injected transport) and the git push targets a **local bare
remote** — so the git operations are real but nothing touches the network. Only
the final GitHub PR API call is mocked.
"""

from __future__ import annotations

import contextlib
import json
import subprocess  # nosec B404 - fixed git argv in a temp repo
import unittest
from pathlib import Path
from unittest import mock

from vestahub import github_connector as gc
from vestahub.local_runner import FreeAPIRunner

from tests._helpers import isolated_home, make_repo


class _FakeStore:
    saved: dict[str, str] = {}

    def get(self, provider):
        return self.saved.get(provider)

    def set(self, provider, secret):
        self.saved[provider] = secret
        return {"stored": True}

    def delete(self, provider):
        return {"deleted": self.saved.pop(provider, None) is not None}


def _tool_turn(calls):
    """One OpenAI-style assistant turn that requests tool calls."""
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for i, (name, args) in enumerate(calls)
                    ],
                }
            }
        ],
        "usage": {},
    }


def _final_turn(text):
    return {"choices": [{"message": {"content": text}}], "usage": {}}


def _git(root: Path, *args):
    subprocess.run(  # nosec B603 - fixed git argv, no shell
        ["git", *args], cwd=str(root), check=True, capture_output=True
    )


class FreeRunnerPushPrLoopTests(unittest.TestCase):
    def setUp(self):
        _FakeStore.saved = {"github": "ghp_test_token"}
        self._ctx = contextlib.ExitStack()
        self._ctx.enter_context(isolated_home())
        self._ctx.enter_context(mock.patch.object(gc, "CredentialStore", _FakeStore))
        self._ctx.enter_context(
            mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""})
        )
        gc.set_push_allowed(True)  # consent on + token present => git ops exposed
        self.addCleanup(self._ctx.close)

    def _repo_with_local_remote(self, tmp: str) -> Path:
        work = Path(tmp) / "work"
        work.mkdir()  # make_repo runs git with cwd=work, so it must exist first
        root = make_repo(work, commit=True)
        bare = Path(tmp) / "remote.git"
        subprocess.run(  # nosec B603
            ["git", "init", "--bare", str(bare)], check=True, capture_output=True
        )
        _git(root, "remote", "add", "origin", str(bare))
        return root

    def test_a_free_runner_opens_a_pr_end_to_end(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo_with_local_remote(tmp)
            runner = FreeAPIRunner(
                "https://api.example.test/v1", "gemini-3.1-flash-lite", "test-key"
            )
            script = [
                _tool_turn([("git_create_branch", {"name": "feat/loop"})]),
                _tool_turn([("write_file", {"path": "f.txt", "content": "hello\n"})]),
                _tool_turn([("git_commit", {"message": "Add f.txt"})]),
                _tool_turn([("git_push", {})]),
                _tool_turn(
                    [("open_pr", {"title": "Add f.txt", "body": "adds a file"})]
                ),
                _final_turn("Opened the pull request."),
            ]
            with (
                mock.patch.object(runner, "_chat", side_effect=script),
                mock.patch(
                    "vestahub.github_connector.create_pull_request",
                    return_value={
                        "ok": True,
                        "url": "https://github.com/o/r/pull/9",
                        "number": 9,
                    },
                ) as fake_pr,
            ):
                result = runner.complete_with_tools(
                    "Create a file and open a PR",
                    project_root=root,
                    allow_edits=True,
                    # Round 5 finding 1: the push now needs the user's one-time
                    # approval as well as Settings consent, so the end-to-end
                    # proof runs with that approval armed — exactly what the GUI
                    # threads back when the user clicks "Approve once". Opening
                    # the PR rides on the approved push, in the same turn.
                    allow_command="git push -u origin feat/loop",
                )

            trace = {t["tool"]: t for t in result["tool_trace"]}
            # Every step of the loop succeeded...
            for tool in ("git_create_branch", "write_file", "git_commit", "git_push"):
                self.assertIn(tool, trace, result["tool_trace"])
                self.assertTrue(trace[tool]["ok"], trace[tool])
            # ...the real push reached the local bare remote...
            branches = subprocess.run(  # nosec B603
                ["git", "branch", "-a"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertIn("origin/feat/loop", branches)
            # ...and the PR was opened from the pushed branch.
            self.assertTrue(trace["open_pr"]["ok"], trace["open_pr"])
            self.assertEqual(fake_pr.call_args.kwargs["head"], "feat/loop")
            self.assertEqual(result["text"], "Opened the pull request.")

    def test_the_loop_is_blocked_without_consent(self):
        # Same script, but consent off -> the git-ops tools are never exposed, so
        # git_push is refused (TOOL_NOT_ALLOWED) and no PR is attempted.
        import tempfile

        gc.set_push_allowed(False)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo_with_local_remote(tmp)
            runner = FreeAPIRunner("https://api.example.test/v1", "gemini-x", "k")
            script = [
                _tool_turn([("git_create_branch", {"name": "feat/x"})]),
                _tool_turn([("git_push", {})]),
                _final_turn("done"),
            ]
            with (
                mock.patch.object(runner, "_chat", side_effect=script),
                mock.patch("vestahub.github_connector.create_pull_request") as fake_pr,
            ):
                result = runner.complete_with_tools(
                    "push it", project_root=root, allow_edits=True
                )
            push = next(t for t in result["tool_trace"] if t["tool"] == "git_push")
            self.assertFalse(push["ok"])
            self.assertEqual(push["error_code"], "TOOL_NOT_ALLOWED")
            fake_pr.assert_not_called()

    def test_the_loop_stops_for_approval_when_consent_alone_is_granted(self):
        # Round 5 finding 1: Settings consent exposes the push tool; it is not a
        # standing approval to push. Without the one-shot grant the loop stops and
        # asks — the confirmation the Pin Full Auto dialog promises — and nothing
        # reaches the remote.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo_with_local_remote(tmp)
            runner = FreeAPIRunner("https://api.example.test/v1", "gemini-x", "k")
            script = [
                _tool_turn([("git_create_branch", {"name": "feat/ask"})]),
                _tool_turn([("git_push", {})]),
                _final_turn("done"),
            ]
            with (
                mock.patch.object(runner, "_chat", side_effect=script),
                mock.patch("vestahub.github_connector.create_pull_request") as fake_pr,
            ):
                result = runner.complete_with_tools(
                    "push it", project_root=root, allow_edits=True
                )
            push = next(t for t in result["tool_trace"] if t["tool"] == "git_push")
            self.assertFalse(push["ok"])
            self.assertEqual(push["error_code"], "COMMAND_NEEDS_APPROVAL")
            fake_pr.assert_not_called()
            # The branch never reached the remote.
            branches = subprocess.run(  # nosec B603
                ["git", "branch", "-a"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertNotIn("origin/feat/ask", branches)
            # And the loop surfaced it as a consent stop, not a completion.
            self.assertEqual(result.get("stopped_reason"), "approval_required")


if __name__ == "__main__":
    unittest.main()
