from __future__ import annotations

from pathlib import Path
from typing import Any

from .loader import load_named_registry, registry_items


REQUIRED = {
    "tools": [
        "id",
        "name",
        "category",
        "description",
        "status",
        "type",
        "cost_level",
        "permission_level",
        "local_first",
        "open_source",
        "requires_api_key",
        "env_vars",
        "install_command",
        "run_command",
        "health_check",
        "inputs",
        "outputs",
        "tags",
        "docs_url",
        "notes",
        "enabled_by_default",
    ],
    "agents": [
        "id",
        "name",
        "purpose",
        "inputs",
        "outputs",
        "tools_allowed",
        "tools_forbidden",
        "cost_policy",
        "permission_policy",
        "escalation_policy",
        "default_model_tier",
        "max_context_policy",
        "run_triggers",
        "stop_conditions",
    ],
    "workflows": [
        "id",
        "trigger",
        "inputs",
        "steps",
        "agents_used",
        "tools_used",
        "cost_policy",
        "permission_policy",
        "output_artifacts",
        "failure_handling",
    ],
    "mcp_servers": [
        "id",
        "name",
        "description",
        "transport",
        "command",
        "args",
        "env",
        "enabled",
        "permission_level",
        "cost_level",
        "allowed_paths",
        "forbidden_paths",
        "requires_confirmation",
        "notes",
    ],
    "models": [
        "id",
        "name",
        "tier",
        "provider",
        "cost_level",
        "requires_api_key",
        "enabled_by_default",
        "use_for",
        "confirmation_required",
    ],
}


def validate_registry(name: str, project_root: Path) -> dict[str, Any]:
    data = load_named_registry(name, project_root)
    items = registry_items(name, project_root)
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        item_id = item.get("id")
        if not item_id:
            issues.append(
                {"index": index, "severity": "error", "message": "missing id"}
            )
        elif item_id in seen:
            issues.append(
                {"id": item_id, "severity": "error", "message": "duplicate id"}
            )
        else:
            seen.add(item_id)
        for field in REQUIRED.get(name, []):
            if field not in item:
                issues.append(
                    {
                        "id": item_id,
                        "severity": "error",
                        "message": f"missing field: {field}",
                    }
                )
    return {
        "registry": name,
        "schema_version": data.get("schema_version")
        if isinstance(data, dict)
        else None,
        "count": len(items),
        "issues": issues,
        "ok": not any(issue["severity"] == "error" for issue in issues),
    }


def validate_all(project_root: Path) -> dict[str, Any]:
    names = ["tools", "agents", "workflows", "mcp_servers", "models"]
    results = [validate_registry(name, project_root) for name in names]
    return {
        "ok": all(result["ok"] for result in results),
        "registries": results,
    }
