from __future__ import annotations

from pathlib import Path
from typing import Any

from .cache import write_artifact
from .context_manager import build_context, load_profile
from .cost import route_task
from .models import build_model_bundle
from .utils import now_iso, project_op_dir, write_json


AGENT_ACTIONS = {
    "cost-controller": [
        "estimate token budget",
        "check route level",
        "block L4 unless confirmed",
    ],
    "context-manager": ["refresh context if stale", "load diff-first project evidence"],
    "planning": ["produce target files, steps, tests, risks"],
    "code-architect": [
        "compare against existing patterns",
        "flag API/data-model risks",
    ],
    "implementation": ["prepare minimal patch strategy"],
    "broken-code-fixer": [
        "reproduce failure",
        "parse stack/log clues",
        "suggest minimal patch",
    ],
    "testing": ["detect targeted tests", "run failing tests before full suite"],
    "gitops": ["summarize status", "scan secrets", "draft commit/PR text"],
    "code-review": ["check risky diffs", "flag missing tests"],
    "refactor": ["verify behavior-preserving scope", "require tests before edits"],
    "documentation": ["derive docs from diff and context"],
    "security": ["run secret/dependency/config scans first"],
    "performance": ["require benchmark/profile evidence"],
    "dependency": ["audit manifests and lockfiles"],
    "deployment-ci": ["parse CI/deploy logs and draft rollback plan"],
}


def run_agent_batch(
    root: Path,
    task: str,
    execute_model: bool = False,
    confirm_expensive: bool = False,
) -> dict[str, Any]:
    profile = load_profile(root)
    context = build_context(root, profile)
    route = route_task(task, root=root, context_chars=len(context))
    agents = route["agents"]
    bundle = build_model_bundle(
        root, task, execute=execute_model, confirm_expensive=confirm_expensive
    )
    result = {
        "created_at": now_iso(),
        "task": task,
        "route": route,
        "shared_context_chars": len(context),
        "batching": "single shared context prepared for all agents",
        "agents": [
            {
                "name": agent,
                "actions": AGENT_ACTIONS.get(
                    agent, ["use project profile and local evidence"]
                ),
                "model_prompt_shared": True,
            }
            for agent in agents
        ],
        "model_bundle_status": bundle.get("status"),
        "model_cache_hit": bundle.get("cache_hit", False),
    }
    op_dir = project_op_dir(root)
    write_json(op_dir / "cache" / "last-agent-run.json", result)
    artifact = write_artifact(root, "agent-run.md", render_agent_run(result))
    result["artifact"] = str(artifact)
    return result


def render_agent_run(result: dict[str, Any]) -> str:
    lines = [
        "# OPcoding Agent Run",
        "",
        f"Task: {result['task']}",
        f"Route: {result['route']['route']} ({result['route']['route_description']})",
        f"Batching: {result['batching']}",
        "",
        "## Agents",
    ]
    for agent in result["agents"]:
        lines.append(f"### {agent['name']}")
        lines.extend(f"- {action}" for action in agent["actions"])
    lines.extend(["", "## Pre-AI Steps"])
    lines.extend(f"- {step}" for step in result["route"]["pre_ai_steps"])
    return "\n".join(lines) + "\n"
