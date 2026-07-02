"""`opai ask`: actually answer cheap tasks locally (open issue #13).

Pipeline: classify with the model-intelligence taxonomy, check the local result
cache (a near-duplicate task in the same repo state is free), otherwise run a
local model on a compact, redacted evidence prompt. A real cloud call is avoided
and recorded to the ledger. Cloud-tier tasks are never auto-called - they return
``confirmation_required`` unless explicitly allowed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import result_cache
from .cost_model import is_local_tier, load_cost_model
from .evidence import collect_evidence
from .local_runner import LocalRunner, detect_local_runner
from .model_intelligence import recommend_model

SYSTEM_PROMPT = (
    "You are OPai's local-first coding assistant. Answer concisely using only "
    "the provided local evidence. Do not invent files or commands."
)


def _build_prompt(project_root: Path, task: str) -> str:
    evidence = collect_evidence(project_root, task)
    git = evidence.get("git", {})
    lines = [
        f"Task: {task}",
        "",
        "Local project evidence (compact, already redacted):",
        f"- languages: {', '.join(evidence.get('languages', [])) or 'unknown'}",
        f"- markers: {', '.join(evidence.get('markers', [])) or 'none'}",
        f"- test commands: {', '.join(evidence.get('test_commands', [])) or 'none'}",
    ]
    changed = str(git.get("changed_files", {}).get("output_tail", "")).strip()
    if changed:
        lines.append("- changed files:\n" + changed[-800:])
    diff = str(git.get("diff_stat", {}).get("output_tail", "")).strip()
    if diff:
        lines.append("- diff stat:\n" + diff[-500:])
    lines.append("\nAnswer concisely.")
    return "\n".join(lines)


def _record(project_root: Path, task: str, tier: str, *, cache_hit: bool) -> None:
    from .ledger import record_route_decision

    record_route_decision(
        project_root,
        task,
        model_tier=tier,
        workflow="ask",
        cache_hit=cache_hit,
        source="ask",
    )


def run_ask(
    project_root: Path,
    task: str,
    *,
    allow_cloud: bool = False,
    runner: LocalRunner | None = None,
    record: bool = True,
    store_answer: bool = True,
    selected_model_id: str | None = None,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    recommendation = recommend_model(root, task)
    tier = str(recommendation.get("recommended_model_tier") or "L1").upper()
    model_id = str(
        selected_model_id or recommendation.get("recommended_model_id") or "local"
    )
    cost_model = load_cost_model(root)
    local = is_local_tier(tier, cost_model)

    base = {
        "task_hash": result_cache.cache_key(root, task, model_id)[:16],
        "tier": tier,
        "model_id": model_id,
    }

    # 1. Result cache: a near-duplicate in the same repo state is free.
    cached = result_cache.lookup(root, task, model_id)
    if cached is not None:
        if record:
            _record(root, task, tier, cache_hit=True)
        return {
            **base,
            "status": "cache_hit",
            "free": True,
            "source": "cache",
            "answer": cached.get("answer", ""),
        }

    # 2. Run locally if a loopback/private model is available.
    active = runner if runner is not None else detect_local_runner(root)
    if active is not None and active.available():
        prompt = _build_prompt(root, task)
        try:
            answer = active.complete(prompt, system=SYSTEM_PROMPT)
        except Exception as exc:  # noqa: BLE001 - report any runner failure cleanly
            return {**base, "status": "runner_error", "error": str(exc)}
        if store_answer:
            result_cache.store(root, task, model_id, answer)
        if record:
            _record(root, task, tier, cache_hit=False)
        return {
            **base,
            "status": "answered_locally",
            "free": True,
            "source": "local_model",
            "runner": active.name,
            "model": active.model,
            "answer": answer,
        }

    # 3. Cloud/paid tier is never auto-called.
    if not local and not allow_cloud:
        return {
            **base,
            "status": "confirmation_required",
            "reason": "Recommended tier is cloud/paid; rerun with --allow-cloud or escalate manually.",
        }

    # 4. No local model available - degrade gracefully with a setup hint.
    return {
        **base,
        "status": "no_local_model",
        "hint": "Start Ollama (`ollama serve`) or set LOCAL_MODEL_URL to a loopback endpoint, then retry.",
        "next_command": "opai models discover-local",
    }


def render_ask(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status in {"answered_locally", "cache_hit"}:
        header = f"[OPai · {result.get('source')} · {result.get('tier')} · free]"
        return f"{header}\n{result.get('answer', '')}"
    if status == "confirmation_required":
        return f"[OPai] {result.get('reason')}"
    if status == "no_local_model":
        return f"[OPai] No local model available. {result.get('hint')}\nTry: {result.get('next_command')}"
    if status == "runner_error":
        return f"[OPai] Local model error: {result.get('error')}"
    return f"[OPai] {status}"
