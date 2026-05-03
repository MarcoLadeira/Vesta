from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import state_dir
from .workflow_runner import find_workflow


def _path(project_root: Path) -> Path:
    return state_dir(project_root) / "schedules.json"


def _read(project_root: Path) -> list[dict[str, Any]]:
    path = _path(project_root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write(project_root: Path, schedules: list[dict[str, Any]]) -> Path:
    path = _path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(schedules, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def create_schedule(
    project_root: Path, workflow_id: str, cadence: str
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    workflow = find_workflow(root, workflow_id)
    if not workflow:
        return {"status": "error", "message": f"workflow not found: {workflow_id}"}
    schedule_id = f"{workflow_id}:{cadence}"
    schedules = [item for item in _read(root) if item.get("id") != schedule_id]
    schedule = {
        "id": schedule_id,
        "workflow_id": workflow_id,
        "cadence": cadence,
        "enabled": True,
        "runner": "manual-cli",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "notes": "No background daemon is started; run with opai hub workflow run when desired.",
    }
    schedules.append(schedule)
    path = _write(root, schedules)
    return {"status": "created", "path": str(path), "schedule": schedule}


def list_schedules(project_root: Path) -> list[dict[str, Any]]:
    return _read(project_root.expanduser().resolve())
