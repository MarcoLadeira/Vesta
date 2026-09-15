from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .state import state_dir


def _team_path(project_root: Path) -> Path:
    return state_dir(project_root) / "team.json"


def init_team(project_root: Path, mode: str = "solo") -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    data = {
        "schema_version": 1,
        "mode": mode,
        "members": [],
        "cloud_sync_enabled": False,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "notes": "Team and cloud sync are explicit opt-in features. They are disabled by default.",
    }
    path = _team_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data | {"path": str(path)}


def team_report(project_root: Path) -> dict[str, Any]:
    """Governance rollup for a team lead: savings, policy, audit, conformance.

    Answers the strategy's key question: who routed what, did it follow policy,
    what spend was avoided, and is the audit trail intact. Local and read-only.
    """
    from .audit import summarize_audit
    from .ledger import summarize_ledger
    from .policy import resolve_policy
    from .runs import recent_runs
    from .team_policy import load_team_policy, validate_against_team_policy

    root = project_root.expanduser().resolve()
    ledger = summarize_ledger(root)
    audit = summarize_audit(root)
    resolved = resolve_policy(root)
    has_team_policy = load_team_policy(root) is not None
    conformance = (
        validate_against_team_policy(root)
        if has_team_policy
        else {"ok": None, "reason": "no team policy"}
    )
    runs = recent_runs(root, limit=10_000)

    return {
        "report": "opai-team-report",
        "project": str(root),
        "policy": {
            "active_profile": resolved["profile"],
            "has_team_policy": has_team_policy,
            "conforms": conformance.get("ok"),
            "violations": conformance.get("violations", []),
        },
        "savings": {
            "estimated_savings_usd": ledger["estimated_savings_usd"],
            "cloud_calls_avoided": ledger["cloud_calls_avoided"],
            "routed_tasks": ledger["route_count"],
            "route_runs_recorded": len(runs),
        },
        "governance": {
            "audit_events": audit["event_count"],
            "denied_actions": audit["denied_actions"],
            "audit_chain_valid": audit["chain"]["ok"],
        },
        "notes": [
            "All figures are local and private; nothing is transmitted.",
            "Run 'vesta policy check' in CI to enforce the team policy.",
        ],
    }


def cloud_status(project_root: Path) -> dict[str, Any]:
    path = _team_path(project_root.expanduser().resolve())
    if not path.exists():
        return {
            "enabled": False,
            "reason": "No team configuration exists.",
            "requires_confirmation": True,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "enabled": False,
            "reason": "Team configuration is unreadable.",
            "requires_confirmation": True,
        }
    enabled = bool(data.get("cloud_sync_enabled"))
    return {
        "enabled": enabled,
        "mode": data.get("mode", "solo"),
        "requires_confirmation": not enabled,
        "reason": "Cloud sync is opt-in and disabled by default."
        if not enabled
        else "Cloud sync is enabled.",
    }
