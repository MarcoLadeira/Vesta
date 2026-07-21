"""Capability- and cost-aware candidate ordering for OPai Auto mode.

Auto's job: pick the **cheapest model that can actually complete the task**,
and if that model fails, move on to the next one *without* dead-ending or
asking the user to prompt again.

This module builds the ordered *fallback chain* Auto walks. The pipeline
(`opaihub.gui_pipeline`) executes candidates in order, advancing on a
retryable failure. The ordering policy, in one place so it is testable:

1. **Local first.** A live-detection sentinel (`"auto"`) always leads: a local
   model is free and private, so Auto tries one before anything leaves the
   device.
2. **Then configured free APIs**, cheapest tier of cloud. Ordered *within* the
   free group by reliability (recent failures sink), then least-recently-used
   (so Auto rotates instead of always picking whichever is first in the list),
   then a stable id sort.
3. **Then connected paid accounts**, ordered the same way. These sit at the
   tail because they cost real money — the pipeline confirms before the first
   paid call unless the user already authorized paid/cloud.

Nothing here is hardcoded to a specific model. The chain is derived entirely
from the live catalog, the task classification, and the local reliability
memory, so a user's own configured free and paid models are what Auto chooses
among.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import provider_reliability as reliability
from .model_intelligence import classify_task

# Pipeline statuses that mean "this provider could not do it, try the next one".
# Approvals, confirmations, cancels, and genuine answers are NOT here — those
# stop the fallback walk.
RETRYABLE_STATUSES = frozenset(
    {
        "runner_error",
        "failed",
        "error",
        "needs_model",
        "model_unavailable",
        "no_local_model",
        "capability_mismatch",
        "empty",
    }
)

# Statuses that must never trigger an automatic fallback: they need the user,
# or they already succeeded.
TERMINAL_STATUSES = frozenset(
    {
        "answered",
        "cancelled",
        "blocked",
        "needs_command_approval",
        "needs_edit_approval",
        "needs_free_confirmation",
        "needs_limit_confirmation",
        "needs_auto_confirmation",
        "needs_confirmation",
    }
)

_UNAVAILABLE_ACCOUNT_STATUSES = frozenset(
    {"misconfigured", "provider_unavailable", "invalid", "expired", "disconnected"}
)


def provider_of(model_id: str) -> str:
    """Extract the provider slug from a model id.

    ``account:claude:haiku`` → ``claude``; ``free:kimi:kimi-k2.6`` → ``kimi``;
    ``ollama:qwen`` → ``ollama``; ``auto`` → ``""`` (no real provider).
    """
    text = str(model_id or "").strip()
    if not text or text == "auto":
        return ""
    parts = text.split(":")
    if parts[0] in {"account", "free"} and len(parts) > 1:
        return parts[1].lower()
    return parts[0].lower()


def _provider_of_entry(item: dict[str, Any]) -> str:
    provider = str(item.get("provider") or "").strip().lower()
    return provider or provider_of(str(item.get("id") or ""))


def is_retryable_status(status: str) -> bool:
    return str(status or "").strip() in RETRYABLE_STATUSES


def answer_is_empty(answer: Any) -> bool:
    return not str(answer or "").strip()


def reason_slug(status: str = "", error: Any = None) -> str:
    """A short reliability reason from a status / structured error code."""
    code = ""
    if isinstance(error, dict):
        code = str(error.get("code") or "")
    code = code.upper()
    if code.startswith("AUTH_"):
        return "auth"
    if code == "PROVIDER_RATE_LIMITED":
        return "rate-limit"
    if code == "PROVIDER_QUOTA_EXHAUSTED":
        return "quota"
    if code == "PROVIDER_TIMEOUT":
        return "timeout"
    if code == "PROVIDER_UNAVAILABLE":
        return "unavailable"
    status = str(status or "").strip()
    return {
        "capability_mismatch": "capability",
        "model_unavailable": "misconfigured",
        "needs_model": "misconfigured",
        "no_local_model": "no-local",
        "empty": "no-answer",
        "runner_error": "runner-error",
    }.get(status, status or "error")


def unavailable_account_providers(catalog: dict[str, Any]) -> set[str]:
    """Providers whose most recent connection health is a known failure."""
    return {
        str(conn.get("providerId") or "").lower()
        for conn in (catalog.get("connections") or [])
        if str(conn.get("authStatus") or "").lower() in _UNAVAILABLE_ACCOUNT_STATUSES
    }


def _rank_bucket(
    project_root: Path, candidates: list[dict[str, Any]], *, now: float | None
) -> list[dict[str, Any]]:
    """Order candidates in one cost bucket: healthy + not-recently-used first.

    Sort key (all ascending): (in_cooldown, reliability_penalty, last_used, id).
    So a provider that just failed sinks (cooldown, penalty), and among equally
    healthy providers the least-recently-used one leads — Auto rotates instead
    of hammering the first entry.
    """

    def key(item: dict[str, Any]) -> tuple[int, float, float, str]:
        provider = _provider_of_entry(item)
        return (
            1 if reliability.in_cooldown(project_root, provider, now=now) else 0,
            reliability.reliability_penalty(project_root, provider, now=now),
            reliability.last_used(project_root, provider),
            str(item.get("id") or ""),
        )

    return sorted(candidates, key=key)


def resolve_auto_chain(
    project_root: Path,
    task: str,
    catalog: dict[str, Any],
    *,
    allow_paid: bool = False,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Build the ordered Auto fallback chain from the live catalog.

    Each element is ``{"id", "kind", "provider", "paid", "reason"}``. The first
    element is always the local live-detection sentinel. ``allow_paid`` only
    affects the reason text — paid accounts are always included so the pipeline
    can offer a confirmed escalation; it decides whether to run or confirm.
    """
    root = project_root.expanduser().resolve()
    models = list(catalog.get("models") or [])
    unavailable = unavailable_account_providers(catalog)

    free = [
        item
        for item in models
        if item.get("kind") == "free" and item.get("available") is True
    ]
    accounts = [
        item
        for item in models
        if item.get("kind") == "account"
        and item.get("available") is True
        and _provider_of_entry(item) not in unavailable
    ]

    classification = classify_task(root, task)
    task_type = classification.get("task_type", "general_coding")

    chain: list[dict[str, Any]] = [
        {
            "id": "auto",
            "kind": "local",
            "provider": "local",
            "paid": False,
            "reason": "local-first (private, no cost)",
        }
    ]
    for item in _rank_bucket(root, free, now=now):
        chain.append(
            {
                "id": str(item["id"]),
                "kind": "free",
                "provider": _provider_of_entry(item),
                "paid": False,
                "reason": f"cheapest capable free API for {task_type}",
            }
        )
    for item in _rank_bucket(root, accounts, now=now):
        chain.append(
            {
                "id": str(item["id"]),
                "kind": "account",
                "provider": _provider_of_entry(item),
                "paid": True,
                "reason": (
                    f"paid escalation for {task_type}"
                    if allow_paid
                    else f"paid escalation for {task_type} (needs confirmation)"
                ),
            }
        )
    return chain


def routing_diagnostics(
    project_root: Path, task: str, catalog: dict[str, Any], *, now: float | None = None
) -> dict[str, Any]:
    """Lightweight, secret-free view of how Auto ordered its candidates.

    For the internal diagnostics surface only — never shown as noise to a
    normal user, but available when a route needs explaining.
    """
    chain = resolve_auto_chain(project_root, task, catalog, now=now)
    return {
        "task_type": classify_task(project_root, task).get("task_type"),
        "chain": [
            {"id": c["id"], "provider": c["provider"], "paid": c["paid"]}
            for c in chain
        ],
        "reliability": reliability.reliability_snapshot(project_root, now=now),
    }
