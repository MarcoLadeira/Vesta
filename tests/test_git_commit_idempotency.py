"""A replayed turn must not commit twice (#295 gate 4).

Gate 4 targets zero duplicate side effects after retry, replay, reconnect or
failover. `open_pr` and `comment_pr` were keyed first because they are visible
to other people. `git_commit` is the remaining one that writes history.

**Git already prevents the duplicate itself**: replaying an identical commit
with an unchanged tree fails with "nothing to commit". Measuring an unkeyed
implementation confirms history does not grow either way, so this is not the
protection the key adds, and claiming otherwise would overstate it.

What the key adds is *honesty about what happened*, plus the case git cannot
see. A replay used to be told `GIT_COMMIT_FAILED` for work that had landed;
since the caller is usually the model, a spurious failure on a successful
commit invites it to amend, force, or commit again differently to fix a problem
that does not exist. And a turn that commits, crashes before recording, then
resumes is genuinely uncertain — git has no opinion on whether Vesta meant to do
that twice.

The interesting constraint is the other half of #295: *consistency while not
limiting user messages and interactions*. A user who commits "wip" twice, with
real work in between, is performing two legitimate operations. Keying on the
message would refuse the second. So the key identifies the staged **content**
via `git write-tree`, which is deterministic, does not touch the worktree or
HEAD, and distinguishes those two cases exactly.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from vestahub.provider_tools import RepositoryToolExecutor


def _git(root: Path, *args: str) -> str:
    return subprocess.run(  # nosec B603 B607 - fixed argv, test repo
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    ).stdout.strip()


class GitCommitIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "repo"
        root.mkdir()
        self.repo = make_repo(root, files={"app.py": "x = 1\n"}, commit=True)
        self.executor = RepositoryToolExecutor(
            self.repo, allow_edits=True, allow_git_ops=True
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _commit(self, message: str = "change one thing"):
        return self.executor._git_commit({"message": message, "paths": ["app.py"]})

    def _write(self, text: str) -> None:
        """Edit through the executor, as a real run does.

        Writing straight to disk makes the executor's repository handle stale
        and the safety gate blocks the commit -- correctly, since from its point
        of view an unrelated party changed the worktree mid-run. The tool path
        keeps the handle fresh and records `written_paths`, which is also what
        supplies the default commit path set.
        """
        result = self.executor._write_file({"path": "app.py", "content": text})
        assert result.get("ok"), result

    def _count_commits(self) -> int:
        out = _git(self.repo, "rev-list", "--count", "HEAD")
        return int(out or 0)

    def test_a_commit_succeeds_and_reports_its_sha(self) -> None:
        self._write("x = 2\n")
        result = self._commit()
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result["data"]["sha"])
        self.assertEqual(self._count_commits(), 2)

    def test_a_replayed_commit_reports_the_one_that_landed(self) -> None:
        """The behaviour the key actually adds.

        Measured against an unkeyed implementation: **git already refuses the
        duplicate**, so history does not grow either way. What changed is what
        the replay is *told*.

            WITHOUT the key   replay.ok=False   GIT_COMMIT_FAILED
            WITH the key      replay.ok=True    Already committed as e46fc0f

        That matters because the caller is usually the model. A spurious
        "commit failed" on work that did land invites it to try something else
        — amend, force, a second commit with a different message — to fix a
        problem that does not exist.
        """
        self._write("x = 2\n")
        first = self._commit()
        before = self._count_commits()
        second = self._commit()

        self.assertTrue(second.get("ok"), second)
        self.assertEqual(second["data"]["sha"], first["data"]["sha"])
        self.assertIn("Already committed", second["message"])
        # Git's own protection, asserted so a regression in either layer shows.
        self.assertEqual(self._count_commits(), before, "history must not grow")

    def test_the_same_message_with_different_work_is_a_real_second_commit(self) -> None:
        # The constraint that shapes the key. Keying on the message alone would
        # refuse this, which is limiting a legitimate interaction.
        self._write("x = 2\n")
        self._commit("wip")
        self._write("x = 3\n")
        second = self._commit("wip")
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(self._count_commits(), 3)

    def test_a_failed_commit_releases_its_key_for_a_corrected_retry(self) -> None:
        # Nothing reached history, so nothing should be deduplicated.
        result = self._commit()  # clean tree -> git refuses
        self.assertFalse(result.get("ok"))
        self._write("x = 2\n")
        retry = self._commit()
        self.assertTrue(retry.get("ok"), retry)

    def test_an_unconfirmed_attempt_reports_uncertainty_rather_than_duplicating(
        self,
    ) -> None:
        # The crash-between-commit-and-record case. Claiming success would
        # assert something unverified; committing again would duplicate.
        # `begin` is stubbed rather than the key reproduced, so the test pins
        # the *behaviour* on an in-flight key and not the key derivation.
        from unittest import mock

        from vestahub import idempotency

        self._write("x = 2\n")
        with mock.patch.object(
            idempotency, "begin", return_value={"state": idempotency.IN_FLIGHT}
        ):
            result = self._commit()

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result["error_code"], "COMMIT_STATE_UNCERTAIN")
        self.assertEqual(self._count_commits(), 1, "no duplicate commit")

    def test_the_recorded_result_holds_no_file_contents(self) -> None:
        # Receipts and stores are durable; a commit body must not leak into one.
        self._write("secret_value = 'hunter2'\n")
        self._commit("add config")
        store = self.repo / ".vestahub" / "health" / "operations.json"
        if store.exists():
            self.assertNotIn("hunter2", store.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
