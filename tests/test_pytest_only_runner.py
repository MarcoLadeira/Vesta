from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    path = ROOT / "scripts" / "run_pytest_only.py"
    assert path.exists(), "selective pytest runner is missing"
    spec = importlib.util.spec_from_file_location("vesta_pytest_only", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PytestOnlyRunnerTests(unittest.TestCase):
    def test_selects_only_files_unittest_cannot_collect(self) -> None:
        runner = _load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            tests = Path(tmp)
            (tests / "test_unittest.py").write_text(
                "import unittest\n"
                "class TestUnit(unittest.TestCase):\n"
                "    def test_it(self): pass\n",
                encoding="utf-8",
            )
            (tests / "test_pytest.py").write_text(
                "def test_it(): pass\n", encoding="utf-8"
            )
            (tests / "test_mixed.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_unit(self): pass\n"
                "def test_pytest_only(): pass\n",
                encoding="utf-8",
            )

            with mock.patch.object(runner, "TESTS", tests):
                selected = runner.pytest_only_paths()

            self.assertEqual(
                selected,
                [tests / "test_mixed.py", tests / "test_pytest.py"],
            )

    def test_main_runs_only_the_selected_files(self) -> None:
        runner = _load_runner()
        selected = [ROOT / "tests" / "test_run_state.py"]
        completed = subprocess.CompletedProcess(args=[], returncode=7)

        with (
            mock.patch.object(runner, "pytest_only_paths", return_value=selected),
            mock.patch.object(runner.subprocess, "run", return_value=completed) as run,
        ):
            result = runner.main()

        self.assertEqual(result, 7)
        self.assertEqual(
            run.call_args.args[0],
            [sys.executable, "-m", "pytest", "-q", str(selected[0])],
        )
        self.assertEqual(run.call_args.kwargs["cwd"], str(runner.ROOT))

    def test_empty_selection_succeeds_without_starting_pytest(self) -> None:
        runner = _load_runner()
        with (
            mock.patch.object(runner, "pytest_only_paths", return_value=[]),
            mock.patch.object(runner.subprocess, "run") as run,
        ):
            result = runner.main()

        self.assertEqual(result, 0)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
