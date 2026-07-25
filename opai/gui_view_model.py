"""Presentation view-model for the OPai premium desktop GUI.

This module stays PySide-free so tests, `opai gui --once`, and headless CLI
paths never need desktop dependencies. It adapts the local app-state layer into
safe display data, action metadata, icons, and confirmation requirements.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from opai import app_state as A

SECTION_META: list[tuple[str, str, str]] = [
    ("home", "Home", "wallet"),
    ("agents", "Agents", "users"),
    ("firewall", "Cost Firewall", "shield"),
    ("context", "Context Waste", "database"),
    ("benchmark", "Benchmark", "gauge"),
    ("proof", "Proof Bundle", "package-check"),
    ("workflows", "Workflows", "activity"),
    ("launch", "Launch", "rocket"),
]

SECTIONS: list[tuple[str, str]] = [(key, label) for key, label, _icon in SECTION_META]

THEME: dict[str, Any] = {
    "name": "opai-premium-dark",
    "font": {
        # One soft typeface across every OPai surface (see opai/assets/fonts).
        "body": '"Nunito", "Segoe UI Variable", "Segoe UI", sans-serif',
        "mono": '"Cascadia Code", "JetBrains Mono", Consolas, monospace',
    },
    "colors": {
        "bg": "#070b12",
        "panel": "#0d131f",
        "panel_raised": "#111a29",
        "panel_soft": "#0a101a",
        "line": "#223047",
        "ink": "#f5f8fc",
        "muted": "#9aa8ba",
        "faint": "#627086",
        "teal": "#26e0d0",
        "green": "#5ff0a7",
        "blue": "#7ab7ff",
        "amber": "#ffc857",
        "coral": "#ff6b7a",
    },
}

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*\S+"),
]


def _redact(text: object) -> str:
    value = str(text)
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(r"\1=[redacted]" if pattern.groups else "[redacted]", value)
    value = value.replace("SECRET", "[redacted]")
    return value


def _money(value: object) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _num(value: object) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _action(
    action_id: str,
    label: str,
    *,
    icon: str,
    command: str | None = None,
    mutates: bool = False,
    risk: str = "read",
    confirmation: str = "",
    variant: str = "secondary",
) -> dict[str, Any]:
    requires = bool(mutates or risk in {"paid", "cloud", "destructive", "config"})
    return {
        "id": action_id,
        "label": label,
        "icon": icon,
        "command": command,
        "mutates": mutates,
        "risk": risk,
        "requires_confirmation": requires,
        "confirmation": confirmation if requires else "",
        "variant": variant,
    }


def _status_severity(status: str) -> str:
    if status in {"active", "on", "ok", "ready"}:
        return "success"
    if status in {"broken", "blocked", "attention"}:
        return "warning"
    if status in {"deny", "panic", "fail"}:
        return "danger"
    return "neutral"


def _client_card(client: dict[str, Any]) -> dict[str, Any]:
    repair = _redact(client.get("repair") or "opai activate --repair")
    return {
        "title": _redact(client.get("label") or client.get("id")),
        "subtitle": _redact(client.get("reason") or "OPai client integration status"),
        "status": _redact(client.get("status", "unknown")).replace("_", " ").upper(),
        "severity": _status_severity(str(client.get("status", "unknown"))),
        "metrics": [
            {
                "label": "Wrapper",
                "value": "installed" if client.get("wrapper_installed") else "missing",
                "severity": "success" if client.get("wrapper_installed") else "neutral",
            },
            {
                "label": "Config / rules",
                "value": "managed" if client.get("config_rules") else "not managed",
                "severity": "success" if client.get("config_rules") else "neutral",
            },
            {
                "label": "Capture",
                "value": str(client.get("wrapper_capture_mode", "missing")).replace(
                    "_", " "
                ),
                "severity": "success"
                if client.get("wrapper_capture_mode") == "selective_proxy"
                else "warning",
            },
            {
                "label": "Global",
                "value": (
                    "not required"
                    if not client.get("global_required", True)
                    else "ready"
                    if client.get("global_ready")
                    else "check"
                ),
                "severity": (
                    "neutral"
                    if not client.get("global_required", True)
                    else "success"
                    if client.get("global_ready")
                    else "neutral"
                ),
            },
        ],
        "command": repair,
        "actions": [
            _action("copy_repair", "Copy repair", icon="copy", command=repair),
        ],
    }


def build_view_model(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    state = A.full_state(root)
    overview = state["overview"]
    savings = overview["savings"]
    clients_summary = overview["clients"]
    budget = overview["budget"]
    has_savings = bool(savings.get("has_data"))
    savings_value = float(savings.get("estimated_savings_usd") or 0)
    protected_clients = (
        f"{clients_summary.get('active', 0)}/{clients_summary.get('total', 5)}"
    )
    capture = overview.get("capture") or {}
    capture_rate = capture.get("rate_percent")
    capture_value = "—" if capture_rate is None else f"{capture_rate:g}%"
    capture_severity = (
        "neutral"
        if capture_rate is None
        else "success"
        if capture_rate == 100
        else "warning"
    )
    benchmark = state["benchmark_proof"]
    benchmark_value = (
        f"{_num(benchmark.get('effectiveness_index'))} index"
        if benchmark.get("has_run") and benchmark.get("effectiveness_index") is not None
        else "Run local max"
    )

    topbar = {
        "brand": "OPai",
        "project_name": root.name,
        "project_root": str(root),
        "status": overview.get("status_label", "ATTENTION"),
        "status_severity": "success" if overview.get("on") else "warning",
        "clients": protected_clients,
        "budget": "panic" if budget.get("panic") else "budget ok",
        "budget_severity": "danger" if budget.get("panic") else "success",
        "privacy": "Local only · no telemetry",
    }

    next_best = _redact(overview.get("next_best_action", "Run opai cockpit."))
    home_actions = [
        _action(
            "first_route",
            "Copy first route",
            icon="copy",
            command='opai route "<task>" --record',
            variant="primary",
        ),
        _action(
            "benchmark_gate" if benchmark.get("has_run") else "benchmark_run",
            "Check latest benchmark gate"
            if benchmark.get("has_run")
            else "Run local benchmark",
            icon="play",
            variant="secondary",
            mutates=not benchmark.get("has_run"),
            risk="config" if not benchmark.get("has_run") else "read",
            confirmation=(
                "Run the offline max benchmark and write privacy-safe evidence "
                "under .opaihub. No cloud model is contacted."
                if not benchmark.get("has_run")
                else ""
            ),
        ),
    ]
    home = {
        "id": "home",
        "label": "Home",
        "icon": "wallet",
        "title": "Money Saved",
        "subtitle": "The fast answer: OPai is on, private, and ready to save AI spend.",
        "hero": {
            "headline": _money(savings_value)
            if has_savings
            else "Ready to record first savings",
            "caption": "estimated saved on this project"
            if has_savings
            else "Run one routed task to turn OPai savings from potential into proof.",
            "severity": "success" if overview.get("on") else "warning",
        },
        "kpis": [
            {
                "label": "OPai",
                "value": overview.get("status_label", "ATTENTION"),
                "severity": topbar["status_severity"],
            },
            {
                "label": "Protected clients",
                "value": protected_clients,
                "severity": "success"
                if clients_summary.get("active") == clients_summary.get("total")
                else "warning",
            },
            {
                "label": "Paid calls avoided",
                "value": _num(savings.get("cloud_calls_avoided")),
                "severity": "success",
            },
            {
                "label": "Context reduced",
                "value": _num(savings.get("context_tokens_saved")),
                "severity": "success",
            },
            {
                "label": "Benchmark proof",
                "value": benchmark_value,
                "severity": "success",
            },
            {
                "label": "Capture health",
                "value": capture_value,
                "severity": capture_severity,
            },
        ],
        "cards": [
            {
                "title": "Next best action",
                "body": next_best,
                "command": next_best,
                "severity": "accent",
            },
            {
                "title": "Zero-state guidance" if not has_savings else "Savings ledger",
                "body": _redact(
                    savings.get("zero_state")
                    or "Savings ledger has real routed task data."
                ),
                "severity": "warning" if not has_savings else "success",
            },
            {
                "title": "Benchmark proof",
                "body": _redact(overview.get("benchmark_claim", "")),
                "footnote": _redact(overview.get("benchmark_caveat", "")),
                "severity": "neutral",
            },
            {
                "title": "Capture integrity",
                "body": _redact(capture.get("label", "No proxy sessions observed")),
                "footnote": _redact(capture.get("caveat", "")),
                "severity": capture_severity,
            },
        ],
        "actions": home_actions,
    }

    agent_cards = [
        _client_card(client) for client in state["agent_readiness"]["clients"]
    ]
    agents = {
        "id": "agents",
        "label": "Agents",
        "icon": "users",
        "title": "Agent Readiness",
        "subtitle": "Claude, Codex, Copilot, Gemini, Cursor, and Cline should all start from OPai policy.",
        "cards": agent_cards,
        "actions": [
            _action(
                "safe_repair",
                "Run safe repair",
                icon="play",
                command=state["agent_readiness"].get("repair_command"),
                mutates=True,
                risk="config",
                confirmation="Re-apply OPai client integration files for this project. This is additive and does not delete source code.",
                variant="primary",
            )
        ],
    }

    firewall = state["cost_firewall"]
    firewall_section = {
        "id": "firewall",
        "label": "Cost Firewall",
        "icon": "shield",
        "title": "Cost Firewall",
        "subtitle": "Use more AI with budget caps, local-first routing, and paid/cloud gates.",
        "kpis": [
            {
                "label": "Policy",
                "value": firewall.get("profile", "solo-balanced"),
                "severity": "accent",
            },
            {
                "label": "Panic mode",
                "value": "ON" if firewall.get("panic") else "off",
                "severity": "danger" if firewall.get("panic") else "success",
            },
            {
                "label": "Spent today",
                "value": _money(firewall.get("spent", {}).get("today_usd")),
                "severity": "neutral",
            },
            {
                "label": "Cloud gate",
                "value": "confirm"
                if firewall.get("require_confirmation_for_cloud")
                else "open",
                "severity": "success"
                if firewall.get("require_confirmation_for_cloud")
                else "warning",
            },
        ],
        "cards": [
            {
                "title": "Local-first route",
                "body": _redact(firewall.get("local_first", "")),
                "severity": "success",
            },
            {
                "title": "Recently blocked",
                "items": [
                    _redact(f"{item.get('event_type')}: {item.get('action')}")
                    for item in firewall.get("recent_blocked", [])[:6]
                ]
                or ["No blocked paid/cloud actions recorded."],
                "severity": "warning",
            },
        ],
        "actions": [
            _action(
                "panic_toggle",
                "Disable panic" if firewall.get("panic") else "Enable panic",
                icon="alert",
                mutates=True,
                risk="config",
                confirmation="Change panic mode for this project. Panic mode blocks paid/cloud routes until disabled.",
                variant="danger" if not firewall.get("panic") else "secondary",
            )
        ],
    }

    context = state["context_waste"]
    max_source = max(
        [source.get("bytes", 0) for source in context.get("top_sources", [])] or [1]
    )
    context_cards = []
    for source in context.get("top_sources", [])[:8]:
        context_cards.append(
            {
                "title": _redact(source.get("path")),
                "subtitle": _redact(source.get("category")),
                "value": f"{int((source.get('bytes', 0) / max_source) * 100)}%",
                "bytes": source.get("bytes", 0),
                "severity": "warning",
            }
        )
    context_section = {
        "id": "context",
        "label": "Context Waste",
        "icon": "database",
        "title": "Context Waste",
        "subtitle": "Find the files and caches making models expensive.",
        "kpis": [
            {
                "label": "Waste share",
                "value": f"{context.get('waste_share', 0) * 100:.1f}%",
                "severity": "warning",
            },
            {
                "label": "Estimated wasted tokens",
                "value": _num(context.get("estimated_tokens_wasted")),
                "severity": "warning",
            },
            {
                "label": "Estimated cost if sent",
                "value": _money(context.get("estimated_cost_wasted_usd")),
                "description": "Not money spent — projection for uncompressed context.",
                "severity": "neutral",
            },
        ],
        "cards": context_cards
        or [
            {
                "title": "No context waste found",
                "body": "Generated/cache folders are already quiet.",
                "severity": "success",
            }
        ],
        "actions": [
            _action("cleanup_preview", "Preview cleanup", icon="activity"),
            _action(
                "generate_ignores",
                "Generate ignore files",
                icon="play",
                mutates=True,
                risk="config",
                confirmation="Append OPai managed ignore blocks. This never deletes source code.",
            ),
        ],
    }

    benchmark_has_run = bool(benchmark.get("has_run"))
    benchmark_section = {
        "id": "benchmark",
        "label": "Benchmark",
        "icon": "gauge",
        "title": "Benchmark Proof",
        "subtitle": "Local proof that OPai is cheaper, smaller, and safer than normal AI usage.",
        "hero": {
            "headline": str(int(float(benchmark.get("effectiveness_index") or 0)))
            if benchmark_has_run
            else "Not run yet",
            "caption": "OPai effectiveness index"
            if benchmark_has_run
            else benchmark.get("next_command", ""),
            "severity": "success" if benchmark_has_run else "warning",
        },
        "kpis": [
            {
                "label": "Context reduction",
                "value": (
                    f"{float(benchmark.get('context_reduction_ratio') or 0):.0f}x"
                    if benchmark_has_run
                    else "—"
                ),
                "severity": "success" if benchmark_has_run else "neutral",
            },
            {
                "label": "Paid calls avoided",
                "value": (
                    _num(benchmark.get("paid_calls_avoided"))
                    if benchmark_has_run
                    else "—"
                ),
                "severity": "success" if benchmark_has_run else "neutral",
            },
            {
                "label": "Risk blocks",
                "value": (
                    _num(benchmark.get("risk_blocks")) if benchmark_has_run else "—"
                ),
                "severity": "warning" if benchmark_has_run else "neutral",
            },
        ],
        "cards": [
            {
                "title": "Approved claim",
                "body": _redact(benchmark.get("claim", A.BENCHMARK_CLAIM)),
                "footnote": _redact(benchmark.get("caveat", A.BENCHMARK_CAVEAT)),
                "severity": "success",
            }
        ],
        "actions": [
            _action(
                "benchmark_run",
                "Run local benchmark",
                icon="play",
                variant="primary",
                mutates=True,
                risk="config",
                confirmation=(
                    "Run the offline max benchmark and write privacy-safe evidence "
                    "under .opaihub. No cloud model is contacted."
                ),
            ),
            _action(
                "benchmark_gate",
                "Check latest gate",
                icon="activity",
                variant="secondary",
            ),
        ],
    }

    proof = state["proof_status"]
    proof_section = {
        "id": "proof",
        "label": "Proof Bundle",
        "icon": "package-check",
        "title": "Proof Bundle",
        "subtitle": "Local signed evidence for alpha users and teams.",
        "kpis": [
            {
                "label": "Available",
                "value": "yes" if proof.get("available") else "no",
                "severity": "success",
            },
            {
                "label": "Signed",
                "value": "yes" if proof.get("signed_by_default") else "no",
                "severity": "success",
            },
            {"label": "Redaction", "value": "hashes only", "severity": "success"},
        ],
        "cards": [
            {
                "title": "Privacy guarantee",
                "body": _redact(proof.get("redaction", "")),
                "footnote": _redact(proof.get("note", "")),
                "severity": "success",
            }
        ],
        "actions": [
            _action(
                "export_proof_json",
                "Export JSON",
                icon="package-check",
                mutates=True,
                risk="config",
                confirmation="Write a redacted, signed proof bundle to a local file.",
            ),
            _action(
                "export_proof_markdown",
                "Export Markdown",
                icon="package-check",
                mutates=True,
                risk="config",
                confirmation="Write a redacted proof report to a local Markdown file.",
            ),
        ],
    }

    workflow_cards = []
    for tile in state["guarded_workflows"].get("tiles", [])[:8]:
        workflow_cards.append(
            {
                "title": _redact(tile.get("title")),
                "subtitle": _redact(tile.get("risk")),
                "body": _redact(tile.get("purpose")),
                "command": _redact(tile.get("start_command")),
                "severity": "warning"
                if tile.get("risk") == "fail-closed"
                else "neutral",
            }
        )
    workflows = {
        "id": "workflows",
        "label": "Workflows",
        "icon": "activity",
        "title": "Guarded Workflows",
        "subtitle": "Repeatable, approval-aware workflows for real development risk.",
        "cards": workflow_cards,
        "actions": [
            _action(
                "copy_workflow_command",
                "Copy workflow list command",
                icon="copy",
                command="opai guard list",
            ),
        ],
    }

    launch = state["launch_readiness"]
    launch_cards = []
    for check in launch.get("checks", []):
        ok = check.get("ok")
        launch_cards.append(
            {
                "title": _redact(check.get("name")),
                "body": _redact(check.get("detail")),
                "command": _redact(check.get("fix", "")),
                "status": "OK" if ok else ("BLOCK" if ok is False else "CHECK"),
                "severity": "success"
                if ok
                else ("danger" if ok is False else "neutral"),
            }
        )
    launch_section = {
        "id": "launch",
        "label": "Launch",
        "icon": "rocket",
        "title": "Launch Readiness",
        "subtitle": "Controlled-alpha blockers before OPai goes public.",
        "hero": {
            "headline": "READY"
            if launch.get("ready")
            else f"{launch.get('blocker_count', 0)} blockers",
            "caption": "launch readiness",
            "severity": "success" if launch.get("ready") else "warning",
        },
        "cards": launch_cards,
        "actions": [
            _action(
                "copy_launch_claim",
                "Copy launch claim",
                icon="copy",
                command=launch.get("launch_claim"),
            ),
        ],
    }

    sections = [
        home,
        agents,
        firewall_section,
        context_section,
        benchmark_section,
        proof_section,
        workflows,
        launch_section,
    ]

    return {
        "theme": THEME,
        "topbar": topbar,
        "generated_from": str(root),
        "privacy": "No telemetry. No raw prompts. No secrets.",
        "sections": sections,
        "state": {
            "overview": {
                "on": overview.get("on"),
                "version": overview.get("version"),
                "release_stage": overview.get("release_stage"),
            }
        },
    }
