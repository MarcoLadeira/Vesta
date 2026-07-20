"""Recent chat prompts — the sidebar 'chat history' (#145).

Privacy contract: chat history is local, per-workspace, redacted, and
clearable. Prompts typed in one workspace never appear in another; entries
pass through the shared secret scrubber before touching disk; and the
legacy *global* ``gui_recents.json`` (verbatim prompts from every
workspace mixed together) is deleted on first use rather than migrated,
because its entries cannot be attributed to a workspace safely.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator

MAX_RECENTS = 12
THREAD_SCHEMA_VERSION = 1
MAX_THREAD_MESSAGES = 40
MAX_THREAD_TEXT_CHARS = 12_000
MAX_THREAD_TOTAL_CHARS = 64_000
MAX_PLAN_STEPS = 50
MAX_RESUME_CONTEXT_CHARS = 12_000

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
# "partial", "blocked", "timeout" carry the completion verdict (#402) into the
# persisted thread so a non-completed run is never coerced to "complete" (or, via
# the old whitelist, to "failed"). See opai.gui_web._thread_status_for.
_THREAD_STATUSES = {
    "complete",
    "partial",
    "blocked",
    "timeout",
    "pending",
    "failed",
    "cancelled",
    "interrupted",
}
_PLAN_STATUSES = {"pending", "in_progress", "completed", "blocked"}


def legacy_recents_path() -> Path:
    """The pre-#145 global history file; only ever looked at to delete it."""

    return Path.home() / ".opai" / "gui_recents.json"


def _workspace_key(workspace_root: str | Path) -> str:
    resolved = str(Path(workspace_root).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def recents_path(workspace_root: str | Path) -> Path:
    return Path.home() / ".opai" / "recents" / f"{_workspace_key(workspace_root)}.json"


def _thread_target(workspace_root: str | Path) -> tuple[Path, Path]:
    from opaihub.state import state_dir

    root = Path(workspace_root).expanduser().resolve()
    return root, state_dir(root) / "gui" / "thread.json"


def thread_path(workspace_root: str | Path) -> Path:
    """The resumable thread belongs to this repository, never global state."""

    root, target = _thread_target(workspace_root)
    with _thread_lock(target):
        _validate_thread_target(root, target)
    return target


def _validate_thread_target(root: Path, target: Path) -> None:
    try:
        _resolved_for_containment(target).relative_to(_resolved_for_containment(root))
    except (OSError, ValueError) as exc:
        raise ValueError("GUI thread state must remain inside the workspace") from exc


def _resolved_for_containment(path: Path) -> Path:
    """Resolve a path while normalising Windows' optional device prefix."""

    resolved = str(path.resolve(strict=False))
    if resolved.startswith("\\\\?\\UNC\\"):
        resolved = "\\\\" + resolved[8:]
    elif resolved.startswith("\\\\?\\"):
        resolved = resolved[4:]
    return Path(resolved)


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


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
def _process_thread_lock(
    root: Path,
    target: Path,
    *,
    timeout_seconds: float = 60.0,
) -> Iterator[None]:
    """Serialize one thread-state path across processes and survive crashes."""

    lock_path = target.with_name(f"{target.name}.lock")
    _validate_thread_target(root, lock_path)
    handle = lock_path.open("a+b")
    acquired = False
    try:
        _validate_thread_target(root, lock_path)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while not (acquired := _try_process_lock(handle)):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for GUI thread lock: {target}")
            time.sleep(0.01)
        _validate_thread_target(root, target)
        _validate_thread_target(root, lock_path)
        yield
    finally:
        try:
            if acquired:
                _unlock_process_file(handle)
        finally:
            handle.close()


@contextmanager
def _thread_transaction(root: Path, target: Path) -> Iterator[None]:
    """Hold the in-process and cross-process locks for one state transaction."""

    with _thread_lock(target):
        _validate_thread_target(root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _validate_thread_target(root, target)
        with _process_thread_lock(root, target):
            yield


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_text(value: Any, *, limit: int) -> str:
    from opaihub.command_runner import redact

    return redact(str(value or "")).strip()[: max(0, int(limit))]


def _clean_messages(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    candidates: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        text = _clean_text(item.get("text"), limit=MAX_THREAD_TEXT_CHARS)
        if not text:
            continue
        status = str(item.get("status") or "complete").strip().lower()
        if status not in _THREAD_STATUSES:
            status = "complete"
        timestamp = str(item.get("timestamp") or "").strip()[:64] or _now()
        candidates.append(
            {
                "role": role,
                "text": text,
                "status": status,
                "timestamp": timestamp,
            }
        )

    # Keep the newest turns under both count and total persisted-text budgets.
    kept: list[dict[str, str]] = []
    remaining = MAX_THREAD_TOTAL_CHARS
    for item in reversed(candidates[-MAX_THREAD_MESSAGES:]):
        if remaining <= 0:
            break
        text = item["text"][:remaining]
        if not text:
            continue
        kept.append({**item, "text": text})
        remaining -= len(text)
    return list(reversed(kept))


def _clean_plan(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[dict[str, str]] = []
    for item in value[:MAX_PLAN_STEPS]:
        if isinstance(item, dict):
            step = _clean_text(item.get("step"), limit=500)
            status = str(item.get("status") or "pending").strip().lower()
        else:
            step = _clean_text(item, limit=500)
            status = "pending"
        if not step:
            continue
        if status not in _PLAN_STATUSES:
            status = "pending"
        out.append({"step": step, "status": status})
    return out


def _clean_changed_files(workspace_root: Path, value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value[:200]:
        raw = _clean_text(item, limit=500).replace("\\", "/")
        if not raw:
            continue
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                raw = candidate.resolve().relative_to(workspace_root).as_posix()
            except (OSError, ValueError):
                continue
        if raw not in out:
            out.append(raw)
    return out


def _clean_id(value: Any, *, limit: int = 128) -> str:
    clean = _clean_text(value, limit=limit)
    return clean if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", clean) else ""


def _thread_payload(
    root: Path,
    *,
    task_id: str,
    mode: str,
    messages: Any,
    checkpoint_id: str = "",
    plan: Any = (),
    changed_files: Any = (),
    active_request_id: str = "",
    state: str = "idle",
) -> dict[str, Any]:
    return {
        "schema_version": THREAD_SCHEMA_VERSION,
        "task_id": _clean_id(task_id),
        "mode": _clean_text(mode, limit=40) or "safe-auto",
        "messages": _clean_messages(messages),
        "checkpoint_id": _clean_id(checkpoint_id, limit=64),
        "plan": _clean_plan(plan),
        "changed_files": _clean_changed_files(root, changed_files),
        "active_request_id": _clean_id(active_request_id, limit=128),
        "state": (
            str(state)
            if str(state) in {"idle", "running", "complete", "interrupted"}
            else "idle"
        ),
        "updated_at": _now(),
    }


def _write_thread_payload(
    root: Path,
    target: Path,
    payload: dict[str, Any],
) -> None:
    """Replace thread state atomically using a unique same-directory file."""

    _validate_thread_target(root, target)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            _validate_thread_target(root, temporary)
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _validate_thread_target(root, target)
        _validate_thread_target(root, temporary)
        temporary.replace(target)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _load_thread_unlocked(root: Path, target: Path) -> dict[str, Any]:
    """Load and normalize state while the caller holds the transaction lock."""

    _validate_thread_target(root, target)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("schema_version") != THREAD_SCHEMA_VERSION:
        return {}
    if not isinstance(raw.get("messages"), list):
        return {}
    messages = _clean_messages(raw["messages"])
    if not messages:
        return {}
    return {
        "schema_version": THREAD_SCHEMA_VERSION,
        "task_id": _clean_id(raw.get("task_id")),
        "mode": _clean_text(raw.get("mode"), limit=40) or "safe-auto",
        "messages": messages,
        "checkpoint_id": _clean_id(raw.get("checkpoint_id"), limit=64),
        "plan": _clean_plan(raw.get("plan")),
        "changed_files": _clean_changed_files(root, raw.get("changed_files")),
        "active_request_id": _clean_id(raw.get("active_request_id"), limit=128),
        "state": (
            str(raw.get("state"))
            if str(raw.get("state")) in {"idle", "running", "complete", "interrupted"}
            else "idle"
        ),
        "updated_at": str(raw.get("updated_at") or "")[:64],
    }


def save_thread(
    workspace_root: str | Path,
    *,
    task_id: str,
    mode: str,
    messages: Any,
    checkpoint_id: str = "",
    plan: Any = (),
    changed_files: Any = (),
    active_request_id: str = "",
    state: str = "idle",
) -> Path:
    """Atomically persist only the safe, bounded fields needed to resume."""

    root, target = _thread_target(workspace_root)
    payload = _thread_payload(
        root,
        task_id=task_id,
        mode=mode,
        messages=messages,
        checkpoint_id=checkpoint_id,
        plan=plan,
        changed_files=changed_files,
        active_request_id=active_request_id,
        state=state,
    )
    with _thread_transaction(root, target):
        _write_thread_payload(root, target, payload)
    return target


def load_thread(workspace_root: str | Path) -> dict[str, Any]:
    """Load and revalidate a thread; corrupt/future state safely means none."""

    root, target = _thread_target(workspace_root)
    with _thread_lock(target):
        _validate_thread_target(root, target)
        if not target.exists():
            return {}
        with _process_thread_lock(root, target):
            return _load_thread_unlocked(root, target)


def normalize_resume_execution_context(value: Any) -> dict[str, Any]:
    """Return a redacted, provider-safe subset of a persisted GUI thread.

    The execution packet is deliberately smaller than the durable thread. It
    keeps the newest useful dialogue plus the active plan while leaving enough
    fixed overhead that the serialized packet cannot exceed its hard budget.
    """

    if not isinstance(value, dict):
        return {}
    messages = _clean_messages(value.get("messages"))[-16:]
    if not messages:
        return {}

    message_budget = 7_000
    bounded_messages: list[dict[str, str]] = []
    for item in reversed(messages):
        if message_budget <= 0:
            break
        text = item["text"][: min(2_000, message_budget)]
        if not text:
            continue
        bounded_messages.append(
            {
                "role": item["role"],
                "text": text,
                "status": item["status"],
                "timestamp": item["timestamp"],
            }
        )
        message_budget -= len(text)
    bounded_messages.reverse()

    plan_budget = 2_000
    bounded_plan: list[dict[str, str]] = []
    for item in _clean_plan(value.get("plan"))[:20]:
        if plan_budget <= 0:
            break
        step = item["step"][: min(300, plan_budget)]
        if step:
            bounded_plan.append({"step": step, "status": item["status"]})
            plan_budget -= len(step)

    changed_budget = 1_000
    bounded_changed: list[str] = []
    for item in value.get("changed_files") or ():
        if changed_budget <= 0 or len(bounded_changed) >= 20:
            break
        path = _clean_text(item, limit=min(300, changed_budget)).replace("\\", "/")
        if path and path not in bounded_changed:
            bounded_changed.append(path)
            changed_budget -= len(path)

    context = {
        "messages": bounded_messages,
        "plan": bounded_plan,
        "checkpoint_id": _clean_id(value.get("checkpoint_id"), limit=64),
        "changed_files": bounded_changed,
    }

    # Defensive final fit in case JSON escaping or many small records consume
    # more overhead than expected. Prefer the plan and newest dialogue.
    def serialized_size() -> int:
        return len(json.dumps(context, ensure_ascii=False, sort_keys=True))

    while serialized_size() > MAX_RESUME_CONTEXT_CHARS:
        if context["changed_files"]:
            context["changed_files"].pop()
        elif len(context["messages"]) > 1:
            context["messages"].pop(0)
        elif context["plan"]:
            context["plan"].pop()
        else:
            text = context["messages"][0]["text"]
            excess = serialized_size() - MAX_RESUME_CONTEXT_CHARS
            context["messages"][0]["text"] = text[: max(1, len(text) - excess - 1)]
    return context


def resume_execution_context(workspace_root: str | Path) -> dict[str, Any]:
    """Load bounded context only after the UI's explicit Resume action."""

    return normalize_resume_execution_context(load_thread(workspace_root))


def clear_thread(workspace_root: str | Path) -> bool:
    root, target = _thread_target(workspace_root)
    with _thread_transaction(root, target):
        target.unlink(missing_ok=True)
    return True


def begin_thread_turn(
    workspace_root: str | Path,
    *,
    request_id: str,
    text: str,
    mode: str,
) -> dict[str, Any]:
    """Durably record the user turn before work starts."""

    root, target = _thread_target(workspace_root)
    with _thread_transaction(root, target):
        current = _load_thread_unlocked(root, target)
        messages = list(current.get("messages") or [])
        messages.append(
            {
                "role": "user",
                "text": text,
                "status": "complete",
                "timestamp": _now(),
            }
        )
        payload = _thread_payload(
            root,
            task_id=str(current.get("task_id") or request_id),
            mode=mode,
            messages=messages,
            checkpoint_id=str(current.get("checkpoint_id") or ""),
            plan=current.get("plan") or (),
            changed_files=current.get("changed_files") or (),
            active_request_id=request_id,
            state="running",
        )
        _write_thread_payload(root, target, payload)
        return _load_thread_unlocked(root, target)


def finish_thread_turn(
    workspace_root: str | Path,
    *,
    request_id: str,
    answer: str,
    status: str,
    task_id: str = "",
    mode: str = "",
    checkpoint_id: str = "",
    plan: Any = (),
    changed_files: Any = (),
) -> dict[str, Any]:
    """Finalize only the active request, preventing stale replies from winning."""

    root, target = _thread_target(workspace_root)
    with _thread_transaction(root, target):
        current = _load_thread_unlocked(root, target)
        if not current or current.get("active_request_id") != _clean_id(request_id):
            return current
        messages = list(current.get("messages") or [])
        messages.append(
            {
                "role": "assistant",
                "text": answer or "The request ended without a response.",
                "status": status if status in _THREAD_STATUSES else "failed",
                "timestamp": _now(),
            }
        )
        payload = _thread_payload(
            root,
            task_id=task_id or str(current.get("task_id") or request_id),
            mode=mode or str(current.get("mode") or "safe-auto"),
            messages=messages,
            checkpoint_id=checkpoint_id or str(current.get("checkpoint_id") or ""),
            plan=plan or current.get("plan") or (),
            changed_files=changed_files or current.get("changed_files") or (),
            state="complete",
        )
        _write_thread_payload(root, target, payload)
        return _load_thread_unlocked(root, target)


def _discard_legacy_global_history() -> None:
    try:
        legacy_recents_path().unlink(missing_ok=True)
    except OSError:
        pass


def load_recents(workspace_root: str | Path) -> list[str]:
    _discard_legacy_global_history()
    try:
        data = json.loads(recents_path(workspace_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for entry in data:
        text = str(entry).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out[:MAX_RECENTS]


def add_recent(workspace_root: str | Path, text: str) -> list[str]:
    """Record ``text`` as this workspace's most-recent prompt (redacted)."""

    from opaihub.command_runner import redact

    clean = redact(str(text or "").strip().replace("\n", " "))
    if not clean:
        return load_recents(workspace_root)
    current = [p for p in load_recents(workspace_root) if p != clean]
    current.insert(0, clean)
    current = current[:MAX_RECENTS]
    try:
        target = recents_path(workspace_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return current


def clear_recents(workspace_root: str | Path) -> list[str]:
    """Delete this workspace's history (and any legacy global file)."""

    _discard_legacy_global_history()
    recents_path(workspace_root).unlink(missing_ok=True)
    return []
