"""`opai ask`: actually answer cheap tasks locally (open issue #13).

Pipeline: classify with the model-intelligence taxonomy, check the local result
cache (a near-duplicate task in the same repo state is free), otherwise run a
local model on a compact, redacted evidence prompt. A real cloud call is avoided
and recorded to the ledger. Cloud-tier tasks are never auto-called - they return
``confirmation_required`` unless explicitly allowed.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import result_cache
from .cost_model import is_local_tier, load_cost_model
from .cancellation import LocalRunCancelled
from .evidence import collect_evidence
from .local_runner import LocalRunner, detect_local_runner
from .model_intelligence import recommend_model
from .project_instructions import build_system_prompt

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


def _complete_streaming(
    runner: Any, text: str, *, cancel: Any, on_text: Any, system: str = SYSTEM_PROMPT
) -> tuple[str, bool]:
    """Call ``runner.complete``, streaming via ``on_text`` when the runner
    supports it (#154). Returns ``(answer, streamed)``; ``streamed`` means
    ``on_text`` already received the whole answer, so the caller must not
    re-emit it. Runners without ``on_text``/``cancel`` degrade gracefully."""
    if on_text is not None:
        try:
            return (
                runner.complete(text, system=system, cancel=cancel, on_text=on_text),
                True,
            )
        except TypeError as exc:
            if "on_text" not in str(exc):
                raise
            # Runner has no on_text — fall through to the non-streaming path.
    try:
        return runner.complete(text, system=system, cancel=cancel), False
    except TypeError as exc:
        if "cancel" not in str(exc):
            raise
        return runner.complete(text, system=system), False


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
    on_text: Any = None,
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
        streamed = False
        try:
            # True mid-flight cancel (#107) and live token streaming (#154): the
            # runner owns emission (deltas, or one blocking emit) and closes its
            # HTTP connection when the cancel Event fires.
            answer, streamed = _complete_streaming(
                active,
                prompt,
                cancel=cancel,
                on_text=on_text,
                # The project's own standing instructions. Account models get
                # these from their vendor CLI; without this line local and
                # free-tier models never saw them, so the same request obeyed
                # the repository's rules or ignored them purely by which model
                # picked it up.
                system=build_system_prompt(SYSTEM_PROMPT, root),
            )
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
            "streamed": streamed,
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


_COMMAND_APPROVAL_ERROR = "COMMAND_NEEDS_APPROVAL"


def _extract_command_approval(payload: Any) -> dict[str, str] | None:
    """Normalize the tool loop's command-approval signal (F17/F9).

    The provider tool executor rejects confirm-class commands with the
    ``COMMAND_NEEDS_APPROVAL`` error code; the loop then stops with
    ``needs_consent`` carrying the exact command and its reason. The field
    layout has evolved, so read the explicit payload shapes first, then fall
    back to the tool trace.
    """

    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("command_approval")
    if isinstance(raw, Mapping):
        command = str(raw.get("command") or "").strip()
        if command:
            return {
                "command": command,
                "reason": str(raw.get("reason") or "").strip(),
            }
    command = str(payload.get("command") or "").strip()
    consent = str(payload.get("completion_state") or "").strip() == "needs_consent"
    if command and consent:
        reason = str(
            payload.get("approval_reason") or payload.get("reason") or ""
        ).strip()
        return {"command": command, "reason": reason}
    for item in payload.get("tool_trace") or []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("error_code") or "") != _COMMAND_APPROVAL_ERROR:
            continue
        command = str(item.get("command") or "").strip()
        if command:
            reason = str(item.get("reason") or item.get("message") or "").strip()
            return {"command": command, "reason": reason}
    return None


def _call_tool_loop(
    complete_with_tools: Any,
    task: str,
    *,
    root: Path,
    allow_edits: bool,
    system: str,
    cancel: Any,
    guard: Any,
    allow_command: str | None,
    tool_loop_policy: Any = None,
    repository_handle: Any = None,
    provider_id: str | None = None,
) -> dict[str, Any]:
    """Invoke the runner's tool loop, threading a one-shot command grant.

    ``allow_command`` is the exact command the user just approved (F17/F9); the
    executor permits it once. Older runners without the parameter simply never
    receive it — the grant is additive, never a behavior change on its own.
    ``tool_loop_policy`` (the turn's contract budgets) is threaded the same way.
    ``provider_id`` names the real provider (e.g. "gemini") for the per-turn
    ledger record; older runners fall back to their own best-effort identity.
    """

    kwargs: dict[str, Any] = {
        "project_root": root,
        "allow_edits": allow_edits,
        "tool_calling_enabled": True,
        "system": system,
        "cancel": cancel,
        "guard": guard,
    }

    def _accepts(name: str) -> bool:
        try:
            params = inspect.signature(complete_with_tools).parameters
        except (TypeError, ValueError):
            return True
        return name in params or any(
            param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values()
        )

    if allow_command and _accepts("allow_command"):
        kwargs["allow_command"] = allow_command
    if tool_loop_policy is not None and _accepts("tool_loop_policy"):
        kwargs["tool_loop_policy"] = tool_loop_policy
    if repository_handle is not None and _accepts("repository_handle"):
        kwargs["repository_handle"] = repository_handle
    if provider_id and _accepts("provider_id"):
        kwargs["provider_id"] = provider_id
    return complete_with_tools(task, **kwargs)


def run_explicit_model(
    project_root: Path,
    task: str,
    *,
    runner: LocalRunner,
    selected_model_id: str,
    allow_edits: bool = False,
    tool_calling_enabled: bool | None = None,
    allow_cloud: bool = False,
    guard: Any = None,
    mode: str = "ask",
    record: bool = True,
    cancel: Any = None,
    on_text: Any = None,
    allow_command: str | None = None,
    tool_loop_policy: Any = None,
    repository_handle: Any = None,
    provider_id: str | None = None,
) -> dict[str, Any]:
    """Run an explicitly selected model without Auto routing or prose caching.

    Authority is split (Task 6): ``allow_edits`` governs repository *mutations*;
    ``tool_calling_enabled`` governs whether tools are offered at all (defaults
    to ``allow_edits`` for compatibility). A per-turn :class:`ExecutionGuard`
    runs the financial/consent/provider checks before every provider turn — a
    caller may inject one, otherwise a default guard is built here.

    ``allow_command`` is a one-shot, exact-command grant the user issued after
    a command-approval prompt (F17/F9); it is threaded to the tool executor
    verbatim. ``tool_loop_policy`` carries the turn's contract budgets (tool
    calls, wall clock, compaction threshold) so a multi-file refactor is not
    held to a one-file fix's allowance; ``None`` keeps the defaults. Completion
    truth (F8): the result's ``completion_state`` is derived from what actually
    happened — never pre-seeded as "completed".
    """

    root = project_root.expanduser().resolve()
    use_tools = allow_edits if tool_calling_enabled is None else tool_calling_enabled
    base = {
        "task_hash": hashlib.sha256(
            (selected_model_id + "\0" + task).encode("utf-8")
        ).hexdigest()[:16],
        "tier": "L2",
        "model_id": selected_model_id,
    }
    if cancel is not None and cancel.is_set():
        return {**base, "status": "cancelled", "answer": ""}
    completion_state = ""
    blocked_reason = ""
    approval: dict[str, str] | None = None
    # Task 6/7: the tool-loop path self-records a per-turn ledger entry for
    # every provider round-trip (opaihub/local_runner.py). When that ran, the
    # caller must not also write a legacy aggregate entry — same events would
    # double-count usage. Runners that predate per-turn recording (no
    # ``provider_id`` parameter) still need the caller's legacy aggregate call.
    ledger_recorded_per_turn = False
    try:
        complete_with_tools = getattr(runner, "complete_with_tools", None)
        if use_tools and callable(complete_with_tools):
            turn_guard = _build_turn_guard(
                root,
                runner=runner,
                allow_cloud=allow_cloud,
                cancel=cancel,
                guard=guard,
            )
            completed = _call_tool_loop(
                complete_with_tools,
                task,
                root=root,
                allow_edits=allow_edits,
                # Same reason as the non-tool path: an editing run is exactly
                # where the project's house rules matter most, and this is the
                # path free-tier coding actually takes.
                system=build_system_prompt(SYSTEM_PROMPT, root),
                cancel=cancel,
                guard=turn_guard,
                allow_command=allow_command,
                tool_loop_policy=tool_loop_policy,
                repository_handle=repository_handle,
                provider_id=provider_id,
            )
            # A runner accepting provider_id is one that self-records per-turn
            # ledger entries (opaihub/local_runner.py); a runner without it
            # (predates Task 6/7) still needs the caller's legacy aggregate call.
            try:
                ledger_recorded_per_turn = (
                    "provider_id" in inspect.signature(complete_with_tools).parameters
                )
            except (TypeError, ValueError):
                ledger_recorded_per_turn = False
            answer = str(completed.get("text") or "")
            tool_trace = list(completed.get("tool_trace") or [])
            stopped_reason = str(completed.get("stopped_reason") or "")
            last_error = str(completed.get("last_error") or "")
            approval = _extract_command_approval(completed)
            completion_state = str(completed.get("completion_state") or "")
            if not completion_state:
                # Derive completion from what actually happened (F8): an
                # approval request is a consent stop; a real answer completes;
                # an empty no-op is an honest failure, never "completed".
                if approval is not None:
                    completion_state = "needs_consent"
                elif stopped_reason:
                    completion_state = ""  # canonical mapping reads stopped_reason
                elif answer.strip():
                    completion_state = "completed"
                else:
                    completion_state = "failed"
            blocked_reason = str(completed.get("blocked_reason") or "")
            streamed = False
        elif allow_edits and not callable(complete_with_tools):
            return {
                **base,
                "status": "capability_mismatch",
                "error": "The selected provider cannot edit repository files.",
            }
        else:
            answer, streamed = _complete_streaming(
                runner, task, cancel=cancel, on_text=on_text
            )
            tool_trace = []
            stopped_reason = ""
            last_error = ""
            # F8: a single-shot free run that produced nothing is not "done".
            completion_state = "completed" if answer.strip() else "failed"
    except LocalRunCancelled:
        return {**base, "status": "cancelled", "answer": ""}
    except Exception as exc:  # noqa: BLE001 - normalize provider failures upstream
        return {**base, "status": "runner_error", "error": str(exc)}
    if record:
        _record(root, task, "L2", cache_hit=False)
    result = {
        **base,
        "status": "answered_locally",
        "free": True,
        "source": "explicit_model",
        "runner": runner.name,
        "model": runner.model,
        "mode": mode,
        "answer": answer,
        "tool_trace": tool_trace,
        # The runner already streamed the answer to on_text (#154); the caller
        # must not re-emit it as one block.
        "streamed": streamed,
        # Typed terminal for a run the tool loop could not finish: empty on a
        # clean completion, else the controller's honest stopped_reason. Callers
        # can detect a non-success run without a new status to special-case.
        "stopped_reason": stopped_reason,
        "last_error": last_error,
        # Canonical completion truth (Task 6): only "completed" is success; a
        # guard-blocked turn also carries the ProviderBlockedReason.
        "completion_state": completion_state,
        "blocked_reason": blocked_reason,
        # Task 6/7: tells the caller whether per-turn ledger events already
        # cover this run's usage, so it does not also write a legacy aggregate
        # entry (which would double-count against the same ledger event type).
        "ledger_recorded_per_turn": ledger_recorded_per_turn,
    }
    if approval is not None:
        result["command_approval"] = approval
    return result


def _build_turn_guard(
    project_root: Path,
    *,
    runner: LocalRunner,
    allow_cloud: bool,
    cancel: Any,
    guard: Any = None,
) -> Any:
    """Return a ``(turn_index) -> GuardDecision`` callable for the tool loop.

    Explicit runs here are free/local ($0), so the guard chiefly enforces
    cancellation, panic mode, and paid/cloud consent per turn. A caller may
    inject its own guard callable (used as-is); otherwise a default
    :class:`ExecutionGuard` is wrapped with a per-turn context.
    """

    if callable(guard):
        return guard
    from opaihub.execution_guard import ExecutionGuard, ExecutionGuardContext

    engine = guard if isinstance(guard, ExecutionGuard) else ExecutionGuard()
    provider_type = "local" if getattr(runner, "name", "") != "free-api" else "free_api"

    def _guard(turn_index: int) -> Any:
        return engine.check(
            ExecutionGuardContext(
                project_root=project_root,
                turn_index=turn_index,
                tier="L2",
                provider_type=provider_type,
                is_free=True,
                estimated_cost_usd=0.0,
                allow_cloud=allow_cloud,
                cancel=cancel,
            )
        )

    return _guard


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
