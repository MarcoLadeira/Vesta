"""Small, prompt-free persisted status for the current coding workflow."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import BinaryIO, Iterator

from .command_runner import redact
from .state import state_dir

WORKFLOW_SCHEMA_VERSION = 2
MAX_WORKFLOW_TEXT_CHARS = 2_000
MAX_WORKFLOW_ITEM_CHARS = 500
MAX_WORKFLOW_HISTORY = 100

_WORKFLOW_LOCKS: dict[str, threading.RLock] = {}
_WORKFLOW_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class WorkflowState:
    task_id: str = ""
    checkpoint_id: str = ""
    mode: str = "explain"
    phase: str = "idle"
    message: str = "Ready"
    tests_status: str = "not_run"
    pr_url: str = ""
    merge_status: str = "not_requested"
    issue_number: int | None = None
    blockers: tuple[str, ...] = ()
    blocker: str = ""
    next_actions: tuple[str, ...] = ()
    plan_steps: tuple[str, ...] = ()
    history: tuple[dict[str, object], ...] = ()
    changed_files: tuple[str, ...] = ()
    last_test: dict[str, object] = field(default_factory=dict)
    provider: dict[str, object] = field(default_factory=dict)
    cost: dict[str, object] = field(default_factory=dict)
    safety_gates: dict[str, object] = field(default_factory=dict)
    diff_review: dict[str, object] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        clean = _sanitize_workflow_state(self)
        data = asdict(clean)
        data["schema_version"] = WORKFLOW_SCHEMA_VERSION
        data["blockers"] = list(clean.blockers)
        data["next_actions"] = list(clean.next_actions)
        data["plan_steps"] = list(clean.plan_steps)
        data["history"] = list(clean.history)
        data["changed_files"] = list(clean.changed_files)
        return data


def _workflow_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _WORKFLOW_LOCKS_GUARD:
        return _WORKFLOW_LOCKS.setdefault(key, threading.RLock())


def _try_process_lock(handle: BinaryIO) -> bool:
    """Try to lock the first byte using the host OS' advisory lock."""

    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_process_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _process_workflow_lock(
    root: Path,
    target: Path,
    *,
    timeout_seconds: float = 60.0,
) -> Iterator[None]:
    """Serialize workflow.json across processes and survive crashes (#313).

    Two OPai windows write workflow state on every turn. Without this, a
    Windows ``replace()`` while another process holds the file open raises
    ``PermissionError`` and fails the turn, and a reader can transiently
    observe a missing file as silent empty state. The advisory lock releases
    automatically when a crashed holder's handle closes.
    """

    lock_path = target.with_name(f"{target.name}.lock")
    _validate_workflow_target(root, lock_path)
    handle = lock_path.open("a+b")
    acquired = False
    try:
        _validate_workflow_target(root, lock_path)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while not (acquired := _try_process_lock(handle)):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for workflow lock: {target}")
            time.sleep(0.01)
        _validate_workflow_target(root, target)
        yield
    finally:
        try:
            if acquired:
                _unlock_process_file(handle)
        finally:
            handle.close()


def _clean_text(value: object, *, limit: int, default: str = "") -> str:
    cleaned = redact(str(value or "")).strip()[: max(0, int(limit))]
    return cleaned or default


def _clean_strings(
    value: object,
    *,
    count: int = 100,
    item_limit: int = MAX_WORKFLOW_ITEM_CHARS,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        cleaned
        for item in value[: max(0, int(count))]
        if (cleaned := _clean_text(item, limit=item_limit))
    )


def _clean_value(value: object, *, depth: int = 0) -> object:
    """Bound and redact nested diagnostic mappings without changing numbers."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _clean_text(value, limit=1_000)
    if depth >= 4:
        return _clean_text(value, limit=1_000)
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for raw_key, raw_value in list(value.items())[:50]:
            key = _clean_text(raw_key, limit=200)
            if key:
                cleaned[key] = _clean_value(raw_value, depth=depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean_value(item, depth=depth + 1) for item in value[:100]]
    return _clean_text(value, limit=1_000)


def _clean_mapping(value: object) -> dict[str, object]:
    cleaned = _clean_value(value)
    return cleaned if isinstance(cleaned, dict) else {}


def _sanitize_workflow_state(state: WorkflowState) -> WorkflowState:
    history = tuple(
        cleaned
        for item in state.history[-MAX_WORKFLOW_HISTORY:]
        if isinstance(item, dict)
        and isinstance((cleaned := _clean_mapping(item)), dict)
    )
    return WorkflowState(
        task_id=_clean_text(state.task_id, limit=128),
        checkpoint_id=_clean_text(state.checkpoint_id, limit=64),
        mode=_clean_text(state.mode, limit=40, default="explain"),
        phase=_clean_text(state.phase, limit=40, default="idle"),
        message=_clean_text(
            state.message,
            limit=MAX_WORKFLOW_TEXT_CHARS,
            default="Ready",
        ),
        tests_status=_clean_text(state.tests_status, limit=40, default="not_run"),
        pr_url=_clean_text(state.pr_url, limit=2_000),
        merge_status=_clean_text(
            state.merge_status,
            limit=40,
            default="not_requested",
        ),
        issue_number=state.issue_number,
        blockers=_clean_strings(state.blockers),
        blocker=_clean_text(state.blocker, limit=MAX_WORKFLOW_TEXT_CHARS),
        next_actions=_clean_strings(state.next_actions),
        plan_steps=_clean_strings(state.plan_steps, count=50),
        history=history,
        changed_files=_clean_strings(state.changed_files, count=200),
        last_test=_clean_mapping(state.last_test),
        provider=_clean_mapping(state.provider),
        cost=_clean_mapping(state.cost),
        safety_gates=_clean_mapping(state.safety_gates),
        diff_review=_clean_mapping(state.diff_review),
        updated_at=_clean_text(state.updated_at, limit=64),
    )


def _workflow_state_target(project_root: Path) -> tuple[Path, Path]:
    root = project_root.expanduser().resolve()
    target = state_dir(root) / "gui" / "workflow.json"
    return root, target


def workflow_state_path(project_root: Path) -> Path:
    root, target = _workflow_state_target(project_root)
    with _workflow_lock(target):
        _validate_workflow_target(root, target)
    return target


def _validate_workflow_target(root: Path, target: Path) -> None:
    try:
        _resolved_for_containment(target).relative_to(_resolved_for_containment(root))
    except (OSError, ValueError) as exc:
        raise ValueError("GUI workflow state must remain inside the workspace") from exc


def _resolved_for_containment(path: Path) -> Path:
    """Resolve a path while normalising Windows' optional device prefix.

    During an atomic replace Windows can transiently return ``\\\\?\\C:`` for
    the file while returning ``C:`` for its parent.  Those spellings identify
    the same path, but ``Path.relative_to`` treats them as different drives.
    """

    resolved = str(path.resolve(strict=False))
    if resolved.startswith("\\\\?\\UNC\\"):
        resolved = "\\\\" + resolved[8:]
    elif resolved.startswith("\\\\?\\"):
        resolved = resolved[4:]
    return Path(resolved)


def save_workflow_state(project_root: Path, state: WorkflowState) -> Path:
    root, target = _workflow_state_target(project_root)
    data = state.to_dict()
    with _workflow_lock(target):
        _validate_workflow_target(root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _validate_workflow_target(root, target)
        with _process_workflow_lock(root, target):
            temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                _replace_with_retry(temporary, target)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    return target


def _replace_with_retry(temporary: Path, target: Path, *, attempts: int = 20) -> None:
    """Atomic replace that tolerates transient Windows sharing denials.

    The advisory lock excludes OPai's own readers/writers, but an antivirus or
    search indexer can briefly hold the freshly written temp file open, making
    ``os.replace`` raise ``PermissionError``. Retry briefly for that external
    case only; a persistent denial still raises.
    """
    for attempt in range(max(1, attempts)):
        try:
            temporary.replace(target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


def load_workflow_state(project_root: Path) -> WorkflowState:
    root, target = _workflow_state_target(project_root)
    with _workflow_lock(target):
        _validate_workflow_target(root, target)
        if not target.parent.is_dir():
            # No state directory yet: nothing to read and nothing to lock.
            return WorkflowState()
        try:
            # Reads share the writers' cross-process lock so a Windows
            # ``replace()`` never races an open reader handle (PermissionError)
            # and a reader never observes the transient missing-file window as
            # silent empty state (#313).
            with _process_workflow_lock(root, target):
                data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return WorkflowState()
    if not isinstance(data, dict):
        return WorkflowState()
    try:
        schema_version = int(data.get("schema_version") or 1)
    except (TypeError, ValueError, OverflowError):
        return WorkflowState()
    if schema_version > WORKFLOW_SCHEMA_VERSION:
        return WorkflowState()

    issue_number: int | None = None
    if data.get("issue_number") is not None:
        try:
            issue_number = int(data["issue_number"])
        except (TypeError, ValueError, OverflowError):
            issue_number = None
    candidate = WorkflowState(
        task_id=str(data.get("task_id") or ""),
        checkpoint_id=str(data.get("checkpoint_id") or ""),
        mode=str(data.get("mode") or "explain"),
        phase=str(data.get("phase") or "idle"),
        message=str(data.get("message") or "Ready"),
        tests_status=str(data.get("tests_status") or "not_run"),
        pr_url=str(data.get("pr_url") or ""),
        merge_status=str(data.get("merge_status") or "not_requested"),
        issue_number=issue_number,
        blockers=tuple(data.get("blockers") or ())
        if isinstance(data.get("blockers"), (list, tuple))
        else (),
        blocker=str(data.get("blocker") or ""),
        next_actions=tuple(data.get("next_actions") or ())
        if isinstance(data.get("next_actions"), (list, tuple))
        else (),
        plan_steps=tuple(data.get("plan_steps") or ())
        if isinstance(data.get("plan_steps"), (list, tuple))
        else (),
        history=tuple(
            dict(item)
            for item in (
                data.get("history") if isinstance(data.get("history"), list) else []
            )[-100:]
            if isinstance(item, dict)
        ),
        changed_files=tuple(data.get("changed_files") or ())
        if isinstance(data.get("changed_files"), (list, tuple))
        else (),
        last_test=dict(data.get("last_test") or {})
        if isinstance(data.get("last_test"), dict)
        else {},
        provider=dict(data.get("provider") or {})
        if isinstance(data.get("provider"), dict)
        else {},
        cost=dict(data.get("cost") or {}) if isinstance(data.get("cost"), dict) else {},
        safety_gates=dict(data.get("safety_gates") or {})
        if isinstance(data.get("safety_gates"), dict)
        else {},
        diff_review=dict(data.get("diff_review") or {})
        if isinstance(data.get("diff_review"), dict)
        else {},
        updated_at=str(data.get("updated_at") or ""),
    )
    return _sanitize_workflow_state(candidate)


def relink_workflow_checkpoint(
    project_root: Path,
    *,
    checkpoint_id: str,
    changed_files: tuple[str, ...] = (),
) -> WorkflowState:
    """Link post-edit evidence unless a different turn is already active.

    The outer per-target lock makes the load/check/save sequence atomic for GUI
    workers in this process. A late build result may finalize its own immutable
    checkpoint, but it must never replace a newer workflow's checkpoint link.
    """

    root, target = _workflow_state_target(project_root)
    with _workflow_lock(target):
        current = load_workflow_state(root)
        if current.checkpoint_id and current.checkpoint_id != checkpoint_id:
            return current
        linked = replace(
            current,
            checkpoint_id=checkpoint_id,
            changed_files=changed_files,
        )
        save_workflow_state(root, linked)
        return linked


def clear_workflow_state(project_root: Path) -> bool:
    """Forget only active workflow UI state; checkpoints remain evidence."""

    root, target = _workflow_state_target(project_root)
    with _workflow_lock(target):
        _validate_workflow_target(root, target)
        target.unlink(missing_ok=True)
    return True
