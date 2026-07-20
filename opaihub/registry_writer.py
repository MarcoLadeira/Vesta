from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_text, interprocess_transaction
from .loader import load_named_registry, registry_file


def add_tool_entry(project_root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    path = registry_file("tools", project_root)
    # Serialize the whole read-check-append-write across threads and processes so
    # two concurrent adds can't both read the pre-append registry and clobber
    # each other's tool (#459). Each writer re-reads the current file under the
    # lock, so no addition is lost and the write is atomic.
    with interprocess_transaction(path):
        data = load_named_registry("tools", project_root)
        tools = data.get("tools")
        tools = tools if isinstance(tools, list) else []
        if any(
            isinstance(tool, dict) and tool.get("id") == entry.get("id")
            for tool in tools
        ):
            return {
                "ok": False,
                "error": f"tool already exists: {entry.get('id')}",
                "path": str(path),
            }
        tools.append(entry)
        data["tools"] = tools
        atomic_write_text(
            path, json.dumps(data, indent=2, sort_keys=False) + "\n"
        )
    return {"ok": True, "id": entry.get("id"), "path": str(path)}


def build_tool_entry(args: Any) -> dict[str, Any]:
    env_vars = [item for item in (args.env_vars or "").split(",") if item]
    tags = [item for item in (args.tags or args.category).split(",") if item]
    return {
        "id": args.id,
        "name": args.name or args.id,
        "category": args.category,
        "description": args.description,
        "status": args.status,
        "type": args.type,
        "cost_level": args.cost_level,
        "permission_level": args.permission_level,
        "local_first": args.local_first,
        "open_source": args.open_source,
        "requires_api_key": bool(env_vars)
        if args.requires_api_key is None
        else args.requires_api_key,
        "env_vars": env_vars,
        "install_command": args.install_command or "",
        "run_command": args.run_command or "",
        "health_check": {"command": args.health_command}
        if args.health_command
        else None,
        "inputs": [args.inputs] if args.inputs else [],
        "outputs": [args.outputs] if args.outputs else [],
        "tags": tags,
        "docs_url": args.docs_url or "",
        "notes": args.notes or "",
        "enabled_by_default": args.enabled_by_default,
    }
