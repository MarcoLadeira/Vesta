from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import command_exists, project_op_dir, run_command


TOOLS = ["python", "git", "node", "npm", "docker", "gh", "rg"]


def doctor(root: Path) -> dict[str, Any]:
    tools = {tool: command_exists(tool) for tool in TOOLS}
    git = run_command("git status --short --branch", root, timeout=20)
    op_dir = project_op_dir(root)
    writable = True
    try:
        op_dir.mkdir(parents=True, exist_ok=True)
        probe = op_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        writable = False
    warnings = []
    if not tools.get("git"):
        warnings.append("git is missing; GitOps and diff-based context will be limited")
    if not tools.get("rg"):
        warnings.append(
            "ripgrep is missing or unavailable; Python fallback scanner will be used"
        )
    if git.returncode != 0:
        warnings.append(
            "project is not a git repo; commit and diff workflows will be limited"
        )
    if not writable:
        warnings.append(".opcoding directory is not writable")
    return {
        "root": str(root),
        "tools": tools,
        "git_status_available": git.returncode == 0,
        "opcoding_writable": writable,
        "warnings": warnings,
    }
