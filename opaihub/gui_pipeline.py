from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cost_model import estimate_route_savings, estimate_tokens, load_cost_model
from .autonomy import evaluate_action, resolve_mode
from .checkpoints import create_checkpoint
from .gui_preferences import DEFAULT_MODE, load_gui_preferences
from .intent_router import route_intents, safety_warnings
from .ledger import record_event, record_route_decision, read_events, task_fingerprint
from .model_intelligence import recommend_model
from .run_state import finish_run, start_run


def _mode_label(mode: str) -> str:
    return {
        "ask": "Ask",
        "plan": "Plan",
        "safe-auto": "Safe Auto",
        "approve-edits": "Approve Edits",
        "full-auto": "Full Auto",
    }.get(mode, "Safe Auto")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _provider_from_model(model_id: str) -> str:
    if model_id.startswith("account:"):
        parts = model_id.split(":")
        return parts[1] if len(parts) > 1 and parts[1] else "account"
    if model_id == "auto":
        return "opai"
    if model_id.startswith("local:"):
        return "local"
    return (model_id.split(":", 1)[0] or "opai").lower()


def _is_paid_account_model(model_id: str) -> bool:
    return model_id.startswith("account:")


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
    route_kind: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    cost_model = load_cost_model(project_root)
    tokens = estimate_tokens(task, cost_model) or int(
        cost_model.get("default_task_tokens", 6000)
    )
    route = estimate_route_savings(chosen_tier, task_tokens=tokens, model=cost_model)
    blocked = confidence == "blocked"
    paid = _is_paid_account_model(selected_model) and not blocked
    provider = _provider_from_model(selected_model)
    route_label = route_kind or (
        "blocked"
        if blocked
        else (
            "paid_account"
            if paid
            else (
                "local_or_deterministic"
                if route["cloud_call_avoided"]
                else "cloud_or_baseline"
            )
        )
    )

    if blocked:
        actual = 0.0
        savings = 0.0
        spend_confidence = "blocked"
        savings_confidence = "not_applicable"
        paid_call_avoided = False
        receipt_reason = reason or "blocked before any paid or local run"
    elif paid:
        actual = (
            route["estimated_actual_usd"]
            if actual_cost_usd is None
            else max(0.0, float(actual_cost_usd))
        )
        savings = 0.0
        spend_confidence = "actual" if actual_cost_usd is not None else "estimated"
        savings_confidence = "not_applicable"
        paid_call_avoided = False
        receipt_reason = reason or "selected paid account model"
    else:
        actual = (
            route["estimated_actual_usd"]
            if actual_cost_usd is None
            else max(0.0, float(actual_cost_usd))
        )
        savings = max(0.0, round(route["estimated_baseline_usd"] - actual, 6))
        spend_confidence = "actual" if actual_cost_usd is not None else "estimated"
        savings_confidence = confidence if savings else "not_applicable"
        paid_call_avoided = bool(route["cloud_call_avoided"] and savings > 0)
        receipt_reason = reason or (
            "local or deterministic route avoided a paid baseline call"
            if paid_call_avoided
            else "chosen route did not create a money-saving claim"
        )

    actual = round(actual, 6)
    savings = round(savings, 6)
    receipt_id = uuid.uuid4().hex[:12]
    return {
        "receipt_id": receipt_id,
        "created_at": _now_iso(),
        "task_hash": task_fingerprint(task),
        "provider": provider,
        "route_kind": route_label,
        "paid": paid,
        "spend_usd": actual,
        "spend_confidence": spend_confidence,
        "savings_usd": savings,
        "savings_confidence": savings_confidence,
        "reason": receipt_reason,
        "session_id": uuid.uuid4().hex[:12],
        "message_id": receipt_id,
        "selected_model": selected_model,
        "selected_mode": selected_mode,
        "mode_label": _mode_label(selected_mode),
        "baseline_tier": route["baseline_tier"],
        "chosen_tier": str(chosen_tier).upper(),
        "estimated_tokens": tokens,
        "estimated_baseline_usd": route["estimated_baseline_usd"],
        "estimated_actual_usd": actual,
        "estimated_savings_usd": savings,
        "paid_call_avoided": paid_call_avoided,
        "context_tokens_saved": int(context_tokens_saved),
        "confidence": confidence,
        "privacy": (
            "Raw prompts are not stored; receipts use one-way task hashes, "
            "spend/savings numbers, and local metadata only."
        ),
    }


def format_savings_receipt(receipt: dict[str, Any]) -> str:
    """Human receipt text for GUI/dashboard proof surfaces."""
    if not receipt:
        return "Savings receipt\nNo receipt recorded yet."
    spend = float(receipt.get("spend_usd") or receipt.get("estimated_actual_usd") or 0)
    savings = float(
        receipt.get("savings_usd") or receipt.get("estimated_savings_usd") or 0
    )
    spend_confidence = receipt.get("spend_confidence") or receipt.get("confidence")
    savings_confidence = receipt.get("savings_confidence") or receipt.get("confidence")
    lines = [
        "Savings receipt",
        f"Spent: ${spend:.4f} ({spend_confidence})",
        f"Saved: ${savings:.4f} ({savings_confidence})",
        f"Route: {receipt.get('route_kind', 'unknown')}",
        f"Reason: {receipt.get('reason', 'no money-saving claim recorded')}",
    ]
    if receipt.get("paid_call_avoided"):
        lines.append("Paid call avoided: yes")
    else:
        lines.append("Paid call avoided: no")
    if receipt.get("receipt_id"):
        lines.append(f"Receipt: {receipt['receipt_id']}")
    return "\n".join(lines)


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
    cancel_event: Any = None,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    prefs = load_gui_preferences(root)
    selected_model = model_id or prefs.get("default_model") or "auto"
    mode_info = resolve_mode(root, requested_mode=mode)
    if mode is None and not mode_info["requested_mode"]:
        mode_info = resolve_mode(
            root, requested_mode=prefs.get("default_mode") or DEFAULT_MODE
        )
    requested_mode = mode_info["requested_mode"]
    selected_mode = mode_info["effective_mode"]
    tool_trace = route_intents(root, message, mode=selected_mode)
    warnings = safety_warnings(root, message, mode=selected_mode)
    if mode_info.get("warning"):
        warnings.append({"reason": mode_info["warning"], "severity": "warning"})
    blocking_warnings = [w for w in warnings if w.get("severity") != "warning"]
    rec = recommend_model(root, message)
    tier = str(rec.get("recommended_model_tier") or "L1").upper()
    allow_edits = selected_mode in {"safe-auto", "full-auto"}
    autonomy_decision = evaluate_action(
        root,
        selected_mode,
        message=message,
        model_id=selected_model,
        mutates=allow_edits,
    )
    run = start_run(
        root,
        message,
        mode=selected_mode,
        model_id=selected_model,
        decision=autonomy_decision,
    )
    run_id = run["id"]

    if autonomy_decision["blocked"] or (
        blocking_warnings and selected_mode != "full-auto"
    ):
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
        finish_run(
            root,
            run_id,
            status="blocked",
            receipt_id=receipt["message_id"],
            next_action="Switch modes only if this is intentional.",
        )
        reason = autonomy_decision.get("reason") or (
            blocking_warnings[0].get("reason", "") if blocking_warnings else ""
        )
        trigger = autonomy_decision.get("trigger")
        if trigger and trigger not in reason:
            reason = f"{reason} ({trigger})"
        blocked_status = (
            "blocked_panic"
            if autonomy_decision.get("risk") == "paid"
            and "panic" in str(autonomy_decision.get("reason", "")).lower()
            else "blocked"
        )
        return {
            "status": blocked_status,
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
            "run_id": run_id,
            "checkpoint_id": None,
            "autonomy_decision": autonomy_decision,
            "effective_mode": selected_mode,
            "requested_mode": requested_mode,
            "stopped": False,
            "resume_actions": [],
            "diff_review": None,
        }

    if selected_model.startswith("account:"):
        from opai import app_state as A

        checkpoint = (
            create_checkpoint(
                root,
                message,
                mode=selected_mode,
                model_id=selected_model,
                decision=autonomy_decision,
            )
            if allow_edits
            else None
        )
        if checkpoint:
            from .run_state import update_run

            update_run(root, run_id, checkpoint_id=checkpoint["id"])
        result = A.ask(
            root,
            message,
            selected_model,
            allow_edits=allow_edits,
            account_runner=account_runner,
            mode=selected_mode,
            cancel_event=cancel_event,
            run_id=run_id,
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
        stopped = status == "account_stopped" or bool(result.get("stopped"))
        finish_run(
            root,
            run_id,
            status="stopped"
            if stopped
            else ("completed" if status == "answered" else "failed"),
            changed_files=result.get("changed_files", []),
            receipt_id=receipt["message_id"],
            next_action="Review changed files before committing.",
        )
        changed = result.get("changed_files", [])
        return {
            "status": status,
            "answer": result.get("answer")
            or result.get("hint")
            or result.get("reason")
            or "The model didn't return anything. Try again or pick another model.",
            "tool_trace": tool_trace,
            "receipt": receipt,
            "changed_files": changed,
            "warnings": warnings,
            "next_actions": ["Review changed files before committing."],
            "raw_result": result,
            "run_id": run_id,
            "checkpoint_id": checkpoint["id"] if checkpoint else None,
            "autonomy_decision": autonomy_decision,
            "effective_mode": selected_mode,
            "requested_mode": requested_mode,
            "stopped": stopped,
            "resume_actions": [
                "Review the diff from the checkpoint.",
                "Rerun from checkpoint context if needed.",
            ]
            if stopped
            else [],
            "diff_review": {
                "checkpoint_id": checkpoint["id"] if checkpoint else None,
                "changed_files": changed,
                "requires_review": bool(changed),
            },
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
    elif result.get("status") == "runner_error" or not answer:
        answer = (
            "The local model couldn't answer that. Pick your Claude or Codex account "
            "in the model menu, or check that your local model is running."
        )
    finish_run(
        root,
        run_id,
        status="completed"
        if status_map.get(result.get("status")) == "answered"
        else "failed",
        receipt_id=receipt["message_id"],
        next_action=result.get("next_command") or "Review the savings receipt.",
    )
    return {
        "status": status_map.get(result.get("status"), result.get("status", "error")),
        "answer": answer,
        "tool_trace": tool_trace,
        "receipt": receipt,
        "changed_files": [],
        "warnings": warnings,
        "next_actions": [result.get("next_command") or "Review the savings receipt."],
        "raw_result": result,
        "run_id": run_id,
        "checkpoint_id": None,
        "autonomy_decision": autonomy_decision,
        "effective_mode": selected_mode,
        "requested_mode": requested_mode,
        "stopped": False,
        "resume_actions": [],
        "diff_review": None,
    }
