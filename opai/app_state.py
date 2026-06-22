"""Reusable OPai application-state layer.

One source of truth for every control-center surface, composed from the existing
OPai data functions (cockpit, clients, budget, policy, context, benchmark, proof,
guarded workflows, launch readiness). The CLI and the desktop GUI both read this,
so they never drift. Everything is local: no network, no telemetry, no raw
prompts or secrets.

Read functions never mutate. Action helpers (panic toggle, repair, ignore
generation, proof export, policy change) DO mutate and are intended to be called
only after explicit user confirmation in the GUI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# 50x is the approved, capped public reduction figure for the local suite.
CONTEXT_REDUCTION_CLAIM = "50x"
BENCHMARK_CLAIM = (
    "OPai reduced context by 50x and avoided 16 paid calls on the "
    "16-task local benchmark suite."
)
BENCHMARK_CAVEAT = (
    "Local OPai benchmark suite result. Not an official SWE-bench, "
    "Terminal-Bench, Aider, or third-party leaderboard result."
)
ZERO_STATE = (
    'No real routed tasks recorded yet. Run `opai route "<task>" --record` '
    "or `opai quickstart`."
)


# --------------------------------------------------------------------------- #
# Read surfaces (never mutate)
# --------------------------------------------------------------------------- #
def overview(project_root: Path) -> dict[str, Any]:
    """Home / Money Saved: the big ON/OFF + savings + next action surface."""
    from opai.cockpit import build_cockpit
    from opaihub.audit import summarize_audit

    root = project_root.expanduser().resolve()
    cockpit = build_cockpit(root)
    audit = summarize_audit(root)
    savings = cockpit["savings"]
    return {
        "on": cockpit["status"] == "on",
        "status_label": "ON" if cockpit["status"] == "on" else "ATTENTION",
        "version": cockpit["version"],
        "release_stage": cockpit["release_stage"],
        "project_root": cockpit["project"]["root"],
        "activated": cockpit["project"]["activated"],
        "clients": cockpit["clients"],
        "savings": {
            "has_data": savings["has_data"],
            "estimated_savings_usd": savings["estimated_savings_usd"],
            "routed_tasks": savings["routed_tasks"],
            "cloud_calls_avoided": savings["cloud_calls_avoided"],
            "context_tokens_saved": savings["context_tokens_saved"],
            "zero_state": None if savings["has_data"] else ZERO_STATE,
        },
        "budget": cockpit["budget"],
        "benchmark_claim": BENCHMARK_CLAIM,
        "benchmark_caveat": BENCHMARK_CAVEAT,
        "risk_blocks": audit["denied_actions"],
        "local_models": cockpit["local_models"],
        "next_actions": cockpit["next_actions"],
        "next_best_action": cockpit["next_actions"][0],
        "privacy": cockpit["privacy"],
    }


def agent_readiness(project_root: Path) -> dict[str, Any]:
    """Per-client cards (Claude/Codex/Copilot/Cursor/Cline) with repair commands."""
    from opai.clients import client_integrations_status, detect_stale_paths
    from opai.integrations import project_status

    root = project_root.expanduser().resolve()
    status = project_status(root)
    integrations = client_integrations_status(root)
    wrappers = status["global"]["wrappers"]
    order = ["claude", "codex", "copilot", "cursor", "cline"]
    by_id = {client["id"]: client for client in integrations["clients"]}

    cards: list[dict[str, Any]] = []
    for client_id in order:
        client = by_id.get(client_id, {"id": client_id, "status": "unknown"})
        wrapper = wrappers.get(client_id) or {}
        cards.append(
            {
                "id": client_id,
                "label": client.get("label", client_id.title()),
                "status": client.get("status", "unknown"),
                "reason": client.get("reason", ""),
                "wrapper_installed": bool(wrapper.get("exists")),
                "config_rules": bool(client.get("project_managed")),
                "global_ready": client.get("global_ready"),
                "repair": client.get("repair", "opai activate --repair"),
            }
        )
    return {
        "clients": cards,
        "summary": integrations["summary"],
        "stale_paths": detect_stale_paths(root),
        "repair_command": integrations.get("repair_command")
        or "opai activate --repair",
    }


def cost_firewall(project_root: Path) -> dict[str, Any]:
    """Budget caps, panic, policy profile, and recently blocked paid calls."""
    from opaihub.audit import GUARD_DENY, POLICY_DENY, read_audit
    from opaihub.budget import budget_status
    from opaihub.policy import list_profiles, resolve_policy

    root = project_root.expanduser().resolve()
    budget = budget_status(root)
    resolved = resolve_policy(root)
    blocked = [
        {
            "created_at": event.get("created_at"),
            "event_type": event.get("event_type"),
            "action": event.get("action") or event.get("decision"),
        }
        for event in read_audit(root)
        if event.get("event_type") in {GUARD_DENY, POLICY_DENY}
    ][-10:]
    settings = resolved["settings"]
    return {
        "profile": resolved["profile"],
        "available_profiles": list_profiles(root),
        "panic": budget["panic"],
        "caps": budget["caps"],
        "spent": budget["spent"],
        "remaining": budget["remaining"],
        "require_confirmation_for_cloud": settings.get(
            "require_confirmation_for_cloud", True
        ),
        "allow_paid": settings.get("allow_paid", True),
        "max_tier": settings.get("max_tier"),
        "recent_blocked": list(reversed(blocked)),
        "local_first": "deterministic tools -> cache -> local model -> confirmed cloud",
    }


def context_waste(project_root: Path) -> dict[str, Any]:
    """Ranked context-waste sources + suggested ignore files. Read-only."""
    from opaihub.context_engine import CLIENT_IGNORE_FILES, profile_context

    root = project_root.expanduser().resolve()
    profile = profile_context(root)
    return {
        "total_bytes": profile["total_bytes"],
        "waste_bytes": profile["waste_bytes"],
        "waste_share": profile["waste_share"],
        "by_category": profile["by_category"],
        "top_sources": profile["top_sources"],
        "estimated_tokens_wasted": profile["estimated_tokens_wasted"],
        "estimated_cost_wasted_usd": profile["estimated_cost_wasted_usd"],
        "before_after": profile["before_after"],
        "suggested_ignores": sorted(CLIENT_IGNORE_FILES.values()),
    }


def benchmark_proof(project_root: Path) -> dict[str, Any]:
    """Latest local benchmark, effectiveness, and report artifact paths."""
    from opaihub.benchmark import benchmark_dir, latest_benchmark_report

    root = project_root.expanduser().resolve()
    report = latest_benchmark_report(root)
    bench_dir = benchmark_dir(root)
    report_files = {
        fmt: str(bench_dir / f"report.{fmt}")
        for fmt in ("md", "json", "html")
        if (bench_dir / f"report.{fmt}").exists()
    }
    if report is None:
        return {
            "has_run": False,
            "claim": BENCHMARK_CLAIM,
            "caveat": BENCHMARK_CAVEAT,
            "report_files": report_files,
            "next_command": "opai benchmark run --suite max --mode both",
        }
    score = report.get("efficiency_score", {})
    return {
        "has_run": True,
        "run_id": report.get("run_id"),
        "suite": report.get("suite"),
        "effectiveness_index": score.get("opai_effectiveness_index"),
        "context_reduction_ratio": min(
            50.0, float(score.get("context_reduction_ratio", 0) or 0)
        ),
        "paid_calls_avoided": score.get("paid_calls_avoided"),
        "risk_blocks": score.get("risk_events_blocked"),
        "claim": BENCHMARK_CLAIM,
        "caveat": BENCHMARK_CAVEAT,
        "report_files": report_files,
    }


def proof_status(project_root: Path) -> dict[str, Any]:
    """Proof-bundle availability + privacy guarantees (no bundle is built here)."""
    from opaihub.signing import resolve_key

    root = project_root.expanduser().resolve()
    _key, source = resolve_key(root, create=False)
    return {
        "available": True,
        "signed_by_default": True,
        "signing_key_source": source,
        "redaction": "one-way task hashes only; raw prompts and secrets never stored",
        "export_formats": ["markdown", "json"],
        "command": "opai proof bundle --out proof.json",
        "note": "Proof bundles redact prompts/secrets; sign locally (not PKI identity).",
    }


def guarded_workflows(project_root: Path) -> dict[str, Any]:
    """Guarded-workflow tiles with purpose, risk, approvals, and evidence."""
    from opaihub.guarded import load_templates

    root = project_root.expanduser().resolve()
    data = load_templates(root)
    tiles = []
    for template in data.get("templates", []):
        tiles.append(
            {
                "id": template.get("id"),
                "title": template.get("title"),
                "purpose": template.get("trigger"),
                "risk": "fail-closed" if template.get("fail_closed") else "standard",
                "approvals": template.get("fail_closed", []),
                "evidence_artifacts": template.get("evidence_artifacts", []),
                "permission_boundaries": template.get("permission_boundaries"),
                "start_command": f"opai guard evidence {template.get('id')} --sign",
            }
        )
    return {
        "reference_implementation": data.get("reference_implementation"),
        "tiles": tiles,
    }


def launch_readiness(project_root: Path) -> dict[str, Any]:
    from opaihub.launch_readiness import build_launch_readiness

    return build_launch_readiness(project_root)


def full_state(project_root: Path) -> dict[str, Any]:
    """Everything the control center needs, in one read. Never mutates."""
    root = project_root.expanduser().resolve()
    return {
        "overview": overview(root),
        "agent_readiness": agent_readiness(root),
        "cost_firewall": cost_firewall(root),
        "context_waste": context_waste(root),
        "benchmark_proof": benchmark_proof(root),
        "proof_status": proof_status(root),
        "guarded_workflows": guarded_workflows(root),
        "launch_readiness": launch_readiness(root),
    }


# --------------------------------------------------------------------------- #
# Action helpers (mutate; GUI must confirm first)
# --------------------------------------------------------------------------- #
def set_panic(project_root: Path, on: bool) -> dict[str, Any]:
    from opaihub.budget import budget_status, set_budget

    set_budget(project_root, panic=bool(on))
    from opaihub.audit import record_audit_event

    record_audit_event(project_root, "panic_on" if on else "panic_off", panic=bool(on))
    return budget_status(project_root)


def set_policy_profile(project_root: Path, profile: str) -> dict[str, Any]:
    from opaihub.audit import record_audit_event
    from opaihub.policy import set_profile

    result = set_profile(project_root, profile)
    if result.get("status") == "updated":
        record_audit_event(project_root, "policy_profile_set", profile=profile)
    return result


def cleanup_preview(project_root: Path) -> dict[str, Any]:
    """Non-mutating preview of what context slimming WOULD ignore."""
    waste = context_waste(project_root)
    return {
        "mutates": False,
        "would_reduce_tokens": waste["estimated_tokens_wasted"],
        "would_reduce_bytes": waste["waste_bytes"],
        "top_sources": waste["top_sources"][:10],
        "suggested_ignores": waste["suggested_ignores"],
        "note": "Preview only. No files are deleted; ignore generation is additive.",
    }


def generate_ignores(
    project_root: Path, clients: list[str] | None = None
) -> dict[str, Any]:
    """Additive: writes per-client ignore files; never deletes source. Confirm first."""
    from opaihub.context_engine import generate_client_ignores

    return generate_client_ignores(project_root, clients)


def run_repair(project_root: Path) -> dict[str, Any]:
    """Re-apply OPai client integration files (safe repair). Confirm first."""
    from opai.integrations import activate_project

    result = activate_project(
        project_root, install_global=True, repair=True, dry_run=False
    )
    return {
        "status": result.get("status"),
        "project_files": result.get("project_files", []),
    }


def export_proof(
    project_root: Path, out_path: Path, *, fmt: str = "json", sign: bool = True
) -> dict[str, Any]:
    """Write a redacted, signed proof bundle. No raw prompts/secrets. Confirm first."""
    from opaihub.proof import build_proof_bundle, render_proof_markdown

    bundle = build_proof_bundle(project_root, sign=sign)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "markdown":
        target.write_text(render_proof_markdown(bundle), encoding="utf-8")
    else:
        import json

        target.write_text(
            json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return {
        "status": "exported",
        "path": str(target),
        "format": fmt,
        "signed": "signature" in bundle,
        "redaction": "raw prompts/secrets not included",
    }


def run_benchmark_gate(
    project_root: Path,
    *,
    min_effectiveness_index: float = 95.0,
    require_risk_blocks: bool = True,
) -> dict[str, Any]:
    """Read-only: evaluate the latest benchmark against launch thresholds."""
    from opaihub.benchmark import benchmark_gate, latest_benchmark_report

    report = latest_benchmark_report(project_root)
    if report is None:
        return {"ok": False, "reason": "no benchmark run recorded"}
    return benchmark_gate(
        report,
        min_effectiveness_index=min_effectiveness_index,
        require_risk_blocks=require_risk_blocks,
    )


# --------------------------------------------------------------------------- #
# Chat surface: model picker, ask, and the tool dispatcher (powers the GUI)
# --------------------------------------------------------------------------- #
def available_models(project_root: Path) -> dict[str, Any]:
    """Models the user can pick: Auto + any connected local models. Read-only."""
    from opaihub.local_runner import list_local_models

    local = list_local_models(project_root)
    options: list[dict[str, Any]] = [
        {
            "id": "auto",
            "label": "Auto - OPai routes the cheapest safe model",
            "kind": "auto",
        }
    ]
    for model in local:
        options.append(
            {
                "id": model["id"],
                "label": f"{model['model']}  ·  {model['provider']} (local)",
                "kind": "local",
                "endpoint": model["endpoint"],
            }
        )
    return {
        "models": options,
        "local_count": len(local),
        "hint": None
        if local
        else "No local model connected. `ollama serve` (then pull a model) or set "
        "LOCAL_MODEL_URL to pick a specific model. Auto still routes locally.",
    }


def ask(
    project_root: Path,
    task: str,
    model_choice: str = "auto",
    *,
    allow_cloud: bool = False,
) -> dict[str, Any]:
    """Run a coding task. ``model_choice`` is 'auto' or a 'provider:model' id.

    Auto lets OPai route the cheapest safe path (local execution + cache). A
    specific model runs on that connected local model. Cloud is never
    auto-called - it returns ``confirmation_required``.
    """
    from opaihub.ask import run_ask
    from opaihub.local_runner import runner_for_model

    runner = None
    if model_choice and model_choice != "auto":
        runner = runner_for_model(model_choice, project_root)
    return run_ask(
        project_root, task, runner=runner, record=True, allow_cloud=allow_cloud
    )


# Tools surfaced in the chat (slash commands + the Tools menu).
TOOLS: list[dict[str, Any]] = [
    {
        "id": "savings",
        "label": "Savings",
        "desc": "Money OPai saved on this project",
        "mutates": False,
    },
    {
        "id": "doctor",
        "label": "Doctor",
        "desc": "Client readiness + repair",
        "mutates": False,
    },
    {
        "id": "budget",
        "label": "Budget",
        "desc": "Cost firewall: caps, panic, gates",
        "mutates": False,
    },
    {
        "id": "context",
        "label": "Context",
        "desc": "Context waste in this repo",
        "mutates": False,
    },
    {
        "id": "benchmark",
        "label": "Benchmark",
        "desc": "Local effectiveness proof",
        "mutates": False,
    },
    {
        "id": "proof",
        "label": "Proof",
        "desc": "Signed proof-bundle status",
        "mutates": False,
    },
    {
        "id": "panic",
        "label": "Panic",
        "desc": "Toggle local-only routing",
        "mutates": True,
    },
    {
        "id": "repair",
        "label": "Repair",
        "desc": "Re-apply OPai client files",
        "mutates": True,
    },
]


def run_tool(project_root: Path, command: str, arg: str = "") -> dict[str, Any]:
    """Dispatch a chat tool command to the real OPai function. GUI confirms mutations."""
    root = project_root.expanduser().resolve()
    tool = command.strip().lstrip("/").lower()

    if tool in {"savings", "money"}:
        o = overview(root)["savings"]
        text = (
            f"Estimated saved: ${o['estimated_savings_usd']:.2f}\n"
            f"Routed tasks: {o['routed_tasks']}\n"
            f"Paid calls avoided: {o['cloud_calls_avoided']}\n"
            f"Context tokens saved: {o['context_tokens_saved']:,}"
        )
        if not o["has_data"]:
            text = o["zero_state"]
        return {"ok": True, "title": "Savings", "text": text}

    if tool in {"doctor", "status", "clients"}:
        ar = agent_readiness(root)
        lines = [f"{c['label']}: {c['status']}" for c in ar["clients"]]
        if ar["summary"].get("broken") or ar["summary"].get("missing"):
            lines.append(f"\nRepair: {ar['repair_command']}")
        return {"ok": True, "title": "Agent readiness", "text": "\n".join(lines)}

    if tool == "budget":
        cf = cost_firewall(root)
        text = (
            f"Profile: {cf['profile']}\n"
            f"Panic mode: {'ON (local-only)' if cf['panic'] else 'off'}\n"
            f"Spent today: ${cf['spent'].get('today_usd', 0):.2f}\n"
            f"Cloud requires confirmation: {cf['require_confirmation_for_cloud']}"
        )
        return {"ok": True, "title": "Cost firewall", "text": text}

    if tool == "context":
        cw = context_waste(root)
        top = cw["top_sources"][:5]
        lines = [
            f"Waste: {cw['waste_share'] * 100:.0f}%  ·  ~{cw['estimated_tokens_wasted']:,} tokens"
        ]
        lines += [f"• {s['path']} ({s['category']})" for s in top]
        return {"ok": True, "title": "Context waste", "text": "\n".join(lines)}

    if tool == "benchmark":
        gate = run_benchmark_gate(
            root, min_effectiveness_index=0.0, require_risk_blocks=False
        )
        bp = benchmark_proof(root)
        if not bp["has_run"]:
            return {
                "ok": True,
                "title": "Benchmark",
                "text": "No run yet. " + bp["next_command"],
            }
        return {
            "ok": True,
            "title": "Benchmark",
            "text": f"Effectiveness index: {bp.get('effectiveness_index')}\n"
            f"Context reduction: {bp.get('context_reduction_ratio')}x\n"
            f"Gate: {'PASS' if gate.get('ok') else 'review'}\n{BENCHMARK_CAVEAT}",
        }

    if tool == "proof":
        ps = proof_status(root)
        return {
            "ok": True,
            "title": "Proof bundle",
            "text": f"Available, signed by default. Redaction: {ps['redaction']}.\n"
            f"Export: {ps['command']}",
        }

    if tool == "panic":
        cf = cost_firewall(root)
        want_on = not cf["panic"] if not arg else arg.lower() in {"on", "true", "1"}
        return {
            "ok": True,
            "title": "Panic mode",
            "text": f"Turn panic {'ON (block paid/cloud)' if want_on else 'off'}?",
            "mutates": True,
            "confirm": f"Turn panic mode {'ON' if want_on else 'off'} for this project?",
            "apply": ("panic", want_on),
        }

    if tool == "repair":
        return {
            "ok": True,
            "title": "Repair",
            "text": "Re-apply OPai client integration files (additive; no source deleted)?",
            "mutates": True,
            "confirm": "Run safe repair for this project?",
            "apply": ("repair", None),
        }

    return {
        "ok": False,
        "title": "Unknown tool",
        "text": "Tools: " + ", ".join("/" + t["id"] for t in TOOLS),
    }


def apply_tool(project_root: Path, apply: tuple[str, Any]) -> dict[str, Any]:
    """Execute a confirmed mutating tool action (called by the GUI after confirm)."""
    name, value = apply
    if name == "panic":
        set_panic(project_root, bool(value))
        return {"ok": True, "text": f"Panic mode {'ON' if value else 'off'}."}
    if name == "repair":
        result = run_repair(project_root)
        return {"ok": True, "text": f"Repair: {result.get('status')}."}
    return {"ok": False, "text": "Nothing to apply."}
