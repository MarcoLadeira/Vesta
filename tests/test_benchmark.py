"""Tests for local OPai effectiveness benchmarking."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from opai.cli import main
from opaihub.audit import BENCHMARK_RUN, read_audit
from opaihub.benchmark import (
    benchmark_gate,
    benchmark_history_path,
    compare_benchmark_reports,
    export_promptfoo_config,
    list_benchmark_suites,
    read_benchmark_history,
    render_benchmark_comparison_markdown,
    render_benchmark_markdown,
    run_benchmark,
)


class BenchmarkTests(unittest.TestCase):
    def test_local_suite_lists_required_task_types(self) -> None:
        suites = list_benchmark_suites()

        self.assertIn("local", suites)
        task_ids = {task["id"] for task in suites["local"]["tasks"]}
        self.assertEqual(
            {
                "planning",
                "bug_triage",
                "test_failure",
                "security_review",
                "release_preflight",
                "docs",
                "dependency_update",
                "mobile_readiness",
            },
            task_ids,
        )
        self.assertEqual("promptfoo", suites["local"]["external_harnesses"][0]["id"])

    def test_local_run_writes_score_metadata_without_raw_prompts(self) -> None:
        secret_prompt = "Investigate API key sk-test-secret-do-not-store"
        tasks = [
            {
                "id": "secret_probe",
                "category": "security_review",
                "prompt": secret_prompt,
                "baseline_context_bytes": 12_000,
                "opai_context_bytes": 800,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run_benchmark(root, suite="local", mode="both", tasks=tasks)

            self.assertEqual("opai-benchmark", report["kind"])
            self.assertEqual("local", report["suite"])
            self.assertEqual("both", report["mode"])
            self.assertGreaterEqual(
                report["efficiency_score"]["context_reduction_ratio"], 10
            )
            self.assertGreaterEqual(
                report["efficiency_score"]["paid_call_avoidance_ratio"], 1
            )
            self.assertEqual(1.0, report["efficiency_score"]["success_rate"])
            self.assertGreaterEqual(
                report["efficiency_score"]["risk_events_blocked"], 1
            )
            self.assertEqual(
                "shareable_local_claim", report["claim_readiness"]["status"]
            )
            self.assertIn("validation_ladder", report)

            history_path = benchmark_history_path(root)
            self.assertTrue(history_path.exists())
            stored = history_path.read_text(encoding="utf-8")
            self.assertNotIn(secret_prompt, stored)
            self.assertNotIn("sk-test-secret-do-not-store", stored)
            self.assertIn("task_hash", stored)

            history = read_benchmark_history(root)
            self.assertEqual(1, len(history))
            self.assertEqual(report["run_id"], history[0]["run_id"])

    def test_markdown_report_includes_efficiency_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run_benchmark(root, suite="local", mode="both")

            markdown = render_benchmark_markdown(report)

            self.assertIn("# OPai Benchmark Report", markdown)
            self.assertIn("OPai Efficiency Score", markdown)
            self.assertIn("context reduction", markdown.lower())
            self.assertIn("promptfoo", markdown.lower())
            self.assertIn("Claim readiness", markdown)

    def test_benchmark_gate_passes_and_fails_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run_benchmark(root, suite="local", mode="both")

            passing = benchmark_gate(report, min_context_reduction=2.0)
            failing = benchmark_gate(report, min_context_reduction=999.0)

            self.assertTrue(passing["ok"], passing)
            self.assertFalse(failing["ok"], failing)
            self.assertIn("context_reduction_ratio", failing["failed"][0]["metric"])

    def test_compare_benchmark_reports_detects_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fast = run_benchmark(
                root,
                suite="local",
                mode="both",
                tasks=[
                    {
                        "id": "probe",
                        "category": "planning",
                        "prompt": "Plan carefully.",
                        "baseline_context_bytes": 10_000,
                        "opai_context_bytes": 500,
                    }
                ],
            )
            slow = run_benchmark(
                root,
                suite="local",
                mode="both",
                tasks=[
                    {
                        "id": "probe",
                        "category": "planning",
                        "prompt": "Plan carefully.",
                        "baseline_context_bytes": 10_000,
                        "opai_context_bytes": 5_000,
                    }
                ],
            )

            comparison = compare_benchmark_reports(fast, slow)
            markdown = render_benchmark_comparison_markdown(comparison)

            self.assertFalse(comparison["ok"])
            self.assertTrue(comparison["regressions"])
            self.assertIn("context_reduction_ratio", comparison["deltas"])
            self.assertIn("# OPai Benchmark Comparison", markdown)

    def test_promptfoo_export_is_privacy_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "promptfooconfig.yaml"

            export = export_promptfoo_config(root, out=out)

            self.assertEqual("promptfoo", export["harness"])
            self.assertTrue(out.exists())
            content = out.read_text(encoding="utf-8")
            self.assertIn("OPai coding-agent benchmark handoff", content)
            self.assertIn("task_hash", content)
            self.assertNotIn("Plan a small feature", content)

    def test_audited_benchmark_records_redacted_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run_benchmark(root, suite="local", mode="both", audit=True)

            events = read_audit(root)

            benchmark_events = [
                event for event in events if event.get("event_type") == BENCHMARK_RUN
            ]
            self.assertEqual(1, len(benchmark_events))
            self.assertEqual(report["run_id"], benchmark_events[0]["run_id"])
            raw_event = json.dumps(benchmark_events[0])
            self.assertNotIn("prompt", raw_event.lower())

    def test_cli_benchmark_list_run_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname = 'bench'\n")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["benchmark", "list", "--project", str(root)])
            self.assertEqual(0, code)
            self.assertIn("local", out.getvalue())

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "benchmark",
                        "run",
                        "--project",
                        str(root),
                        "--suite",
                        "local",
                        "--mode",
                        "both",
                    ]
                )
            self.assertEqual(0, code)
            payload = json.loads(out.getvalue())
            self.assertEqual("opai-benchmark", payload["kind"])
            self.assertEqual(str(root.resolve()), payload["project"])
            run_id = payload["run_id"]

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "benchmark",
                        "report",
                        "--project",
                        str(root),
                        "--format",
                        "markdown",
                    ]
                )
            self.assertEqual(0, code)
            self.assertIn("# OPai Benchmark Report", out.getvalue())

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "benchmark",
                        "report",
                        "--project",
                        str(root),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(0, code)
            payload = json.loads(out.getvalue())
            self.assertEqual(run_id, payload["run_id"])
            self.assertEqual("promptfoo", payload["external_harnesses"][0]["id"])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "benchmark",
                        "report",
                        "--project",
                        str(root),
                        "--format",
                        "html",
                    ]
                )
            self.assertEqual(0, code)
            self.assertIn("<!doctype html>", out.getvalue())

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "benchmark",
                        "gate",
                        "--project",
                        str(root),
                        "--min-context-reduction",
                        "2",
                    ]
                )
            self.assertEqual(0, code)
            self.assertTrue(json.loads(out.getvalue())["ok"])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "benchmark",
                        "gate",
                        "--project",
                        str(root),
                        "--min-context-reduction",
                        "999",
                    ]
                )
            self.assertEqual(1, code)
            self.assertFalse(json.loads(out.getvalue())["ok"])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    [
                        "benchmark",
                        "export",
                        "--project",
                        str(root),
                        "--harness",
                        "promptfoo",
                    ]
                )
            self.assertEqual(0, code)
            export_payload = json.loads(out.getvalue())
            self.assertEqual("promptfoo", export_payload["harness"])
            self.assertTrue(Path(export_payload["path"]).exists())


if __name__ == "__main__":
    unittest.main()
