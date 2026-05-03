from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .command_runner import run_policy_command
from .loader import registry_items


MARKERS = [
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "go.mod",
    "Dockerfile",
    ".github/workflows",
    "tests",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _command_tail(root: Path, argv: list[str], timeout: int = 15) -> dict[str, Any]:
    if not shutil.which(argv[0]):
        return {"argv": argv, "returncode": 127, "output_tail": f"{argv[0]} not found"}
    result = run_policy_command(argv, root, timeout=timeout)
    return {
        "argv": argv,
        "returncode": result.returncode,
        "output_tail": result.combined_output[-2000:],
        "executed": result.executed,
        "policy": result.policy["decision"],
    }


def _skipped(argv: list[str], reason: str) -> dict[str, Any]:
    return {
        "argv": argv,
        "returncode": 0,
        "output_tail": f"skipped: {reason}",
        "executed": False,
        "policy": "skip",
    }


def _detect_test_commands(root: Path) -> list[str]:
    commands: list[str] = []
    if (root / "tests").is_dir() or (root / "pyproject.toml").exists():
        commands.append("python -m unittest discover -s tests")
    if (root / "package.json").exists():
        commands.append("npm test")
    if (root / "Cargo.toml").exists():
        commands.append("cargo test")
    if (root / "go.mod").exists():
        commands.append("go test ./...")
    return commands


def _detect_languages(root: Path, max_files: int = 1000) -> list[str]:
    suffix_map = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".cs": "csharp",
        ".java": "java",
    }
    found: set[str] = set()
    count = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(
            part.startswith(".opai") or part.startswith(".opcoding")
            for part in path.parts
        ):
            continue
        count += 1
        if count > max_files:
            break
        language = suffix_map.get(path.suffix.lower())
        if language:
            found.add(language)
    return sorted(found)


def collect_evidence(project_root: Path, task: str = "") -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    markers = [marker for marker in MARKERS if (root / marker).exists()]
    git_status = _command_tail(root, ["git", "status", "--short", "--branch"])
    is_git_repo = git_status["returncode"] == 0
    changed_files_argv = ["git", "diff", "--name-only", "--", "."]
    diff_stat_argv = ["git", "diff", "--stat", "--", "."]
    changed_files = (
        _command_tail(root, changed_files_argv)
        if is_git_repo
        else _skipped(changed_files_argv, "not a git repository")
    )
    diff_stat = (
        _command_tail(root, diff_stat_argv)
        if is_git_repo
        else _skipped(diff_stat_argv, "not a git repository")
    )

    registry_counts = {
        name: len(registry_items(name, root))
        for name in ["tools", "agents", "workflows", "mcp_servers", "models"]
    }
    payload = {
        "created_at": _now_iso(),
        "project": str(root),
        "task": task,
        "ai_used": False,
        "markers": markers,
        "languages": _detect_languages(root),
        "test_commands": _detect_test_commands(root),
        "git": {
            "is_repo": is_git_repo,
            "status": git_status,
            "changed_files": changed_files,
            "diff_stat": diff_stat,
        },
        "registry_counts": registry_counts,
    }
    payload["cache_key"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return payload
