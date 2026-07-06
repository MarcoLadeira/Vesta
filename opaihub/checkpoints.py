"""Recoverable run checkpoints for every edit-capable execution (#75).

Safe Auto is only trustworthy if a run that can edit files begins from
recoverable evidence and ends with an understandable review. This module is
the single checkpoint contract shared by the GUI pipeline, CLI, account
runners, workflows, and worktree lanes: a checkpoint is created *before* any
model or workflow receives edit capability, and finalized with the resulting
changes afterwards.

Privacy: a checkpoint stores a one-way task hash, Git identity, changed-file
paths, and the run's mode/model/policy/budget/panic state. It never stores
raw prompt text, secrets, credentials, or source snippets. File paths are
redacted defensively before they touch disk.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - fixed git argv, never a shell
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .command_runner import redact
from .ledger import task_fingerprint
from .proc import no_window_kwargs
from .state import state_dir

GitRunner = Callable[..., "subprocess.CompletedProcess[str]"]

# Terminal states a finalized checkpoint can carry. read_only/blocked/
# cancelled_before_edit are honest non-edit outcomes (#75 acceptance).
COMPLETION_STATES = {
    "answered",
    "read_only",
    "blocked",
    "cancelled",
    "cancelled_before_edit",
    "failed",
    "interrupted",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _valid_id(value: str) -> str:
    checkpoint_id = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", checkpoint_id):
        raise ValueError("Invalid checkpoint id")
    return checkpoint_id


def _redact_paths(paths: Any) -> list[str]:
    out: list[str] = []
    for item in paths or ():
        value = redact(str(item)).replace("\\", "/").strip()
        if value and value not in out:
            out.append(value)
    return out


@dataclass(frozen=True)
class RunCheckpoint:
    checkpoint_id: str
    task_id: str
    task_hash: str
    edit_capable: bool
    mode: str
    model: str
    policy: dict[str, Any] = field(default_factory=dict)
    git: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    baseline_changed_files: tuple[str, ...] = ()
    created_at: str = ""
    # Result (filled by finalize_run_checkpoint):
    completion_state: str = "pending"
    outcome: str = ""
    result_changed_files: tuple[str, ...] = ()
    changed_during_run: tuple[str, ...] = ()
    diff_summary: dict[str, Any] = field(default_factory=dict)
    recovery_actions: tuple[str, ...] = ()
    finalized_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["baseline_changed_files"] = list(self.baseline_changed_files)
        value["result_changed_files"] = list(self.result_changed_files)
        value["changed_during_run"] = list(self.changed_during_run)
        value["recovery_actions"] = list(self.recovery_actions)
        return value


def _checkpoint_dir(project_root: Path) -> Path:
    return state_dir(project_root.expanduser().resolve()) / "agent" / "checkpoints"


def _checkpoint_path(project_root: Path, checkpoint_id: str) -> Path:
    return _checkpoint_dir(project_root) / f"{_valid_id(checkpoint_id)}.json"


def _save(project_root: Path, checkpoint: RunCheckpoint) -> Path:
    path = _checkpoint_path(project_root, checkpoint.checkpoint_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(checkpoint.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _git(root: Path, argv: list[str], *, run: GitRunner) -> str:
    try:
        completed = run(
            ["git", *argv],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15.0,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "").strip() if completed.returncode == 0 else ""


def _git_snapshot(root: Path, *, run: GitRunner) -> tuple[dict[str, Any], list[str]]:
    """Return (git identity, baseline changed paths) without a full repo walk."""
    top = _git(root, ["rev-parse", "--show-toplevel"], run=run)
    if not top:
        return {"is_repo": False}, []
    head = _git(root, ["rev-parse", "HEAD"], run=run)
    branch = _git(root, ["branch", "--show-current"], run=run)
    remote = _git(root, ["remote", "get-url", "origin"], run=run)
    status = _git(root, ["status", "--porcelain=v1", "--untracked-files=all"], run=run)
    baseline: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        normalized = path.replace("\\", "/")
        if normalized.startswith((".opaihub/", ".opcoding/")):
            continue
        if normalized:
            baseline.append(normalized)
    git = {
        "is_repo": True,
        "head": head,
        "branch": branch,
        "remote": redact(remote),
        "dirty_baseline_count": len(baseline),
    }
    return git, _redact_paths(baseline)


def create_run_checkpoint(
    project_root: Path,
    *,
    task: str,
    task_id: str,
    edit_capable: bool,
    mode: str,
    model: str,
    policy: dict[str, Any] | None = None,
    checkpoint_id: str | None = None,
    git_runner: GitRunner = subprocess.run,
    read_budget: bool = True,
) -> RunCheckpoint:
    """Snapshot recoverable evidence *before* a run may touch files (#75)."""
    root = project_root.expanduser().resolve()
    git, baseline = _git_snapshot(root, run=git_runner)
    budget: dict[str, Any] = {}
    if read_budget:
        try:
            from .budget import budget_status

            status = budget_status(root)
            budget = {
                "panic": bool(status.get("panic")),
                "profile": status.get("profile"),
                "caps": status.get("caps"),
                "spent": status.get("spent"),
            }
        except Exception:  # noqa: BLE001 - a budget read must never block a run
            budget = {}
    checkpoint = RunCheckpoint(
        checkpoint_id=_valid_id(checkpoint_id or uuid.uuid4().hex[:16]),
        task_id=str(task_id),
        task_hash=task_fingerprint(task),
        edit_capable=bool(edit_capable),
        mode=str(mode),
        model=str(model),
        policy=dict(redact_policy(policy or {})),
        git=git,
        budget=budget,
        baseline_changed_files=tuple(baseline),
        created_at=_now(),
    )
    _save(root, checkpoint)
    return checkpoint


def redact_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Keep only the non-sensitive policy fields worth recovering."""
    allowed = ("mode", "label", "requires_confirmation", "capabilities", "rationale")
    out: dict[str, Any] = {}
    for key in allowed:
        if key in policy:
            value = policy[key]
            if isinstance(value, str):
                value = redact(value)
            elif isinstance(value, (list, tuple)):
                value = [redact(str(item)) for item in value]
            out[key] = value
    return out


def finalize_run_checkpoint(
    project_root: Path,
    checkpoint_id: str,
    *,
    completion_state: str,
    outcome: str = "",
    changed_files: Any = (),
    diff_summary: dict[str, Any] | None = None,
    recovery_actions: Any = (),
) -> RunCheckpoint:
    """Record the run's result: changed files, diff summary, completion (#75)."""
    if completion_state not in COMPLETION_STATES:
        raise ValueError(f"Unknown completion state: {completion_state!r}")
    checkpoint = load_run_checkpoint(project_root, checkpoint_id)
    resulting = _redact_paths(changed_files)
    baseline = set(checkpoint.baseline_changed_files)
    # Files changed *during* the run are those not already dirty at baseline.
    during = [path for path in resulting if path not in baseline]
    finalized = replace(
        checkpoint,
        completion_state=completion_state,
        outcome=redact(str(outcome)),
        result_changed_files=tuple(resulting),
        changed_during_run=tuple(during),
        diff_summary=dict(diff_summary or {}),
        recovery_actions=tuple(redact(str(item)) for item in (recovery_actions or ())),
        finalized_at=_now(),
    )
    _save(project_root, finalized)
    return finalized


def load_run_checkpoint(project_root: Path, checkpoint_id: str) -> RunCheckpoint:
    data = json.loads(
        _checkpoint_path(project_root, checkpoint_id).read_text(encoding="utf-8")
    )
    return RunCheckpoint(
        checkpoint_id=str(data["checkpoint_id"]),
        task_id=str(data.get("task_id") or ""),
        task_hash=str(data.get("task_hash") or ""),
        edit_capable=bool(data.get("edit_capable", False)),
        mode=str(data.get("mode") or ""),
        model=str(data.get("model") or ""),
        policy=dict(data.get("policy") or {}),
        git=dict(data.get("git") or {}),
        budget=dict(data.get("budget") or {}),
        baseline_changed_files=tuple(data.get("baseline_changed_files") or ()),
        created_at=str(data.get("created_at") or ""),
        completion_state=str(data.get("completion_state") or "pending"),
        outcome=str(data.get("outcome") or ""),
        result_changed_files=tuple(data.get("result_changed_files") or ()),
        changed_during_run=tuple(data.get("changed_during_run") or ()),
        diff_summary=dict(data.get("diff_summary") or {}),
        recovery_actions=tuple(data.get("recovery_actions") or ()),
        finalized_at=str(data.get("finalized_at") or ""),
    )


def list_run_checkpoints(project_root: Path) -> list[RunCheckpoint]:
    directory = _checkpoint_dir(project_root)
    if not directory.is_dir():
        return []
    checkpoints = []
    for path in directory.glob("*.json"):
        try:
            checkpoints.append(load_run_checkpoint(project_root, path.stem))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(checkpoints, key=lambda item: (item.created_at, item.checkpoint_id))


def recover_interrupted_checkpoints(project_root: Path) -> list[RunCheckpoint]:
    """Finalize orphaned pending checkpoints (e.g. after a crash) as interrupted."""
    recovered = []
    for checkpoint in list_run_checkpoints(project_root):
        if checkpoint.completion_state == "pending":
            recovered.append(
                finalize_run_checkpoint(
                    project_root,
                    checkpoint.checkpoint_id,
                    completion_state="interrupted",
                    outcome="Run interrupted before finalizing; review before reuse",
                    changed_files=checkpoint.baseline_changed_files,
                    recovery_actions=(
                        "inspect the working tree against the recorded git head",
                    ),
                )
            )
    return recovered
