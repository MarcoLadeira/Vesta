"""Targeted test selection (power-efficiency roadmap Phase 4).

Maps changed source files to the tests most likely to cover them so Vesta can run
a focused subset instead of the whole suite. Pure, deterministic, read-only by
default - running tests is explicit.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from .proc import no_window_kwargs


# Base test command per framework marker.
FRAMEWORK_COMMANDS = [
    ("pyproject.toml", "python -m pytest"),
    ("pytest.ini", "python -m pytest"),
    ("tox.ini", "python -m pytest"),
    ("package.json", "npm test"),
    ("Cargo.toml", "cargo test"),
    ("go.mod", "go test ./..."),
]

_PY_TEST_PATTERNS = [
    "tests/test_{stem}.py",
    "tests/{stem}_test.py",
    "test_{stem}.py",
    "{stem}_test.py",
]
_JS_TEST_PATTERNS = [
    "{stem}.test.{ext}",
    "{stem}.spec.{ext}",
    "__tests__/{stem}.test.{ext}",
    "tests/{stem}.test.{ext}",
]


def _git_changed_files(root: Path) -> list[str]:
    git = shutil.which("git")
    if not git:
        return []
    files: list[str] = []
    # Modified (working tree), staged, and new untracked files. Untracked files
    # are real changes too; --exclude-standard keeps gitignored paths out.
    for args in (
        ["diff", "--name-only"],
        ["diff", "--name-only", "--cached"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        try:
            completed = subprocess.run(  # nosec B603
                [git, "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
                **no_window_kwargs(),  # no flashing console window on Windows
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            files.extend(
                line.strip() for line in completed.stdout.splitlines() if line.strip()
            )
    # Stable, de-duplicated order.
    return list(dict.fromkeys(files))


def detect_test_command(root: Path) -> dict[str, Any]:
    for marker, command in FRAMEWORK_COMMANDS:
        if (root / marker).exists():
            return {"framework_marker": marker, "command": command}
    return {"framework_marker": None, "command": None}


def likely_tests_for(source_path: str, root: Path) -> list[str]:
    """Return existing test files that most likely cover a changed source file."""
    path = Path(source_path)
    stem = path.stem
    suffix = path.suffix.lower()
    if suffix == ".py" and stem.startswith("test_"):
        return [source_path] if (root / source_path).exists() else []
    candidates: list[str] = []
    if suffix == ".py":
        for pattern in _PY_TEST_PATTERNS:
            candidates.append(pattern.format(stem=stem))
            if path.parent != Path("."):
                candidates.append(str(path.parent / f"test_{stem}.py"))
    elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
        ext = suffix.lstrip(".")
        for pattern in _JS_TEST_PATTERNS:
            candidates.append(pattern.format(stem=stem, ext=ext))
            if path.parent != Path("."):
                candidates.append(str(path.parent / pattern.format(stem=stem, ext=ext)))
    found = []
    for candidate in dict.fromkeys(candidates):
        if (root / candidate).exists():
            found.append(candidate.replace("\\", "/"))
    return found


def select_tests(
    project_root: Path, changed: list[str] | None = None
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    changed_files = changed if changed is not None else _git_changed_files(root)
    selected: list[str] = []
    mapping: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for source in changed_files:
        tests = likely_tests_for(source, root)
        if tests:
            mapping[source] = tests
            selected.extend(tests)
        elif source.endswith((".py", ".js", ".jsx", ".ts", ".tsx")):
            unmatched.append(source)
    selected = list(dict.fromkeys(selected))
    detector = detect_test_command(root)
    command = detector["command"]
    targeted_command = None
    if command and selected:
        if command.startswith("python -m pytest"):
            targeted_command = "python -m pytest " + " ".join(selected)
        else:
            targeted_command = command
    return {
        "project": str(root),
        "changed_files": changed_files,
        "selected_tests": selected,
        "mapping": mapping,
        "unmatched_sources": unmatched,
        "framework": detector["framework_marker"],
        "base_command": command,
        "targeted_command": targeted_command,
        "note": "Selection is heuristic; fall back to the full suite when unmatched sources exist.",
    }
