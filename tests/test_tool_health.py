"""Real per-tool health checks (#2): installed / runnable / passed_on_project."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from vestahub.tool_health import (
    KNOWN_TOOL_CHECKS,
    check_tool_health,
    is_known_tool,
    known_tool_ids,
    tool_health_summary,
)


def _completed(argv, code=0, out="", err=""):
    return subprocess.CompletedProcess(argv, code, out, err)


class _FakeTools:
    """Injectable runner + which for hermetic health checks (no real tools)."""

    def __init__(self, *, installed=True, version_ok=True, project_ok=True):
        self.installed = installed
        self.version_ok = version_ok
        self.project_ok = project_ok
        self.calls: list[list[str]] = []

    def which(self, name):
        return f"/usr/bin/{name}" if self.installed else None

    def run(self, argv, cwd, timeout):
        self.calls.append(argv)
        # The version call is the shorter one; the project check is longer.
        is_version = argv[1:] in [list(v) for v in [("--version",), ("version",)]]
        if is_version:
            return _completed(argv, 0 if self.version_ok else 1, out="tool 1.2.3")
        return _completed(argv, 0 if self.project_ok else 2, out="findings...")


class KnownToolsTests(unittest.TestCase):
    def test_the_eight_named_tools_are_known(self):
        for tool in (
            "ruff",
            "bandit",
            "detect-secrets",
            "markdownlint",
            "pyright",
            "gitleaks",
            "actionlint",
            "pip-audit",
        ):
            self.assertTrue(is_known_tool(tool), tool)
        self.assertEqual(set(known_tool_ids()), set(KNOWN_TOOL_CHECKS))

    def test_aliases_resolve(self):
        self.assertTrue(is_known_tool("detect_secrets"))
        self.assertTrue(is_known_tool("pip_audit"))

    def test_unknown_tool_is_not_known(self):
        self.assertFalse(is_known_tool("not-a-tool"))


class CheckToolHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_healthy_tool_runs_a_cheap_tool_specific_command_not_the_doctor(self):
        fake = _FakeTools()
        result = check_tool_health("ruff", self.root, run=fake.run, which=fake.which)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["installed"])
        self.assertTrue(result["runnable"])
        self.assertTrue(result["passed_on_current_project"])
        # It ran `ruff`, never `opcoding ... doctor`.
        flat = [" ".join(c) for c in fake.calls]
        self.assertTrue(any(c.startswith("ruff ") for c in flat))
        self.assertFalse(any("opcoding" in c for c in flat))
        self.assertTrue(any("--version" in c for c in flat))
        self.assertTrue(any("check" in c for c in flat))

    def test_failing_project_check_reports_failing_with_a_fix(self):
        fake = _FakeTools(project_ok=False)
        result = check_tool_health("ruff", self.root, run=fake.run, which=fake.which)
        self.assertEqual(result["status"], "failing")
        self.assertTrue(result["runnable"])
        self.assertFalse(result["passed_on_current_project"])
        self.assertIn("ruff check", result["fix"])
        self.assertIn("output_tail", result)

    def test_missing_tool_reports_install_hint(self):
        fake = _FakeTools(installed=False)
        result = check_tool_health("bandit", self.root, run=fake.run, which=fake.which)
        self.assertEqual(result["status"], "missing")
        self.assertFalse(result["installed"])
        self.assertIn("pip install bandit", result["fix"])
        self.assertEqual(fake.calls, [])  # never runs a missing binary

    def test_installed_but_not_runnable_is_distinguished(self):
        fake = _FakeTools(version_ok=False)
        result = check_tool_health("ruff", self.root, run=fake.run, which=fake.which)
        self.assertEqual(result["status"], "installed")
        self.assertTrue(result["installed"])
        self.assertFalse(result["runnable"])

    def test_tool_without_a_project_check_is_runnable(self):
        fake = _FakeTools()
        result = check_tool_health("pyright", self.root, run=fake.run, which=fake.which)
        self.assertEqual(result["status"], "runnable")
        self.assertIsNone(result["passed_on_current_project"])

    def test_project_check_can_be_skipped(self):
        fake = _FakeTools()
        result = check_tool_health(
            "ruff",
            self.root,
            run=fake.run,
            which=fake.which,
            include_project_check=False,
        )
        self.assertEqual(result["status"], "runnable")
        self.assertIsNone(result["passed_on_current_project"])

    def test_unknown_tool_returns_unknown(self):
        result = check_tool_health("mystery", self.root)
        self.assertFalse(result["known"])
        self.assertEqual(result["status"], "unknown")

    def test_a_crashing_runner_is_not_fatal(self):
        def boom(argv, cwd, timeout):
            raise OSError("tool exploded")

        result = check_tool_health(
            "ruff", self.root, run=boom, which=lambda n: "/usr/bin/ruff"
        )
        self.assertEqual(result["status"], "installed")  # version run failed
        self.assertFalse(result["runnable"])


class SummaryAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_summary_lists_failing_tools_with_fixes(self):
        fake = _FakeTools(installed=False)
        summary = tool_health_summary(self.root, run=fake.run, which=fake.which)
        self.assertEqual(summary["installed"], 0)
        self.assertEqual(len(summary["failing"]), summary["total"])
        self.assertTrue(all(item["fix"] for item in summary["failing"]))

    def test_cli_tool_health_uses_the_per_tool_check(self):
        import contextlib
        import io
        import json

        from vestahub.cli import main

        # ruff is installed in this environment (CI installs it); the per-tool
        # path must run a ruff command and return structured per-tool health.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--project", str(self.root), "tool", "health", "--id", "ruff"])
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["known"])
        self.assertEqual(payload["id"], "ruff")
        self.assertIn("installed", payload)
        # Exit code reflects health: 0 for passed/runnable, 1 otherwise.
        self.assertIn(code, (0, 1))

    def test_cli_unknown_tool_falls_back_without_crashing(self):
        import contextlib
        import io

        from vestahub.cli import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(
                ["--project", str(self.root), "tool", "health", "--id", "not-real"]
            )
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
