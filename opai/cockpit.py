from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from opai import __brand__
from opai.integrations import project_status
from opai.release_identity import surface_identity_payload
from opaihub.benchmark import latest_benchmark_report
from opaihub.budget import budget_status
from opaihub.cost_telemetry import summarize_cost_telemetry
from opaihub.local_models import discover_local_models
from opaihub.savings import build_savings_report


def _client_counts(status: dict[str, Any]) -> dict[str, int]:
    summary = status["client_integrations"]["summary"]
    active = len(summary["active"])
    broken = len(summary["broken"])
    missing = len(summary["missing"])
    return {
        "active": active,
        "broken": broken,
        "missing": missing,
        "total": active + broken + missing,
    }


def _wrapper_status(status: dict[str, Any]) -> dict[str, Any]:
    wrappers = status["global"]["wrappers"]
    bin_dir = Path.home() / ".opai" / "bin"
    path_parts = [
        Path(part).expanduser()
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if part
    ]
    bin_on_path = any(str(part).lower() == str(bin_dir).lower() for part in path_parts)
    return {
        "bin_on_path": bin_on_path,
        "shell_aliases_installed": bool(
            status["global"].get("shell_aliases_installed")
        ),
        "wrappers": {
            name: {
                "exists": bool(data.get("exists")),
                "path": data.get("path"),
                "launch_command": f"vesta launch {name}",
            }
            for name, data in wrappers.items()
        },
    }


def _benchmark_status(project_root: Path) -> dict[str, Any]:
    report = latest_benchmark_report(project_root)
    if report is None:
        return {
            "present": False,
            "claim": "No benchmark run recorded yet.",
            "next_command": "vesta benchmark run --suite max --mode both",
        }
    claim = report.get("claim_readiness", {})
    score = report.get("efficiency_score", {})
    return {
        "present": True,
        "run_id": report.get("run_id"),
        "suite": report.get("suite"),
        "claim": claim.get("public_claim", "Benchmark proof recorded."),
        "status": claim.get("status", "unknown"),
        "effectiveness_index": score.get("opai_effectiveness_index"),
        "paid_calls_avoided": score.get("paid_calls_avoided"),
    }


def build_cockpit(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    status = project_status(root)
    counts = _client_counts(status)
    savings = build_savings_report(root)
    budget = budget_status(root)
    local_models = discover_local_models(root)
    readiness = (
        "on"
        if status["project"]["activated"]
        and counts["total"] > 0
        and counts["broken"] == 0
        and counts["missing"] == 0
        and status["stale_paths"]["ok"]
        else "attention"
    )
    return {
        "report": "opai-cockpit",
        **surface_identity_payload(brand=__brand__),
        "status": readiness,
        "project": {
            "root": str(root),
            "activated": status["project"]["activated"],
        },
        "clients": {
            **counts,
            "summary": status["client_integrations"]["summary"],
        },
        "wrappers": _wrapper_status(status),
        "savings": {
            "has_data": savings["has_data"],
            "estimated_savings_usd": savings["totals"]["estimated_savings_usd"],
            "routed_tasks": savings["totals"]["routed_tasks"],
            "cloud_calls_avoided": savings["totals"]["cloud_calls_avoided"],
            "context_tokens_saved": savings["totals"]["context_tokens_saved"],
        },
        "budget": {
            "panic": budget["panic"],
            "profile": budget["profile"],
            "caps": budget["caps"],
            "spent": budget["spent"],
        },
        "cost_telemetry": summarize_cost_telemetry(root),
        "benchmark": _benchmark_status(root),
        "local_models": {
            "available": local_models["available"],
            "commands": sorted(local_models.get("commands", {}).keys()),
            "next_command": "vesta models discover-local",
        },
        "next_actions": _next_actions(status, savings, budget),
        "privacy": "Local only. No telemetry, raw prompts, or secrets are transmitted.",
    }


def _next_actions(
    status: dict[str, Any], savings: dict[str, Any], budget: dict[str, Any]
) -> list[str]:
    actions: list[str] = []
    summary = status["client_integrations"]["summary"]
    if summary["broken"] or summary["missing"] or not status["stale_paths"]["ok"]:
        actions.append(
            "Run `vesta activate --repair` to restore Vesta client coverage."
        )
    wrappers = _wrapper_status(status)
    if wrappers["shell_aliases_installed"] and not wrappers["bin_on_path"]:
        actions.append(
            "Open a new shell or reload your profile so Vesta client wrappers are visible."
        )
    if not savings["has_data"]:
        actions.append(
            'Run `vesta route "<task>" --record` or `vesta quickstart` to prove real savings.'
        )
    if budget["panic"]:
        actions.append(
            "Panic mode is ON; disable with `vesta budget panic --off` when ready."
        )
    if not actions:
        actions.append(
            "Vesta is active. Launch agents through `vesta launch <client>` or your Vesta shell aliases."
        )
    return actions


def compact_statusline(payload: dict[str, Any]) -> str:
    status = "ON" if payload["status"] == "on" else "ATTENTION"
    clients = payload["clients"]
    saved = float(payload["savings"]["estimated_savings_usd"])
    budget = "panic" if payload["budget"]["panic"] else "budget ok"
    return f"Vesta {status} | {clients['active']}/{clients['total']} clients | ${saved:.2f} saved | {budget}"


def _provider_telemetry_line(telemetry: dict[str, Any]) -> str:
    # #475: when events were torn/unreadable the totals are a lower bound,
    # not the complete truth — say so instead of looking authoritative.
    skipped = int(telemetry.get("skipped_events") or 0)
    degraded = bool(telemetry.get("degraded"))
    if telemetry["has_data"]:
        line = (
            f"${telemetry['actual_usd']:.4f} actual"
            f" / ${telemetry['derived_usd']:.4f} derived"
            f" / ${telemetry['estimated_usd']:.4f} estimated"
            f" across {telemetry['calls']} call(s)"
        )
        if degraded:
            line += (
                f" (partial — {skipped} event(s) unreadable; totals are a lower bound)"
            )
        return line
    if degraded:
        return f"{skipped} provider event(s) unreadable; no readable calls recorded"
    return "no provider calls recorded yet"


def render_cockpit(payload: dict[str, Any]) -> str:
    state = "ON" if payload["status"] == "on" else "NEEDS ATTENTION"
    clients = payload["clients"]
    savings = payload["savings"]
    budget = payload["budget"]
    telemetry = payload["cost_telemetry"]
    benchmark = payload["benchmark"]
    local = payload["local_models"]
    wrappers = payload["wrappers"]
    lines = [
        f"Vesta {state}",
        f"Version: {payload['version']} {payload['release_stage']}",
        f"Project: {payload['project']['root']}",
        "",
        f"Clients: {clients['active']}/{clients['total']} active",
        f"- Active: {', '.join(clients['summary']['active']) or 'none'}",
        f"- Broken: {', '.join(clients['summary']['broken']) or 'none'}",
        f"- Missing: {', '.join(clients['summary']['missing']) or 'none'}",
        "",
        f"Wrappers: {'ready' if wrappers['shell_aliases_installed'] else 'manual launch'}",
        f"- ~/.opai/bin on PATH: {wrappers['bin_on_path']}",
        "",
        f"Savings: ${savings['estimated_savings_usd']:.2f} saved across {savings['routed_tasks']} routed tasks",
        f"- Cloud calls avoided: {savings['cloud_calls_avoided']}",
        f"- Context tokens saved: {savings['context_tokens_saved']}",
        "",
        f"Budget: {'panic mode ON' if budget['panic'] else 'budget ok'} ({budget['profile']})",
        f"- Spent today: ${budget['spent']['today_usd']:.4f}",
        f"- Spent this month: ${budget['spent']['month_usd']:.4f}",
        "",
        f"Provider telemetry: {_provider_telemetry_line(telemetry)}",
        "",
        f"Benchmark: {benchmark['claim']}",
        f"Local model / ask: {'available' if local['available'] else 'not running'}",
        "",
        "Next:",
    ]
    lines.extend(f"- {action}" for action in payload["next_actions"])
    lines.extend(["", payload["privacy"]])
    return "\n".join(lines) + "\n"
