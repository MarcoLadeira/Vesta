"""Canonical, provider-independent completion truth.

Legacy runner and UI status strings remain supported during the alpha, but
only :class:`CompletionState.COMPLETED` is allowed to map to an answered
status.  In particular, a legacy payload carrying a stop reason is never
silently upgraded to success.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any, Mapping


class CompletionState(str, Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NEEDS_USER_INPUT = "needs_user_input"
    NEEDS_CONSENT = "needs_consent"
    RETRYABLE_PROVIDER_ERROR = "retryable_provider_error"
    PROVIDER_BLOCKED = "provider_blocked"
    STUCK_NO_PROGRESS = "stuck_no_progress"
    FAILED = "failed"


class ProviderBlockedReason(str, Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    BILLING = "billing"
    PANIC = "panic"
    DAILY_CAP = "daily_cap"
    MONTHLY_CAP = "monthly_cap"
    TASK_CAP = "task_cap"


class AcceptanceRequirement(str, Enum):
    """Machine-checkable evidence required before a run may be completed."""

    ANSWER_PRESENT = "answer_present"
    EXPECTED_EDIT = "expected_edit"
    TESTS_PASS = "tests_pass"


class CompletionVerdict(str, Enum):
    """User-visible terminal truth, independent from legacy runner status."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class ObjectiveRecord:
    """The declared goal and its deterministic acceptance requirements."""

    objective_text: str
    mode: str
    acceptance: tuple[AcceptanceRequirement, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        text = str(self.objective_text or "").strip()
        if not text:
            raise ValueError("objective_text is required")
        if len(text) > 20_000:
            raise ValueError("objective_text exceeds the safety limit")
        object.__setattr__(self, "objective_text", text)
        object.__setattr__(self, "mode", _normalized(self.mode) or "explain")
        requirements: list[AcceptanceRequirement] = []
        for requirement in self.acceptance:
            typed = (
                requirement
                if isinstance(requirement, AcceptanceRequirement)
                else AcceptanceRequirement(str(requirement))
            )
            if typed not in requirements:
                requirements.append(typed)
        if not requirements:
            raise ValueError("objective acceptance is required")
        object.__setattr__(self, "acceptance", tuple(requirements))
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "objective_text": self.objective_text,
            "mode": self.mode,
            "acceptance": [item.value for item in self.acceptance],
        }


@dataclass(frozen=True)
class EvidenceRef:
    """A bounded reference to evidence OPai observed, never self-attestation."""

    kind: str
    summary: str

    def __post_init__(self) -> None:
        kind = _normalized(self.kind)
        summary = str(self.summary or "").strip()
        if not kind:
            raise ValueError("evidence kind is required")
        if not summary:
            raise ValueError("evidence summary is required")
        object.__setattr__(self, "kind", kind[:64])
        object.__setattr__(self, "summary", summary[:500])

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "summary": self.summary}


@dataclass(frozen=True)
class CompletionVerdictResult:
    """The sole verdict producer consumed by all OPai surfaces."""

    verdict: CompletionVerdict
    reason_code: str
    reason: str
    objective: ObjectiveRecord
    evidence: tuple[EvidenceRef, ...] = ()
    next_action: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, CompletionVerdict):
            object.__setattr__(self, "verdict", CompletionVerdict(str(self.verdict)))
        code = _normalized(self.reason_code)
        if not code:
            raise ValueError("reason_code is required")
        object.__setattr__(self, "reason_code", code[:120])
        reason = str(self.reason or "").strip()
        if not reason:
            raise ValueError("reason is required")
        object.__setattr__(self, "reason", reason[:1_000])
        if not isinstance(self.objective, ObjectiveRecord):
            raise TypeError("objective must be an ObjectiveRecord")
        refs: list[EvidenceRef] = []
        for evidence in self.evidence:
            typed = (
                evidence
                if isinstance(evidence, EvidenceRef)
                else EvidenceRef(**evidence)
            )
            if typed not in refs:
                refs.append(typed)
        object.__setattr__(self, "evidence", tuple(refs))
        object.__setattr__(
            self, "next_action", str(self.next_action or "").strip()[:500]
        )
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer")

    def to_dict(self, *, include_objective_text: bool = True) -> dict[str, Any]:
        objective = self.objective.to_dict()
        if not include_objective_text:
            objective.pop("objective_text", None)
        return {
            "schema_version": self.schema_version,
            "verdict": self.verdict.value,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "objective": objective,
            "evidence": [item.to_dict() for item in self.evidence],
            "next_action": self.next_action,
        }


# Stop reasons that are honestly a timeout, not a generic failure or a stuck
# no-progress run (#402). The account path emits "timeout"; the tool-loop
# controller emits "controller_timeout" when it exceeds max_active_seconds.
# Both must surface the TIMEOUT verdict end-to-end even though the tool-loop
# controller keeps STUCK_NO_PROGRESS as its canonical legacy state.
_TIMEOUT_STOP_REASONS = frozenset({"timeout", "controller_timeout"})
_TIMEOUT_STATUSES = frozenset({"timeout", "account_timeout"})

_EDIT_MODES = frozenset({"implement", "ship", "build", "edit", "fix"})
_TEST_REQUEST = re.compile(r"\b(?:test|tests|testing|verify|verification|ci)\b", re.I)


def objective_from_request(objective_text: str, *, mode: str) -> ObjectiveRecord:
    """Create the run objective without trusting a provider's claimed success."""

    normalized_mode = _normalized(mode) or "explain"
    if normalized_mode in _EDIT_MODES:
        acceptance: list[AcceptanceRequirement] = [AcceptanceRequirement.EXPECTED_EDIT]
        if normalized_mode == "ship" or _TEST_REQUEST.search(str(objective_text)):
            acceptance.append(AcceptanceRequirement.TESTS_PASS)
    else:
        acceptance = [AcceptanceRequirement.ANSWER_PRESENT]
    return ObjectiveRecord(
        objective_text=objective_text,
        mode=normalized_mode,
        acceptance=tuple(acceptance),
    )


def _has_successful_test(payload: Mapping[str, Any]) -> bool:
    tests = payload.get("tests") or payload.get("test_results")
    if isinstance(tests, Mapping):
        status = _normalized(tests.get("status") or tests.get("result"))
        if status in {"passed", "pass", "success", "successful"}:
            return True
    for item in payload.get("tool_trace") or ():
        if not isinstance(item, Mapping):
            continue
        tool = _normalized(item.get("tool") or item.get("type"))
        status = _normalized(item.get("status"))
        if tool in {"run_tests", "test", "tests"} and (
            item.get("ok") is True or status in {"success", "passed"}
        ):
            return True
    return False


def _evidence_from_payload(payload: Mapping[str, Any]) -> tuple[EvidenceRef, ...]:
    refs: list[EvidenceRef] = []
    files = [
        str(item).strip()
        for item in payload.get("changed_files") or ()
        if str(item).strip()
    ]
    if files:
        refs.append(EvidenceRef("diff", f"{len(files)} file(s) changed"))
    diff = payload.get("diff_review")
    if not refs and isinstance(diff, Mapping):
        summary = diff.get("summary")
        if isinstance(summary, Mapping) and int(summary.get("files_changed") or 0) > 0:
            refs.append(
                EvidenceRef("diff", f"{int(summary['files_changed'])} file(s) changed")
            )
    if _has_successful_test(payload):
        refs.append(EvidenceRef("tests", "Repository tests passed"))
    answer = str(payload.get("answer") or "").strip()
    if answer:
        refs.append(EvidenceRef("answer", "Provider returned a non-empty response"))
    return tuple(refs)


def _verdict(
    verdict: CompletionVerdict,
    code: str,
    reason: str,
    objective: ObjectiveRecord,
    evidence: tuple[EvidenceRef, ...],
    next_action: str,
) -> CompletionVerdictResult:
    return CompletionVerdictResult(
        verdict=verdict,
        reason_code=code,
        reason=reason,
        objective=objective,
        evidence=evidence,
        next_action=next_action,
    )


def evaluate_completion(
    objective: ObjectiveRecord, payload: Mapping[str, Any] | None
) -> CompletionVerdictResult:
    """Evaluate terminal truth from objective + observed evidence.

    This function deliberately ignores provider prose such as "done".  Only
    a canonical terminal state and evidence produced by OPai's execution path
    can return :attr:`CompletionVerdict.COMPLETED`.
    """

    result = payload if isinstance(payload, Mapping) else {}
    evidence = _evidence_from_payload(result)
    if AcceptanceRequirement.ANSWER_PRESENT not in objective.acceptance:
        evidence = tuple(item for item in evidence if item.kind != "answer")
    stopped_reason = _normalized(result.get("stopped_reason"))
    status = _normalized(result.get("status"))
    canonical = completion_state_from_legacy(result)

    if canonical is CompletionState.CANCELLED:
        return _verdict(
            CompletionVerdict.CANCELLED,
            stopped_reason or "cancel_requested",
            "Stopped by you before OPai could verify the objective.",
            objective,
            evidence,
            "Retry when you are ready.",
        )
    if stopped_reason in _TIMEOUT_STOP_REASONS or status in _TIMEOUT_STATUSES:
        return _verdict(
            CompletionVerdict.TIMEOUT,
            "timeout",
            "The run timed out before OPai could verify the objective.",
            objective,
            evidence,
            "Retry with a narrower task or a longer timeout.",
        )
    if status == "capability_mismatch":
        return _verdict(
            CompletionVerdict.BLOCKED,
            "capability_mismatch",
            "The selected provider cannot perform this task.",
            objective,
            evidence,
            "Choose a provider with the required capability.",
        )
    if status.startswith("needs_"):
        return _verdict(
            CompletionVerdict.BLOCKED,
            "permission_required",
            "OPai needs your approval or input before it can verify the objective.",
            objective,
            evidence,
            "Resolve the requested approval or input, then retry.",
        )
    if canonical in {
        CompletionState.PROVIDER_BLOCKED,
        CompletionState.NEEDS_CONSENT,
        CompletionState.NEEDS_USER_INPUT,
    }:
        code = (
            "permission_required"
            if canonical is CompletionState.NEEDS_CONSENT
            else "input_required"
            if canonical is CompletionState.NEEDS_USER_INPUT
            else "provider_blocked"
        )
        return _verdict(
            CompletionVerdict.BLOCKED,
            code,
            "OPai is blocked before it can verify the objective.",
            objective,
            evidence,
            "Resolve the blocker and retry.",
        )
    if canonical is not CompletionState.COMPLETED:
        return _verdict(
            CompletionVerdict.FAILED,
            "provider_failed",
            "The provider failed before OPai could verify the objective.",
            objective,
            evidence,
            "Retry the run.",
        )

    kinds = {item.kind for item in evidence}
    if (
        AcceptanceRequirement.EXPECTED_EDIT in objective.acceptance
        and "diff" not in kinds
    ):
        return _verdict(
            CompletionVerdict.PARTIAL,
            "change_not_verified",
            "OPai received a response but no changed-file or diff evidence verifies the requested edit.",
            objective,
            evidence,
            "Review the tool trace or ask OPai to apply the change.",
        )
    if (
        AcceptanceRequirement.TESTS_PASS in objective.acceptance
        and "tests" not in kinds
    ):
        return _verdict(
            CompletionVerdict.PARTIAL,
            "tests_not_verified",
            "Edits were observed, but required tests were not verified as passing.",
            objective,
            evidence,
            "Run the relevant tests and retry verification.",
        )
    if (
        AcceptanceRequirement.ANSWER_PRESENT in objective.acceptance
        and "answer" not in kinds
    ):
        return _verdict(
            CompletionVerdict.PARTIAL,
            "answer_missing",
            "The run ended without a response that verifies the answer-only objective.",
            objective,
            evidence,
            "Retry the request.",
        )
    return _verdict(
        CompletionVerdict.COMPLETED,
        "objective_verified",
        "Objective verified from OPai-observed evidence.",
        objective,
        evidence,
        "Review the attached evidence.",
    )


_ANSWERED_STATUSES = {
    "answered",
    "answered_by_account",
    "answered_by_free_api",
    "answered_locally",
    "cache_hit",
    "done",
    "completed",
}
_CANCELLED_STATUSES = {"cancelled", "canceled", "user_cancelled", "aborted"}
_USER_INPUT_STATUSES = {"needs_user_input", "needs_input", "question"}
_CONSENT_STATUSES = {
    "needs_confirmation",
    "needs_paid_confirmation",
    "needs_consent",
    "read_only",
}
_RETRYABLE_STATUSES = {
    "retryable_provider_error",
    "provider_unavailable",
    "temporarily_unavailable",
    "timeout",
}
_BLOCKED_STATUSES = {"provider_blocked", "blocked"}
_STUCK_STATUSES = {"incomplete", "stuck", "stuck_no_progress"}

_CANCELLED_REASONS = _CANCELLED_STATUSES | {"cancel_requested"}
_USER_INPUT_REASONS = _USER_INPUT_STATUSES
_CONSENT_REASONS = _CONSENT_STATUSES | {"consent_required", "approval_required"}
_RETRYABLE_REASONS = _RETRYABLE_STATUSES | {
    "transient_error",
    "network_error",
    "connection_error",
}
_BLOCKED_REASONS = {reason.value for reason in ProviderBlockedReason}
_STUCK_REASONS = _STUCK_STATUSES | {
    "tool_budget_exhausted",
    "repeated_failure",
    "repeated_success",
    "no_progress",
    "exploration_limit",
    "controller_timeout",
}


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError("checkpoint values must be JSON-compatible")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _state_for_stop_reason(reason: str) -> CompletionState:
    if reason in _CANCELLED_REASONS:
        return CompletionState.CANCELLED
    if reason in _USER_INPUT_REASONS:
        return CompletionState.NEEDS_USER_INPUT
    if reason in _CONSENT_REASONS:
        return CompletionState.NEEDS_CONSENT
    if reason in _BLOCKED_REASONS:
        return CompletionState.PROVIDER_BLOCKED
    if reason in _RETRYABLE_REASONS:
        return CompletionState.RETRYABLE_PROVIDER_ERROR
    if reason in _STUCK_REASONS:
        return CompletionState.STUCK_NO_PROGRESS
    return CompletionState.FAILED


def completion_state_from_legacy(result: Mapping[str, Any] | None) -> CompletionState:
    """Read a typed or legacy result without ever inferring false success.

    A non-empty ``stopped_reason`` wins even if an older layer labelled the
    payload as answered.  Unknown legacy states fail closed.
    """

    payload = result or {}
    stopped_reason = _normalized(payload.get("stopped_reason"))
    if stopped_reason:
        return _state_for_stop_reason(stopped_reason)

    explicit = _normalized(payload.get("completion_state"))
    if explicit:
        try:
            return CompletionState(explicit)
        except ValueError:
            return CompletionState.FAILED

    status = _normalized(payload.get("status"))
    if status in _ANSWERED_STATUSES:
        return CompletionState.COMPLETED
    if status in _CANCELLED_STATUSES:
        return CompletionState.CANCELLED
    if status in _USER_INPUT_STATUSES:
        return CompletionState.NEEDS_USER_INPUT
    if status in _CONSENT_STATUSES:
        return CompletionState.NEEDS_CONSENT
    if status in _RETRYABLE_STATUSES:
        return CompletionState.RETRYABLE_PROVIDER_ERROR
    if status in _BLOCKED_STATUSES:
        return CompletionState.PROVIDER_BLOCKED
    if status in _STUCK_STATUSES:
        return CompletionState.STUCK_NO_PROGRESS
    return CompletionState.FAILED


_LEGACY_STATUS_BY_STATE = {
    CompletionState.CANCELLED: "cancelled",
    CompletionState.NEEDS_USER_INPUT: "needs_user_input",
    CompletionState.NEEDS_CONSENT: "needs_confirmation",
    CompletionState.RETRYABLE_PROVIDER_ERROR: "retryable_provider_error",
    CompletionState.PROVIDER_BLOCKED: "provider_blocked",
    CompletionState.STUCK_NO_PROGRESS: "incomplete",
    CompletionState.FAILED: "failed",
}


def result_is_completed(result: Mapping[str, Any] | None) -> bool:
    """True only when a runner/pipeline result genuinely completed.

    The single honest gate for "may this surface say the task finished?".  It
    reads the canonical completion truth (``completion_state`` / ``stopped_reason``
    win over any legacy ``status``), so a stuck, blocked, cancelled, or
    needs-input run is never reported as done even when an older layer left its
    status as "answered".
    """

    return completion_state_from_legacy(result) is CompletionState.COMPLETED


def legacy_status_for_completion(
    state: CompletionState | str,
    *,
    completed_status: str = "answered",
) -> str:
    """Return the temporary compatibility status for a canonical state."""

    try:
        canonical = (
            state if isinstance(state, CompletionState) else CompletionState(state)
        )
    except ValueError:
        return "failed"
    if canonical is CompletionState.COMPLETED:
        candidate = _normalized(completed_status)
        return completed_status if candidate in _ANSWERED_STATUSES else "answered"
    return _LEGACY_STATUS_BY_STATE[canonical]


@dataclass(frozen=True)
class CompletionResult:
    """Small immutable result shared by runners, persistence, and surfaces."""

    state: CompletionState
    answer: str = ""
    user_question: str = ""
    stopped_reason: str = ""
    blocked_reason: ProviderBlockedReason | None = None
    recovery_action: str = ""
    error: str = ""
    checkpoint: Mapping[str, Any] | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.state, CompletionState):
            object.__setattr__(self, "state", CompletionState(str(self.state)))
        if self.blocked_reason is not None and not isinstance(
            self.blocked_reason, ProviderBlockedReason
        ):
            object.__setattr__(
                self,
                "blocked_reason",
                ProviderBlockedReason(str(self.blocked_reason)),
            )
        if (
            self.state is CompletionState.PROVIDER_BLOCKED
            and self.blocked_reason is None
        ):
            raise ValueError("provider_blocked completion requires blocked_reason")
        if (
            self.state is not CompletionState.PROVIDER_BLOCKED
            and self.blocked_reason is not None
        ):
            raise ValueError("blocked_reason is only valid for provider_blocked")
        if self.state is CompletionState.COMPLETED and self.stopped_reason:
            raise ValueError("completed result cannot carry a stopped_reason")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer")
        if self.checkpoint is not None:
            if not isinstance(self.checkpoint, Mapping):
                raise TypeError("checkpoint must be a mapping or None")
            object.__setattr__(self, "checkpoint", _freeze(self.checkpoint))

    def to_dict(self) -> dict[str, Any]:
        return {
            "completion_schema_version": int(self.schema_version),
            "completion_state": self.state.value,
            "answer": self.answer,
            "user_question": self.user_question,
            "stopped_reason": self.stopped_reason,
            "provider_blocked_reason": (
                self.blocked_reason.value if self.blocked_reason is not None else None
            ),
            "recovery_action": self.recovery_action,
            "error": self.error,
            "checkpoint": _thaw(self.checkpoint),
            "status": legacy_status_for_completion(self.state),
        }
