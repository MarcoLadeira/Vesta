from __future__ import annotations

import subprocess  # nosec B404 - read-only git argv calls, no shell
from pathlib import Path
from typing import Any


def _git(root: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(  # nosec B603 - argv list, no shell
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip()


def lane_status(project_root: Path) -> dict[str, Any]:
    """Read-only work lane summary for Safe Auto.

    This never creates a worktree. Creating isolated implementation lanes is a
    mutating git operation and remains confirmation-required.
    """
    root = project_root.expanduser().resolve()
    branch = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    head = _git(root, ["rev-parse", "--short", "HEAD"])
    status = _git(root, ["status", "--short"])
    worktrees = [
        line[len("worktree ") :].strip()
        for line in _git(root, ["worktree", "list", "--porcelain"]).splitlines()
        if line.startswith("worktree ")
    ]
    has_extra_worktree = any(Path(path).resolve() != root for path in worktrees)
    dirty = bool(status.strip())
    recommended = "worktree" if dirty else "local"
    return {
        "project_root": str(root),
        "recommended_lane": recommended,
        "lanes": [
            {
                "id": "local",
                "label": "Local checkout",
                "active": True,
                "path": str(root),
                "branch": branch if branch and branch != "HEAD" else "",
                "head": head,
                "dirty": dirty,
                "read_only": False,
                "create_requires_confirmation": False,
            },
            {
                "id": "worktree",
                "label": "Isolated worktree lane",
                "active": has_extra_worktree,
                "path": None,
                "dirty": False,
                "read_only": False,
                "create_requires_confirmation": True,
                "why": "Use for edit-heavy Safe Auto tasks when the current checkout is dirty.",
            },
            {
                "id": "review",
                "label": "Review lane",
                "active": False,
                "path": str(root),
                "dirty": dirty,
                "read_only": True,
                "create_requires_confirmation": False,
                "why": "Use for audits, planning, and diff review without changing files.",
            },
        ],
    }
