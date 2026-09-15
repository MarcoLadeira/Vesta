"""Guarded-workflow contract engine (issue #39).

Standardizes how Vesta keeps autonomy bounded: a shared contract (path locks,
evidence artifacts, stop conditions, permission boundaries) plus a fail-closed
gate for risky actions and a consistent, hashable evidence packet.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .loader import hub_root, load_registry
from .sandbox import classify_command
from .state import state_dir


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_contract(project_root: Path | None = None) -> dict[str, Any]:
    path = hub_root(project_root) / "workflows" / "guarded-contract.yaml"
    if not path.exists():
        return {
            "required_fields": [
                "id",
                "permission_boundaries",
                "path_locks",
                "evidence_artifacts",
                "stop_conditions",
                "fail_closed",
            ],
            "fail_closed_actions": ["git push", "deploy", "release publish"],
            "default_path_locks": {"forbidden_paths": [".git", ".env"]},
        }
    data = load_registry(path)
    return data if isinstance(data, dict) else {}


def load_templates(project_root: Path | None = None) -> dict[str, Any]:
    path = hub_root(project_root) / "workflows" / "templates.yaml"
    if not path.exists():
        return {"templates": [], "reference_implementation": None}
    data = load_registry(path)
    return data if isinstance(data, dict) else {"templates": []}


def get_template(project_root: Path, template_id: str) -> dict[str, Any] | None:
    for template in load_templates(project_root).get("templates", []):
        if template.get("id") == template_id:
            return template
    return None


def validate_workflow(
    workflow: dict[str, Any], project_root: Path | None = None
) -> dict[str, Any]:
    """Check a workflow declares every field the guarded contract requires."""
    contract = load_contract(project_root)
    required = contract.get("required_fields", [])
    missing = [field for field in required if not workflow.get(field)]
    return {
        "id": workflow.get("id", "<unknown>"),
        "ok": not missing,
        "missing_fields": missing,
        "required_fields": required,
    }


def validate_all_templates(project_root: Path) -> dict[str, Any]:
    templates = load_templates(project_root).get("templates", [])
    results = [validate_workflow(template, project_root) for template in templates]
    return {
        "ok": all(item["ok"] for item in results),
        "count": len(results),
        "reference_implementation": load_templates(project_root).get(
            "reference_implementation"
        ),
        "results": results,
    }


def _forbidden_globs(project_root: Path, template: dict[str, Any] | None) -> list[str]:
    contract = load_contract(project_root)
    globs = list(contract.get("default_path_locks", {}).get("forbidden_paths", []))
    if template:
        globs.extend(template.get("path_locks", {}).get("forbidden", []))
    return globs


def check_path_lock(
    project_root: Path, target_path: str, template_id: str | None = None
) -> dict[str, Any]:
    """Return whether writing target_path violates the path locks (fail closed)."""
    template = get_template(project_root, template_id) if template_id else None
    normalized = str(target_path).replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    segments = [segment for segment in normalized.split("/") if segment]
    for pattern in _forbidden_globs(project_root, template):
        pat = str(pattern).replace("\\", "/")
        core = pat.strip("*/")
        if (
            fnmatch.fnmatch(normalized, pat)
            or any(fnmatch.fnmatch(segment, pat) for segment in segments)
            or (core and (core in segments or core in normalized))
        ):
            return {
                "path": target_path,
                "allowed": False,
                "matched_forbidden": pattern,
                "reason": "Path is forbidden by the guarded-workflow contract.",
            }
    return {"path": target_path, "allowed": True}


def guard_action(
    project_root: Path,
    action: str,
    *,
    template_id: str | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Fail-closed gate for a risky action.

    Risky actions (push/deploy/submit/publish) are denied unless explicitly
    confirmed. The sandbox command policy is also consulted so denied commands
    stay denied.
    """
    contract = load_contract(project_root)
    template = get_template(project_root, template_id) if template_id else None
    fail_closed = list(contract.get("fail_closed_actions", []))
    if template:
        fail_closed.extend(template.get("fail_closed", []))

    lowered = action.lower()
    is_risky = any(token.lower() in lowered for token in fail_closed)
    sandbox = classify_command(action, project_root)

    if sandbox["decision"] == "deny":
        decision = "deny"
        reason = sandbox["reason"]
    elif is_risky and not confirmed:
        decision = "deny"
        reason = "Risky action blocked by fail-closed contract; explicit confirmation required."
    elif is_risky and confirmed:
        decision = "confirm"
        reason = "Risky action permitted only with recorded human confirmation."
    elif sandbox["decision"] == "confirm":
        decision = "confirm"
        reason = sandbox["reason"]
    else:
        decision = "allow"
        reason = "No fail-closed rule matched."

    return {
        "action": action,
        "decision": decision,
        "allowed": decision == "allow",
        "fail_closed": is_risky,
        "confirmed": confirmed,
        "reason": reason,
    }


def build_evidence_packet(
    project_root: Path,
    workflow_id: str,
    *,
    checks: list[dict[str, Any]] | None = None,
    write: bool = True,
    sign: bool = False,
) -> dict[str, Any]:
    """Produce a consistent, hashable evidence packet for a guarded run.

    With ``sign=True`` the packet carries an HMAC signature (tamper-evidence).
    """
    root = project_root.expanduser().resolve()
    template = get_template(root, workflow_id)
    validation = (
        validate_workflow(template, root)
        if template
        else {"ok": False, "missing_fields": ["template_not_found"]}
    )
    checks = checks or []
    body = {
        "report": "vesta-guarded-evidence",
        "workflow_id": workflow_id,
        "created_at": _now_iso(),
        "project": str(root),
        "contract_valid": validation["ok"],
        "permission_boundaries": (template or {}).get("permission_boundaries"),
        "path_locks": (template or {}).get("path_locks"),
        "stop_conditions": (template or {}).get("stop_conditions", []),
        "fail_closed": (template or {}).get("fail_closed", []),
        "checks": checks,
    }
    packet_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    packet = {**body, "packet_sha256": packet_hash}

    if sign:
        from .signing import sign as sign_payload

        packet = sign_payload(root, packet)

    if write:
        path = state_dir(root) / "evidence" / f"{workflow_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        packet["evidence_path"] = str(path)
    return packet


def verify_evidence_packet(
    project_root: Path, packet: dict[str, Any]
) -> dict[str, Any]:
    """Verify an evidence packet's content hash and signature (if present)."""
    root = project_root.expanduser().resolve()
    body = {
        key: value
        for key, value in packet.items()
        if key not in {"packet_sha256", "signature", "evidence_path"}
    }
    recomputed = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    hash_ok = recomputed == packet.get("packet_sha256")

    result: dict[str, Any] = {
        "hash_ok": hash_ok,
        "hash_reason": "content hash matches"
        if hash_ok
        else "content hash mismatch (tampered)",
    }
    if "signature" in packet:
        from .signing import verify as verify_payload

        signature_result = verify_payload(root, packet)
        result["signature"] = signature_result
        result["verified"] = hash_ok and signature_result.get("verified", False)
    else:
        result["signature"] = {"verified": False, "reason": "unsigned packet"}
        result["verified"] = hash_ok
    return result
