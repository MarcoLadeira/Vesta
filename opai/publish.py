from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any


GENERATED_DIRS = [
    ".opaihub/",
    ".opcoding-tools/",
    ".ruff_cache/",
    "*.egg-info/",
    "build/",
    "dist/",
    "__pycache__/",
]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _gitignore_status(root: Path) -> dict[str, bool]:
    text = _read_text(root / ".gitignore")
    return {pattern: pattern in text for pattern in GENERATED_DIRS}


def _script_status(root: Path) -> dict[str, str]:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return {}
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return dict(data.get("project", {}).get("scripts", {}))


def publish_status(root: Path) -> dict[str, Any]:
    project_root = root.expanduser().resolve()
    scripts = _script_status(project_root)
    gitignore = _gitignore_status(project_root)
    required_files = [
        "README.md",
        "pyproject.toml",
        ".gitignore",
        "scripts/smoke-install.py",
        ".github/workflows/ci.yml",
    ]
    files = {name: (project_root / name).exists() for name in required_files}
    is_repo_root = (project_root / ".git").exists()
    ready = (
        is_repo_root
        and all(files.values())
        and all(gitignore.values())
        and scripts.get("op") == "opai.cli:main"
        and scripts.get("opai") == "opai.cli:main"
    )
    next_steps: list[str] = []
    if not is_repo_root:
        next_steps.append("git init -b main")
    if not all(files.values()):
        next_steps.append("add missing publish files")
    if not all(gitignore.values()):
        next_steps.append("complete generated-artifact .gitignore coverage")
    if scripts.get("op") != "opai.cli:main":
        next_steps.append("make op entry point launch OPai")
    return {
        "ready": ready,
        "root": str(project_root),
        "git": {"is_repo_root": is_repo_root, "path": str(project_root / ".git")},
        "files": files,
        "gitignore": gitignore,
        "scripts": scripts,
        "next_steps": next_steps,
    }


def write_publish_status(root: Path) -> Path:
    status = publish_status(root)
    path = root.expanduser().resolve() / ".opaihub" / "publish-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path
