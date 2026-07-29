"""Canonical repository identity and stale-handle safety tests (#536)."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from opaihub.repository_safety import (
    capture_repository_handle,
    parse_porcelain_v2,
    revalidate_repository_handle,
)


def _git(root: Path, *args: str) -> str:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GIT_CONFIG_")
    }
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return result.stdout.strip()


def _tree_and_index_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file() and ".git" not in item.parts
    ):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    index = root / ".git" / "index"
    if index.exists():
        digest.update(index.read_bytes())
    return digest.hexdigest()


class RepositoryCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "repo"
        root.mkdir()
        self.repo = make_repo(
            root,
            files={"src/app.py": "print('original')\n"},
            commit=True,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_capture_records_canonical_git_and_filesystem_identity(self) -> None:
        _git(
            self.repo,
            "remote",
            "add",
            "origin",
            "https://secret-token@github.com/acme/demo.git",
        )

        handle = capture_repository_handle(
            self.repo / "src", task_id="task-1", run_id="run-1"
        )

        self.assertTrue(handle.identity.repository_id)
        self.assertEqual(handle.identity.worktree_root, self.repo.resolve())
        self.assertEqual(handle.identity.head_sha, _git(self.repo, "rev-parse", "HEAD"))
        self.assertFalse(handle.identity.detached)
        self.assertEqual(handle.identity.branch, _git(self.repo, "branch", "--show-current"))
        self.assertIn(("origin", "https://github.com/acme/demo.git"), handle.identity.remotes)
        self.assertNotIn("secret-token", repr(handle))
        self.assertEqual(handle.dirty_state.changed_paths, ())

    def test_porcelain_v2_parser_keeps_spaces_unicode_and_categories(self) -> None:
        dirty = parse_porcelain_v2(
            b"1 M. N... 100644 100644 100644 abc abc file with space.py\0"
            b"? caf\xc3\xa9.txt\0"
            b"! ignored.tmp\0"
            b"u UU N... 100644 100644 100644 100644 abc abc abc conflict.txt\0"
        )

        self.assertEqual(dirty.staged, ("file with space.py",))
        self.assertEqual(dirty.untracked, ("caf\u00e9.txt",))
        self.assertEqual(dirty.ignored, ("ignored.tmp",))
        self.assertEqual(dirty.conflicted, ("conflict.txt",))
        self.assertEqual(
            dirty.changed_paths,
            ("file with space.py", "caf\u00e9.txt", "conflict.txt"),
        )

    def test_capture_tracks_actual_untracked_and_ignored_paths_separately(self) -> None:
        (self.repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        (self.repo / "untracked caf\u00e9.txt").write_text("u", encoding="utf-8")
        (self.repo / "ignored").mkdir()
        (self.repo / "ignored" / "keep.out").write_text("i", encoding="utf-8")

        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")

        self.assertIn("untracked caf\u00e9.txt", handle.dirty_state.untracked)
        self.assertIn("ignored/", handle.dirty_state.ignored)
        self.assertNotIn("ignored/", handle.dirty_state.changed_paths)

    def test_revalidation_detects_head_branch_remote_and_dirty_changes(self) -> None:
        _git(self.repo, "remote", "add", "origin", "https://github.com/acme/demo.git")
        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")
        (self.repo / "src" / "app.py").write_text("print('moved')\n", encoding="utf-8")
        _git(self.repo, "add", "src/app.py")
        _git(self.repo, "commit", "-m", "move head")
        _git(self.repo, "remote", "set-url", "origin", "https://github.com/acme/other.git")

        result = revalidate_repository_handle(handle)

        self.assertFalse(result.fresh)
        self.assertIn("head_changed", result.reasons)
        self.assertIn("remote_changed", result.reasons)

    def test_revalidation_reports_missing_repository_without_guessing(self) -> None:
        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")
        self.repo.rename(Path(self._tmp.name) / "moved-repository")

        result = revalidate_repository_handle(handle)

        self.assertFalse(result.fresh)
        self.assertIn("repository_missing", result.reasons)
        self.assertIsNone(result.current)

    def test_capture_and_revalidation_are_read_only(self) -> None:
        before = _tree_and_index_digest(self.repo)

        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")
        result = revalidate_repository_handle(handle)

        self.assertTrue(result.fresh)
        self.assertEqual(_tree_and_index_digest(self.repo), before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
