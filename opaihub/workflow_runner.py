from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .command_runner import run_policy_command
from .loader import registry_items
from .state import state_dir


SAFE_STEP_COMMANDS = {
    "opcoding scan": ["python", "-m", "opcoding", "scan", "${PROJECT_ROOT}", "--json"],
    "opcoding init": ["python", "-m", "opcoding", "init", "${PROJECT_ROOT}"],
    "opcoding context": [
        "python",
        "-m",
        "opcoding",
        "context",
        "${PROJECT_ROOT}",
        "--refresh",
    ],
    "op-hub doctor": [
        "python",
        "-m",
        "opaihub",
        "--project",
        "${PROJECT_ROOT}",
        "doctor",
    ],
    "opcoding test": [
        "python",
        "-m",
        "opcoding",
        "test",
        "${PROJECT_ROOT}",
        "--dry-run",
    ],
    "opcoding review": ["python", "-m", "opcoding", "review", "${PROJECT_ROOT}"],
    "opcoding git": ["python", "-m", "opcoding", "git", "${PROJECT_ROOT}", "summary"],
}


def find_workflow(project_root: Path, workflow_id: str) -> dict[str, Any] | None:
    for workflow in registry_items("workflows", project_root):
        if workflow.get("id") == workflow_id:
            return workflow
    return None


def _render_command(parts: list[str], project_root: Path) -> list[str]:
    return [
        part.replace("${PROJECT_ROOT}", str(project_root.resolve())) for part in parts
    ]


def workflow_plan(project_root: Path, workflow_id: str) -> dict[str, Any]:
    workflow = find_workflow(project_root, workflow_id)
    if not workflow:
        return {"ok": False, "error": f"workflow not found: {workflow_id}"}
    steps = []
    for step in workflow.get("steps", []):
        command = SAFE_STEP_COMMANDS.get(step)
        steps.append(
            {
                "step": step,
                "safe_command": _render_command(command, project_root)
                if command
                else None,
                "executable": command is not None,
            }
        )
    return {"ok": True, "workflow": workflow, "steps": steps}


def run_workflow(
    project_root: Path, workflow_id: str, execute: bool = False, timeout: int = 120
) -> dict[str, Any]:
    plan = workflow_plan(project_root, workflow_id)
    if not plan.get("ok"):
        return plan
    if not execute:
        return {
            **plan,
            "executed": False,
            "note": "Pass --execute to run safe mapped steps.",
        }

    results = []
    for step in plan["steps"]:
        command = step.get("safe_command")
        if not command:
            results.append(
                {
                    "step": step["step"],
                    "status": "skipped",
                    "reason": "no safe command mapping",
                }
            )
            continue
        completed = run_policy_command(command, project_root, timeout=timeout)
        results.append(
            {
                "step": step["step"],
                "command": command,
                "returncode": completed.returncode,
                "status": "ok" if completed.returncode == 0 else "failed",
                "output_tail": completed.combined_output[-1200:],
            }
        )
        if completed.returncode != 0:
            break
    path = state_dir(project_root) / "logs" / "workflow-last.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {**plan, "executed": True, "results": results, "log": str(path)}
