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
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .atomic_io import read_utf8_tail_lines
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


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _token_count(value: Any) -> int | None:
    if not _is_finite_number(value):
        return None
    number = float(value)
    if number < 0 or not number.is_integer():
        return None
    return int(number)


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
        if self.cost_usd is not None and not _is_finite_number(self.cost_usd):
            raise ValueError("cost_usd must be a finite number or None")

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
    actual = _is_finite_number(cost)
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
    if _is_finite_number(usd_per_1k_tokens) and provider_tokens:
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
        cost_usd=float(cost_usd) if _is_finite_number(cost_usd) else None,
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


def _read_cost_events(
    project_root: Path, *, limit: int | None = None
) -> tuple[list[dict[str, Any]], int]:
    """``(events, skipped)`` — cost events plus the count of malformed lines.

    A torn line from a concurrent append or on-disk corruption is unparseable.
    Dropping it silently makes a spend summary look authoritative while it is
    actually missing evidence, so the skipped count is tracked and surfaced as a
    degraded/partial signal (#475) instead of vanishing.
    """

    path = state_dir(project_root.expanduser().resolve()) / "agent" / "events.jsonl"
    if not path.exists():
        return [], 0

    def matching(lines: list[str]) -> tuple[list[dict[str, Any]], int]:
        events: list[dict[str, Any]] = []
        skipped = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(value, dict):
                skipped += 1
                continue
            if value.get("event_type") == "cost_telemetry":
                events.append(value)
        return events, skipped

    if limit is None:
        return matching(path.read_text(encoding="utf-8", errors="replace").splitlines())
    target = max(0, int(limit))
    if target == 0:
        return [], 0
    window = max(64, target * 2)
    previous_line_count = -1
    while True:
        lines = read_utf8_tail_lines(path, window)
        events, skipped = matching(lines)
        if len(events) >= target:
            return events[-target:], skipped
        line_count = len(lines)
        if line_count < window or line_count == previous_line_count:
            return events, skipped
        previous_line_count = line_count
        window *= 2


def read_cost_events(
    project_root: Path, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Read ``cost_telemetry`` events across every workflow task."""

    return _read_cost_events(project_root, limit=limit)[0]


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
    events, skipped = _read_cost_events(project_root, limit=limit)
    invalid_events = 0
    for event in events:
        data = event.get("metadata")
        if not isinstance(data, dict):
            invalid_events += 1
            continue
        invalid = False
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
        call_tokens = _token_count(data.get("total_tokens"))
        if call_tokens is None:
            invalid = data.get("total_tokens") not in (None, 0)
            call_tokens = 0
        entry["tokens"] += call_tokens
        tokens += call_tokens
        if _is_finite_number(cost):
            key = f"{measurement}_usd" if measurement in COST_MEASUREMENTS else None
            if key and key in totals:
                total = totals[key] + float(cost)
                provider_total = entry[key] + float(cost)
                if math.isfinite(total) and math.isfinite(provider_total):
                    totals[key] = round(total, 6)
                    entry[key] = round(provider_total, 6)
                else:
                    invalid = True
        elif cost is not None:
            invalid = True
        quota = data.get("quota")
        if isinstance(quota, dict) and quota:
            entry["last_quota"] = {str(key): str(value) for key, value in quota.items()}
        if invalid:
            invalid_events += 1
    return {
        "has_data": bool(events),
        "calls": len(events),
        "total_tokens": tokens,
        **totals,
        "by_provider": by_provider,
        # #475: a summary built over corrupted/torn events is partial, not
        # authoritative — surface that so receipts/UI never present an
        # under-counted total as the complete truth.
        "complete": skipped + invalid_events == 0,
        "degraded": skipped + invalid_events > 0,
        "skipped_events": skipped + invalid_events,
        "privacy": "Telemetry is local and redacted; no raw prompts are stored.",
    }
