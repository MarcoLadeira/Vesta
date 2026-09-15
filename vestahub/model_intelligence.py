from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .loader import hub_root, load_registry


def _load_model_intelligence(
    name: str, project_root: Path | None = None
) -> dict[str, Any]:
    path = hub_root(project_root) / "model-intelligence" / f"{name}.yaml"
    if not path.exists():
        return {}
    data = load_registry(path)
    return data if isinstance(data, dict) else {}


def task_taxonomy(project_root: Path | None = None) -> dict[str, Any]:
    return _load_model_intelligence("task_taxonomy", project_root)


def model_scorecards(project_root: Path | None = None) -> dict[str, Any]:
    return _load_model_intelligence("model_scorecards", project_root)


def routing_policy(project_root: Path | None = None) -> dict[str, Any]:
    return _load_model_intelligence("routing_policy", project_root)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _signal_match(task: str, signals: list[str]) -> tuple[int, list[str]]:
    lowered = task.lower()
    tokens = _tokens(task)
    matched = []
    for signal in signals:
        normalized = signal.lower()
        if " " in normalized:
            if normalized in lowered:
                matched.append(signal)
        elif normalized in tokens:
            matched.append(signal)
    return len(matched), matched


def classify_task(project_root: Path, task: str) -> dict[str, Any]:
    taxonomy = task_taxonomy(project_root)
    best: dict[str, Any] | None = None
    best_score = -1
    best_matches: list[str] = []
    for item in taxonomy.get("task_types", []):
        score, matches = _signal_match(task, item.get("signals", []))
        if score > best_score:
            best = item
            best_score = score
            best_matches = matches
    if not best or best_score <= 0:
        best = taxonomy.get("default_task_type", {})
        best_matches = []
    return {
        "task_type": best.get("id", "general_coding"),
        "default_tier": best.get("default_tier", "L1"),
        "requires_confirmation": best.get("requires_confirmation", False),
        "matched_signals": best_matches,
        "escalation_conditions": best.get("escalation_conditions", []),
        "description": best.get("description", ""),
    }


def _tier_value(tier: str) -> int:
    try:
        return int(str(tier).upper().removeprefix("L"))
    except ValueError:
        return 1


def _cost_penalty(cost_level: str) -> float:
    return {
        "free": 0.0,
        "free-local": 0.0,
        "low": 0.12,
        "medium": 0.3,
        "high": 0.65,
        "very-high": 0.9,
    }.get(str(cost_level).lower(), 0.4)


def _model_score(
    model: dict[str, Any],
    task_type: str,
    required_tier: str,
    weights: dict[str, float],
) -> float:
    strengths = set(model.get("strengths", []))
    quality = model.get("quality_by_task", {}).get(task_type)
    if quality is None:
        quality = 0.95 if task_type in strengths else 0.62
    task_fit = 1.0 if task_type in strengths else 0.58
    tier_fit = (
        1.0
        if _tier_value(model.get("tier", "L1")) >= _tier_value(required_tier)
        else 0.45
    )
    privacy_fit = 1.0 if model.get("provider_type") == "local" else 0.65
    reliability = float(model.get("reliability", 0.75))
    cost = _cost_penalty(model.get("cost_level", "medium"))
    return (
        task_fit * weights.get("task_fit", 0.35)
        + float(quality) * weights.get("eval_score", 0.25)
        + tier_fit * weights.get("context_fit", 0.15)
        + reliability * weights.get("reliability", 0.1)
        + privacy_fit * weights.get("privacy_fit", 0.1)
        - cost * weights.get("cost_penalty", 0.25)
    )


def recommend_model(project_root: Path, task: str) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    classification = classify_task(root, task)
    scorecards = model_scorecards(root)
    policy = routing_policy(root)
    weights = policy.get("weights", {})
    required_tier = classification["default_tier"]
    candidates = []
    for model in scorecards.get("models", []):
        if not model.get("enabled_by_default", False):
            continue
        score = _model_score(model, classification["task_type"], required_tier, weights)
        candidates.append(
            {
                "id": model.get("id"),
                "name": model.get("name"),
                "tier": model.get("tier"),
                "provider": model.get("provider"),
                "provider_type": model.get("provider_type"),
                "cost_level": model.get("cost_level"),
                "requires_api_key": model.get("requires_api_key", False),
                "confirmation_required": model.get("confirmation_required", False),
                "score": round(score, 4),
            }
        )
    candidates.sort(key=lambda item: item["score"], reverse=True)
    recommended = candidates[0] if candidates else {}
    confirmation_required = bool(recommended.get("confirmation_required"))
    confirmation_required = (
        confirmation_required
        or bool(classification.get("requires_confirmation"))
        or _tier_value(recommended.get("tier", "L1"))
        >= _tier_value(policy.get("confirmation_required_at_or_above", "L3"))
    )

    # Gate the recommendation against the active policy profile (#37/#16).
    from .cost_model import load_cost_model, tier_cost
    from .policy import evaluate_action

    cost_model = load_cost_model(root)
    rec_tier = recommended.get("tier", required_tier)
    task_tokens = int(cost_model.get("default_task_tokens", 6000))
    gate = evaluate_action(
        root,
        tier=rec_tier,
        provider_type=recommended.get("provider_type", "local"),
        cost_usd=tier_cost(rec_tier, task_tokens, cost_model),
        estimated_tokens=task_tokens,
        paid=bool(recommended.get("requires_api_key")),
    )
    confirmation_required = (
        confirmation_required or gate["requires_confirmation"] or gate["denied"]
    )

    return {
        "task": task,
        "project": str(root),
        "task_type": classification["task_type"],
        "description": classification["description"],
        "matched_signals": classification["matched_signals"],
        "recommended_model_id": recommended.get("id"),
        "recommended_model_tier": recommended.get("tier", required_tier),
        "recommended_model": recommended,
        "requires_confirmation": confirmation_required,
        "policy_profile": gate["profile"],
        "policy_decision": gate["decision"],
        "policy_reasons": gate["reasons"],
        "escalation_conditions": classification["escalation_conditions"],
        "escalation_path": policy.get("escalation_path", []),
        "policy": "local evidence first; cheapest capable model",
        "candidates": candidates[:5],
    }
