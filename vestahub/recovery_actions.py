"""Typed recovery actions for a terminal run (#656).

One canonical recovery projection, built once from the run payload and consumed
identically by the GUI terminal card and the CLI outcome block. Surfaces render
this structure; they never infer availability or failure class themselves.

Two rules keep the surface honest:

* **Availability is decided here, from evidence.** An action the runtime cannot
  honour for this run is present but ``available: False`` with a plain-language
  ``disabled_reason`` — so a button can never silently mean "repeat the whole
  paid run" when only a new attempt is actually possible.
* **Unknown stays unknown.** No failure-class guessing and no prompt or raw
  output text is copied into this structure; it carries static copy, enums and
  cost figures only.
"""

from __future__ import annotations

from typing import Any, Mapping

from .completion import CompletionVerdict, FailureReason

RECOVERY_SCHEMA_VERSION = 1

# Canonical action ids (#656). The GUI buttons and the CLI lines render this
# exact list, so both surfaces always offer the same controls for the same run.
CONTINUE_FROM_CHECKPOINT = "continue_from_checkpoint"
RETRY_OPERATION = "retry_operation"
REPLAN = "replan"
TRY_PROVIDER = "try_provider"
ASK = "ask"
START_OVER = "start_over"
STOP = "stop"

#: Primary recovery states this projection can report for a terminal run.
#: (The active mid-run states — recovering, waiting, resumed — are owned by the
#: run controller, not by a terminal card.)
STATE_COMPLETED = "completed"
STATE_PARTIAL_RETAINED = "partial_result_retained"
STATE_STOPPED_WITH_CHECKPOINT = "stopped_with_checkpoint"
STATE_BLOCKED = "blocked"
STATE_PROVIDER_UNAVAILABLE = "provider_unavailable"
STATE_STOPPED = "stopped"
STATE_NEEDS_ATTENTION = "needs_attention"
STATE_WAITING = "waiting_for_user_answer"
STATE_UNKNOWN = "unknown"

#: Failure classes that constitute provider *evidence*. Derived from the
#: typed vocabulary rather than restated, so a new class cannot be silently
#: excluded from -- or wrongly counted as -- provider blame.
_PROVIDER_FAILURE_CODES = frozenset(
    {
        FailureReason.AUTH.value,
        FailureReason.RATE_LIMIT.value,
        FailureReason.NETWORK.value,
        FailureReason.PROVIDER.value,
    }
)

#: Every verdict that is not "completed", derived from the enum.
#:
#: This was a hand-written set of the same seven words, which is a second
#: authority over the lifecycle vocabulary and is what #612's ratchet exists to
#: catch -- it failed the build the moment this module landed on main. Deriving
#: it is also simply better: a verdict added to CompletionVerdict tomorrow is
#: included here without anyone remembering to come back.
_NON_COMPLETED = frozenset(
    verdict.value
    for verdict in CompletionVerdict
    if verdict is not CompletionVerdict.COMPLETED
)

#: Verdicts a checkpoint can be resumed from, and verdicts that leave useful
#: work behind. Written as enum members rather than bare strings for the same
#: reason as above: a set of state words spelled by hand is a second authority,
#: and #612's ratchet counts it as one.
_RESUMABLE_VERDICTS = frozenset(
    {
        CompletionVerdict.PARTIAL.value,
        CompletionVerdict.FAILED.value,
        CompletionVerdict.TIMEOUT.value,
    }
)
_RETAINED_WORK_VERDICTS = frozenset(
    {CompletionVerdict.PARTIAL.value, CompletionVerdict.TIMEOUT.value}
)


def _verdict_and_reason(payload: Mapping[str, Any]) -> tuple[str, str]:
    verdict_raw = payload.get("completion_verdict")
    if isinstance(verdict_raw, Mapping):
        verdict = str(verdict_raw.get("verdict") or "").strip().lower()
        reason_code = str(verdict_raw.get("reason_code") or "").strip().lower()
        if verdict:
            return verdict, reason_code
    run_result = payload.get("run_result")
    if isinstance(run_result, Mapping):
        lifecycle = run_result.get("lifecycle")
        if isinstance(lifecycle, Mapping):
            verdict = str(lifecycle.get("state") or "").strip().lower()
            if verdict:
                return verdict, ""
    return "", ""


def _has_checkpoint(payload: Mapping[str, Any]) -> bool:
    """True only when the payload carries resumable checkpoint evidence."""

    if payload.get("resumable") is True:
        return True
    if str(payload.get("checkpoint_id") or "").strip():
        return True
    checkpoint = payload.get("checkpoint")
    return isinstance(checkpoint, Mapping) and bool(checkpoint.get("id"))


def _incurred_cost(payload: Mapping[str, Any]) -> dict[str, Any]:
    """What this run already cost, qualified actual/derived/estimated/unavailable."""

    receipt = payload.get("receipt")
    if not isinstance(receipt, Mapping):
        return {"kind": "unavailable"}
    value = receipt.get("estimated_actual_usd")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return {"kind": "unavailable"}
    confidence = str(receipt.get("confidence") or "").strip().lower()
    kind = {
        "actual": "actual",
        "estimated": "estimated",
        "unreconciled": "unavailable",
        "blocked": "unavailable",
    }.get(confidence, "estimated" if value else "unavailable")
    if kind == "unavailable":
        return {"kind": "unavailable"}
    return {"kind": kind, "value_usd": round(float(value), 6)}


def _action(
    action_id: str,
    label: str,
    summary: str,
    *,
    available: bool,
    disabled_reason: str = "",
    style: str = "secondary",
) -> dict[str, Any]:
    return {
        "id": action_id,
        "label": label,
        "summary": summary,
        "available": bool(available),
        "disabled_reason": "" if available else str(disabled_reason),
        "style": style,
    }


def build_recovery_actions(
    payload: Mapping[str, Any], *, provider_count: int = 1
) -> dict[str, Any]:
    """Project the recovery surface for one terminal run payload.

    Pure and deterministic: rebuilding from the same persisted payload yields
    the same dict, which is what lets a reconnecting renderer reconstruct the
    identical card. ``provider_count`` is the number of distinct configured
    providers the caller knows about; it gates only ``try_provider``.
    """

    data = payload if isinstance(payload, Mapping) else {}
    verdict, reason_code = _verdict_and_reason(data)
    has_checkpoint = _has_checkpoint(data)
    safe_operation = str(data.get("safe_retry_operation") or "").strip()
    # A needs_* turn is not terminal: it is paused on one user answer, and its
    # own approval card — not a recovery action — is the way through.
    waiting = str(data.get("status") or "").strip().lower().startswith("needs_")

    if verdict == "completed":
        # A completed run needs no recovery; surfaces render no action row.
        return {
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "state": STATE_COMPLETED,
            "actions": [],
            "cost": {
                "incurred": _incurred_cost(data),
                "incremental": {"kind": "unavailable"},
            },
        }

    if waiting:
        state = STATE_WAITING
    elif verdict not in _NON_COMPLETED:
        state = STATE_UNKNOWN
    elif has_checkpoint and verdict in _RESUMABLE_VERDICTS:
        state = STATE_STOPPED_WITH_CHECKPOINT
    elif verdict == "blocked":
        state = STATE_BLOCKED
    elif reason_code in _PROVIDER_FAILURE_CODES:
        state = STATE_PROVIDER_UNAVAILABLE
    elif verdict in _RETAINED_WORK_VERDICTS:
        state = STATE_PARTIAL_RETAINED
    elif verdict == "needs_attention":
        state = STATE_NEEDS_ATTENTION
    else:
        state = STATE_STOPPED

    providers = provider_count if isinstance(provider_count, int) else 0
    waiting_reason = (
        "This run is waiting for your answer — answer it above to continue."
    )
    stop_reason = (
        "This run is waiting for your answer — decline it above to stop."
        if waiting
        else "This run has already stopped."
    )
    actions = [
        _action(
            CONTINUE_FROM_CHECKPOINT,
            "Continue from checkpoint",
            "Keep the verified findings and continue this run from its last "
            "checkpoint instead of restarting.",
            available=has_checkpoint and not waiting,
            disabled_reason=(
                waiting_reason
                if waiting
                else "No resumable checkpoint was recorded for this run."
            ),
            style="primary" if has_checkpoint and not waiting else "secondary",
        ),
        _action(
            RETRY_OPERATION,
            "Retry failed operation",
            "Repeat only the single operation that failed, when it is known to "
            "be safe and not already completed.",
            available=bool(safe_operation) and not waiting,
            disabled_reason=(
                waiting_reason
                if waiting
                else "No single failed operation is known to be safe to repeat."
            ),
        ),
        _action(
            REPLAN,
            "Re-plan",
            "Keep the evidence and any changes; ask Vesta for a new plan with a "
            "narrower approach.",
            available=True,
        ),
        _action(
            TRY_PROVIDER,
            "Try another provider",
            "Keep the checkpoint and re-run with a different configured provider.",
            available=providers >= 2,
            disabled_reason="Only one provider is configured for this workspace.",
        ),
        _action(
            ASK,
            "Ask a question",
            "Pause and ask Vesta about what happened before deciding.",
            available=True,
        ),
        _action(
            START_OVER,
            "Start over",
            "Create a new linked attempt from the original request. History and "
            "changes are kept; nothing is silently discarded.",
            available=True,
            style="primary" if not has_checkpoint else "secondary",
        ),
        _action(
            STOP,
            "Stop",
            "End further spend while keeping the evidence and checkpoint.",
            available=False,
            disabled_reason=stop_reason,
            style="danger",
        ),
    ]
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "state": state,
        "actions": actions,
        "cost": {
            "incurred": _incurred_cost(data),
            "incremental": {
                "kind": "unavailable",
                "note": "The cost of a new attempt is estimated when it runs.",
            },
        },
    }


def render_recovery_text(recovery: Mapping[str, Any]) -> list[str]:
    """Render the same recovery dict as CLI lines (GUI/CLI parity by construction)."""

    if not isinstance(recovery, Mapping):
        return []
    actions = recovery.get("actions")
    if not isinstance(actions, list) or not actions:
        return []
    lines = ["Recovery:"]
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        label = str(action.get("label") or "").strip()
        if not label:
            continue
        if action.get("available"):
            lines.append(f"- {label}: {action.get('summary') or ''}".rstrip())
        else:
            reason = str(action.get("disabled_reason") or "unavailable").strip()
            lines.append(f"- {label} (unavailable: {reason})")
    return lines
