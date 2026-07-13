"""`opai ask`: actually answer cheap tasks locally (open issue #13).

Pipeline: classify with the model-intelligence taxonomy, check the local result
cache (a near-duplicate task in the same repo state is free), otherwise run a
local model on a compact, redacted evidence prompt. A real cloud call is avoided
and recorded to the ledger. Cloud-tier tasks are never auto-called - they return
``confirmation_required`` unless explicitly allowed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import result_cache
from .cost_model import is_local_tier, load_cost_model
from .cancellation import LocalRunCancelled
from .evidence import collect_evidence
from .local_runner import LocalRunner, detect_local_runner
from .model_intelligence import recommend_model

SYSTEM_PROMPT = (
    "You are OPai's local-first coding assistant. Reply directly to the user's "
    "message. The project context below is background you may use ONLY when it "
    "is relevant to what they asked. For a greeting, small talk, or a general "
    "question, respond naturally and briefly and do NOT bring up the project, "
    "its files, or how to run its tests. Never invent files or commands."
)


def _build_prompt(project_root: Path, task: str) -> str:
    evidence = collect_evidence(project_root, task)
    git = evidence.get("git", {})
    lines = [
        f"User message: {task}",
        "",
        "Project context (reference only if it is relevant to the message above; "
        "otherwise ignore it entirely):",
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
    lines.append("\nNow respond to the user message.")
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


def _record_cache_lookup(project_root: Path, task: str, lookup: Any) -> None:
    from .ledger import record_cache_lookup

    record_cache_lookup(
        project_root,
        task,
        cache_kind="result",
        outcome=str(lookup.outcome),
        reason=lookup.reason,
        age_seconds=lookup.age_seconds,
        avoided_model_call=lookup.outcome == "hit",
    )


def _cache_metadata(lookup: Any) -> dict[str, Any]:
    return {
        "outcome": str(lookup.outcome),
        "reason": lookup.reason,
        "age_seconds": lookup.age_seconds,
    }


def run_ask(
    project_root: Path,
    task: str,
    *,
    allow_cloud: bool = False,
    allow_edits: bool = False,
    runner: LocalRunner | None = None,
    record: bool = True,
    store_answer: bool = True,
    selected_model_id: str | None = None,
    cancel: Any = None,
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
        "task_hash": result_cache.task_hash(task, model_id),
        "tier": tier,
        "model_id": model_id,
    }

    # 1. Result cache: a near-duplicate in the same repo state is free, but a
    # cache hit cannot prove a requested repository mutation happened.
    if not allow_edits:
        cache_lookup = result_cache.lookup_with_meta(root, task, model_id)
        if record:
            _record_cache_lookup(root, task, cache_lookup)
        if cache_lookup.entry is not None:
            if record:
                _record(root, task, tier, cache_hit=True)
            return {
                **base,
                "status": "cache_hit",
                "free": True,
                "source": "cache",
                "answer": cache_lookup.entry.get("answer", ""),
                "cache": _cache_metadata(cache_lookup),
            }

    # 2. Run locally if a loopback/private model is available.
    active = runner
    active_checked = runner is not None
    if not active_checked:
        active = detect_local_runner(root)
    active_available = active is not None and active.available()

    if allow_edits and active_available:
        return {
            **base,
            "status": "capability_mismatch",
            "capability": "edit_files",
            "provider": str(getattr(active, "name", "local")),
            "reason": (
                "The selected local runner can answer, but OPai has no bounded "
                "repository-tool adapter for it yet."
            ),
            "hint": (
                "Switch to Ask or Plan, or choose a provider with bounded "
                "repository tools for edits."
            ),
        }

    if active_available:
        if cancel is not None and cancel.is_set():
            return {**base, "status": "cancelled", "answer": ""}
        prompt = _build_prompt(root, task)
        try:
            try:
                # True mid-flight cancel (#107): the runner closes its HTTP
                # connection when the cancel Event fires.
                answer = active.complete(prompt, system=SYSTEM_PROMPT, cancel=cancel)
            except TypeError as exc:
                if "cancel" not in str(exc):
                    raise
                # Runner without cancel support (e.g. a test fake): run
                # blocking; Stop still works via the stale-response guard.
                # Real local and free-tier (#152) runners are cancellable.
                answer = active.complete(prompt, system=SYSTEM_PROMPT)
        except LocalRunCancelled:
            return {**base, "status": "cancelled", "answer": ""}
        except Exception as exc:  # noqa: BLE001 - report any runner failure cleanly
            return {**base, "status": "runner_error", "error": str(exc)}
        if store_answer:
            result_cache.store(
                root,
                task,
                model_id,
                answer,
                expected_key=cache_lookup.key if not allow_edits else None,
            )
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
            "cache": _cache_metadata(cache_lookup)
            if not allow_edits
            else {"outcome": "skipped", "reason": "edit_request", "age_seconds": None},
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


def run_explicit_model(
    project_root: Path,
    task: str,
    *,
    runner: LocalRunner,
    selected_model_id: str,
    allow_edits: bool = False,
    mode: str = "ask",
    record: bool = True,
    cancel: Any = None,
) -> dict[str, Any]:
    """Run an explicitly selected model without Auto routing or prose caching."""

    root = project_root.expanduser().resolve()
    base = {
        "task_hash": hashlib.sha256(
            (selected_model_id + "\0" + task).encode("utf-8")
        ).hexdigest()[:16],
        "tier": "L2",
        "model_id": selected_model_id,
    }
    if cancel is not None and cancel.is_set():
        return {**base, "status": "cancelled", "answer": ""}
    try:
        if allow_edits:
            complete_with_tools = getattr(runner, "complete_with_tools", None)
            if not callable(complete_with_tools):
                return {
                    **base,
                    "status": "capability_mismatch",
                    "error": "The selected provider cannot edit repository files.",
                }
            completed = complete_with_tools(
                task,
                project_root=root,
                allow_edits=True,
                system=SYSTEM_PROMPT,
                cancel=cancel,
            )
            answer = str(completed.get("text") or "")
            tool_trace = list(completed.get("tool_trace") or [])
            stopped_reason = str(completed.get("stopped_reason") or "")
            last_error = str(completed.get("last_error") or "")
        else:
            try:
                answer = runner.complete(task, system=SYSTEM_PROMPT, cancel=cancel)
            except TypeError as exc:
                if "cancel" not in str(exc):
                    raise
                answer = runner.complete(task, system=SYSTEM_PROMPT)
            tool_trace = []
            stopped_reason = ""
            last_error = ""
    except LocalRunCancelled:
        return {**base, "status": "cancelled", "answer": ""}
    except Exception as exc:  # noqa: BLE001 - normalize provider failures upstream
        return {**base, "status": "runner_error", "error": str(exc)}
    if record:
        _record(root, task, "L2", cache_hit=False)
    return {
        **base,
        "status": "answered_locally",
        "free": True,
        "source": "explicit_model",
        "runner": runner.name,
        "model": runner.model,
        "mode": mode,
        "answer": answer,
        "tool_trace": tool_trace,
        # Typed terminal for a run the tool loop could not finish (#311): empty
        # on a clean completion, else "tool_budget_exhausted"/"repeated_failure".
        # Callers can detect a stuck run without a new status to special-case.
        "stopped_reason": stopped_reason,
        "last_error": last_error,
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
