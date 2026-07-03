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


def _plan_payload(mode: str, status: str, answer: str) -> dict[str, Any]:
    """Structured steps for plan-mode answers (issue #130); {} otherwise."""
    if mode != "plan" or status != "answered":
        return {}
    from opai.gui_modes import parse_plan_steps

    steps = parse_plan_steps(answer)
    if not steps:
        return {}
    return {"steps": steps, "source": "parsed_from_answer"}


def _cancelled_result(
    message: str,
    tool_trace: list[dict[str, Any]],
    model_id: str,
    mode: str,
    *,
    answer: str = "",
) -> dict[str, Any]:
    """A calm 'stopped by user' result — partial text kept, not an error."""
    body = "Generation stopped by you."
    if answer.strip():
        body = answer.strip() + "\n\n_(stopped by you)_"
    return {
        "status": "cancelled",
        "answer": body,
        "tool_trace": tool_trace,
        "receipt": {},
        "changed_files": [],
        "warnings": [],
        "next_actions": ["Edit the prompt, retry, or switch model."],
        "partial": bool(answer.strip()),
    }


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
    on_event: Any = None,
    on_text: Any = None,
    cancel: Any = None,
    allow_cloud: bool = False,
    allow_limit: bool = False,
) -> dict[str, Any]:
    """Run one chat turn. With ``on_event``/``on_text``/``cancel`` supplied it
    emits live activity and streams account output; without them it behaves
    exactly as before (one blocking call)."""

    def _emit(etype: str, status: str, title: str, **kw: Any) -> None:
        if on_event:
            from opai.activity import make_event

            on_event(make_event(etype, status, title, **kw))

    def _cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    root = project_root.expanduser().resolve()
    _emit("request_prepare", "success", "Preparing request")
    prefs = load_gui_preferences(root)
    selected_model = model_id or prefs.get("default_model") or "auto"
    selected_mode = mode or prefs.get("default_mode") or DEFAULT_MODE

    def _fallback_model() -> dict[str, Any] | None:
        from opai import app_state as app

        catalog = app.available_models(root, discover_local=False)
        models = list(catalog.get("models") or [])
        free = [
            item
            for item in models
            if item.get("kind") == "free" and item.get("available") is not False
        ]
        if free:
            return free[0]
        accounts = [
            item
            for item in models
            if item.get("kind") == "account" and item.get("available") is not False
        ]
        preferred = ("haiku", "mini", "spark", "sonnet")
        return next(
            (
                item
                for suffix in preferred
                for item in accounts
                if suffix in str(item.get("id") or "").lower()
            ),
            accounts[0] if accounts else None,
        )

    if selected_model == "auto" and allow_cloud:
        fallback = _fallback_model()
        if fallback is not None:
            selected_model = str(fallback["id"])

    usage_limits = prefs.get("usage_limits") or {}
    if selected_model in usage_limits and not allow_limit:
        from .usage import usage_limit_gate

        parts = selected_model.split(":")
        provider = parts[1] if len(parts) > 1 else "opai"
        usage = usage_limit_gate(
            root,
            selected_model,
            provider=provider,
            limits=usage_limits,
        )
        if usage["requiresConfirmation"]:
            return {
                "status": "needs_limit_confirmation",
                "answer": (
                    f"{selected_model} reached your {usage['limit']:,} "
                    f"{usage['metric']} soft limit. Confirm to continue."
                ),
                "usage": usage,
                "tool_trace": [],
                "changed_files": [],
                "warnings": [],
                "next_actions": ["Confirm this call or raise the limit in Settings."],
            }
    tool_trace = route_intents(root, message, mode=selected_mode)
    _emit(
        "context_read",
        "success",
        "Read project context",
        detail=f"{len(tool_trace)} routing step(s)",
    )
    warnings = safety_warnings(root, message, mode=selected_mode)
    rec = recommend_model(root, message)
    tier = str(rec.get("recommended_model_tier") or "L1").upper()
    _emit(
        "model_selected",
        "success",
        "Selected OPai mode",
        metadata={"model": selected_model},
    )

    if warnings and selected_mode != "full-auto":
        _emit("error", "warning", "Blocked before running (looked risky)")
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

    if selected_model.startswith("free:"):
        from opai import app_state as A

        if _cancelled():
            return _cancelled_result(message, tool_trace, selected_model, selected_mode)
        provider = selected_model.split(":", 2)[1]
        _emit(
            "request_sending" if allow_cloud else "needs_confirmation",
            "running" if allow_cloud else "warning",
            "Sending free-tier API request"
            if allow_cloud
            else "Free-tier API confirmation required",
            metadata={"provider": provider},
        )
        result = A.ask(
            root,
            message,
            selected_model,
            allow_cloud=allow_cloud,
            allow_edits=False,
            mode=selected_mode,
        )
        receipt = build_savings_receipt(
            root,
            task=message,
            selected_model=selected_model,
            selected_mode=selected_mode,
            chosen_tier="L2",
            confidence="estimated",
        )
        status_map = {
            "answered_by_free_api": "answered",
            "cache_hit": "answered",
            "confirmation_required": "needs_free_confirmation",
            "model_unavailable": "needs_model",
            "runner_error": "runner_error",
        }
        status = status_map.get(result.get("status"), result.get("status", "error"))
        # Ledger truth (#144): a route/savings event is only real once the task
        # actually answered — confirmation prompts and failures record nothing.
        if status == "answered":
            _record_gui_route(
                root,
                message,
                tier="L2",
                receipt=receipt,
                tool_trace=tool_trace,
                model_id=selected_model,
                mode=selected_mode,
            )
        answer = (
            result.get("answer")
            or result.get("message")
            or result.get("hint")
            or result.get("error")
            or "The free-tier API did not return an answer."
        )
        if status == "answered":
            _emit("completed", "success", "OPai completed")
            if on_text and answer:
                on_text(answer)
        elif status != "needs_free_confirmation":
            _emit("failed", "error", "Free-tier API request failed")
        return {
            "status": status,
            "answer": answer,
            "tool_trace": tool_trace,
            "receipt": receipt,
            "changed_files": [],
            "warnings": [],
            "next_actions": ["Review provider quota and billing settings."],
            "raw_result": result,
            "error": result.get("error"),
        }

    if selected_model.startswith("account:"):
        from opai import app_state as A

        if _cancelled():
            return _cancelled_result(message, tool_trace, selected_model, selected_mode)
        provider = selected_model.split(":")[1] if ":" in selected_model else "account"
        _emit(
            "provider_checking",
            "running",
            "Checking OPai connection",
            metadata={"provider": provider},
        )
        if account_runner is None:
            from .accounts import test_account_connection

            connection = test_account_connection(provider)
            if connection["authStatus"] not in {"connected", "unknown"}:
                from opai.provider_contract import normalize_provider_error

                status_detail = {
                    "not_configured": "No credentials configured",
                    "invalid": "401 Invalid authentication credentials",
                    "expired": "OAuth token expired",
                    "misconfigured": "Provider CLI is misconfigured",
                    "provider_unavailable": "Provider unavailable",
                    "disconnected": "Provider disconnected",
                }.get(str(connection["authStatus"]), "Provider connection failed")
                error = normalize_provider_error(
                    provider,
                    connection.get("safeDiagnostic")
                    if connection.get("lastErrorCode")
                    else status_detail,
                )
                event_type = (
                    "provider_auth_failed"
                    if error["code"].startswith("AUTH_")
                    else "failed"
                )
                _emit(
                    event_type,
                    "error",
                    error["title"],
                    metadata={"provider": provider, "code": error["code"]},
                )
                return {
                    "status": "failed",
                    "answer": error["userMessage"],
                    "error": error,
                    "tool_trace": tool_trace,
                    "changed_files": [],
                    "warnings": [],
                    "next_actions": list(error["recoveryActions"]),
                }
            if connection["authStatus"] == "connected":
                _emit(
                    "provider_authenticated",
                    "success",
                    "OPai connection verified",
                    metadata={"provider": provider},
                )
        else:
            _emit(
                "provider_authenticated",
                "success",
                "OPai connection verified",
                metadata={"provider": provider},
            )
        _emit(
            "request_sending",
            "running",
            "Sending OPai request",
            metadata={"provider": provider},
        )
        result = A.ask(
            root,
            message,
            selected_model,
            allow_edits=allow_edits,
            account_runner=account_runner,
            mode=selected_mode,
            on_event=on_event,
            on_text=on_text,
            cancel=cancel,
        )
        if result.get("status") == "cancelled":
            _emit("cancelled", "cancelled", "Stopped by you")
            return _cancelled_result(
                message,
                tool_trace,
                selected_model,
                selected_mode,
                answer=result.get("answer") or "",
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
        status = (
            "answered"
            if result.get("status") == "answered_by_account"
            else result.get("status", "error")
        )
        # Ledger truth (#144): only an answered call leaves a receipt event —
        # a failed provider call must not become the "last savings receipt".
        # (The real spend is recorded by record_model_call on success only.)
        if status == "answered":
            record_event(
                root,
                "gui_receipt",
                task=message,
                receipt=receipt,
                selected_model=selected_model,
                selected_mode=selected_mode,
                tool_count=len(tool_trace),
            )
        if status == "answered":
            _emit("completed", "success", "OPai completed")
        else:
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            code = str(error.get("code") or "UNKNOWN")
            event_type = (
                "provider_auth_failed" if code.startswith("AUTH_") else "failed"
            )
            _emit(
                event_type,
                "error",
                str(error.get("title") or "OPai could not complete this request."),
                metadata={"provider": provider, "code": code},
            )
        answer_text = (
            result.get("answer")
            or result.get("hint")
            or result.get("reason")
            or "The model didn't return anything. Try again or pick another model."
        )
        return {
            "status": status,
            "answer": answer_text,
            "tool_trace": tool_trace,
            "receipt": receipt,
            "changed_files": result.get("changed_files", []),
            "warnings": [],
            "next_actions": ["Review changed files before committing."],
            "raw_result": result,
            "error": result.get("error"),
            # Structured plan (#130): steps parsed from the REAL plan-mode
            # answer, so the GUI can render an editable checklist and build
            # only the steps the user keeps. Empty when the answer isn't a
            # recognizable step list — never invented.
            "plan": _plan_payload(selected_mode, status, answer_text),
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
    if _cancelled():
        _emit("cancelled", "cancelled", "Stopped by you")
        return _cancelled_result(message, tool_trace, selected_model, selected_mode)
    _emit("request_sending", "running", "Running OPai locally")
    # cancel threads into the local runner too (#107): Stop closes the HTTP
    # connection mid-generation instead of only ignoring the late result.
    result = run_ask(root, message, record=False, cancel=cancel)
    if result.get("status") == "cancelled":
        _emit("cancelled", "cancelled", "Stopped by you")
        return _cancelled_result(message, tool_trace, selected_model, selected_mode)
    status_map = {
        "answered_locally": "answered",
        "cache_hit": "answered",
        "no_local_model": "needs_model",
        "confirmation_required": "needs_confirmation",
    }
    answer = result.get("answer") or result.get("hint") or result.get("reason") or ""
    if result.get("status") == "no_local_model":
        fallback = _fallback_model() if selected_model == "auto" else None
        if fallback is not None:
            label = str(fallback.get("label") or fallback.get("id") or "a cloud model")
            answer = (
                f"No local model is running. OPai can continue with {label}, but "
                "your task will leave this device. Confirm to continue."
            )
            return {
                "status": "needs_auto_confirmation",
                "answer": answer,
                "fallbackModelId": fallback["id"],
                "fallbackModelLabel": label,
                "cloudStarted": False,
                "tool_trace": tool_trace,
                "receipt": receipt,
                "changed_files": [],
                "warnings": [],
                "next_actions": [
                    "Confirm the named fallback or connect a local model."
                ],
                "raw_result": result,
            }
        answer = (
            "Auto has no available model. Connect a free API, account, or local "
            "model in Settings, then retry."
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
    final_status = status_map.get(result.get("status"), result.get("status", "error"))
    if final_status == "answered":
        # Ledger truth (#144): record the route + savings only for a run that
        # actually answered. "No local model" cards and runner errors used to
        # inflate routed_tasks / estimated_savings_usd before anything ran.
        _record_gui_route(
            root,
            message,
            tier=tier,
            receipt=receipt,
            tool_trace=tool_trace,
            model_id=selected_model,
            mode=selected_mode,
        )
        _emit("completed", "success", "OPai completed")
        if on_text and answer:
            on_text(answer)
    else:
        _emit("failed", "error", "OPai could not complete locally")
    return {
        "status": final_status,
        "answer": answer,
        "tool_trace": tool_trace,
        "receipt": receipt,
        "changed_files": [],
        "warnings": [],
        "next_actions": [result.get("next_command") or "Review the savings receipt."],
        "raw_result": result,
        "plan": _plan_payload(selected_mode, final_status, answer),
    }
