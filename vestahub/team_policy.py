"""Committable team policy distribution (business strategy: Team layer).

A team policy is a single file committed to the repo (``vesta-team-policy.yaml``)
that pins the cost/safety profile, budgets, and approved MCP servers for everyone
who works in it. Unlike per-project state (under the gitignored ``.vestahub/``),
this file is meant to be shared via version control so a whole team routes under
one policy. ``apply`` sets local state from it; ``validate`` checks the project
conforms (used by the CI gate).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .loader import load_registry
from .policy import resolve_policy, set_profile, tier_value
from .state import effective_mcp_servers


TEAM_POLICY_FILE = "vesta-team-policy.yaml"

_TEMPLATE = """# Vesta team policy (committed and shared via git).
# Apply with:   vesta team apply
# Check in CI:  vesta policy check
schema_version: 1
team: {team}
profile: {profile}
require_confirmation_for_cloud: true
allow_paid: true
# Optional MCP allowlist (ids from the registry). Leave unset to allow any.
# Uncomment to enforce, e.g.:
# approved_mcp_servers: [filesystem, git]
budgets:
  monthly_usd_limit: 50.0
  per_task_hard_limit_usd: 3.0
# Guarded-workflow templates expected before risky actions.
required_guards: []
"""


def team_policy_path(project_root: Path) -> Path:
    from vesta import legacy

    return legacy.repository_file(
        project_root.expanduser().resolve(),
        TEAM_POLICY_FILE,
        legacy.LEGACY_TEAM_POLICY_FILE,
    )


def load_team_policy(project_root: Path) -> dict[str, Any] | None:
    path = team_policy_path(project_root)
    if not path.exists():
        return None
    try:
        data = load_registry(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def init_team_policy(
    project_root: Path, profile: str = "team-safe", team: str = "my-team"
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    path = team_policy_path(root)
    if path.exists():
        return {"status": "exists", "path": str(path), "policy": load_team_policy(root)}
    path.write_text(_TEMPLATE.format(profile=profile, team=team), encoding="utf-8")
    return {"status": "created", "path": str(path), "policy": load_team_policy(root)}


def apply_team_policy(project_root: Path) -> dict[str, Any]:
    """Set local project policy from the committed team policy."""
    root = project_root.expanduser().resolve()
    policy = load_team_policy(root)
    if not policy:
        return {"status": "error", "reason": f"no {TEAM_POLICY_FILE} found"}
    profile = policy.get("profile")
    if not profile:
        return {"status": "error", "reason": "team policy has no 'profile'"}
    overrides: dict[str, Any] = {}
    if "budgets" in policy and isinstance(policy["budgets"], dict):
        overrides["budgets"] = policy["budgets"]
    for key in ("allow_paid", "require_confirmation_for_cloud"):
        if key in policy:
            overrides[key] = policy[key]
    result = set_profile(root, profile, overrides=overrides or None)
    return {
        "status": result.get("status", "error"),
        "applied_profile": profile,
        **result,
    }


def validate_against_team_policy(project_root: Path) -> dict[str, Any]:
    """Check the project conforms to the committed team policy (fail-closed)."""
    root = project_root.expanduser().resolve()
    policy = load_team_policy(root)
    if not policy:
        return {
            "ok": False,
            "reason": f"no {TEAM_POLICY_FILE} found; run 'vesta team init'",
            "violations": ["team_policy_missing"],
        }

    resolved = resolve_policy(root)
    settings = resolved["settings"]
    violations: list[dict[str, Any]] = []

    expected_profile = policy.get("profile")
    if expected_profile and resolved["profile"] != expected_profile:
        violations.append(
            {
                "check": "profile",
                "expected": expected_profile,
                "actual": resolved["profile"],
                "fix": "vesta team apply",
            }
        )

    # Approved MCP allowlist: enabled MCP servers must be a subset.
    approved = policy.get("approved_mcp_servers")
    if isinstance(approved, list):
        approved_set = set(approved)
        enabled = [
            server["id"]
            for server in effective_mcp_servers(root)
            if server.get("effective_enabled")
        ]
        unapproved = [server for server in enabled if server not in approved_set]
        if unapproved:
            violations.append(
                {
                    "check": "approved_mcp_servers",
                    "unapproved_enabled": unapproved,
                    "approved": sorted(approved_set),
                    "fix": "disable the server or add it to approved_mcp_servers",
                }
            )

    # Paid posture must not be looser than the team policy.
    if policy.get("allow_paid") is False and settings.get("allow_paid"):
        violations.append(
            {
                "check": "allow_paid",
                "expected": False,
                "actual": True,
                "fix": "vesta team apply",
            }
        )

    # Budget ceilings must not exceed the team's.
    team_budgets = policy.get("budgets", {})
    local_budgets = settings.get("budgets", {})
    for key in ("monthly_usd_limit", "per_task_hard_limit_usd"):
        team_cap = team_budgets.get(key)
        local_cap = local_budgets.get(key)
        if (
            team_cap is not None
            and local_cap is not None
            and float(local_cap) > float(team_cap)
        ):
            violations.append(
                {
                    "check": f"budget:{key}",
                    "team_cap": team_cap,
                    "local": local_cap,
                    "fix": "vesta team apply",
                }
            )

    return {
        "ok": not violations,
        "team": policy.get("team"),
        "expected_profile": expected_profile,
        "active_profile": resolved["profile"],
        "violations": violations,
    }


def _tier_ceiling_ok(settings: dict[str, Any], max_tier: str) -> bool:
    return tier_value(settings.get("max_tier", "L3")) <= tier_value(max_tier)
