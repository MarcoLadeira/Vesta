"""Boundary projector: build the canonical RunResult from evidence a caller
already computed, so every surface attaches one result instead of deciding
completion truth again from its own slice of the same facts (#612, #618).

This module never re-derives evidence. ``evaluate_completion`` (#539) already
decided whether a run's objective was met from evidence OPai itself observed;
a completion verdict *is* the verification act, not raw provider prose. What
this module adds is turning that verdict, plus whatever durable evidence
reference the caller already holds for delivery and cost, into the one
schema-valid envelope every surface can serialize identically.

Missing or invalid evidence degrades to ``needs_attention`` rather than
raising — the same contract ``legacy_status.py`` uses for imported legacy
data — so a boundary integration mistake surfaces as an honestly degraded
result for one turn, never a crash or a silently invented completion.
"""

from __future__ import annotations

from typing import Any, Mapping

from .completion import CompletionVerdictResult
from .generated_lifecycle import TERMINAL_STATE_IDS
from .run_result import RunResult


def _record_ref(kind: str, identifier: str) -> dict[str, str]:
    return {"kind": kind[:64], "id": identifier[:500]}


def project_run_result(
    *,
    verdict: CompletionVerdictResult,
    final_transition_at: str,
    mutating: bool,
    task_id: str = "",
    run_id: str = "",
    delivery_ref: tuple[str, str] | None = None,
    economics_ref: tuple[str, str] | None = None,
    automatic_retry: bool = False,
    retry_reason: str = "none",
    provider: Mapping[str, Any] | None = None,
    authority: Mapping[str, Any] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> RunResult:
    """Construct the canonical RunResult for one reconciled, verdict-bearing turn.

    ``delivery_ref``/``economics_ref`` are ``(kind, id)`` pairs naming durable
    evidence the caller already holds (a receipt digest, a ledger event id, a
    background run's own persisted record) — this function does not invent a
    reference; a caller with no such evidence should pass ``None`` and accept
    the resulting degradation rather than fabricate one.
    """

    if not isinstance(verdict, CompletionVerdictResult):
        raise TypeError("verdict must be a CompletionVerdictResult")
    state = verdict.verdict.value
    identity: dict[str, Any] = {}
    if task_id:
        identity["task_id"] = task_id
    if run_id:
        identity["run_id"] = run_id

    verification: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None
    economics: dict[str, Any] | None = None
    if state == "completed":
        verification = {
            "applicable": mutating,
            "verdict": "verified" if mutating else "not_applicable",
        }
        if mutating:
            verification["record_ref"] = _record_ref(
                "completion_verdict", verdict.reason_code
            )
        if delivery_ref is not None:
            delivery = {
                "applicable": True,
                "verdict": "delivered",
                "record_ref": _record_ref(*delivery_ref),
            }
        if economics_ref is not None:
            economics = {
                "integrity": "reconciled",
                "record_ref": _record_ref(*economics_ref),
            }

    try:
        return RunResult.from_payload(
            state=state,
            reason_detail=verdict.reason[:500] or verdict.reason_code,
            final_transition_at=final_transition_at,
            mutating=mutating,
            identity=identity,
            provider=provider,
            recovery={"automatic_retry": automatic_retry, "reason": retry_reason},
            verification=verification,
            delivery=delivery,
            economics=economics,
            authority=authority,
            diagnostics=diagnostics,
        )
    except (TypeError, ValueError) as exc:
        return RunResult.from_payload(
            state="needs_attention",
            reason_detail=f"Run result evidence was incomplete: {exc}",
            final_transition_at=final_transition_at,
            mutating=mutating,
            identity=identity,
            recovery={"automatic_retry": False, "reason": "manual_review"},
        )


def project_run_result_for_background_run(
    *,
    verdict: CompletionVerdictResult,
    final_transition_at: str,
    mutating: bool,
    task_id: str,
    run_id: str,
    automatic_retry: bool = False,
    retry_reason: str = "none",
) -> RunResult:
    """A background run's own persisted record is its delivery/cost evidence.

    The record at ``.opaihub/agent/background/runs/<run_id>.json`` (#176) is
    already the durable reconciliation of what happened, cost included — it
    is not a separate claim invented here, only referenced.
    """

    return project_run_result(
        verdict=verdict,
        final_transition_at=final_transition_at,
        mutating=mutating,
        task_id=task_id,
        run_id=run_id,
        delivery_ref=("background_run", run_id),
        economics_ref=("background_run", run_id),
        automatic_retry=automatic_retry,
        retry_reason=retry_reason,
    )


def canonical_run_state(run_result: Any) -> str:
    """The terminal lifecycle state of a canonical RunResult, or ``""``.

    #618: the one way a consumer is allowed to learn what a finished turn
    means. Surfaces previously each reached into ``completion_verdict`` (or
    worse, a legacy status string) and decided for themselves, which is how the
    same evidence could read `complete` in history and `partial` in the GUI.

    Returns ``""`` rather than guessing when the payload carries no canonical
    result -- an old record, or a turn that ended before the projection ran.
    The caller then falls back to its documented compatibility path; it does
    not get a fabricated state from here.
    """

    if isinstance(run_result, RunResult):
        payload: Any = run_result.to_dict()
    else:
        payload = run_result
    if not isinstance(payload, Mapping):
        return ""
    lifecycle = payload.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        return ""
    state = str(lifecycle.get("state") or "").strip().lower()
    return state if state in TERMINAL_STATE_IDS else ""
