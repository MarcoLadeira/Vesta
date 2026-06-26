from __future__ import annotations

import hashlib
import json
import subprocess  # nosec B404 - read-only git argv calls, no shell
import uuid
from pathlib import Path
from typing import Any

from .budget import budget_status
from .policy import resolve_policy
from .state import now_iso, state_dir


def checkpoints_dir(project_root: Path) -> Path:
    return state_dir(project_root.expanduser().resolve()) / "checkpoints"


def _task_hash(task: str) -> str:
    return hashlib.sha256(task.encode("utf-8", errors="replace")).hexdigest()


def _git(root: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(  # nosec B603 - argv list, no shell
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip()


def create_checkpoint(
    project_root: Path,
    task: str,
    *,
    mode: str,
    model_id: str,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write privacy-safe pre-run metadata before an edit-capable run."""
    root = project_root.expanduser().resolve()
    checkpoint_id = f"chk-{uuid.uuid4().hex[:12]}"
    budget = budget_status(root)
    policy = resolve_policy(root)
    status_short = _git(root, ["status", "--short"])
    data: dict[str, Any] = {
        "schema_version": 1,
        "id": checkpoint_id,
        "created_at": now_iso(),
        "project_root": str(root),
        "task_hash": _task_hash(task),
        "mode": mode,
        "model_id": model_id,
        "git": {
            "branch": _git(root, ["rev-parse", "--abbrev-ref", "HEAD"]),
            "head": _git(root, ["rev-parse", "HEAD"]),
            "status_short": status_short.splitlines(),
        },
        "changed_files_before": [
            line[3:] if len(line) > 3 else line for line in status_short.splitlines()
        ],
        "policy_profile": policy.get("profile"),
        "budget": {
            "panic": bool(budget.get("panic")),
            "spent_today_usd": budget.get("spent", {}).get("today_usd", 0.0),
        },
        "decision": decision or {},
        "privacy": "Raw prompts and secrets are not stored in checkpoints.",
    }
    path = checkpoints_dir(root) / f"{checkpoint_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_checkpoints(project_root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    root = project_root.expanduser().resolve()
    base = checkpoints_dir(root)
    if not base.exists():
        return []
    items = []
    for path in sorted(base.glob("chk-*.json"), reverse=True):
        data = _read(path)
        if data:
            items.append(data)
        if len(items) >= limit:
            break
    return items


def latest_checkpoint(project_root: Path) -> dict[str, Any] | None:
    items = list_checkpoints(project_root, limit=1)
    return items[0] if items else None


def show_checkpoint(project_root: Path, checkpoint_id: str) -> dict[str, Any] | None:
    path = checkpoints_dir(project_root) / f"{checkpoint_id}.json"
    return _read(path)
