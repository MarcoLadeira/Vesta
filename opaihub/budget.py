"""Hard cost firewall: budgets, accumulation gating, and panic mode (#50).

A reporting dashboard tells you what you spent; this *prevents* avoidable
paid/cloud AI calls. Budgets layer on top of the policy profile: per-project and
per-team ceilings, daily/monthly accumulation read from the local ledger, and a
panic mode that forces deterministic/local-only routing until disabled. The gate
fails closed - `opai budget gate` exits non-zero when the next route would
exceed policy or budget.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cost_model import is_local_tier, load_cost_model
from .ledger import EVENT_MODEL_CALL, EVENT_ROUTE, read_events
from .policy import evaluate_action, resolve_policy
from .state import state_dir


def budget_path(project_root: Path) -> Path:
    return state_dir(project_root) / "budget.json"


def _default_caps(project_root: Path) -> dict[str, Any]:
    settings = resolve_policy(project_root)["settings"]
    budgets = settings.get("budgets", {})
    return {
        "daily_usd_limit": budgets.get("daily_usd_limit"),
        "monthly_usd_limit": budgets.get("monthly_usd_limit"),
        "per_task_hard_limit_usd": budgets.get("per_task_hard_limit_usd"),
        "panic": False,
    }


def load_budget(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    caps = _default_caps(root)
    path = budget_path(root)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                caps.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    return caps


def set_budget(
    project_root: Path,
    *,
    daily_usd: float | None = None,
    monthly_usd: float | None = None,
    per_task_usd: float | None = None,
    panic: bool | None = None,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    caps = load_budget(root)
    if daily_usd is not None:
        caps["daily_usd_limit"] = daily_usd
    if monthly_usd is not None:
        caps["monthly_usd_limit"] = monthly_usd
    if per_task_usd is not None:
        caps["per_task_hard_limit_usd"] = per_task_usd
    if panic is not None:
        caps["panic"] = bool(panic)
    path = budget_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(caps, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "updated", **caps, "path": str(path)}


def _spent(project_root: Path, *, period: str) -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    month = today[:7]
    total = 0.0
    for event in read_events(project_root):
        if event.get("event_type") not in {EVENT_ROUTE, EVENT_MODEL_CALL}:
            continue
        created = str(event.get("created_at", ""))
        if period == "day" and not created.startswith(today):
            continue
        if period == "month" and not created.startswith(month):
            continue
        value = event.get("estimated_actual_usd")
        if isinstance(value, (int, float)):
            total += value
    return round(total, 6)


def budget_status(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    caps = load_budget(root)
    spent_day = _spent(root, period="day")
    spent_month = _spent(root, period="month")

    def remaining(limit: Any, spent: float) -> Any:
        return round(float(limit) - spent, 6) if limit is not None else None

    return {
        "report": "opai-budget-status",
        "project": str(root),
        "panic": bool(caps.get("panic")),
        "profile": resolve_policy(root)["profile"],
        "caps": {
            "daily_usd_limit": caps.get("daily_usd_limit"),
            "monthly_usd_limit": caps.get("monthly_usd_limit"),
            "per_task_hard_limit_usd": caps.get("per_task_hard_limit_usd"),
        },
        "spent": {"today_usd": spent_day, "month_usd": spent_month},
        "remaining": {
            "today_usd": remaining(caps.get("daily_usd_limit"), spent_day),
            "month_usd": remaining(caps.get("monthly_usd_limit"), spent_month),
        },
        "notes": [
            "Spend is estimated locally from the usage ledger; nothing is transmitted.",
            "Panic mode forces deterministic/local-only routing until disabled.",
        ],
    }


def budget_gate(
    project_root: Path,
    *,
    next_cost_usd: float = 0.0,
    tier: str = "L3",
    provider_type: str = "cloud",
    estimated_tokens: int = 0,
    destructive: bool = False,
) -> dict[str, Any]:
    """Decide whether the next route is allowed. Fail-closed (#50)."""
    root = project_root.expanduser().resolve()
    caps = load_budget(root)
    cost_model = load_cost_model(root)
    is_local = is_local_tier(tier, cost_model)
    reasons: list[str] = []
    decision = "allow"

    sev = {"allow": 0, "confirm": 1, "deny": 2}

    def escalate(level: str, reason: str) -> None:
        nonlocal decision
        if sev[level] > sev[decision]:
            decision = level
        reasons.append(reason)

    # 1. Panic mode: only deterministic/local routes allowed.
    if caps.get("panic") and not is_local:
        escalate(
            "deny",
            "Panic mode is ON: paid/cloud routes blocked. Disable with: opai budget panic --off",
        )

    # 2. Policy gate (tier/cloud/destructive/per-task budget).
    policy_gate = evaluate_action(
        root,
        tier=tier,
        provider_type=provider_type,
        cost_usd=next_cost_usd,
        estimated_tokens=estimated_tokens,
        destructive=destructive,
    )
    if policy_gate["denied"]:
        escalate(
            "deny", "Policy denied this route: " + "; ".join(policy_gate["reasons"])
        )
    elif policy_gate["requires_confirmation"]:
        escalate(
            "confirm",
            "Policy requires confirmation: " + "; ".join(policy_gate["reasons"]),
        )

    # 3. Accumulation ceilings (daily/monthly).
    if not is_local and next_cost_usd > 0:
        spent_day = _spent(root, period="day")
        spent_month = _spent(root, period="month")
        daily = caps.get("daily_usd_limit")
        monthly = caps.get("monthly_usd_limit")
        if daily is not None and spent_day + next_cost_usd > float(daily):
            escalate(
                "deny",
                f"Daily budget exceeded: ${spent_day:.4f}+${next_cost_usd:.4f} > ${daily}.",
            )
        if monthly is not None and spent_month + next_cost_usd > float(monthly):
            escalate(
                "deny",
                f"Monthly budget exceeded: ${spent_month:.4f}+${next_cost_usd:.4f} > ${monthly}.",
            )

    if not reasons:
        reasons.append("Within budget and policy.")

    return {
        "report": "opai-budget-gate",
        "project": str(root),
        "decision": decision,
        "allowed": decision == "allow",
        "requires_confirmation": decision == "confirm",
        "denied": decision == "deny",
        "panic": bool(caps.get("panic")),
        "tier": str(tier).upper(),
        "is_local_route": is_local,
        "next_cost_usd": next_cost_usd,
        "reasons": reasons,
        "exit_code": 0 if decision != "deny" else 1,
    }
