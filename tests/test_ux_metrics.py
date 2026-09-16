"""Local-only product-health metrics (#395)."""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from vestahub.ledger import UNKNOWN, record_event
from vestahub.ux_metrics import (
    render_ux_metrics_markdown,
    summarize_ux_metrics,
)


class UxMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _record(self, *verdicts: str) -> None:
        for i, verdict in enumerate(verdicts):
            record_event(self.root, "completion_verdict", task="t", verdict=verdict)

    def test_verdict_distribution_and_rates(self):
        self._record(
            "completed",
            "completed",
            "completed",
            "partial",
            "cancelled",
            "timeout",
            "failed",
            "blocked",
        )
        metrics = summarize_ux_metrics(self.root)
        self.assertEqual(metrics["runs"], 8)
        self.assertEqual(metrics["verdict_distribution"]["completed"], 3)
        self.assertEqual(metrics["verdict_distribution"]["cancelled"], 1)
        self.assertAlmostEqual(metrics["rates"]["completion"], 0.375)
        self.assertAlmostEqual(metrics["rates"]["cancel"], 0.125)
        self.assertAlmostEqual(metrics["rates"]["timeout"], 0.125)

    def test_empty_project_reports_zero_runs_and_unknown_rates_not_zero(self):
        metrics = summarize_ux_metrics(self.root)
        self.assertEqual(metrics["runs"], 0)
        for rate in metrics["rates"].values():
            self.assertEqual(rate, UNKNOWN)

    def test_unrecognised_verdicts_are_counted_separately_not_dropped(self):
        self._record("completed", "mystery_state", "another_bad_one")
        metrics = summarize_ux_metrics(self.root)
        self.assertEqual(metrics["runs"], 1)  # only the real verdict
        self.assertEqual(metrics["unclassified_verdicts"], 2)

    def test_non_verdict_events_are_ignored(self):
        record_event(self.root, "route_decision", task="t", model_tier="L1")
        self._record("completed")
        metrics = summarize_ux_metrics(self.root)
        self.assertEqual(metrics["runs"], 1)

    def test_markdown_renders_for_empty_and_populated(self):
        empty = render_ux_metrics_markdown(summarize_ux_metrics(self.root))
        self.assertIn("No runs recorded yet", empty)
        self._record("completed", "cancelled")
        populated = render_ux_metrics_markdown(summarize_ux_metrics(self.root))
        self.assertIn("Runs: 2", populated)
        self.assertIn("completed", populated)
        self.assertIn("50.0%", populated)

    def test_summary_makes_no_network_call(self):
        # #395 is for a telemetry-allergic audience: the metrics engine must be
        # provably local. Any socket use raises, so a network call would fail here.
        self._record("completed", "failed")

        def _no_network(*args, **kwargs):
            raise AssertionError("ux-metrics must not open a socket")

        with mock.patch.object(socket, "socket", _no_network):
            metrics = summarize_ux_metrics(self.root)
        self.assertEqual(metrics["runs"], 2)


class UxMetricsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cli_prints_json_and_markdown(self):
        import argparse
        import io
        from contextlib import redirect_stdout

        from vesta.cli import cmd_ux_metrics

        record_event(self.root, "completion_verdict", task="t", verdict="completed")
        args = argparse.Namespace(project=str(self.root), markdown=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cmd_ux_metrics(args)
        self.assertEqual(code, 0)
        self.assertIn('"runs": 1', buf.getvalue())

        args.markdown = True
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_ux_metrics(args)
        self.assertIn("product health", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
