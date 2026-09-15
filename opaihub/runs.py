"""Route-run history and the `vesta why` explainer (power-efficiency roadmap Phase 2).

Persists compact, privacy-safe route decisions under `.opaihub/runs/` and turns a
routing decision into a plain explanation of *why* Vesta chose its path. Building
an explanation is read-only; only `record_run` writes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_io import read_utf8_tail_json_objects
from .cost_model import load_cost_model, tier_cost
from .ledger import task_fingerprint
from .router import route_task
from .state import state_dir


TIER_MEANING = {
    "L0": "deterministic local tools / cached context (free)",
    "L1": "local small model (free)",
    "L2": "confirmed cheap cloud model",
    "L3": "strong frontier model",
    "L4": "max frontier model (gated)",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def runs_path(project_root: Path) -> Path:
    return state_dir(project_root) / "runs" / "history.jsonl"


def record_run(project_root: Path, decision: dict[str, Any]) -> dict[str, Any]:
    """Append a compact, privacy-safe run record. No raw task text is stored."""
    root = project_root.expanduser().resolve()
    record = {
        "created_at": _now_iso(),
        "task_hash": task_fingerprint(decision.get("task", "")),
        "workflow": decision.get("workflow"),
        "model_tier": decision.get("model_tier"),
        "policy_profile": decision.get("policy_profile"),
        "policy_decision": decision.get("policy_decision"),
        "cache_hit": decision.get("evidence_cache_hit", False),
        "estimated_cost_usd": decision.get("estimated_cost_usd", 0.0),
    }
    path = runs_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def recent_runs(project_root: Path, limit: int = 10) -> list[dict[str, Any]]:
    path = runs_path(project_root.expanduser().resolve())
    if not path.exists():
        return []
    return read_utf8_tail_json_objects(path, limit)


def explain_route(project_root: Path, task: str) -> dict[str, Any]:
    """Explain why Vesta chose its route for a task. Read-only."""
    root = project_root.expanduser().resolve()
    decision = route_task(root, task, include_evidence=False)
    cost_model = load_cost_model(root)
    tier = decision["model_tier"]
    baseline_tier = cost_model.get("baseline_tier", "L3")
    task_tokens = int(cost_model.get("default_task_tokens", 6000))
    baseline_cost = tier_cost(baseline_tier, task_tokens, cost_model)
    chosen_cost = tier_cost(tier, task_tokens, cost_model)

    reasons = [
        f"Classified as '{decision['workflow']}' from the task wording.",
        f"Chose tier {tier}: {TIER_MEANING.get(tier, tier)}.",
        f"Policy profile '{decision['policy_profile']}' returned '{decision['policy_decision']}'.",
    ]
    reasons.extend(decision.get("policy_reasons", []))
    if decision.get("evidence_cache_hit"):
        reasons.append("Reused a cached evidence pack (no repo re-scan needed).")
    if decision.get("requires_confirmation"):
        reasons.append(
            "Confirmation is required before escalating to a paid/cloud model."
        )

    return {
        "report": "opai-why",
        "task_hash": task_fingerprint(task),
        "workflow": decision["workflow"],
        "model_tier": tier,
        "tier_meaning": TIER_MEANING.get(tier, tier),
        "policy_profile": decision["policy_profile"],
        "policy_decision": decision["policy_decision"],
        "requires_confirmation": decision["requires_confirmation"],
        "estimated_cost_usd": chosen_cost,
        "estimated_baseline_usd": baseline_cost,
        "estimated_savings_usd": round(max(0.0, baseline_cost - chosen_cost), 6),
        "cache_hit": decision.get("evidence_cache_hit", False),
        "reasons": reasons,
        "next_actions": decision.get("next_actions", []),
        "safety_gates": decision.get("safety_gates", []),
    }


def render_why_markdown(explanation: dict[str, Any]) -> str:
    lines = [
        "# Vesta - Why this route",
        "",
        f"- **Workflow:** {explanation['workflow']}",
        f"- **Tier:** {explanation['model_tier']} - {explanation['tier_meaning']}",
        f"- **Policy:** {explanation['policy_profile']} -> {explanation['policy_decision']}",
        f"- **Estimated cost:** ${explanation['estimated_cost_usd']:.4f} "
        f"(baseline ${explanation['estimated_baseline_usd']:.4f}, "
        f"saves ${explanation['estimated_savings_usd']:.4f})",
        "",
        "## Reasons",
        "",
    ]
    lines.extend(f"- {reason}" for reason in explanation["reasons"])
    return "\n".join(lines)
