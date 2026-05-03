from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .loader import load_named_registry, registry_file, registry_items


def add_tool_entry(project_root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    path = registry_file("tools", project_root)
    data = load_named_registry("tools", project_root)
    tools = registry_items("tools", project_root)
    if any(tool.get("id") == entry.get("id") for tool in tools):
        return {
            "ok": False,
            "error": f"tool already exists: {entry.get('id')}",
            "path": str(path),
        }
    data.setdefault("tools", []).append(entry)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8"
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
