"""The cancellation token actually reaches git tools and PR creation (#380).

Before this, `RepositoryToolExecutor.invoke()` checked `cancel` exactly once,
before dispatching to whichever tool method — so once `_git_commit` or
`_git_push` started, nothing downstream ever looked at the token again, and a
cancellation firing between two internal git calls (stage, then commit; push,
then open a PR) was invisible until the *next whole tool call*. These prove
the token is now threaded all the way through, and that a cancelled attempt is
reported as CANCELLED — never disguised as an ordinary failure, which matters
because a retry policy must never retry a cancellation (#380: "separate
retryable faults from cancellation").
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from opaihub import idempotency
from opaihub.provider_tools import RepositoryToolExecutor

from tests._helpers import make_repo


def _executor(root: Path, **kwargs) -> RepositoryToolExecutor:
    kwargs.setdefault("allow_edits", True)
    return RepositoryToolExecutor(root, **kwargs)


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _RecordingGitRun:
    """A ``git_run`` fake that flips ``cancel`` once a chosen call succeeds.

    Records every argv it was actually asked to run, so a test can assert a
    *later* step in the chain never reached a subprocess — robust to
    whatever else the executor legitimately does internally (repository
    safety re-checks, etc.), unlike failing on any unexpected call.
    """

    def __init__(
        self, cancel: threading.Event, trigger_suffix: tuple[str, ...]
    ) -> None:
        self.cancel = cancel
        self.trigger_suffix = trigger_suffix
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if tuple(argv[-len(self.trigger_suffix) :]) == self.trigger_suffix:
            self.cancel.set()
        return _FakeCompleted(0, "", "")

    def ran(self, *suffix: str) -> bool:
        return any(tuple(call[-len(suffix) :]) == suffix for call in self.calls)


class GitCommitCancellationTests(unittest.TestCase):
    def test_a_commit_already_cancelled_is_refused_before_any_git_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            fake = _RecordingGitRun(threading.Event(), ("never", "triggers"))
            executor = _executor(root, git_run=fake)
            executor.invoke("write_file", {"path": "f.txt", "content": "hi"})
            cancel = threading.Event()
            cancel.set()
            result = executor.invoke("git_commit", {"message": "wip"}, cancel=cancel)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error_code"], "CANCELLED")
            self.assertEqual(fake.calls, [])

    def test_cancelling_right_after_staging_stops_write_tree_from_ever_running(
        self,
    ) -> None:
        cancel = threading.Event()
        fake = _RecordingGitRun(cancel, ("add", "--", "f.txt"))
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root, git_run=fake)
            executor.invoke("write_file", {"path": "f.txt", "content": "hi"})
            result = executor.invoke("git_commit", {"message": "wip"}, cancel=cancel)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_code"], "CANCELLED")
        self.assertTrue(fake.ran("add", "--", "f.txt"))
        self.assertFalse(fake.ran("write-tree"))
        self.assertFalse(fake.ran("commit", "-m", mock.ANY, "--", "f.txt"))

    def test_cancelling_right_before_the_commit_stops_it_from_ever_running(
        self,
    ) -> None:
        cancel = threading.Event()
        fake = _RecordingGitRun(cancel, ("write-tree",))
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root, git_run=fake)
            executor.invoke("write_file", {"path": "f.txt", "content": "hi"})
            result = executor.invoke("git_commit", {"message": "wip"}, cancel=cancel)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_code"], "CANCELLED")
        self.assertTrue(fake.ran("write-tree"))
        # write-tree's own output feeds the idempotency key; committing after
        # the cancellation was observed would be exactly the bug #380 names.
        self.assertFalse(any("commit" in call and "-m" in call for call in fake.calls))

    def test_a_cancelled_commit_leaves_no_idempotency_residue(self) -> None:
        """The key must not be stuck IN_FLIGHT — a corrected retry must be free."""

        cancel = threading.Event()
        fake = _RecordingGitRun(cancel, ("write-tree",))
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root, git_run=fake)
            executor.invoke("write_file", {"path": "f.txt", "content": "hi"})
            cancelled_result = executor.invoke(
                "git_commit", {"message": "wip"}, cancel=cancel
            )
            self.assertEqual(cancelled_result["error_code"], "CANCELLED")

            # A fresh, uncancelled retry of the exact same content must land
            # normally — not "uncertain", not silently treated as already done.
            real_result = executor.invoke(
                "git_commit", {"message": "wip"}, cancel=threading.Event()
            )
        self.assertTrue(real_result["ok"], real_result)
        self.assertNotEqual(real_result.get("error_code"), "COMMIT_STATE_UNCERTAIN")


class GitPushCancellationTests(unittest.TestCase):
    def test_a_cancelled_push_is_reported_as_cancelled_not_git_push_failed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(
                root, allow_git_ops=True, allow_command="git push -u origin feat/x"
            )
            executor.invoke("git_create_branch", {"name": "feat/x"})
            cancel = threading.Event()
            cancel.set()
            with mock.patch.object(executor, "_git") as git:
                result = executor.invoke(
                    "git_push", {"branch": "feat/x"}, cancel=cancel
                )
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error_code"], "CANCELLED")
            # invoke()'s own top-level check catches an already-cancelled
            # call before dispatch — push never even reaches a git process.
            git.assert_not_called()

    def test_cancelling_mid_push_is_reported_as_cancelled_not_git_push_failed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(
                root, allow_git_ops=True, allow_command="git push -u origin feat/x"
            )
            executor.invoke("git_create_branch", {"name": "feat/x"})
            cancel = threading.Event()
            with mock.patch.object(
                executor,
                "_git",
                return_value={"ok": False, "output": "cancelled", "cancelled": True},
            ) as git:
                result = executor.invoke(
                    "git_push", {"branch": "feat/x"}, cancel=cancel
                )
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error_code"], "CANCELLED")
            git.assert_called_once()
            self.assertIs(git.call_args.kwargs["cancel"], cancel)


class OpenPrCancellationTests(unittest.TestCase):
    """The literal scenario #380 names: "between push and PR creation"."""

    def test_a_cancellation_after_push_refuses_the_pr_before_it_is_created(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(
                root, allow_git_ops=True, allow_command="git push -u origin feat/x"
            )
            executor.invoke("git_create_branch", {"name": "feat/x"})
            cancel = threading.Event()

            with mock.patch.object(
                executor, "_git", return_value={"ok": True, "output": ""}
            ):
                pushed = executor.invoke(
                    "git_push", {"branch": "feat/x"}, cancel=cancel
                )
            self.assertTrue(pushed["ok"], pushed)

            # The push landed; the user cancels in the gap before the PR.
            cancel.set()
            with mock.patch(
                "opaihub.github_connector.create_pull_request"
            ) as create_pr:
                result = executor.invoke(
                    "open_pr", {"title": "Fix the thing"}, cancel=cancel
                )
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error_code"], "CANCELLED")
            create_pr.assert_not_called()

    def test_no_idempotency_key_is_claimed_for_a_pre_cancelled_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(
                root, allow_git_ops=True, allow_command="git push -u origin feat/x"
            )
            executor.invoke("git_create_branch", {"name": "feat/x"})
            with mock.patch.object(
                executor, "_git", return_value={"ok": True, "output": ""}
            ):
                executor.invoke(
                    "git_push", {"branch": "feat/x"}, cancel=threading.Event()
                )
            cancel = threading.Event()
            cancel.set()
            executor.invoke("open_pr", {"title": "Fix the thing"}, cancel=cancel)

            key = idempotency.operation_key(
                "open_pr",
                root=str(root),
                head="feat/x",
                base="main",
                title="Fix the thing",
            )
            self.assertEqual(idempotency.status(root, key)["state"], idempotency.FRESH)


class RunCommandWiringTests(unittest.TestCase):
    """``aci.py``'s own tests already prove real process-tree termination;
    this proves the plumbing above it — that a cancel token given to
    ``invoke()`` actually reaches ``aci.run_command`` for a real tool call."""

    def test_invoke_run_command_forwards_the_same_cancel_object_to_the_aci(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root)
            cancel = threading.Event()
            with mock.patch.object(
                executor.aci, "run_command", wraps=executor.aci.run_command
            ) as run_command:
                result = executor.invoke(
                    "run_command", {"command": "git status --short"}, cancel=cancel
                )
            self.assertTrue(result["ok"], result)
            run_command.assert_called_once()
            self.assertIs(run_command.call_args.kwargs["cancel"], cancel)

    def test_run_tests_forwards_cancel_to_the_aci_as_well(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            executor = _executor(root)
            executor.test_commands = {"unit": ["python", "-c", "pass"]}
            cancel = threading.Event()
            with mock.patch.object(
                executor.aci, "run_tests", wraps=executor.aci.run_tests
            ) as run_tests:
                result = executor.invoke(
                    "run_tests", {"command_id": "unit"}, cancel=cancel
                )
            self.assertTrue(result["ok"], result)
            run_tests.assert_called_once()
            self.assertIs(run_tests.call_args.kwargs["cancel"], cancel)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
