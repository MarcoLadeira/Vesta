"""Workspace summaries do not repeat unchanged Git subprocesses."""

from __future__ import annotations

import concurrent.futures
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from opai import app_state
from tests._helpers import make_repo


class WorkspaceSummaryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear = getattr(app_state, "clear_workspace_summary_cache", None)
        if clear is not None:
            clear()

    def tearDown(self) -> None:
        clear = getattr(app_state, "clear_workspace_summary_cache", None)
        if clear is not None:
            clear()

    def test_unchanged_repo_runs_one_git_inventory_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            with mock.patch.object(
                app_state,
                "_git_text",
                wraps=app_state._git_text,
            ) as git_text:
                first = app_state.workspace_summary(root)
                second = app_state.workspace_summary(root)
                third = app_state.workspace_summary(root)

            self.assertEqual(git_text.call_count, 1)
            self.assertEqual(first, second)
            self.assertEqual(second, third)

    def test_cached_summary_is_a_defensive_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            first = app_state.workspace_summary(root)
            first["file_count"] = 999

            second = app_state.workspace_summary(root)

            self.assertNotEqual(second["file_count"], 999)

    def test_concurrent_misses_share_one_git_inventory_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            callers = threading.Barrier(3)
            first_probe = threading.Event()
            release_probe = threading.Event()
            original = app_state._git_text

            def delayed_git_text(*args, **kwargs):
                first_probe.set()
                release_probe.wait(timeout=2)
                return original(*args, **kwargs)

            def summarize():
                callers.wait(timeout=2)
                return app_state.workspace_summary(root)

            with mock.patch.object(
                app_state, "_git_text", side_effect=delayed_git_text
            ) as git_text:
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [pool.submit(summarize) for _ in range(3)]
                    self.assertTrue(first_probe.wait(timeout=2))
                    time.sleep(0.1)
                    release_probe.set()
                    summaries = [future.result(timeout=3) for future in futures]

            self.assertEqual(git_text.call_count, 1)
            self.assertEqual(summaries, [summaries[0]] * 3)

    def test_staged_file_invalidates_the_cached_file_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            before = app_state.workspace_summary(root)
            (root / "new_file.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "new_file.py"],
                cwd=root,
                check=True,
                capture_output=True,
            )

            after = app_state.workspace_summary(root)

            self.assertEqual(after["file_count"], before["file_count"] + 1)

    def test_branch_switch_invalidates_the_cached_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            before = app_state.workspace_summary(root)
            subprocess.run(
                ["git", "switch", "-q", "-c", "perf-cache-test"],
                cwd=root,
                check=True,
                capture_output=True,
            )

            after = app_state.workspace_summary(root)

            self.assertNotEqual(before["branch"], after["branch"])
            self.assertEqual(after["branch"], "perf-cache-test")

    def test_linked_worktree_git_file_is_cacheable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            root = make_repo(repo, commit=True)
            linked = base / "linked"
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "linked-cache", str(linked)],
                cwd=root,
                check=True,
                capture_output=True,
            )
            with mock.patch.object(
                app_state,
                "_git_text",
                wraps=app_state._git_text,
            ) as git_text:
                first = app_state.workspace_summary(linked)
                second = app_state.workspace_summary(linked)

            self.assertTrue((linked / ".git").is_file())
            self.assertEqual(first, second)
            self.assertEqual(first["branch"], "linked-cache")
            self.assertEqual(git_text.call_count, 1)

    def test_detached_head_keeps_the_branch_badge_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), commit=True)
            subprocess.run(
                ["git", "checkout", "-q", "--detach", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
            )

            summary = app_state.workspace_summary(root)

            self.assertEqual(summary["branch"], "")

    def test_badge_refresh_uses_one_git_probe_and_sees_live_dirty_paths(self) -> None:
        from opai.gui_web import _workspace, _workspace_refresh
        from opaihub import repository_safety

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                Path(tmp), files={"src/app.py": "value = 1\n"}, commit=True
            )
            boot_workspace = _workspace(root)
            (root / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")

            with mock.patch.object(
                repository_safety,
                "_run_git",
                wraps=repository_safety._run_git,
            ) as run_git:
                refreshed = _workspace_refresh(root)

            self.assertEqual(run_git.call_count, 1)
            self.assertEqual(refreshed["root"], str(root.resolve()))
            self.assertEqual(refreshed["branch"], boot_workspace["branch"])
            self.assertIn("src/app.py", refreshed["dirty_paths"])


if __name__ == "__main__":
    unittest.main()
