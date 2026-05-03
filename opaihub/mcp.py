from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .state import effective_mcp_servers, state_dir


def _render_arg(value: str, project_root: Path) -> str:
    return value.replace("${PROJECT_ROOT}", str(project_root.resolve()))


def render_mcp_config(project_root: Path) -> dict[str, Any]:
    servers: dict[str, Any] = {}
    for server in effective_mcp_servers(project_root):
        if not server.get("effective_enabled"):
            continue
        command = server.get("command", "")
        if not command:
            continue
        servers[server["id"]] = {
            "command": command,
            "args": [
                _render_arg(str(arg), project_root) for arg in server.get("args", [])
            ],
            "env": server.get("env", {}),
            "notes": server.get("notes", ""),
        }
    return {
        "mcpServers": servers,
        "policy": "Generated from OP AI Hub effective MCP state. Review before use.",
    }


def write_mcp_config(project_root: Path) -> Path:
    path = state_dir(project_root) / "generated" / "mcp_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(render_mcp_config(project_root), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
