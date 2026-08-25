"""Run test files that ``unittest discover`` cannot collect."""

from __future__ import annotations

import ast
import subprocess  # nosec B404 - fixed interpreter and repository-owned paths
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_test_collection import TESTS, _unittest_counts  # noqa: E402


def _has_pytest_style_tests(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test"):
            return True
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            bases = {
                base.attr
                if isinstance(base, ast.Attribute)
                else getattr(base, "id", "")
                for base in node.bases
            }
            if any(
                name.endswith("TestCase") or name.endswith("Base") for name in bases
            ):
                continue
            if any(
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name.startswith("test")
                for item in node.body
            ):
                return True
    return False


def pytest_only_paths() -> list[Path]:
    counts = _unittest_counts(TESTS)
    return sorted(
        path
        for path in TESTS.glob("test_*.py")
        if counts[path.name] == 0 or _has_pytest_style_tests(path)
    )


def main() -> int:
    selected = pytest_only_paths()
    if not selected:
        return 0
    completed = subprocess.run(  # nosec B603 - fixed argv and selected repo tests
        [sys.executable, "-m", "pytest", "-q", *(str(path) for path in selected)],
        cwd=str(ROOT),
        check=False,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
