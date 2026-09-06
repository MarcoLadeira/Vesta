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
import math
from datetime import datetime, timezone
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from . import shadow_journal
from .atomic_io import atomic_write_text, interprocess_transaction
from .cost_model import is_degraded, is_local_tier, load_cost_model
from .ledger import (
    EVENT_MODEL_CALL,
    EVENT_MODEL_CALL_ABANDONED,
    cost_reconciliation,
    read_events,
    reconcile_abandoned_calls,
)
from .policy import evaluate_action, resolve_policy
from .state import state_dir
from .execution_scope import financial_root

# The numeric spend ceilings a budget can carry.
_CAP_KEYS = ("daily_usd_limit", "monthly_usd_limit", "per_task_hard_limit_usd")


def _validate_cap(value: Any, *, name: str) -> float | None:
    """A cap the user is *setting* must be a finite, non-negative amount.

    NaN and infinity round-trip through JSON but silently disable the ceiling —
    every ``spent + cost > NaN`` comparison is false — so an invalid cap is
    rejected at the source rather than persisted and failing open later (#469).
    """

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite dollar amount, not {value!r}")
    if number < 0:
        raise ValueError(f"{name} must not be negative")
    return number


def _sanitize_cap(raw: Any) -> float | None:
    """A usable cap read from disk, or ``None`` when unset.

    Defense in depth for a hand-edited or legacy budget.json: a present-but-
    invalid ceiling (NaN, infinity, negative, non-number) becomes ``0.0`` so the
    gate *fails closed* — a corrupt ceiling blocks paid spend instead of
    silently vanishing on a NaN comparison that is always false.
    """

    if raw is None:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number


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


def _backup_path(project_root: Path) -> Path:
    return state_dir(project_root) / "budget.json.bak"


def _read_budget_dict(path: Path) -> dict[str, Any] | None:
    """Parse a budget file into a dict, or ``None`` when it is absent or corrupt.

    Distinguishes "no budget configured" from "budget configured but unreadable"
    so a corrupt file never silently reads as "no caps" (#470).
    """

    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def budget_state(project_root: Path) -> dict[str, str]:
    """Typed state of the on-disk budget configuration (#470).

    ``ok`` (absent or valid), ``recovered`` (primary unreadable, a valid backup
    exists), or ``unreadable`` (primary and backup both unreadable — the gate
    fails closed on paid routes).
    """

    root = project_root.expanduser().resolve()
    path = budget_path(root)
    if not path.exists() or _read_budget_dict(path) is not None:
        return {"state": "ok", "reason": ""}
    if _read_budget_dict(_backup_path(root)) is not None:
        return {
            "state": "recovered",
            "reason": "budget.json was unreadable; recovered the last valid backup",
        }
    return {
        "state": "unreadable",
        "reason": "budget.json and its backup are unreadable; failing closed on paid routes",
    }


def load_budget(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    caps = _default_caps(root)
    path = budget_path(root)
    data = _read_budget_dict(path)
    if data is None and path.exists():
        # Primary is present but corrupt — recover the last known-good backup
        # rather than silently reverting to policy defaults (which may drop the
        # user's configured cap and let the next paid route through).
        data = _read_budget_dict(_backup_path(root))
    if isinstance(data, dict):
        caps.update(data)
    # A corrupt/non-finite ceiling from disk must never fail open at the gate.
    for key in _CAP_KEYS:
        caps[key] = _sanitize_cap(caps.get(key))
    return caps


def _valid_budget_record(record) -> bool:
    """Match :func:`_read_budget_dict`'s own bar: any object is a budget.

    Deliberately permissive, because an *unset* cap is ``None`` and that is a
    real, meaningful record -- ``_default_caps`` returns ``None`` for every cap
    the policy does not set, and removing a cap persists ``None`` over a
    number. A validator demanding numeric caps would drop exactly the record
    that says "this ceiling was lifted", which is the sixth module in this
    migration where rejecting the empty state would discard the record #613
    most needs. Getting it wrong here is the expensive direction too: a shadow
    that kept a stale ceiling would misreport a spending limit the user has
    already removed.
    """

    return isinstance(record, Mapping)


def budget_shadow_projection(project_root: Path) -> dict[str, Any]:
    """Rebuild the persisted budget caps from their shadow journal."""

    return shadow_journal.projection(
        budget_path(project_root.expanduser().resolve()),
        is_valid_record=_valid_budget_record,
    )


def budget_contradiction_report(project_root: Path) -> dict[str, Any] | None:
    """``None`` when the budget file and its shadow agree, else what differs."""

    path = budget_path(project_root.expanduser().resolve())
    return shadow_journal.contradiction_report(
        path,
        lambda: _read_budget_dict(path) or {},
        is_valid_record=_valid_budget_record,
    )


def set_budget(
    project_root: Path,
    *,
    daily_usd: float | None = None,
    monthly_usd: float | None = None,
    per_task_usd: float | None = None,
    panic: bool | None = None,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    updates: dict[str, Any] = {}
    # Validate before acquiring the transaction or reading durable state: a bad
    # request must fail without writing an old snapshot over another window's
    # valid change.
    if daily_usd is not None:
        updates["daily_usd_limit"] = _validate_cap(daily_usd, name="daily_usd")
    if monthly_usd is not None:
        updates["monthly_usd_limit"] = _validate_cap(monthly_usd, name="monthly_usd")
    if per_task_usd is not None:
        updates["per_task_hard_limit_usd"] = _validate_cap(
            per_task_usd, name="per_task_usd"
        )
    if panic is not None:
        updates["panic"] = bool(panic)
    path = budget_path(root)
    with interprocess_transaction(path):
        # The lock covers the full read–merge–write transaction, so separate
        # windows changing different caps cannot lose each other's update.
        caps = load_budget(root)
        caps.update(updates)
        # allow_nan=False makes a non-finite value fail loudly at write time
        # rather than emit invalid JSON that would parse back to a fail-open cap.
        payload = json.dumps(caps, indent=2, sort_keys=True, allow_nan=False) + "\n"
        # The shared writer uses a unique same-directory temporary and durable
        # replacement. Keep primary and its recovery backup in this transaction.
        atomic_write_text(path, payload)
        atomic_write_text(_backup_path(root), payload)
        # #613 Stage 2: the .bak above is a hand-rolled single-slot version
        # of what the journal does properly -- it survives a torn write but
        # not a bad value written twice. Mirrored inside the same transaction
        # as both files so all three agree on the order caps changed in.
        shadow_journal.record_snapshot(path, caps, is_valid_record=_valid_budget_record)
        return {"status": "updated", **caps, "path": str(path)}


def _spent(
    project_root: Path,
    *,
    period: str,
    events: Iterable[dict[str, Any]] | None = None,
) -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    month = today[:7]
    total = 0.0
    for event in read_events(project_root) if events is None else events:
        if event.get("event_type") != EVENT_MODEL_CALL:
            continue
        created = str(event.get("created_at", ""))
        if period == "day" and not created.startswith(today):
            continue
        if period == "month" and not created.startswith(month):
            continue
        value = event.get("estimated_actual_usd")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += value
    return round(total, 6)


def _unpriced_calls(
    project_root: Path,
    *,
    period: str,
    events: Iterable[dict[str, Any]] | None = None,
) -> int:
    """In-window model calls whose cost could not be priced (#619 AC5/AC8).

    These contribute ``$0.00`` to :func:`_spent` — not because they were
    free, but because no price was known for their tier. A cap compared
    against a total containing them is a cap compared against an
    understatement, so surfaces must be able to say the total is incomplete
    rather than present it as authoritative.
    """

    today = datetime.now(timezone.utc).date().isoformat()
    month = today[:7]
    unpriced = 0
    for event in read_events(project_root) if events is None else events:
        if event.get("event_type") != EVENT_MODEL_CALL:
            continue
        created = str(event.get("created_at", ""))
        if period == "day" and not created.startswith(today):
            continue
        if period == "month" and not created.startswith(month):
            continue
        # Events written before this field existed are not evidence of a
        # pricing failure — absence means "not recorded", not "unpriced".
        if event.get("cost_price_known") is False:
            unpriced += 1
    return unpriced


def _abandoned_calls(
    project_root: Path,
    *,
    period: str,
    events: Iterable[dict[str, Any]] | None = None,
) -> int:
    """In-window calls dispatched whose outcome never arrived (#685).

    Counted by when the call was *retired*, not when it was dispatched: the
    retirement is the moment the hole in the accounting became known, and
    dating the gate by it is what lets a stale crash stop prompting. A call
    orphaned last month and swept today is today's news exactly once.
    """

    today = datetime.now(timezone.utc).date().isoformat()
    month = today[:7]
    abandoned = 0
    for event in read_events(project_root) if events is None else events:
        if event.get("event_type") != EVENT_MODEL_CALL_ABANDONED:
            continue
        if event.get("is_local_route"):
            continue  # Local routes spend nothing; an unknown cost is still $0.
        created = str(event.get("created_at", ""))
        if period == "day" and not created.startswith(today):
            continue
        if period == "month" and not created.startswith(month):
            continue
        abandoned += 1
    return abandoned


def budget_status(
    project_root: Path,
    *,
    events: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    caps = load_budget(root)
    ledger_events = read_events(root) if events is None else list(events)
    spent_day = _spent(root, period="day", events=ledger_events)
    spent_month = _spent(root, period="month", events=ledger_events)
    unpriced_day = _unpriced_calls(root, period="day", events=ledger_events)
    unpriced_month = _unpriced_calls(root, period="month", events=ledger_events)
    abandoned_day = _abandoned_calls(root, period="day", events=ledger_events)
    abandoned_month = _abandoned_calls(root, period="month", events=ledger_events)
    # Status is a report, so it stays read-only and does not sweep. A call
    # retirable but not yet retired is counted here as unaccounted rather than
    # being written away behind a status read (#685).
    reconciliation = cost_reconciliation(root, events=ledger_events)

    def remaining(limit: Any, spent: float) -> Any:
        return round(float(limit) - spent, 6) if limit is not None else None

    notes = [
        "Spend is estimated locally from the usage ledger; nothing is transmitted.",
        "Panic mode forces deterministic/local-only routing until disabled.",
    ]
    if unpriced_month:
        notes.append(
            f"{unpriced_month} call(s) this month had no known price for their "
            "tier and count as $0.00 here — the totals below are a lower "
            "bound, not a complete figure. Check tier_usd_per_1k_tokens in "
            ".opaihub/model-intelligence/cost_model.yaml."
        )
    if abandoned_month:
        notes.append(
            f"{abandoned_month} call(s) this month were dispatched but never "
            "reported an outcome. What they cost is unknown, so the totals "
            "below are a lower bound. Run 'opai savings' to see which."
        )

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
        # #619 AC5/AC8: a total built partly from unpriced calls is a lower
        # bound. Say so explicitly instead of letting a confident-looking
        # number imply the cap is being enforced against real spend.
        "spend_completeness": {
            "complete": not (
                unpriced_day or unpriced_month or reconciliation["unaccounted_calls"]
            ),
            "unpriced_calls_today": unpriced_day,
            "unpriced_calls_month": unpriced_month,
            "unaccounted_calls": reconciliation["unaccounted_calls"],
            # #685: dispatched, never reported an outcome. Reported forever;
            # only *gating* uses the self-clearing daily window.
            "abandoned_calls_today": abandoned_day,
            "abandoned_calls_month": abandoned_month,
        },
        "remaining": {
            "today_usd": remaining(caps.get("daily_usd_limit"), spent_day),
            "month_usd": remaining(caps.get("monthly_usd_limit"), spent_month),
        },
        "notes": notes,
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
    root = financial_root(project_root)
    caps = load_budget(root)
    cost_model = load_cost_model(root)
    is_local = is_local_tier(tier, cost_model)
    budget_config = budget_state(root)
    reasons: list[str] = []
    decision = "allow"

    sev = {"allow": 0, "confirm": 1, "deny": 2}

    def escalate(level: str, reason: str) -> None:
        nonlocal decision
        if sev[level] > sev[decision]:
            decision = level
        reasons.append(reason)

    # 0a. Unreadable budget configuration (#470): the user's caps may be gone,
    # so a paid/cloud route must fail closed instead of proceeding as if no
    # budget were set. A recovered backup is used transparently by load_budget.
    if budget_config["state"] == "unreadable" and not is_local:
        escalate(
            "deny",
            "Budget state unreadable — failing closed: " + budget_config["reason"],
        )

    # 0. A degraded cost model (#471): the paid estimate can't be trusted, so a
    # paid/cloud route must never be silently allowed on a possibly-understated
    # number — require explicit confirmation until the model is repaired.
    cost_model_degraded = is_degraded(cost_model)
    if cost_model_degraded and not is_local:
        escalate(
            "confirm",
            "Cost model is degraded ("
            + (str(cost_model.get("degraded_reason") or "unreadable"))
            + "); the paid estimate is unreliable — confirm before routing.",
        )

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

        # 3a. #619 AC8: "Budget protection fails closed or requires explicit
        # policy when authoritative maximum is unknown."
        #
        # `_spent` is a LOWER BOUND. It omits calls priced at $0.00 because no
        # price was known for their tier (#619 AC5, cost_price_known=False),
        # so comparing it against a cap under-triggers the ceiling: actual
        # spend can already be over the limit while this arithmetic reports
        # room to spare.
        #
        # Confirm rather than deny, matching the degraded-cost-model
        # precedent above: the figure is understated, not absent. A cap the
        # user never set has no ceiling to under-trigger, so this only
        # applies when one exists.
        #
        # Scoped to TODAY's unpriced calls, deliberately. Two exclusions:
        #
        #  - Not the month. A daily window clears on its own, so a repaired
        #    cost model stops the prompt tomorrow at the latest rather than
        #    for the rest of the month.
        #  - Not calls that are merely *in flight*. A turn running right now is
        #    normal operation, not a blind spot; it resolves by itself moments
        #    later, and prompting on it would fire during ordinary concurrent
        #    use.
        #
        # Abandoned calls (#685) ARE gated on, and only became safe to gate on
        # once they were bounded. Previously every unresolved call sat in the
        # ledger head's `active_calls` forever, so one crashed run would have
        # required confirmation on every paid route with no way to clear it —
        # a permanent prompt is not a safety feature, it trains people to click
        # through. Now a call is retired to a terminal cost-unknown state, and
        # this gate looks only at ones retired TODAY, so it clears on the same
        # self-healing daily window as the unpriced case above.
        if daily is not None or monthly is not None:
            # Retire anything that cannot still be running before counting, so
            # the gate sees a crash from a dead process rather than waiting for
            # some other surface to notice first. Calls this process started
            # are never retired, so a mid-turn gate check is safe.
            reconcile_abandoned_calls(root)
            unpriced_today = _unpriced_calls(root, period="day")
            if unpriced_today:
                escalate(
                    "confirm",
                    f"Recorded spend is a lower bound ({unpriced_today} call(s) "
                    "today had no known price for their tier), so the budget "
                    "ceiling cannot be enforced against a complete total — "
                    "confirm before routing, or set the tier's price in "
                    ".opaihub/model-intelligence/cost_model.yaml.",
                )
            abandoned_today = _abandoned_calls(root, period="day")
            if abandoned_today:
                escalate(
                    "confirm",
                    f"Recorded spend is a lower bound ({abandoned_today} call(s) "
                    "today were dispatched but never reported an outcome, so "
                    "what they cost is unknown), and the budget ceiling cannot "
                    "be enforced against an incomplete total — confirm before "
                    "routing. Run 'opai savings' to see which calls.",
                )

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
        "cost_model_degraded": cost_model_degraded,
        "budget_state": budget_config["state"],
        "tier": str(tier).upper(),
        "is_local_route": is_local,
        "next_cost_usd": next_cost_usd,
        "reasons": reasons,
        "exit_code": 0 if decision != "deny" else 1,
    }
