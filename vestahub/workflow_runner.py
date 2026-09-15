from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any
import uuid

from . import owner_lease, run_journal
from .atomic_io import atomic_write_text, interprocess_transaction
from .command_runner import run_policy_command
from .loader import registry_items
from .run_state import RunState, can_transition, is_terminal, transition
from .state import state_dir
from .boundary_errors import safe_detail


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
        "vestahub",
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
_WORKFLOW_TASK_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_WORKFLOW_LOG_SCHEMA_VERSION = 2

_LEGACY_WORKFLOW_STATES = {
    "running": RunState.RUNNING,
    "completed": RunState.COMPLETED,
    "failed": RunState.FAILED,
}

_STEP_STATE_FOR_LEGACY_STATUS = {
    "ok": RunState.COMPLETED,
    "failed": RunState.FAILED,
    "timeout": RunState.TIMEOUT,
    "skipped": RunState.BLOCKED,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _valid_run_id(value: str) -> str:
    run_id = str(value)
    if not _WORKFLOW_RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid workflow run id")
    return run_id


def _valid_task_id(value: str) -> str:
    task_id = str(value)
    if not _WORKFLOW_TASK_ID.fullmatch(task_id):
        raise ValueError("Invalid workflow task id")
    return task_id


def _reason_code(value: object, *, fallback: str) -> str:
    code = re.sub(r"[^a-z0-9_]+", "_", str(value or "").lower()).strip("_")
    return code[:120] or fallback


def _state_event(state: RunState, *, at: str, reason_code: str) -> dict[str, str]:
    return {"state": state.value, "at": at, "reason_code": reason_code}


def _legacy_run_state(snapshot: dict[str, Any]) -> RunState:
    state = str(snapshot.get("state") or "").strip().lower()
    mapped = _LEGACY_WORKFLOW_STATES.get(state)
    if mapped is None:
        raise ValueError(f"unknown legacy workflow state: {state or 'missing'}")
    return mapped


def _legacy_step_state(result: dict[str, Any]) -> RunState:
    status = str(result.get("status") or "").strip().lower()
    return _STEP_STATE_FOR_LEGACY_STATUS.get(status, RunState.FAILED)


def _canonical_workflow_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Read old #445 snapshots as canonical #379 records without guessing success.

    The first workflow-log schema stored a private ``state`` string and step
    ``status`` strings.  New readers project those known values into the
    canonical record on read; unknown legacy data fails closed instead of being
    displayed as a completed run.
    """

    data = dict(snapshot)
    try:
        source_schema = int(data.get("schema_version") or 1)
    except (TypeError, ValueError):
        raise ValueError("invalid workflow schema version") from None
    if source_schema > _WORKFLOW_LOG_SCHEMA_VERSION:
        raise ValueError("unsupported workflow schema version")

    if source_schema < _WORKFLOW_LOG_SCHEMA_VERSION:
        run_state = _legacy_run_state(data)
        data["schema_version"] = _WORKFLOW_LOG_SCHEMA_VERSION
        data["migrated_from_schema_version"] = source_schema
        data["task_id"] = f"legacy:{_valid_run_id(str(data['run_id']))}"
        data["attempt"] = 1
        data["predecessor_run_id"] = None
        data["run_state"] = run_state.value
        data["reason_code"] = (
            "workflow_completed"
            if run_state is RunState.COMPLETED
            else "workflow_failed"
        )
        data["state_history"] = [
            _state_event(
                run_state,
                at=str(data.get("updated_at") or data.get("started_at") or ""),
                reason_code=data["reason_code"],
            )
        ]
    else:
        try:
            run_state = RunState(str(data["run_state"]).strip().lower())
        except (KeyError, ValueError):
            raise ValueError("invalid canonical workflow run state") from None
        if str(data.get("state") or "").strip().lower() != run_state.value:
            raise ValueError("workflow compatibility state contradicts run state")
        data["task_id"] = _valid_task_id(str(data.get("task_id") or ""))
        try:
            data["attempt"] = max(1, int(data.get("attempt") or 1))
        except (TypeError, ValueError):
            raise ValueError("invalid workflow attempt") from None
        predecessor = data.get("predecessor_run_id")
        data["predecessor_run_id"] = (
            _valid_run_id(str(predecessor)) if predecessor else None
        )
        data["reason_code"] = _reason_code(data.get("reason_code"), fallback="unknown")
        history = data.get("state_history")
        if not isinstance(history, list) or not history:
            raise ValueError("missing workflow state history")
        states: list[RunState] = []
        cleaned_history: list[dict[str, str]] = []
        for item in history:
            if not isinstance(item, dict):
                raise ValueError("invalid workflow state-history entry")
            try:
                item_state = RunState(str(item["state"]).strip().lower())
            except (KeyError, ValueError):
                raise ValueError("invalid workflow state-history state") from None
            if states and not can_transition(states[-1], item_state):
                raise ValueError("illegal workflow state-history transition")
            states.append(item_state)
            cleaned_history.append(
                _state_event(
                    item_state,
                    at=str(item.get("at") or ""),
                    reason_code=_reason_code(
                        item.get("reason_code"), fallback="unknown"
                    ),
                )
            )
        if states[-1] is not run_state:
            raise ValueError("workflow state history does not match current state")
        data["state_history"] = cleaned_history

    results = data.get("results")
    if not isinstance(results, list):
        raise ValueError("invalid workflow results")
    canonical_results: list[dict[str, Any]] = []
    for index, raw_result in enumerate(results):
        if not isinstance(raw_result, dict):
            raise ValueError("invalid workflow result")
        result = dict(raw_result)
        if source_schema < _WORKFLOW_LOG_SCHEMA_VERSION:
            step_state = _legacy_step_state(result)
            result["step_id"] = f"step-{index + 1}"
            result["run_state"] = step_state.value
            result["reason_code"] = (
                "command_completed"
                if step_state is RunState.COMPLETED
                else "no_safe_command_mapping"
                if step_state is RunState.BLOCKED
                else "command_timeout"
                if step_state is RunState.TIMEOUT
                else "command_failed"
            )
        else:
            try:
                step_state = RunState(str(result["run_state"]).strip().lower())
            except (KeyError, ValueError):
                raise ValueError("invalid workflow step run state") from None
            if not is_terminal(step_state):
                raise ValueError("workflow result must have a terminal step state")
            result["reason_code"] = _reason_code(
                result.get("reason_code"), fallback="unknown"
            )
        canonical_results.append(result)
    data["results"] = canonical_results

    steps = data.get("steps")
    if source_schema < _WORKFLOW_LOG_SCHEMA_VERSION:
        data["steps"] = [
            {
                "step_id": result["step_id"],
                "step": str(result.get("step") or result["step_id"]),
                "run_state": result["run_state"],
                "reason_code": result["reason_code"],
                "state_history": [
                    _state_event(
                        RunState(result["run_state"]),
                        at=str(data.get("updated_at") or ""),
                        reason_code=result["reason_code"],
                    )
                ],
            }
            for result in canonical_results
        ]
    elif not isinstance(steps, list):
        raise ValueError("invalid workflow steps")
    else:
        canonical_steps: list[dict[str, Any]] = []
        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                raise ValueError("invalid workflow step")
            step = dict(raw_step)
            step_id = str(step.get("step_id") or "")
            if not step_id:
                raise ValueError("workflow step missing identity")
            try:
                step_state = RunState(str(step["run_state"]).strip().lower())
            except (KeyError, ValueError):
                raise ValueError("invalid workflow step state") from None
            history = step.get("state_history")
            if not isinstance(history, list) or not history:
                raise ValueError("missing workflow step history")
            step_states: list[RunState] = []
            cleaned_history: list[dict[str, str]] = []
            for item in history:
                if not isinstance(item, dict):
                    raise ValueError("invalid workflow step-history entry")
                try:
                    item_state = RunState(str(item["state"]).strip().lower())
                except (KeyError, ValueError):
                    raise ValueError("invalid workflow step-history state") from None
                if step_states and not can_transition(step_states[-1], item_state):
                    raise ValueError("illegal workflow step-history transition")
                step_states.append(item_state)
                cleaned_history.append(
                    _state_event(
                        item_state,
                        at=str(item.get("at") or ""),
                        reason_code=_reason_code(
                            item.get("reason_code"), fallback="unknown"
                        ),
                    )
                )
            if step_states[-1] is not step_state:
                raise ValueError("workflow step history does not match current state")
            if is_terminal(run_state) and not is_terminal(step_state):
                raise ValueError("terminal workflow has a non-terminal step")
            step["step_id"] = step_id
            step["run_state"] = step_state.value
            step["reason_code"] = _reason_code(
                step.get("reason_code"), fallback="unknown"
            )
            step["state_history"] = cleaned_history
            canonical_steps.append(step)
        data["steps"] = canonical_steps

        steps_by_id = {str(step["step_id"]): step for step in canonical_steps}
        if len(steps_by_id) != len(canonical_steps):
            raise ValueError("duplicate workflow step identity")
        for result in canonical_results:
            step_id = str(result.get("step_id") or "")
            step = steps_by_id.get(step_id)
            if step is None:
                raise ValueError("workflow result has no matching step")
            if step["run_state"] != result["run_state"]:
                raise ValueError("workflow result contradicts step state")

    if is_terminal(run_state):
        verdict = data.get("completion_verdict")
        if verdict is None:
            data["completion_verdict"] = {
                "verdict": run_state.value,
                "reason_code": data["reason_code"],
            }
        elif not isinstance(verdict, dict):
            raise ValueError("invalid workflow completion verdict")
        else:
            try:
                verdict_state = RunState(str(verdict["verdict"]).strip().lower())
            except (KeyError, ValueError):
                raise ValueError("invalid workflow completion verdict") from None
            verdict_reason = _reason_code(
                verdict.get("reason_code"), fallback="unknown"
            )
            if verdict_state is not run_state or verdict_reason != data["reason_code"]:
                raise ValueError("completion verdict contradicts run state")
            data["completion_verdict"] = {
                "verdict": verdict_state.value,
                "reason_code": verdict_reason,
            }
    return data


def workflow_last_path(project_root: Path) -> Path:
    return state_dir(project_root) / "logs" / "workflow-last.json"


def workflow_log_path(project_root: Path, run_id: str) -> Path:
    return (
        state_dir(project_root) / "logs" / "workflows" / f"{_valid_run_id(run_id)}.json"
    )


def workflow_journal_path(project_root: Path, run_id: str) -> Path:
    """The append-only journal backing one run's snapshot (#517).

    A sibling of :func:`workflow_log_path`, not a replacement — the snapshot
    file remains the fast-read projection every existing caller already
    depends on. The journal exists so that projection can be *proven*
    correct by replay (:func:`replay_workflow_run`) instead of trusted on
    faith, and so a crash mid-transition leaves durable, monotonically
    ordered evidence independent of whatever the snapshot rewrite managed to
    get to disk.
    """
    return (
        state_dir(project_root)
        / "logs"
        / "workflows"
        / f"{_valid_run_id(run_id)}.journal.jsonl"
    )


def workflow_lease_path(project_root: Path, run_id: str) -> Path:
    """The durable, fenced supervisor lease for one run (#517)."""
    return owner_lease.lease_path(project_root, f"workflow-{_valid_run_id(run_id)}")


def _empty_workflow_projection() -> dict[str, Any]:
    return {"run_state": None, "reason_code": None, "state_history": [], "steps": {}}


def _reduce_workflow_journal(
    projection: dict[str, Any], event: dict[str, Any]
) -> dict[str, Any]:
    """Fold one durable journal event onto the run/step projection it refines.

    Deliberately narrow: the journal is evidence for *state*, not a second
    home for step commands, output tails or results — those stay exactly
    where they already live, in the snapshot file, which remains authoritative
    for anything beyond the canonical state machine.
    """
    entry = {
        "state": event["state"],
        "at": event["at"],
        "reason_code": event["reason_code"],
    }
    if event["kind"] == "run":
        return {
            **projection,
            "run_state": event["state"],
            "reason_code": event["reason_code"],
            "state_history": [*projection["state_history"], entry],
        }
    steps = dict(projection["steps"])
    index = str(event["step_index"])
    step = dict(
        steps.get(index)
        or {"run_state": None, "reason_code": None, "state_history": []}
    )
    step["run_state"] = event["state"]
    step["reason_code"] = event["reason_code"]
    step["state_history"] = [*step["state_history"], entry]
    steps[index] = step
    return {**projection, "steps": steps}


def _validate_workflow_journal_event(event: dict[str, Any]) -> bool:
    if event.get("kind") not in {"run", "step"}:
        return False
    try:
        RunState(str(event.get("state") or "").strip().lower())
    except ValueError:
        return False
    if not isinstance(event.get("at"), str) or not event["at"]:
        return False
    if not isinstance(event.get("reason_code"), str) or not event["reason_code"]:
        return False
    if event["kind"] == "step":
        index = event.get("step_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            return False
    return True


def _append_workflow_journal(
    project_root: Path, run_id: str, event: dict[str, Any]
) -> dict[str, Any]:
    _record, projection = run_journal.append(
        workflow_journal_path(project_root, run_id),
        event,
        reduce=_reduce_workflow_journal,
        empty=_empty_workflow_projection,
        validate=_validate_workflow_journal_event,
    )
    return projection


def replay_workflow_run(project_root: Path, run_id: str) -> dict[str, Any]:
    """Reconstruct one run's canonical state purely from its journal (#517).

    Ignores the persisted snapshot entirely — this is what proves replay
    determinism rather than assuming it: for any run, this must agree with
    the snapshot's ``run_state``, ``reason_code``, and both the run- and
    step-level ``state_history``.
    """
    root = project_root.expanduser().resolve()
    return run_journal.replay(
        workflow_journal_path(root, _valid_run_id(run_id)),
        reduce=_reduce_workflow_journal,
        empty=_empty_workflow_projection,
        validate=_validate_workflow_journal_event,
    )


def _snapshot_for_comparison(project_root: Path, run_id: str) -> dict[str, Any] | None:
    """The persisted snapshot, read without failing on a legacy or broken one."""

    raw, _reason = _read_json_object(workflow_log_path(project_root, run_id))
    if raw is None:
        return None
    try:
        return _canonical_workflow_snapshot(raw)
    except (KeyError, TypeError, ValueError):
        # A snapshot too broken to canonicalise is exactly the divergence this
        # report exists to surface, so return it as-is rather than raising past
        # it the way a strict reader would.
        return raw


def workflow_contradiction_report(
    project_root: Path, run_id: str
) -> dict[str, Any] | None:
    """``None`` when snapshot and journal agree about state, else what differs.

    #613 Stage 2 asks every journal-owned record for a dual read. This module
    already had the other half -- it has shadow-written its state transitions
    to a #517 journal since the start, and :func:`replay_workflow_run` already
    rebuilds them -- but the agreement between the two was only ever asserted
    in tests and in that function's docstring. Nothing checked it at runtime,
    which is the one thing Stage 2 needs before Stage 4 can qualify a cutover
    on real traffic.

    Comparison is exact, including the timestamps inside each history entry.
    Both sides are written from the same ``now`` value in the same
    transaction, so they are byte-identical in practice -- a looser comparison
    would be weaker than the invariant actually is.

    Scope matches what the journal is evidence *for*.
    ``_reduce_workflow_journal`` is deliberately narrow -- state only, never
    step commands, output tails or results -- so comparing anything wider
    would report a difference that is by design rather than a contradiction.
    """

    root = project_root.expanduser().resolve()
    try:
        clean_id = _valid_run_id(run_id)
    except ValueError:
        return None
    snapshot = _snapshot_for_comparison(root, clean_id)
    replayed = replay_workflow_run(root, clean_id)
    empty = _empty_workflow_projection()

    if snapshot is None:
        # Never persisted, or the snapshot is gone. A journal still holding
        # transitions is itself the finding.
        if replayed == empty:
            return None
        return _workflow_report(clean_id, {}, replayed, ["snapshot_missing"])

    if replayed == empty:
        # A run migrated from the pre-journal schema has no journal to
        # disagree with. Reported with its own marker rather than as a state
        # mismatch, so a backlog of legacy runs cannot bury a real
        # contradiction in noise.
        reason = (
            "journal_absent_legacy_run"
            if snapshot.get("migrated_from_schema_version") is not None
            else "journal_absent"
        )
        return _workflow_report(clean_id, snapshot, replayed, [reason])

    mismatched: list[str] = []
    for field_name in ("run_state", "reason_code", "state_history"):
        if snapshot.get(field_name) != replayed.get(field_name):
            mismatched.append(field_name)

    steps = snapshot.get("steps")
    steps = steps if isinstance(steps, list) else []
    replayed_steps = replayed.get("steps") or {}
    if len(steps) != len(replayed_steps):
        mismatched.append("step_count")
    for index, step in enumerate(steps):
        mirrored = replayed_steps.get(str(index)) or {}
        if not isinstance(step, dict):
            mismatched.append(f"steps[{index}]")
            continue
        for field_name in ("run_state", "reason_code", "state_history"):
            if step.get(field_name) != mirrored.get(field_name):
                mismatched.append(f"steps[{index}].{field_name}")

    if not mismatched:
        return None
    return _workflow_report(clean_id, snapshot, replayed, mismatched)


def _workflow_report(
    run_id: str,
    snapshot: dict[str, Any],
    journal: dict[str, Any],
    mismatched: list[str],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "snapshot": snapshot,
        "journal": journal,
        "mismatched_fields": mismatched,
    }


def _renew_workflow_lease(project_root: Path, run_id: str) -> dict[str, Any]:
    """Best-effort heartbeat for a run's supervisor lease.

    A run's lease is durable evidence for restart recovery, not (yet) a
    correctness gate this pipeline enforces — refusing to advance a run
    because a lease renewal lost a race would be a new way for ordinary,
    single-owner work to fail. A stale or missing lease is claimed rather
    than renewed, which is always safe today: nothing currently drives the
    same ``run_id`` from two processes at once (:func:`_reserve_workflow_run`
    refuses a colliding id outright), so "not owned by us" only happens here
    if the lease was never acquired in the first place.
    """
    path = workflow_lease_path(project_root, run_id)
    held = owner_lease.current(path)
    if owner_lease.owned_by_this_process(held):
        return owner_lease.renew(path, held)
    return owner_lease.acquire(path)


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


def _workflow_steps(
    plan_steps: list[dict[str, Any]], *, at: str
) -> list[dict[str, Any]]:
    """Create durable step records before side effects begin (#379)."""

    steps: list[dict[str, Any]] = []
    for index, plan_step in enumerate(plan_steps):
        step_id = f"step-{index + 1}"
        steps.append(
            {
                "step_id": step_id,
                "step": str(plan_step.get("step") or step_id),
                "run_state": RunState.QUEUED.value,
                "reason_code": "queued",
                "state_history": [
                    _state_event(RunState.QUEUED, at=at, reason_code="queued")
                ],
            }
        )
    return steps


def _reserve_workflow_run(
    project_root: Path,
    workflow_id: str,
    run_id: str,
    *,
    task_id: str,
    plan_steps: list[dict[str, Any]],
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
        attempt = 1
        predecessor_run_id: str | None = None
        if pointer is not None:
            predecessor, predecessor_reason = _read_json_object(
                workflow_log_path(project_root, str(pointer["run_id"]))
            )
            if predecessor is None:
                raise RuntimeError(
                    f"workflow evidence is degraded: predecessor {predecessor_reason}"
                )
            previous = _canonical_workflow_snapshot(predecessor)
            if previous["task_id"] == task_id:
                if not is_terminal(previous["run_state"]):
                    raise RuntimeError("workflow task already has an active run")
                attempt = int(previous["attempt"]) + 1
                predecessor_run_id = str(previous["run_id"])
        snapshot = {
            "schema_version": _WORKFLOW_LOG_SCHEMA_VERSION,
            "task_id": task_id,
            "run_id": run_id,
            "attempt": attempt,
            "predecessor_run_id": predecessor_run_id,
            "revision": (int(pointer["revision"]) + 1) if pointer else 1,
            "workflow_id": workflow_id,
            "started_at": now,
            "updated_at": now,
            # ``state`` is retained for readers of #445 snapshots; ``run_state``
            # is the authoritative #379 lifecycle field.
            "state": RunState.QUEUED.value,
            "run_state": RunState.QUEUED.value,
            "reason_code": "queued",
            "state_history": [
                _state_event(RunState.QUEUED, at=now, reason_code="queued")
            ],
            "steps": _workflow_steps(plan_steps, at=now),
            "results": [],
        }
        _write_workflow_snapshot(project_root, snapshot)
        _write_workflow_pointer(project_root, snapshot)
        _append_workflow_journal(
            project_root,
            run_id,
            {
                "kind": "run",
                "state": RunState.QUEUED.value,
                "at": now,
                "reason_code": "queued",
            },
        )
        for step_index in range(len(snapshot["steps"])):
            _append_workflow_journal(
                project_root,
                run_id,
                {
                    "kind": "step",
                    "step_index": step_index,
                    "state": RunState.QUEUED.value,
                    "at": now,
                    "reason_code": "queued",
                },
            )
        owner_lease.acquire(workflow_lease_path(project_root, run_id))
    return snapshot


def _persist_workflow_snapshot(
    project_root: Path, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Publish one run update without letting a stale run reclaim ``current``."""

    pointer_path = workflow_last_path(project_root)
    with interprocess_transaction(pointer_path):
        _write_workflow_snapshot(project_root, snapshot)
        pointer, reason = _read_workflow_pointer(project_root)
        if pointer is None:
            raise RuntimeError(f"workflow evidence is degraded: {reason}")
        current = str(pointer["run_id"]) == str(snapshot["run_id"]) and int(
            pointer["revision"]
        ) == int(snapshot["revision"])
        if current:
            _write_workflow_pointer(project_root, snapshot)
            current_run_id = str(snapshot["run_id"])
            current_revision = int(snapshot["revision"])
        else:
            current_run_id = str(pointer["run_id"])
            current_revision = int(pointer["revision"])
    return {
        "state": "current" if current else "previous",
        "current_run_id": current_run_id,
        "current_revision": current_revision,
        "run": snapshot,
    }


def _advance_workflow_run(
    project_root: Path,
    snapshot: dict[str, Any],
    target: RunState,
    *,
    reason_code: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move a workflow through the shared canonical state machine."""

    current = RunState(str(snapshot["run_state"]).strip().lower())
    if not can_transition(current, target):
        # Keep the global #295 illegal-transition signal accurate even though
        # this caller raises: a bad workflow edge must be both rejected and
        # observable.
        transition(current, target, source="workflow_runner")
        raise RuntimeError(
            f"illegal workflow transition: {current.value} -> {target.value}"
        )
    now = _now_iso()
    updated = {
        **snapshot,
        "updated_at": now,
        "state": target.value,
        "run_state": target.value,
        "reason_code": _reason_code(reason_code, fallback="unknown"),
        "state_history": [
            *snapshot["state_history"],
            _state_event(target, at=now, reason_code=reason_code),
        ],
    }
    run_id = str(snapshot["run_id"])
    _append_workflow_journal(
        project_root,
        run_id,
        {
            "kind": "run",
            "state": target.value,
            "at": now,
            "reason_code": _reason_code(reason_code, fallback="unknown"),
        },
    )
    _renew_workflow_lease(project_root, run_id)
    return updated, _persist_workflow_snapshot(project_root, updated)


def _advance_workflow_step(
    project_root: Path,
    snapshot: dict[str, Any],
    step_index: int,
    target: RunState,
    *,
    reason_code: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Durably track a workflow step with the same canonical vocabulary."""

    steps = [dict(step) for step in snapshot["steps"]]
    step = dict(steps[step_index])
    current = RunState(str(step["run_state"]).strip().lower())
    if not can_transition(current, target):
        transition(current, target, source="workflow_runner.step")
        raise RuntimeError(
            f"illegal workflow-step transition: {current.value} -> {target.value}"
        )
    now = _now_iso()
    clean_reason = _reason_code(reason_code, fallback="unknown")
    step["run_state"] = target.value
    step["reason_code"] = clean_reason
    step["state_history"] = [
        *step["state_history"],
        _state_event(target, at=now, reason_code=reason_code),
    ]
    steps[step_index] = step
    updated = {**snapshot, "updated_at": now, "steps": steps}
    _append_workflow_journal(
        project_root,
        str(snapshot["run_id"]),
        {
            "kind": "step",
            "step_index": step_index,
            "state": target.value,
            "at": now,
            "reason_code": clean_reason,
        },
    )
    _renew_workflow_lease(project_root, str(snapshot["run_id"]))
    return updated, _persist_workflow_snapshot(project_root, updated)


def _record_workflow_result(
    snapshot: dict[str, Any], step_index: int, **values: Any
) -> dict[str, Any]:
    """Append the compatibility result after its canonical step terminal."""

    step = snapshot["steps"][step_index]
    result = {
        "step_id": step["step_id"],
        "step": step["step"],
        "run_state": step["run_state"],
        "reason_code": step["reason_code"],
        **values,
    }
    return {**snapshot, "results": [*snapshot["results"], result]}


def _terminal_workflow_outcome(results: list[dict[str, Any]]) -> tuple[RunState, str]:
    if not results:
        return RunState.BLOCKED, "no_executable_steps"
    states = {RunState(str(result["run_state"])) for result in results}
    if RunState.TIMEOUT in states:
        return RunState.TIMEOUT, "workflow_step_timeout"
    if RunState.FAILED in states:
        return RunState.FAILED, "workflow_step_failed"
    if RunState.BLOCKED in states:
        return (
            (RunState.PARTIAL, "workflow_steps_unmapped")
            if RunState.COMPLETED in states
            else (RunState.BLOCKED, "no_executable_steps")
        )
    return RunState.COMPLETED, "workflow_completed"


def _terminalize_unstarted_steps(
    project_root: Path, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Close steps the workflow could not reach after an earlier terminal result.

    A command failure or timeout ends the workflow's execution loop. Leaving
    later declared steps ``queued`` would make a terminal run look as if it
    might still do work after restart, so they are explicitly blocked instead
    of silently abandoned.
    """

    for index, step in enumerate(snapshot["steps"]):
        if is_terminal(step["run_state"]):
            continue
        snapshot, _ = _advance_workflow_step(
            project_root,
            snapshot,
            index,
            RunState.BLOCKED,
            reason_code="workflow_terminated",
        )
    return snapshot


def _complete_workflow_run(
    project_root: Path, snapshot: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify then terminally finalize one workflow run (#379)."""

    snapshot, _ = _advance_workflow_run(
        project_root, snapshot, RunState.VERIFYING, reason_code="verifying_workflow"
    )
    outcome, reason_code = _terminal_workflow_outcome(snapshot["results"])
    snapshot["completion_verdict"] = {
        "verdict": outcome.value,
        "reason_code": reason_code,
    }
    return _advance_workflow_run(
        project_root, snapshot, outcome, reason_code=reason_code
    )


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
        try:
            snapshot = _canonical_workflow_snapshot(snapshot)
        except ValueError as exc:
            return {
                "state": "degraded",
                "reason": f"workflow snapshot canonicalization failed: {safe_detail(exc)}",
                "current_run_id": pointer["run_id"],
                "current_revision": pointer["revision"],
            }
        lease = owner_lease.current(workflow_lease_path(root, target_run_id))
        return {
            "state": "current" if target_run_id == pointer["run_id"] else "previous",
            "current_run_id": pointer["run_id"],
            "current_revision": pointer["revision"],
            "run": snapshot,
            # Restart safety (#517): lets a resume flow tell "abandoned mid-run"
            # (stale) apart from "another live process is working on this"
            # (owner_alive_elsewhere) instead of guessing from run_state alone.
            "lease": owner_lease.describe(lease),
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
    task_id: str | None = None,
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
    durable_task_id = _valid_task_id(task_id or uuid.uuid4().hex)
    snapshot = _reserve_workflow_run(
        root,
        workflow_id,
        durable_run_id,
        task_id=durable_task_id,
        plan_steps=plan["steps"],
    )
    snapshot, _ = _advance_workflow_run(
        root, snapshot, RunState.PREPARING, reason_code="preparing_execution"
    )
    snapshot, _ = _advance_workflow_run(
        root, snapshot, RunState.RUNNING, reason_code="execution_started"
    )
    for index, step in enumerate(plan["steps"]):
        command = step.get("safe_command")
        if not command:
            snapshot, _ = _advance_workflow_step(
                root,
                snapshot,
                index,
                RunState.BLOCKED,
                reason_code="no_safe_command_mapping",
            )
            snapshot = _record_workflow_result(
                snapshot,
                index,
                status="skipped",
                reason="no safe command mapping",
            )
            snapshot = _persist_workflow_snapshot(root, snapshot)["run"]
            continue
        snapshot, _ = _advance_workflow_step(
            root,
            snapshot,
            index,
            RunState.PREPARING,
            reason_code="preparing_command",
        )
        snapshot, _ = _advance_workflow_step(
            root,
            snapshot,
            index,
            RunState.RUNNING,
            reason_code="command_started",
        )
        completed = run_policy_command(command, project_root, timeout=timeout)
        if getattr(completed, "timed_out", False):
            step_state, step_reason, status = (
                RunState.TIMEOUT,
                "command_timeout",
                "timeout",
            )
        elif completed.returncode == 0:
            step_state, step_reason, status = (
                RunState.COMPLETED,
                "command_completed",
                "ok",
            )
        else:
            step_state, step_reason, status = (
                RunState.FAILED,
                "command_failed",
                "failed",
            )
        snapshot, _ = _advance_workflow_step(
            root,
            snapshot,
            index,
            step_state,
            reason_code=step_reason,
        )
        snapshot = _record_workflow_result(
            snapshot,
            index,
            command=command,
            returncode=completed.returncode,
            status=status,
            output_tail=completed.combined_output[-1200:],
        )
        snapshot = _persist_workflow_snapshot(root, snapshot)["run"]
        if step_state is not RunState.COMPLETED:
            break
    snapshot = _terminalize_unstarted_steps(root, snapshot)
    snapshot, evidence = _complete_workflow_run(root, snapshot)
    return {
        **plan,
        "executed": True,
        # Executed runs expose only the canonical step records.  Keep the
        # registry/planning projection under an explicit name so callers never
        # mistake a merely declared safe command for completed work.
        "plan_steps": plan["steps"],
        "steps": snapshot["steps"],
        "task_id": durable_task_id,
        "run_id": durable_run_id,
        "revision": snapshot["revision"],
        "attempt": snapshot["attempt"],
        "predecessor_run_id": snapshot["predecessor_run_id"],
        "run_state": snapshot["run_state"],
        "reason_code": snapshot["reason_code"],
        "completion_verdict": snapshot["completion_verdict"],
        "results": snapshot["results"],
        "log": str(workflow_log_path(root, durable_run_id)),
        "evidence": evidence,
    }
