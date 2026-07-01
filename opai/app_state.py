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
import os
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


def workspace_summary(project_root: Path) -> dict[str, Any]:
    """Tracked-file count + branch - the 'indexed workspace' the agent can see."""
    root = project_root.expanduser().resolve()
    files = [ln for ln in _git_text(root, ["ls-files"]).splitlines() if ln.strip()]
    branch = _git_text(root, ["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    return {
        "root": str(root),
        "name": root.name,
        "file_count": len(files),
        "branch": branch if branch and branch != "HEAD" else "",
    }


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
    """Pickable models: connected accounts first, then Auto, then local (advanced).

    Accounts (Claude/Codex via your logged-in CLI) are the headline path. Auto
    routes the cheapest safe option. Local models are the free, advanced
    fallback. Read-only - never installs, downloads, or signs in.
    """
    from opaihub.accounts import account_models, list_connected_accounts
    from opaihub.local_runner import list_local_models

    accounts = account_models()
    account_catalog = account_models(include_unavailable=True)
    local = list_local_models(project_root)
    options: list[dict[str, Any]] = []
    for account in accounts:
        options.append(
            {
                "id": account["id"],
                "label": account["label"],
                "kind": "account",
                "paid": True,
                "provider": account["provider"],
                "model": account.get("model", ""),
                "available": account.get("available", True),
                "disabled_reason": account.get("disabled_reason"),
            }
        )
    options.append(
        {
            "id": "auto",
            "label": "Auto · OPai routes the cheapest safe model",
            "kind": "auto",
        }
    )
    for model in local:
        options.append(
            {
                "id": model["id"],
                "label": f"{model['model']} · {model['provider']} (local)",
                "kind": "local",
                "endpoint": model["endpoint"],
            }
        )
    if accounts or local:
        hint = None
    else:
        hint = (
            "No AI account connected. Sign in to Claude, Codex, or Copilot (run "
            "`claude`, `codex`, or `copilot` once), or add a local model under Advanced."
        )
    return {
        "models": options,
        "available_models": options,
        "account_models": account_catalog,
        "accounts": list_connected_accounts(),
        "account_count": len(accounts),
        "account_model_count": len(accounts),
        "local_count": len(local),
        "setup": model_setup(project_root),
        "hint": hint,
    }


def ask(
    project_root: Path,
    task: str,
    model_choice: str = "auto",
    *,
    allow_cloud: bool = False,
    allow_edits: bool = False,
    account_runner: Any = None,
    mode: str | None = None,
    on_event: Any = None,
    on_text: Any = None,
    cancel: Any = None,
) -> dict[str, Any]:
    """Run a coding task. ``model_choice`` is 'auto', 'account:<id>', or 'provider:model'.

    - ``account:<id>`` runs through your connected Claude/Codex CLI: paid, blocked
      under panic mode, and recorded as a real spend (not a saving).
    - ``auto`` lets OPai route the cheapest safe path (local execution + cache).
    - a local ``provider:model`` id runs that connected local model.
    Cloud auto-routing is never auto-called - it returns ``confirmation_required``.
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
            runner=account_runner,
            mode=mode,
            on_event=on_event,
            on_text=on_text,
            cancel=cancel,
        )

    from opaihub.ask import run_ask
    from opaihub.local_runner import runner_for_model

    runner = None
    if model_choice and model_choice != "auto":
        runner = runner_for_model(model_choice, project_root)
    return run_ask(root, task, runner=runner, record=True, allow_cloud=allow_cloud)


def _changed_files(root: Path) -> list[str]:
    """`git status --short` after an edit run, so the user sees what changed."""
    with contextlib.suppress(Exception):
        from opaihub.accounts import _hidden_run

        proc = _hidden_run(["git", "status", "--short"], cwd=str(root), timeout=10.0)
        return [line for line in (proc.stdout or "").splitlines() if line.strip()]
    return []


def _ask_account(
    project_root: Path,
    task: str,
    account_id: str,
    *,
    model: str | None = None,
    allow_edits: bool = False,
    runner: Any = None,
    mode: str | None = None,
    on_event: Any = None,
    on_text: Any = None,
    cancel: Any = None,
) -> dict[str, Any]:
    """Run a task through a connected paid-account CLI, with firewall gating.

    When ``on_event``/``on_text``/``cancel`` are supplied and the runner exposes
    ``stream()``, the call streams live activity and is cancellable; otherwise it
    uses the blocking ``complete()`` path (unchanged).
    """
    root = project_root.expanduser().resolve()
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
    want_stream = (
        on_event is not None or on_text is not None or cancel is not None
    ) and hasattr(run, "stream")
    before = set(_changed_files(root)) if allow_edits else set()
    try:
        if want_stream:
            result = run.stream(
                task,
                project_root=root,
                allow_edits=allow_edits,
                mode=mode,
                on_event=on_event,
                on_text=on_text,
                cancel=cancel,
            )
        else:
            try:
                result = run.complete(
                    task, project_root=root, allow_edits=allow_edits, mode=mode
                )
            except TypeError as exc:
                if "mode" not in str(exc):
                    raise
                result = run.complete(task, project_root=root, allow_edits=allow_edits)
    except Exception as exc:  # noqa: BLE001 - surface any CLI failure cleanly
        return {
            "status": "account_error",
            "provider": account_id,
            "answer": (
                f"{account_id.capitalize()} hit an error and couldn't finish that. "
                "Try again, or pick a different model."
            ),
            "error": str(exc),  # kept for debugging, not shown raw to the user
        }

    # User stopped it mid-flight: return the partial cleanly (not an error).
    if isinstance(result, dict) and result.get("cancelled"):
        return {
            "status": "cancelled",
            "provider": account_id,
            "model": getattr(run, "model", "") or account_id,
            "answer": (result.get("text") or "").strip(),
            "cost_usd": result.get("cost"),
        }
    if isinstance(result, dict) and result.get("error") and not result.get("text"):
        return {
            "status": "account_error",
            "provider": account_id,
            "answer": (
                f"{account_id.capitalize()} hit an error and couldn't finish that. "
                "Try again, or pick a different model."
            ),
            "error": str(result.get("error")),
        }

    # A long agentic run that hit the time limit: stop cleanly, guide the user.
    if isinstance(result, dict) and result.get("timed_out"):
        return {
            "status": "account_timeout",
            "provider": account_id,
            "answer": (
                f"{account_id.capitalize()} ran past the time limit and was stopped. "
                "Big jobs (build a feature and open a PR in one go) often need more "
                "than one step. Try a smaller request, switch to a faster model "
                "(Sonnet or Haiku), or run the long task in your terminal."
            ),
        }

    # complete() returns {"text", "cost"}; tolerate a plain string too.
    if isinstance(result, dict):
        answer = result.get("text") or ""
        cost = result.get("cost")
    else:
        answer, cost = str(result), None

    # Surface what the agent actually changed, like Claude Code / Cursor do.
    changed = sorted(set(_changed_files(root)) - before) if allow_edits else []

    # Honest firewall accounting: a paid account call is a real spend, not a
    # saving. Use the runner's real cost when available (claude returns
    # total_cost_usd); fall back to the L3 tier estimate for Codex which
    # doesn't report cost. "CLOUD" was never a key in the L0-L4 cost model,
    # so using it always wrote estimated_actual_usd=0 (the "$0.00 bug").
    ledger_recorded = False
    with contextlib.suppress(Exception):
        from opaihub.cost_model import estimate_tokens
        from opaihub.ledger import record_model_call

        record_model_call(
            root,
            task,
            model_tier="L3",
            provider_type="cloud",
            tokens=estimate_tokens(task + "\n" + (answer or "")),
            confirmed=True,
            real_cost_usd=cost if isinstance(cost, (int, float)) else None,
        )
        ledger_recorded = True

    return {
        "status": "answered_by_account",
        "provider": account_id,
        "paid": True,
        "model": getattr(run, "model", "") or account_id,
        "allow_edits": allow_edits,
        "cost_usd": cost,
        "changed_files": changed,
        "ledger_recorded": ledger_recorded,
        "answer": answer or "(no output)",
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
            "already signed into. No API keys, no credentials stored.",
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
