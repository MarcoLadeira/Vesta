from __future__ import annotations

from pathlib import Path
from typing import Any

from .loader import hub_root, load_registry


def skill_registry(project_root: Path | None = None) -> dict[str, Any]:
    path = hub_root(project_root) / "skills" / "registry.yaml"
    if not path.exists():
        return {"schema_version": None, "skills": []}
    data = load_registry(path)
    return data if isinstance(data, dict) else {"schema_version": None, "skills": []}


def skill_items(project_root: Path | None = None) -> list[dict[str, Any]]:
    data = skill_registry(project_root)
    skills = data.get("skills", [])
    return skills if isinstance(skills, list) else []


def skill_status(project_root: Path | None = None) -> dict[str, Any]:
    root = hub_root(project_root)
    skills = skill_items(project_root)
    items = []
    for skill in skills:
        path = root / str(skill.get("path", ""))
        items.append(
            {
                "id": skill.get("id"),
                "name": skill.get("name"),
                "path": str(path),
                "exists": path.exists(),
                "cost_policy": skill.get("cost_policy"),
                "enabled_by_default": skill.get("enabled_by_default", False),
            }
        )
    return {
        "hub": str(root),
        "count": len(skills),
        "missing": [item for item in items if not item["exists"]],
        "skills": items,
        "ok": all(item["exists"] for item in items),
    }
