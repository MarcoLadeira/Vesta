"""Canonical repository identity and stale-handle safety tests (#536)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
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

    def test_capture_batches_standard_worktree_metadata(self) -> None:
        commands: list[tuple[str, ...]] = []

        def counting_run(command: list[str], **kwargs: object):
            commands.append(tuple(command))
            return subprocess.run(command, **kwargs)

        capture_repository_handle(
            self.repo,
            task_id="task-1",
            run_id="run-1",
            git_run=counting_run,
        )

        self.assertLessEqual(len(commands), 6)

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

    def test_capture_and_revalidation_never_touch_git_hooks(self) -> None:
        # Addendum: a command classified as read-only must not mutate hooks.
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        before = hook.read_bytes()

        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")
        revalidate_repository_handle(handle)

        self.assertEqual(hook.read_bytes(), before)

    def test_a_repository_with_no_remote_captures_an_empty_remote_set(self) -> None:
        handle = capture_repository_handle(self.repo, task_id="task-1", run_id="run-1")

        self.assertEqual(handle.identity.remotes, ())


class UnbornHeadTests(unittest.TestCase):
    """A `git init` with no commit yet is a real repository (#295).

    Requiring `rev-parse HEAD` to succeed conflated "has history" with "is a Git
    repository". A brand-new project — one of the most common places a user
    starts, and the exact case for "build me an app" — has an unborn HEAD, so
    every edit-capable run in it was refused with "Vesta could not establish and
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

    @given(st.binary(max_size=2000))
    def test_the_porcelain_parser_never_raises_on_arbitrary_bytes(
        self, raw: bytes
    ) -> None:
        # Untrusted git output, not just well-formed records: unusual
        # encodings and truncated/garbage input must surface as
        # malformed_records, never as an unhandled exception (#536 addendum).
        state = parse_porcelain_v2(raw)
        self.assertIsInstance(state, DirtyState)

    @given(
        st.lists(
            st.text(min_size=1, max_size=80).filter(lambda s: "\0" not in s),
            max_size=300,
        )
    )
    def test_the_classifier_never_raises_on_large_fuzzed_untracked_sets(
        self, paths: list[str]
    ) -> None:
        assessment = classify_dirty_state(
            DirtyState(untracked=tuple(paths)), planned_paths=("src/app.py",)
        )
        self.assertIn(
            assessment.classification,
            {"clean", "compatible", "unrelated", "overlapping", "unsafe", "unknown"},
        )


class IdentityInvalidationTests(unittest.TestCase):
    """Every invalidation event the addendum names, proven individually (#536)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "repo"
        root.mkdir()
        self.repo = make_repo(root, files={"app.py": "print('x')\n"}, commit=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_switching_branches_is_reported_as_branch_changed(self) -> None:
        _git(self.repo, "checkout", "-b", "feature")
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        _git(self.repo, "checkout", "-")

        result = revalidate_repository_handle(handle)

        self.assertFalse(result.fresh)
        self.assertIn("branch_changed", result.reasons)

    def test_replacing_the_repository_at_the_same_path_is_detected(self) -> None:
        # The path-substitution attack the addendum names: delete (or move
        # away) and reinitialize a different repository at the same location.
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        shutil.move(str(self.repo), str(self.repo.parent / "displaced"))
        self.repo.mkdir()
        make_repo(self.repo, files={"app.py": "print('replaced')\n"}, commit=True)

        result = revalidate_repository_handle(handle)

        self.assertFalse(result.fresh)
        self.assertIn("repository_replaced", result.reasons)

    def test_a_handle_older_than_its_max_age_expires(self) -> None:
        clock = [1_000.0]
        handle = capture_repository_handle(
            self.repo,
            task_id="t",
            run_id="r",
            max_age_seconds=30.0,
            now=lambda: clock[0],
        )
        clock[0] += 31.0

        result = revalidate_repository_handle(handle, now=lambda: clock[0])

        self.assertFalse(result.fresh)
        self.assertIn("handle_expired", result.reasons)

    def test_a_handle_within_its_max_age_does_not_expire(self) -> None:
        clock = [1_000.0]
        handle = capture_repository_handle(
            self.repo,
            task_id="t",
            run_id="r",
            max_age_seconds=30.0,
            now=lambda: clock[0],
        )
        clock[0] += 10.0

        result = revalidate_repository_handle(handle, now=lambda: clock[0])

        self.assertNotIn("handle_expired", result.reasons)

    def test_detached_head_is_captured_and_never_silently_fresh(self) -> None:
        sha = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "checkout", sha)

        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        self.assertTrue(handle.identity.detached)

        result = revalidate_repository_handle(handle)
        self.assertFalse(result.fresh)
        self.assertIn("detached_head", result.reasons)


class NestedRepositoryTests(unittest.TestCase):
    """Nested repositories are separate identities; no parent/child traversal (#536)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        outer = Path(self._tmp.name) / "outer"
        outer.mkdir()
        self.outer = make_repo(outer, files={"outer.py": "1\n"}, commit=True)
        inner = self.outer / "vendor" / "inner"
        inner.mkdir(parents=True)
        self.inner = make_repo(inner, files={"inner.py": "2\n"}, commit=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_nested_repository_resolves_its_own_identity_not_its_parents(
        self,
    ) -> None:
        inner_handle = capture_repository_handle(self.inner, task_id="t", run_id="r")
        outer_handle = capture_repository_handle(self.outer, task_id="t", run_id="r")

        self.assertEqual(inner_handle.identity.worktree_root, self.inner.resolve())
        self.assertNotEqual(
            inner_handle.identity.repository_id, outer_handle.identity.repository_id
        )

    def test_capturing_from_inside_the_nested_repository_never_traverses_to_the_parent(
        self,
    ) -> None:
        subdir = self.inner / "src"
        subdir.mkdir()

        handle = capture_repository_handle(subdir, task_id="t", run_id="r")

        self.assertEqual(handle.identity.worktree_root, self.inner.resolve())


class GitNativeWorktreeTests(unittest.TestCase):
    """A `git worktree add` linked worktree is captured correctly (#536)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "repo"
        root.mkdir()
        self.repo = make_repo(root, files={"app.py": "1\n"}, commit=True)
        self.linked = Path(self._tmp.name) / "linked-worktree"
        _git(self.repo, "worktree", "add", "-b", "linked", str(self.linked))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_linked_worktree_has_its_own_git_dir_but_shares_the_common_one(
        self,
    ) -> None:
        primary = capture_repository_handle(self.repo, task_id="t", run_id="r")
        linked = capture_repository_handle(self.linked, task_id="t", run_id="r")

        self.assertEqual(linked.identity.worktree_root, self.linked.resolve())
        self.assertNotEqual(linked.identity.git_dir, primary.identity.git_dir)
        self.assertEqual(
            linked.identity.common_git_dir, primary.identity.common_git_dir
        )
        self.assertEqual(linked.identity.branch, "linked")


class MultipleRemotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "repo"
        root.mkdir()
        self.repo = make_repo(root, files={"app.py": "1\n"}, commit=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_all_remotes_are_captured_with_credentials_stripped(self) -> None:
        _git(
            self.repo,
            "remote",
            "add",
            "origin",
            "https://tok1@github.com/acme/demo.git",
        )
        _git(
            self.repo,
            "remote",
            "add",
            "upstream",
            "https://tok2@github.com/upstream/demo.git",
        )

        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")

        names = {name for name, _ in handle.identity.remotes}
        self.assertEqual(names, {"origin", "upstream"})
        self.assertNotIn("tok1", repr(handle))
        self.assertNotIn("tok2", repr(handle))

    def test_a_remote_added_after_capture_is_detected_as_remote_changed(self) -> None:
        _git(self.repo, "remote", "add", "origin", "https://github.com/acme/demo.git")
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")
        _git(
            self.repo,
            "remote",
            "add",
            "upstream",
            "https://github.com/upstream/demo.git",
        )

        result = revalidate_repository_handle(handle)

        self.assertFalse(result.fresh)
        self.assertIn("remote_changed", result.reasons)


class MergeConflictTests(unittest.TestCase):
    """A real (not hand-crafted) git merge conflict is captured and blocks (#536)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "repo"
        root.mkdir()
        self.repo = make_repo(root, files={"app.py": "base\n"}, commit=True)
        _git(self.repo, "checkout", "-b", "feature")
        (self.repo / "app.py").write_text("feature\n", encoding="utf-8")
        _git(self.repo, "commit", "-am", "feature change")
        _git(self.repo, "checkout", "-")
        (self.repo / "app.py").write_text("main\n", encoding="utf-8")
        _git(self.repo, "commit", "-am", "main change")
        subprocess.run(  # nosec B603 B607 - fixed argv, throwaway test repo
            ["git", "merge", "feature", "-m", "merge"],
            cwd=self.repo,
            capture_output=True,
            check=False,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_real_merge_conflict_is_captured_as_conflicted(self) -> None:
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")

        self.assertIn("app.py", handle.dirty_state.conflicted)

    def test_a_real_merge_conflict_blocks_as_unsafe(self) -> None:
        handle = capture_repository_handle(self.repo, task_id="t", run_id="r")

        assessment = classify_dirty_state(handle.dirty_state, planned_paths=("app.py",))

        self.assertEqual(assessment.classification, "unsafe")
        self.assertEqual(assessment.outcome, "block")
        self.assertEqual(assessment.rule_id, "git_conflict")


class SymlinkedWorktreeTests(unittest.TestCase):
    """A symlinked path to a repository resolves to the real identity (#536)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        real = base / "real-repo"
        real.mkdir()
        self.real = make_repo(real, files={"app.py": "1\n"}, commit=True)
        self.link = base / "link-to-repo"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_symlinked_path_to_the_repository_resolves_to_the_same_identity(
        self,
    ) -> None:
        try:
            self.link.symlink_to(self.real, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")

        via_link = capture_repository_handle(self.link, task_id="t", run_id="r")
        via_real = capture_repository_handle(self.real, task_id="t", run_id="r")

        self.assertEqual(via_link.identity.worktree_root, self.real.resolve())
        self.assertEqual(
            via_link.identity.repository_id, via_real.identity.repository_id
        )


class CaseInsensitiveIdentityTests(unittest.TestCase):
    """Case-variant paths to the same repository never fork identity (#536)."""

    @unittest.skipUnless(
        sys.platform == "win32", "os.path.normcase is a no-op on POSIX"
    )
    def test_repository_id_collapses_case_variant_paths(self) -> None:
        from opaihub.repository_safety import _repository_id

        common = {
            "git_dir": Path("C:/Repo/.git"),
            "common_git_dir": Path("C:/Repo/.git"),
            "filesystem_id": (1, 2),
            "remotes": (),
        }
        lower = _repository_id(root=Path("C:/somewhere/repo"), **common)
        upper = _repository_id(root=Path("C:/SOMEWHERE/REPO"), **common)

        self.assertEqual(lower, upper)

    @unittest.skipUnless(
        sys.platform != "win32", "case sensitivity is intentional here"
    )
    def test_repository_id_does_not_collapse_case_on_a_case_sensitive_platform(
        self,
    ) -> None:
        from opaihub.repository_safety import _repository_id

        common = {
            "git_dir": Path("/repo/.git"),
            "common_git_dir": Path("/repo/.git"),
            "filesystem_id": (1, 2),
            "remotes": (),
        }
        lower = _repository_id(root=Path("/somewhere/repo"), **common)
        upper = _repository_id(root=Path("/somewhere/REPO"), **common)

        self.assertNotEqual(lower, upper)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
