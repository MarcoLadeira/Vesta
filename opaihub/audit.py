"""Tamper-evident governance audit trail (business strategy: Trust layer).

An append-only, hash-chained log of governance events - policy denials, guarded
actions, evidence packets, confirmations - so a team or auditor can answer "what
did the agent do, and did it follow policy?". Each entry hashes the previous
one, so any edit or deletion breaks the chain. Privacy-safe: string fields are
redacted and no raw prompts are stored. Exports can be signed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def _write_checkpoint(project_root: Path, entry: dict[str, Any]) -> Path:
    path = checkpoint_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "schema_version": 1,
        "updated_at": _now_iso(),
        "length": int(entry.get("seq", 0)),
        "head_hash": entry.get("entry_hash", GENESIS),
    }
    path.write_text(
        json.dumps(checkpoint, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
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


def record_audit_event(
    project_root: Path,
    event_type: str,
    *,
    actor: str = "local",
    **fields: Any,
) -> dict[str, Any]:
    """Append a redacted, hash-chained governance event. Append-only write."""
    root = project_root.expanduser().resolve()
    last = _last_entry(root)
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

    path = audit_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    _write_checkpoint(root, entry)
    return entry


def read_audit(project_root: Path, limit: int | None = None) -> list[dict[str, Any]]:
    path = audit_path(project_root.expanduser().resolve())
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if limit is not None:
        lines = lines[-limit:]
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def verify_chain(project_root: Path) -> dict[str, Any]:
    """Verify the audit hash chain is intact (tamper-evidence)."""
    root = project_root.expanduser().resolve()
    path = audit_path(root)
    events: list[dict[str, Any]] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line_no, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return {
                    "ok": False,
                    "length": len(events),
                    "broken_at": line_no - 1,
                    "reason": "malformed audit entry",
                }
            if not isinstance(entry, dict):
                return {
                    "ok": False,
                    "length": len(events),
                    "broken_at": line_no - 1,
                    "reason": "malformed audit entry",
                }
            events.append(entry)
    prev_hash = GENESIS
    for index, entry in enumerate(events):
        if entry.get("prev_hash") != prev_hash:
            return {
                "ok": False,
                "length": len(events),
                "broken_at": index,
                "reason": "prev_hash mismatch",
            }
        recomputed = _entry_hash(prev_hash, entry)
        if recomputed != entry.get("entry_hash"):
            return {
                "ok": False,
                "length": len(events),
                "broken_at": index,
                "reason": "entry_hash mismatch",
            }
        prev_hash = entry["entry_hash"]
    checkpoint = _read_checkpoint(root)
    if checkpoint is not None:
        if checkpoint.get("invalid"):
            return {
                "ok": False,
                "length": len(events),
                "head_hash": prev_hash,
                "reason": "checkpoint invalid",
            }
        expected_length = checkpoint.get("length")
        expected_hash = checkpoint.get("head_hash")
        if expected_length != len(events) or expected_hash != prev_hash:
            return {
                "ok": False,
                "length": len(events),
                "head_hash": prev_hash,
                "checkpoint_length": expected_length,
                "checkpoint_head_hash": expected_hash,
                "reason": "checkpoint mismatch",
            }
    return {"ok": True, "length": len(events), "head_hash": prev_hash}


def summarize_audit(project_root: Path) -> dict[str, Any]:
    events = read_audit(project_root)
    by_type: dict[str, int] = {}
    for event in events:
        by_type[event["event_type"]] = by_type.get(event["event_type"], 0) + 1
    return {
        "event_count": len(events),
        "by_type": dict(sorted(by_type.items())),
        "denied_actions": by_type.get(GUARD_DENY, 0) + by_type.get(POLICY_DENY, 0),
        "chain": verify_chain(project_root),
    }


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
