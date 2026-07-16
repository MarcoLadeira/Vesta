"""Normalized provider cost telemetry for the agent runtime (#178).

One canonical shape for what a provider call actually cost: tokens, dollars,
and quota, with the measurement honestly labelled. ``actual`` means the
provider reported dollars itself (claude's ``total_cost_usd``); ``derived``
means dollars were computed from provider-reported tokens and a known rate;
``estimated`` means OPai guessed. Telemetry lands in the redacted workflow
ledger (``cost_telemetry`` events) and is summarized for the cockpit.

Recording telemetry never authorizes spend: the existing confirmation and
usage-limit gates run before any call, unchanged.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .command_runner import redact
from .state import state_dir
from .workflow_ledger import WorkflowLedger

COST_MEASUREMENTS = {"actual", "derived", "estimated", "mixed", "unknown"}
TOKEN_MEASUREMENTS = {"provider", "derived", "estimated", "mixed", "unknown"}

# Rate-limit/quota headers worth keeping, lowercased. Values are short
# numeric/date strings; anything else a provider sends is dropped.
_QUOTA_HEADER_PREFIXES = (
    "x-ratelimit-",
    "anthropic-ratelimit-",
    "x-quota-",
    "retry-after",
)
_QUOTA_FIELD_NAMES = {
    "limit",
    "observed_at",
    "remaining",
    "requests_remaining",
    "reset",
    "reset_at",
    "resetat",
    "retry_after",
    "retryafter",
    "stale",
    "status",
    "tokens_remaining",
    "used",
    "window",
}


@dataclass(frozen=True)
class CostTelemetry:
    provider: str
    model: str = ""
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    total_tokens: int | None = 0
    cost_usd: float | None = None
    cost_measurement: str = "estimated"
    tokens_measurement: str = "estimated"
    quota: dict[str, str] = field(default_factory=dict)
    source: str = ""

    def __post_init__(self) -> None:
        if self.cost_measurement not in COST_MEASUREMENTS:
            raise ValueError(f"Unknown cost measurement: {self.cost_measurement!r}")
        if self.tokens_measurement not in TOKEN_MEASUREMENTS:
            raise ValueError(f"Unknown tokens measurement: {self.tokens_measurement!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def quota_from_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Keep only recognizable rate-limit/quota headers, lowercased."""

    quota = {}
    for key, value in (headers or {}).items():
        name = str(key).lower().strip()
        if name.startswith(_QUOTA_HEADER_PREFIXES):
            quota[name] = str(value)[:64]
    return quota


def _quota_from_report(quota: Mapping[str, Any] | None) -> dict[str, str]:
    """Allow only bounded quota metadata and redact even allowlisted values."""

    safe: dict[str, str] = {}
    for key, value in (quota or {}).items():
        name = str(key).lower().strip()
        if not (name in _QUOTA_FIELD_NAMES or name.startswith(_QUOTA_HEADER_PREFIXES)):
            continue
        safe[name] = redact(str(value))[:64]
    return safe


def normalize_account_result(
    provider_id: str,
    result: Mapping[str, Any] | None,
    *,
    model: str = "",
) -> CostTelemetry:
    """Normalize an account-CLI result (claude/codex) into telemetry.

    Claude reports ``total_cost_usd`` (surfaced as ``cost``/``cost_usd``);
    that is an *actual* dollar figure. Codex reports no dollars, so its cost
    stays honestly ``None``/estimated rather than a fabricated zero.
    """

    payload = dict(result or {})
    cost = payload.get("cost_usd", payload.get("cost"))
    actual = isinstance(cost, (int, float)) and not isinstance(cost, bool)
    return CostTelemetry(
        provider=str(provider_id or "").strip().lower(),
        model=str(model or payload.get("model") or ""),
        cost_usd=float(cost) if actual else None,
        cost_measurement="actual" if actual else "estimated",
        tokens_measurement="estimated",
        source="account_cli",
    )


def normalize_api_usage(
    provider_id: str,
    body: Mapping[str, Any] | None,
    headers: Mapping[str, Any] | None = None,
    *,
    model: str = "",
    usd_per_1k_tokens: float | None = None,
) -> CostTelemetry:
    """Normalize an HTTP usage body plus quota headers into telemetry.

    Token counts come straight from the provider when a ``usage`` block is
    present. Dollars are ``derived`` when a rate is supplied for
    provider-reported tokens - never labelled actual.
    """

    from .provider_adapters import adapter_for

    usage = adapter_for(provider_id).extract_usage(dict(body or {}), None)
    provider_tokens = usage["measurement"] == "provider"
    total = int(usage["tokens"])
    cost_usd = None
    cost_measurement = "estimated"
    if usd_per_1k_tokens is not None and provider_tokens:
        cost_usd = round(total / 1000.0 * float(usd_per_1k_tokens), 6)
        cost_measurement = "derived"
    return CostTelemetry(
        provider=str(provider_id or "").strip().lower(),
        model=str(model or ""),
        input_tokens=int(usage["input_tokens"]),
        output_tokens=int(usage["output_tokens"]),
        total_tokens=total,
        cost_usd=cost_usd,
        cost_measurement=cost_measurement,
        tokens_measurement="provider" if provider_tokens else "estimated",
        quota=quota_from_headers(headers),
        source="usage_api",
    )


def estimated_telemetry(
    provider_id: str,
    *,
    tokens: int = 0,
    cost_usd: float | None = None,
    model: str = "",
) -> CostTelemetry:
    """Telemetry for a call where OPai only has its own estimates."""

    return CostTelemetry(
        provider=str(provider_id or "").strip().lower(),
        model=str(model or ""),
        total_tokens=max(0, int(tokens)),
        cost_usd=float(cost_usd) if cost_usd is not None else None,
        cost_measurement="estimated",
        tokens_measurement="estimated",
        source="estimate",
    )


def usage_report_to_cost_telemetry(report: Any) -> CostTelemetry:
    """Adapt the canonical usage report without re-estimating its values.

    This is the sole compatibility boundary between provider-turn accounting
    and the older workflow telemetry shape.  Token and dollar provenance are
    intentionally selected independently.
    """

    from .usage_report import UsageReport

    if not isinstance(report, UsageReport):
        raise TypeError("report must be a UsageReport")
    summary = report.summary()
    input_usage = summary["inputTokens"]
    output_usage = summary["outputTokens"]
    total_usage = summary["totalTokens"]
    cost_usage = summary["costUsd"]
    quota = _quota_from_report(report.latest_provider_quota)
    return CostTelemetry(
        provider=report.provider_id.strip().lower(),
        model=report.model_id,
        input_tokens=input_usage["value"],
        output_tokens=output_usage["value"],
        total_tokens=total_usage["value"],
        cost_usd=cost_usage["value"],
        cost_measurement=str(cost_usage["provenance"]),
        tokens_measurement=str(total_usage["provenance"]),
        quota=quota,
        source="usage_report",
    )


def record_workflow_cost(
    project_root: Path,
    task_id: str,
    telemetry: CostTelemetry,
    *,
    task: str = "",
) -> dict[str, Any]:
    """Append one redacted ``cost_telemetry`` event to the workflow ledger."""

    return WorkflowLedger(project_root, task_id=str(task_id)).append(
        "cost_telemetry",
        task=task,
        metadata=telemetry.to_dict(),
    )


def read_cost_events(
    project_root: Path, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Read ``cost_telemetry`` events across every workflow task."""

    path = state_dir(project_root.expanduser().resolve()) / "agent" / "events.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if limit is not None:
        lines = lines[-limit:]
    events = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if value.get("event_type") == "cost_telemetry":
            events.append(value)
    return events


def summarize_cost_telemetry(
    project_root: Path, *, limit: int = 2000
) -> dict[str, Any]:
    """Aggregate workflow cost telemetry for the cockpit.

    Actual, derived, and estimated dollars are kept apart so a real spend
    figure is never silently mixed with a guess.
    """

    totals = {
        "actual_usd": 0.0,
        "derived_usd": 0.0,
        "estimated_usd": 0.0,
        "mixed_usd": 0.0,
    }
    tokens = 0
    by_provider: dict[str, dict[str, Any]] = {}
    events = read_cost_events(project_root, limit=limit)
    for event in events:
        data = event.get("metadata") or {}
        provider = str(data.get("provider") or "unknown")
        measurement = str(data.get("cost_measurement") or "estimated")
        cost = data.get("cost_usd")
        entry = by_provider.setdefault(
            provider,
            {
                "actual_usd": 0.0,
                "derived_usd": 0.0,
                "estimated_usd": 0.0,
                "mixed_usd": 0.0,
                "tokens": 0,
                "calls": 0,
                "last_quota": {},
            },
        )
        entry["calls"] += 1
        call_tokens = int(data.get("total_tokens") or 0)
        entry["tokens"] += call_tokens
        tokens += call_tokens
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            key = f"{measurement}_usd" if measurement in COST_MEASUREMENTS else None
            if key and key in totals:
                totals[key] = round(totals[key] + float(cost), 6)
                entry[key] = round(entry[key] + float(cost), 6)
        quota = data.get("quota")
        if isinstance(quota, dict) and quota:
            entry["last_quota"] = {str(key): str(value) for key, value in quota.items()}
    return {
        "has_data": bool(events),
        "calls": len(events),
        "total_tokens": tokens,
        **totals,
        "by_provider": by_provider,
        "privacy": "Telemetry is local and redacted; no raw prompts are stored.",
    }
