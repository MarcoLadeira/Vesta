"""Dependency-light project-root discovery shared by bootstrap and the CLI."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT_MARKERS = (
    ".opaihub",
    ".git",
    "pyproject.toml",
    "package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "Cargo.toml",
    "go.mod",
    "composer.json",
    "Gemfile",
    "mix.exs",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "Dockerfile",
)


def discover_project_root(start: Path) -> Path:
    """Return the nearest marked project root without importing runtime code."""

    path = start.expanduser().resolve()
    current = path.parent if path.is_file() else path
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in PROJECT_ROOT_MARKERS):
            return candidate
    return current
