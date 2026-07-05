"""Durable, user-owned background runs for bounded coding workflows (#176).

Runs live under ``.opaihub/agent/background/`` and survive restarts. The queue
and schedule files keep the *redacted* task text locally so queued work can
still execute later; ledger events and notifications only ever carry hashes
and redacted metadata, never raw prompts or credentials.

No daemon is started: runs execute inside a user-owned process (GUI or CLI)
through :class:`BackgroundRunner`, and scheduling is a deterministic
``tick_automations`` call driven by the user's own session. Paid or cloud
escalation is fail-closed - a run may only carry ``allow_cloud=True`` when the
user confirmed it explicitly at enqueue time, and any provider result that
asks for confirmation parks the run as ``blocked`` instead of auto-approving.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .command_runner import redact
from .state import state_dir
from .workflow_ledger import WorkflowLedger, redact_structure
from .workflow_templates import workflow_templates

RUN_STATUSES = {
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "blocked",
    "interrupted",
}
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "blocked", "interrupted"}

# Pipeline statuses that mean "a human must approve before anything spends or
# escalates". A background run must park on these, never answer for the user.
CONFIRMATION_STATUSES = {
    "blocked",
    "confirmation_required",
    "needs_auto_confirmation",
    "needs_confirmation",
    "needs_free_confirmation",
    "needs_limit_confirmation",
}
_SUCCESS_STATUSES = {"answered", "completed", "ok", "success"}

CADENCE_SECONDS = {"hourly": 3600, "daily": 86400, "weekly": 604800}
CADENCES = {"manual", *CADENCE_SECONDS}

_ACTIVE_LOCK = threading.RLock()
_ACTIVE_CANCEL_EVENTS: dict[tuple[str, str], threading.Event] = {}

Executor = Callable[[Path, "AutomationRun", threading.Event], dict[str, Any]]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _now_iso() -> str:
    return _now().isoformat()


def _valid_run_id(value: str) -> str:
    run_id = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ValueError("Invalid background run id")
    return run_id


@dataclass(frozen=True)
class AutomationRun:
    run_id: str
    workflow_id: str
    task: str
    status: str = "queued"
    owner: str = "user"
    schedule_id: str = ""
    allow_cloud: bool = False
    cancel_requested: bool = False
    message: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _background_dir(project_root: Path) -> Path:
    return state_dir(project_root.expanduser().resolve()) / "agent" / "background"


def _run_path(project_root: Path, run_id: str) -> Path:
    return _background_dir(project_root) / "runs" / f"{_valid_run_id(run_id)}.json"


def _notifications_path(project_root: Path) -> Path:
    return _background_dir(project_root) / "notifications.jsonl"


def _schedules_path(project_root: Path) -> Path:
    return _background_dir(project_root) / "schedules.json"


def _save_run(project_root: Path, run: AutomationRun) -> Path:
    path = _run_path(project_root, run.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(run.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path


def load_run(project_root: Path, run_id: str) -> AutomationRun:
    data = json.loads(_run_path(project_root, run_id).read_text(encoding="utf-8"))
    return AutomationRun(
        run_id=str(data["run_id"]),
        workflow_id=str(data.get("workflow_id") or ""),
        task=str(data.get("task") or ""),
        status=str(data.get("status") or "queued"),
        owner=str(data.get("owner") or "user"),
        schedule_id=str(data.get("schedule_id") or ""),
        allow_cloud=bool(data.get("allow_cloud", False)),
        cancel_requested=bool(data.get("cancel_requested", False)),
        message=str(data.get("message") or ""),
        created_at=str(data.get("created_at") or ""),
        started_at=str(data.get("started_at") or ""),
        finished_at=str(data.get("finished_at") or ""),
        result=dict(data.get("result") or {}),
    )


def list_runs(project_root: Path, *, status: str | None = None) -> list[AutomationRun]:
    runs_dir = _background_dir(project_root) / "runs"
    if not runs_dir.is_dir():
        return []
    runs = []
    for path in runs_dir.glob("*.json"):
        try:
            run = load_run(project_root, path.stem)
        except (OSError, ValueError, KeyError):
            continue
        if status is None or run.status == status:
            runs.append(run)
    return sorted(runs, key=lambda run: (run.created_at, run.run_id))


def _notify(project_root: Path, run: AutomationRun, message: str) -> dict[str, Any]:
    """Append one redacted status notification; the user's feed, not a prompt."""

    entry = {
        "created_at": _now_iso(),
        "run_id": run.run_id,
        "workflow_id": run.workflow_id,
        "schedule_id": run.schedule_id,
        "status": run.status,
        "message": str(redact_structure(str(message))),
    }
    path = _notifications_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    WorkflowLedger(project_root, task_id=run.run_id).append(
        "background_run",
        task=run.task,
        metadata={
            "workflow_id": run.workflow_id,
            "status": run.status,
            "schedule_id": run.schedule_id,
            "allow_cloud": run.allow_cloud,
        },
    )
    return entry


def read_notifications(project_root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    path = _notifications_path(project_root)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries[-max(1, int(limit)) :]


def _update_run(
    project_root: Path, run: AutomationRun, **changes: Any
) -> AutomationRun:
    updated = replace(run, **changes)
    _save_run(project_root, updated)
    return updated


def enqueue_automation(
    project_root: Path,
    workflow_id: str,
    task: str,
    *,
    allow_cloud: bool = False,
    cloud_confirmed: bool = False,
    schedule_id: str = "",
    run_id: str | None = None,
) -> AutomationRun:
    """Queue one durable background run for a bounded coding workflow."""

    workflow = str(workflow_id or "").strip()
    if workflow not in workflow_templates():
        raise ValueError(f"Unknown coding workflow: {workflow_id!r}")
    if allow_cloud and not cloud_confirmed:
        raise ValueError(
            "Cloud or paid escalation for a background run requires explicit "
            "confirmation (cloud_confirmed=True)"
        )
    run = AutomationRun(
        run_id=_valid_run_id(run_id or uuid.uuid4().hex[:16]),
        workflow_id=workflow,
        task=redact(str(task or "").strip()),
        status="queued",
        schedule_id=str(schedule_id or ""),
        allow_cloud=bool(allow_cloud),
        message="Queued and waiting for a user-owned runner",
        created_at=_now_iso(),
    )
    if _run_path(project_root, run.run_id).exists():
        raise FileExistsError(f"Background run already exists: {run.run_id}")
    _save_run(project_root, run)
    _notify(project_root, run, "Background run queued")
    return run


def request_cancel(project_root: Path, run_id: str) -> AutomationRun:
    """Cancel durably: queued runs stop now, running runs get the signal."""

    run = load_run(project_root, run_id)
    if run.status in TERMINAL_STATUSES:
        return run
    if run.status == "queued":
        run = _update_run(
            project_root,
            run,
            status="cancelled",
            cancel_requested=True,
            message="Cancelled before it started",
            finished_at=_now_iso(),
        )
        _notify(project_root, run, "Cancelled before it started")
        return run
    run = _update_run(project_root, run, cancel_requested=True)
    with _ACTIVE_LOCK:
        event = _ACTIVE_CANCEL_EVENTS.get(
            (str(project_root.expanduser().resolve()), run.run_id)
        )
    if event is not None:
        event.set()
    _notify(project_root, run, "Cancellation requested")
    return run


def _bounded_result(payload: dict[str, Any]) -> dict[str, Any]:
    error = payload.get("error")
    if isinstance(error, dict):
        error = {
            "code": str(error.get("code") or ""),
            "title": str(error.get("title") or ""),
        }
    return dict(
        redact_structure(
            {
                "status": str(payload.get("status") or ""),
                "changed_files": [
                    str(item) for item in payload.get("changed_files") or []
                ],
                "next_actions": [
                    str(item) for item in payload.get("next_actions") or []
                ],
                "error": error or "",
            }
        )
    )


class BackgroundRunner:
    """Execute queued runs inside the user's own process; never a daemon."""

    def __init__(
        self,
        project_root: Path,
        *,
        executor: Executor,
        max_concurrent: int = 1,
    ) -> None:
        self.project_root = project_root.expanduser().resolve()
        self.executor = executor
        self.max_concurrent = max(1, int(max_concurrent))
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    def active_run_ids(self) -> tuple[str, ...]:
        with self._lock:
            self._threads = {
                run_id: thread
                for run_id, thread in self._threads.items()
                if thread.is_alive()
            }
            return tuple(sorted(self._threads))

    def start(self, run_id: str) -> threading.Thread:
        run = load_run(self.project_root, run_id)
        if run.status != "queued":
            raise ValueError(f"Only queued runs can start (got {run.status!r})")
        with self._lock:
            if len(self.active_run_ids()) >= self.max_concurrent:
                raise RuntimeError(
                    f"Background concurrency limit reached ({self.max_concurrent})"
                )
            thread = threading.Thread(
                target=self._execute, args=(run,), daemon=True, name=f"opai-bg-{run_id}"
            )
            self._threads[run.run_id] = thread
            thread.start()
            return thread

    def run_now(self, run_id: str) -> AutomationRun:
        """Synchronous execution for CLI use; same gates as threaded runs."""

        run = load_run(self.project_root, run_id)
        if run.status != "queued":
            raise ValueError(f"Only queued runs can start (got {run.status!r})")
        self._execute(run)
        return load_run(self.project_root, run_id)

    def _execute(self, run: AutomationRun) -> None:
        key = (str(self.project_root), run.run_id)
        cancel_event = threading.Event()
        if run.cancel_requested:
            cancel_event.set()
        with _ACTIVE_LOCK:
            _ACTIVE_CANCEL_EVENTS[key] = cancel_event
        run = _update_run(
            self.project_root,
            run,
            status="running",
            message="Running in a user-owned session",
            started_at=_now_iso(),
        )
        _notify(self.project_root, run, "Background run started")
        try:
            payload = self.executor(self.project_root, run, cancel_event)
        except Exception as exc:  # noqa: BLE001 - a background run must fail closed
            self._finish(
                run, status="failed", message=f"Executor error: {exc}", payload={}
            )
            return
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE_CANCEL_EVENTS.pop(key, None)
        status = str((payload or {}).get("status") or "")
        if cancel_event.is_set() or status == "cancelled":
            self._finish(
                run,
                status="cancelled",
                message="Stopped by you",
                payload=payload or {},
            )
        elif status in CONFIRMATION_STATUSES:
            self._finish(
                run,
                status="blocked",
                message=(
                    "Paused: this run needs your explicit confirmation before any "
                    "paid, cloud, or gated action. Nothing was approved for you."
                ),
                payload=payload or {},
            )
        elif status in _SUCCESS_STATUSES:
            self._finish(
                run,
                status="completed",
                message="Background run completed",
                payload=payload or {},
            )
        else:
            self._finish(
                run,
                status="failed",
                message=f"Background run failed ({status or 'no status'})",
                payload=payload or {},
            )

    def _finish(
        self,
        run: AutomationRun,
        *,
        status: str,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        finished = _update_run(
            self.project_root,
            load_run(self.project_root, run.run_id),
            status=status,
            message=str(redact_structure(str(message))),
            finished_at=_now_iso(),
            result=_bounded_result(payload),
        )
        _notify(self.project_root, finished, message)


def recover_interrupted_runs(
    project_root: Path, *, active_run_ids: tuple[str, ...] = ()
) -> list[AutomationRun]:
    """Mark orphaned 'running' runs (e.g. after a crash) as interrupted."""

    recovered = []
    for run in list_runs(project_root, status="running"):
        if run.run_id in active_run_ids:
            continue
        updated = _update_run(
            project_root,
            run,
            status="interrupted",
            message="Interrupted: the owning session ended before it finished",
            finished_at=_now_iso(),
        )
        _notify(project_root, updated, "Run interrupted; re-enqueue to retry")
        recovered.append(updated)
    return recovered


def pipeline_executor(
    *, model_id: str | None = None, mode: str | None = None
) -> Executor:
    """Executor that routes a run through the normal OPai pipeline.

    ``allow_cloud`` comes from the run itself (already user-confirmed at
    enqueue time); every existing pipeline gate still applies.
    """

    def _execute(
        project_root: Path, run: AutomationRun, cancel_event: threading.Event
    ) -> dict[str, Any]:
        from .gui_pipeline import handle_gui_message

        steps = ", ".join(
            str(step)
            for step in (workflow_templates().get(run.workflow_id) or {}).get("steps")
            or []
        )
        message = (
            f"Background {run.workflow_id} workflow"
            + (f" ({steps})" if steps else "")
            + f": {run.task}"
        )
        return handle_gui_message(
            project_root,
            message,
            model_id=model_id,
            mode=mode,
            cancel=cancel_event,
            allow_cloud=run.allow_cloud,
        )

    return _execute


def _read_schedules(project_root: Path) -> list[dict[str, Any]]:
    path = _schedules_path(project_root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_schedules(project_root: Path, schedules: list[dict[str, Any]]) -> Path:
    path = _schedules_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(schedules, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path


def schedule_automation(
    project_root: Path,
    workflow_id: str,
    task: str,
    *,
    cadence: str,
    allow_cloud: bool = False,
    cloud_confirmed: bool = False,
) -> dict[str, Any]:
    """Create or replace a cadence schedule for a bounded coding workflow."""

    workflow = str(workflow_id or "").strip()
    if workflow not in workflow_templates():
        raise ValueError(f"Unknown coding workflow: {workflow_id!r}")
    if cadence not in CADENCES:
        raise ValueError(f"Unknown cadence: {cadence!r} (use {sorted(CADENCES)})")
    if allow_cloud and not cloud_confirmed:
        raise ValueError(
            "Cloud or paid escalation for a schedule requires explicit "
            "confirmation (cloud_confirmed=True)"
        )
    schedule = {
        "id": f"{workflow}:{cadence}:{uuid.uuid4().hex[:8]}",
        "workflow_id": workflow,
        "task": redact(str(task or "").strip()),
        "cadence": cadence,
        "enabled": True,
        "owner": "user",
        "allow_cloud": bool(allow_cloud),
        "created_at": _now_iso(),
        "last_enqueued_at": "",
        "notes": "No daemon runs this; tick_automations enqueues due work.",
    }
    schedules = _read_schedules(project_root)
    schedules.append(schedule)
    _write_schedules(project_root, schedules)
    return schedule


def list_automation_schedules(project_root: Path) -> list[dict[str, Any]]:
    return _read_schedules(project_root)


def tick_automations(
    project_root: Path, *, now: datetime | None = None
) -> list[AutomationRun]:
    """Enqueue due scheduled runs, at most once per cadence window."""

    moment = (now or _now()).astimezone(timezone.utc)
    schedules = _read_schedules(project_root)
    enqueued = []
    for schedule in schedules:
        cadence = str(schedule.get("cadence") or "manual")
        window = CADENCE_SECONDS.get(cadence)
        if not schedule.get("enabled", True) or window is None:
            continue
        last_raw = str(schedule.get("last_enqueued_at") or "")
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw)
            except ValueError:
                last = None
            if last is not None and (moment - last).total_seconds() < window:
                continue
        run = enqueue_automation(
            project_root,
            str(schedule.get("workflow_id") or ""),
            str(schedule.get("task") or ""),
            allow_cloud=bool(schedule.get("allow_cloud", False)),
            cloud_confirmed=bool(schedule.get("allow_cloud", False)),
            schedule_id=str(schedule.get("id") or ""),
        )
        schedule["last_enqueued_at"] = moment.isoformat()
        enqueued.append(run)
    if enqueued:
        _write_schedules(project_root, schedules)
    return enqueued
