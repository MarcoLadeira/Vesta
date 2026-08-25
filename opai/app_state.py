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

import contextlib
import hashlib
import os
import subprocess  # nosec B404 - process calls below use fixed argv/no shell
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from opaihub.provider_invocation import (
    ProviderInvocationCompatibilityError,
    ProviderInvocationPlan,
)

# Benchmark proof is local and must be reproduced before publishing a result.
CONTEXT_REDUCTION_CLAIM = "local max"
BENCHMARK_CLAIM = (
    "Local max benchmark proof is available after you run the reproducible "
    "benchmark suite."
)
BENCHMARK_CAVEAT = (
    "Local OPai benchmark suite result. Not an official SWE-bench, "
    "Terminal-Bench, Aider, or third-party leaderboard result."
)
ZERO_STATE = (
    'No real routed tasks recorded yet. Run `opai route "<task>" --record` '
    "or `opai quickstart`."
)


def model_setup(project_root: Path) -> dict[str, Any]:
    """Free local model setup guidance. Read-only; never installs or downloads."""
    root = project_root.expanduser().resolve()
    is_windows = os.name == "nt"
    return {
        "project": str(root),
        "status": "free_first",
        "summary": (
            "Connect a free local model so OPai can answer cheap tasks without "
            "spending cloud credits."
        ),
        "install": {
            "provider": "ollama",
            "label": "Install Ollama",
            "command": "irm https://ollama.com/install.ps1 | iex"
            if is_windows
            else "curl -fsSL https://ollama.com/install.sh | sh",
            "notes": "Run only if you want to install Ollama. OPai never downloads models automatically.",
        },
        "recommended": [
            {
                "id": "ollama-qwen2.5-coder",
                "label": "Best default coding model",
                "provider": "ollama",
                "model": "qwen2.5-coder:7b",
                "command": "ollama pull qwen2.5-coder:7b",
                "why": "Strong free coding model for normal laptops with enough RAM.",
            },
            {
                "id": "ollama-qwen2.5-coder-small",
                "label": "Small-machine fallback",
                "provider": "ollama",
                "model": "qwen2.5-coder:1.5b",
                "command": "ollama pull qwen2.5-coder:1.5b",
                "why": "Fast, tiny local model for first-run smoke tests and older machines.",
            },
            {
                "id": "ollama-deepseek-coder",
                "label": "Alternative coding model",
                "provider": "ollama",
                "model": "deepseek-coder:6.7b",
                "command": "ollama pull deepseek-coder:6.7b",
                "why": "Another free coding-focused option if Qwen is not a good fit.",
            },
            {
                "id": "lm-studio-local-server",
                "label": "GUI model manager option",
                "provider": "lm-studio",
                "model": "OpenAI-compatible local server",
                "command": "setx LOCAL_MODEL_URL http://127.0.0.1:1234/v1",
                "why": "Use LM Studio if you prefer a desktop model browser and local server.",
            },
        ],
        "start_commands": [
            "ollama serve",
            "ollama run qwen2.5-coder:7b",
        ],
        "verify_command": "opai models discover-local",
        "ask_command": 'opai ask "summarize my changes"',
        "privacy": "Loopback/private endpoints only count as local. Public endpoints require cloud confirmation.",
    }


def local_model_onboarding(project_root: Path) -> dict[str, Any]:
    """Guided local-model readiness for the GUI and CLI (#3), one shared source.

    Every surface (``opai models onboard``, ``opai doctor``, the desktop GUI)
    derives readiness from ``opaihub.local_onboarding`` so they agree. Never
    downloads a model, starts a service, or reaches a public host on its own.
    """
    from opaihub.local_onboarding import local_onboarding_status

    return local_onboarding_status(project_root.expanduser().resolve())


# Plain-English of what each run mode is actually allowed to do (no sci-fi labels).
MODE_CAPABILITY = {
    "ask": "Answers only. No files changed, no commands run.",
    "plan": "Lays out the steps. Nothing is changed yet.",
    "safe-auto": "Edits files after safe checks; asks before risky commands.",
    "approve-edits": "Proposes edits for your approval before writing to disk.",
    "full-auto": "Edits files and runs commands without asking. Review the diff.",
}


def _git_text(root: Path, args: list[str], *, timeout: float = 12.0) -> str:
    """Run a read-only git command hidden, returning stdout (or '' on failure)."""
    with contextlib.suppress(Exception):
        from opaihub.accounts import _hidden_run

        proc = _hidden_run(["git", *args], cwd=str(root), timeout=timeout)
        return (proc.stdout or "").strip()
    return ""


_WORKSPACE_SUMMARY_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
_WORKSPACE_SUMMARY_INFLIGHT: dict[tuple[str, tuple[Any, ...]], threading.Event] = {}
_WORKSPACE_SUMMARY_CACHE_LOCK = threading.RLock()


def _path_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), -1, -1)
    return (str(path), int(stat.st_size), int(stat.st_mtime_ns))


def _workspace_git_dir(root: Path) -> tuple[tuple[str, int, int], Path] | None:
    marker = root / ".git"
    marker_signature = _path_signature(marker)
    if marker.is_dir():
        git_dir = marker
    elif marker.is_file():
        try:
            line = marker.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        prefix, separator, raw_path = line.partition(":")
        if not separator or prefix.strip().casefold() != "gitdir":
            return None
        git_dir = Path(raw_path.strip()).expanduser()
        if not git_dir.is_absolute():
            git_dir = marker.parent / git_dir
        try:
            git_dir = git_dir.resolve()
        except OSError:
            return None
    else:
        return None
    return marker_signature, git_dir


def _workspace_git_signature(root: Path) -> tuple[Any, ...] | None:
    """Metadata that exactly owns tracked-file count and branch name.

    ``git ls-files`` is determined by the worktree index and the displayed
    branch by its HEAD file. Reading their stat metadata is substantially
    cheaper than spawning two Git processes on every GUI status refresh. Both
    ordinary repositories and linked worktrees (whose ``.git`` is a pointer
    file) are supported.
    """

    metadata = _workspace_git_dir(root)
    if metadata is None:
        return None
    marker_signature, git_dir = metadata
    return (
        marker_signature,
        _path_signature(git_dir / "index"),
        _path_signature(git_dir / "HEAD"),
    )


def _workspace_branch(root: Path) -> str:
    metadata = _workspace_git_dir(root)
    if metadata is not None:
        _marker_signature, git_dir = metadata
        try:
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        else:
            prefix = "ref: refs/heads/"
            if head.startswith(prefix):
                return head[len(prefix) :]
            if not head.startswith("ref:"):
                return ""
    branch = _git_text(root, ["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    return branch if branch != "HEAD" else ""


def clear_workspace_summary_cache() -> None:
    with _WORKSPACE_SUMMARY_CACHE_LOCK:
        _WORKSPACE_SUMMARY_CACHE.clear()


def workspace_summary(project_root: Path) -> dict[str, Any]:
    """Tracked-file count + branch - the 'indexed workspace' the agent can see."""
    root = project_root.expanduser().resolve()
    signature = _workspace_git_signature(root)
    key = str(root)
    flight_key: tuple[str, tuple[Any, ...]] | None = None
    while signature is not None:
        with _WORKSPACE_SUMMARY_CACHE_LOCK:
            cached = _WORKSPACE_SUMMARY_CACHE.get(key)
            if cached is not None and cached[0] == signature:
                return dict(cached[1])
            candidate = (key, signature)
            pending = _WORKSPACE_SUMMARY_INFLIGHT.get(candidate)
            if pending is None:
                pending = threading.Event()
                _WORKSPACE_SUMMARY_INFLIGHT[candidate] = pending
                flight_key = candidate
                break
        pending.wait()
        signature = _workspace_git_signature(root)

    try:
        files = _git_text(root, ["ls-files"])
        branch = _workspace_branch(root)
        summary = {
            "root": str(root),
            "name": root.name,
            "file_count": sum(1 for line in files.splitlines() if line.strip()),
            "branch": branch,
        }
        if signature is not None and _workspace_git_signature(root) == signature:
            with _WORKSPACE_SUMMARY_CACHE_LOCK:
                _WORKSPACE_SUMMARY_CACHE[key] = (signature, dict(summary))
        return summary
    finally:
        if flight_key is not None:
            with _WORKSPACE_SUMMARY_CACHE_LOCK:
                completed = _WORKSPACE_SUMMARY_INFLIGHT.pop(flight_key, None)
                if completed is not None:
                    completed.set()


def workspace_diff(project_root: Path, *, max_chars: int = 6000) -> str:
    """Working-tree diff vs HEAD so the GUI can show exactly what changed."""
    root = project_root.expanduser().resolve()
    stat = _git_text(root, ["diff", "--stat", "HEAD"])
    body = _git_text(root, ["diff", "HEAD"])
    diff = (f"{stat}\n\n{body}".strip()) if stat else body
    if len(diff) > max_chars:
        diff = diff[:max_chars] + "\n… (diff truncated — run `git diff` for the rest)"
    return diff


def inspector_state(project_root: Path, *, mode: str = "safe-auto") -> dict[str, Any]:
    """Concrete, non-cryptic telemetry for the Inspector (real numbers, not labels)."""
    root = project_root.expanduser().resolve()
    from opaihub.budget import budget_status

    budget = budget_status(root)
    daily = budget["caps"].get("daily_usd_limit")
    spent = float(budget["spent"].get("today_usd") or 0.0)
    if isinstance(daily, (int, float)) and daily > 0:
        pct = max(0, min(100, int(round(100 * spent / daily))))
        budget_text = f"${spent:.2f} / ${daily:.2f} today"
    else:
        pct, budget_text = 0, f"${spent:.2f} today · no cap set"
    ws = workspace_summary(root)
    ws_text = f"{ws['file_count']} files indexed" + (
        f" · {ws['branch']}" if ws["branch"] else ""
    )
    return {
        "budget": {
            "spent_today": spent,
            "daily_limit": daily,
            "pct": pct,
            "text": budget_text,
            "panic": bool(budget.get("panic")),
        },
        "workspace": {**ws, "text": ws_text},
        "mode": {"id": mode, "capability": MODE_CAPABILITY.get(mode, "")},
    }


# --------------------------------------------------------------------------- #
# Read surfaces (never mutate)
# --------------------------------------------------------------------------- #
def overview(project_root: Path) -> dict[str, Any]:
    """Home / Money Saved: the big ON/OFF + savings + next action surface."""
    from opai.cockpit import build_cockpit
    from opaihub.audit import summarize_audit
    from opaihub.ledger import summarize_ledger

    root = project_root.expanduser().resolve()
    cockpit = build_cockpit(root)
    audit = summarize_audit(root)
    ledger = summarize_ledger(root)
    savings = cockpit["savings"]
    return {
        "on": cockpit["status"] == "on",
        "status_label": "ON" if cockpit["status"] == "on" else "ATTENTION",
        "version": cockpit["version"],
        "release_stage": cockpit["release_stage"],
        "release_identity": cockpit["release_identity"],
        "compatibility": cockpit["compatibility"],
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
        "capture": ledger["capture"],
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
    """Per-client cards for every supported AI client, with repair commands."""
    from opai.clients import client_integrations_status, detect_stale_paths
    from opai.integrations import project_status

    root = project_root.expanduser().resolve()
    status = project_status(root)
    integrations = client_integrations_status(root)
    wrappers = status["global"]["wrappers"]
    order = ["claude", "codex", "copilot", "gemini", "cursor", "cline"]
    by_id = {client["id"]: client for client in integrations["clients"]}

    cards: list[dict[str, Any]] = []
    for client_id in order:
        client = by_id.get(client_id, {"id": client_id, "status": "unknown"})
        wrapper = wrappers.get(client_id) or {}
        global_required = "global_ready" in client
        global_ready = bool(client.get("global_ready")) if global_required else True
        ready = (
            bool(wrapper.get("exists"))
            and wrapper.get("capture_mode") == "selective_proxy"
            and global_ready
        )
        cards.append(
            {
                "id": client_id,
                "label": client.get("label", client_id.title()),
                "status": client.get("status", "unknown") if ready else "needs_setup",
                "reason": client.get("reason", ""),
                "wrapper_installed": bool(wrapper.get("exists")),
                "wrapper_capture_mode": wrapper.get("capture_mode", "missing"),
                "config_rules": bool(client.get("project_managed")),
                "global_required": global_required,
                "global_ready": global_ready,
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


def cost_firewall(
    project_root: Path,
    *,
    events: Any = None,
) -> dict[str, Any]:
    """Budget caps, panic, policy profile, and recently blocked paid calls."""
    from opaihub.audit import GUARD_DENY, POLICY_DENY, read_recent_audit
    from opaihub.budget import budget_status
    from opaihub.policy import list_profiles, resolve_policy

    root = project_root.expanduser().resolve()
    budget = budget_status(root, events=events)
    resolved = resolve_policy(root)
    blocked = [
        {
            "created_at": event.get("created_at"),
            "event_type": event.get("event_type"),
            "action": event.get("action") or event.get("decision"),
        }
        for event in read_recent_audit(
            root, event_types={GUARD_DENY, POLICY_DENY}, limit=10
        )
    ]
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


def run_local_benchmark(project_root: Path) -> dict[str, Any]:
    """Run the offline max suite and persist privacy-safe benchmark evidence."""
    from opaihub.benchmark import run_benchmark

    report = run_benchmark(project_root, suite="max", mode="both")
    score = report.get("efficiency_score", {})
    return {
        "run_id": report.get("run_id"),
        "effectiveness_index": score.get("opai_effectiveness_index"),
        "context_reduction_ratio": min(
            50.0, float(score.get("context_reduction_ratio", 0) or 0)
        ),
        "paid_calls_avoided": score.get("paid_calls_avoided"),
    }


# --------------------------------------------------------------------------- #
# Chat surface: model picker, ask, and the tool dispatcher (powers the GUI)
# --------------------------------------------------------------------------- #
def available_models(
    project_root: Path, *, discover_local: bool = True
) -> dict[str, Any]:
    """Pickable models: connected accounts → free API → Auto → local.

    Accounts (Claude/Codex/Copilot via logged-in CLIs) are the headline path.
    Verified free-tier API models (Gemini, Groq, Mistral) always appear — grayed
    when no API key is set. Auto routes cheapest safe option. Local models are
    the fully private, advanced fallback. Read-only — never installs, downloads,
    or signs in.
    """
    from opaihub.accounts import (
        account_models,
        connection_for_account,
        list_connected_accounts,
        provider_contract_payload,
        provider_connection_doctor,
    )
    from opaihub.provider_catalog import CATALOG_VERSION, PROTOCOL_VERSION, provider_ids
    from opaihub.free_models import list_free_models
    from opaihub.local_runner import cached_local_models, list_local_models

    detected_accounts = list_connected_accounts()
    connections = [connection_for_account(account) for account in detected_accounts]
    # Use only local connection history here: it records a recent safe auth
    # check (including a known failure) without adding a CLI/provider probe to
    # model-picker enumeration.
    account_health = {
        str(entry.get("providerId") or ""): entry
        for entry in provider_connection_doctor(
            accounts=detected_accounts,
            connections=connections,
            include_cli_versions=False,
            include_history=True,
        )
    }
    account_types = {
        provider: str(health.get("accountType") or "unknown")
        for provider, health in account_health.items()
    }
    accounts = account_models(
        accounts=detected_accounts,
        account_types=account_types,
    )
    account_catalog = account_models(
        include_unavailable=True,
        accounts=detected_accounts,
        account_types=account_types,
    )
    unavailable_account_statuses = {
        "misconfigured",
        "provider_unavailable",
        "invalid",
        "expired",
        "disconnected",
    }
    local = list_local_models(project_root) if discover_local else cached_local_models()
    options: list[dict[str, Any]] = []

    # 1. Connected account models — group already set by _account_options()
    for account in accounts:
        health = account_health.get(str(account.get("provider") or ""), {})
        auth_status = str(health.get("authStatus") or "").lower()
        known_unavailable = auth_status in unavailable_account_statuses
        options.append(
            {
                "id": account["id"],
                "label": account["label"],
                "advanced_label": account.get("advanced_label", account["label"]),
                "kind": "account",
                "group": account.get("group", "account"),
                "paid": True,
                "provider": account["provider"],
                "model": account.get("model", ""),
                "available": bool(account.get("available", True))
                and not known_unavailable,
                "disabled_reason": (
                    str(health.get("safeDiagnostic") or "") or None
                    if known_unavailable
                    else account.get("disabled_reason")
                ),
                "repo_editing": account.get("repo_editing"),
                "cli_version": account.get("cli_version", ""),
            }
        )

    # 2. Free API models (always visible, grayed when no key)
    options.extend(list_free_models())

    # 2b. Paid direct-API models (#673) — same always-visible/grayed contract
    # as free, but every entry here carries paid=True from list_paid_api_models.
    from opaihub.paid_api_models import list_paid_api_models

    options.extend(list_paid_api_models())

    # 3. OPai Auto routing
    options.append(
        {
            "id": "auto",
            "label": "OPai · Auto mode",
            "advanced_label": "Automatic local-first routing",
            "kind": "auto",
            "group": "routing",
        }
    )

    # 4. Local models (ollama, lmstudio, etc.)
    for model in local:
        options.append(
            {
                "id": model["id"],
                "label": "OPai · Local mode",
                "advanced_label": f"{model['model']} via {model['provider']} on this device",
                "kind": "local",
                "group": "local",
                "endpoint": model["endpoint"],
            }
        )

    # Health signal for the model picker (#redesign): a model is "proven
    # working" only when it is configured/reachable (``available``) AND its
    # provider is not in a reliability cooldown from recent failures. This is
    # what lets the picker show only models that actually work — e.g. a
    # free-tier key that is set but whose account is suspended (it keeps
    # failing) is marked unhealthy so the picker can gray it out honestly.
    # Purely local: reads the reliability memory, makes no network call.
    from opaihub import provider_balance as _bal
    from opaihub import provider_blocks as _blocks
    from opaihub import provider_reliability as _rel

    health_now = time.time()
    remote_providers = {
        str(option.get("provider") or "").strip().lower()
        for option in options
        if option.get("kind") not in {"auto", "local"}
        and str(option.get("provider") or "").strip()
    }
    reliability = _rel.reliability_snapshot(project_root, now=health_now)
    balances = _bal.balance_snapshots(project_root, remote_providers, now=health_now)
    blocks = _blocks.blocked_providers(project_root, needs_edit=True, now=health_now)

    for option in options:
        provider = str(option.get("provider") or "").strip().lower()
        if not provider or option.get("kind") in {"auto", "local"}:
            # The Auto card and on-device local models have no remote provider
            # reliability to consult; treat them as healthy.
            option["healthy"] = True
            option["health_reason"] = None
            option["balance"] = None
            option["out_of_credit"] = False
            option["blocked_reason"] = None
            option["edit_blocked_reason"] = None
            continue
        provider_reliability = reliability.get(provider, {})
        cooldown = bool(provider_reliability.get("cooldown", False))
        penalty = float(provider_reliability.get("penalty", 0.0))
        healthy = not cooldown and penalty < 0.5
        option["healthy"] = healthy
        option["health_reason"] = (
            None
            if healthy
            else "Recently unavailable — OPai will retry it automatically."
        )
        # Balance truth for the picker (cache-only — no network call during
        # enumeration): the exact remaining amount when known, and a hard
        # "out of credit" verdict that removes the model from selection with
        # an explanation instead of leaving a dead entry the user can click.
        balance = balances[provider]
        option["balance"] = balance
        out_of_credit = balance["status"] == "out"
        option["out_of_credit"] = out_of_credit
        if out_of_credit:
            option["available"] = False
            option["healthy"] = False
            reason = (
                f"{balance['displayName']} is out of credit. {balance['rechargeHint']}"
            )
            option["disabled_reason"] = reason
            option["health_reason"] = reason
        # Deterministic blocks (stale CLI, invalid config, no bounded edit
        # tools). Letting the user pick a model OPai has already watched refuse
        # every request is the picker's version of the consistency bug: the
        # click looks fine and the run always fails. Two separate fields so an
        # edit-incapable provider stays a legitimate Ask/Plan choice — the
        # picker grays it only when the current mode will write files.
        block = blocks.get(provider)
        option["blocked_reason"] = None
        option["edit_blocked_reason"] = None
        # Known before the first run, not discovered by failing one: a CLI that
        # cannot expose a bounded edit-tool set will be refused write access by
        # OPai every time. Say so in the picker instead of letting the user pick
        # it for an editing task and hit the refusal.
        if option.get("repo_editing") is False:
            option["edit_blocked_reason"] = (
                f"{_bal.provider_display_name(provider)} cannot be given safe "
                "repository write access from this CLI. Use it for Ask or Plan, "
                "or update its CLI for scoped tools."
            )
        if block:
            text = f"{block['title']} {block['remedy']}".strip()
            if block["scope"] == "edit":
                option["edit_blocked_reason"] = text
            else:
                option["blocked_reason"] = text
                option["available"] = False
                option["healthy"] = False
                option["disabled_reason"] = text
                option["health_reason"] = text

    if accounts or local:
        hint = None
    else:
        hint = (
            "No AI account connected. Sign in to Claude, Codex, or Copilot (run "
            "`claude`, `codex`, or `copilot` once), or add a local model under Advanced."
        )
    # Doctor precomputes every item with the same pure catalog contract helper.
    # Reuse that readout rather than probing a provider or declaring
    # completion/cost/authority/verification truth during model enumeration.
    provider_contracts: dict[str, dict[str, Any]] = {}
    for provider in provider_ids():
        doctor_entry = account_health.get(provider)
        contract = (
            doctor_entry.get("providerContract")
            if isinstance(doctor_entry, dict)
            else None
        )
        # Older callers/tests may supply only a partial doctor record.  Rebuild
        # it through the same canonical helper rather than falling back to a
        # stale provider-specific profile.
        provider_contracts[provider] = (
            dict(contract)
            if isinstance(contract, dict)
            else provider_contract_payload(provider, observation=doctor_entry)
        )
    return {
        "models": options,
        "available_models": options,
        "account_models": account_catalog,
        "accounts": detected_accounts,
        "connections": connections,
        "account_count": len(accounts),
        "account_model_count": len(accounts),
        "local_count": len(local),
        "setup": model_setup(project_root),
        "hint": hint,
        "providerCatalogVersion": CATALOG_VERSION,
        "providerProtocolVersion": PROTOCOL_VERSION,
        "providerContracts": provider_contracts,
    }


def ask(
    project_root: Path,
    task: str,
    model_choice: str = "auto",
    *,
    allow_cloud: bool = False,
    allow_edits: bool = False,
    tool_calling_enabled: bool | None = None,
    allow_command: str | None = None,
    edit_grant: bool = False,
    record_route: bool = True,
    account_runner: Any = None,
    mode: str | None = None,
    on_event: Any = None,
    on_text: Any = None,
    cancel: Any = None,
    tool_loop_policy: Any = None,
    deadline_budget: Any = None,
    repository_handle: Any = None,
) -> dict[str, Any]:
    """Run a coding task. ``model_choice`` is 'auto', 'account:<id>', 'free:<id>', 'paid:<id>', or 'provider:model'.

    - ``account:<id>`` runs through your connected Claude/Codex CLI: paid, blocked
      under panic mode, and recorded as a real spend (not a saving).
    - ``free:<id>`` runs through a free-tier-eligible public API
      (Gemini/Groq/Mistral). Requires the relevant API key env var and always
      prompts because provider quotas or billing may still apply.
    - ``paid:<id>`` (#673, e.g. DeepSeek) runs through a paid direct public API:
      same key-from-env-var and confirmation-gate contract as ``free:<id>``,
      but real per-token spend is recorded, never $0.
    - ``auto`` lets OPai route the cheapest safe path (local execution + cache).
    - a local ``provider:model`` id runs that connected local model.
    Cloud auto-routing is never auto-called - it returns ``confirmation_required``.

    ``tool_calling_enabled`` offers the read-only tool vocabulary to free
    models even when ``allow_edits`` is False (F6/F7); ``allow_command`` is a
    one-shot exact-command grant from a command-approval prompt (F17/F9).
    """
    root = project_root.expanduser().resolve()
    if model_choice and model_choice.startswith("account:"):
        # "account:claude:opus" -> account_id "claude", model "opus"; "account:codex" -> "codex", "".
        spec = model_choice.split(":", 1)[1]
        account_id, _, model = spec.partition(":")
        return _ask_account(
            root,
            task,
            account_id,
            model=model or None,
            allow_edits=allow_edits,
            edit_grant=edit_grant,
            runner=account_runner,
            mode=mode,
            on_event=on_event,
            on_text=on_text,
            cancel=cancel,
            tool_loop_policy=tool_loop_policy,
            deadline_budget=deadline_budget,
        )

    # "paid:" (#673, e.g. DeepSeek) shares the free tier's whole dispatch
    # shape — direct public API, key from env var, same confirmation gate —
    # differing only in what it actually costs, which _ask_direct_api_model
    # resolves per-model rather than by branch here.
    if model_choice and (
        model_choice.startswith("free:") or model_choice.startswith("paid:")
    ):
        return _ask_direct_api_model(
            root,
            task,
            model_choice,
            allow_cloud=allow_cloud,
            allow_edits=allow_edits,
            tool_calling_enabled=tool_calling_enabled,
            allow_command=allow_command,
            mode=mode,
            record_route=record_route,
            cancel=cancel,
            on_text=on_text,
            tool_loop_policy=tool_loop_policy,
            deadline_budget=deadline_budget,
            repository_handle=repository_handle,
        )

    from opaihub.ask import run_ask
    from opaihub.local_runner import runner_for_model

    runner = None
    if model_choice and model_choice != "auto":
        runner = runner_for_model(model_choice, project_root)
    return run_ask(
        root,
        task,
        runner=runner,
        record=True,
        allow_cloud=allow_cloud,
        allow_edits=allow_edits,
        cancel=cancel,
        # Live token streaming for local/free chat (#154); edits use the tool
        # loop, which doesn't stream prose.
        on_text=None if allow_edits else on_text,
        deadline_budget=deadline_budget,
    )


def _ask_direct_api_model(
    project_root: Path,
    task: str,
    model_id: str,
    *,
    allow_cloud: bool = False,
    allow_edits: bool = False,
    tool_calling_enabled: bool | None = None,
    allow_command: str | None = None,
    mode: str | None = None,
    record_route: bool = True,
    cancel: Any = None,
    on_text: Any = None,
    tool_loop_policy: Any = None,
    deadline_budget: Any = None,
    repository_handle: Any = None,
) -> dict[str, Any]:
    """Run a task through a direct public-API model — free tier (Gemini, Groq,
    Mistral) or paid per-token tier (DeepSeek, #673).

    Both tiers leave the device — always returns ``confirmation_required``
    unless ``allow_cloud=True`` is explicitly set by the caller (e.g. after
    user confirmed the dialog). When confirmed, the call is dispatched
    through :class:`~opaihub.local_runner.FreeAPIRunner` or its paid subclass
    :class:`~opaihub.local_runner.PaidAPIRunner`, both of which read the API
    key from the environment; ``runner_for_model`` picks the right one from
    the ``free:``/``paid:`` prefix.
    """
    from opaihub.ask import run_explicit_model
    from opaihub.local_runner import runner_for_model

    is_paid = model_id.startswith("paid:")

    def _spec_for(mid: str) -> dict[str, Any] | None:
        if is_paid:
            from opaihub.paid_api_models import spec_for_model_id as paid_spec

            return paid_spec(mid)
        from opaihub.free_models import spec_for_model_id as free_spec

        return free_spec(mid)

    if not allow_cloud:
        spec = _spec_for(model_id)
        # Extract display name from label "Gemini · 3.1 Flash-Lite (...)".
        if spec:
            label = spec["label"]
            provider_name = label.split(" ·")[0] if " ·" in label else spec["provider"]
        else:
            provider_name = model_id
        context_description = (
            "your task and repository file contents requested by the coding tools"
            if allow_edits
            else "your task and compact project context"
        )
        # Paid tier: state plainly that this is metered, not "may apply" —
        # OPai already has real per-token pricing for it, so hedged free-tier
        # wording would understate a known, real cost (#673 "product truth").
        cost_sentence = (
            "This provider bills per token; OPai records the exact spend."
            if is_paid
            else "Provider quota or billing may apply depending on your account."
        )
        return {
            "status": "confirmation_required",
            "message": (
                f"This will send your task to {provider_name}'s public API. "
                f"{context_description.capitalize()} will leave this device. "
                f"{cost_sentence} Continue?"
            ),
            "model_id": model_id,
        }

    runner = runner_for_model(model_id, project_root)
    spec = _spec_for(model_id)
    if runner is None or not runner.available():
        return {
            "status": "model_unavailable",
            "model_id": model_id,
            "hint": (spec or {}).get(
                "setup_hint", "The selected model is not configured."
            ),
        }
    before = set(_changed_files(project_root)) if allow_edits else set()
    before_identities = _changed_file_identities(project_root, before)
    if not allow_edits:
        before_identities = {}
    result = run_explicit_model(
        project_root,
        task,
        runner=runner,
        selected_model_id=model_id,
        allow_edits=allow_edits,
        # F6/F7: free models get the (read-only, when edits are off) tool
        # vocabulary and a real tool loop instead of narrating fake calls.
        tool_calling_enabled=tool_calling_enabled,
        allow_command=allow_command,
        provider_id=(spec or {}).get("provider"),
        mode=mode or ("safe-auto" if allow_edits else "ask"),
        record=record_route,
        cancel=cancel,
        on_text=None if allow_edits else on_text,
        # The consent to leave the device was already granted above (the
        # allow_cloud gate this function opened with) — without threading it
        # through, every per-turn ExecutionGuard check inside the tool loop
        # re-demands consent it has no way to collect, so the run always
        # dead-ends as "needs_consent" with an empty answer (#219).
        allow_cloud=allow_cloud,
        # The turn's contract budgets (tool calls, wall clock, compaction),
        # so a long multi-file task is not held to a short task's allowance.
        tool_loop_policy=tool_loop_policy,
        deadline_budget=deadline_budget,
        repository_handle=repository_handle,
    )
    if result.get("status") == "runner_error":
        from opai.provider_contract import normalize_provider_error

        error = normalize_provider_error(
            str((spec or {}).get("provider") or "direct-api"),
            result.get("error"),
            model=str((spec or {}).get("model_id") or model_id),
        )
        result["error"] = error
        result["answer"] = error["userMessage"]
    # Balance truth: a refused-for-credit call marks the provider out of
    # credit (picker + Auto skip it); a genuinely completed call proves credit
    # exists and clears any stale exhaustion. Best-effort, never raises.
    _note_provider_balance(
        project_root, str((spec or {}).get("provider") or ""), result
    )
    if result.get("status") == "answered_locally":
        # #673: a DeepSeek answer is not "free" — say which direct-API tier
        # it actually was. opaihub/lifecycle_schema.json's legacy_mappings
        # maps both statuses to "completed"; test_state_vocabulary_drift.py
        # enforces every literal status string here stays mapped.
        result["status"] = "answered_by_paid_api" if is_paid else "answered_by_free_api"
        result["source"] = "paid_api" if is_paid else "free_api"
        # Task 6/7: a tool-loop run already recorded one sequenced ledger event
        # per provider turn (opaihub/local_runner.py). Both writers append to
        # the SAME ledger event type usage.py aggregates, so also writing this
        # legacy aggregate here would double-count tokens/cost/model_calls.
        # Only write it for runners that don't yet self-record per turn.
        if not result.get("ledger_recorded_per_turn"):
            with contextlib.suppress(Exception):
                from opaihub.cost_model import estimate_tokens
                from opaihub.ledger import record_model_call

                usage = dict(getattr(runner, "last_usage", {}) or {})
                answer = str(result.get("answer") or "")
                tokens = int(
                    usage.get("tokens") or estimate_tokens(task + "\n" + answer)
                )
                # #673: real spend for the paid tier via the same _cost_for
                # seam the tool-loop path uses (opaihub/local_runner.py).
                # Deliberately NOT `real_cost_usd = None` for a paid call that
                # lacks it: None triggers record_model_call's tier-rate
                # fallback, which has no DeepSeek entry and would silently
                # under-report a real charge as the (near-zero) free-tier
                # rate. runner_for_model always builds a PaidAPIRunner for a
                # "paid:" id — cost_for missing here means a non-standard
                # runner was injected (a test double), so failing the cost to
                # unresolved rather than guessing is the only honest option.
                real_cost_usd: float | None = None
                cost_measurement = str(usage.get("measurement") or "estimated")
                if is_paid:
                    cost_for = getattr(runner, "_cost_for", None)
                    if callable(cost_for):
                        real_cost_usd, cost_measurement = cost_for(usage)
                    else:
                        real_cost_usd, cost_measurement = 0.0, "unknown"
                record_model_call(
                    project_root,
                    task,
                    model_tier="L2",
                    provider_type="paid_api" if is_paid else "free_api",
                    tokens=tokens,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    confirmed=True,
                    model_id=model_id,
                    provider_id=(spec or {}).get("provider"),
                    real_cost_usd=real_cost_usd,
                    measurement=cost_measurement,
                    quota_snapshot=usage.get("quota_snapshot"),
                    # #334: a multi-step tool run is many provider calls; record
                    # the count so the summed token figure reads honestly.
                    model_calls=int(usage.get("model_calls") or 1),
                )
        result["changed_files"] = (
            _changed_since(project_root, before, before_identities)
            if allow_edits
            else []
        )
    result["model_id"] = model_id
    result["free_tier"] = not is_paid
    return result


def _note_provider_balance(
    project_root: Path, provider: str, result: dict[str, Any]
) -> None:
    """Feed the provider-balance memory from a real run's outcome.

    Out-of-credit evidence comes from either the normalized error code or the
    raw transport error a tool-loop run carries in ``last_error`` (the loop
    reports ``provider_error`` without normalizing). A completed run clears
    the flag. Best-effort: balance memory is an optimization, never a
    dependency of the run result.
    """
    if not provider:
        return
    with contextlib.suppress(Exception):
        from opai.provider_contract import classify_error_code
        from opaihub import provider_balance

        error = result.get("error")
        code = str(error.get("code") or "") if isinstance(error, dict) else ""
        if not code and result.get("last_error"):
            code = classify_error_code(result.get("last_error"))
        if code == "PROVIDER_QUOTA_EXHAUSTED":
            provider_balance.record_exhausted(project_root, provider)
        elif str(result.get("completion_state") or "") == "completed":
            provider_balance.record_success(project_root, provider)


def _changed_files(root: Path) -> list[str]:
    """`git status --short` after an edit run, so the user sees what changed."""
    with contextlib.suppress(Exception):
        from opaihub.accounts import _hidden_run

        proc = _hidden_run(["git", "status", "--short"], cwd=str(root), timeout=10.0)
        return [line for line in (proc.stdout or "").splitlines() if line.strip()]
    return []


def _status_path(root: Path, status_line: str) -> Path:
    rel = status_line[3:].strip() if len(status_line) >= 3 else status_line.strip()
    if " -> " in rel:
        rel = rel.rsplit(" -> ", 1)[-1]
    rel = rel.strip('"')
    return root / rel


def _changed_file_identities(root: Path, status_lines: set[str]) -> dict[str, str]:
    """Content identities for already-dirty files before a provider run.

    #620: status-line deltas miss edits to files that were dirty before the
    run, because they remain the same ``git status --short`` line afterward.
    Keep the public status-line contract, but compare a cheap file identity for
    pre-existing dirty paths so OPai can still attribute the provider's write.
    """
    identities: dict[str, str] = {}
    for status in status_lines:
        with contextlib.suppress(OSError):
            path = _status_path(root, status)
            if path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                identities[status] = f"file:{digest}"
            elif path.exists():
                identities[status] = "exists"
            else:
                identities[status] = "missing"
    return identities


def _changed_since(
    root: Path, before: set[str], before_identities: dict[str, str]
) -> list[str]:
    after = set(_changed_files(root))
    after_identities = _changed_file_identities(root, after)
    changed_existing = {
        status
        for status in after & before
        if after_identities.get(status) != before_identities.get(status)
    }
    return sorted((after - before) | changed_existing)


def _invalidate_stale_auth_cache(account_id: str, error: dict[str, Any]) -> None:
    """Bust the connection cache when a real completion call proves it stale.

    The pre-flight connection check can report "connected" from a 5-minute
    cache while the account's OAuth session has actually died in between —
    the exact gap that turns "OPai says connected" into a live 401. Once a
    genuine completion call proves the cached verdict wrong, drop it so the
    next check (an automatic retry, or "Test connection" in Settings) reflects
    reality instead of repeating the stale "connected" for the rest of the
    cache window.
    """
    if str(error.get("code") or "").startswith("AUTH_"):
        with contextlib.suppress(Exception):
            from opaihub.accounts import invalidate_connection_cache

            invalidate_connection_cache(account_id)


def _account_completion(result: Any, answer: str) -> tuple[str, str]:
    """Canonical ``(completion_state, stopped_reason)`` for an account run.

    Older runners only ever returned ``{"text", "cost"}``; newer ones surface
    the provider's own terminal signals (``is_error`` / result ``subtype`` /
    permission denials). A run the provider says errored, stopped early, or
    was refused permission is NOT a completion, even when it produced prose
    (F24) — the GUI's green state must never be inferred from text alone.
    """

    if not isinstance(result, dict):
        return ("completed" if str(answer).strip() else "failed"), ""
    explicit = str(result.get("completion_state") or "").strip()
    if explicit:
        return explicit, str(result.get("stopped_reason") or "")
    if result.get("no_progress"):
        # F27: the no-progress guard checkpointed the run — never a completion.
        return "stuck_no_progress", "no_progress_guard"
    if (
        result.get("permission_denied")
        or result.get("permission_denials")
        or result.get("edit_denials")
    ):
        return "needs_consent", "approval_required"
    subtype = str(result.get("subtype") or "").strip().lower()
    if result.get("is_error") is True or subtype.startswith("error_"):
        return "failed", subtype or "provider_error"
    if subtype and subtype != "success":
        return "stuck_no_progress", subtype
    if not str(answer).strip():
        return "failed", ""
    return "completed", ""


def _record_paid_account_cost(
    root: Path,
    task: str,
    answer: str,
    cost: Any,
    *,
    call_id: str,
) -> dict[str, Any]:
    """Finalize one answered paid call through ledger v2 only."""

    finalized = False
    ledger_error = ""
    try:
        from opaihub.cost_model import estimate_tokens, tier_cost
        from opaihub.ledger import (
            EVENT_MODEL_CALL,
            reconcile_observed_model_calls,
            record_model_call_usage_observed,
        )
        from opaihub.usage_report import (
            UNKNOWN_USAGE,
            ProviderTurnUsage,
            UsageValue,
        )

        tokens = estimate_tokens(task + "\n" + (answer or ""))
        reported_cost = (
            float(cost)
            if isinstance(cost, (int, float))
            and not isinstance(cost, bool)
            and cost >= 0
            else None
        )
        tier_estimate = tier_cost("L3", tokens)
        measured_cost = (
            UsageValue(reported_cost, "actual")
            if reported_cost is not None
            else UNKNOWN_USAGE
        )
        use_reported_cost = reported_cost is not None and reported_cost > 0
        estimated_actual = UsageValue(
            reported_cost if use_reported_cost else tier_estimate,
            "actual" if use_reported_cost else "estimated",
        )
        observed = record_model_call_usage_observed(
            root,
            task,
            call_id=call_id,
            usage=ProviderTurnUsage(
                turn_index=1,
                total_tokens=UsageValue(tokens, "estimated"),
                cost_usd=measured_cost,
                tier_estimate_usd=UsageValue(tier_estimate, "estimated"),
                estimated_actual_usd=estimated_actual,
            ),
        )
        finalized = observed.get("event_type") == EVENT_MODEL_CALL
        if not finalized:
            finalized = bool(reconcile_observed_model_calls(root, call_ids=(call_id,)))
    except Exception as exc:  # noqa: BLE001 - preserve the provider outcome
        from opai.provider_contract import redact_secrets

        ledger_error = redact_secrets(exc)

    return {
        "ledger_recorded": finalized,
        "ledger_recorded_per_turn": finalized,
        "cost_integrity": "complete" if finalized else "unreconciled",
        "cost_unreconciled": not finalized,
        "ledger_error": ledger_error,
    }


def _unresolved_paid_account_cost() -> dict[str, Any]:
    """A dispatched paid call whose terminal usage is not authoritative."""

    return {
        "ledger_recorded": False,
        "ledger_recorded_per_turn": False,
        "cost_integrity": "unreconciled",
        "cost_unreconciled": True,
        "ledger_error": "",
    }


def _settle_paid_account_non_dispatch(
    root: Path,
    task: str,
    *,
    call_id: str,
    error_code: str,
) -> dict[str, Any]:
    """Close a started ledger item only with typed proof of non-dispatch."""

    from opaihub.operation_class import DispatchProof, dispatch_proof

    if dispatch_proof(error_code) is not DispatchProof.NOT_DISPATCHED:
        return _unresolved_paid_account_cost()
    try:
        from opaihub.ledger import record_model_call_not_dispatched

        record_model_call_not_dispatched(
            root,
            task,
            call_id=call_id,
            reason_code=error_code,
        )
    except Exception as exc:  # noqa: BLE001 - unresolved is the honest fallback
        from opai.provider_contract import redact_secrets

        unresolved = _unresolved_paid_account_cost()
        unresolved["ledger_error"] = redact_secrets(exc)
        return unresolved
    return {
        "ledger_recorded": True,
        "ledger_recorded_per_turn": True,
        "cost_integrity": "complete",
        "cost_unreconciled": False,
        "ledger_error": "",
        "dispatch_proof": "not_dispatched",
    }


def _ask_account(
    project_root: Path,
    task: str,
    account_id: str,
    *,
    model: str | None = None,
    allow_edits: bool = False,
    edit_grant: bool = False,
    runner: Any = None,
    mode: str | None = None,
    on_event: Any = None,
    on_text: Any = None,
    cancel: Any = None,
    tool_loop_policy: Any = None,
    deadline_budget: Any = None,
    _fallback_used: bool = False,
    _parent_operation_id: str | None = None,
) -> dict[str, Any]:
    """Run a task through a connected paid-account CLI, with firewall gating.

    When ``on_event``/``on_text``/``cancel`` are supplied and the runner exposes
    ``stream()``, the call streams live activity and is cancellable; otherwise it
    uses the blocking ``complete()`` path (unchanged).

    ``_fallback_used`` is internal: on a ``MODEL_UNAVAILABLE`` error OPai retries
    once with the provider's safe default model (#318), and this guard stops the
    retry from recursing.
    """
    root = project_root.expanduser().resolve()

    def _fail(error: dict[str, Any]) -> dict[str, Any]:
        """Return an honest failure — but first, recover once from a rejected
        model by retrying with the provider's safe default (#318)."""
        error_code = str(error.get("code") or "UNKNOWN")
        cost_record = _settle_paid_account_non_dispatch(
            root,
            task,
            call_id=call_id,
            error_code=error_code,
        )
        failed = {
            "status": "failed",
            "provider": account_id,
            "answer": error["userMessage"],
            "error": error,
            "operation_id": operation_id,
            "ledger_dispatch_recorded": dispatch_recorded,
            "ledger_call_id": call_id,
            "provider_invocation": invocation_info,
            **cost_record,
        }
        if (
            _fallback_used
            or error_code != "MODEL_UNAVAILABLE"
            or cost_record.get("dispatch_proof") != "not_dispatched"
        ):
            return failed
        from opai.model_registry import default_model, models_for, resolve_id

        failed_id = resolve_id(account_id, model) if model else None
        default = default_model(account_id)
        fallback_id = default.id if default is not None else None
        if fallback_id and fallback_id == failed_id:
            # The default itself was rejected — try any other listed model.
            fallback_id = next(
                (s.id for s in models_for(account_id) if s.id != failed_id), None
            )
        if not fallback_id or fallback_id == failed_id:
            return failed
        recovered = _ask_account(
            root,
            task,
            account_id,
            model=fallback_id,
            allow_edits=allow_edits,
            edit_grant=edit_grant,
            runner=None,  # build a fresh runner for the fallback model
            mode=mode,
            on_event=on_event,
            on_text=on_text,
            cancel=cancel,
            tool_loop_policy=tool_loop_policy,
            deadline_budget=deadline_budget,
            _fallback_used=True,
            _parent_operation_id=operation_id,
        )
        if recovered.get("status") == "failed":
            return failed  # the fallback also failed — surface the original error
        note = f"(The selected model was unavailable; ran {fallback_id} instead.)"
        recovered["model_fallback"] = {
            "from": model or "",
            "to": fallback_id,
            "reason": "MODEL_UNAVAILABLE",
            "prior_operation_id": operation_id,
            "operation_id": recovered.get("operation_id"),
        }
        recovered["answer"] = (
            str(recovered.get("answer") or "").rstrip() + "\n\n" + note
        ).strip()
        return recovered

    # Cost firewall: panic mode means local-only, so block paid account calls.
    if cost_firewall(root).get("panic"):
        return {
            "status": "blocked_panic",
            "provider": account_id,
            "reason": "Panic mode is on (local-only). Turn panic off to use a paid account.",
        }

    from opaihub.accounts import runner_for_account

    run = runner if runner is not None else runner_for_account(account_id, model=model)
    if run is None or not run.available():
        return {
            "status": "account_not_connected",
            "provider": account_id,
            "hint": f"Connect your {account_id} account: run `{account_id}` once and sign in.",
        }
    if account_id == "copilot" and allow_edits:
        capability_check = getattr(run, "supports_scoped_editing", None)
        try:
            scoped_editing = (
                bool(capability_check()) if callable(capability_check) else False
            )
        except (OSError, subprocess.SubprocessError):
            scoped_editing = False
        if not scoped_editing:
            return {
                "status": "capability_mismatch",
                "provider": "copilot",
                "capability": "edit_files",
                "reason": (
                    "This Copilot CLI cannot expose a bounded edit-tool set, so "
                    "OPai refused to launch it with repository write access."
                ),
                "hint": (
                    "Update GitHub Copilot CLI, or switch to Ask or Plan until "
                    "scoped tool permissions are available."
                ),
            }
    stream_method = getattr(run, "stream", None)
    want_stream = (
        on_event is not None or on_text is not None or cancel is not None
    ) and callable(stream_method)
    invocation_method = stream_method if want_stream else getattr(run, "complete", None)
    timeout_seconds = getattr(deadline_budget, "task_deadline_seconds", None)
    if timeout_seconds is None:
        timeout_seconds = getattr(tool_loop_policy, "max_active_seconds", None)
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds, (int, float)
    ):
        timeout_seconds = None
    provider_idle_seconds = getattr(
        deadline_budget, "provider_idle_timeout_seconds", None
    )
    if isinstance(provider_idle_seconds, bool) or not isinstance(
        provider_idle_seconds, (int, float)
    ):
        provider_idle_seconds = None
    try:
        invocation_plan = ProviderInvocationPlan.prepare(
            invocation_method,
            provider_id=account_id,
            method_name="stream" if want_stream else "complete",
        )

        def _invocation_kwargs(stable_operation_id: str) -> dict[str, Any]:
            required = {
                "project_root": root,
                "allow_edits": allow_edits,
            }
            optional: dict[str, Any] = {
                "mode": mode,
                "operation_id": stable_operation_id,
            }
            if timeout_seconds is not None:
                optional["timeout"] = float(timeout_seconds)
            if deadline_budget is not None:
                optional["deadline_budget"] = deadline_budget
            if edit_grant:
                optional["edit_grant"] = True
            if want_stream:
                optional.update(
                    {
                        "on_event": on_event,
                        "on_text": on_text,
                        "cancel": cancel,
                        "cancellation_scope_id": (
                            f"account-{stable_operation_id.replace(':', '-')}"
                        ),
                    }
                )
                if provider_idle_seconds is not None:
                    optional["provider_idle_timeout"] = float(provider_idle_seconds)
            return invocation_plan.keyword_arguments(
                required=required,
                optional=optional,
            )

        # Validate the full call shape before recording a paid operation.
        _invocation_kwargs("preflight-operation-id")
    except ProviderInvocationCompatibilityError as exc:
        from opai.provider_contract import redact_secrets

        return {
            "status": "capability_mismatch",
            "provider": account_id,
            "answer": (
                "The provider adapter is incompatible with this OPai runtime. "
                "Update the provider integration and try again."
            ),
            "operation_recorded": False,
            "error": {
                "code": "ADAPTER_INCOMPATIBLE",
                "technicalMessage": redact_secrets(exc),
            },
        }
    before = set(_changed_files(root)) if allow_edits else set()
    before_identities = _changed_file_identities(root, before)
    if not allow_edits:
        before_identities = {}
    run_id = uuid.uuid4().hex[:16]
    operation_kind = "model_call_paid"
    model_id = f"account:{account_id}:{model}" if model else f"account:{account_id}"
    operation_id = ""
    operation_key = ""
    dispatch_recorded = False
    dispatch_record_error = ""
    try:
        from opaihub.idempotency import (
            FRESH,
            begin,
            operation_key as make_operation_key,
        )
        from opaihub.ledger import record_operation_intent
        from opaihub.operation_class import classify_operation

        operation_key = make_operation_key(
            operation_kind,
            run_id=run_id,
            step_id=1,
            provider=account_id,
            model=model or "",
            mode=mode or "",
            task=task,
        )
        operation_id = operation_key
        claim = begin(root, operation_key)
        if claim.get("state") != FRESH:
            return {
                "status": "needs_attention",
                "provider": account_id,
                "answer": (
                    "OPai found an unresolved provider operation for this run. "
                    "It will not send the same paid request again until that "
                    "operation is reconciled."
                ),
                "operation_id": operation_id,
                "operation_state": claim.get("state"),
            }
        record_operation_intent(
            root,
            task,
            operation_id=operation_id,
            operation_key=operation_key,
            operation_kind=operation_kind,
            operation_class=classify_operation(operation_kind).value,
            target=model_id,
            metadata={
                "provider": account_id,
                "model": model or "",
                "parent_operation_id": _parent_operation_id or "",
            },
        )
    except Exception as exc:  # noqa: BLE001 - no durable intent, no paid dispatch
        from opai.provider_contract import redact_secrets

        return {
            "status": "operation_unrecorded",
            "provider": account_id,
            "answer": (
                "OPai did not send this paid request because it could not "
                "record the operation intent first."
            ),
            "operation_recorded": False,
            "error": {
                "code": "OPERATION_INTENT_UNRECORDED",
                "technicalMessage": redact_secrets(exc),
            },
        }
    call_id = operation_id
    try:
        from opaihub.ledger import record_model_call_started

        record_model_call_started(
            root,
            task,
            call_id=call_id,
            run_id=run_id,
            turn_index=1,
            model_id=model_id,
            provider_id=account_id,
            model_tier="L3",
            provider_type="cloud",
            confirmed=True,
        )
        dispatch_recorded = True
    except Exception as exc:  # noqa: BLE001 - do not dispatch untracked paid work
        from opai.provider_contract import redact_secrets

        dispatch_record_error = redact_secrets(exc)
    if not dispatch_recorded:
        return {
            "status": "cost_unreconciled",
            "provider": account_id,
            "answer": (
                "OPai did not send this paid request because it could not "
                "record the cost identity first."
            ),
            "operation_id": operation_id,
            "operation_recorded": True,
            "cost_integrity": "unreconciled",
            "cost_unreconciled": True,
            "ledger_recorded": False,
            "ledger_recorded_per_turn": False,
            "ledger_dispatch_recorded": False,
            "ledger_call_id": None,
            "ledger_error": dispatch_record_error,
        }
    invocation_kwargs = _invocation_kwargs(call_id)
    invocation_info = invocation_plan.to_dict(
        operation_id=operation_id,
        model_id=model_id,
    )
    try:
        result = invocation_plan.invoke(task, invocation_kwargs)
    except Exception as exc:  # noqa: BLE001 - surface any CLI failure cleanly
        from opai.provider_contract import normalize_provider_error

        error = normalize_provider_error(account_id, str(exc), model=model)
        _invalidate_stale_auth_cache(account_id, error)
        _note_provider_balance(root, account_id, {"error": error})
        return _fail(error)

    # User stopped it mid-flight: return the partial cleanly (not an error).
    if isinstance(result, dict) and result.get("cancelled"):
        cancellation = result.get("cancellation")
        cancellation = cancellation if isinstance(cancellation, dict) else {}
        if cancellation.get("phase") != "terminated":
            return {
                "status": "needs_attention",
                "provider": account_id,
                "model": getattr(run, "model", "") or account_id,
                "answer": (
                    "OPai received the stop request, but could not prove that "
                    "all provider work terminated. Inspect the cancellation "
                    "evidence before retrying."
                ),
                "completion_state": "needs_attention",
                "stopped_reason": "cancellation_unconfirmed",
                "cost_usd": result.get("cost"),
                "operation_id": operation_id,
                "ledger_dispatch_recorded": dispatch_recorded,
                "ledger_call_id": call_id if dispatch_recorded else None,
                "cancellation": cancellation,
                "provider_invocation": invocation_info,
                **_unresolved_paid_account_cost(),
            }
        return {
            "status": "cancelled",
            "provider": account_id,
            "model": getattr(run, "model", "") or account_id,
            "answer": (result.get("text") or "").strip(),
            "cost_usd": result.get("cost"),
            "operation_id": operation_id,
            "ledger_dispatch_recorded": dispatch_recorded,
            "ledger_call_id": call_id if dispatch_recorded else None,
            "cancellation": cancellation,
            "provider_invocation": invocation_info,
            **_unresolved_paid_account_cost(),
        }
    if isinstance(result, dict) and result.get("error") and not result.get("text"):
        from opai.provider_contract import normalize_provider_error

        raw_error = result.get("error")
        error = (
            raw_error
            if isinstance(raw_error, dict) and raw_error.get("code")
            else normalize_provider_error(
                account_id,
                raw_error,
                model=model,
                returncode=result.get("returncode"),
            )
        )
        _invalidate_stale_auth_cache(account_id, error)
        _note_provider_balance(root, account_id, {"error": error})
        return _fail(error)

    # A long agentic run that hit the time limit: stop cleanly, guide the user.
    if isinstance(result, dict) and result.get("timed_out"):
        from opai.provider_contract import normalize_provider_error
        from opaihub.deadlines import is_task_deadline

        timeout_info = result.get("timeout_event")
        timeout_info = timeout_info if isinstance(timeout_info, dict) else {}
        timeout_origin = str(timeout_info.get("timeout_origin") or "timeout")
        error = normalize_provider_error(
            account_id,
            "",
            model=model,
            timed_out=True,
            timeout_origin=timeout_origin,
        )
        partial_answer = str(result.get("text") or "").strip()
        cost = result.get("cost")
        changed = _changed_since(root, before, before_identities) if allow_edits else []
        # A timeout can arrive before final provider usage is complete. Even a
        # partial numeric cost does not prove the bill is final, so the started
        # call remains open for reconciliation.
        cost_record = _unresolved_paid_account_cost()
        task_deadline = is_task_deadline(timeout_info)
        return {
            # #378/#402: a timeout is a distinct terminal cause. The typed
            # PROVIDER_TIMEOUT error and the "timeout" stop reason are what the
            # shared completion verdict reads to render TIMEOUT identically in
            # GUI + CLI. The legacy status stays "failed" so ask/handle_gui_message
            # keep their clean-error contract, while proxy_run maps a
            # PROVIDER_TIMEOUT/"timeout" result to the "account_timeout" status
            # its capture ledger and desktop timeout card expect.
            "status": "failed",
            "provider": account_id,
            "model": getattr(run, "model", "") or account_id,
            "answer": error["userMessage"],
            "partial_answer": partial_answer,
            "error": error,
            "completion_state": "timeout",
            "stopped_reason": timeout_origin,
            "timeout_event": timeout_info,
            "timeout_origin": timeout_origin,
            "provider_condition": timeout_info.get("provider_condition"),
            "cost_usd": cost,
            "changed_files": changed,
            "retained_progress": {
                "partial_answer": bool(partial_answer),
                "changed_files": list(changed),
                "verification": "incomplete",
            },
            "next_actions": [
                (
                    "Inspect retained work and reconcile the prior operation before continuing."
                    if task_deadline
                    else "Inspect retained work, then retry or choose another provider."
                )
            ],
            "operation_id": operation_id,
            "operation_key": operation_key,
            "operation_recorded": True,
            "ledger_dispatch_recorded": dispatch_recorded,
            "ledger_call_id": call_id if dispatch_recorded else None,
            "cancellation": result.get("cancellation"),
            "provider_invocation": invocation_info,
            # Commands this run started and never saw finish. A deadline that
            # expired with one still running is a different story from a
            # provider that went quiet, and the verdict says which.
            "background_work": result.get("background_work") or {},
            **cost_record,
        }

    # complete() returns {"text", "cost"}; tolerate a plain string too.
    if isinstance(result, dict):
        answer = result.get("text") or ""
        cost = result.get("cost")
    else:
        answer, cost = str(result), None

    no_progress = isinstance(result, dict) and bool(result.get("no_progress"))
    if not str(answer).strip():
        if no_progress:
            steps = result.get("tool_steps") if isinstance(result, dict) else None
            # #648: say what actually stopped the run. This used to assert
            # "without a single edit attempt" unconditionally, which stayed on
            # screen after the guard became evidence-based -- so a run stopped
            # for repeating itself, or for reaching the absolute ceiling, was
            # still told its crime was not editing. That sends the user to fix
            # the wrong thing.
            trigger = str(result.get("no_progress_trigger") or "").strip()
            counted = f"{steps} tool steps" if steps is not None else "this run"
            if trigger == "stagnation":
                detail = (
                    f"{counted} stopped producing new evidence -- the recent "
                    "steps repeated work already done."
                )
            elif trigger == "exploration_ceiling":
                detail = (
                    f"{counted} reached the absolute exploration limit while "
                    "still investigating."
                )
            elif trigger == "time":
                detail = f"{counted} ran without learning anything new for too long."
            else:
                detail = f"{counted} ran without converging on the objective."
            answer = (
                f"OPai stopped this run early (convergence guard): {detail} "
                "Refine the request, or re-send to continue from here."
            )
        else:
            from opai.provider_contract import normalize_provider_error

            error = normalize_provider_error(account_id, "", model=model, returncode=0)
            return _fail(error)

    # Surface what the agent actually changed, like Claude Code / Cursor do.
    changed = _changed_since(root, before, before_identities) if allow_edits else []

    # F24: completion truth comes from the provider's own terminal signals,
    # not from the fact that prose exists.
    completion_state, stopped_reason = _account_completion(result, answer)
    _note_provider_balance(root, account_id, {"completion_state": completion_state})
    if operation_key:
        with contextlib.suppress(Exception):
            from opaihub.idempotency import complete as complete_operation

            complete_operation(
                root,
                operation_key,
                {
                    "status": completion_state,
                    "provider": account_id,
                    "model": model or "",
                },
            )

    # Only an answered terminal turn is finalized. Timeout, cancellation and
    # no-response exits deliberately leave the pre-dispatch identity unresolved.
    cost_record = _record_paid_account_cost(
        root,
        task,
        answer,
        cost,
        call_id=call_id,
    )

    return {
        "status": "answered_by_account",
        "provider": account_id,
        "paid": True,
        "model": getattr(run, "model", "") or account_id,
        "allow_edits": allow_edits,
        "cost_usd": cost,
        "changed_files": changed,
        "operation_id": operation_id,
        "operation_key": operation_key,
        "operation_recorded": True,
        "ledger_dispatch_recorded": dispatch_recorded,
        "ledger_call_id": call_id if dispatch_recorded else None,
        "provider_invocation": invocation_info,
        **cost_record,
        "completion_state": completion_state,
        "stopped_reason": stopped_reason,
        # #378: terminal verification must consume OPai-observed tool results.
        # Keep the structured trace through the account normalization boundary;
        # dropping it made a passed ``run_tests`` call indistinguishable from a
        # provider's unverified prose claim.
        "tool_trace": list(result.get("tool_trace") or [])
        if isinstance(result, dict)
        else [],
        # F26: Edit/Write attempts the provider's permission gate refused —
        # the pipeline turns these into an in-context approval card.
        "edit_denials": list(result.get("edit_denials") or [])
        if isinstance(result, dict)
        else [],
        # #486 follow-up: a turn that backgrounded a command and ended before it
        # reported must say so. Without this the verdict only saw the absence of
        # the result and called the evidence missing, which sends the user to
        # debug OPai instead of telling them their command is still running.
        "background_work": (
            result.get("background_work") or {} if isinstance(result, dict) else {}
        ),
        "no_progress": no_progress,
        "answer": answer,
    }


# Tools surfaced in the chat (slash commands + the Tools menu).
TOOLS: list[dict[str, Any]] = [
    {
        "id": "connect",
        "label": "Connect",
        "desc": "Connect Claude / Codex / Copilot accounts",
        "mutates": False,
    },
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

    if tool in {"connect", "accounts", "account", "login", "models", "model"}:
        data = available_models(root)
        lines = [
            "Connect your AI accounts — OPai routes through the CLIs you are "
            "already signed into, so these accounts need no API key. (API "
            "providers like GitHub or Gemini keep their keys in your OS "
            "credential store — see Settings → Providers & Connections.)",
            "",
        ]
        for account in data["accounts"]:
            if account["connected"]:
                state = "connected ✓"
            elif account["cli_present"]:
                state = "CLI found, not signed in"
            else:
                state = "CLI not installed"
            lines.append(f"- {account['label']} ({account['vendor']}): {state}")
            if not account["connected"]:
                lines.append(f"    {account['login_hint']}")
        lines.append("")
        if data["local_count"]:
            lines.append(
                f"Advanced · {data['local_count']} free local model(s) detected ($0)."
            )
        else:
            setup = data["setup"]
            lines.append("Advanced · optional free local model:")
            lines.append(f"    {setup['install']['command']}")
            lines.append(f"    {setup['recommended'][0]['command']}")
        lines.append("")
        lines.append("Local only. OPai never stores your credentials or prompts.")
        return {"ok": True, "title": "Connect accounts", "text": "\n".join(lines)}

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

    if tool in {"context_preview", "cleanup_preview"}:
        preview = cleanup_preview(root)
        sources = preview.get("top_sources", [])
        lines = [
            str(preview["note"]),
            "",
            f"Potential context reduction: ~{int(preview['would_reduce_tokens']):,} tokens "
            f"({int(preview['would_reduce_bytes']):,} bytes).",
        ]
        if sources:
            lines += [
                "",
                "Largest generated/cache sources:",
                *[
                    f"• {source.get('path', 'unknown')} "
                    f"({source.get('category', 'generated')})"
                    for source in sources
                ],
            ]
        suggested = preview.get("suggested_ignores", [])
        if suggested:
            lines += ["", "Ignore files OPai can update: " + ", ".join(suggested)]
        return {"ok": True, "title": "Cleanup preview", "text": "\n".join(lines)}

    if tool in {"ignores", "generate_ignores"}:
        return {
            "ok": True,
            "title": "Generate ignore files",
            "text": (
                "Append OPai-managed rules to supported AI ignore files. "
                "Existing user rules are preserved."
            ),
            "mutates": True,
            "confirm": (
                "Generate additive AI ignore rules for this project? "
                "This never deletes source code or existing user rules."
            ),
            "apply": ("ignores", None),
        }

    if tool == "benchmark_run":
        return {
            "ok": True,
            "title": "Run local benchmark",
            "text": (
                "Run the offline max benchmark and save privacy-safe evidence "
                "under .opaihub. No model provider is contacted."
            ),
            "mutates": True,
            "confirm": (
                "Run the local benchmark and write its privacy-safe evidence "
                "under .opaihub? No raw prompts or secrets are stored, and no "
                "cloud model is contacted."
            ),
            "apply": ("benchmark_run", None),
        }

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

    if tool in {"proof_json", "proof_markdown"}:
        fmt = "markdown" if tool == "proof_markdown" else "json"
        filename = "proof-bundle.md" if fmt == "markdown" else "proof-bundle.json"
        relative = f".opaihub/{filename}"
        return {
            "ok": True,
            "title": "Export proof bundle",
            "text": (
                f"Write a redacted, locally signed {fmt.upper()} proof bundle "
                f"to {relative}."
            ),
            "mutates": True,
            "confirm": (
                f"Export the redacted proof bundle to {relative}? "
                "Raw prompts and secrets are excluded."
            ),
            "apply": (tool, None),
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
    if name == "ignores":
        result = generate_ignores(project_root)
        entries = result.get("results")
        if not isinstance(entries, list):
            entries = result.get("written", [])
        count = len(entries) if isinstance(entries, list) else 0
        return {
            "ok": True,
            "text": (
                f"Updated {count} ignore file{'s' if count != 1 else ''}. "
                "Existing user rules were preserved; no source files were deleted."
            ),
        }
    if name == "benchmark_run":
        result = run_local_benchmark(project_root)
        return {
            "ok": True,
            "text": (
                "Benchmark complete.\n"
                f"Effectiveness index: {result.get('effectiveness_index')}\n"
                f"Context reduction: {result.get('context_reduction_ratio')}x\n"
                f"Paid calls avoided: {result.get('paid_calls_avoided')}\n"
                "Privacy-safe evidence was saved under .opaihub."
            ),
        }
    if name in {"proof_json", "proof_markdown"}:
        fmt = "markdown" if name == "proof_markdown" else "json"
        filename = "proof-bundle.md" if fmt == "markdown" else "proof-bundle.json"
        target = project_root / ".opaihub" / filename
        result = export_proof(project_root, target, fmt=fmt)
        return {
            "ok": result.get("status") == "exported",
            "text": (
                f"Exported a redacted, locally signed proof bundle to "
                f".opaihub/{filename}."
            ),
        }
    return {"ok": False, "text": "Nothing to apply."}
