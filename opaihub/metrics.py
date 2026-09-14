"""Local product metrics aligned to the Vesta business strategy.

Reports the strategy's measurable signals - estimated tokens avoided, cloud
escalations avoided, savings, route activity, and cache efficiency - from local
files only. No telemetry. Client-readiness is merged in by the CLI layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .ledger import summarize_ledger
from .runs import recent_runs, runs_path


def build_local_metrics(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    ledger = summarize_ledger(root)

    # Read the whole run history for cache efficiency, not just the tail.
    history: list[dict[str, Any]] = []
    path = runs_path(root)
    if path.exists():
        history = recent_runs(root, limit=10_000)
    run_count = len(history)
    cache_hits = sum(1 for run in history if run.get("cache_hit"))
    cache_hit_rate = round(cache_hits / run_count, 3) if run_count else 0.0

    return {
        "report": "opai-metrics",
        "project": str(root),
        "estimated_savings_usd": ledger["estimated_savings_usd"],
        "estimated_spend_usd": ledger["estimated_actual_spend_usd"],
        "cloud_escalations_avoided": ledger["cloud_calls_avoided"],
        "estimated_tokens_avoided": ledger["context_tokens_saved"],
        "routed_tasks": ledger["route_count"],
        "route_runs_recorded": run_count,
        "evidence_cache_hit_rate": cache_hit_rate,
        "routes_by_tier": ledger["routes_by_tier"],
        "local_route_share": (
            round(ledger["local_routes"] / ledger["route_count"], 3)
            if ledger["route_count"]
            else 0.0
        ),
        "notes": [
            "All metrics are local and private; nothing is transmitted.",
            "Cache hit rate measures evidence reuse efficiency on this project.",
        ],
    }
