from __future__ import annotations

from pathlib import Path
from typing import Any

from .loader import hub_root, load_registry
from .state import load_state, save_state


TIER_ORDER = ["L0", "L1", "L2", "L3", "L4"]

# Used when policy_profiles.yaml is missing. Mirrors solo-balanced.
_FALLBACK_PROFILE: dict[str, Any] = {
    "description": "Built-in balanced fallback profile.",
    "max_tier": "L3",
    "confirmation_required_at_or_above": "L2",
    "require_confirmation_for_cloud": True,
    "allow_paid": True,
    "block_destructive": False,
    "require_evidence": False,
    "oversized_context_tokens": 18000,
    "budgets": {
        "monthly_usd_limit": 20.0,
        "daily_usd_limit": 2.0,
        "per_task_soft_limit_usd": 0.5,
        "per_task_hard_limit_usd": 2.0,
    },
}

_SEVERITY = {"allow": 0, "confirm": 1, "deny": 2}


def tier_value(tier: str) -> int:
    try:
        return TIER_ORDER.index(str(tier).upper())
    except ValueError:
        return 1


def load_profiles(project_root: Path | None = None) -> dict[str, Any]:
    path = hub_root(project_root) / "model-intelligence" / "policy_profiles.yaml"
    if not path.exists():
        return {
            "default_profile": "solo-balanced",
            "profiles": {"solo-balanced": dict(_FALLBACK_PROFILE)},
        }
    try:
        data = load_registry(path)
    except Exception:
        data = None
    if not isinstance(data, dict) or "profiles" not in data:
        return {
            "default_profile": "solo-balanced",
            "profiles": {"solo-balanced": dict(_FALLBACK_PROFILE)},
        }
    return data


def list_profiles(project_root: Path | None = None) -> list[str]:
    return sorted(load_profiles(project_root).get("profiles", {}).keys())


def _project_policy(project_root: Path) -> dict[str, Any]:
    state = load_state(project_root)
    policy = state.get("policy")
    return policy if isinstance(policy, dict) else {}


def resolve_policy(project_root: Path) -> dict[str, Any]:
    """Resolve the effective policy = profile defaults + per-project overrides."""
    root = project_root.expanduser().resolve()
    catalog = load_profiles(root)
    profiles = catalog.get("profiles", {})
    project_policy = _project_policy(root)

    name = project_policy.get("profile") or catalog.get(
        "default_profile", "solo-balanced"
    )
    profile = dict(profiles.get(name) or _FALLBACK_PROFILE)
    profile.setdefault("budgets", dict(_FALLBACK_PROFILE["budgets"]))

    overrides = project_policy.get("overrides", {})
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key == "budgets" and isinstance(value, dict):
                merged = dict(profile.get("budgets", {}))
                merged.update(value)
                profile["budgets"] = merged
            else:
                profile[key] = value

    return {
        "profile": name,
        "source": "project-override" if project_policy.get("profile") else "default",
        "settings": profile,
        "available_profiles": sorted(profiles.keys()),
    }


def set_profile(
    project_root: Path, profile: str, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    available = list_profiles(root)
    if profile not in available:
        return {
            "status": "error",
            "reason": f"unknown profile '{profile}'",
            "available_profiles": available,
        }
    state = load_state(root)
    policy = state.get("policy") if isinstance(state.get("policy"), dict) else {}
    policy["profile"] = profile
    if overrides is not None:
        policy["overrides"] = overrides
    state["policy"] = policy
    save_state(root, state)
    return {"status": "updated", **resolve_policy(root)}


def evaluate_action(
    project_root: Path,
    *,
    tier: str,
    provider_type: str = "local",
    cost_usd: float = 0.0,
    estimated_tokens: int = 0,
    destructive: bool = False,
    paid: bool | None = None,
) -> dict[str, Any]:
    """Gate a routing/model action against the effective policy.

    Returns a decision of allow / confirm / deny with the most restrictive
    outcome winning. Cloud and paid actions always require at least confirmation
    (issue #16); strict profiles fail closed on destructive actions (#37/#39).
    """
    resolved = resolve_policy(project_root)
    settings = resolved["settings"]
    budgets = settings.get("budgets", {})
    is_cloud = str(provider_type).lower() in {"cloud", "remote", "paid"}
    if paid is None:
        paid = is_cloud

    decision = "allow"
    reasons: list[str] = []

    def escalate(level: str, reason: str) -> None:
        nonlocal decision
        if _SEVERITY[level] > _SEVERITY[decision]:
            decision = level
        reasons.append(reason)

    # Fail-closed handling of destructive actions.
    if destructive:
        if settings.get("block_destructive"):
            escalate("deny", "Destructive action blocked by policy (fail closed).")
        else:
            escalate("confirm", "Destructive action requires explicit confirmation.")

    # Tier ceiling.
    if tier_value(tier) > tier_value(settings.get("max_tier", "L3")):
        escalate(
            "deny",
            f"Tier {str(tier).upper()} exceeds profile max_tier "
            f"{settings.get('max_tier', 'L3')}; override required.",
        )

    # Paid not allowed at all under this profile.
    if paid and not settings.get("allow_paid", True):
        escalate("deny", "Paid/cloud models are disabled by this profile.")

    # Any cloud/paid call requires confirmation regardless of tier (#16).
    if is_cloud and settings.get("require_confirmation_for_cloud", True):
        escalate("confirm", "Cloud/paid model requires explicit confirmation.")

    # Confirmation threshold tier.
    if tier_value(tier) >= tier_value(
        settings.get("confirmation_required_at_or_above", "L3")
    ):
        escalate(
            "confirm",
            f"Tier {str(tier).upper()} is at or above the confirmation threshold.",
        )

    # Budget caps.
    hard = budgets.get("per_task_hard_limit_usd")
    soft = budgets.get("per_task_soft_limit_usd")
    if hard is not None and cost_usd > float(hard):
        escalate(
            "deny", f"Estimated ${cost_usd:.4f} exceeds hard per-task budget ${hard}."
        )
    elif soft is not None and cost_usd > float(soft):
        escalate(
            "confirm",
            f"Estimated ${cost_usd:.4f} exceeds soft per-task budget ${soft}.",
        )

    # Oversized context.
    oversized = settings.get("oversized_context_tokens")
    if oversized is not None and estimated_tokens > int(oversized):
        escalate(
            "confirm",
            f"Context ~{estimated_tokens} tokens exceeds {oversized}; compact or confirm.",
        )

    if not reasons:
        reasons.append("Within policy; no gate triggered.")

    return {
        "profile": resolved["profile"],
        "decision": decision,
        "allowed": decision == "allow",
        "requires_confirmation": decision == "confirm",
        "denied": decision == "deny",
        "tier": str(tier).upper(),
        "provider_type": provider_type,
        "reasons": reasons,
        "require_evidence": bool(settings.get("require_evidence", False)),
    }
