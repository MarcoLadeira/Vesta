from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .command_runner import redact
from .cost_model import (
    estimate_route_savings,
    estimate_tokens,
    is_local_tier,
    load_cost_model,
    tier_cost,
)
from .state import state_dir


# Event types recorded in the local usage ledger.
EVENT_ROUTE = "route_decision"
EVENT_MODEL_CALL = "model_call"
EVENT_CONTEXT_COMPACT = "context_compaction"
EVENT_CACHE = "cache_lookup"

KNOWN_EVENT_TYPES = {
    EVENT_ROUTE,
    EVENT_MODEL_CALL,
    EVENT_CONTEXT_COMPACT,
    EVENT_CACHE,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ledger_path(project_root: Path) -> Path:
    return state_dir(project_root) / "ledger" / "usage.jsonl"


def task_fingerprint(task: str) -> str:
    """Stable, non-reversible fingerprint of a task string."""
    return hashlib.sha256((task or "").encode("utf-8")).hexdigest()[:16]


def _safe_summary(task: str, store_summary: bool) -> str | None:
    """Optional short, redacted task summary. Off by default for privacy."""
    if not store_summary or not task:
        return None
    return redact(task.strip())[:120]


def record_event(
    project_root: Path,
    event_type: str,
    *,
    task: str = "",
    store_summary: bool = False,
    **fields: Any,
) -> dict[str, Any]:
    """Append a single privacy-safe event to the local usage ledger.

    The raw ``task`` is never stored; only a one-way ``task_hash`` is written
    (plus an optional redacted summary when ``store_summary`` is explicitly
    enabled). Nothing leaves the machine.
    """
    root = project_root.expanduser().resolve()
    event: dict[str, Any] = {
        "created_at": _now_iso(),
        "event_type": event_type,
        "task_hash": task_fingerprint(task),
    }
    summary = _safe_summary(task, store_summary)
    if summary is not None:
        event["task_summary_redacted"] = summary
    # Redact any string field defensively before persisting.
    for key, value in fields.items():
        event[key] = redact(value) if isinstance(value, str) else value

    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return event


def record_route_decision(
    project_root: Path,
    task: str,
    *,
    model_tier: str,
    workflow: str = "",
    full_context_chars: int = 0,
    compact_context_chars: int = 0,
    task_tokens: int | None = None,
    cache_hit: bool = False,
    store_summary: bool = False,
) -> dict[str, Any]:
    """Record a routing decision plus its estimated savings and compaction."""
    root = project_root.expanduser().resolve()
    cost_model = load_cost_model(root)
    savings = estimate_route_savings(
        model_tier, task_tokens=task_tokens, model=cost_model
    )
    context_tokens_saved = 0
    if full_context_chars and compact_context_chars:
        delta = max(0, full_context_chars - compact_context_chars)
        context_tokens_saved = estimate_tokens("x" * delta, cost_model)
    return record_event(
        root,
        EVENT_ROUTE,
        task=task,
        store_summary=store_summary,
        workflow=workflow,
        model_tier=str(model_tier).upper(),
        is_local_route=is_local_tier(model_tier, cost_model),
        cache_hit=bool(cache_hit),
        full_context_chars=int(full_context_chars),
        compact_context_chars=int(compact_context_chars),
        context_chars_saved=max(
            0, int(full_context_chars) - int(compact_context_chars)
        ),
        context_tokens_saved=context_tokens_saved,
        **savings,
    )


def record_model_call(
    project_root: Path,
    task: str,
    *,
    model_tier: str,
    provider_type: str,
    tokens: int,
    confirmed: bool,
    store_summary: bool = False,
) -> dict[str, Any]:
    """Record an actual model call (cloud or local) and its estimated cost."""
    root = project_root.expanduser().resolve()
    cost_model = load_cost_model(root)
    cost = tier_cost(model_tier, tokens, cost_model)
    return record_event(
        root,
        EVENT_MODEL_CALL,
        task=task,
        store_summary=store_summary,
        model_tier=str(model_tier).upper(),
        provider_type=provider_type,
        tokens=int(tokens),
        confirmed=bool(confirmed),
        is_local_route=is_local_tier(model_tier, cost_model),
        estimated_actual_usd=cost,
    )


def read_events(project_root: Path, limit: int | None = None) -> list[dict[str, Any]]:
    path = ledger_path(project_root.expanduser().resolve())
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if limit is not None:
        lines = lines[-limit:]
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _sum(events: Iterable[dict[str, Any]], key: str) -> float:
    total = 0.0
    for event in events:
        value = event.get(key)
        if isinstance(value, (int, float)):
            total += value
    return round(total, 6)


def summarize_ledger(project_root: Path) -> dict[str, Any]:
    """Aggregate the local ledger into cost-control signals. Read-only."""
    root = project_root.expanduser().resolve()
    events = read_events(root)
    routes = [event for event in events if event.get("event_type") == EVENT_ROUTE]
    model_calls = [
        event for event in events if event.get("event_type") == EVENT_MODEL_CALL
    ]

    by_tier: dict[str, int] = {}
    for route in routes:
        tier = str(route.get("model_tier", "L0")).upper()
        by_tier[tier] = by_tier.get(tier, 0) + 1

    cloud_calls_avoided = sum(1 for route in routes if route.get("cloud_call_avoided"))
    local_routes = sum(1 for route in routes if route.get("is_local_route"))

    return {
        "project": str(root),
        "ledger_path": str(ledger_path(root)),
        "event_count": len(events),
        "route_count": len(routes),
        "model_call_count": len(model_calls),
        "routes_by_tier": dict(sorted(by_tier.items())),
        "local_routes": local_routes,
        "cloud_calls_avoided": cloud_calls_avoided,
        "estimated_baseline_usd": _sum(routes, "estimated_baseline_usd"),
        "estimated_actual_spend_usd": round(
            _sum(routes, "estimated_actual_usd")
            + _sum(model_calls, "estimated_actual_usd"),
            6,
        ),
        "estimated_savings_usd": _sum(routes, "estimated_savings_usd"),
        "context_chars_saved": int(_sum(routes, "context_chars_saved")),
        "context_tokens_saved": int(_sum(routes, "context_tokens_saved")),
        "privacy": "Raw prompts are never stored; only one-way task hashes and counts.",
    }
