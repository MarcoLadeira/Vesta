"""Durable Vesta worktree lease tests (#537)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from _helpers import make_repo

from vestahub.repository_safety import capture_repository_handle
from vestahub.worktree_leases import WorktreeLeaseError, WorktreeManager


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


class WorktreeLeaseLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        make_repo(self.repo, files={"src/app.py": "print('ok')\n"}, commit=True)
        self.handle = capture_repository_handle(
            self.repo, task_id="task-a", run_id="run-a"
        )
        self.manager = WorktreeManager(self.repo, min_free_bytes=0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _create(self, *, branch: str = "codex/task-a", target: Path | None = None):
        return self.manager.create(
            self.handle,
            task_id="task-a",
            run_id="run-a",
            owner="worker-a",
            branch=branch,
            target=target or self.base / "task-a",
            base="HEAD",
            planned_paths=("src/",),
        )

    def test_creation_records_resolved_base_then_reconciles_active_lease(self) -> None:
        lease = self._create()

        self.assertEqual(lease.state, "active")
        self.assertEqual(lease.base_sha, _git(self.repo, "rev-parse", "HEAD"))
        self.assertTrue(Path(lease.path).is_dir())
        self.assertEqual(lease.branch, "codex/task-a")
        self.assertEqual(lease.evidence["registry_state"], "registered")

    def test_cleanup_preserves_user_modified_worktree(self) -> None:
        lease = self._create()
        (Path(lease.path) / "user-note.txt").write_text("keep me\n", encoding="utf-8")

        result = self.manager.cleanup(lease.lease_id, owner="worker-a")

        self.assertEqual(result.state, "needs_review")
        self.assertTrue(Path(lease.path).exists())
        self.assertIn("dirty_worktree", result.evidence["reasons"])

    def test_branch_or_target_claim_collision_fails_without_second_worktree(
        self,
    ) -> None:
        self._create()

        with self.assertRaises(WorktreeLeaseError):
            self._create(target=self.base / "task-b")

        self.assertEqual(len(self.manager.list()), 1)

    def test_cleanup_releases_only_pristine_owned_worktree(self) -> None:
        lease = self._create()

        result = self.manager.cleanup(lease.lease_id, owner="worker-a")

        self.assertEqual(result.state, "released")
        self.assertFalse(Path(lease.path).exists())
        self.assertIn("removed", result.evidence["cleanup"])

    def test_non_owner_cannot_clean_up_a_lease(self) -> None:
        lease = self._create()

        with self.assertRaises(WorktreeLeaseError):
            self.manager.cleanup(lease.lease_id, owner="worker-b")

        self.assertTrue(Path(lease.path).exists())

    def test_interrupted_add_keeps_recoverable_cleanup_failed_lease(self) -> None:
        def fail_add(argv, **kwargs):
            if argv[1:3] == ["worktree", "add"]:
                return subprocess.CompletedProcess(
                    argv, 1, "", "simulated disk failure"
                )
            return subprocess.run(argv, **kwargs)

        manager = WorktreeManager(self.repo, git_run=fail_add, min_free_bytes=0)
        with self.assertRaises(WorktreeLeaseError):
            manager.create(
                self.handle,
                task_id="task-a",
                run_id="run-a",
                owner="worker-a",
                branch="codex/interrupted",
                target=self.base / "interrupted",
                base="HEAD",
                planned_paths=("src/",),
            )

        leases = manager.list()
        self.assertEqual(len(leases), 1)
        self.assertEqual(leases[0].state, "cleanup_failed")
        self.assertEqual(leases[0].evidence["reasons"], ["worktree_add_failed"])

    def test_low_disk_space_blocks_before_creating_a_lease(self) -> None:
        manager = WorktreeManager(
            self.repo,
            min_free_bytes=100,
            disk_usage=lambda _: SimpleNamespace(free=0),
        )

        with self.assertRaises(WorktreeLeaseError):
            manager.create(
                self.handle,
                task_id="task-a",
                run_id="run-a",
                owner="worker-a",
                branch="codex/no-space",
                target=self.base / "no-space",
                base="HEAD",
                planned_paths=("src/",),
            )

        self.assertEqual(manager.list(), [])

    def test_missing_registry_entry_moves_lease_to_review_without_recreation(
        self,
    ) -> None:
        lease = self._create()
        _git(self.repo, "worktree", "remove", lease.path)

        recovered = self.manager.reconcile(lease.lease_id)

        self.assertEqual(recovered.state, "needs_review")
        self.assertIn("registry_missing", recovered.evidence["reasons"])
        self.assertFalse(Path(lease.path).exists())

    def test_concurrent_branch_claims_leave_exactly_one_active_lease(self) -> None:
        def claim(index: int) -> str:
            try:
                self.manager.create(
                    self.handle,
                    task_id=f"task-{index}",
                    run_id=f"run-{index}",
                    owner=f"worker-{index}",
                    branch="codex/shared-claim",
                    target=self.base / f"concurrent-{index}",
                    base="HEAD",
                    planned_paths=("src/",),
                )
                return "created"
            except WorktreeLeaseError:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, (1, 2)))

        self.assertEqual(results.count("created"), 1)
        self.assertEqual(results.count("blocked"), 1)
        self.assertEqual(len(self.manager.list()), 1)


class WorktreePreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        make_repo(self.repo, files={"src/shared.py": "base\n"}, commit=True)
        self.manager = WorktreeManager(self.repo, min_free_bytes=0)
        self.source_handle = capture_repository_handle(
            self.repo, task_id="task-a", run_id="run-a"
        )
        self.lease = self.manager.create(
            self.source_handle,
            task_id="task-a",
            run_id="run-a",
            owner="worker-a",
            branch="codex/task-a",
            target=self.base / "task-a",
            base="HEAD",
            planned_paths=("src/",),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_target_divergence_with_overlapping_paths_blocks_apply_preview(
        self,
    ) -> None:
        source = Path(self.lease.path)
        (source / "src" / "shared.py").write_text("source\n", encoding="utf-8")
        _git(source, "add", "src/shared.py")
        _git(source, "commit", "-m", "source change")
        (self.repo / "src" / "shared.py").write_text("target\n", encoding="utf-8")
        _git(self.repo, "add", "src/shared.py")
        _git(self.repo, "commit", "-m", "target change")
        target_handle = capture_repository_handle(
            self.repo, task_id="task-b", run_id="run-b"
        )

        preview = self.manager.preview_apply(self.lease.lease_id, target_handle)

        self.assertFalse(preview.allowed)
        self.assertEqual(preview.reason, "target_diverged_overlap")
        self.assertEqual(preview.paths, ("src/shared.py",))


class WorktreeRecoveryTests(unittest.TestCase):
    def test_orphan_recovery_never_deletes_unknown_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            make_repo(repo, files={"src/app.py": "base\n"}, commit=True)
            manager = WorktreeManager(repo, min_free_bytes=0)
            handle = capture_repository_handle(repo, task_id="task-a", run_id="run-a")
            lease = manager.create(
                handle,
                task_id="task-a",
                run_id="run-a",
                owner="worker-a",
                branch="codex/task-a",
                target=base / "task-a",
                base="HEAD",
                planned_paths=("src/",),
            )
            (Path(lease.path) / "user-note.txt").write_text(
                "preserve\n", encoding="utf-8"
            )

            recovered = manager.recover()

            self.assertEqual(recovered[0].lease.state, "needs_review")
            self.assertIn("inspect", recovered[0].recommended_actions)
            self.assertTrue(Path(lease.path).exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
