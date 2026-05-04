from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context_manager import load_profile
from .utils import (
    append_jsonl,
    load_json,
    now_iso,
    project_op_dir,
    sha256_text,
    write_json,
)


LEVELS = {
    "L0": "deterministic local tools/scripts",
    "L1": "cache or local lightweight model",
    "L2": "cheap coding model",
    "L3": "strong coding model",
    "L4": "GPT-5.5 Max reasoning, explicit confirmation required",
}

HIGH_RISK_TERMS = [
    "architecture",
    "migration",
    "security",
    "auth",
    "payment",
    "data loss",
    "incident",
    "production database",
    "irreversible",
]

SHIP_TERMS = ["deploy", "release", "ship", "publish"]

AGENT_KEYWORDS = {
    "planning": ["plan", "feature", "build", "create", "design"],
    "code-architect": ["architecture", "system", "database", "api", "scalable"],
    "implementation": ["implement", "code", "add", "change", "create", "build"],
    "broken-code-fixer": ["fix", "error", "broken", "failing", "stack trace", "bug"],
    "testing": ["test", "pytest", "jest", "vitest", "xunit", "failing test"],
    "gitops": ["git", "commit", "branch", "pull request", "pr", "merge"],
    "code-review": ["review", "diff", "risk", "regression"],
    "refactor": ["refactor", "cleanup", "rename", "extract"],
    "documentation": ["docs", "readme", "document", "explain"],
    "security": ["security", "secret", "auth", "token", "permission", "vulnerability"],
    "performance": ["slow", "performance", "latency", "profile", "optimize"],
    "dependency": ["dependency", "upgrade", "package", "audit", "version"],
    "deployment-ci": [
        "deploy",
        "release",
        "ship",
        "ci",
        "pipeline",
        "docker",
        "vercel",
        "cloudflare",
    ],
}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def select_agents(task: str) -> list[str]:
    lowered = task.lower()
    selected = ["cost-controller", "context-manager"]
    for agent, keywords in AGENT_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            selected.append(agent)
    if len(selected) == 2:
        selected.extend(["planning", "implementation"])
    return list(dict.fromkeys(selected))


def route_task(
    task: str, root: Path | None = None, context_chars: int = 0
) -> dict[str, Any]:
    lowered = task.lower()
    token_estimate = estimate_tokens(task) + estimate_tokens("x" * context_chars)
    agents = select_agents(task)
    level = "L0"
    reasons: list[str] = []

    if any(
        word in lowered
        for word in [
            "status",
            "diff",
            "commit message",
            "branch name",
            "run test",
            "scan",
        ]
    ):
        level = "L0"
        reasons.append("deterministic local command can answer this")
    elif any(
        word in lowered for word in ["explain", "summarize", "where is", "what file"]
    ):
        level = "L1"
        reasons.append("can use cached context or a local lightweight model")
    elif any(
        word in lowered
        for word in [
            "fix",
            "bug",
            "implement",
            "add",
            "create",
            "build",
            "test",
            "refactor",
        ]
    ):
        level = "L2"
        reasons.append(
            "coding help likely needed, but start with cheap model after local evidence"
        )
    if any(word in lowered for word in HIGH_RISK_TERMS):
        level = "L3"
        reasons.append("higher-risk design or production-impacting work")
    elif any(word in lowered for word in SHIP_TERMS) and level < "L2":
        level = "L2"
        reasons.append("shipping work starts with local preflight and cheap model only")
    if any(
        phrase in lowered
        for phrase in [
            "rewrite the whole",
            "complex distributed",
            "data loss",
            "incident",
            "unknown critical",
        ]
    ):
        level = "L4"
        reasons.append("explicit high-complexity/high-risk reasoning trigger")
    if token_estimate > 50000:
        level = "L4"
        reasons.append(
            "large context estimate requires explicit expensive-model approval"
        )
    elif token_estimate > 18000 and level < "L3":
        level = "L3"
        reasons.append("large context should use stronger model only after compression")

    profile = load_profile(root) if root else {}
    if profile and profile.get("file_count", 0) > 20000 and level in {"L2", "L3"}:
        reasons.append("large repo: require context compression before model use")

    return {
        "task_hash": sha256_text(task)[:16],
        "created_at": now_iso(),
        "task": task,
        "estimated_input_tokens": token_estimate,
        "route": level,
        "route_description": LEVELS[level],
        "requires_confirmation": level == "L4",
        "agents": agents,
        "reasons": reasons or ["default local-first route"],
        "pre_ai_steps": [
            "refresh project profile if stale",
            "collect git diff/status",
            "collect failing logs or targeted files",
            "check cache for matching task hash",
            "redact secrets before any model call",
        ],
    }


def budget_report(root: Path) -> dict[str, Any]:
    op_dir = project_op_dir(root)
    budget = load_json(
        op_dir / "budget.json",
        {
            "daily_usd_limit": 0.5,
            "monthly_usd_limit": 5.0,
            "per_task_soft_limit_usd": 0.1,
            "per_task_hard_limit_usd": 0.5,
            "max_context_chars": 6000,
            "store_prompts": False,
        },
    )
    usage_path = op_dir / "logs" / "ai-usage.jsonl"
    usage = []
    if usage_path.exists():
        for line in usage_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if line.strip():
                try:
                    usage.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    total = sum(float(item.get("estimated_usd", 0)) for item in usage)
    return {
        "budget": budget,
        "usage_events": len(usage),
        "estimated_total_usd": round(total, 4),
        "remaining_monthly_usd": round(
            float(budget.get("monthly_usd_limit", 0)) - total, 4
        ),
        "policy": "local-first; L2+ cloud requires confirmation; cache before model; do not store prompts by default",
    }


def log_route(root: Path, route: dict[str, Any]) -> None:
    append_jsonl(project_op_dir(root) / "logs" / "routes.jsonl", route)
    cache_path = project_op_dir(root) / "cache" / "last-route.json"
    write_json(cache_path, route)
