"""Real-pipeline, hermetic coding-task parity benchmark (#314)."""

from __future__ import annotations

import contextlib
import glob
import io
import json
import os
import re
import shutil
import subprocess  # nosec B404 - test-only fixed Git argv, never a shell
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opaihub.opaibench import (
    PARITY_SUITE_VERSION,
    _file_sha,
    load_parity_baseline,
    parity_fixture_manifest,
    render_parity_html,
    render_parity_markdown,
    run_parity_benchmark,
)


class ParityRunnerTests(unittest.TestCase):
    def test_wheel_package_data_layout_contains_every_runnable_fixture(self):
        project_root = Path(__file__).resolve().parents[1]
        pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
        package_data_section = pyproject.split("[tool.setuptools.package-data]", 1)[
            1
        ].split("\n[", 1)[0]
        assignment = re.search(
            r"^opaihub\s*=\s*\[(.*?)\]",
            package_data_section,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(assignment)
        patterns = re.findall(r'"([^"]+)"', assignment.group(1))
        package_root = project_root / "opaihub"
        packaged_files = {
            candidate.resolve()
            for pattern in patterns
            for value in glob.glob(str(package_root / pattern), recursive=True)
            if (candidate := Path(value)).is_file()
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel_package = root / "site-packages" / "opaihub"
            for source in packaged_files:
                destination = wheel_package / source.relative_to(package_root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

            parity_root = wheel_package / "data" / "hub" / "benchmarks" / "parity"
            manifest = json.loads(
                (parity_root / "manifest.json").read_text(encoding="utf-8")
            )
            fixture_manifests = [
                parity_root / str(task["fixture"]) / ".opai-app.json"
                for task in manifest["tasks"]
            ]
            self.assertTrue(
                all(path.is_file() for path in fixture_manifests),
                [str(path) for path in fixture_manifests if not path.is_file()],
            )
            with mock.patch("opaihub.opaibench._PARITY_DATA", parity_root):
                report = run_parity_benchmark(root / "evidence", write=False)

        self.assertEqual(
            report["totals"], {"passed": 4, "total": 4, "score": 1.0, "steps": 12}
        )

    def test_four_representative_tasks_run_through_real_pipeline_and_pass(self):
        manifest_path = parity_fixture_manifest()
        before = manifest_path.parent
        before_snapshot = {
            path.relative_to(before).as_posix(): path.read_bytes()
            for path in before.rglob("*")
            if path.is_file()
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = run_parity_benchmark(Path(tmp), write=False)

        self.assertEqual(report["kind"], "opaibench_parity")
        self.assertEqual(report["harness"], "hermetic-contract")
        self.assertEqual(
            {item["category"] for item in report["tasks"]},
            {"bugfix", "feature", "refactor", "test-add"},
        )
        self.assertEqual(len(report["tasks"]), 4)
        self.assertTrue(all(item["passed"] for item in report["tasks"]), report)
        self.assertTrue(
            all(item["pipeline"]["real_pipeline"] for item in report["tasks"])
        )
        self.assertTrue(all(item["tests"]["passed"] for item in report["tasks"]))
        self.assertTrue(all(item["edits"]["correct"] for item in report["tasks"]))
        self.assertEqual(report["totals"]["passed"], 4)
        self.assertEqual(report["cost"]["cost_usd"], 0.0)
        self.assertEqual(report["cost"]["measurement"], "actual")
        self.assertTrue(
            all(item["cost"]["cost_usd"] == 0.0 for item in report["tasks"])
        )
        self.assertTrue(
            all(
                item["cost"]["routing_estimate_usd"] is not None
                for item in report["tasks"]
            )
        )
        self.assertEqual(report["paid_comparison"], "offline_only")
        after_snapshot = {
            path.relative_to(before).as_posix(): path.read_bytes()
            for path in before.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after_snapshot, before_snapshot)

    def test_solution_evidence_normalizes_host_newlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lf = root / "lf.py"
            crlf = root / "crlf.py"
            lf.write_bytes(b"value = 1\n")
            crlf.write_bytes(b"value = 1\r\n")

            self.assertEqual(_file_sha(lf), _file_sha(crlf))

    def test_answer_without_edits_fails_from_disk_evidence(self):
        class NoEditRunner:
            def __init__(self, _answer: str) -> None:
                self.calls = []

            def stream(self, prompt: str, **kwargs):
                self.calls.append(prompt)
                return {"text": "I would change it.", "cost": 0.0}

            complete = stream

        with tempfile.TemporaryDirectory() as tmp:
            report = run_parity_benchmark(
                Path(tmp),
                write=False,
                task_ids=("bugfix",),
                runner_factory=NoEditRunner,
            )

        task = report["tasks"][0]
        self.assertFalse(task["passed"])
        self.assertFalse(task["completion"])
        self.assertFalse(task["edits"]["correct"])
        self.assertEqual(task["pipeline"]["status"], "no_edits")

    def test_default_processes_are_local_python_and_git_only(self):
        calls: list[tuple[str, ...]] = []

        def observe(argv, **kwargs):
            import subprocess

            calls.append(tuple(str(item) for item in argv))
            return subprocess.run(argv, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            report = run_parity_benchmark(
                Path(tmp),
                write=False,
                task_ids=("feature",),
                process_runner=observe,
            )

        self.assertTrue(report["tasks"][0]["passed"])
        executables = {Path(call[0]).name.lower() for call in calls}
        python_calls = [
            call for call in calls if Path(call[0]).name.lower().startswith("python")
        ]
        self.assertTrue(any(name.startswith("python") for name in executables))
        self.assertIn("git", executables)
        self.assertFalse(executables.intersection({"claude", "codex", "npx", "npm"}))
        self.assertTrue(
            all(
                call[1:6] == ("-I", "-S", "-B", "-m", "unittest")
                for call in python_calls
            )
        )

    def test_inherited_git_and_python_overrides_are_sanitized(self):
        calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

        def observe(argv, **kwargs):
            import subprocess

            calls.append(
                (
                    tuple(str(item) for item in argv),
                    dict(kwargs.get("env") or {}),
                )
            )
            return subprocess.run(argv, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            poisoned_git_dir = str(Path(tmp) / "outside.git")
            with mock.patch.dict(
                os.environ,
                {"GIT_DIR": poisoned_git_dir, "PYTHONPATH": "untrusted-path"},
            ):
                report = run_parity_benchmark(
                    Path(tmp),
                    write=False,
                    task_ids=("feature",),
                    process_runner=observe,
                )

        self.assertTrue(report["tasks"][0]["passed"])
        git_envs = [env for argv, env in calls if Path(argv[0]).name.lower() == "git"]
        python_envs = [
            env
            for argv, env in calls
            if Path(argv[0]).name.lower().startswith("python")
        ]
        self.assertTrue(git_envs)
        self.assertTrue(python_envs)
        self.assertTrue(all("GIT_DIR" not in env for env in git_envs))
        self.assertTrue(all(env.get("GIT_CONFIG_NOSYSTEM") == "1" for env in git_envs))
        self.assertTrue(all("PYTHONPATH" not in env for env in python_envs))

    def test_inherited_git_dir_cannot_steer_the_in_process_pipeline(self):
        observations: list[dict[str, object]] = []

        class InspectingRunner:
            def __init__(self, answer: str) -> None:
                self.answer = answer
                self.model = "hermetic-scripted"
                self.actual_cost_usd = 0.0
                self.calls: list[dict[str, str]] = []

            @staticmethod
            def available() -> bool:
                return True

            def complete(self, prompt: str, **_kwargs):
                from opaihub.checkpoints import load_run_checkpoint
                from opaihub.repo_context import load_active_repo
                from opaihub.workflow_state import load_workflow_state

                self.calls.append({"prompt": prompt})
                prefix = "- Active repository: "
                root = Path(
                    next(
                        line for line in prompt.splitlines() if line.startswith(prefix)
                    )[len(prefix) :]
                )
                workflow = load_workflow_state(root)
                checkpoint = load_run_checkpoint(root, workflow.checkpoint_id)
                repo_context = load_active_repo(root)
                clean_env = dict(os.environ)
                for name in tuple(clean_env):
                    if name.startswith("GIT_"):
                        clean_env.pop(name, None)
                fixture_head = subprocess.run(  # nosec B603 B607 - fixed test argv
                    ["git", "rev-parse", "HEAD"],
                    cwd=root,
                    env=clean_env,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                observations.append(
                    {
                        "git_dir": os.environ.get("GIT_DIR"),
                        "fixture_head": fixture_head,
                        "workflow_checkpoint": workflow.checkpoint_id,
                        "checkpoint_id": checkpoint.checkpoint_id,
                        "checkpoint_head": str(checkpoint.git.get("head") or ""),
                        "checkpoint_baseline": checkpoint.baseline_changed_files,
                        "repo_dirty": (
                            repo_context.dirty_paths if repo_context is not None else ()
                        ),
                    }
                )
                return {"text": self.answer, "cost": 0.0}

            def stream(self, prompt: str, **kwargs):
                result = self.complete(prompt, **kwargs)
                if on_text := kwargs.get("on_text"):
                    on_text(self.answer)
                return result

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            (temp_root / "external").mkdir()
            external = make_repo(
                temp_root / "external",
                files={"PRIVATE_MARKER.py": "PRIVATE_MARKER = True\n"},
                commit=True,
            )
            external_head = subprocess.run(  # nosec B603 B607 - fixed test argv
                ["git", "rev-parse", "HEAD"],
                cwd=external,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            with mock.patch.dict(os.environ, {"GIT_DIR": str(external / ".git")}):
                report = run_parity_benchmark(
                    temp_root / "evidence",
                    write=False,
                    task_ids=("feature",),
                    runner_factory=InspectingRunner,
                )

        self.assertTrue(report["tasks"][0]["passed"], report)
        self.assertEqual(len(observations), 1)
        evidence = observations[0]
        self.assertIsNone(evidence["git_dir"])
        self.assertNotEqual(evidence["fixture_head"], external_head)
        self.assertEqual(evidence["workflow_checkpoint"], evidence["checkpoint_id"])
        self.assertEqual(evidence["checkpoint_head"], evidence["fixture_head"])
        self.assertNotIn("PRIVATE_MARKER.py", evidence["checkpoint_baseline"])
        self.assertNotIn("PRIVATE_MARKER.py", evidence["repo_dirty"])


class BaselineAndRenderingTests(unittest.TestCase):
    def test_baseline_system_rejects_table_forgery_and_renderers_escape_labels(self):
        unsafe_system = "Trusted | Baseline\n| forged <script>"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unsafe-system.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "suite_version": PARITY_SUITE_VERSION,
                        "system": unsafe_system,
                        "measurement": "offline_import",
                        "tasks": {},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "system"):
                load_parity_baseline(path)

        report = {
            "harness": "hermetic-contract",
            "tasks": [],
            "totals": {"passed": 0, "total": 0},
            "baseline": {"system": unsafe_system, "tasks": {}},
        }
        markdown = render_parity_markdown(report)
        html = render_parity_html(report)
        self.assertNotIn("\n| forged", markdown)
        self.assertIn(
            r"Trusted \| Baseline \| forged &lt;script&gt; completion", markdown
        )
        self.assertNotIn("<script>", html)
        self.assertIn("Trusted | Baseline | forged &lt;script&gt;", html)

    def test_missing_baseline_renders_na_and_offline_baseline_is_versioned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run_parity_benchmark(root, write=False, task_ids=("bugfix",))
            markdown = render_parity_markdown(report)
            self.assertIn("N/A", markdown)

            path = root / "claude.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "suite_version": PARITY_SUITE_VERSION,
                        "system": "Claude Code",
                        "measurement": "offline_manual",
                        "tasks": {
                            "bugfix": {
                                "completion": True,
                                "edits_correct": True,
                                "tests_passed": True,
                                "steps": 4,
                                "cost_usd": 0.12,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            baseline = load_parity_baseline(path)
            compared = run_parity_benchmark(
                root, write=False, task_ids=("bugfix",), baseline_path=path
            )

        self.assertEqual(baseline["system"], "Claude Code")
        self.assertEqual(compared["baseline"]["measurement"], "offline_manual")
        self.assertIn("$0.1200", render_parity_markdown(compared))
        self.assertIn("Claude Code", render_parity_html(compared))

    def test_baseline_suite_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "suite_version": "old-suite",
                        "system": "Claude Code",
                        "tasks": {},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "suite version"):
                load_parity_baseline(path)

    def test_baseline_rejects_non_finite_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad-cost.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "suite_version": PARITY_SUITE_VERSION,
                        "system": "Offline baseline",
                        "measurement": "offline_import",
                        "tasks": {"bugfix": {"cost_usd": float("nan")}},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "finite"):
                load_parity_baseline(path)

    def test_written_artifacts_include_json_markdown_and_html_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_parity_benchmark(Path(tmp), write=True, task_ids=("test-add",))
            artifacts = report["artifacts"]
            contents = {
                name: Path(path).read_text(encoding="utf-8")
                for name, path in artifacts.items()
            }

        self.assertIn("Vesta vs baseline", contents["markdown"])
        self.assertIn("<table", contents["html"])
        self.assertEqual(json.loads(contents["json"])["kind"], "opaibench_parity")


class ParityCliTests(unittest.TestCase):
    def test_hub_cli_parity_exit_code_and_json_report(self):
        from opaihub.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "--project",
                        tmp,
                        "opaibench",
                        "parity",
                        "--task",
                        "refactor",
                        "--no-write",
                    ]
                )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["totals"]["passed"], 1)

    def test_primary_opai_cli_exposes_parity_with_same_result(self):
        # The daily-driver proof (#309/#314) must be reachable from the primary
        # `opai` CLI, not only the secondary op-hub tool. Same fixtures, same
        # report shape, same exit-code contract — parity by delegation.
        from opai.cli import main as opai_main

        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = opai_main(
                    [
                        "benchmark",
                        "parity",
                        "--project",
                        tmp,
                        "--task",
                        "refactor",
                        "--format",
                        "json",
                        "--no-write",
                    ]
                )
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["kind"], "opaibench_parity")
        self.assertEqual(report["totals"]["passed"], 1)

    def test_primary_opai_cli_parity_markdown_is_default(self):
        from opai.cli import main as opai_main

        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = opai_main(
                    [
                        "benchmark",
                        "parity",
                        "--project",
                        tmp,
                        "--task",
                        "bugfix",
                        "--no-write",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertIn("OPaiBench parity", output.getvalue())


if __name__ == "__main__":
    unittest.main()
