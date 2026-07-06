from __future__ import annotations

from pathlib import Path
from typing import Any

from .cost_model import load_cost_model
from .ledger import summarize_ledger


def _per_tier_explainer(cost_model: dict[str, Any]) -> list[dict[str, Any]]:
    table = cost_model.get("tier_usd_per_1k_tokens", {})
    labels = {
        "L0": "deterministic local tools / cached context",
        "L1": "local small model",
        "L2": "confirmed cheap cloud model",
        "L3": "strong frontier model",
        "L4": "max frontier model (gated)",
    }
    rows = []
    for tier in ["L0", "L1", "L2", "L3", "L4"]:
        rows.append(
            {
                "tier": tier,
                "meaning": labels[tier],
                "usd_per_1k_tokens": float(table.get(tier, 0.0)),
                "is_baseline": tier == str(cost_model.get("baseline_tier", "L3")),
            }
        )
    return rows


def build_savings_report(project_root: Path) -> dict[str, Any]:
    """User-facing cost-firewall report. Pure read of the local ledger."""
    root = project_root.expanduser().resolve()
    cost_model = load_cost_model(root)
    summary = summarize_ledger(root)

    baseline = summary["estimated_baseline_usd"]
    savings = summary["estimated_savings_usd"]
    pct = round((savings / baseline) * 100, 1) if baseline else 0.0

    has_data = summary["route_count"] > 0
    headline = (
        f"OPai estimates ${savings:.4f} saved across "
        f"{summary['route_count']} routed task(s) "
        f"({pct:.1f}% vs un-routed {summary.get('estimated_baseline_usd') and cost_model.get('baseline_tier', 'L3')} baseline)."
        if has_data
        else 'No routed tasks recorded yet. Run: opai route "<task>" --record'
    )

    return {
        "report": "opai-savings",
        "project": str(root),
        "headline": headline,
        "has_data": has_data,
        "baseline_tier": cost_model.get("baseline_tier", "L3"),
        "totals": {
            "routed_tasks": summary["route_count"],
            "local_routes": summary["local_routes"],
            "cloud_calls_avoided": summary["cloud_calls_avoided"],
            "estimated_baseline_usd": baseline,
            "estimated_actual_spend_usd": summary["estimated_actual_spend_usd"],
            "estimated_savings_usd": savings,
            "estimated_savings_percent": pct,
            "legacy_routes_excluded": summary.get("legacy_route_count", 0),
            "context_chars_saved": summary["context_chars_saved"],
            "context_tokens_saved": summary["context_tokens_saved"],
        },
        "routes_by_tier": summary["routes_by_tier"],
        "tier_cost_model": _per_tier_explainer(cost_model),
        "assumptions": [
            "Savings are estimates, not invoices.",
            f"Baseline assumes un-routed {cost_model.get('baseline_tier', 'L3')} usage for every task.",
            f"~{cost_model.get('chars_per_token', 4)} characters per token.",
            "Tune hub/model-intelligence/cost_model.yaml to match your providers.",
            "Paid provider calls record spend with zero implied savings.",
        ]
        + (
            [
                f"{summary.get('legacy_route_count', 0)} legacy route event(s) with an "
                "unverifiable cost basis are excluded from these totals."
            ]
            if summary.get("legacy_route_count", 0)
            else []
        ),
        "privacy": summary["privacy"],
        "next_steps": [
            'Record more routes with: opai route "<task>" --record',
            "Inspect raw events under .opaihub/ledger/usage.jsonl (local only).",
            "See per-tier cost meaning in tier_cost_model above.",
        ],
    }


def render_savings_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# OPai Savings Report",
        "",
        f"**{report['headline']}**",
        "",
        "| Signal | Value |",
        "| --- | --- |",
        f"| Routed tasks | {totals['routed_tasks']} |",
        f"| Local routes | {totals['local_routes']} |",
        f"| Cloud calls avoided | {totals['cloud_calls_avoided']} |",
        f"| Estimated baseline spend | ${totals['estimated_baseline_usd']:.4f} |",
        f"| Estimated actual spend | ${totals['estimated_actual_spend_usd']:.4f} |",
        f"| Estimated savings | ${totals['estimated_savings_usd']:.4f} ({totals['estimated_savings_percent']}%) |",
        f"| Context characters saved | {totals['context_chars_saved']} |",
        f"| Context tokens saved (est.) | {totals['context_tokens_saved']} |",
        "",
        "## Assumptions",
        "",
    ]
    lines.extend(f"- {item}" for item in report["assumptions"])
    lines.extend(["", f"_{report['privacy']}_", ""])
    return "\n".join(lines)
