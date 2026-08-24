"""#616 fault-injection matrix: crash, timeout, replay and restart at every
external boundary must create zero duplicate effects.

The matrix's uniform fault is the window the issue names: the external
system accepted the operation, but OPai died before recording it. Every
adapter is exercised by letting the real side effect complete and then
raising from ``idempotency.complete`` — the exact crash-after-success
window. The next attempt runs in a FRESH executor (a restarted process)
and must resolve to one operation identity: reconcile-and-confirm where
the effect is observable, fail closed where it is not. Never a duplicate.

Restart semantics are structural here, not simulated by flag: each attempt
builds a new executor/adapter against the same repository, so only the
persisted operation store carries state across the "restart".

Concurrency: 10,000 raced claim sequences prove one winner per operation.
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opaihub import idempotency
from opaihub.idempotency import DONE, FRESH, IN_FLIGHT, begin, operation_key, status


def _executor(root: Path, **kwargs):
    from opaihub.provider_tools import RepositoryToolExecutor

    defaults = {"allow_edits": True, "allow_git_ops": True, "allow_github_write": True}
    defaults.update(kwargs)
    return RepositoryToolExecutor(root, **defaults)


def _pr_executor(root: Path):
    executor = _executor(root)
    executor._current_branch = lambda: "feature-branch"  # type: ignore[method-assign]
    return executor


def _adapter(root: Path, run):
    from opaihub.github_workflow import GitHubAdapter

    adapter = GitHubAdapter(root)
    adapter._run = run  # type: ignore[method-assign]
    return adapter


class _CrashAfterSuccess:
    """Patch idempotency.complete to die exactly once — the process crash
    between 'the external system accepted the operation' and 'OPai recorded
    the outcome'."""

    def __init__(self) -> None:
        self._real = idempotency.complete
        self.crashed = False

    def __enter__(self):
        def flaky_complete(*args, **kwargs):
            if not self.crashed:
                self.crashed = True
                raise idempotency.OperationPersistenceError(
                    "simulated crash after dispatch"
                )
            return self._real(*args, **kwargs)

        self._patch = mock.patch.object(idempotency, "complete", flaky_complete)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


class FaultMatrixTests(unittest.TestCase):
    """For every adapter: the external effect happens at most once, however
    the crash, timeout, replay and restart points line up."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name), commit=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _commits(self) -> list[str]:
        out = subprocess.run(
            ["git", "log", "--format=%s"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return [line for line in out.stdout.splitlines() if line.strip()]

    def test_git_commit_crash_after_success_never_duplicates(self) -> None:
        first_executor = _executor(self.root)
        first_executor.invoke("write_file", {"path": "f.txt", "content": "v1"})
        with _CrashAfterSuccess():
            with self.assertRaises(idempotency.OperationPersistenceError):
                first_executor.invoke("git_commit", {"message": "Add f.txt"})
        # The commit landed; only the record was lost. A restarted process
        # must block as uncertain — not commit the same content again.
        restarted = _executor(self.root)
        restarted.invoke("write_file", {"path": "f.txt", "content": "v1"})
        result = restarted.invoke("git_commit", {"message": "Add f.txt"})
        self.assertEqual(result["error_code"], "COMMIT_STATE_UNCERTAIN")
        self.assertEqual(self._commits().count("Add f.txt"), 1)

    def test_git_create_branch_crash_after_success_never_duplicates(self) -> None:
        with _CrashAfterSuccess():
            with self.assertRaises(idempotency.OperationPersistenceError):
                _executor(self.root).invoke(
                    "git_create_branch", {"name": "feat/matrix"}
                )
        result = _executor(self.root).invoke(
            "git_create_branch", {"name": "feat/matrix"}
        )
        self.assertEqual(result["error_code"], "BRANCH_STATE_UNCERTAIN")
        branches = (
            subprocess.run(
                ["git", "branch", "--list", "feat/matrix"],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .lstrip("* ")
            .strip()
        )
        self.assertEqual(branches, "feat/matrix")

    def test_git_push_crash_after_success_reconciles_via_remote(self) -> None:
        pushes: list[list[str]] = []

        def fake_git(argv, **kwargs):
            if argv[0] == "rev-parse":
                return {"ok": True, "output": "abc123"}
            if argv[0] == "push":
                pushes.append(list(argv))
                return {"ok": True, "output": "pushed"}
            if argv[0] == "ls-remote":
                return {"ok": True, "output": "abc123\trefs/heads/feat/x"}
            return {"ok": True, "output": ""}

        first = _executor(self.root)
        first.grant_command_once("git push -u origin feat/x")
        with mock.patch.object(first, "_git", side_effect=fake_git):
            with _CrashAfterSuccess():
                with self.assertRaises(idempotency.OperationPersistenceError):
                    first._git_push({"branch": "feat/x"})
        restarted = _executor(self.root)
        restarted.grant_command_once("git push -u origin feat/x")
        with mock.patch.object(restarted, "_git", side_effect=fake_git):
            result = restarted._git_push({"branch": "feat/x"})
        self.assertTrue(result["ok"], result)
        self.assertIn("confirmed on origin", result["message"])
        self.assertEqual(len(pushes), 1)

    def test_open_pr_crash_after_success_reconciles_via_github(self) -> None:
        creates: list[dict] = []

        def fake_create(root, **kwargs):
            creates.append(kwargs)
            return {"ok": True, "url": "https://example/pr/9", "number": 9}

        def fake_find(root, *, head, base="main", **kwargs):
            return {
                "ok": True,
                "found": True,
                "url": "https://example/pr/9",
                "number": 9,
            }

        with mock.patch(
            "opaihub.github_connector.create_pull_request", side_effect=fake_create
        ):
            with _CrashAfterSuccess():
                with self.assertRaises(idempotency.OperationPersistenceError):
                    _pr_executor(self.root)._open_pr({"title": "Fix", "base": "main"})
        with mock.patch(
            "opaihub.github_connector.find_pull_request", side_effect=fake_find
        ):
            result = _pr_executor(self.root)._open_pr({"title": "Fix", "base": "main"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["number"], 9)
        self.assertEqual(len(creates), 1)

    def test_comment_crash_after_success_reconciles_via_github(self) -> None:
        posts: list[dict] = []

        def fake_add(root, number, body, **kwargs):
            posts.append({"number": number, "body": body})
            return {"ok": True, "url": "https://example/pr/5#c1"}

        def fake_find(root, number, *, body, **kwargs):
            return {"ok": True, "found": True, "url": "https://example/pr/5#c1"}

        first = _executor(self.root)
        first.grant_command_once("gh pr comment 5")
        with mock.patch("opaihub.github_connector.add_comment", side_effect=fake_add):
            with _CrashAfterSuccess():
                with self.assertRaises(idempotency.OperationPersistenceError):
                    first._github_comment({"number": 5, "body": "ship it"})
        with mock.patch("opaihub.github_connector.find_comment", side_effect=fake_find):
            result = _executor(self.root)._github_comment(
                {"number": 5, "body": "ship it"}
            )
        self.assertTrue(result["ok"], result)
        self.assertIn("confirmed", result["message"])
        self.assertEqual(len(posts), 1)

    def test_review_request_crash_after_success_reconciles_via_github(self) -> None:
        calls: list[dict] = []

        def fake_request(root, number, reviewers, **kwargs):
            calls.append({"number": number})
            return {"ok": True, "requested": list(reviewers)}

        def fake_find(root, number, reviewers, **kwargs):
            return {"ok": True, "found": True}

        first = _executor(self.root)
        first.grant_command_once("gh pr edit 7 --add-reviewer alice")
        with mock.patch(
            "opaihub.github_connector.request_reviewers", side_effect=fake_request
        ):
            with _CrashAfterSuccess():
                with self.assertRaises(idempotency.OperationPersistenceError):
                    first._github_request_review({"number": 7, "reviewers": ["alice"]})
        with mock.patch(
            "opaihub.github_connector.find_requested_reviewers", side_effect=fake_find
        ):
            result = _executor(self.root)._github_request_review(
                {"number": 7, "reviewers": ["alice"]}
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(calls), 1)

    def test_merge_crash_after_success_reconciles_via_pr_state(self) -> None:
        merges: list[list[str]] = []

        def run(args, **kwargs):
            if args[:2] == ["pr", "view"]:
                return '{"state": "MERGED"}'
            merges.append(list(args))
            return "merged"

        with _CrashAfterSuccess():
            with self.assertRaises(idempotency.OperationPersistenceError):
                _adapter(self.root, run).merge_pr(9)
        result = _adapter(self.root, run).merge_pr(9)
        self.assertEqual(result, "confirmed merged")
        self.assertEqual(len(merges), 1)

    def test_comment_pr_crash_after_success_reconciles_via_thread(self) -> None:
        posts: list[list[str]] = []

        def run(args, **kwargs):
            if args[:2] == ["pr", "view"]:
                return '{"comments": [{"body": "ship it"}]}'
            posts.append(list(args))
            return "posted"

        with _CrashAfterSuccess():
            with self.assertRaises(idempotency.OperationPersistenceError):
                _adapter(self.root, run).comment_pr(4, "ship it")
        result = _adapter(self.root, run).comment_pr(4, "ship it")
        self.assertEqual(result, "confirmed on GitHub")
        self.assertEqual(len(posts), 1)

    def test_granted_command_crash_after_success_fails_closed(self) -> None:
        dispatches: list[list[str]] = []

        def fake_run(argv, **kwargs):
            dispatches.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "done", "")

        command = "git push origin HEAD"
        first = _executor(self.root, git_run=fake_run)
        first.grant_command_once(command)
        with _CrashAfterSuccess():
            with self.assertRaises(idempotency.OperationPersistenceError):
                first.invoke("run_command", {"command": command})
        # A command's effects are not observable in general, so the restart
        # must fail closed — the one thing it must never do is run it again.
        restarted = _executor(self.root, git_run=fake_run)
        restarted.grant_command_once(command)
        result = restarted.invoke("run_command", {"command": command})
        self.assertEqual(result["error_code"], "COMMAND_STATE_UNCERTAIN")
        self.assertEqual(len(dispatches), 1)

    def test_write_file_crash_after_success_reconciles_via_disk(self) -> None:
        with _CrashAfterSuccess():
            with self.assertRaises(idempotency.OperationPersistenceError):
                _executor(self.root).invoke(
                    "write_file", {"path": "m.txt", "content": "matrix"}
                )
        result = _executor(self.root).invoke(
            "write_file", {"path": "m.txt", "content": "matrix"}
        )
        self.assertTrue(result["ok"], result)
        self.assertIn("confirmed on disk", result["message"])
        self.assertEqual((self.root / "m.txt").read_text(encoding="utf-8"), "matrix")

    def test_apply_patch_crash_after_success_reconciles_via_reverse_check(
        self,
    ) -> None:
        patch = (
            "diff --git a/a.txt b/a.txt\n"
            "--- a/a.txt\n"
            "+++ b/a.txt\n"
            "@@ -1 +1 @@\n"
            "-seed\n"
            "+hello\n"
        )
        (self.root / "a.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", "a.txt"],
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "-m", "seed a.txt"],
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        with _CrashAfterSuccess():
            with self.assertRaises(idempotency.OperationPersistenceError):
                _executor(self.root).invoke("apply_patch", {"patch": patch})
        result = _executor(self.root).invoke("apply_patch", {"patch": patch})
        self.assertTrue(result["ok"], result)
        self.assertIn("confirmed on disk", result["message"])
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "hello\n")


class DuplicateRaceTests(unittest.TestCase):
    """10,000 raced claim sequences: one intended operation, no duplicate
    effect, however the threads interleave."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name), commit=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_ten_thousand_raced_claims_have_exactly_one_winner_each(self) -> None:
        rounds = 100
        racers = 100  # 100 x 100 = 10,000 raced begin() sequences
        for round_number in range(rounds):
            key = operation_key("race", root=str(self.root), round=round_number)
            outcomes: list[str] = []
            lock = threading.Lock()

            def race() -> None:
                outcome = begin(self.root, key)["state"]
                with lock:
                    outcomes.append(outcome)

            threads = [threading.Thread(target=race) for _ in range(racers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(
                outcomes.count(FRESH),
                1,
                f"round {round_number}: claims {sorted(set(outcomes))}",
            )
            self.assertEqual(outcomes.count(IN_FLIGHT), racers - 1)
            self.assertEqual(status(self.root, key)["state"], IN_FLIGHT)

    def test_concurrent_identical_commands_dispatch_once(self) -> None:
        # A whole adapter raced by 16 threads: the store, not luck, decides
        # the single dispatcher.
        dispatches: list[list[str]] = []
        dispatch_lock = threading.Lock()

        def fake_run(argv, **kwargs):
            with dispatch_lock:
                dispatches.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "done", "")

        command = "git push origin HEAD"
        results: list[dict] = []
        results_lock = threading.Lock()

        def attempt() -> None:
            executor = _executor(self.root, git_run=fake_run)
            executor.grant_command_once(command)
            outcome = executor.invoke("run_command", {"command": command})
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=attempt) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        # The exact-once contract is about the side effect: one dispatch,
        # however the threads interleave.
        self.assertEqual(len(dispatches), 1)
        # The winner sees the direct dispatch; losers either observe DONE and
        # replay the recorded success, or observe IN_FLIGHT and fail closed
        # with the typed uncertain marker — both are the designed protocol,
        # and which one a loser gets is scheduler timing, not correctness.
        # What must never appear is a second dispatch or an untyped failure.
        self.assertEqual(
            sum(1 for r in results if r.get("ok") and not r["data"].get("replayed")),
            1,
            results,
        )
        for result in results:
            if result.get("ok"):
                self.assertEqual(result["data"]["returncode"], 0)
            else:
                self.assertEqual(
                    result["error_code"],
                    "COMMAND_STATE_UNCERTAIN",
                    results,
                )
        self.assertEqual(
            status(
                self.root,
                operation_key("granted_command", root=str(self.root), argv=command),
            )["state"],
            DONE,
        )
        # Once settled, a later identical call replays the recorded success.
        settled = _executor(self.root, git_run=fake_run)
        settled.grant_command_once(command)
        replay = settled.invoke("run_command", {"command": command})
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["data"]["replayed"], replay)
        self.assertEqual(len(dispatches), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
