from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .loader import registry_items


def _command_name(command: str) -> str | None:
    if not command or command in {"included", "restart"}:
        return None
    first = command.split()[0].strip('"')
    if first.lower() in {"included", "restart"}:
        return None
    if first in {"python", "python3", "cmd", "powershell", "pwsh"}:
        return first
    return first


def discover_tools(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    tools = registry_items("tools", root)
    checked = []
    for tool in tools:
        command = _command_name(str(tool.get("run_command") or ""))
        health = tool.get("health_check") or {}
        health_command = _command_name(str(health.get("command") or ""))
        candidates = [item for item in [command, health_command] if item]
        found = any(Path(item).exists() or shutil.which(item) for item in candidates)
        env_ready = all(os.environ.get(name) for name in tool.get("env_vars", []))
        checked.append(
            {
                "id": tool["id"],
                "status": tool.get("status"),
                "registered": True,
                "command_candidates": candidates,
                "command_found": found,
                "env_ready": env_ready,
                "enabled_by_default": bool(tool.get("enabled_by_default")),
            }
        )
    return {
        "project": str(root),
        "registered_ids": [tool["id"] for tool in tools],
        "tools_checked": checked,
        "notes": "Discovery inspects local commands and environment variables only; it does not install or call cloud APIs.",
    }
