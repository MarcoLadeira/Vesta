"""Boundary projector: build the canonical RunResult from evidence a caller
already computed, so every surface attaches one result instead of deciding
completion truth again from its own slice of the same facts (#612, #618).

This module never re-derives evidence. ``evaluate_completion`` (#539) already
decided whether a run's objective was met from evidence Vesta itself observed;
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

import math
from typing import Any, Mapping

from .completion import CompletionVerdictResult
from .run_result import RunResult
from .run_state import RunState, canonical_for_cancel_phase
from .boundary_errors import safe_detail


def _record_ref(kind: str, identifier: str) -> dict[str, str]:
    return {"kind": kind[:64], "id": identifier[:500]}


def _latency_seconds(value: Any) -> float | None:
    """A recorded latency, or None — never a bool, NaN, infinity, or negative."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _cancellation_projection(
    cancellation: Mapping[str, Any] | None,
) -> tuple[str, dict[str, str] | None, dict[str, Any]]:
    if not isinstance(cancellation, Mapping):
        return "", None, {}
    phase = str(cancellation.get("phase") or "").strip().lower()
    scope_id = str(cancellation.get("scope_id") or "").strip()
    reference = _record_ref("cancellation_journal", scope_id) if scope_id else None
    raw_metrics = cancellation.get("metrics")
    raw_metrics = raw_metrics if isinstance(raw_metrics, Mapping) else {}
    # #666: the latencies #380 names, computed from the journal's recorded
    # timestamps by `CancelMetrics`, are part of the canonical account of a
    # proven stop — support reads them here instead of re-deriving them.
    metrics = {
        "forced": raw_metrics.get("forced") is True,
        "acknowledgement_latency_seconds": _latency_seconds(
            raw_metrics.get("acknowledgement_latency_seconds")
        ),
        "hard_stop_latency_seconds": _latency_seconds(
            raw_metrics.get("hard_stop_latency_seconds")
        ),
    }
    return phase, reference, metrics


def _timeout_projection(
    timeout: Mapping[str, Any] | None,
    timeout_ref: tuple[str, str] | None,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    if not isinstance(timeout, Mapping) or not timeout:
        return None, None
    reference = _record_ref(*timeout_ref) if timeout_ref is not None else None
    projected = {
        "origin": str(timeout.get("timeout_origin") or "unknown_timeout")[:64],
        "owner": str(timeout.get("owner") or "unknown")[:64],
        "provider_condition": str(timeout.get("provider_condition") or "unknown")[:64],
        "retry_safety": str(timeout.get("retry_safety") or "reconcile_before_retry")[
            :64
        ],
    }
    if reference is not None:
        projected["record_ref"] = reference
    return projected, reference


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
    cancellation: Mapping[str, Any] | None = None,
    timeout: Mapping[str, Any] | None = None,
    timeout_ref: tuple[str, str] | None = None,
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
    reason_detail = verdict.reason[:500] or verdict.reason_code
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

    authority_value = dict(authority or {})
    diagnostics_value = dict(diagnostics or {})
    cancellation_phase, cancellation_ref, cancellation_metrics = (
        _cancellation_projection(cancellation)
    )
    if state == "cancelled":
        try:
            teardown_proven = (
                canonical_for_cancel_phase(cancellation_phase) is RunState.CANCELLED
            )
        except ValueError:
            teardown_proven = False
        if not teardown_proven or cancellation_ref is None:
            state = "needs_attention"
            reason_detail = (
                "Cancellation was requested, but provider teardown was not "
                "proven complete. Reconcile the prior operation before retrying."
            )
            automatic_retry = False
            retry_reason = "manual_review"
        else:
            authority_value["cancellation"] = {
                "phase": cancellation_phase,
                "record_ref": cancellation_ref,
                **cancellation_metrics,
            }
            refs = list(diagnostics_value.get("record_refs") or [])
            if cancellation_ref not in refs:
                refs.append(cancellation_ref)
            diagnostics_value["record_refs"] = refs
            codes = list(diagnostics_value.get("codes") or [])
            if "cancellation_terminated" not in codes:
                codes.append("cancellation_terminated")
            diagnostics_value["codes"] = codes

    timeout_value, projected_timeout_ref = _timeout_projection(timeout, timeout_ref)
    if timeout_value is not None and state == "timeout":
        authority_value["timeout"] = timeout_value
        refs = list(diagnostics_value.get("record_refs") or [])
        if projected_timeout_ref is not None and projected_timeout_ref not in refs:
            refs.append(projected_timeout_ref)
        diagnostics_value["record_refs"] = refs
        codes = list(diagnostics_value.get("codes") or [])
        timeout_code = str(timeout_value["origin"])
        if timeout_code not in codes:
            codes.append(timeout_code)
        diagnostics_value["codes"] = codes
        if timeout_code == "task_deadline":
            automatic_retry = False
            retry_reason = "manual_review"

    try:
        return RunResult.from_payload(
            state=state,
            reason_detail=reason_detail,
            final_transition_at=final_transition_at,
            mutating=mutating,
            identity=identity,
            provider=provider,
            recovery={"automatic_retry": automatic_retry, "reason": retry_reason},
            verification=verification,
            delivery=delivery,
            economics=economics,
            authority=authority_value,
            diagnostics=diagnostics_value,
        )
    except (TypeError, ValueError) as exc:
        return RunResult.from_payload(
            state="needs_attention",
            reason_detail=f"Run result evidence was incomplete: {safe_detail(exc)}",
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

    The record at ``.vestahub/agent/background/runs/<run_id>.json`` (#176) is
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
