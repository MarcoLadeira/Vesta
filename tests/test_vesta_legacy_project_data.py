"""Project data written before the rename to Vesta keeps working in place.

A project's ``.opaihub`` becomes ``.vestahub`` the first time Vesta resolves
its state. These tests start from a project exactly as the pre-rename version
left it and prove the move keeps the state private to git, drops the old
status page, and is retried when it cannot happen yet.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vesta import legacy
from vestahub import (
    benchmark,
    build_loop,
    eval_harness,
    project_instructions,
    state,
    vestabench,
)

OLD_EXCLUDE = (
    "# git ls-files --others --exclude-from=.git/info/exclude\n"
    "# OPai local status and proof files\n"
    "OPAI_STATUS.md\n"
    ".opaihub/\n"
    ".opaihub/opai-status.json\n"
    ".opaihub/dashboard.html\n"
    ".opaihub/benchmarks/\n"
)
OLD_STATUS_PAGE = (
    "# OPai Status\n\nOPai is active for this project.\n\n## Commands\n\n"
    "- `opai cockpit` - obvious ON/OFF control panel\n"
)


def _old_project(root: Path) -> None:
    (root / ".opaihub").mkdir()
    (root / ".opaihub" / "project.json").write_text("{}\n", encoding="utf-8")


class GitExcludeCarryOverTests(unittest.TestCase):
    def test_the_renamed_state_directory_stays_out_of_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _old_project(root)
            exclude = root / ".git" / "info" / "exclude"
            exclude.parent.mkdir(parents=True)
            exclude.write_text(OLD_EXCLUDE, encoding="utf-8")

            path = state.state_dir(root)
            lines = exclude.read_text(encoding="utf-8").splitlines()

        self.assertEqual(path.name, ".vestahub")
        for line in (
            ".vestahub/",
            ".vestahub/vesta-status.json",
            ".vestahub/dashboard.html",
            ".vestahub/benchmarks/",
            "VESTA_STATUS.md",
        ):
            self.assertEqual(lines.count(line), 1, line)
        self.assertIn(".opaihub/", lines)

    def test_a_worktree_updates_the_exclude_file_git_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            common = base / "main" / ".git"
            worktree_git = common / "worktrees" / "feature"
            worktree_git.mkdir(parents=True)
            (worktree_git / "commondir").write_text("../..\n", encoding="utf-8")
            exclude = common / "info" / "exclude"
            exclude.parent.mkdir(parents=True)
            exclude.write_text(".opaihub/\n", encoding="utf-8")
            root = base / "feature"
            root.mkdir()
            (root / ".git").write_text(f"gitdir: {worktree_git}\n", encoding="utf-8")
            _old_project(root)

            state.state_dir(root)
            lines = exclude.read_text(encoding="utf-8").splitlines()

        self.assertIn(".vestahub/", lines)

    def test_a_repository_that_never_excluded_the_state_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _old_project(root)
            exclude = root / ".git" / "info" / "exclude"
            exclude.parent.mkdir(parents=True)
            exclude.write_text("*.log\n", encoding="utf-8")

            state.state_dir(root)

            self.assertEqual(exclude.read_text(encoding="utf-8"), "*.log\n")


class LegacyStatusPageTests(unittest.TestCase):
    def test_the_generated_status_page_listing_old_commands_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _old_project(root)
            (root / "OPAI_STATUS.md").write_text(OLD_STATUS_PAGE, encoding="utf-8")

            state.state_dir(root)

            self.assertFalse((root / "OPAI_STATUS.md").exists())

    def test_a_file_the_user_wrote_under_that_name_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _old_project(root)
            (root / "OPAI_STATUS.md").write_text("# My notes\n", encoding="utf-8")

            state.state_dir(root)

            self.assertTrue((root / "OPAI_STATUS.md").exists())


class FailedStateMoveTests(unittest.TestCase):
    def test_state_stays_in_the_old_directory_until_the_move_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _old_project(root)

            with mock.patch.object(
                legacy.os, "replace", side_effect=PermissionError("in use")
            ):
                during = state.state_dir(root)
                during.joinpath("ledger.jsonl").write_text("{}\n", encoding="utf-8")
            self.assertFalse(os.path.lexists(root / ".vestahub"))

            after = state.state_dir(root)
            carried = (after / "ledger.jsonl").exists()

        self.assertEqual(during, root / ".opaihub")
        self.assertEqual(after, root / ".vestahub")
        self.assertTrue(carried)


class LegacyBuildAppTests(unittest.TestCase):
    def test_an_app_created_before_the_rename_is_still_an_app_with_its_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / ".opai-app.json").write_text(
                json.dumps({"files": ["index.html"], "template": "static"}),
                encoding="utf-8",
            )
            (app / ".opai-build-log.jsonl").write_text(
                json.dumps({"schema": 2, "status": "applied"}) + "\n",
                encoding="utf-8",
            )

            manifest = build_loop.load_app_manifest(app)
            history = build_loop.read_build_log(app)
            renamed = (app / ".vesta-app.json").is_file()
            log_renamed = (app / ".vesta-build-log.jsonl").is_file()

        self.assertEqual(manifest["files"], ["index.html"])
        self.assertEqual(len(history), 1)
        self.assertTrue(renamed)
        self.assertTrue(log_renamed)

    def test_old_backups_are_never_treated_as_app_files(self):
        self.assertIn(".opai-backups", build_loop._PRIVATE_PATH_PARTS)


class LegacyBenchmarkHistoryTests(unittest.TestCase):
    def test_runs_recorded_before_the_rename_stay_the_regression_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / ".vestahub" / "benchmarks" / "opaibench"
            old.mkdir(parents=True)
            (old / "runs.jsonl").write_text(
                json.dumps({"run_id": "before-rename"}) + "\n", encoding="utf-8"
            )

            runs = vestabench.read_vestabench_history(root)
            moved = (root / ".vestahub" / "benchmarks" / "vestabench").is_dir()

        self.assertEqual([run.get("run_id") for run in runs], ["before-rename"])
        self.assertTrue(moved)

    def test_an_old_effectiveness_score_still_detects_a_regression(self):
        previous = {"efficiency_score": {"opai_effectiveness_index": 80.0}}
        current = {"efficiency_score": {"vesta_effectiveness_index": 60.0}}

        comparison = benchmark.compare_benchmark_reports(previous, current)

        self.assertFalse(comparison["ok"])


class LegacyProjectRulesTests(unittest.TestCase):
    def test_rules_committed_under_the_old_directory_still_reach_the_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".opai").mkdir()
            (root / ".opai" / "rules.md").write_text(
                "Always run the linter.\n", encoding="utf-8"
            )

            text = project_instructions.load_project_instructions(root)

        self.assertIn("Always run the linter.", text)


class LegacyModelEvalScorecardTests(unittest.TestCase):
    def test_a_scorecard_published_before_the_rename_is_still_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = eval_harness.eval_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "report": "opai-model-eval",
                        "evaluation_id": "eval-1",
                        "publication_sequence": 7,
                        "results": [],
                    }
                ),
                encoding="utf-8",
            )

            result = eval_harness.read_scorecard(root)

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["scorecard"]["publication_sequence"], 7)


if __name__ == "__main__":
    unittest.main()
