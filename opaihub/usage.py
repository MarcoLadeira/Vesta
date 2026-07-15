"""Per-model usage snapshots built from OPai's privacy-safe local ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ledger import EVENT_MODEL_CALL, read_events


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _created_at(event: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(
            str(event.get("created_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return None


def _inside_window(event: dict[str, Any], window: str, now: datetime) -> bool:
    created = _created_at(event)
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if window == "minute":
        start = now - timedelta(minutes=1)
    elif window == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return created >= start


def build_usage_snapshots(
    project_root: Path,
    models: list[dict[str, Any]],
    *,
    limits: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    events = [
        event
        for event in read_events(project_root)
        if event.get("event_type") == EVENT_MODEL_CALL
    ]
    configured_limits = limits or {}
    now = datetime.now(timezone.utc)
    snapshots: list[dict[str, Any]] = []
    for model in models:
        model_id = str(model.get("id") or "")
        provider = str(model.get("provider") or model.get("kind") or "opai")
        matched = [event for event in events if event.get("model_id") == model_id]
        soft = configured_limits.get(model_id) or {}
        soft_window = str(soft.get("window") or "month")
        window_events = [
            event for event in matched if _inside_window(event, soft_window, now)
        ]
        used_tokens = int(sum(_number(event.get("tokens")) for event in window_events))
        # #334: model calls in-window. A tool-loop task is many calls, so this
        # is what makes a large token total legible (older events without the
        # field count as the one call they represent).
        model_calls = int(
            sum(int(_number(event.get("model_calls")) or 1) for event in window_events)
        )
        task_count = len(window_events)
        latest_quota = next(
            (
                event.get("quota_snapshot")
                for event in reversed(matched)
                if isinstance(event.get("quota_snapshot"), dict)
            ),
            None,
        )
        updated = matched[-1].get("created_at") if matched else None
        if latest_quota and _number(latest_quota.get("limit")) > 0:
            limit = _number(latest_quota.get("limit"))
            remaining = max(0.0, _number(latest_quota.get("remaining")))
            used = max(0.0, limit - remaining)
            metric = str(latest_quota.get("metric") or "requests")
            window = str(latest_quota.get("window") or "unknown")
            source = "provider"
            confidence = "provider-reported"
            requires_confirmation = False
        else:
            metric = str(soft.get("metric") or "tokens")
            limit = _number(soft.get("limit")) or None
            used = float(len(window_events) if metric == "requests" else used_tokens)
            remaining = max(0.0, limit - used) if limit is not None else None
            window = str(soft.get("window") or "month")
            source = "opai"
            confidence = "measured" if matched else "no-data"
            requires_confirmation = bool(limit is not None and used >= limit)
        percent = round(min(100.0, 100.0 * used / limit), 1) if limit else None
        snapshots.append(
            {
                "modelId": model_id,
                "provider": provider,
                "source": source,
                "metric": metric,
                "used": int(used) if used.is_integer() else round(used, 2),
                "limit": int(limit)
                if isinstance(limit, float) and limit.is_integer()
                else limit,
                "remaining": (
                    int(remaining)
                    if isinstance(remaining, float) and remaining.is_integer()
                    else remaining
                ),
                "percent": percent,
                "window": window,
                "resetsAt": latest_quota.get("resetsAt") if latest_quota else None,
                "confidence": confidence,
                "updatedAt": updated,
                "requiresConfirmation": requires_confirmation,
                # #334: how the token total was actually produced.
                "modelCalls": model_calls,
                "taskCount": task_count,
            }
        )
    return snapshots


def usage_limit_gate(
    project_root: Path,
    model_id: str,
    *,
    provider: str,
    limits: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return the selected model's current soft-limit decision."""

    return build_usage_snapshots(
        project_root,
        [{"id": model_id, "provider": provider}],
        limits=limits,
    )[0]
