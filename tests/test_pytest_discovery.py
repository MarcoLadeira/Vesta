"""Regression tests for canonical pytest discovery scope."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def collect_pytest(*paths: str) -> str:
    completed = subprocess.run(  # nosec B603 - fixed hermetic Python argv
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}".replace("\\", "/")
    assert completed.returncode == 0, output
    return output


def test_default_collection_excludes_embedded_benchmark_fixtures() -> None:
    collected = collect_pytest()

    assert "tests/test_gui_web.py" in collected
    assert "opaihub/data/hub/benchmarks/parity" not in collected


def test_explicit_collection_still_allows_embedded_benchmark_fixture() -> None:
    fixture = "opaihub/data/hub/benchmarks/parity/bugfix/repo/test_calculator.py"

    collected = collect_pytest(fixture)

    assert (
        f"{fixture}::CalculatorTests::test_adds_positive_and_negative_values"
        in collected
    )
