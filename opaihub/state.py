from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .loader import registry_items


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def state_dir(project_root: Path) -> Path:
    return project_root.resolve() / ".opaihub"


def state_path(project_root: Path) -> Path:
    return state_dir(project_root) / "project.json"


def default_state(project_root: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "project_root": str(project_root.resolve()),
        "profile": {
            "name": project_root.resolve().name,
            "mode": "local-first",
        },
        "enabled_tools": [],
        "disabled_tools": [],
        "enabled_mcp_servers": [],
        "disabled_mcp_servers": [],
        "workflow_overrides": {},
        "budgets": {},
        "notes": [],
    }


def load_state(project_root: Path) -> dict[str, Any]:
    path = state_path(project_root)
    if not path.exists():
        return default_state(project_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state(project_root)
    base = default_state(project_root)
    base.update(data)
    return base


def save_state(project_root: Path, state: dict[str, Any]) -> Path:
    path = state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now_iso()
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def attach_project(project_root: Path, force: bool = False) -> dict[str, Any]:
    path = state_path(project_root)
    if path.exists() and not force:
        state = load_state(project_root)
    else:
        state = default_state(project_root)
        save_state(project_root, state)
    for child in ["cache", "logs", "generated", "health"]:
        (state_dir(project_root) / child).mkdir(parents=True, exist_ok=True)
    return {"state_path": str(path), "state": state}


def _set_list(
    state: dict[str, Any], add_key: str, remove_key: str, item_id: str
) -> None:
    add = list(dict.fromkeys([*state.get(add_key, []), item_id]))
    remove = [item for item in state.get(remove_key, []) if item != item_id]
    state[add_key] = add
    state[remove_key] = remove


def set_tool(project_root: Path, tool_id: str, enabled: bool) -> dict[str, Any]:
    state = load_state(project_root)
    if enabled:
        _set_list(state, "enabled_tools", "disabled_tools", tool_id)
    else:
        _set_list(state, "disabled_tools", "enabled_tools", tool_id)
    save_state(project_root, state)
    return state


def set_mcp(project_root: Path, server_id: str, enabled: bool) -> dict[str, Any]:
    state = load_state(project_root)
    if enabled:
        _set_list(state, "enabled_mcp_servers", "disabled_mcp_servers", server_id)
    else:
        _set_list(state, "disabled_mcp_servers", "enabled_mcp_servers", server_id)
    save_state(project_root, state)
    return state


def effective_tools(project_root: Path) -> list[dict[str, Any]]:
    state = load_state(project_root)
    enabled = set(state.get("enabled_tools", []))
    disabled = set(state.get("disabled_tools", []))
    tools = []
    for tool in registry_items("tools", project_root):
        active = bool(tool.get("enabled_by_default", False))
        if tool["id"] in enabled:
            active = True
        if tool["id"] in disabled:
            active = False
        tools.append({**tool, "effective_enabled": active})
    return tools


def effective_mcp_servers(project_root: Path) -> list[dict[str, Any]]:
    state = load_state(project_root)
    enabled = set(state.get("enabled_mcp_servers", []))
    disabled = set(state.get("disabled_mcp_servers", []))
    servers = []
    for server in registry_items("mcp_servers", project_root):
        active = bool(server.get("enabled", False))
        if server["id"] in enabled:
            active = True
        if server["id"] in disabled:
            active = False
        servers.append({**server, "effective_enabled": active})
    return servers
