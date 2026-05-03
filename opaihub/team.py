from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import state_dir


def _team_path(project_root: Path) -> Path:
    return state_dir(project_root) / "team.json"


def init_team(project_root: Path, mode: str = "solo") -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    data = {
        "schema_version": 1,
        "mode": mode,
        "members": [],
        "cloud_sync_enabled": False,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "notes": "Team and cloud sync are explicit opt-in features. They are disabled by default.",
    }
    path = _team_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data | {"path": str(path)}


def cloud_status(project_root: Path) -> dict[str, Any]:
    path = _team_path(project_root.expanduser().resolve())
    if not path.exists():
        return {
            "enabled": False,
            "reason": "No team configuration exists.",
            "requires_confirmation": True,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "enabled": False,
            "reason": "Team configuration is unreadable.",
            "requires_confirmation": True,
        }
    enabled = bool(data.get("cloud_sync_enabled"))
    return {
        "enabled": enabled,
        "mode": data.get("mode", "solo"),
        "requires_confirmation": not enabled,
        "reason": "Cloud sync is opt-in and disabled by default."
        if not enabled
        else "Cloud sync is enabled.",
    }
