from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any
import uuid

from .atomic_io import atomic_write_text, interprocess_transaction
from .command_runner import run_policy_command
from .loader import registry_items
from .state import state_dir


SAFE_STEP_COMMANDS = {
    "opcoding scan": ["python", "-m", "opcoding", "scan", "${PROJECT_ROOT}", "--json"],
    "opcoding init": ["python", "-m", "opcoding", "init", "${PROJECT_ROOT}"],
    "opcoding context": [
        "python",
        "-m",
        "opcoding",
        "context",
        "${PROJECT_ROOT}",
        "--refresh",
    ],
    "op-hub doctor": [
        "python",
        "-m",
        "opaihub",
        "--project",
        "${PROJECT_ROOT}",
        "doctor",
    ],
    "opcoding test": [
        "python",
        "-m",
        "opcoding",
        "test",
        "${PROJECT_ROOT}",
        "--dry-run",
    ],
    "opcoding review": ["python", "-m", "opcoding", "review", "${PROJECT_ROOT}"],
    "opcoding git": ["python", "-m", "opcoding", "git", "${PROJECT_ROOT}", "summary"],
}

_WORKFLOW_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_WORKFLOW_LOG_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _valid_run_id(value: str) -> str:
    run_id = str(value)
    if not _WORKFLOW_RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid workflow run id")
    return run_id


def workflow_last_path(project_root: Path) -> Path:
    return state_dir(project_root) / "logs" / "workflow-last.json"


def workflow_log_path(project_root: Path, run_id: str) -> Path:
    return (
        state_dir(project_root) / "logs" / "workflows" / f"{_valid_run_id(run_id)}.json"
    )


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "invalid_json"
    if not isinstance(value, dict):
        return None, "invalid_shape"
    return value, None


def _read_workflow_pointer(
    project_root: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    pointer, reason = _read_json_object(workflow_last_path(project_root))
    if pointer is None:
        return None, reason
    try:
        run_id = _valid_run_id(str(pointer["run_id"]))
        revision = int(pointer["revision"])
    except (KeyError, TypeError, ValueError):
        return None, "invalid_identity"
    if revision < 1:
        return None, "invalid_identity"
    return {**pointer, "run_id": run_id, "revision": revision}, None


def _write_workflow_snapshot(project_root: Path, snapshot: dict[str, Any]) -> Path:
    """Atomically publish one complete, immutable-by-identity run snapshot."""

    run_id = _valid_run_id(str(snapshot["run_id"]))
    path = workflow_log_path(project_root, run_id)
    atomic_write_text(path, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    return path


def _write_workflow_pointer(project_root: Path, snapshot: dict[str, Any]) -> Path:
    """Atomically publish the identity of the current workflow run only."""

    path = workflow_last_path(project_root)
    pointer = {
        "schema_version": _WORKFLOW_LOG_SCHEMA_VERSION,
        "run_id": _valid_run_id(str(snapshot["run_id"])),
        "revision": int(snapshot["revision"]),
        "workflow_id": str(snapshot["workflow_id"]),
        "started_at": str(snapshot["started_at"]),
        "updated_at": str(snapshot["updated_at"]),
    }
    atomic_write_text(path, json.dumps(pointer, indent=2, sort_keys=True) + "\n")
    return path


def _reserve_workflow_run(
    project_root: Path, workflow_id: str, run_id: str
) -> dict[str, Any]:
    """Allocate an ordered run identity before executing any workflow step."""

    pointer_path = workflow_last_path(project_root)
    with interprocess_transaction(pointer_path):
        pointer, reason = _read_workflow_pointer(project_root)
        if pointer is None and reason not in {"missing", None}:
            raise RuntimeError(f"workflow evidence is degraded: {reason}")
        path = workflow_log_path(project_root, run_id)
        if path.exists():
            raise ValueError("Workflow run id already has durable evidence")
        now = _now_iso()
        snapshot = {
            "schema_version": _WORKFLOW_LOG_SCHEMA_VERSION,
            "run_id": run_id,
            "revision": (int(pointer["revision"]) + 1) if pointer else 1,
            "workflow_id": workflow_id,
            "started_at": now,
            "updated_at": now,
            "state": "running",
            "results": [],
        }
        _write_workflow_snapshot(project_root, snapshot)
        _write_workflow_pointer(project_root, snapshot)
    return snapshot


def _complete_workflow_run(
    project_root: Path, snapshot: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any]:
    """Commit one run's final snapshot without letting stale runs reclaim current."""

    final = {
        **snapshot,
        "updated_at": _now_iso(),
        "state": "failed"
        if any(item.get("status") == "failed" for item in results)
        else "completed",
        "results": results,
    }
    pointer_path = workflow_last_path(project_root)
    with interprocess_transaction(pointer_path):
        _write_workflow_snapshot(project_root, final)
        pointer, reason = _read_workflow_pointer(project_root)
        if pointer is None:
            raise RuntimeError(f"workflow evidence is degraded: {reason}")
        current = int(pointer["revision"]) <= int(final["revision"])
        if current:
            _write_workflow_pointer(project_root, final)
            current_run_id = str(final["run_id"])
            current_revision = int(final["revision"])
        else:
            current_run_id = str(pointer["run_id"])
            current_revision = int(pointer["revision"])
    return {
        "state": "current" if current else "previous",
        "current_run_id": current_run_id,
        "current_revision": current_revision,
        "run": final,
    }


def read_workflow_log(project_root: Path, run_id: str | None = None) -> dict[str, Any]:
    """Read one coherent run snapshot without substituting another run's evidence."""

    root = project_root.expanduser().resolve()
    requested_run_id = _valid_run_id(run_id) if run_id is not None else None
    with interprocess_transaction(workflow_last_path(root)):
        pointer, reason = _read_workflow_pointer(root)
        if pointer is None:
            if reason == "missing":
                return {"state": "missing", "reason": "no workflow evidence"}
            return {"state": "degraded", "reason": f"workflow pointer {reason}"}
        target_run_id = requested_run_id or str(pointer["run_id"])
        snapshot, snapshot_reason = _read_json_object(
            workflow_log_path(root, target_run_id)
        )
        if snapshot is None:
            return {
                "state": "degraded",
                "reason": f"workflow snapshot {snapshot_reason}",
                "current_run_id": pointer["run_id"],
                "current_revision": pointer["revision"],
            }
        try:
            snapshot_run_id = _valid_run_id(str(snapshot["run_id"]))
            snapshot_revision = int(snapshot["revision"])
        except (KeyError, TypeError, ValueError):
            return {
                "state": "degraded",
                "reason": "workflow snapshot invalid_identity",
                "current_run_id": pointer["run_id"],
                "current_revision": pointer["revision"],
            }
        if snapshot_run_id != target_run_id or (
            target_run_id == pointer["run_id"]
            and snapshot_revision != pointer["revision"]
        ):
            return {
                "state": "degraded",
                "reason": "workflow snapshot identity mismatch",
                "current_run_id": pointer["run_id"],
                "current_revision": pointer["revision"],
            }
        return {
            "state": "current" if target_run_id == pointer["run_id"] else "previous",
            "current_run_id": pointer["run_id"],
            "current_revision": pointer["revision"],
            "run": snapshot,
        }


def find_workflow(project_root: Path, workflow_id: str) -> dict[str, Any] | None:
    for workflow in registry_items("workflows", project_root):
        if workflow.get("id") == workflow_id:
            return workflow
    return None


def _render_command(parts: list[str], project_root: Path) -> list[str]:
    return [
        part.replace("${PROJECT_ROOT}", str(project_root.resolve())) for part in parts
    ]


def workflow_plan(project_root: Path, workflow_id: str) -> dict[str, Any]:
    workflow = find_workflow(project_root, workflow_id)
    if not workflow:
        return {"ok": False, "error": f"workflow not found: {workflow_id}"}
    steps = []
    for step in workflow.get("steps", []):
        command = SAFE_STEP_COMMANDS.get(step)
        steps.append(
            {
                "step": step,
                "safe_command": _render_command(command, project_root)
                if command
                else None,
                "executable": command is not None,
            }
        )
    return {"ok": True, "workflow": workflow, "steps": steps}


def run_workflow(
    project_root: Path,
    workflow_id: str,
    execute: bool = False,
    timeout: int = 120,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    plan = workflow_plan(project_root, workflow_id)
    if not plan.get("ok"):
        return plan
    if not execute:
        return {
            **plan,
            "executed": False,
            "note": "Pass --execute to run safe mapped steps.",
        }

    root = project_root.expanduser().resolve()
    durable_run_id = _valid_run_id(run_id or uuid.uuid4().hex)
    snapshot = _reserve_workflow_run(root, workflow_id, durable_run_id)
    results = []
    for step in plan["steps"]:
        command = step.get("safe_command")
        if not command:
            results.append(
                {
                    "step": step["step"],
                    "status": "skipped",
                    "reason": "no safe command mapping",
                }
            )
            continue
        completed = run_policy_command(command, project_root, timeout=timeout)
        results.append(
            {
                "step": step["step"],
                "command": command,
                "returncode": completed.returncode,
                "status": "ok" if completed.returncode == 0 else "failed",
                "output_tail": completed.combined_output[-1200:],
            }
        )
        if completed.returncode != 0:
            break
    evidence = _complete_workflow_run(root, snapshot, results)
    return {
        **plan,
        "executed": True,
        "results": results,
        "run_id": durable_run_id,
        "revision": snapshot["revision"],
        "log": str(workflow_log_path(root, durable_run_id)),
        "evidence": evidence,
    }
