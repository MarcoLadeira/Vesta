from __future__ import annotations

from pathlib import Path
from typing import Any

from .context_manager import load_profile
from .cost import route_task
from .gitops import suggest_branch_name
from .scanner import scan_project
from .utils import now_iso, project_op_dir, write_json


def ship_plan(root: Path, task: str) -> dict[str, Any]:
    profile = load_profile(root) or scan_project(root)
    commands = profile.get("commands", {})
    route = route_task(f"build and ship: {task}", root=root)
    plan = {
        "created_at": now_iso(),
        "task": task,
        "route": route,
        "branch": suggest_branch_name(task),
        "commands": {
            "install": commands.get("install"),
            "build": commands.get("build"),
            "test": commands.get("test"),
            "lint": commands.get("lint"),
            "typecheck": commands.get("typecheck"),
        },
        "ship_in_two_prompts": [
            "Prompt 1: op ship plan, collect profile/context, produce implementation plan and test/deploy checklist.",
            "Prompt 2: implement the plan, run targeted tests, review, prepare PR/deploy checklist.",
        ],
        "automatic_gates": [
            "cost route before model",
            "secret scan before commit",
            "targeted tests before full tests",
            "review risky diffs before PR",
            "explicit confirmation before push/deploy/expensive model",
        ],
    }
    write_json(project_op_dir(root) / "cache" / "ship-plan.json", plan)
    return plan
