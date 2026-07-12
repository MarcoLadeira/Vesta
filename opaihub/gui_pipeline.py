from __future__ import annotations

import contextlib
import json
import uuid
from pathlib import Path
from typing import Any

from .agent_policy import (
    AgentMode,
    build_capability_contract,
    resolve_agent_policy,
)
from .agent_runtime import AgentRuntime, RuntimePhase
from .autonomy import effective_mode
from .checkpoints import create_run_checkpoint, finalize_run_checkpoint
from .cost_model import estimate_route_savings, estimate_tokens, load_cost_model
from .cost_telemetry import (
    estimated_telemetry,
    normalize_account_result,
    record_workflow_cost,
)
from .diff_review import build_diff_review
from .gui_preferences import load_gui_preferences
from .intent_router import route_intents, safety_warnings
from .ledger import record_event, record_route_decision, read_events
from .model_intelligence import recommend_model
from .repo_context import classify_dirty_paths, resolve_repo_context, save_active_repo
from .task_packet import build_task_packet
from .workflow_state import WorkflowState, load_workflow_state, save_workflow_state


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
    paid_call: bool = False,
) -> dict[str, Any]:
    cost_model = load_cost_model(project_root)
    tokens = estimate_tokens(task, cost_model) or int(
        cost_model.get("default_task_tokens", 6000)
    )
    route = estimate_route_savings(chosen_tier, task_tokens=tokens, model=cost_model)
    measured = (
        float(actual_cost_usd)
        if isinstance(actual_cost_usd, (int, float))
        and not isinstance(actual_cost_usd, bool)
        else None
    )
    if paid_call:
        # Savings truth (#76): a paid provider call records spend, never
        # savings - there is no separately measured comparison to claim one.
        # A reported $0.00 is subscription-style billing, not "free": the
        # spend is shown as the tier estimate and labelled unknown.
        if measured is not None and measured > 0:
            actual, effective_confidence = measured, "actual"
        elif measured == 0.0:
            actual, effective_confidence = route["estimated_actual_usd"], "unknown"
        else:
            actual, effective_confidence = (
                route["estimated_actual_usd"],
                "estimated",
            )
        savings = 0.0
        paid_avoided = False
        basis = "paid_call_records_spend_not_savings"
    else:
        actual = route["estimated_actual_usd"] if measured is None else measured
        effective_confidence = confidence
        savings = max(0.0, round(route["estimated_baseline_usd"] - actual, 6))
        paid_avoided = bool(
            route["cloud_call_avoided"] and actual <= route["estimated_actual_usd"]
        )
        basis = "estimated_vs_unrouted_baseline"
    return {
        "schema": 2,
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
        "measured_cost_usd": measured,
        "paid_call": bool(paid_call),
        "paid_call_avoided": paid_avoided,
        "savings_basis": basis,
        "context_tokens_saved": int(context_tokens_saved),
        "confidence": effective_confidence,
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
    focus_hint: str | None = None,
    output_instruction: str | None = None,
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

    # One preamble row per turn (#225): every pre-provider step updates the
    # same derived event id in place, so preparation reads as one calm row
    # instead of 4-6 appended rows. Connection/model facts are mirrored on the
    # status channel for the status strip (docs/AI_ACTIVITY_UX.md).
    from opai.activity import derived_id, new_id

    turn_id = new_id()
    _phase_id = derived_id(turn_id, "phase")
    _phase_state = {"open": False, "etype": "request_prepare"}

    def _phase(etype: str, status: str, title: str, **kw: Any) -> None:
        _phase_state["open"] = status == "running"
        _phase_state["etype"] = etype
        _emit(etype, status, title, event_id=_phase_id, request_id=turn_id, **kw)

    def _phase_close(status: str, title: str) -> None:
        # Never leave the preamble row spinning after a terminal outcome.
        if _phase_state["open"]:
            _phase(_phase_state["etype"], status, title)

    def _status_mirror(phase: str, etype: str, title: str, **kw: Any) -> None:
        _emit(
            etype,
            "success",
            title,
            event_id=derived_id(turn_id, phase),
            request_id=turn_id,
            channel="status",
            **kw,
        )

    root = project_root.expanduser().resolve()
    _phase("request_prepare", "running", "Preparing request")
    prefs = load_gui_preferences(root)
    selected_model = model_id or prefs.get("default_model") or "auto"
    # Central autonomy decision (#137): a requested/stored full-auto is honored
    # only when Full Auto is pinned; otherwise it is downgraded to Safe Auto.
    autonomy = effective_mode(mode, prefs)
    requested_run_mode = autonomy.effective_mode
    policy = resolve_agent_policy(message, focus_hint=focus_hint)
    if policy.mode in {AgentMode.IMPLEMENT, AgentMode.SHIP}:
        selected_mode = (
            requested_run_mode
            if requested_run_mode in {"safe-auto", "full-auto"}
            else "safe-auto"
        )
    elif requested_run_mode == "plan":
        selected_mode = "plan"
    else:
        selected_mode = "ask"
    repo_context = resolve_repo_context(root)
    save_active_repo(root, repo_context)
    previous_workflow = load_workflow_state(root)
    runtime = AgentRuntime(root, task=message)
    runtime.transition(
        RuntimePhase.INTENT_RESOLVED,
        message=f"{policy.mode.value.title()} mode selected",
        metadata={"mode": policy.mode.value},
        next_actions=("resolve active repository",),
    )
    runtime.transition(
        RuntimePhase.REPO_RESOLVED,
        message="Active repository resolved",
        metadata={
            "path": str(repo_context.path),
            "branch": repo_context.branch,
            "remote": repo_context.remote,
            "dirty_count": len(repo_context.dirty_paths),
        },
        next_actions=("gather bounded context",),
    )
    runtime.transition(
        RuntimePhase.CONTEXT_GATHERING,
        message="Task context assembled",
        metadata={"dirty_paths": list(repo_context.dirty_paths)},
    )
    if policy.requires_confirmation:
        runtime.transition(
            RuntimePhase.AWAITING_APPROVAL,
            message="A dangerous action requires explicit approval",
            next_actions=("confirm the exact dangerous action",),
        )
    elif policy.mode in {AgentMode.IMPLEMENT, AgentMode.SHIP}:
        runtime.transition(
            RuntimePhase.IMPLEMENTING,
            message="Provider is executing the authorized implementation",
            next_actions=("inspect the structured provider result",),
        )
    else:
        runtime.transition(
            RuntimePhase.PLANNING,
            message="Provider is preparing a read-only response",
        )
    dirty = classify_dirty_paths(repo_context.dirty_paths, None)
    task_packet = build_task_packet(
        user_request=message,
        mode=policy.mode.value,
        repo=repo_context.to_dict(),
        dirty=dirty.to_dict(),
        issue=(
            {"number": previous_workflow.issue_number}
            if previous_workflow.issue_number is not None
            else {}
        ),
        constraints=(
            "preserve unrelated user changes",
            "no force push, hard reset, clean, branch deletion, secret exposure, or production credential changes",
            "paid or cloud calls require the existing OPai confirmation boundary",
        ),
        allowed_actions=policy.capabilities,
        forbidden_actions=(
            "force_push",
            "hard_reset",
            "git_clean",
            "delete_user_work",
            "expose_secrets",
            "mutate_production_credentials",
        ),
        done_criteria=(
            "requested behavior is implemented or explained",
            "relevant tests are run when edits are authorized",
            "changed files and blockers are reported truthfully",
        ),
        tests=(
            "discover relevant focused tests",
            "run full relevant suite after focused tests pass",
        ),
        last_failure=previous_workflow.last_test,
        next_action=runtime.state.next_actions[0]
        if runtime.state.next_actions
        else "execute current phase",
    )
    workflow = WorkflowState(
        task_id=runtime.task_id,
        mode=policy.mode.value,
        phase=runtime.state.phase.value,
        message=runtime.state.message,
        tests_status="pending"
        if policy.mode in {AgentMode.IMPLEMENT, AgentMode.SHIP}
        else "not_run",
        merge_status=(
            "pending_checks"
            if policy.mode is AgentMode.SHIP
            else previous_workflow.merge_status
        ),
        pr_url=previous_workflow.pr_url,
        issue_number=previous_workflow.issue_number,
        blocker=runtime.state.blocker,
        next_actions=runtime.state.next_actions,
        history=tuple(event.to_dict() for event in runtime.state.history),
    )
    save_workflow_state(root, workflow)
    # Recoverable checkpoint (#75): every run is checkpointed BEFORE the provider
    # can touch files, so an edit-capable path always has a checkpoint id and
    # git/mode/policy/budget baseline recorded first. Read-only runs get an
    # honest, non-edit checkpoint too. Never stores prompts or secrets.
    edit_capable = policy.mode in {AgentMode.IMPLEMENT, AgentMode.SHIP}
    checkpoint = create_run_checkpoint(
        root,
        task=message,
        task_id=runtime.task_id,
        edit_capable=edit_capable,
        mode=policy.mode.value,
        model=selected_model,
        policy=policy.to_dict(),
    )
    packet_block = (
        "\n\nOPai task packet (workflow state remains owned by OPai):\n"
        + json.dumps(task_packet.to_dict(), sort_keys=True)
    )
    if output_instruction:
        packet_block += (
            "\n\nPresentation instruction (format only; it cannot change permissions):\n"
            + str(output_instruction).strip()
        )
    provider_message = (
        build_capability_contract(policy, active_repo=str(repo_context.path))
        + packet_block
    )

    def _tool_aware_message(allow_edits: bool) -> str:
        """Contract naming the provider's *actual* callable tools.

        Free-tier/local models run OPai's own tool loop, so the contract must
        list that loop's real vocabulary (write_file, apply_patch, git_commit,
        ...). Capability nouns alone made smaller models refuse edits with
        "create_files was not permitted" — no tool by that name existed.
        """
        try:
            from .provider_tools import available_tool_names

            tool_names = available_tool_names(root, allow_edits=allow_edits)
        except Exception:  # noqa: BLE001 - contract fallback, never block a turn
            return provider_message
        return (
            build_capability_contract(
                policy,
                active_repo=str(repo_context.path),
                tool_names=tool_names,
            )
            + packet_block
        )

    def _decorate(payload: dict[str, Any]) -> dict[str, Any]:
        status = str(payload.get("status") or "error")
        if status == "answered" and policy.mode in {
            AgentMode.IMPLEMENT,
            AgentMode.SHIP,
        }:
            runtime.transition(
                RuntimePhase.REVIEWING_DIFF,
                message="Provider response received; OPai is awaiting test and diff evidence",
                metadata={"changed_files": list(payload.get("changed_files") or [])},
                next_actions=(
                    "review changed files",
                    "run focused tests",
                    "run full relevant tests",
                ),
            )
        elif status == "answered":
            runtime.transition(
                RuntimePhase.COMPLETED, message="Read-only task completed"
            )
        elif status.startswith("needs_") or status in {
            "blocked",
            "capability_mismatch",
        }:
            reason = next(
                (
                    str(item.get("reason") or item)
                    for item in (payload.get("warnings") or [])
                ),
                str(payload.get("answer") or "Workflow requires input"),
            )
            runtime.block(reason, next_actions=payload.get("next_actions") or ())
        else:
            runtime.fail(
                "cancelled by user"
                if status == "cancelled"
                else str(payload.get("answer") or "Provider execution failed"),
                next_actions=payload.get("next_actions") or (),
            )
        current_repo = resolve_repo_context(root)
        save_active_repo(root, current_repo)
        changed_files = tuple(str(item) for item in payload.get("changed_files") or [])
        attributed_paths = changed_files or tuple(
            path
            for path in current_repo.dirty_paths
            if path not in set(repo_context.dirty_paths)
        )
        diff_review: dict[str, Any] = {}
        if status == "answered" and policy.mode in {
            AgentMode.IMPLEMENT,
            AgentMode.SHIP,
        }:
            diff_review = build_diff_review(
                current_repo.path, include_paths=attributed_paths
            )
        state = WorkflowState(
            task_id=runtime.task_id,
            mode=policy.mode.value,
            phase=runtime.state.phase.value,
            message=runtime.state.message,
            tests_status=(
                "not_verified"
                if status == "answered" and policy.allows("run_tests")
                else workflow.tests_status
            ),
            merge_status=workflow.merge_status,
            pr_url=workflow.pr_url,
            issue_number=workflow.issue_number,
            blockers=tuple(
                str(item.get("reason") or item)
                for item in (payload.get("warnings") or [])
            ),
            blocker=runtime.state.blocker,
            next_actions=runtime.state.next_actions,
            history=tuple(event.to_dict() for event in runtime.state.history),
            changed_files=changed_files,
            provider={"model": selected_model, "run_mode": selected_mode},
            cost={
                **(payload.get("receipt") or {}),
                **(
                    {"telemetry": payload["cost_telemetry"]}
                    if payload.get("cost_telemetry")
                    else {}
                ),
            },
            diff_review=diff_review,
        )
        runtime.ledger.append(
            "turn_result",
            task=message,
            repo=str(current_repo.path),
            provider=selected_model,
            mode=policy.mode.value,
            phase=runtime.state.phase.value,
            files=list(payload.get("changed_files") or []),
            tests=state.tests_status,
            pr=state.pr_url,
            error=payload.get("error") or "",
            cost=payload.get("receipt") or {},
            telemetry=payload.get("cost_telemetry") or {},
        )
        save_workflow_state(root, state)
        # Finalize the checkpoint (#75) with the run's real result. Completion
        # state is honest per outcome: an edit-capable answered run is
        # "answered"; a read-only answer is "read_only"; confirmation prompts
        # edited nothing; a cancel with no in-run changes is
        # "cancelled_before_edit".
        if status == "answered":
            completion = "answered" if edit_capable else "read_only"
        elif status == "cancelled":
            completion = "cancelled_before_edit" if not changed_files else "cancelled"
        elif status in {"blocked", "capability_mismatch"}:
            completion = "blocked"
        elif status.startswith("needs_"):
            completion = "read_only"
        else:
            completion = "failed"
        recovery = tuple(str(item) for item in payload.get("next_actions") or ())
        with contextlib.suppress(Exception):  # noqa: BLE001 - never fail a turn
            finalize_run_checkpoint(
                root,
                checkpoint.checkpoint_id,
                completion_state=completion,
                outcome=str(payload.get("status") or ""),
                changed_files=changed_files,
                diff_summary=diff_review.get("summary") or {},
                recovery_actions=recovery,
            )
        return {
            **payload,
            "agent_policy": policy.to_dict(),
            "requested_run_mode": autonomy.requested_mode,
            "effective_run_mode": selected_mode,
            "autonomy": autonomy.to_dict(),
            "checkpoint_id": checkpoint.checkpoint_id,
            "checkpoint": {
                "id": checkpoint.checkpoint_id,
                "edit_capable": checkpoint.edit_capable,
                "completion_state": completion,
                "git_head": checkpoint.git.get("head", ""),
            },
            "repo_context": current_repo.to_dict(),
            "workflow": state.to_dict(),
            "task_packet": task_packet.to_dict(),
        }

    if policy.requires_confirmation:
        blocked_tier = str(
            recommend_model(root, message).get("recommended_model_tier") or "L1"
        ).upper()
        blocked_receipt = build_savings_receipt(
            root,
            task=message,
            selected_model=selected_model,
            selected_mode=selected_mode,
            chosen_tier=blocked_tier,
            confidence="blocked",
        )
        return _decorate(
            {
                "status": "blocked",
                "answer": (
                    "This request includes a destructive or irreversible action. "
                    "Switch to Ask or Plan to review it without execution, or "
                    "confirm that specific action before OPai runs it."
                ),
                "tool_trace": [],
                "receipt": blocked_receipt,
                "changed_files": [],
                "warnings": [{"severity": "danger", "reason": policy.rationale}],
                "next_actions": [
                    "Switch to Ask or Plan, rephrase safely, or confirm the exact dangerous action."
                ],
            }
        )

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
            _phase_close("warning", "Awaiting your confirmation")
            return _decorate(
                {
                    "status": "needs_limit_confirmation",
                    "answer": (
                        f"{selected_model} reached your {usage['limit']:,} "
                        f"{usage['metric']} soft limit. Confirm to continue."
                    ),
                    "usage": usage,
                    "tool_trace": [],
                    "changed_files": [],
                    "warnings": [],
                    "next_actions": [
                        "Confirm this call or raise the limit in Settings."
                    ],
                }
            )
    tool_trace = route_intents(root, message, mode=selected_mode)
    _phase(
        "context_read",
        "running",
        "Read project context",
        detail=f"{len(tool_trace)} routing step(s)",
    )
    warnings = safety_warnings(root, message, mode=selected_mode)
    rec = recommend_model(root, message)
    tier = str(rec.get("recommended_model_tier") or "L1").upper()
    _phase(
        "model_selected",
        "running",
        "Selected OPai mode",
        metadata={"model": selected_model},
    )
    _status_mirror(
        "model",
        "model_selected",
        f"Model: {selected_model}",
        metadata={"model": selected_model},
    )

    if warnings and selected_mode != "full-auto":
        _phase("error", "warning", "Blocked before running (looked risky)")
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
        # Guide the user to a *safe* next step, never toward Full Auto (#142):
        # nudging someone to the mode that disables every safeguard just to get
        # past a risk warning is the opposite of a cost/safety firewall.
        return _decorate(
            {
                "status": "blocked",
                "answer": (
                    "Safe Auto held this back because it matched a command that can "
                    "change or delete files"
                    + (f" ({reason})" if reason else "")
                    + ".\nSwitch to Ask or Plan mode to have OPai explain or plan it "
                    "without running anything, or rephrase the request without the "
                    "risky command."
                ),
                "tool_trace": tool_trace,
                "receipt": receipt,
                "changed_files": [],
                "warnings": warnings,
                "next_actions": [
                    "Switch to Ask or Plan mode to review this safely, or rephrase "
                    "the request."
                ],
            }
        )

    # Plan / Ask / Approve-Edits are read-only; Safe Auto / Full Auto may edit.
    # The selected model always actually answers - no canned template.
    allow_edits = selected_mode in {"safe-auto", "full-auto"}

    if selected_model.startswith("free:"):
        from opai import app_state as A

        if _cancelled():
            _phase_close("cancelled", "Stopped by you")
            return _decorate(
                _cancelled_result(message, tool_trace, selected_model, selected_mode)
            )
        provider = selected_model.split(":", 2)[1]
        _phase(
            "request_sending" if allow_cloud else "needs_confirmation",
            "running" if allow_cloud else "warning",
            "Sending free-tier API request"
            if allow_cloud
            else "Free-tier API confirmation required",
            metadata={"provider": provider},
        )
        # Real Stop for free-tier (#152): thread the cancel Event so the HTTP
        # request is aborted mid-flight, not just hidden by the stale guard.
        result = A.ask(
            root,
            _tool_aware_message(allow_edits),
            selected_model,
            allow_cloud=allow_cloud,
            allow_edits=allow_edits,
            mode=selected_mode,
            cancel=cancel,
        )
        if result.get("status") == "cancelled":
            _phase_close("cancelled", "Stopped by you")
            _emit("cancelled", "cancelled", "Stopped by you")
            return _decorate(
                _cancelled_result(message, tool_trace, selected_model, selected_mode)
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
        free_telemetry = None
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
            # Free APIs report no dollars here, so the telemetry is honestly
            # labelled estimated (#178) - never presented as a real spend.
            free_telemetry = estimated_telemetry(
                provider,
                tokens=int(receipt["estimated_tokens"]),
                cost_usd=float(receipt["estimated_actual_usd"]),
                model=selected_model,
            )
            record_workflow_cost(root, runtime.task_id, free_telemetry, task=message)
        answer = (
            result.get("answer")
            or result.get("message")
            or result.get("hint")
            or result.get("error")
            or "The free-tier API did not return an answer."
        )
        if status == "answered":
            _phase_close("success", "Request sent")
            _emit("completed", "success", "OPai completed")
            if on_text and answer:
                on_text(answer)
        elif status != "needs_free_confirmation":
            _phase_close("error", "Free-tier API request failed")
            _emit("failed", "error", "Free-tier API request failed")
        else:
            _phase_close("warning", "Awaiting your confirmation")
        return _decorate(
            {
                "status": status,
                "answer": answer,
                "tool_trace": tool_trace + list(result.get("tool_trace") or []),
                "receipt": receipt,
                "changed_files": list(result.get("changed_files") or []),
                "warnings": [],
                "next_actions": ["Review provider quota and billing settings."],
                "raw_result": result,
                "error": result.get("error"),
                "cost_telemetry": free_telemetry.to_dict() if free_telemetry else {},
            }
        )

    if selected_model.startswith("account:"):
        from opai import app_state as A

        if _cancelled():
            _phase_close("cancelled", "Stopped by you")
            return _decorate(
                _cancelled_result(message, tool_trace, selected_model, selected_mode)
            )
        provider = selected_model.split(":")[1] if ":" in selected_model else "account"
        _phase(
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

                # Prefer the structured error the connection check already
                # produced (e.g. CONFIG_INVALID with the "run the repair"
                # guidance). Re-normalizing the human diagnostic string lost
                # that classification and showed a dead-end "could not
                # complete this request" instead.
                error = connection.get("error")
                if not error:
                    status_detail = {
                        "not_configured": "No credentials configured",
                        "invalid": "401 Invalid authentication credentials",
                        "expired": "OAuth token expired",
                        "misconfigured": "Provider CLI is misconfigured",
                        "provider_unavailable": "Provider unavailable",
                        "disconnected": "Provider disconnected",
                    }.get(str(connection["authStatus"]), "Provider connection failed")
                    error = normalize_provider_error(provider, status_detail)
                event_type = (
                    "provider_auth_failed"
                    if error["code"].startswith("AUTH_")
                    else "failed"
                )
                _phase(
                    event_type,
                    "error",
                    error["title"],
                    metadata={"provider": provider, "code": error["code"]},
                )
                return _decorate(
                    {
                        "status": "failed",
                        "answer": error["userMessage"],
                        "error": error,
                        "tool_trace": tool_trace,
                        "changed_files": [],
                        "warnings": [],
                        "next_actions": list(error["recoveryActions"]),
                    }
                )
            if connection["authStatus"] == "connected":
                _phase(
                    "provider_authenticated",
                    "running",
                    "OPai sign-in verified locally",
                    metadata={"provider": provider},
                )
                _status_mirror(
                    "connect",
                    "provider_authenticated",
                    f"Connected · {provider}",
                    metadata={"provider": provider},
                )
        else:
            _phase(
                "provider_authenticated",
                "running",
                "OPai sign-in detected",
                metadata={"provider": provider},
            )
            _status_mirror(
                "connect",
                "provider_authenticated",
                f"Connected · {provider}",
                metadata={"provider": provider},
            )
        _phase(
            "request_sending",
            "running",
            "Sending OPai request",
            metadata={"provider": provider},
        )
        # The provider stream takes over from here (its own connect/stream
        # rows are the live surface), so the preamble row closes honestly.
        _phase("request_sending", "success", "Request sent")
        result = A.ask(
            root,
            provider_message,
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
            return _decorate(
                _cancelled_result(
                    message,
                    tool_trace,
                    selected_model,
                    selected_mode,
                    answer=result.get("answer") or "",
                )
            )
        if result.get("status") == "capability_mismatch":
            answer = str(result.get("hint") or result.get("reason") or "")
            _phase_close("warning", "Provider cannot enforce this edit mode")
            _emit(
                "capability_mismatch",
                "warning",
                "Choose a tool-capable provider",
                metadata={"provider": provider, "capability": "edit_files"},
            )
            return _decorate(
                {
                    "status": "capability_mismatch",
                    "answer": answer,
                    "tool_trace": tool_trace,
                    "receipt": {},
                    "changed_files": [],
                    "warnings": [{"reason": result.get("reason") or answer}],
                    "next_actions": [result.get("hint") or answer],
                    "raw_result": result,
                }
            )
        actual = result.get("cost_usd")
        # Savings truth (#76): an account call is a real spend; the receipt
        # records it with zero implied savings and derives its own confidence
        # (actual > 0 measured, unknown for subscription-style $0.00,
        # estimated when the provider reports nothing).
        receipt = build_savings_receipt(
            root,
            task=message,
            selected_model=selected_model,
            selected_mode=selected_mode,
            chosen_tier="L3",
            actual_cost_usd=actual if isinstance(actual, (int, float)) else None,
            paid_call=True,
        )
        # Provider cost telemetry (#178): the call actually ran, so record
        # what the provider itself reported - claude's total_cost_usd stays
        # "actual", codex stays honestly estimated - into the redacted
        # workflow ledger. This observes spend; it never authorizes it.
        account_telemetry = normalize_account_result(
            provider, result, model=selected_model
        )
        record_workflow_cost(root, runtime.task_id, account_telemetry, task=message)
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
        return _decorate(
            {
                "status": status,
                "answer": answer_text,
                "tool_trace": tool_trace,
                "receipt": receipt,
                "changed_files": result.get("changed_files", []),
                "warnings": [],
                "next_actions": ["Review changed files before committing."],
                "raw_result": result,
                "error": result.get("error"),
                "cost_telemetry": account_telemetry.to_dict(),
                # Structured plan (#130): steps parsed from the REAL plan-mode
                # answer, so the GUI can render an editable checklist and build
                # only the steps the user keeps. Empty when the answer isn't a
                # recognizable step list — never invented.
                "plan": _plan_payload(selected_mode, status, answer_text),
            }
        )

    from .ask import run_ask
    from .local_runner import runner_for_model

    receipt = build_savings_receipt(
        root,
        task=message,
        selected_model=selected_model,
        selected_mode=selected_mode,
        chosen_tier=tier,
        confidence="estimated",
    )
    if _cancelled():
        _phase_close("cancelled", "Stopped by you")
        _emit("cancelled", "cancelled", "Stopped by you")
        return _decorate(
            _cancelled_result(message, tool_trace, selected_model, selected_mode)
        )
    _phase("request_sending", "running", "Running OPai locally")
    # Honour the picked local model (#143): a concrete "provider:model" id must
    # run *that* model, not whatever detect_local_runner finds first. "auto"
    # (and unknown ids) fall through to run_ask's own local-first detection.
    picked_runner = None
    if selected_model not in {"auto", "", None} and ":" in str(selected_model):
        picked_runner = runner_for_model(selected_model, root)
    # cancel threads into the local runner too (#107): Stop closes the HTTP
    # connection mid-generation instead of only ignoring the late result.
    result = run_ask(
        root,
        provider_message,
        record=False,
        cancel=cancel,
        runner=picked_runner,
        selected_model_id=selected_model if picked_runner is not None else None,
        allow_edits=allow_edits,
    )
    if result.get("status") == "cancelled":
        _phase_close("cancelled", "Stopped by you")
        _emit("cancelled", "cancelled", "Stopped by you")
        return _decorate(
            _cancelled_result(message, tool_trace, selected_model, selected_mode)
        )
    if result.get("status") == "capability_mismatch":
        answer = str(result.get("hint") or result.get("reason") or "")
        _phase_close("warning", "Local model cannot enforce this edit mode")
        _emit(
            "capability_mismatch",
            "warning",
            "Choose a tool-capable provider",
            metadata={"provider": result.get("provider") or "local"},
        )
        return _decorate(
            {
                "status": "capability_mismatch",
                "answer": answer,
                "tool_trace": tool_trace,
                "receipt": {},
                "changed_files": [],
                "warnings": [{"reason": result.get("reason") or answer}],
                "next_actions": [result.get("hint") or answer],
                "raw_result": result,
            }
        )
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
            _phase_close("warning", "Needs your confirmation")
            return _decorate(
                {
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
            )
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
        _phase_close("success", "Answered locally")
        _emit("completed", "success", "OPai completed")
        if on_text and answer:
            on_text(answer)
    else:
        _phase_close("error", "OPai could not complete locally")
        _emit("failed", "error", "OPai could not complete locally")
    return _decorate(
        {
            "status": final_status,
            "answer": answer,
            "tool_trace": tool_trace,
            "receipt": receipt,
            "changed_files": [],
            "warnings": [],
            "next_actions": [
                result.get("next_command") or "Review the savings receipt."
            ],
            "raw_result": result,
            "plan": _plan_payload(selected_mode, final_status, answer),
        }
    )
