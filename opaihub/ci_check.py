"""CI policy gate (business strategy: Team Governance layer).

A single fail-closed check teams drop into CI: it verifies the project conforms
to the committed team policy, that cloud/paid models stay confirmation-gated, and
that guarded-workflow templates still satisfy the contract. Exits non-zero on any
violation so a pull request cannot merge with a governance regression.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .guarded import validate_all_templates
from .loader import registry_items
from .team_policy import load_team_policy, validate_against_team_policy


def _check_team_policy(root: Path) -> dict[str, Any]:
    if load_team_policy(root) is None:
        return {
            "name": "team_policy",
            "ok": True,
            "skipped": True,
            "detail": "No opai-team-policy.yaml; run 'opai team init' to enforce one.",
        }
    result = validate_against_team_policy(root)
    return {
        "name": "team_policy",
        "ok": result["ok"],
        "detail": "Project conforms to team policy."
        if result["ok"]
        else f"{len(result['violations'])} violation(s).",
        "violations": result.get("violations", []),
    }


def _is_cloud_or_paid(model: dict[str, Any]) -> bool:
    if str(model.get("provider_type", "")).lower() in {"cloud", "remote", "paid"}:
        return True
    if model.get("requires_api_key"):
        return True
    if str(model.get("cost_level", "")).lower() in {
        "low",
        "medium",
        "high",
        "very-high",
    }:
        return True
    # L2 and above are cloud/escalation tiers in OPai's routing model.
    tier = str(model.get("tier") or model.get("default_model_tier") or "").upper()
    return tier in {"L2", "L3", "L4"}


def _check_cloud_gated(root: Path) -> dict[str, Any]:
    ungated = []
    for model in registry_items("models", root):
        if (
            _is_cloud_or_paid(model)
            and model.get("enabled_by_default")
            and not model.get("confirmation_required")
        ):
            ungated.append(model.get("id"))
    return {
        "name": "cloud_models_gated",
        "ok": not ungated,
        "detail": "All cloud/paid models require confirmation."
        if not ungated
        else f"Ungated cloud models: {ungated}",
        "ungated_models": ungated,
    }


def _check_guarded_contract(root: Path) -> dict[str, Any]:
    result = validate_all_templates(root)
    return {
        "name": "guarded_contract",
        "ok": result["ok"],
        "detail": f"{result['count']} guarded templates valid."
        if result["ok"]
        else "One or more guarded templates miss required fields.",
    }


def run_policy_check(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    checks = [
        _check_team_policy(root),
        _check_cloud_gated(root),
        _check_guarded_contract(root),
    ]
    failed = [check for check in checks if not check["ok"]]
    return {
        "report": "opai-policy-check",
        "project": str(root),
        "ok": not failed,
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "checks": checks,
        "summary": "All governance checks passed."
        if not failed
        else f"{len(failed)} governance check(s) failed.",
    }
