"""OPaiBench runner + dashboard (#180): deterministic, local, regression-aware."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub import opaibench

from opaihub.opaibench import (
    DIMENSIONS,
    SCENARIOS,
    build_opaibench_dashboard,
    detect_regressions,
    latest_opaibench_report,
    read_opaibench_history,
    run_opaibench,
)


class RunnerTests(unittest.TestCase):
    def test_every_dimension_has_scenarios_and_all_pass_on_current_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_opaibench(Path(tmp), write=False)

        self.assertEqual(set(report["dimensions"]), set(DIMENSIONS))
        for name, data in report["dimensions"].items():
            self.assertGreaterEqual(data["total"], 3, name)
            self.assertEqual(data["passed"], data["total"], name)
        self.assertEqual(report["totals"]["passed"], report["totals"]["total"])
        failing = [item for item in report["scenarios"] if not item["passed"]]
        self.assertEqual(failing, [])

    def test_latency_and_zero_cost_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_opaibench(Path(tmp), write=False)

        self.assertGreaterEqual(report["totals"]["latency_ms"], 0.0)
        for item in report["scenarios"]:
            self.assertGreaterEqual(item["latency_ms"], 0.0)
        self.assertEqual(report["cost"]["cloud_calls"], 0)
        self.assertEqual(report["cost"]["cost_usd"], 0.0)

    def test_crashing_scenarios_fail_instead_of_crashing_the_run(self):
        def boom():
            raise RuntimeError("scenario exploded")

        scenario = {"id": "intent.boom", "dimension": "intent", "run": boom}
        with tempfile.TemporaryDirectory() as tmp:
            report = run_opaibench(Path(tmp), write=False, scenarios=(scenario,))

        self.assertFalse(report["scenarios"][0]["passed"])
        self.assertIn("RuntimeError", report["scenarios"][0]["detail"])

    def test_runs_append_to_durable_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = run_opaibench(root)
            second = run_opaibench(root)
            history = read_opaibench_history(root)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["run_id"], first["run_id"])
        self.assertEqual(history[1]["run_id"], second["run_id"])

    def test_latest_report_reads_the_most_recent_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(latest_opaibench_report(root))
            run_opaibench(root)
            newest = run_opaibench(root)
            self.assertEqual(latest_opaibench_report(root)["run_id"], newest["run_id"])

    def test_run_reads_only_the_previous_history_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                opaibench, "read_opaibench_history", return_value=[]
            ) as history:
                run_opaibench(root, write=False, scenarios=())

        history.assert_called_once_with(root, limit=1)


class RegressionTests(unittest.TestCase):
    def _run(self, run_id, scenarios, dimensions):
        return {
            "run_id": run_id,
            "scenarios": scenarios,
            "dimensions": dimensions,
        }

    def test_first_run_has_no_baseline(self):
        result = detect_regressions(self._run("a", [], {}), None)
        self.assertIsNone(result["baseline_run_id"])
        self.assertEqual(result["scenarios"], [])

    def test_newly_failing_scenarios_are_named(self):
        previous = self._run(
            "old",
            [{"id": "gates.catch_secrets", "passed": True}],
            {"safety_gates": {"score": 1.0}},
        )
        current = self._run(
            "new",
            [{"id": "gates.catch_secrets", "passed": False}],
            {"safety_gates": {"score": 0.75}},
        )
        result = detect_regressions(current, previous)
        self.assertEqual(result["baseline_run_id"], "old")
        self.assertEqual(result["scenarios"], ["gates.catch_secrets"])
        self.assertEqual(result["dimensions"][0]["dimension"], "safety_gates")

    def test_a_scenario_that_always_failed_is_not_a_regression(self):
        previous = self._run("old", [{"id": "x", "passed": False}], {})
        current = self._run("new", [{"id": "x", "passed": False}], {})
        result = detect_regressions(current, previous)
        self.assertEqual(result["scenarios"], [])

    def test_second_identical_run_reports_no_regressions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_opaibench(root)
            second = run_opaibench(root)
        self.assertEqual(second["regressions"]["scenarios"], [])
        self.assertEqual(second["regressions"]["dimensions"], [])
        self.assertIsNotNone(second["regressions"]["baseline_run_id"])


class DashboardTests(unittest.TestCase):
    def test_dashboard_renders_dimensions_cost_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_opaibench(root)
            run_opaibench(root)
            path = build_opaibench_dashboard(root)
            html = path.read_text(encoding="utf-8")

        self.assertTrue(path.name.endswith(".html"))
        for label in (
            "Intent resolution",
            "Context quality",
            "Repair success",
            "Safety gates",
            "Run history",
            "$0.00",
            "No regressions against the previous run.",
        ):
            self.assertIn(label, html)

    def test_dashboard_without_runs_points_to_the_run_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = build_opaibench_dashboard(Path(tmp)).read_text(encoding="utf-8")
        self.assertIn("No OPaiBench run recorded yet", html)
        self.assertIn("opai hub opaibench run", html)


class CliTests(unittest.TestCase):
    def _main(self, argv):
        import contextlib
        import io

        from opaihub.cli import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def test_cli_run_reports_and_exits_zero_when_all_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, output = self._main(["--project", tmp, "opaibench", "run"])
            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["kind"], "opaibench")

            code, output = self._main(["--project", tmp, "opaibench", "dashboard"])
            self.assertEqual(code, 0)
            self.assertTrue(Path(json.loads(output)["path"]).exists())

            code, output = self._main(
                ["--project", tmp, "opaibench", "history", "--limit", "5"]
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(output)), 1)

    def test_scenario_registry_ids_are_unique(self):
        ids = [scenario["id"] for scenario in SCENARIOS]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
