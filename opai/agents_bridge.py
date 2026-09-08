"""Shared desktop/CLI boundary for canonical engineering objectives."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

from opaihub.agent_objectives import ObjectiveStore
from opaihub.objective_execution import ObjectiveExecutor
from opaihub.gui_preferences import MODES


def create_objective_payload(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Expected an objective request")
    text = payload.get("text")
    mode = payload.get("mode", "safe-auto")
    model = payload.get("model", "auto")
    limit = payload.get("maxParallel", 2)
    if not isinstance(text, str) or not text.strip() or len(text) > 16000:
        raise ValueError("An objective must contain between 1 and 16000 characters")
    if mode not in MODES or not isinstance(model, str) or not model or len(model) > 200:
        raise ValueError("Choose a supported mode and model")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 4:
        raise ValueError("Concurrency must be between 1 and 4")
    request = str(payload.get("requestId") or uuid.uuid4().hex)
    identity = uuid.uuid5(uuid.NAMESPACE_URL, f"{root.resolve()}:{request}").hex
    return ObjectiveStore(root).create(
        text.strip(),
        [],
        task_id=f"objective-{identity}",
        run_id=f"objective-run-{identity}",
        mode=mode,
        model=model,
        max_parallel=limit,
        budget_usd=payload.get("budgetUsd"),
        shared_context="",
        allow_cloud=payload.get("allowCloud") is True,
    )


def control_objective_payload(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("objective_id"), str
    ):
        raise ValueError("A canonical objective ID is required")
    action = payload.get("action")
    actions = {
        "run": "run",
        "cancel": "stop",
        "stop": "stop",
        "pause": "pause",
        "resume": "resume",
        "sequential": "sequential",
        "set_budget": "budget",
        "budget": "budget",
        "prioritize": "prioritize",
        "reroute": "reroute",
        "reconcile": "reconcile",
        "verify": "verify",
    }
    if action not in actions:
        raise ValueError("Unsupported objective control")
    value = payload.get("value")
    for field in {
        "set_budget": ["budget_usd"],
        "prioritize": ["priority"],
        "reroute": ["model"],
    }.get(action, []):
        value = payload.get(field, value)
    if action == "reroute" and isinstance(value, str):
        value = {"model": value}
    result = ObjectiveExecutor(root).control(
        payload["objective_id"],
        actions[action],
        assignment_id=payload.get("assignment_id"),
        value=value,
    )
    return {"ok": True, "objective": result, "workspaceRoot": str(root.resolve())}


def objectives_payload(root: Path) -> dict[str, Any]:
    return {
        "objectives": ObjectiveStore(root).list_objectives(),
        "workspaceRoot": str(root.resolve()),
    }


def objective_worktree_path(root: Path, payload: dict[str, Any]) -> Path:
    from opaihub.worktree_leases import WorktreeManager

    if not isinstance(payload, dict) or not isinstance(
        payload.get("objective_id"), str
    ):
        raise ValueError("A canonical objective ID is required")
    objective = ObjectiveStore(root).snapshot(payload["objective_id"])
    if payload.get("assignment_id"):
        item = next(
            (
                row
                for row in objective["assignments"]
                if row["assignment_id"] == payload["assignment_id"]
            ),
            None,
        )
        if item is None:
            raise ValueError("Assignment does not belong to this objective")
        task_id, run_id = item["task_id"], item["run_id"]
    else:
        item = objective["integration"]
        task_id, run_id = objective["task_id"], objective["run_id"] + "-integration"
    if not item.get("worktree"):
        raise ValueError("No worktree has been recorded yet")
    target = Path(item["worktree"]).resolve()
    lease = next(
        (
            row
            for row in WorktreeManager(root).list()
            if row.task_id == task_id
            and row.run_id == run_id
            and Path(row.path).resolve() == target
            and row.branch == item["branch"]
        ),
        None,
    )
    if lease is None or not target.is_dir():
        raise ValueError("The recorded worktree is no longer available")
    info = target.stat()
    if lease.filesystem_id and tuple(lease.filesystem_id) != (info.st_dev, info.st_ino):
        raise ValueError("The recorded worktree was replaced")
    return target
