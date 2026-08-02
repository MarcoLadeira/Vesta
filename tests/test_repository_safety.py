"""Canonical repository identity and stale-handle safety tests (#536)."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hypothesis import given, strategies as st

from _helpers import make_repo

from opaihub.repository_safety import (
    DirtyState,
    RepositorySafetyError,
    RepositorySafetyPersistenceError,
    capture_repository_handle,
    classify_dirty_state,
    load_repository_handle,
    parse_porcelain_v2,
    revalidate_repository_handle,
    require_mutation_permitted,
    save_repository_handle,
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
        item for item in root.rglob("*") if item.is_file() and ".git" not in item.parts
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
        self.assertEqual(
            handle.identity.branch, _git(self.repo, "branch", "--show-current")
        )
        self.assertIn(
            ("origin", "https://github.com/acme/demo.git"), handle.identity.remotes
        )
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
        _git(
            self.repo,
            "remote",
            "set-url",
            "origin",
            "https://github.com/acme/other.git",
        )

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


class UnbornHeadTests(unittest.TestCase):
    """A `git init` with no commit yet is a real repository (#295).

    Requiring `rev-parse HEAD` to succeed conflated "has history" with "is a Git
    repository". A brand-new project — one of the most common places a user
    starts, and the exact case for "build me an app" — has an unborn HEAD, so
    every edit-capable run in it was refused with "OPai could not establish and
    persist a fresh repository identity": wrong, and nothing the user could act
    on. It also turned `main` red.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "fresh"
        root.mkdir()
        self.repo = make_repo(root, files={"app.py": "print('hi')\n"}, commit=False)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_repository_with_no_commits_can_still_be_captured(self) -> None:
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        self.assertTrue(handle.identity.repository_id)
        self.assertEqual(handle.identity.worktree_root, self.repo.resolve())

    def test_an_unborn_head_is_reported_as_empty_not_invented(self) -> None:
        # "" is the honest identity for "no commit yet". Inventing a sha, or
        # borrowing one from anywhere, would make the staleness check lie.
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        self.assertEqual(handle.identity.head_sha, "")

    def test_an_unborn_branch_is_not_mistaken_for_a_detached_head(self) -> None:
        # `symbolic-ref HEAD` still resolves on an unborn branch, so the run is
        # on a branch — detached-HEAD safety rules must not fire here.
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        self.assertTrue(handle.identity.branch)
        self.assertFalse(handle.identity.detached)

    def test_the_first_commit_registers_as_a_head_change(self) -> None:
        # The empty sha must still participate in staleness detection: "" -> sha
        # is precisely the change the comparison exists to catch.
        before = capture_repository_handle(self.repo, task_id="t", run_id="r")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "first")
        validation = revalidate_repository_handle(before)
        self.assertFalse(validation.fresh)
        self.assertIn("head_changed", validation.reasons)


class IndexFingerprintStabilityTests(unittest.TestCase):
    """The index fingerprint tracks staged content, not git's bookkeeping.

    It used to hash the raw ``.git/index`` bytes, which carry each entry's stat
    cache. Git rewrites that cache on its own schedule -- refreshing "racily
    clean" entries, and rolling the index back when a partial ``git commit``
    fails. The observed failure: ``git add`` refreshed the cache, a handle was
    captured, the commit failed with "nothing to commit", git restored the
    previous index, and the next perfectly ordinary edit was refused as
    REPOSITORY_SAFETY_BLOCKED / index_changed. Nothing unsafe had happened.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "repo"
        root.mkdir()
        self.repo = make_repo(root, files={"app.py": "x = 1\n"}, commit=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_failed_partial_commit_does_not_invalidate_the_handle(self) -> None:
        # The exact sequence that was blocking edits.
        _git(self.repo, "add", "--", "app.py")
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        # Fails: nothing staged differs from HEAD. Git restores the index.
        # `_git` asserts success, and this command is *meant* to fail.
        subprocess.run(  # nosec B603 B607 - fixed argv, throwaway test repo
            ["git", "commit", "-m", "nothing", "--", "app.py"],
            cwd=self.repo,
            capture_output=True,
            check=False,
        )

        self.assertTrue(revalidate_repository_handle(handle).fresh)

    def test_repeated_probes_agree_when_nothing_changed(self) -> None:
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        for _ in range(5):
            self.assertTrue(revalidate_repository_handle(handle).fresh)

    def test_staging_different_content_is_still_caught(self) -> None:
        # The property the fingerprint exists for. A guard that stopped noticing
        # real staging changes would be worse than no guard at all.
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        (self.repo / "app.py").write_text("x = 999\n", encoding="utf-8")
        _git(self.repo, "add", "--", "app.py")

        validation = revalidate_repository_handle(handle)
        self.assertFalse(validation.fresh)
        self.assertIn("index_changed", validation.reasons)

    def test_staging_a_new_file_is_still_caught(self) -> None:
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        (self.repo / "new.py").write_text("y = 1\n", encoding="utf-8")
        _git(self.repo, "add", "--", "new.py")

        validation = revalidate_repository_handle(handle)
        self.assertFalse(validation.fresh)
        self.assertIn("index_changed", validation.reasons)


class RepositorySafetyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "repo"
        root.mkdir()
        self.repo = make_repo(
            root,
            files={"src/app.py": "print('original')\n", "docs/guide.md": "guide\n"},
            commit=True,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unknown_scope_and_malformed_state_fail_closed(self) -> None:
        unknown = classify_dirty_state(
            DirtyState(untracked=("notes.txt",)), planned_paths=None
        )
        malformed = classify_dirty_state(
            DirtyState(malformed_records=("x:bad",)), planned_paths=("src/app.py",)
        )

        self.assertEqual(unknown.outcome, "block")
        self.assertEqual(unknown.rule_id, "unknown_scope")
        self.assertEqual(malformed.outcome, "block")
        self.assertEqual(malformed.classification, "unsafe")

    def test_classifier_distinguishes_clean_compatible_unrelated_and_overlap(
        self,
    ) -> None:
        clean = classify_dirty_state(DirtyState(), planned_paths=("src/app.py",))
        compatible = classify_dirty_state(
            DirtyState(unstaged=("generated/report.json",)),
            planned_paths=("src/app.py",),
            opai_owned_paths=("generated/",),
        )
        unrelated = classify_dirty_state(
            DirtyState(unstaged=("docs/guide.md",)), planned_paths=("src/app.py",)
        )
        overlap = classify_dirty_state(
            DirtyState(unstaged=("src/app.py",)), planned_paths=("src/",)
        )

        self.assertEqual((clean.classification, clean.outcome), ("clean", "proceed"))
        self.assertEqual(
            (compatible.classification, compatible.outcome),
            ("compatible", "proceed_carefully"),
        )
        self.assertEqual(
            (unrelated.classification, unrelated.outcome), ("unrelated", "isolate")
        )
        self.assertEqual(
            (overlap.classification, overlap.outcome), ("overlapping", "block")
        )
        self.assertEqual(overlap.overlapping_paths, ("src/app.py",))

    def test_explicit_owned_file_can_continue_its_own_pending_mutation(self) -> None:
        assessment = classify_dirty_state(
            DirtyState(unstaged=("src/app.py",)),
            planned_paths=("src/app.py",),
            opai_owned_paths=("src/app.py",),
        )

        self.assertEqual(assessment.classification, "compatible")
        self.assertEqual(assessment.outcome, "proceed_carefully")

    def test_gate_revalidates_immediately_before_write(self) -> None:
        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")
        (self.repo / "src" / "other.py").write_text("changed\n", encoding="utf-8")

        with self.assertRaises(RepositorySafetyError) as context:
            require_mutation_permitted(
                handle, planned_paths=("src/app.py",), operation="write_file"
            )

        self.assertIn("dirty_state_changed", context.exception.decision.reasons)

    def test_revalidation_detects_changed_contents_with_the_same_dirty_status(
        self,
    ) -> None:
        target = self.repo / "src" / "app.py"
        target.write_text("print('first')\n", encoding="utf-8")
        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")
        target.write_text("print('second')\n", encoding="utf-8")

        result = revalidate_repository_handle(handle)

        self.assertFalse(result.fresh)
        self.assertIn("working_tree_changed", result.reasons)

    def test_gate_refuses_unrelated_user_changes_without_isolation(self) -> None:
        (self.repo / "docs" / "guide.md").write_text("user edit\n", encoding="utf-8")
        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")

        with self.assertRaises(RepositorySafetyError) as context:
            require_mutation_permitted(
                handle, planned_paths=("src/app.py",), operation="write_file"
            )

        self.assertEqual(context.exception.decision.assessment.outcome, "isolate")


class RepositorySafetyPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "repo"
        root.mkdir()
        self.repo = make_repo(root, files={"src/app.py": "print('ok')\n"}, commit=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_handle_round_trips_without_remote_credentials(self) -> None:
        _git(
            self.repo,
            "remote",
            "add",
            "origin",
            "https://secret-token@github.com/acme/demo.git",
        )
        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")

        path = save_repository_handle(self.repo, handle)
        loaded = load_repository_handle(self.repo, handle.handle_id)

        self.assertEqual(loaded.handle_id, handle.handle_id)
        self.assertEqual(loaded.identity.repository_id, handle.identity.repository_id)
        self.assertNotIn("secret-token", path.read_text(encoding="utf-8"))

    def test_corrupt_or_unwritable_handle_is_degraded_not_fresh(self) -> None:
        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")
        path = save_repository_handle(self.repo, handle)
        path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(RepositorySafetyPersistenceError):
            load_repository_handle(self.repo, handle.handle_id)
        with mock.patch(
            "opaihub.repository_safety.atomic_write_text", side_effect=OSError("no")
        ):
            with self.assertRaises(RepositorySafetyPersistenceError):
                save_repository_handle(self.repo, handle)


class RepositorySafetyPropertyTests(unittest.TestCase):
    @given(st.lists(st.text(min_size=1, max_size=40), max_size=40))
    def test_path_overlap_classifier_never_allows_unknown_scope(
        self, paths: list[str]
    ) -> None:
        assessment = classify_dirty_state(
            DirtyState(untracked=tuple(paths)), planned_paths=None
        )
        self.assertEqual(assessment.outcome, "block")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
