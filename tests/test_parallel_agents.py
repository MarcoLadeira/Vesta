"""Multi-agent parallel issue solving (#177): isolation, ownership, reconciliation."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from opaihub.parallel_agents import (
    AgentAssignment,
    OwnershipError,
    SharedFileConflictError,
    claim_assignment,
    create_assignment_lease,
    create_assignment_worktree,
    detect_shared_file_overwrites,
    ensure_no_overwrite,
    list_assignments,
    load_assignment,
    plan_parallel_assignments,
    plan_reconciliation,
    release_assignment,
    save_assignment,
)
from opaihub.repo_context import DirtyConflictError
from _helpers import make_repo


def _issue(number, title, paths):
    return {"number": number, "title": title, "intended_paths": paths}


class PlanningTests(unittest.TestCase):
    def test_independent_issues_get_isolated_branches_worktrees_and_owners(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            plan = plan_parallel_assignments(
                repo,
                [
                    _issue(11, "Fix login bug", ["auth/login.py"]),
                    _issue(12, "Update docs", ["docs/guide.md"]),
                ],
                max_parallel=2,
            )
            self.assertEqual(len(plan.assignments), 2)
            self.assertEqual(plan.deferred, ())
            first, second = plan.assignments
            self.assertEqual(first.owner, "agent-1")
            self.assertEqual(second.owner, "agent-2")
            self.assertTrue(first.branch.startswith("codex/issue-11-"))
            self.assertTrue(second.branch.startswith("codex/issue-12-"))
            self.assertNotEqual(first.worktree, second.worktree)
            for assignment in plan.assignments:
                worktree = Path(assignment.worktree).resolve()
                self.assertNotIn(repo.resolve(), (worktree, *worktree.parents))

    def test_overlapping_intended_paths_defer_the_later_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            plan = plan_parallel_assignments(
                repo,
                [
                    _issue(11, "Refactor auth", ["auth/"]),
                    _issue(12, "Fix login bug", ["auth/login.py"]),
                ],
                max_parallel=4,
            )
            self.assertEqual(len(plan.assignments), 1)
            self.assertEqual(plan.assignments[0].issue_number, 11)
            self.assertEqual(plan.deferred[0]["number"], 12)
            self.assertIn("overlap", plan.deferred[0]["reason"])

    def test_unknown_scope_is_never_parallelized(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            plan = plan_parallel_assignments(
                repo,
                [
                    _issue(11, "Fix login bug", ["auth/login.py"]),
                    _issue(12, "Mystery scope", []),
                ],
                max_parallel=4,
            )
            self.assertEqual(len(plan.assignments), 1)
            self.assertEqual(plan.deferred[0]["number"], 12)

    def test_concurrency_limit_defers_extra_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            plan = plan_parallel_assignments(
                repo,
                [
                    _issue(1, "One", ["a.py"]),
                    _issue(2, "Two", ["b.py"]),
                    _issue(3, "Three", ["c.py"]),
                ],
                max_parallel=2,
            )
            self.assertEqual(len(plan.assignments), 2)
            self.assertIn("concurrency limit", plan.deferred[0]["reason"])


class OwnershipTests(unittest.TestCase):
    def _saved_assignment(self, root, number=11, status="planned", owner="agent-1"):
        assignment = AgentAssignment(
            assignment_id=f"assign-{number}",
            issue_number=number,
            title=f"Issue {number}",
            owner=owner,
            branch=f"codex/issue-{number}-x",
            worktree=str(root / f"wt-{number}"),
            intended_paths=("src/x.py",),
            status=status,
        )
        save_assignment(root, assignment)
        return assignment

    def test_claim_and_release_are_owner_scoped_and_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = self._saved_assignment(root)
            claimed = claim_assignment(root, saved.assignment_id, "agent-1")
            self.assertEqual(claimed.status, "active")

            with self.assertRaises(OwnershipError):
                claim_assignment(root, saved.assignment_id, "agent-2")
            with self.assertRaises(OwnershipError):
                release_assignment(root, saved.assignment_id, "agent-2")

            released = release_assignment(
                root,
                saved.assignment_id,
                "agent-1",
                changed_files=("src/x.py", "tests/test_x.py"),
            )
            self.assertEqual(released.status, "completed")
            self.assertEqual(
                load_assignment(root, saved.assignment_id).changed_files,
                ("src/x.py", "tests/test_x.py"),
            )
            with self.assertRaises(ValueError):
                claim_assignment(root, saved.assignment_id, "agent-1")

    def test_active_concurrency_limit_is_enforced_at_claim_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._saved_assignment(root, number=1)
            second = self._saved_assignment(root, number=2)
            claim_assignment(root, first.assignment_id, "agent-1", max_active=1)
            with self.assertRaises(RuntimeError):
                claim_assignment(root, second.assignment_id, "agent-2", max_active=1)

    def test_listing_filters_by_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._saved_assignment(root, number=1)
            self._saved_assignment(root, number=2)
            claim_assignment(root, first.assignment_id, "agent-1")
            self.assertEqual(len(list_assignments(root, status="active")), 1)
            self.assertEqual(len(list_assignments(root, status="planned")), 1)
            self.assertEqual(len(list_assignments(root)), 2)


class WorktreeTests(unittest.TestCase):
    def test_worktree_creation_uses_the_guarded_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            make_repo(repo, files={"auth/login.py": "original\n"}, commit=True)
            assignment = AgentAssignment(
                assignment_id="assign-1",
                issue_number=11,
                title="Fix login",
                owner="agent-1",
                branch="codex/issue-11-fix-login",
                worktree=str(Path(tmp) / "repo-agents" / "issue-11"),
                intended_paths=("auth/login.py",),
            )
            save_assignment(repo, assignment)

            command = create_assignment_worktree(repo, assignment, base="HEAD")
            self.assertEqual(command[:3], ["git", "worktree", "add"])
            self.assertIn("codex/issue-11-fix-login", command)
            persisted = load_assignment(repo, assignment.assignment_id)
            self.assertTrue(persisted.lease_id)
            self.assertEqual(persisted.lease_state, "active")

    def test_lease_creation_returns_manager_owned_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            make_repo(repo, files={"auth/login.py": "original\n"}, commit=True)
            assignment = AgentAssignment(
                assignment_id="assign-lease",
                issue_number=13,
                title="Fix login",
                owner="agent-1",
                branch="codex/issue-13-fix-login",
                worktree=str(Path(tmp) / "repo-agents" / "issue-13"),
                intended_paths=("auth/login.py",),
            )

            lease = create_assignment_lease(repo, assignment, base="HEAD")

            self.assertEqual(lease.state, "active")
            self.assertEqual(lease.owner, assignment.owner)
            self.assertTrue(Path(lease.path).is_dir())

    def test_dirty_overlap_blocks_worktree_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            assignment = AgentAssignment(
                assignment_id="assign-1",
                issue_number=11,
                title="Fix login",
                owner="agent-1",
                branch="codex/issue-11-fix-login",
                worktree=str(Path(tmp) / "repo-agents" / "issue-11"),
                intended_paths=("auth/login.py",),
            )
            with self.assertRaises(DirtyConflictError):
                create_assignment_worktree(
                    repo,
                    assignment,
                    dirty_paths=("auth/login.py",),
                    run=lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""),
                )


class ReconciliationTests(unittest.TestCase):
    def _completed(self, assignment_id, number, changed):
        return AgentAssignment(
            assignment_id=assignment_id,
            issue_number=number,
            title=f"Issue {number}",
            owner=f"agent-{number}",
            branch=f"codex/issue-{number}-x",
            worktree=f"wt-{number}",
            intended_paths=("src/",),
            status="completed",
            changed_files=tuple(changed),
        )

    def test_disjoint_changes_auto_reconcile_in_issue_order(self):
        plan = plan_reconciliation(
            [
                self._completed("b", 2, ["b.py"]),
                self._completed("a", 1, ["a.py"]),
            ]
        )
        self.assertEqual(plan["merge_order"], ["a", "b"])
        self.assertTrue(plan["can_auto_reconcile"])
        self.assertEqual(plan["conflicts"], {})

    def test_shared_files_force_sequential_review_not_overwrites(self):
        plan = plan_reconciliation(
            [
                self._completed("a", 1, ["shared.py", "a.py"]),
                self._completed("b", 2, ["shared.py"]),
                self._completed("c", 3, ["c.py"]),
            ]
        )
        self.assertEqual(plan["merge_order"], ["c"])
        self.assertEqual(plan["needs_sequential_review"], ["a", "b"])
        self.assertEqual(plan["conflicts"], {"shared.py": ["a", "b"]})
        self.assertFalse(plan["can_auto_reconcile"])

    def test_detect_shared_file_overwrites_normalizes_paths(self):
        conflicts = detect_shared_file_overwrites(
            [
                self._completed("a", 1, ["src\\shared.py"]),
                self._completed("b", 2, ["src/shared.py"]),
            ]
        )
        self.assertEqual(conflicts, {"src/shared.py": ("a", "b")})

    def test_ensure_no_overwrite_fails_closed(self):
        ensure_no_overwrite(["a.py"], ["b.py"])
        with self.assertRaises(SharedFileConflictError):
            ensure_no_overwrite(["a.py", "shared.py"], ["shared.py"])

    def test_incomplete_assignments_never_enter_the_merge_plan(self):
        active = AgentAssignment(
            assignment_id="active",
            issue_number=9,
            title="WIP",
            owner="agent-9",
            branch="codex/issue-9-wip",
            worktree="wt-9",
            intended_paths=("src/",),
            status="active",
            changed_files=("src/wip.py",),
        )
        plan = plan_reconciliation([active, self._completed("a", 1, ["a.py"])])
        self.assertEqual(plan["merge_order"], ["a"])


if __name__ == "__main__":
    unittest.main()
