"""Capability and observed-quota admission for managed objective workers.

This is a projection of the existing catalog, routing memory and usage ledger,
not another reservation or accounting store. Call after acquiring host capacity
and immediately before dispatch. ObjectiveStore owns project/assignment capacity;
objective_capacity owns host capacity. A busy slot never changes the cost lane.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from . import auto_router, provider_balance, provider_blocks, provider_usage
from .execution_scope import financial_root
from .local_models import classify_endpoint
from .provider_catalog import provider_record

_READ_ONLY_ROLES = {"planner", "explorer", "researcher", "reviewer", "critic"}
_CAPABILITY_ALIASES = {
    "read_files": "repo_read",
    "edit_files": "repo_editing",
    "shell": "code_execution",
    "tests": "run_tests",
    "tools": "tool_calling",
}


def required_capabilities(objective: dict, assignment: dict, *, planning=False):
    """Explicit requirements narrow the route; roles only determine edit access."""
    required = {
        _CAPABILITY_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())
        for value in assignment.get("capabilities", [])
    }
    required.add("chat")
    if (
        not planning
        and str(assignment.get("role", "implementer")).casefold()
        not in _READ_ONLY_ROLES
        and objective.get("mode", "safe-auto") not in {"ask", "plan"}
    ):
        required.add("repo_editing")
    return sorted(required)


def _provider(item: dict) -> str:
    model_id = str(item.get("id") or "")
    provider = str(item.get("provider") or "").lower()
    if not provider:
        parts = model_id.split(":", 2)
        provider = (
            parts[1]
            if len(parts) == 3 and parts[0] in {"account", "free", "paid"}
            else auto_router.provider_of(model_id)
        )
    return (
        "openai-compatible"
        if item.get("kind") == "local" and provider == "openai"
        else provider
    )


def _paid(item: dict) -> bool:
    return (
        item.get("paid") is True
        or item.get("kind") in {"account", "paid"}
        or str(item.get("id") or "").startswith(("account:", "paid:"))
    )


def _capabilities(item: dict) -> dict:
    try:
        result = dict(provider_record(_provider(item))["capabilities"])
    except ValueError:
        result = {}
    # A model cannot add tools its dispatch adapter does not implement.
    # Model evidence may only narrow the adapter's declared contract.
    if isinstance(item.get("capabilities"), dict):
        for name, status in item["capabilities"].items():
            if status not in (True, "supported"):
                result[name] = status
    if item.get("repo_editing") is False:
        result["repo_editing"] = "unsupported"
    return result


def _exhausted(usage: dict) -> bool:
    official = usage.get("official") or {}
    if not official.get("available") or official.get("stale"):
        return False
    remaining = official.get("remaining")
    if remaining is None or isinstance(remaining, bool):
        return False
    try:
        number = float(remaining)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number <= 0


def managed_local_runner(model_id: str, endpoint: str | None):
    """Bind a managed route to its admitted endpoint without environment fallback."""
    from .local_runner import OllamaRunner, OpenAICompatibleRunner

    provider, separator, name = str(model_id).partition(":")
    if (
        not separator
        or not name.strip()
        or provider not in {"ollama", "openai", "openai-compatible"}
        or not classify_endpoint(str(endpoint or ""))["is_local"]
    ):
        raise ValueError(
            "Managed dispatch requires a concrete local model and private endpoint"
        )
    runner_type = OllamaRunner if provider == "ollama" else OpenAICompatibleRunner
    return runner_type(str(endpoint), name)


def select_worker_route(
    project_root: Path,
    objective: dict,
    assignment: dict,
    *,
    catalog: dict | None = None,
    planning: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Choose one concrete eligible model without provider/model calls.

    Explicit user model authority remains pinned, even when unavailable. Planner
    model and route suggestions grant no authority. Auto independently evaluates
    each assignment and uses the canonical cheapest-first/reliability ordering.
    Local discovery lists models only on local/private endpoints. Usage reads
    never probe a provider. Unknown quota stays unknown and is not invented from
    OPai-only activity counts; fresh observed exhaustion excludes the provider.

    Dispatch the returned concrete model, rather than an unrestricted Auto
    sentinel that could fall back outside these capability/consent constraints.
    Provider turn guards remain responsible for budgets and subsequent failures.
    """
    root = financial_root(project_root)
    if catalog is None:
        from opai.app_state import available_models

        catalog = available_models(root, discover_local=True, discover_accounts=False)
    models = [
        dict(item)
        for item in catalog.get("models", [])
        if item.get("kind") in {"local", "free", "account", "paid"}
    ]
    required = required_capabilities(objective, assignment, planning=planning)
    needs_edit = "repo_editing" in required
    pinned = str(
        assignment.get("model")
        if assignment.get("model_authorized") is True
        else objective.get("model") or "auto"
    )
    pinned = pinned if pinned and pinned != "None" else "auto"
    allow_paid = objective.get("allow_cloud") is True
    providers = sorted(
        {_provider(item) for item in models if item.get("kind") != "local"}
    )
    usage = {
        row["provider"]: row
        for row in provider_usage.usage_overview(
            root,
            [{"provider": provider, "configured": True} for provider in providers],
            probe=False,
            now=now,
        )
    }
    unavailable = auto_router.unavailable_account_providers(catalog)
    blockers = []
    eligible = []
    for item in models:
        model_id, provider = str(item.get("id") or ""), _provider(item)
        if pinned != "auto" and model_id != pinned:
            continue
        reasons = []
        if item.get("available") is False or (
            item.get("kind") != "local" and item.get("available") is not True
        ):
            reasons.append(str(item.get("disabled_reason") or "Model is unavailable"))
        if provider in unavailable and item.get("kind") == "account":
            reasons.append("Provider connection is unavailable")
        if _paid(item) and not allow_paid:
            reasons.append("Paid or subscription quota use requires authorization")
        elif item.get("kind") != "local" and not allow_paid:
            reasons.append("Cloud context transmission requires authorization")
        if item.get("kind") == "local":
            try:
                managed_local_runner(model_id, item.get("endpoint"))
            except ValueError as exc:
                reasons.append(str(exc))
        # gui_pipeline has dedicated account/free dispatch, but paid: IDs still
        # enter its local one-shot branch. Do not claim editing capability or
        # invoke that path until it implements the paid API tool/consent flow.
        if item.get("kind") == "paid" or model_id.startswith("paid:"):
            reasons.append("Paid direct API objective dispatch is not supported")
        caps = _capabilities(item)
        missing = [
            name for name in required if caps.get(name) not in (True, "supported")
        ]
        if missing:
            reasons.append("Required capabilities unavailable: " + ", ".join(missing))
        if item.get("out_of_credit") is True or provider_balance.is_exhausted(
            root, provider, now=now
        ):
            reasons.append("Provider credit is exhausted")
        if provider_blocks.is_blocked(root, provider, needs_edit=needs_edit, now=now):
            reasons.append("Provider is blocked for this operation")
        if _exhausted(usage.get(provider, {})):
            reasons.append("Provider quota is exhausted for the observed window")
        if reasons:
            blockers.append(
                {"model_id": model_id, "provider": provider, "reasons": reasons}
            )
        else:
            eligible.append({**item, "provider": provider})

    if pinned != "auto" and not any(item.get("id") == pinned for item in models):
        blockers.append(
            {
                "model_id": pinned,
                "provider": "",
                "reasons": ["Explicit authorized model is absent from the catalog"],
            }
        )

    # The canonical chain currently recognizes account/free buckets. Normalize
    # paid API entries only for ordering, preserving their original dispatch IDs.
    ranked = auto_router.resolve_auto_chain(
        root,
        str(assignment.get("objective") or objective.get("objective") or ""),
        {
            "models": [
                {**item, "kind": "account" if _paid(item) else item["kind"]}
                for item in eligible
            ],
            "connections": catalog.get("connections", []),
        },
        allow_paid=allow_paid,
        needs_edit=needs_edit,
        now=now,
    )
    by_id = {item["id"]: item for item in eligible}
    candidates = [item for item in eligible if item["kind"] == "local"]
    candidates.extend(by_id[item["id"]] for item in ranked if item["id"] in by_id)
    selected = candidates[0] if candidates else None
    return {
        "allowed": selected is not None,
        "model_id": selected["id"] if selected else pinned,
        "provider": _provider(selected) if selected else "",
        "endpoint": selected.get("endpoint")
        if selected and selected["kind"] == "local"
        else None,
        "reason": (
            "Explicit authorized model"
            if selected and pinned != "auto"
            else "Cheapest eligible model for assignment capabilities"
            if selected
            else "No eligible model satisfies this assignment and its authorization"
        ),
        "capabilities": required,
        "eligible_candidates": [item["id"] for item in candidates],
        "blockers": blockers,
        "quota": usage.get(_provider(selected), {}) if selected else {},
    }
