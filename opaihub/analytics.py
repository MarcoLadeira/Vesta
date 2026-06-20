from __future__ import annotations

from pathlib import Path
from typing import Any

from .health import health_history
from .ledger import summarize_ledger
from .loader import registry_items
from .state import effective_mcp_servers, effective_tools


def build_analytics_summary(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    history = health_history(root, limit=20)
    ledger = summarize_ledger(root)
    latest_results = history[-1]["results"] if history else []
    failures = [
        item
        for item in latest_results
        if item.get("status") not in {"ok", "disabled", "unknown"}
    ]
    models = registry_items("models", root)
    enabled_cloud_models = [
        model["id"]
        for model in models
        if model.get("enabled_by_default")
        and str(model.get("provider", "")).startswith("cloud")
    ]
    return {
        "project": str(root),
        "registry_counts": {
            name: len(registry_items(name, root))
            for name in ["tools", "agents", "workflows", "mcp_servers", "models"]
        },
        "effective_enabled": {
            "tools": [
                tool["id"]
                for tool in effective_tools(root)
                if tool.get("effective_enabled")
            ],
            "mcp_servers": [
                server["id"]
                for server in effective_mcp_servers(root)
                if server.get("effective_enabled")
            ],
        },
        "health_events": len(history),
        "latest_failures": failures,
        "estimated_spend_usd": ledger["estimated_actual_spend_usd"],
        "estimated_savings_usd": ledger["estimated_savings_usd"],
        "routed_tasks": ledger["route_count"],
        "cloud_calls_avoided": ledger["cloud_calls_avoided"],
        "context_tokens_saved": ledger["context_tokens_saved"],
        "ledger_event_count": ledger["event_count"],
        "enabled_cloud_models": enabled_cloud_models,
        "notes": "Analytics are local and file-based in 0.2.0 alpha.1; spend and savings come from the local usage ledger. No telemetry leaves the machine.",
    }
