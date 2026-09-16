from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

# The header these rules were written under before the rebrand to Vesta.
from vesta.legacy import LEGACY_IGNORE_HEADER, legacy_spelling


AI_IGNORE_FILES = [
    ".claudeignore",
    ".cursorignore",
    ".aiderignore",
    ".continueignore",
    ".geminiignore",
    ".vestaignore",
]

AI_IGNORE_PATTERNS = [
    "# Vesta context-slimming rules",
    ".git/",
    ".opcoding/",
    ".opcoding-tools/",
    ".opcoding-tool-cache/",
    ".vestahub/cache/",
    ".vestahub/logs/",
    ".vestahub/generated/",
    ".vestahub/health/",
    ".vestahub/install-test-*/",
    ".vestahub/smoke-install-venv/",
    ".vestahub/wheelhouse/",
    ".ruff_cache/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".tox/",
    ".nox/",
    "vesta.egg-info/",
    "*.egg-info/",
    "build/",
    "dist/",
    "__pycache__/",
    "**/__pycache__/",
    "node_modules/",
    "**/node_modules/",
    ".venv/",
    "venv/",
    "env/",
    "**/.venv/",
    "**/venv/",
    ".next/",
    ".nuxt/",
    "coverage/",
    ".coverage",
    "htmlcov/",
    "*.log",
]

GENERATED_CONTEXT_TARGETS = [
    ".opcoding-tools",
    ".opcoding-tool-cache",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "vesta.egg-info",
    "build",
    "dist",
]

VESTAHUB_GENERATED_TARGETS = [
    "cache",
    "logs",
    "generated",
    "health",
    "wheelhouse",
    "smoke-install-venv",
]


def _append_unique(existing: str, lines: list[str]) -> str:
    body = existing.rstrip()
    present = {line.strip() for line in existing.splitlines()}
    missing = [line for line in lines if line.strip() not in present]
    if not body:
        return "\n".join(lines).rstrip() + "\n"
    if not missing:
        return body + "\n"
    return body + "\n\n" + "\n".join(missing).rstrip() + "\n"


def _drop_legacy_pattern_lines(text: str) -> str:
    """``text`` without the lines an install from before the rename generated.

    Those lines name the old state directory and package, so they match nothing
    any more; the current spelling of each is appended in their place. Every
    other line is the user's and stays.
    """

    lines = text.splitlines()
    kept = [line for line in lines if line.strip() not in _LEGACY_PATTERN_LINES]
    if len(kept) == len(lines):
        return text
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


# The pre-rename spelling of each generated pattern that the rename changed.
_LEGACY_PATTERN_LINES = {
    legacy_spelling(line)
    for line in AI_IGNORE_PATTERNS[1:]
    if legacy_spelling(line) != line
}


def write_ai_ignore_files(project_root: Path) -> list[str]:
    root = project_root.expanduser().resolve()
    written: list[str] = []
    for name in AI_IGNORE_FILES:
        path = root / name
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        # Rename the old header in place rather than appending a second one.
        current = existing.replace(LEGACY_IGNORE_HEADER, AI_IGNORE_PATTERNS[0])
        current = _drop_legacy_pattern_lines(current)
        updated = _append_unique(current, AI_IGNORE_PATTERNS)
        if updated != existing:
            path.write_text(updated, encoding="utf-8")
        written.append(str(path))
    return written


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    if path.is_file():
        return path.stat().st_size
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def _mb(size: int) -> float:
    return round(size / (1024 * 1024), 2)


def generated_context_targets(project_root: Path) -> list[Path]:
    root = project_root.expanduser().resolve()
    targets = [root / name for name in GENERATED_CONTEXT_TARGETS]
    vestahub = root / ".vestahub"
    targets.extend(vestahub / name for name in VESTAHUB_GENERATED_TARGETS)
    if vestahub.exists():
        targets.extend(
            path for path in vestahub.glob("install-test-*") if path.is_dir()
        )
    return targets


def context_bloat_report(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    items = []
    total = 0
    for path in generated_context_targets(root):
        if not path.exists():
            continue
        size = _dir_size(path)
        total += size
        items.append(
            {
                "path": str(path),
                "relative": path.relative_to(root).as_posix(),
                "mb": _mb(size),
                "kind": "directory" if path.is_dir() else "file",
            }
        )
    items.sort(key=lambda item: item["mb"], reverse=True)
    return {
        "project_root": str(root),
        "total_generated_mb": _mb(total),
        "items": items,
        "ai_ignore_files": [str(root / name) for name in AI_IGNORE_FILES],
        "policy": "generated caches stay out of model context; use --clean to remove local bloat",
    }


def clean_generated_context(project_root: Path, dry_run: bool = True) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    cleaned = []
    skipped = []
    root_marker = str(root)
    for path in generated_context_targets(root):
        if not path.exists():
            continue
        resolved = path.resolve()
        if not str(resolved).startswith(root_marker):
            skipped.append({"path": str(path), "reason": "outside project root"})
            continue
        item = {
            "path": str(resolved),
            "relative": resolved.relative_to(root).as_posix(),
            "mb": _mb(_dir_size(resolved)),
        }
        if not dry_run:
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
        cleaned.append(item)
    return {
        "project_root": str(root),
        "dry_run": dry_run,
        "removed": cleaned,
        "skipped": skipped,
        "total_mb": round(sum(item["mb"] for item in cleaned), 2),
    }
