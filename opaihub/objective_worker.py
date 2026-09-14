"""One process-local assignment, with independent consent and provider routing."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading

from .atomic_io import atomic_write_text
from .boundary_errors import safe_detail


def authorize_request(packet, request_path, response_path):
    from .agent_objectives import ObjectiveStore
    from .state import state_dir
    from .worktree_leases import WorktreeManager

    if not isinstance(packet, dict):
        raise ValueError("Worker request must be an object")
    authority = Path(packet["authority_root"]).resolve()
    objective = ObjectiveStore(authority).snapshot(packet["objective_id"])
    planning = packet["run_id"] == objective["run_id"] + "-plan"
    verifying = packet.get("operation") == "verification"
    if verifying and packet["run_id"] != objective["run_id"] + "-integration":
        raise ValueError("Verification run does not match the objective")
    assignment = (
        None
        if planning
        else next(
            (
                item
                for item in objective["assignments"]
                if item["run_id"] == packet["run_id"]
            ),
            None,
        )
    )
    owned = (
        objective["integration"]
        if verifying
        else objective["planning"]
        if planning
        else assignment
    )
    if (
        not owned
        or not owned["owner"]
        or owned["owner"] != packet.get("owner")
        or type(packet.get("fence")) is not int
        or owned["fence"] != packet["fence"]
        or owned["status"] != "running"
        or objective["status"] in {"stopping", "cancelled", "completed"}
    ):
        raise ValueError("Worker no longer has active objective ownership")
    task_id = objective["task_id"] if planning or verifying else assignment["task_id"]
    if packet["task_id"] != task_id:
        raise ValueError("Worker task does not match its objective assignment")
    directory = state_dir(authority) / "objectives" / "workers" / packet["run_id"]
    if verifying:
        directory = directory.with_name(packet["run_id"] + "-" + str(packet["fence"]))
    if (
        request_path.resolve() != (directory / "request.json").resolve()
        or response_path.resolve() != (directory / "response.json").resolve()
    ):
        raise ValueError(
            "Worker request and response must use the assigned evidence directory"
        )
    worktree = Path(packet["worktree"]).resolve()
    lease = next(
        (
            item
            for item in WorktreeManager(authority).list()
            if item.run_id == packet["run_id"]
            and Path(item.path).resolve() == worktree
            and item.task_id == task_id
            and item.owner == owned["owner"]
            and item.state == "active"
        ),
        None,
    )
    if lease is None or worktree == authority:
        raise ValueError("Worker requires its active isolated worktree lease")
    if (
        not planning
        and not verifying
        and (
            assignment["lease_id"] != lease.lease_id
            or Path(assignment["worktree"]).resolve() != worktree
        )
    ):
        raise ValueError("Worker worktree does not match the canonical assignment")
    if verifying:
        return {**packet, "objective": objective, "mode": "verify"}
    prompt = packet.get("prompt")
    if not isinstance(prompt, str) or not prompt or len(prompt) > 32_000:
        raise ValueError("Worker prompt exceeds bounded context")
    readonly = planning or str(assignment.get("role", "")).casefold() in {
        "planner",
        "explorer",
        "researcher",
        "reviewer",
        "critic",
    }
    from .objective_routing import select_worker_route

    route = select_worker_route(
        authority, objective, assignment or {"role": "planner"}, planning=planning
    )
    grant = (assignment or {}).get("approval_grant") or {}
    if grant.get("run_id") != packet["run_id"]:
        grant = {}
    return {
        **packet,
        "mode": "plan" if readonly else objective["mode"],
        "model_id": route["model_id"],
        "routing": route,
        "allow_command": grant.get("command")
        if grant.get("kind") == "command"
        else None,
        "allow_edits_once": not readonly and grant.get("kind") == "edits",
        "allow_cloud": objective["allow_cloud"] is True,
        "bypass_permissions": not readonly
        and objective.get("bypass_permissions") is True,
    }


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 2
    request_path, response_path = map(Path, args)
    if request_path.stat().st_size > 512_000:
        raise ValueError("Worker request exceeds bounded context")
    packet = json.loads(request_path.read_text(encoding="utf-8"))
    packet = authorize_request(packet, request_path, response_path)
    if packet.get("operation") == "verification":
        from .objective_execution import execute_objective_verification

        result = execute_objective_verification(
            Path(packet["authority_root"]),
            Path(packet["worktree"]),
            packet["objective"],
            packet["run_id"],
        )
        atomic_write_text(response_path, json.dumps(result, default=str))
        return 0
    from .execution_scope import assignment_scope, managed_budget_gate

    route = packet["routing"]
    blocked_reason = ""
    if not route["allowed"]:
        blocked_reason = (
            route["reason"]
            + ": "
            + "; ".join(
                reason for row in route["blockers"] for reason in row["reasons"]
            )[:2000]
        )
    else:
        # Establish a retryable denial before entering the pipeline. The pipeline
        # repeats the gate at dispatch; any later denial remains non-retryable.
        with assignment_scope(
            Path(packet["worktree"]),
            Path(packet["authority_root"]),
            task_id=packet["task_id"],
            run_id=packet["run_id"],
        ):
            budget = managed_budget_gate(
                Path(packet["worktree"]),
                next_cost_usd=None
                if packet["model_id"].startswith(("account:", "paid:"))
                else "0",
            )
        if budget["denied"]:
            blocked_reason = "; ".join(budget["reasons"])
    if blocked_reason:
        atomic_write_text(
            response_path,
            json.dumps(
                {
                    "status": "blocked",
                    "dispatch_state": "not-dispatched",
                    "error": blocked_reason,
                    "routing": route,
                    "objective_cost_events": [
                        {
                            "operation_key": packet["run_id"] + "-provider",
                            "amount_usd": "0",
                            "measurement_kind": "actual",
                        }
                    ],
                    "answer": blocked_reason,
                }
            ),
        )
        return 0
    from .execution_scope import assignment_cost_events
    from .gui_pipeline import handle_gui_message

    cancel = threading.Event()

    def parent_closed():
        if sys.stdin is None:
            return
        try:
            descriptor = sys.stdin.fileno()
            while os.read(descriptor, 4096):
                pass
        except (OSError, ValueError):
            pass
        cancel.set()

    threading.Thread(
        target=parent_closed, name="objective-parent-watch", daemon=True
    ).start()

    def activity(event):
        if isinstance(event, dict):
            fields = {
                key: str(event[key])[:1000]
                for key in ("phase", "status", "title", "detail")
                if key in event
            }
            metadata = event.get("metadata") or {}
            for key in ("model", "provider"):
                if isinstance(metadata, dict) and metadata.get(key):
                    fields[key] = str(metadata[key])[:200]
            atomic_write_text(
                response_path.parent / "activity.json", json.dumps(fields)
            )

    try:
        result = handle_gui_message(
            Path(packet["worktree"]),
            packet["prompt"],
            surface="agent",
            model_id=packet.get("model_id"),
            local_model_endpoint=packet["routing"].get("endpoint"),
            mode=packet["mode"],
            allow_cloud=packet.get("allow_cloud", False),
            allow_command=packet.get("allow_command"),
            allow_edits_once=packet.get("allow_edits_once", False),
            objective_bypass_permissions=packet.get("bypass_permissions", False),
            task_id=packet["task_id"],
            run_id=packet["run_id"],
            authority_root=Path(packet["authority_root"]),
            cancel=cancel,
            on_event=activity,
        )
        # Persist only attributable output, not the parent transcript or raw provider metadata.
        payload = {
            "status": result.get("status", "failed"),
            "answer": str(result.get("answer", ""))[:100_000],
            "error": result.get("error"),
            "completion_state": result.get("completion_state"),
            "stopped_reason": result.get("stopped_reason"),
            "completion_verdict": result.get("completion_verdict"),
            "command_approval": result.get("command_approval"),
            "edit_approval": result.get("edit_approval"),
            "routing": packet["routing"],
            "objective_cost_events": result.get("objective_cost_events")
            or assignment_cost_events(Path(packet["authority_root"]), packet["run_id"]),
        }
    except Exception as exc:  # noqa: BLE001 - parent must retain failed attempt evidence
        payload = {
            "status": "failed",
            "error": safe_detail(exc, limit=500),
            "objective_cost_events": assignment_cost_events(
                Path(packet["authority_root"]), packet["run_id"]
            ),
        }
    atomic_write_text(response_path, json.dumps(payload, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
