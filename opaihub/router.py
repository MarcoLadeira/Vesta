from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .evidence import collect_evidence


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


def route_task(project_root: Path, task: str) -> dict[str, Any]:
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
        model_tier = "L2"
        next_actions = [
            "run secret scan",
            "run dependency audit",
            "run static security checks",
            "summarize findings before model review",
        ]
    elif _has_any(lowered, ["deploy", "release", "ship", "production", "publish"]):
        workflow = "release_prepare"
        model_tier = "L3"
        requires_confirmation = True
        next_actions = [
            "run release preflight",
            "run tests and security checks",
            "prepare rollback plan",
            "confirm before any cloud/deploy action",
        ]
        safety_gates.append("ask before deploy/cloud action")
    elif _has_any(lowered, ["fix", "bug", "implement", "add", "build", "refactor"]):
        workflow = "bug_fix" if "fix" in lowered or "bug" in lowered else "feature_plan"
        model_tier = "L1"
        next_actions.extend(
            ["build minimal context pack", "prefer cheap/local model for first pass"]
        )

    return {
        "task": task,
        "workflow": workflow,
        "model_tier": model_tier,
        "requires_confirmation": requires_confirmation,
        "next_actions": next_actions,
        "safety_gates": safety_gates,
        "evidence_cache_key": evidence["cache_key"],
        "evidence": evidence,
        "policy": "local evidence first; no cloud or expensive model without escalation gate",
    }
