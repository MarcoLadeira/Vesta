from __future__ import annotations

import copy
import hashlib
import json
import threading
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
EVENT_CAPTURE_SESSION = "capture_session"

KNOWN_EVENT_TYPES = {
    EVENT_ROUTE,
    EVENT_MODEL_CALL,
    EVENT_CONTEXT_COMPACT,
    EVENT_CACHE,
    EVENT_CAPTURE_SESSION,
}

# Capture-rate contract (#9): defined once so every surface reports the same
# boundary. Client readiness (how many clients are wired) is NOT capture — a
# 5/5 readiness never means 100% of sessions are measured.
CAPTURE_RATE_DEFINITION = {
    "numerator": "capture_session events with captured=true (a session OPai measured)",
    "denominator": "all observed capture_session events (measurable sessions)",
    "excludes": (
        "Direct unwrapped agent launches OPai never sees are unmeasurable and are "
        "not in the denominator. Client readiness is not capture."
    ),
    "unit": "percent of observed proxy sessions",
}

_LEDGER_LOCK = threading.RLock()


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
    with _LEDGER_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    return event


def record_capture_session(
    project_root: Path,
    task: str,
    *,
    capture_id: str,
    agent: str,
    mode: str,
    outcome: str,
    captured: bool,
    paid: bool,
    spend_accounted: bool,
    model: str | None = None,
    reason_code: str | None = None,
    source: str = "proxy",
) -> dict[str, Any]:
    """Record one privacy-safe terminal event for a proxied agent session.

    ``capture_id`` makes finalization idempotent inside a process. The event is
    deliberately separate from ``model_call``: blocked/cancelled sessions prove
    policy activity, but they are not spend and must not affect savings math.
    """
    root = project_root.expanduser().resolve()
    stable_id = str(capture_id).strip()
    if not stable_id:
        raise ValueError("capture_id is required")
    with _LEDGER_LOCK:
        for event in read_events(root):
            if (
                event.get("event_type") == EVENT_CAPTURE_SESSION
                and event.get("capture_id") == stable_id
            ):
                return event
        fields: dict[str, Any] = {
            "capture_id": stable_id,
            "agent": str(agent).strip().lower(),
            "mode": str(mode).strip().lower(),
            "model": str(model or ""),
            "outcome": str(outcome).strip().lower(),
            "captured": bool(captured),
            "paid": bool(paid),
            "spend_accounted": bool(spend_accounted),
            "source": str(source or "proxy").strip().lower(),
        }
        if reason_code:
            fields["reason_code"] = str(reason_code).strip().lower()
        return record_event(
            root,
            EVENT_CAPTURE_SESSION,
            task=task,
            **fields,
        )


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
    agent: str | None = None,
    repo: str | None = None,
    source: str = "route",
) -> dict[str, Any]:
    """Record a routing decision plus its estimated savings and compaction.

    ``agent`` (claude/codex/cursor/cline/copilot), ``repo``, and ``source``
    (route/benchmark) let the ledger roll up by client, repo, and origin so a
    benchmark event and a normal routed action share one schema (#49).
    """
    root = project_root.expanduser().resolve()
    cost_model = load_cost_model(root)
    savings = estimate_route_savings(
        model_tier, task_tokens=task_tokens, model=cost_model
    )
    context_tokens_saved = 0
    if full_context_chars and compact_context_chars:
        delta = max(0, full_context_chars - compact_context_chars)
        context_tokens_saved = estimate_tokens("x" * delta, cost_model)
    extra: dict[str, Any] = {"source": source}
    if agent:
        extra["agent"] = agent
    if repo:
        extra["repo"] = repo
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
        **extra,
        **savings,
    )


def record_cache_lookup(
    project_root: Path,
    task: str,
    *,
    cache_kind: str,
    outcome: str,
    reason: str | None = None,
    age_seconds: int | None = None,
    avoided_model_call: bool = False,
    source: str = "ask",
) -> dict[str, Any]:
    """Record privacy-safe cache evidence without affecting spend or savings."""
    normalized_outcome = str(outcome).strip().lower()
    if normalized_outcome not in {
        "hit",
        "miss",
        "expired",
        "schema_mismatch",
        "corrupt",
        "bypass",
    }:
        raise ValueError(f"unknown cache outcome: {outcome}")
    fields: dict[str, Any] = {
        "cache_kind": str(cache_kind).strip().lower(),
        "outcome": normalized_outcome,
        "avoided_model_call": bool(avoided_model_call),
        "source": str(source).strip().lower(),
    }
    if reason:
        fields["reason"] = str(reason).strip().lower()
    if age_seconds is not None:
        fields["age_seconds"] = max(0, int(age_seconds))
    return record_event(project_root, EVENT_CACHE, task=task, **fields)


def record_model_call(
    project_root: Path,
    task: str,
    *,
    model_tier: str,
    provider_type: str,
    tokens: int,
    confirmed: bool,
    real_cost_usd: float | None = None,
    store_summary: bool = False,
    model_id: str | None = None,
    provider_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    measurement: str = "estimated",
    quota_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record an actual model call (cloud or local) and its estimated cost.

    When ``real_cost_usd`` is provided (e.g. claude's ``total_cost_usd``),
    it is used as-is so the ledger reflects the true spend rather than an
    estimate. When None, the tier rate is used as a fallback.
    """
    root = project_root.expanduser().resolve()
    cost_model = load_cost_model(root)
    cost = (
        float(real_cost_usd)
        if real_cost_usd is not None
        else tier_cost(model_tier, tokens, cost_model)
    )
    metadata: dict[str, Any] = {"measurement": str(measurement or "estimated")}
    if model_id:
        metadata["model_id"] = str(model_id)
    if provider_id:
        metadata["provider_id"] = str(provider_id)
    if input_tokens is not None:
        metadata["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        metadata["output_tokens"] = int(output_tokens)
    if quota_snapshot:
        metadata["quota_snapshot"] = dict(quota_snapshot)
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
        **metadata,
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


_SUMMARY_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_SUMMARY_CACHE_LOCK = threading.RLock()


def _ledger_signature(path: Path) -> tuple[int, int]:
    """(size, mtime_ns) of the ledger file; (0, 0) when it does not exist."""
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (int(stat.st_size), int(stat.st_mtime_ns))


def clear_ledger_summary_cache() -> None:
    with _SUMMARY_CACHE_LOCK:
        _SUMMARY_CACHE.clear()


def summarize_ledger(project_root: Path) -> dict[str, Any]:
    """Aggregate the local ledger into cost-control signals. Read-only.

    Performance (#153): the ledger JSONL is append-only and grows forever, and
    this headline read runs on every chat send *and* after every answer. The
    result is cached by the file's (size, mtime); an append changes both, so
    the cache invalidates itself correctly and repeated reads are O(1).
    """
    root = project_root.expanduser().resolve()
    signature = _ledger_signature(ledger_path(root))
    key = str(root)
    with _SUMMARY_CACHE_LOCK:
        cached = _SUMMARY_CACHE.get(key)
        if cached is not None and cached[:2] == signature:
            return copy.deepcopy(cached[2])
    events = read_events(root)
    all_routes = [event for event in events if event.get("event_type") == EVENT_ROUTE]
    # Savings truth (#76): only routes with a known tier have a verifiable
    # cost basis. Legacy events (e.g. the old "CLOUD" pseudo-tier, whose $0
    # rate inflated savings to the full baseline) are counted separately and
    # excluded from every trusted total below.
    known_tiers = {"L0", "L1", "L2", "L3", "L4"}
    routes = [
        event
        for event in all_routes
        if str(event.get("model_tier", "")).upper() in known_tiers
    ]
    legacy_routes = [
        event
        for event in all_routes
        if str(event.get("model_tier", "")).upper() not in known_tiers
    ]
    model_calls = [
        event for event in events if event.get("event_type") == EVENT_MODEL_CALL
    ]
    capture_sessions = [
        event for event in events if event.get("event_type") == EVENT_CAPTURE_SESSION
    ]

    by_tier: dict[str, int] = {}
    for route in routes:
        tier = str(route.get("model_tier", "L0")).upper()
        by_tier[tier] = by_tier.get(tier, 0) + 1

    cloud_calls_avoided = sum(1 for route in routes if route.get("cloud_call_avoided"))
    local_routes = sum(1 for route in routes if route.get("is_local_route"))
    captured_sessions = sum(1 for event in capture_sessions if event.get("captured"))
    uncaptured_sessions = len(capture_sessions) - captured_sessions
    capture_rate = (
        round(100 * captured_sessions / len(capture_sessions), 1)
        if capture_sessions
        else None
    )
    outcomes: dict[str, int] = {}
    for event in capture_sessions:
        outcome = str(event.get("outcome") or "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    summary = {
        "project": str(root),
        "ledger_path": str(ledger_path(root)),
        "event_count": len(events),
        "route_count": len(routes),
        "legacy_route_count": len(legacy_routes),
        "model_call_count": len(model_calls),
        "routes_by_tier": dict(sorted(by_tier.items())),
        "local_routes": local_routes,
        "cloud_calls_avoided": cloud_calls_avoided,
        "estimated_baseline_usd": _sum(routes, "estimated_baseline_usd"),
        "route_estimated_actual_usd": _sum(routes, "estimated_actual_usd"),
        "estimated_actual_spend_usd": _sum(model_calls, "estimated_actual_usd"),
        "estimated_savings_usd": _sum(routes, "estimated_savings_usd"),
        "context_chars_saved": int(_sum(routes, "context_chars_saved")),
        "context_tokens_saved": int(_sum(routes, "context_tokens_saved")),
        "capture": {
            "observed_sessions": len(capture_sessions),
            # Measurable = sessions OPai actually observed (the denominator).
            # Pass-through = observed but not captured (fail-open/unsupported).
            # Unmeasured = direct unwrapped launches OPai never saw: unknown by
            # definition, so they are NOT counted here (#9).
            "measurable_sessions": len(capture_sessions),
            "captured_sessions": captured_sessions,
            "uncaptured_sessions": uncaptured_sessions,
            "pass_through_sessions": uncaptured_sessions,
            "unmeasured_sessions": "unknown",
            "rate_percent": capture_rate,
            "denominator": "observed proxy sessions",
            "definition": dict(CAPTURE_RATE_DEFINITION),
            "label": (
                f"{capture_rate:g}% of observed proxy sessions captured"
                if capture_rate is not None
                else "No proxy sessions observed"
            ),
            "outcomes": dict(sorted(outcomes.items())),
            "scope": "Observed OPai proxy sessions only",
            "caveat": (
                "Direct unwrapped agent launches are not measurable yet and are not "
                "included in this rate. Client readiness is not session capture."
            ),
        },
        "privacy": "Raw prompts are never stored; only one-way task hashes and counts.",
    }
    with _SUMMARY_CACHE_LOCK:
        _SUMMARY_CACHE[key] = (signature[0], signature[1], copy.deepcopy(summary))
    return summary


def _iso_week(created_at: str) -> str:
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "unknown"
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _bucket(
    routes: list[dict[str, Any]], key_fn, *, default: str = "unknown"
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for route in routes:
        key = key_fn(route) or default
        slot = buckets.setdefault(
            key,
            {"routes": 0, "estimated_savings_usd": 0.0, "cloud_calls_avoided": 0},
        )
        slot["routes"] += 1
        value = route.get("estimated_savings_usd")
        if isinstance(value, (int, float)):
            slot["estimated_savings_usd"] = round(
                slot["estimated_savings_usd"] + value, 6
            )
        if route.get("cloud_call_avoided"):
            slot["cloud_calls_avoided"] += 1
    return dict(sorted(buckets.items()))


def rollup_ledger(project_root: Path) -> dict[str, Any]:
    """Roll up savings by day, week, month, agent, and repo (#49). Read-only."""
    root = project_root.expanduser().resolve()
    routes = [
        event for event in read_events(root) if event.get("event_type") == EVENT_ROUTE
    ]
    return {
        "project": str(root),
        "totals": summarize_ledger(root),
        "by_day": _bucket(routes, lambda r: str(r.get("created_at", ""))[:10]),
        "by_week": _bucket(routes, lambda r: _iso_week(str(r.get("created_at", "")))),
        "by_month": _bucket(routes, lambda r: str(r.get("created_at", ""))[:7]),
        "by_agent": _bucket(routes, lambda r: r.get("agent")),
        "by_repo": _bucket(routes, lambda r: r.get("repo")),
        "by_source": _bucket(routes, lambda r: r.get("source")),
    }
