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
from collections.abc import Mapping
from typing import Any, Callable

from .command_runner import redact
from .ledger import task_fingerprint
from .proc import no_window_kwargs
from .state import state_dir
from . import shadow_journal
from .atomic_io import atomic_write_text, interprocess_transaction

GitRunner = Callable[..., "subprocess.CompletedProcess[str]"]

# Terminal states a finalized checkpoint can carry. read_only/blocked/
# cancelled_before_edit are honest non-edit outcomes (#75 acceptance).
COMPLETION_STATES = {
    "answered",
    "read_only",
    "blocked",
    "cancelled",
    "cancelled_before_edit",
    "partial",
    "timeout",
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
    completion_verdict: dict[str, Any] = field(default_factory=dict)
    recovery_actions: tuple[str, ...] = ()
    timeout: dict[str, Any] = field(default_factory=dict)
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


def _valid_checkpoint_record(record: Mapping[str, Any]) -> bool:
    """A mirrored checkpoint must carry the identity and state a reader needs.

    ``pending`` is accepted alongside the terminal states, deliberately. A
    checkpoint is *created* pending and only later finalized, so requiring a
    terminal state here silently dropped every checkpoint's opening record
    from the mirror: the shadow would only ever have seen finalized runs, and
    an interrupted one -- precisely the case #613 exists to reconstruct --
    would have left no trace at all. Caught by this module's shadow tests.
    """

    return (
        isinstance(record.get("checkpoint_id"), str)
        and bool(record.get("checkpoint_id"))
        and record.get("completion_state") in (*COMPLETION_STATES, "pending")
    )


def _save(project_root: Path, checkpoint: RunCheckpoint) -> Path:
    path = _checkpoint_path(project_root, checkpoint.checkpoint_id)
    with interprocess_transaction(path):
        atomic_write_text(
            path,
            json.dumps(checkpoint.to_dict(), indent=2, sort_keys=True) + "\n",
        )
        # #613 Stage 2: mirror the canonical event while still holding the
        # file's own lock, so the journal can never observe saves in a
        # different order than the file did. `_save` is the single place
        # every checkpoint transition reaches disk through (create and
        # finalize both funnel here), so this covers all of them.
        shadow_journal.record_snapshot(
            path, checkpoint.to_dict(), is_valid_record=_valid_checkpoint_record
        )
    return path


def shadow_journal_projection(project_root: Path, checkpoint_id: str) -> dict[str, Any]:
    """The checkpoint state the shadow journal alone would reconstruct."""

    return shadow_journal.projection(
        _checkpoint_path(project_root, checkpoint_id),
        is_valid_record=_valid_checkpoint_record,
    )


def checkpoint_contradiction_report(
    project_root: Path, checkpoint_id: str
) -> dict[str, Any] | None:
    """``None`` when the file and its shadow agree; otherwise, what disagrees.

    The dual-read #613 asks for. ``_load_checkpoint`` is deliberately not
    reused here: it raises on anything malformed, which is right for every
    other caller but would raise past the very divergence this reports.
    """

    path = _checkpoint_path(project_root, checkpoint_id)

    def read_legacy() -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    return shadow_journal.contradiction_report(
        path,
        read_legacy,
        is_valid_record=_valid_checkpoint_record,
        identity={"checkpoint_id": checkpoint_id},
    )


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
    path = _checkpoint_path(root, checkpoint.checkpoint_id)
    with interprocess_transaction(path):
        # A replayed start request must not reset a checkpoint which another
        # process has already finalized. Returning the durable record also
        # makes a same-id create retry harmless.
        if path.is_file():
            return _load_checkpoint(path)
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


def _redacted_json(value: Any, *, depth: int = 0) -> Any:
    """Return bounded JSON data suitable for durable recovery metadata."""

    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact(value)[:512]
    if isinstance(value, Mapping):
        return {
            redact(str(key))[:128]: _redacted_json(item, depth=depth + 1)
            for key, item in list(value.items())[:64]
        }
    if isinstance(value, (list, tuple)):
        return [_redacted_json(item, depth=depth + 1) for item in value[:64]]
    return redact(str(value))[:512]


_TIMEOUT_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "timeout_id",
        "timeout_origin",
        "owner",
        "configured_seconds",
        "elapsed_seconds",
        "provider_condition",
        "retry_safety",
        "deadline_budget_id",
        "task_deadline_seconds",
        "provider_idle_timeout_seconds",
        "lane",
        "last_activity_seconds_ago",
        "phase",
        "operation_id",
        "route_id",
        "teardown_state",
        "cost_state",
        "verification_state",
        "progress_observed",
        "external_effect_possible",
        "task_id",
        "run_id",
        "attempt_id",
        "step_id",
        "checkpoint_id",
    }
)
_PROGRESS_EVIDENCE_FIELDS = frozenset(
    {
        "score",
        "best_score",
        "steps_since_best",
        "distinct_observations",
        "repeated_failures",
        "milestones",
    }
)


def record_timeout_checkpoint(
    project_root: Path,
    checkpoint_id: str,
    *,
    stage: str,
    timeout_event: Mapping[str, Any],
    progress_evidence: Mapping[str, Any] | None = None,
    verification_state: str = "incomplete",
    partial_answer_retained: bool = False,
    git_runner: GitRunner = subprocess.run,
) -> RunCheckpoint:
    """Persist deadline evidence while the run checkpoint is still pending.

    ``pre_teardown`` is written before the provider process tree is terminated;
    ``terminal`` records the observed teardown result afterward. Both are kept
    so recovery can prove that useful work was captured before destructive
    cleanup without confusing that early observation with final process truth.
    """

    if stage not in {"pre_teardown", "terminal"}:
        raise ValueError(f"Unknown timeout checkpoint stage: {stage!r}")
    root = project_root.expanduser().resolve()
    path = _checkpoint_path(root, checkpoint_id)
    with interprocess_transaction(path):
        checkpoint = _load_checkpoint(path)
        if checkpoint.completion_state != "pending":
            return checkpoint
        current_git, current_changed = _git_snapshot(root, run=git_runner)
        baseline = set(checkpoint.baseline_changed_files)
        changed_during = [item for item in current_changed if item not in baseline]
        repository = {
            "git": current_git,
            "changed_files": current_changed,
            "changed_during_run": changed_during,
            "head_changed": bool(
                checkpoint.git.get("head")
                and current_git.get("head")
                and checkpoint.git.get("head") != current_git.get("head")
            ),
        }
        snapshot = {
            "recorded_at": _now(),
            "recorded_before_teardown": stage == "pre_teardown",
            "timeout_event": _redacted_json(
                {
                    key: timeout_event[key]
                    for key in _TIMEOUT_EVENT_FIELDS
                    if key in timeout_event
                }
            ),
            "progress_evidence": _redacted_json(
                {
                    key: progress_evidence[key]
                    for key in _PROGRESS_EVIDENCE_FIELDS
                    if progress_evidence is not None and key in progress_evidence
                }
            ),
            "verification_state": redact(str(verification_state or "incomplete"))[:64],
            "partial_answer_retained": bool(partial_answer_retained),
            "repository": repository,
        }
        timeout = dict(checkpoint.timeout)
        timeout[stage] = snapshot
        updated = replace(checkpoint, timeout=timeout)
        _save(root, updated)
        return updated


def finalize_run_checkpoint(
    project_root: Path,
    checkpoint_id: str,
    *,
    completion_state: str,
    outcome: str = "",
    changed_files: Any = (),
    diff_summary: dict[str, Any] | None = None,
    completion_verdict: dict[str, Any] | None = None,
    recovery_actions: Any = (),
) -> RunCheckpoint:
    """Record the run's result: changed files, diff summary, completion (#75)."""
    finalized, _ = _finalize_pending_checkpoint(
        project_root,
        checkpoint_id,
        completion_state=completion_state,
        outcome=outcome,
        changed_files=changed_files,
        diff_summary=diff_summary,
        completion_verdict=completion_verdict,
        recovery_actions=recovery_actions,
    )
    return finalized


def _finalize_pending_checkpoint(
    project_root: Path,
    checkpoint_id: str,
    *,
    completion_state: str,
    outcome: str = "",
    changed_files: Any = (),
    diff_summary: dict[str, Any] | None = None,
    completion_verdict: dict[str, Any] | None = None,
    recovery_actions: Any = (),
) -> tuple[RunCheckpoint, bool]:
    """Finalize once, returning whether this caller made the durable change."""
    if completion_state not in COMPLETION_STATES:
        raise ValueError(f"Unknown completion state: {completion_state!r}")
    path = _checkpoint_path(project_root, checkpoint_id)
    with interprocess_transaction(path):
        checkpoint = _load_checkpoint(path)
        # A terminal checkpoint is immutable: normal completion wins over a
        # stale recovery snapshot, and duplicate finalize calls are idempotent.
        if checkpoint.completion_state != "pending":
            return checkpoint, False
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
            completion_verdict=dict(completion_verdict or {}),
            recovery_actions=tuple(
                redact(str(item)) for item in (recovery_actions or ())
            ),
            finalized_at=_now(),
        )
        _save(project_root, finalized)
        return finalized, True


def load_run_checkpoint(project_root: Path, checkpoint_id: str) -> RunCheckpoint:
    path = _checkpoint_path(project_root, checkpoint_id)
    with interprocess_transaction(path):
        return _load_checkpoint(path)


def _load_checkpoint(path: Path) -> RunCheckpoint:
    data = json.loads(path.read_text(encoding="utf-8"))
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
        completion_verdict=dict(data.get("completion_verdict") or {}),
        recovery_actions=tuple(data.get("recovery_actions") or ()),
        timeout=dict(data.get("timeout") or {}),
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
            finalized, recovered_here = _finalize_pending_checkpoint(
                project_root,
                checkpoint.checkpoint_id,
                completion_state="interrupted",
                outcome="Run interrupted before finalizing; review before reuse",
                changed_files=checkpoint.baseline_changed_files,
                recovery_actions=(
                    "inspect the working tree against the recorded git head",
                ),
            )
            if recovered_here:
                recovered.append(finalized)
    return recovered
