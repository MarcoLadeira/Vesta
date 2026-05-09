from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .evidence import collect_evidence


MAX_ROUTE_TAIL_CHARS = 360


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


def _compact_command(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "returncode": result.get("returncode"),
        "executed": result.get("executed", False),
        "policy": result.get("policy"),
        "output_tail": str(result.get("output_tail", ""))[-MAX_ROUTE_TAIL_CHARS:],
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
            "changed_files": _compact_command(git.get("changed_files", {})),
            "diff_stat": _compact_command(git.get("diff_stat", {})),
        },
    }


def route_task(
    project_root: Path, task: str, include_evidence: bool = False
) -> dict[str, Any]:
    evidence = collect_evidence(project_root, task)
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

    decision = {
        "task": task,
        "workflow": workflow,
        "model_tier": model_tier,
        "requires_confirmation": requires_confirmation,
        "next_actions": next_actions,
        "safety_gates": safety_gates,
        "evidence_cache_key": evidence["cache_key"],
        "evidence_summary": _compact_evidence(evidence),
        "policy": "local evidence first; no cloud or expensive model without escalation gate",
    }
    if include_evidence:
        decision["evidence"] = evidence
    return decision


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
