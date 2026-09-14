from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .loader import registry_items


PROJECT_STATE_SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def state_dir(project_root: Path) -> Path:
    root = project_root.expanduser().resolve()
    path = root / ".opaihub"
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise OSError(f"unsafe Vesta state directory link: {path}")
    try:
        path.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise OSError(f"unsafe Vesta state directory outside project: {path}") from exc
    return path


def state_path(project_root: Path) -> Path:
    return state_dir(project_root) / "project.json"


def _state_backup_path(project_root: Path) -> Path:
    return state_dir(project_root) / "project.json.bak"


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + os.replace).

    A reader during the write always sees either the complete old file or the
    complete new one — never a torn, unparseable project state (#473).
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_state_dict(path: Path) -> dict[str, Any] | None:
    """Parsed project state, or ``None`` when the file is absent or unreadable."""

    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def default_state(project_root: Path) -> dict[str, Any]:
    return {
        "schema_version": PROJECT_STATE_SCHEMA_VERSION,
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
    data = _read_state_dict(path)
    if data is None and path.exists():
        # Primary is present but unreadable (e.g. an interrupted legacy write):
        # recover the last known-good backup before reverting to defaults, so a
        # torn file doesn't silently discard the project's saved configuration.
        data = _read_state_dict(_state_backup_path(project_root))
    base = default_state(project_root)  # always carries the current schema_version
    if isinstance(data, dict):
        base.update(data)
    return base


def save_state(project_root: Path, state: dict[str, Any]) -> Path:
    path = state_path(project_root)
    state["updated_at"] = now_iso()
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    # Atomic write + last-known-good backup: an interrupted write can't leave a
    # torn project.json, and a corrupted file is recoverable (#473).
    _atomic_write(path, payload)
    _atomic_write(_state_backup_path(project_root), payload)
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
