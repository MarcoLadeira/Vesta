from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .state import now_iso, state_dir


def runs_dir(project_root: Path) -> Path:
    return state_dir(project_root.expanduser().resolve()) / "runs"


def _task_hash(task: str) -> str:
    return hashlib.sha256(task.encode("utf-8", errors="replace")).hexdigest()


def _run_path(project_root: Path, run_id: str) -> Path:
    return runs_dir(project_root) / f"{run_id}.json"


def start_run(
    project_root: Path,
    task: str,
    *,
    mode: str,
    model_id: str,
    decision: dict[str, Any],
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    data: dict[str, Any] = {
        "schema_version": 1,
        "id": run_id,
        "project_root": str(root),
        "task_hash": _task_hash(task),
        "status": "running",
        "current_step": "started",
        "mode": mode,
        "model_id": model_id,
        "checkpoint_id": checkpoint_id,
        "started_at": now_iso(),
        "finished_at": None,
        "changed_files": [],
        "decision": decision,
        "receipt_id": None,
        "next_action": None,
        "privacy": "Raw prompts and secrets are not stored in run state.",
    }
    path = _run_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def update_run(project_root: Path, run_id: str, **updates: Any) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    path = _run_path(root, run_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"id": run_id, "project_root": str(root)}
    if not isinstance(data, dict):
        data = {"id": run_id, "project_root": str(root)}
    data.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def finish_run(
    project_root: Path,
    run_id: str,
    *,
    status: str,
    changed_files: list[str] | None = None,
    receipt_id: str | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    return update_run(
        project_root,
        run_id,
        status=status,
        finished_at=now_iso(),
        current_step=status,
        changed_files=changed_files or [],
        receipt_id=receipt_id,
        next_action=next_action,
    )


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_runs(project_root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    root = project_root.expanduser().resolve()
    base = runs_dir(root)
    if not base.exists():
        return []
    items = []
    for path in sorted(base.glob("run-*.json"), reverse=True):
        data = _read(path)
        if data:
            items.append(data)
        if len(items) >= limit:
            break
    return items


def latest_run(project_root: Path) -> dict[str, Any] | None:
    items = list_runs(project_root, limit=1)
    return items[0] if items else None


def show_run(project_root: Path, run_id: str) -> dict[str, Any] | None:
    return _read(_run_path(project_root, run_id))
