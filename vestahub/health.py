from __future__ import annotations

import os
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_io import read_utf8_tail_json_objects
from .command_runner import run_policy_command, split_command
from .loader import registry_items
from .state import state_dir


def _substitute(command: str, project_root: Path) -> str:
    return command.replace("${PROJECT_ROOT}", str(project_root))


def run_health_check(
    tool: dict[str, Any], project_root: Path, timeout: int = 30
) -> dict[str, Any]:
    health = tool.get("health_check") or {}
    command = health.get("command") or tool.get("health_check")
    if not command:
        return {
            "id": tool.get("id"),
            "status": "unknown",
            "reason": "no health_check command",
        }

    env_missing = [
        name for name in tool.get("env_vars", []) if not os.environ.get(name)
    ]
    if tool.get("requires_api_key") and env_missing:
        return {
            "id": tool.get("id"),
            "status": "disabled",
            "reason": f"missing env vars: {', '.join(env_missing)}",
        }

    substituted = _substitute(str(command), project_root)
    argv = split_command(substituted)
    first = argv[0] if argv else ""
    if not Path(first).exists() and shutil.which(first) is None:
        return {
            "id": tool.get("id"),
            "status": "missing",
            "reason": f"command not found: {first}",
        }

    result = run_policy_command(substituted, project_root, timeout=timeout)
    if result.policy["decision"] == "deny":
        return {
            "id": tool.get("id"),
            "status": "denied",
            "reason": result.policy["reason"],
        }
    if result.policy["decision"] == "confirm" and not result.executed:
        return {
            "id": tool.get("id"),
            "status": "confirmation_required",
            "reason": result.policy["reason"],
        }
    return {
        "id": tool.get("id"),
        "status": "ok" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "output_tail": result.combined_output[-1200:],
    }


def health_all(project_root: Path, category: str | None = None) -> list[dict[str, Any]]:
    tools = registry_items("tools", project_root)
    if category:
        tools = [tool for tool in tools if tool.get("category") == category]
    results = [
        run_health_check(tool, project_root)
        for tool in tools
        if tool.get("health_check")
    ]
    log_health(project_root, results)
    return results


def log_health(project_root: Path, results: list[dict[str, Any]]) -> Path:
    path = state_dir(project_root) / "health" / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "results": results,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return path


def health_history(project_root: Path, limit: int = 10) -> list[dict[str, Any]]:
    path = state_dir(project_root) / "health" / "history.jsonl"
    if not path.exists():
        return []
    return read_utf8_tail_json_objects(path, limit)
