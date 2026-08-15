from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from . import shadow_journal
from .atomic_io import atomic_write_text, interprocess_transaction
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


def _valid_schedule_record(record: Mapping[str, Any]) -> bool:
    """A mirrored record must carry the schedule list a reader needs."""

    return isinstance(record.get("schedules"), list)


def _write(project_root: Path, schedules: list[dict[str, Any]]) -> Path:
    path = _path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # This wrote with a bare `path.write_text` -- not atomic, and with no
    # interprocess lock. A crash or a concurrent writer mid-write leaves a
    # truncated schedules.json, and `_read` turns any JSONDecodeError into an
    # empty list: every schedule silently gone, reported as "you have none".
    # That is precisely the "corruption becomes empty/default permissive
    # state" #613 forbids, so the write is now atomic and serialized like
    # every other migrated module's.
    #
    # This file is one document rather than one file per record, so the
    # "record" being journalled is the document itself, carried under a single
    # key because the helper mirrors mappings.
    with interprocess_transaction(path):
        atomic_write_text(path, json.dumps(schedules, indent=2, sort_keys=True) + "\n")
        shadow_journal.record_snapshot(
            path, {"schedules": schedules}, is_valid_record=_valid_schedule_record
        )
    return path


def shadow_journal_projection(project_root: Path) -> dict[str, Any]:
    """The schedule document the shadow journal alone would reconstruct."""

    return shadow_journal.projection(
        _path(project_root.expanduser().resolve()),
        is_valid_record=_valid_schedule_record,
    )


def schedule_contradiction_report(project_root: Path) -> dict[str, Any] | None:
    """``None`` when the file and its shadow agree; otherwise what differs.

    The dual-read #613 asks for. ``_read`` is deliberately not reused: it
    turns unreadable content into an empty list, which is the exact behaviour
    that would hide a divergence rather than report it.
    """

    path = _path(project_root.expanduser().resolve())

    def read_legacy() -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {"schedules": data} if isinstance(data, list) else {}

    return shadow_journal.contradiction_report(
        path, read_legacy, is_valid_record=_valid_schedule_record
    )


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
