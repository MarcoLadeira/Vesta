from __future__ import annotations

from pathlib import Path
from typing import Any

from .loader import hub_root, load_registry


# Rough industry-standard estimate: ~4 characters per token. Used only for
# local, offline estimates. No prompt text leaves the machine.
CHARS_PER_TOKEN = 4

# Default cost model used when hub/model-intelligence/cost_model.yaml is absent.
# All values are transparent, documented estimates - not billing data.
DEFAULT_COST_MODEL: dict[str, Any] = {
    "schema_version": 1,
    "currency": "USD",
    "chars_per_token": CHARS_PER_TOKEN,
    # Blended (input+output) USD per 1K tokens, conservative public estimates.
    "tier_usd_per_1k_tokens": {
        "L0": 0.0,
        "L1": 0.0,
        "L2": 0.0015,
        "L3": 0.012,
        "L4": 0.045,
    },
    # Without OPai, the assumption is a developer sends most coding tasks
    # straight to a strong frontier model. That is the savings baseline.
    "baseline_tier": "L3",
    # Default assumed tokens for one model round-trip on a coding task, used
    # when an event does not carry a measured token count.
    "default_task_tokens": 6000,
    "local_tiers": ["L0", "L1"],
    "notes": (
        "Estimates only. Tier costs are conservative public blended rates and "
        "the baseline assumes un-routed frontier usage. Tune these values to "
        "match your providers; nothing here is collected or transmitted."
    ),
}


def _degraded_model(reason: str) -> dict[str, Any]:
    """The default model, flagged as a degraded fallback (#471).

    A *missing* cost model is a clean default; a *present but unreadable* one is
    degraded — its absence of the user's tuned rates can understate paid cost, so
    the fallback carries a typed flag surfaces can escalate on instead of
    silently trusting a possibly-too-low estimate.
    """

    model = dict(DEFAULT_COST_MODEL)
    model["degraded"] = True
    model["degraded_reason"] = reason
    return model


def load_cost_model(project_root: Path | None = None) -> dict[str, Any]:
    path = hub_root(project_root) / "model-intelligence" / "cost_model.yaml"
    if not path.exists():
        return dict(DEFAULT_COST_MODEL)
    try:
        data = load_registry(path)
    except Exception as exc:  # noqa: BLE001 - a broken model must degrade, not crash
        return _degraded_model(f"cost model could not be parsed ({type(exc).__name__})")
    if not isinstance(data, dict):
        return _degraded_model("cost model file is not a mapping")
    merged = dict(DEFAULT_COST_MODEL)
    # A user file never smuggles in a false all-clear flag.
    data.pop("degraded", None)
    data.pop("degraded_reason", None)
    merged.update(data)
    return merged


def is_degraded(model: dict[str, Any] | None) -> bool:
    """True when a cost model is a degraded fallback for an unreadable file."""

    return bool((model or {}).get("degraded"))


def cost_model_status(project_root: Path | None = None) -> dict[str, Any]:
    """Typed load status for routing/budget surfaces (#471)."""

    model = load_cost_model(project_root)
    return {
        "ok": not is_degraded(model),
        "degraded": is_degraded(model),
        "reason": str(model.get("degraded_reason") or ""),
    }


def chars_per_token(model: dict[str, Any] | None = None) -> int:
    model = model or DEFAULT_COST_MODEL
    value = model.get("chars_per_token", CHARS_PER_TOKEN)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return CHARS_PER_TOKEN


def estimate_tokens(text: str, model: dict[str, Any] | None = None) -> int:
    if not text:
        return 0
    return max(1, len(text) // chars_per_token(model))


def tier_cost_per_1k(tier: str, model: dict[str, Any] | None = None) -> float:
    model = model or DEFAULT_COST_MODEL
    table = model.get("tier_usd_per_1k_tokens", {})
    try:
        return float(table.get(str(tier).upper(), 0.0))
    except (TypeError, ValueError):
        return 0.0


def tier_cost(tier: str, tokens: int, model: dict[str, Any] | None = None) -> float:
    return round(tier_cost_per_1k(tier, model) * (max(0, tokens) / 1000.0), 6)


def is_local_tier(tier: str, model: dict[str, Any] | None = None) -> bool:
    model = model or DEFAULT_COST_MODEL
    return str(tier).upper() in {str(t).upper() for t in model.get("local_tiers", [])}


def estimate_route_savings(
    chosen_tier: str,
    task_tokens: int | None = None,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate the USD a single routed task saves versus the baseline tier.

    The numbers are explicit estimates governed by the cost model config; they
    are not invoices. ``baseline`` is what the task would cost if sent straight
    to the un-routed frontier tier, ``actual`` is the chosen route's cost.
    """
    model = model or DEFAULT_COST_MODEL
    tokens = (
        task_tokens
        if task_tokens is not None
        else int(model.get("default_task_tokens", 6000))
    )
    baseline_tier = str(model.get("baseline_tier", "L3"))
    baseline = tier_cost(baseline_tier, tokens, model)
    actual = tier_cost(chosen_tier, tokens, model)
    savings = round(baseline - actual, 6)
    return {
        "chosen_tier": str(chosen_tier).upper(),
        "baseline_tier": baseline_tier.upper(),
        "task_tokens": tokens,
        "estimated_baseline_usd": baseline,
        "estimated_actual_usd": actual,
        "estimated_savings_usd": max(0.0, savings),
        "cloud_call_avoided": is_local_tier(chosen_tier, model)
        and not is_local_tier(baseline_tier, model),
    }
