"""Tamper-evident governance audit trail (business strategy: Trust layer).

An append-only, hash-chained log of governance events - policy denials, guarded
actions, evidence packets, confirmations - so a team or auditor can answer "what
did the agent do, and did it follow policy?". Each entry hashes the previous
one, so any edit or deletion breaks the chain. Privacy-safe: string fields are
redacted and no raw prompts are stored. Exports can be signed.

The audit log and its head checkpoint are updated under one transaction. A
process crash after the durable log append but before checkpoint replacement can
only leave a stale checkpoint; :func:`recover_audit_checkpoint` rebuilds it
after the complete hash chain has been verified. It never repairs a damaged log.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_io import (
    atomic_write_text,
    interprocess_transaction,
    read_utf8_tail_lines,
)
from .command_runner import redact
from .state import state_dir


GENESIS = "0" * 64

# Governance event types.
POLICY_DENY = "policy_deny"
POLICY_CONFIRM = "policy_confirm"
GUARD_DENY = "guard_deny"
GUARD_ALLOW = "guard_allow"
EVIDENCE_PACKET = "evidence_packet"
TEAM_POLICY_APPLIED = "team_policy_applied"
CI_CHECK = "ci_check"
EDITION_CHANGE = "edition_change"
BENCHMARK_RUN = "benchmark_run"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def audit_path(project_root: Path) -> Path:
    return state_dir(project_root) / "audit" / "audit.jsonl"


def checkpoint_path(project_root: Path) -> Path:
    return state_dir(project_root) / "audit" / "head.json"


def _canonical(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def _entry_hash(prev_hash: str, entry: dict[str, Any]) -> str:
    basis = prev_hash + _canonical(entry)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _last_entry(project_root: Path) -> dict[str, Any] | None:
    path = audit_path(project_root)
    if not path.exists():
        return None
    last = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def _tail_entry(project_root: Path) -> dict[str, Any] | None:
    """Read only the final JSONL record, growing to that record's size."""

    path = audit_path(project_root)
    try:
        with path.open("rb") as handle:
            cursor = handle.seek(0, os.SEEK_END)
            data = b""
            while cursor > 0:
                chunk_size = min(8192, cursor)
                cursor -= chunk_size
                handle.seek(cursor)
                data = handle.read(chunk_size) + data
                stripped = data.rstrip(b"\r\n")
                if b"\n" in stripped or cursor == 0:
                    raw = stripped.rsplit(b"\n", 1)[-1].rstrip(b"\r")
                    value = json.loads(raw.decode("utf-8"))
                    return value if isinstance(value, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return None


def _write_checkpoint(project_root: Path, entry: dict[str, Any]) -> Path:
    path = checkpoint_path(project_root)
    try:
        log_size = audit_path(project_root).stat().st_size
    except OSError:
        log_size = 0
    checkpoint = {
        "schema_version": 2,
        "updated_at": _now_iso(),
        "length": int(entry.get("seq", 0)),
        "head_hash": entry.get("entry_hash", GENESIS),
        "log_size": int(log_size),
    }
    atomic_write_text(path, json.dumps(checkpoint, indent=2, sort_keys=True) + "\n")
    return path


def _read_checkpoint(project_root: Path) -> dict[str, Any] | None:
    path = checkpoint_path(project_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"invalid": True}
    return data if isinstance(data, dict) else {"invalid": True}


def _checkpoint_last_entry(project_root: Path) -> dict[str, Any] | None:
    """Return the persisted audit head only when it matches the durable log.

    Normal appends update the JSONL file and checkpoint under the same
    interprocess transaction. Matching the checkpoint's recorded byte size and
    head to the durable final record makes the next append proportional only to
    that record's size. A legacy/stale checkpoint or interrupted write falls
    back to the log scan in :func:`_last_entry`.
    """

    checkpoint = _read_checkpoint(project_root)
    if not isinstance(checkpoint, dict) or checkpoint.get("invalid"):
        return None
    length = checkpoint.get("length")
    head_hash = checkpoint.get("head_hash")
    log_size = checkpoint.get("log_size")
    if (
        isinstance(length, bool)
        or not isinstance(length, int)
        or length < 0
        or not isinstance(head_hash, str)
        or len(head_hash) != 64
        or isinstance(log_size, bool)
        or not isinstance(log_size, int)
        or log_size < 0
    ):
        return None
    try:
        current_size = audit_path(project_root).stat().st_size
    except OSError:
        current_size = 0
    if current_size != log_size:
        return None
    if current_size == 0:
        if length == 0 and head_hash == GENESIS:
            return {"seq": 0, "entry_hash": GENESIS}
        return None
    durable_head = _tail_entry(project_root)
    if (
        durable_head is None
        or durable_head.get("seq") != length
        or durable_head.get("entry_hash") != head_hash
    ):
        return None
    return {"seq": length, "entry_hash": head_hash}


def _append_entry(path: Path, entry: dict[str, Any]) -> None:
    """Append and fsync one entry while the audit transaction is held."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def record_audit_event(
    project_root: Path,
    event_type: str,
    *,
    actor: str = "local",
    **fields: Any,
) -> dict[str, Any]:
    """Append a redacted, hash-chained governance event. Append-only write."""
    root = project_root.expanduser().resolve()
    path = audit_path(root)
    with interprocess_transaction(path):
        last = _checkpoint_last_entry(root) or _last_entry(root)
        prev_hash = last.get("entry_hash", GENESIS) if last else GENESIS
        seq = (last.get("seq", 0) + 1) if last else 1

        entry: dict[str, Any] = {
            "seq": seq,
            "created_at": _now_iso(),
            "event_type": event_type,
            "actor": actor,
            "prev_hash": prev_hash,
        }
        for key, value in fields.items():
            entry[key] = redact(value) if isinstance(value, str) else value
        entry["entry_hash"] = _entry_hash(prev_hash, entry)

        _append_entry(path, entry)
        _write_checkpoint(root, entry)
    return entry


def _read_audit(
    project_root: Path, limit: int | None = None
) -> tuple[list[dict[str, Any]], int]:
    """``(events, skipped)`` — audit events plus the count of malformed entries.

    A damaged JSONL line (torn append, corruption, or a non-object value) is
    unreadable. Dropping it silently lets it vanish from a dashboard as if it
    never happened, so the skipped count is tracked and surfaced as an
    integrity-degraded signal (#474) rather than hidden.
    """

    path = audit_path(project_root.expanduser().resolve())
    if not path.exists():
        return [], 0
    lines = (
        path.read_text(encoding="utf-8", errors="replace").splitlines()
        if limit is None
        else read_utf8_tail_lines(path, limit)
    )
    events: list[dict[str, Any]] = []
    skipped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(value, dict):
            skipped += 1
            continue
        events.append(value)
    return events, skipped


def read_audit(project_root: Path, limit: int | None = None) -> list[dict[str, Any]]:
    return _read_audit(project_root, limit)[0]


def read_recent_audit(
    project_root: Path,
    *,
    event_types: Iterable[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Return recent matching events without materializing an unrelated prefix."""
    wanted = {str(value) for value in event_types}
    target = max(0, int(limit))
    if not wanted or target == 0:
        return []
    path = audit_path(project_root.expanduser().resolve())
    if not path.exists():
        return []

    window = max(64, target * 4)
    previous_line_count = -1
    while True:
        lines = read_utf8_tail_lines(path, window)
        matches: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("event_type") in wanted:
                matches.append(value)
        if len(matches) >= target:
            return matches[-target:]
        line_count = len(lines)
        if line_count < window or line_count == previous_line_count:
            return matches
        previous_line_count = line_count
        window *= 2


def _verify_audit_log(
    project_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verify the audit log without consulting its derived head checkpoint."""

    path = audit_path(project_root)
    events: list[dict[str, Any]] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line_no, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return events, {
                    "ok": False,
                    "length": len(events),
                    "broken_at": line_no - 1,
                    "reason": "malformed audit entry",
                }
            if not isinstance(entry, dict):
                return events, {
                    "ok": False,
                    "length": len(events),
                    "broken_at": line_no - 1,
                    "reason": "malformed audit entry",
                }
            events.append(entry)
    prev_hash = GENESIS
    for index, entry in enumerate(events):
        if entry.get("prev_hash") != prev_hash:
            return events, {
                "ok": False,
                "length": len(events),
                "broken_at": index,
                "reason": "prev_hash mismatch",
            }
        recomputed = _entry_hash(prev_hash, entry)
        if recomputed != entry.get("entry_hash"):
            return events, {
                "ok": False,
                "length": len(events),
                "broken_at": index,
                "reason": "entry_hash mismatch",
            }
        prev_hash = entry["entry_hash"]
    return events, {"ok": True, "length": len(events), "head_hash": prev_hash}


def verify_chain(project_root: Path) -> dict[str, Any]:
    """Verify the audit hash chain and checkpoint as one consistent snapshot."""

    root = project_root.expanduser().resolve()
    with interprocess_transaction(audit_path(root)):
        events, result = _verify_audit_log(root)
        if not result["ok"]:
            return result
        checkpoint = _read_checkpoint(root)
        if checkpoint is None:
            return result
        if checkpoint.get("invalid"):
            return {
                "ok": False,
                "length": result["length"],
                "head_hash": result["head_hash"],
                "reason": "checkpoint invalid",
            }
        expected_length = checkpoint.get("length")
        expected_hash = checkpoint.get("head_hash")
        if expected_length != result["length"] or expected_hash != result["head_hash"]:
            return {
                "ok": False,
                "length": result["length"],
                "head_hash": result["head_hash"],
                "checkpoint_length": expected_length,
                "checkpoint_head_hash": expected_hash,
                "reason": "checkpoint mismatch",
            }
        return result


def recover_audit_checkpoint(project_root: Path) -> dict[str, Any]:
    """Rebuild only a stale audit checkpoint from an intact, durable log.

    This is the crash-recovery path for an append that reached the audit log
    before its atomic checkpoint replacement. A malformed or hash-inconsistent
    log stays failed closed and is never rewritten into an apparently valid head.
    """

    root = project_root.expanduser().resolve()
    with interprocess_transaction(audit_path(root)):
        events, result = _verify_audit_log(root)
        if not result["ok"]:
            return {**result, "recovered": False}
        checkpoint = _read_checkpoint(root)
        stale = (
            checkpoint is None
            or checkpoint.get("invalid")
            or checkpoint.get("length") != result["length"]
            or checkpoint.get("head_hash") != result["head_hash"]
        )
        if stale:
            _write_checkpoint(
                root, events[-1] if events else {"seq": 0, "entry_hash": GENESIS}
            )
        return {**result, "recovered": stale}


_AUDIT_SUMMARY_CACHE: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}
_AUDIT_SUMMARY_CACHE_LOCK = threading.RLock()


def _windows_file_usn(path: Path) -> int | None:
    """Return NTFS's monotonic per-file change generation when available."""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.DeviceIoControl.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.DeviceIoControl.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateFileW(str(path), 0x80, 0x7, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            return None
        try:
            output = ctypes.create_string_buffer(1024)
            returned = wintypes.DWORD()
            ok = kernel32.DeviceIoControl(
                handle,
                0x000900EB,
                None,
                0,
                output,
                len(output),
                ctypes.byref(returned),
                None,
            )
            if not ok or returned.value < 32:
                return None
            return int.from_bytes(output.raw[24:32], "little", signed=True)
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return None


def _audit_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (0, 0, 0)
    except OSError:
        return None
    change = _windows_file_usn(path) if os.name == "nt" else int(stat.st_ctime_ns)
    if change is None:
        return None
    return (int(stat.st_size), int(stat.st_mtime_ns), change)


def clear_audit_summary_cache() -> None:
    with _AUDIT_SUMMARY_CACHE_LOCK:
        _AUDIT_SUMMARY_CACHE.clear()


def summarize_audit(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    path = audit_path(root)
    signature = _audit_signature(path)
    key = str(root)
    with _AUDIT_SUMMARY_CACHE_LOCK:
        cached = _AUDIT_SUMMARY_CACHE.get(key)
    if signature is not None and cached is not None and cached[0] == signature:
        return copy.deepcopy(cached[1])

    events, skipped = _read_audit(root)
    by_type: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or "unknown")
        by_type[event_type] = by_type.get(event_type, 0) + 1
    summary = {
        "event_count": len(events),
        "by_type": dict(sorted(by_type.items())),
        "denied_actions": by_type.get(GUARD_DENY, 0) + by_type.get(POLICY_DENY, 0),
        # #474: malformed entries must not vanish silently — a summary over a
        # damaged log is integrity-degraded, not complete evidence.
        "complete": skipped == 0,
        "degraded": skipped > 0,
        "skipped_events": skipped,
        "chain": verify_chain(root),
    }
    if signature is not None and _audit_signature(path) == signature:
        with _AUDIT_SUMMARY_CACHE_LOCK:
            _AUDIT_SUMMARY_CACHE[key] = (signature, copy.deepcopy(summary))
    return summary


def export_audit(project_root: Path, sign: bool = True) -> dict[str, Any]:
    """Produce an audit bundle for review/compliance, signed by default."""
    root = project_root.expanduser().resolve()
    events = read_audit(root)
    chain = verify_chain(root)
    bundle = {
        "report": "opai-audit-export",
        "generated_at": _now_iso(),
        "project": str(root),
        "event_count": len(events),
        "chain_valid": chain["ok"],
        "by_type": summarize_audit(root)["by_type"],
        "events": events,
    }
    if sign:
        from .signing import sign as sign_payload

        bundle = sign_payload(root, bundle)
    return bundle
