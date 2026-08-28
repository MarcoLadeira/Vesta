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
import uuid
from contextlib import contextmanager

from opaihub import shadow_journal
from opaihub.generated_lifecycle import TERMINAL_STATE_IDS
from opaihub.owner_lease import new_lease, owned_by_this_process
from opaihub.owner_lease import touch as touch_lease

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator

MAX_RECENTS = 12
#: How many finished conversations the sidebar keeps per workspace. Bounded for
#: the same reason the prompt list is: this is local history the user did not
#: ask to accumulate, and it is read on every boot.
MAX_CONVERSATIONS = 20
CONVERSATION_SCHEMA_VERSION = 1
MAX_CONVERSATION_TITLE_CHARS = 120
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
# the old whitelist, to "failed"). See thread_status_for_result() below.
_THREAD_STATUSES = {
    "complete",
    "partial",
    "blocked",
    "timeout",
    "pending",
    "failed",
    "cancelled",
    "needs_attention",
    "interrupted",
}
_PLAN_STATUSES = {"pending", "in_progress", "completed", "blocked"}

# completion_verdict (#378/#402) wins over the legacy status when both are
# present -- a stuck/partial run whose legacy status is still "answered"
# persists with its verdict label, never "complete".
# #618: derived from the #612 terminal states, not written out by hand. The
# hand-written table omitted `needs_attention`, and the omission was not inert:
# an unmapped verdict fell through to the legacy branch below, so a run OPai
# could not verify was persisted as "complete" whenever its legacy status
# happened to be `answered_by_account`. Deriving the map means a terminal state
# added to the schema cannot silently acquire a fall-through meaning.
_VERDICT_THREAD_STATUS = {
    state_id: "complete" if state_id == "completed" else state_id
    for state_id in TERMINAL_STATE_IDS
}
_RUN_RESULT_THREAD_STATUS = dict(_VERDICT_THREAD_STATUS)
_ANSWERED_THREAD_STATUSES = {
    "answered",
    "cache_hit",
    "answered_by_account",
    "answered_by_free_api",
    "answered_locally",
    "applied",
    "no_edits",
}


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


def _clean_lease(value: Any) -> dict[str, Any]:
    """Whitelist a supervisor lease: identity and timestamps only.

    A closed shape by construction — this file is read on every boot, so it
    must never become a place arbitrary structure can be persisted.
    """

    if not isinstance(value, dict):
        return {}
    pid = value.get("pid")
    out: dict[str, Any] = {}
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        out["pid"] = int(pid)
    boot = str(value.get("boot") or "")
    if boot and len(boot) <= 64 and boot.isalnum():
        out["boot"] = boot
    for key in ("acquired_at", "heartbeat_at"):
        stamp = value.get(key)
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            out[key] = float(stamp)
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
    lease: dict[str, Any] | None = None,
    conversation_id: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": THREAD_SCHEMA_VERSION,
        "task_id": _clean_id(task_id),
        # Identifies the *conversation* this thread is, stable across its turns
        # and replaced when a new chat starts. `task_id` cannot do this job: it
        # falls back to the per-turn request id, so archiving on it produced one
        # saved chat per reply, each holding the whole accumulated transcript.
        "conversation_id": _clean_id(conversation_id, limit=64),
        "mode": _clean_text(mode, limit=40) or "safe-auto",
        "messages": _clean_messages(messages),
        "checkpoint_id": _clean_id(checkpoint_id, limit=64),
        "plan": _clean_plan(plan),
        "changed_files": _clean_changed_files(root, changed_files),
        "active_request_id": _clean_id(active_request_id, limit=128),
        # #295 invariant 4 (one active owner): a running turn records the
        # process that owns it and keeps a heartbeat, so a later reader can
        # tell a live sibling window from a crash. Only running turns carry
        # one — a finished thread has no owner to prove alive.
        "lease": _clean_lease(lease) if str(state) == "running" else {},
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
        "conversation_id": _clean_id(raw.get("conversation_id"), limit=64),
        "mode": _clean_text(raw.get("mode"), limit=40) or "safe-auto",
        "messages": messages,
        "checkpoint_id": _clean_id(raw.get("checkpoint_id"), limit=64),
        "plan": _clean_plan(raw.get("plan")),
        "changed_files": _clean_changed_files(root, raw.get("changed_files")),
        "active_request_id": _clean_id(raw.get("active_request_id"), limit=128),
        # Revalidated on read like every other field: a lease is evidence
        # about a process, so it must survive the round trip to be worth
        # anything (#295 invariant 4).
        "lease": _clean_lease(raw.get("lease")),
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
            # Claim ownership at the same moment the turn is recorded as
            # running, so there is never a window where a run looks active
            # with nobody accountable for it (#295 invariant 4).
            lease=new_lease(),
            # A thread with no id yet is a new chat; later turns inherit it, so
            # the whole conversation archives as one entry.
            conversation_id=str(current.get("conversation_id") or "")
            or uuid.uuid4().hex[:16],
        )
        _write_thread_payload(root, target, payload)
        started = _load_thread_unlocked(root, target)
    # Archived as soon as the turn starts, not only when it finishes. The chat
    # then appears in the sidebar the moment it is sent — matching what the old
    # prompt list did — and a question survives a crash mid-answer instead of
    # being recoverable only through the separate resume path.
    archive_conversation(root, started)
    return started


def refresh_thread_lease(workspace_root: str | Path, *, request_id: str) -> bool:
    """Restamp the running turn's lease so it keeps proving the owner alive.

    Returns True when this process refreshed its own lease. A lease that is
    never restamped goes stale mid-run and a healthy run starts looking
    abandoned, so the owning process must beat while it works.

    Refuses to touch a turn that is not running, or one this process does
    not own — keeping someone else's lease warm would make a dead owner look
    alive forever, which is the failure the lease exists to catch.
    """

    root, target = _thread_target(workspace_root)
    try:
        with _thread_transaction(root, target):
            current = _load_thread_unlocked(root, target)
            if str(current.get("state") or "") != "running":
                return False
            if _clean_id(current.get("active_request_id"), limit=128) != _clean_id(
                request_id, limit=128
            ):
                return False
            existing = current.get("lease") or {}
            if not owned_by_this_process(existing):
                return False
            payload = _thread_payload(
                root,
                task_id=str(current.get("task_id") or ""),
                mode=str(current.get("mode") or ""),
                messages=current.get("messages") or [],
                checkpoint_id=str(current.get("checkpoint_id") or ""),
                plan=current.get("plan") or (),
                changed_files=current.get("changed_files") or (),
                active_request_id=str(current.get("active_request_id") or ""),
                state="running",
                lease=touch_lease(existing),
                conversation_id=str(current.get("conversation_id") or ""),
            )
            _write_thread_payload(root, target, payload)
            return True
    except (OSError, ValueError):
        # A heartbeat is an optimization on top of the real work; failing to
        # write one must never break the run it is describing.
        return False


def thread_status_for_result(
    status: str, completion_verdict: Any, run_result: Any = None
) -> str:
    """The honest thread status for a finished turn, shared by every surface.

    GUI and CLI turns both finish through this (#545) so "complete" means the
    same thing regardless of which surface ran the turn -- previously this
    lived only in opai.gui_web, reachable by GUI turns alone.

    #618 authority order, strictest first:

    1. the canonical RunResult, when the turn carried one;
    2. the #378 verdict, for records written before the projection existed;
    3. the legacy status string -- compatibility import only, and never able to
       report success.

    Step 3 used to be able to *win*: an unmapped verdict fell through to it, so
    `needs_attention` + `answered_by_account` persisted as "complete". A legacy
    string may now only narrow an unknown result to a failure-shaped one; it can
    no longer manufacture completion that no evidence supports.
    """
    if run_result is not None:
        from opaihub.run_result import terminal_presentation

        canonical = terminal_presentation(run_result)
        return _RUN_RESULT_THREAD_STATUS.get(canonical.state, "needs_attention")
    if isinstance(completion_verdict, dict):
        verdict = str(completion_verdict.get("verdict") or "").strip().lower()
        mapped = _VERDICT_THREAD_STATUS.get(verdict)
        if mapped is not None:
            return mapped
        if verdict:
            # A verdict we do not recognise is an incompatible import, not a
            # success and not a plain failure. Degrading it explicitly is what
            # keeps an unknown value from becoming "complete" by omission.
            return "needs_attention"
    if status in _ANSWERED_THREAD_STATUSES:
        # Compatibility import only: an old record whose sole surviving signal
        # is "the provider answered". That is transport, not engineering
        # completion, so it cannot claim more than "we cannot verify this".
        return "needs_attention"
    return "cancelled" if status == "cancelled" else "failed"


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
            conversation_id=str(current.get("conversation_id") or ""),
        )
        _write_thread_payload(root, target, payload)
        finished = _load_thread_unlocked(root, target)
    # Archive outside the thread transaction: the conversation folder is a
    # different resource, and holding the thread lock across it would let a
    # slow archive write block the next turn from starting.
    archive_conversation(root, finished)
    return finished


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


def clear_recents(
    workspace_root: str | Path, *, preserve_conversation_id: str = ""
) -> list[str]:
    """Delete this workspace's history while optionally retaining one chat."""

    _discard_legacy_global_history()
    recents_path(workspace_root).unlink(missing_ok=True)
    clear_conversations(
        workspace_root, preserve_conversation_id=preserve_conversation_id
    )
    return []


# --------------------------------------------------------------------------- #
# Saved conversations
#
# The sidebar called itself "Recent chats" while storing only prompt *strings*,
# so selecting one re-typed the question and threw the answer away. There was
# exactly one resumable conversation per workspace (``thread.json``), replaced
# in place by the next one — the transcript the user was looking for had
# already been overwritten by the time they went looking.
#
# A conversation is archived on every finished turn rather than when the user
# remembers to press "New chat", because history that depends on the user
# performing a bookkeeping step is history that is missing precisely when it
# matters. Archiving upserts on ``task_id``, so a multi-turn chat stays one
# entry that grows instead of becoming one entry per reply.
# --------------------------------------------------------------------------- #


def conversations_dir(workspace_root: str | Path) -> Path:
    from opaihub.state import state_dir

    root = Path(workspace_root).expanduser().resolve()
    return state_dir(root) / "gui" / "conversations"


def _conversation_target(workspace_root: str | Path, conversation_id: str) -> Path:
    """Resolve one conversation file, refusing any id that escapes the folder.

    ``conversation_id`` reaches this from the front-end, so it is treated as
    untrusted input: only the sanitized id is ever joined to a path, and the
    result is re-checked for containment afterwards.
    """

    clean = _clean_id(conversation_id, limit=64)
    if not clean:
        raise ValueError("conversation id is required")
    folder = conversations_dir(workspace_root)
    target = folder / f"{clean}.json"
    try:
        _resolved_for_containment(target).relative_to(_resolved_for_containment(folder))
    except (OSError, ValueError) as exc:
        raise ValueError("conversation state must stay inside the workspace") from exc
    return target


def _conversation_title(messages: list[dict[str, str]]) -> str:
    """Title a conversation by its first question, the way the user recalls it."""

    for item in messages:
        if item.get("role") == "user":
            text = " ".join(str(item.get("text") or "").split())
            if text:
                return text[:MAX_CONVERSATION_TITLE_CHARS]
    return "Untitled chat"


def _conversation_payload(root: Path, thread: dict[str, Any]) -> dict[str, Any]:
    messages = _clean_messages(thread.get("messages"))
    return {
        "schema_version": CONVERSATION_SCHEMA_VERSION,
        "id": _clean_id(thread.get("conversation_id"), limit=64),
        "title": _conversation_title(messages),
        "mode": _clean_text(thread.get("mode"), limit=40) or "safe-auto",
        "messages": messages,
        "plan": _clean_plan(thread.get("plan")),
        "changed_files": _clean_changed_files(root, thread.get("changed_files")),
        "updated_at": _now(),
        # `updated_at` is second-granularity, so two chats finished in the same
        # second tie and the sidebar order becomes arbitrary. Ordering gets its
        # own precise value rather than making the displayed timestamp noisier.
        "updated_ts": time.time(),
    }


def _valid_conversation_record(record: dict[str, Any]) -> bool:
    """Accept exactly what :func:`_read_conversation` would accept.

    Deliberately the same bar as the legacy reader rather than a stricter one.
    A validator that demanded more than the module itself demands would drop
    records the module considers real -- silent history loss, which is the
    failure #613 exists to remove rather than to add a second copy of.
    """

    return (
        isinstance(record.get("id"), str)
        and bool(record.get("id"))
        and record.get("schema_version") == CONVERSATION_SCHEMA_VERSION
    )


def conversation_shadow_projection(
    workspace_root: str | Path, conversation_id: str
) -> dict[str, Any]:
    """Rebuild one saved conversation from its shadow journal."""

    try:
        target = _conversation_target(workspace_root, conversation_id)
    except ValueError:
        return {}
    return shadow_journal.projection(target, is_valid_record=_valid_conversation_record)


def conversation_contradiction_report(
    workspace_root: str | Path, conversation_id: str
) -> dict[str, Any] | None:
    """``None`` when the saved conversation and its shadow agree, else what differs."""

    try:
        target = _conversation_target(workspace_root, conversation_id)
    except ValueError:
        return None
    return shadow_journal.contradiction_report(
        target,
        lambda: _read_conversation_raw(target),
        is_valid_record=_valid_conversation_record,
        identity={"conversation_id": conversation_id},
    )


def _read_conversation_raw(path: Path) -> dict[str, Any]:
    """Read the file as persisted, without :func:`_read_conversation`'s cleaning.

    ``_read_conversation`` re-cleans messages and drops foreign schemas, which
    is right for the sidebar but would hide the very divergence the
    contradiction report exists to describe.
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def archive_conversation(
    workspace_root: str | Path, thread: dict[str, Any]
) -> dict[str, Any]:
    """Upsert ``thread`` into this workspace's saved conversations.

    Never raises: an archive write failing must not take down the turn that
    just succeeded. The conversation is history, not the result.
    """

    if not isinstance(thread, dict):
        return {}
    root = Path(workspace_root).expanduser().resolve()
    payload = _conversation_payload(root, thread)
    if not payload["id"] or not payload["messages"]:
        return {}
    try:
        target = _conversation_target(workspace_root, payload["id"])
        target.parent.mkdir(parents=True, exist_ok=True)
        # #613 Stage 2: the archive write had no cross-process lock at all --
        # only `save_thread` and friends used `_thread_transaction`. Two
        # concurrent archives of one conversation could interleave, and the
        # mirror would then record an order the file never took. Holding the
        # module's own lock here makes the shadow order-faithful and fixes the
        # underlying race for the legacy file at the same time.
        with _thread_transaction(target.parent, target):
            _write_thread_payload(target.parent, target, payload)
            shadow_journal.record_snapshot(
                target, payload, is_valid_record=_valid_conversation_record
            )
        _prune_conversations(workspace_root)
    except (OSError, ValueError):
        return {}
    return payload


def _conversation_files(workspace_root: str | Path) -> list[Path]:
    folder = conversations_dir(workspace_root)
    try:
        return [p for p in folder.glob("*.json") if p.is_file()]
    except OSError:
        return []


def _read_conversation(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != CONVERSATION_SCHEMA_VERSION
    ):
        return {}
    messages = _clean_messages(raw.get("messages"))
    if not messages:
        return {}
    return {
        "schema_version": CONVERSATION_SCHEMA_VERSION,
        "id": _clean_id(raw.get("id"), limit=64),
        "title": _clean_text(raw.get("title"), limit=MAX_CONVERSATION_TITLE_CHARS)
        or _conversation_title(messages),
        "mode": _clean_text(raw.get("mode"), limit=40) or "safe-auto",
        "messages": messages,
        "plan": _clean_plan(raw.get("plan")),
        "updated_at": str(raw.get("updated_at") or "")[:64],
        "updated_ts": float(raw.get("updated_ts") or 0.0)
        if isinstance(raw.get("updated_ts"), (int, float))
        else 0.0,
    }


def list_conversations(workspace_root: str | Path) -> list[dict[str, Any]]:
    """Saved conversations, newest first, without their message bodies.

    The sidebar only needs enough to choose one, and a transcript per entry
    would be read on every boot for chats the user never opens.
    """

    summaries: list[dict[str, Any]] = []
    for path in _conversation_files(workspace_root):
        record = _read_conversation(path)
        if not record.get("id"):
            continue
        summaries.append(
            {
                "id": record["id"],
                "title": record["title"],
                "mode": record["mode"],
                "message_count": len(record["messages"]),
                "updated_at": record["updated_at"],
                "updated_ts": record["updated_ts"],
            }
        )
    summaries.sort(key=lambda item: item.get("updated_ts") or 0.0, reverse=True)
    return summaries[:MAX_CONVERSATIONS]


def load_conversation(
    workspace_root: str | Path, conversation_id: str
) -> dict[str, Any]:
    """One saved conversation with its full transcript, or ``{}`` if unusable."""

    try:
        target = _conversation_target(workspace_root, conversation_id)
    except ValueError:
        return {}
    if not target.exists():
        return {}
    return _read_conversation(target)


def _prune_conversations(workspace_root: str | Path) -> None:
    """Keep the newest ``MAX_CONVERSATIONS``; drop unreadable files too."""

    entries: list[tuple[str, Path]] = []
    for path in _conversation_files(workspace_root):
        record = _read_conversation(path)
        if not record.get("id"):
            # Corrupt or foreign-schema: it can never be listed or opened, so
            # leaving it would only grow the folder forever.
            with _suppress_os_error():
                path.unlink(missing_ok=True)
            _tombstone(path)
            continue
        entries.append((record.get("updated_ts") or 0.0, path))
    entries.sort(key=lambda item: item[0], reverse=True)
    for _, path in entries[MAX_CONVERSATIONS:]:
        with _suppress_os_error():
            path.unlink(missing_ok=True)
        _tombstone(path)


def _tombstone(path: Path) -> None:
    """Mirror a conversation's removal so the shadow forgets it too.

    #613 Stage 2, and the single most important judgement call in this
    migration. Retention pruning and :func:`clear_conversations` are both
    *deliberate* removals, so the journal records a deletion rather than
    quietly keeping the record.

    Not tombstoning would have been the easier code and two separate bugs.
    Pruning would have left the journal holding conversations past
    ``MAX_CONVERSATIONS``, so Stage 5's cutover to canonical reads would flip
    this workspace's retention from twenty conversations to unbounded without
    anyone deciding that. Worse, ``clear_conversations`` would have left the
    shadow holding chats the user explicitly deleted -- and this module's
    stated privacy contract is that chat history is *clearable*. A shadow that
    outlives an erasure is not a migration detail; it is the erasure failing.
    """

    with _suppress_os_error():
        shadow_journal.record_deletion(path)


def clear_conversations(
    workspace_root: str | Path, *, preserve_conversation_id: str = ""
) -> bool:
    """Delete saved conversations except the explicitly preserved chat."""

    preserved = _clean_id(preserve_conversation_id, limit=64)
    for path in _conversation_files(workspace_root):
        if preserved and path.stem == preserved:
            continue
        with _suppress_os_error():
            path.unlink(missing_ok=True)
        _tombstone(path)
    return True


@contextmanager
def _suppress_os_error() -> Iterator[None]:
    try:
        yield
    except OSError:
        pass
