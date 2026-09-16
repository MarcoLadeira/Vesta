from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from _helpers import make_repo


def git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


class ObjectiveExecutionTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(
            importlib.util.find_spec("vestahub.objective_execution"),
            "A bounded objective execution service must exist",
        )
        from vestahub import objective_execution

        return objective_execution

    def test_planner_cannot_set_authority_or_execution_evidence(self):
        execution = self.module()
        plan = execution.parse_plan(
            json.dumps(
                {
                    "assignments": [
                        {
                            "name": "parser",
                            "objective": "Fix parser",
                            "intended_paths": ["src/parser.py"],
                            "allow_cloud": True,
                            "mode": "full-auto",
                            "state": "completed",
                            "verification": {"status": "passed"},
                            "worktree": "C:/outside",
                        }
                    ]
                }
            )
        )
        self.assertEqual(plan[0]["name"], "parser")
        for name in ("allow_cloud", "mode", "state", "verification", "worktree"):
            self.assertNotIn(name, plan[0])

    def test_planner_rejects_oversized_ambiguous_or_non_object_output(self):
        execution = self.module()
        for body in (
            "x" * 100_001,
            "{} {}",
            "[]",
            '{"assignments": []}',
            '{"assignments": ["do anything"]}',
        ):
            with self.subTest(body=body[:30]), self.assertRaises(ValueError):
                execution.parse_plan(body)

    def test_worker_context_excludes_parent_chat_and_bounds_shared_context(self):
        execution = self.module()
        packet = execution.worker_prompt(
            {
                "objective": "Fix parser",
                "shared_context": "contract " * 9000,
                "history": [{"text": "private old conversation"}],
            },
            {
                "objective": "Repair tokenization",
                "intended_paths": ["src/parser.py"],
                "dependencies": [],
                "verification_targets": ["tests/test_parser.py"],
            },
        )
        self.assertLess(len(packet), 24000)
        self.assertNotIn("private old conversation", packet)
        self.assertIn("src/parser.py", packet)

    def test_git_change_evidence_includes_committed_dirty_deleted_and_untracked(self):
        execution = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_repo(
                root,
                files={"a.py": "old\n", "b.py": "old\n", "c.py": "old\n"},
                commit=True,
            )
            base = git(root, "rev-parse", "HEAD")
            (root / "a.py").write_text("committed\n")
            git(root, "add", "a.py")
            git(root, "commit", "-m", "worker")
            (root / "b.py").write_text("dirty\n")
            (root / "c.py").unlink()
            (root / "new.py").write_text("new\n")
            observed = execution.observe_changes(root, base)
            self.assertEqual(
                observed["changed_files"], ["a.py", "b.py", "c.py", "new.py"]
            )
            self.assertNotEqual(observed["head_sha"], base)

    def test_scope_escape_is_detected_from_git_even_when_worker_claims_success(self):
        execution = self.module()
        self.assertEqual(
            execution.scope_violations(["src/a.py", "secrets.txt"], ["src"]),
            ["secrets.txt"],
        )
        self.assertEqual(
            execution.scope_violations(["src/ab.py"], ["src/a.py"]), ["src/ab.py"]
        )

    def test_process_cancellation_waits_until_real_child_is_dead(self):
        execution = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stop = threading.Event()
            beat = root / "observed.beat"
            script = (
                "import pathlib,time; p=pathlib.Path("
                + repr(str(beat))
                + "); exec(\"while True:\\n with p.open('a') as f: f.write('.')\\n time.sleep(.02)\")"
            )

            def stop_after_liveness():
                for _ in range(100):
                    if beat.exists() and beat.stat().st_size:
                        stop.set()
                        return
                    time.sleep(0.05)
                stop.set()

            timer = threading.Thread(target=stop_after_liveness)
            timer.start()
            try:
                result = execution.run_worker_process(
                    {
                        "authority_root": str(root),
                        "worktree": str(root),
                        "run_id": "cancel-run",
                        "operation_key": "cancel-op",
                    },
                    root / "worker",
                    stop,
                    argv=[sys.executable, "-c", script],
                )
            finally:
                timer.join()
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(result["cancellation"]["phase"], "terminated")
            size = beat.stat().st_size
            self.assertGreater(size, 0)
            time.sleep(0.2)
            self.assertEqual(beat.stat().st_size, size)

    def test_executor_integrates_actual_changes_without_touching_checkout(self):
        execution = self.module()
        self.assertTrue(hasattr(execution, "ObjectiveExecutor"))
        from vestahub.agent_objectives import ObjectiveStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            policy = {
                "schema_version": 1,
                "checks": [
                    {
                        "id": "static_analysis",
                        "kind": "static_analysis",
                        "requirement": "required",
                        "reason": "Verify the integrated documentation",
                        "command": [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; assert Path('a.md').read_text() == 'new a'; assert Path('b.md').read_text() == 'new b'",
                        ],
                    }
                ],
            }
            make_repo(
                root,
                files={
                    "a.md": "old a",
                    "b.md": "old b",
                    ".gitignore": ".vestahub/\n",
                    "vesta-verification-policy.yaml": json.dumps(policy),
                },
                commit=True,
            )
            store = ObjectiveStore(root)
            obj = store.create(
                "Update README documentation",
                assignments=[
                    {"name": "a", "objective": "Update a", "intended_paths": ["a.md"]},
                    {"name": "b", "objective": "Update b", "intended_paths": ["b.md"]},
                ],
                max_parallel=2,
            )

            def worker(packet, cancel, activity):
                name = packet["assignment"]["name"]
                (Path(packet["worktree"]) / f"{name}.md").write_text(f"new {name}")
                return {
                    "status": "completed",
                    "cost_usd": 0,
                    "measurement_kind": "actual",
                }

            executor = execution.ObjectiveExecutor(
                root, store=store, worker=worker, worktree_root=root.parent / "workers"
            )
            result = executor.run(obj["objective_id"])
            self.assertEqual(result["status"], "completed", result)
            integrated = Path(result["integration"]["worktree"])
            self.assertEqual((integrated / "a.md").read_text(), "new a")
            self.assertEqual((integrated / "b.md").read_text(), "new b")
            self.assertEqual((root / "a.md").read_text(), "old a")
            self.assertEqual(git(root, "status", "--porcelain"), "")

    def test_executor_scope_failure_preserves_independent_sibling(self):
        execution = self.module()
        self.assertTrue(hasattr(execution, "ObjectiveExecutor"))
        from vestahub.agent_objectives import ObjectiveStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            make_repo(
                root,
                files={"a.md": "a", "b.md": "b", ".gitignore": ".vestahub/\n"},
                commit=True,
            )
            store = ObjectiveStore(root)
            obj = store.create(
                "Update documentation",
                assignments=[
                    {"name": "a", "objective": "Update a", "intended_paths": ["a.md"]},
                    {"name": "b", "objective": "Update b", "intended_paths": ["b.md"]},
                ],
                max_parallel=2,
            )

            def worker(packet, cancel, activity):
                name = packet["assignment"]["name"]
                target = "outside.md" if name == "a" else "b.md"
                (Path(packet["worktree"]) / target).write_text("changed")
                return {"status": "completed"}

            result = execution.ObjectiveExecutor(
                root, store=store, worker=worker, worktree_root=root.parent / "workers"
            ).run(obj["objective_id"])
            rows = {row["name"]: row for row in result["assignments"]}
            self.assertEqual(rows["a"]["status"], "needs-attention")
            self.assertEqual(rows["b"]["status"], "completed")
            self.assertNotEqual(result["status"], "completed")


if __name__ == "__main__":
    unittest.main()
