from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .cost_model import estimate_route_savings, estimate_tokens, load_cost_model
from .gui_preferences import DEFAULT_MODE, load_gui_preferences
from .intent_router import route_intents, safety_warnings
from .ledger import record_event, record_route_decision, read_events
from .model_intelligence import recommend_model


def _mode_label(mode: str) -> str:
    return {
        "ask": "Ask",
        "plan": "Plan",
        "safe-auto": "Safe Auto",
        "approve-edits": "Approve Edits",
        "full-auto": "Full Auto",
    }.get(mode, "Safe Auto")


def build_savings_receipt(
    project_root: Path,
    *,
    task: str,
    selected_model: str,
    selected_mode: str,
    chosen_tier: str,
    actual_cost_usd: float | None = None,
    context_tokens_saved: int = 0,
    confidence: str = "estimated",
) -> dict[str, Any]:
    cost_model = load_cost_model(project_root)
    tokens = estimate_tokens(task, cost_model) or int(
        cost_model.get("default_task_tokens", 6000)
    )
    route = estimate_route_savings(chosen_tier, task_tokens=tokens, model=cost_model)
    actual = (
        route["estimated_actual_usd"]
        if actual_cost_usd is None
        else float(actual_cost_usd)
    )
    savings = max(0.0, round(route["estimated_baseline_usd"] - actual, 6))
    return {
        "session_id": uuid.uuid4().hex[:12],
        "message_id": uuid.uuid4().hex[:12],
        "selected_model": selected_model,
        "selected_mode": selected_mode,
        "mode_label": _mode_label(selected_mode),
        "baseline_tier": route["baseline_tier"],
        "chosen_tier": str(chosen_tier).upper(),
        "estimated_tokens": tokens,
        "estimated_baseline_usd": route["estimated_baseline_usd"],
        "estimated_actual_usd": round(actual, 6),
        "estimated_savings_usd": savings,
        "paid_call_avoided": bool(
            route["cloud_call_avoided"] and actual <= route["estimated_actual_usd"]
        ),
        "context_tokens_saved": int(context_tokens_saved),
        "confidence": confidence,
        "privacy": "Raw prompts are not stored; receipts use task hashes and estimates.",
    }


def last_savings_receipt(project_root: Path) -> dict[str, Any] | None:
    for event in reversed(read_events(project_root, limit=50)):
        receipt = event.get("receipt")
        if isinstance(receipt, dict):
            return receipt
    return None


def _record_gui_route(
    project_root: Path,
    task: str,
    *,
    tier: str,
    receipt: dict[str, Any],
    tool_trace: list[dict[str, Any]],
    model_id: str,
    mode: str,
) -> None:
    event = record_route_decision(
        project_root,
        task,
        model_tier=tier,
        workflow="gui_message",
        task_tokens=receipt["estimated_tokens"],
        agent="opai-gui",
        repo=project_root.resolve().name,
        source="gui",
    )
    event["receipt"] = receipt
    event["tool_trace"] = [
        {"id": item.get("id"), "label": item.get("label")} for item in tool_trace
    ]
    event["selected_model"] = model_id
    event["selected_mode"] = mode
    record_event(
        project_root,
        "gui_receipt",
        task=task,
        receipt=receipt,
        selected_model=model_id,
        selected_mode=mode,
        tool_count=len(tool_trace),
    )


def handle_gui_message(
    project_root: Path,
    message: str,
    *,
    model_id: str | None = None,
    mode: str | None = None,
    account_runner: Any = None,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    prefs = load_gui_preferences(root)
    selected_model = model_id or prefs.get("default_model") or "auto"
    selected_mode = mode or prefs.get("default_mode") or DEFAULT_MODE
    tool_trace = route_intents(root, message, mode=selected_mode)
    warnings = safety_warnings(root, message, mode=selected_mode)
    rec = recommend_model(root, message)
    tier = str(rec.get("recommended_model_tier") or "L1").upper()

    if warnings and selected_mode != "full-auto":
        receipt = build_savings_receipt(
            root,
            task=message,
            selected_model=selected_model,
            selected_mode=selected_mode,
            chosen_tier=tier,
            confidence="blocked",
        )
        record_event(
            root,
            "gui_blocked",
            task=message,
            receipt=receipt,
            selected_model=selected_model,
            selected_mode=selected_mode,
        )
        reason = warnings[0].get("reason", "") if warnings else ""
        return {
            "status": "blocked",
            "answer": (
                "Safe Auto stopped this before running it because it looks risky"
                + (f": {reason}" if reason else ".")
                + "\nSwitch the mode to Full Auto only if you intend that."
            ),
            "tool_trace": tool_trace,
            "receipt": receipt,
            "changed_files": [],
            "warnings": warnings,
            "next_actions": ["Switch to Full Auto only if this is intentional."],
        }

    # Plan / Ask / Approve-Edits are read-only; Safe Auto / Full Auto may edit.
    # The selected model always actually answers - no canned template.
    allow_edits = selected_mode in {"safe-auto", "full-auto"}

    if selected_model.startswith("account:"):
        from opai import app_state as A

        result = A.ask(
            root,
            message,
            selected_model,
            allow_edits=allow_edits,
            account_runner=account_runner,
            mode=selected_mode,
        )
        actual = result.get("cost_usd")
        receipt = build_savings_receipt(
            root,
            task=message,
            selected_model=selected_model,
            selected_mode=selected_mode,
            chosen_tier="L3",
            actual_cost_usd=actual if isinstance(actual, (int, float)) else None,
            confidence="actual" if isinstance(actual, (int, float)) else "estimated",
        )
        record_event(
            root,
            "gui_receipt",
            task=message,
            receipt=receipt,
            selected_model=selected_model,
            selected_mode=selected_mode,
            tool_count=len(tool_trace),
        )
        status = (
            "answered"
            if result.get("status") == "answered_by_account"
            else result.get("status", "error")
        )
        return {
            "status": status,
            "answer": result.get("answer")
            or result.get("hint")
            or result.get("error")
            or "",
            "tool_trace": tool_trace,
            "receipt": receipt,
            "changed_files": result.get("changed_files", []),
            "warnings": [],
            "next_actions": ["Review changed files before committing."],
            "raw_result": result,
        }

    from .ask import run_ask

    receipt = build_savings_receipt(
        root,
        task=message,
        selected_model=selected_model,
        selected_mode=selected_mode,
        chosen_tier=tier,
        confidence="estimated",
    )
    _record_gui_route(
        root,
        message,
        tier=tier,
        receipt=receipt,
        tool_trace=tool_trace,
        model_id=selected_model,
        mode=selected_mode,
    )
    result = run_ask(root, message, record=False)
    status_map = {
        "answered_locally": "answered",
        "cache_hit": "answered",
        "no_local_model": "needs_model",
        "confirmation_required": "needs_confirmation",
    }
    answer = result.get("answer") or result.get("hint") or result.get("reason") or ""
    if result.get("status") == "no_local_model":
        answer = (
            "Auto has no free model to run this. Pick your Claude or Codex account "
            "in the model menu to answer it, or connect a local model under Advanced."
        )
    elif result.get("status") == "confirmation_required":
        answer = (
            "This needs a paid model. Pick your Claude or Codex account in the model "
            "menu to run it — OPai won't spend on a paid call automatically."
        )
    return {
        "status": status_map.get(result.get("status"), result.get("status", "error")),
        "answer": answer,
        "tool_trace": tool_trace,
        "receipt": receipt,
        "changed_files": [],
        "warnings": [],
        "next_actions": [result.get("next_command") or "Review the savings receipt."],
        "raw_result": result,
    }
