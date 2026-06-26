from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .command_runner import redact
from .evidence import collect_evidence


MAX_ROUTE_TAIL_CHARS = 360
MAX_CHANGED_FILES = 50
MAX_DIFF_STAT_LINES = 20
MAX_COMPACT_ROUTE_CHARS = 8_000


def _has_any(text: str, terms: list[str]) -> bool:
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    for term in terms:
        term = term.lower()
        if " " in term:
            if term in text:
                return True
        elif term in tokens:
            return True
    return False


def _cap_lines(text: str, max_lines: int) -> str:
    """Keep the first max_lines non-empty lines; append omission notice if trimmed."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    omitted = len(lines) - max_lines
    return "\n".join(lines[:max_lines]) + f"\n[+{omitted} more lines omitted]"


def _compact_command(
    result: dict[str, Any], max_lines: int | None = None
) -> dict[str, Any]:
    raw = str(result.get("output_tail", ""))
    if max_lines is not None:
        tail = redact(_cap_lines(raw, max_lines))
    else:
        tail = redact(raw[-MAX_ROUTE_TAIL_CHARS:])
    return {
        "returncode": result.get("returncode"),
        "executed": result.get("executed", False),
        "policy": result.get("policy"),
        "output_tail": tail,
    }


def _compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    git = evidence.get("git", {})
    return {
        "cache_key": evidence.get("cache_key"),
        "ai_used": evidence.get("ai_used", False),
        "markers": evidence.get("markers", []),
        "languages": evidence.get("languages", []),
        "test_commands": evidence.get("test_commands", []),
        "registry_counts": evidence.get("registry_counts", {}),
        "git": {
            "is_repo": git.get("is_repo", False),
            "status": _compact_command(git.get("status", {})),
            "changed_files": _compact_command(
                git.get("changed_files", {}), max_lines=MAX_CHANGED_FILES
            ),
            "diff_stat": _compact_command(
                git.get("diff_stat", {}), max_lines=MAX_DIFF_STAT_LINES
            ),
        },
    }


def route_task(
    project_root: Path,
    task: str,
    include_evidence: bool = False,
    use_cache: bool = True,
    persist_cache: bool = False,
) -> dict[str, Any]:
    # Reuse a cached evidence pack when the repo is unchanged. Reads are
    # side-effect free; only persist_cache writes (keeps route read-only, #12).
    if use_cache:
        from .evidence_cache import collect_evidence_cached

        evidence, cache_meta = collect_evidence_cached(
            project_root, task, write=persist_cache
        )
    else:
        evidence = collect_evidence(project_root, task)
        cache_meta = {"cache_hit": False, "cache_key": evidence.get("cache_key")}
    lowered = task.lower()

    workflow = "feature_plan"
    model_tier = "L1"
    requires_confirmation = False
    next_actions = [
        "reuse evidence pack",
        "load changed files before broad context",
        "check cache before model call",
    ]
    safety_gates = [
        "redact secrets before model use",
        "ask before expensive model",
        "ask before destructive command",
    ]

    if _has_any(lowered, ["status", "diff", "commit", "branch", "pr"]):
        workflow = "pull_request_review"
        model_tier = "L0"
        next_actions = [
            "summarize git diff/status",
            "scan for secrets",
            "draft local git output",
        ]
    elif _has_any(
        lowered, ["test", "tests", "failing", "failure", "traceback", "error"]
    ):
        workflow = "test_failure_debug"
        model_tier = "L0"
        next_actions = [
            "run targeted tests first",
            "collect failing logs",
            "inspect changed files",
            "escalate only after local failure evidence",
        ]
    elif _has_any(lowered, ["security", "secret", "auth", "vulnerability"]):
        workflow = "security_audit"
        model_tier = "L0"
        requires_confirmation = True
        next_actions = [
            "run secret scan",
            "run dependency audit",
            "run static security checks",
            "summarize local findings before any model review",
        ]
    elif _has_any(lowered, ["deploy", "release", "ship", "production", "publish"]):
        workflow = "release_prepare"
        model_tier = "L0"
        requires_confirmation = True
        next_actions = [
            "run release preflight locally",
            "run targeted tests and security checks",
            "prepare rollback plan",
            "confirm before any cloud, deploy, or strong-model action",
        ]
        safety_gates.append("ask before deploy/cloud action")
    elif _has_any(lowered, ["fix", "bug", "implement", "add", "build", "refactor"]):
        workflow = "bug_fix" if "fix" in lowered or "bug" in lowered else "feature_plan"
        model_tier = "L1"
        next_actions.extend(
            ["build minimal context pack", "prefer cheap/local model for first pass"]
        )

    # Gate the chosen tier against the effective policy profile (issues #37/#16).
    from .cost_model import load_cost_model, tier_cost
    from .policy import evaluate_action

    cost_model = load_cost_model(project_root)
    task_tokens = int(cost_model.get("default_task_tokens", 6000))
    provider_type = "local" if model_tier in ("L0", "L1") else "cloud"
    estimated_cost = tier_cost(model_tier, task_tokens, cost_model)
    gate = evaluate_action(
        project_root,
        tier=model_tier,
        provider_type=provider_type,
        cost_usd=estimated_cost,
        estimated_tokens=task_tokens,
        paid=provider_type == "cloud",
    )
    requires_confirmation = (
        requires_confirmation or gate["requires_confirmation"] or gate["denied"]
    )

    decision = {
        "task": task,
        "workflow": workflow,
        "model_tier": model_tier,
        "requires_confirmation": requires_confirmation,
        "next_actions": next_actions,
        "safety_gates": safety_gates,
        "evidence_cache_key": evidence["cache_key"],
        "evidence_cache_hit": cache_meta.get("cache_hit", False),
        "evidence_summary": _compact_evidence(evidence),
        "policy_profile": gate["profile"],
        "policy_decision": gate["decision"],
        "policy_reasons": gate["reasons"],
        "estimated_cost_usd": estimated_cost,
        "policy": "local evidence first; no cloud or expensive model without escalation gate",
    }
    if include_evidence:
        decision["evidence"] = evidence
    return decision


def route_context_sizes(decision_full: dict[str, Any]) -> dict[str, int]:
    """Measure full vs compact route payload size in characters.

    This is the real, observable compaction OPai applies to AI-facing output:
    full evidence versus the compact summary an agent actually consumes.
    """
    full_chars = len(json.dumps(decision_full, sort_keys=True, default=str))
    compact_chars = len(
        json.dumps(compact_decision(decision_full), sort_keys=True, default=str)
    )
    return {"full_chars": full_chars, "compact_chars": compact_chars}


def compact_decision(decision: dict[str, Any]) -> dict[str, Any]:
    evidence = decision.get("evidence") or decision.get("evidence_summary", {})
    git = evidence.get("git", {})
    changed = git.get("changed_files", {}).get("output_tail", "")
    changed_count = len([line for line in changed.splitlines() if line.strip()])
    return {
        "output": "compact",
        "workflow": decision.get("workflow"),
        "tier": decision.get("model_tier"),
        "confirm": bool(decision.get("requires_confirmation")),
        "cache_key": decision.get("evidence_cache_key"),
        "local": {
            "markers": len(evidence.get("markers", [])),
            "tests": len(evidence.get("test_commands", [])),
            "changed": changed_count,
        },
        "hint": "full evidence: --full-evidence",
    }
