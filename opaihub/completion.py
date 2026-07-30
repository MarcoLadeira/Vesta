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


# The one user-facing label for each verdict (#396). GUI, CLI, and the receipt
# summary all render from this table so the same run never reads as "Timeout"
# on one surface and "Timed out" on another. Mirrored in assets/web/app.js
# (VERDICT_LABELS) and guarded by a vocabulary-parity test.
VERDICT_LABELS = {
    CompletionVerdict.COMPLETED.value: "Completed",
    CompletionVerdict.PARTIAL.value: "Partial",
    CompletionVerdict.BLOCKED.value: "Blocked",
    CompletionVerdict.FAILED.value: "Failed",
    CompletionVerdict.CANCELLED.value: "Cancelled",
    CompletionVerdict.TIMEOUT.value: "Timed out",
}


def verdict_label(verdict: Any) -> str:
    """The shared user-facing label for a verdict value (#396)."""

    key = str(getattr(verdict, "value", verdict) or "").strip().lower()
    return VERDICT_LABELS.get(key) or (key.replace("_", " ").title() or "Unknown")


class FailureReason(str, Enum):
    """The typed cause behind a FAILED verdict (#380).

    A failure the user can act on names its class instead of a generic
    "provider failed": an auth failure sends them to re-connect, a rate-limit to
    wait or switch, an internal error to report. GUI and CLI read the same
    verdict, so they present the identical typed failure and next action.
    """

    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    PROVIDER = "provider"
    POLICY = "policy"
    INTERNAL = "internal"


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

# Typed provider error codes (opai.provider_contract.ERROR_CODES) → failure class
# (#380). Codes that are their own verdict — PROVIDER_TIMEOUT (TIMEOUT),
# USER_CANCELLED (CANCELLED) — never reach the FAILED branch and are omitted.
_FAILURE_BY_ERROR_CODE = {
    "AUTH_MISSING": FailureReason.AUTH,
    "AUTH_INVALID": FailureReason.AUTH,
    "AUTH_EXPIRED": FailureReason.AUTH,
    "PROVIDER_RATE_LIMITED": FailureReason.RATE_LIMIT,
    "PROVIDER_QUOTA_EXHAUSTED": FailureReason.RATE_LIMIT,
    "NETWORK_ERROR": FailureReason.NETWORK,
    "PROVIDER_UNAVAILABLE": FailureReason.PROVIDER,
    "MODEL_UNAVAILABLE": FailureReason.PROVIDER,
    "NO_RESPONSE": FailureReason.PROVIDER,
    "STREAM_ABORTED": FailureReason.PROVIDER,
    "CONTEXT_TOO_LARGE": FailureReason.PROVIDER,
    "CONFIG_INVALID": FailureReason.INTERNAL,
    "UNKNOWN": FailureReason.PROVIDER,
}

# Per-class user-facing reason + next safe action (#380: "next safe action
# offered"). Kept provider-independent and free of raw diagnostics.
_FAILURE_COPY = {
    FailureReason.AUTH: (
        "The provider rejected OPai's credentials before the objective could be verified.",
        "Re-connect the provider account, then retry.",
    ),
    FailureReason.RATE_LIMIT: (
        "The provider throttled the request or its quota was exhausted before the objective could be verified.",
        "Wait a moment and retry, or switch to another provider.",
    ),
    FailureReason.NETWORK: (
        "OPai could not reach the provider before it could verify the objective.",
        "Check your connection and retry.",
    ),
    FailureReason.PROVIDER: (
        "The provider failed before OPai could verify the objective.",
        "Retry the run, or switch to another provider.",
    ),
    FailureReason.POLICY: (
        "A safety policy stopped the run before the objective could be verified.",
        "Adjust the request or autonomy level, then retry.",
    ),
    FailureReason.INTERNAL: (
        "OPai hit an internal error before it could verify the objective.",
        "Retry the run; if it keeps happening, report it with the run id.",
    ),
}

_INTERNAL_FAILURE_STATUSES = frozenset({"runner_error", "internal_error", "opai_error"})
_POLICY_FAILURE_STATUSES = frozenset({"blocked_policy", "policy_error"})

_EDIT_MODES = frozenset({"implement", "ship", "build", "edit", "fix"})
_TEST_REQUEST = re.compile(r"\b(?:test|tests|testing|verify|verification|ci)\b", re.I)


def classify_failure_reason(result: Mapping[str, Any] | None) -> FailureReason:
    """Map a failed run to its typed cause (#380).

    Prefers the normalized provider error code (``error.code``); falls back to
    the legacy ``status`` and finally to :attr:`FailureReason.PROVIDER` — the
    same honest default the generic "provider_failed" verdict used, never a
    fabricated internal blame.
    """

    payload = result or {}
    error = payload.get("error")
    if isinstance(error, Mapping):
        code = str(error.get("code") or "").strip().upper()
        mapped = _FAILURE_BY_ERROR_CODE.get(code)
        if mapped is not None:
            return mapped
    status = _normalized(payload.get("status"))
    if status in _INTERNAL_FAILURE_STATUSES:
        return FailureReason.INTERNAL
    if status in _POLICY_FAILURE_STATUSES:
        return FailureReason.POLICY
    if status.startswith("auth") or "unauthor" in status:
        return FailureReason.AUTH
    if "rate" in status or "quota" in status:
        return FailureReason.RATE_LIMIT
    if "network" in status or "connection" in status:
        return FailureReason.NETWORK
    return FailureReason.PROVIDER


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


#: Keys that carry *verification* truth into the verdict. Only OPai's own
#: execution path may populate them, and :func:`evidence_payload` is the one
#: place that decides what goes in — see its docstring for why the allowlist is
#: the guarantee rather than a convention.
MEASURED_EVIDENCE_KEYS = frozenset(
    {
        "answer",
        "changed_files",
        "diff_review",
        "repo_change",
        "tool_trace",
        "verification",
        "status",
        "completion_state",
        "stopped_reason",
        "error",
    }
)


def _has_successful_test(payload: Mapping[str, Any]) -> bool:
    """Whether OPai *observed* tests pass — never whether something said so.

    The ``tests``/``test_results`` mapping this used to accept was a bare
    status string with no provenance: any payload carrying
    ``{"tests": {"status": "passed"}}`` manufactured a passing-test evidence
    ref, which was enough to satisfy the ``tests_pass`` acceptance requirement
    and stamp a run **completed**. Nothing populates that key on the live path
    today, so it was not exploitable — but gate 1 asks for completion to be
    *technically impossible* without evidence, not merely unreached, and
    ``evidence_payload`` used to spread the whole provider-influenced result
    into the verdict, so one refactor introducing a ``tests`` key anywhere
    would have made it live.

    Evidence now has to come from a record of something OPai ran: an entry in
    its own tool trace, or a structured verification result carrying the exit
    status it observed.
    """
    for item in payload.get("tool_trace") or ():
        if not isinstance(item, Mapping):
            continue
        tool = _normalized(item.get("tool") or item.get("type"))
        status = _normalized(item.get("status"))
        if tool in {"run_tests", "test", "tests"} and (
            item.get("ok") is True or status in {"success", "passed"}
        ):
            return True
    # A structured verification record from OPai's own check runner (#539).
    # `exit_status` is required: it is the part a claim cannot fabricate,
    # because only the process that ran the command can report it.
    verification = payload.get("verification")
    if isinstance(verification, Mapping):
        for check in verification.get("checks") or ():
            if not isinstance(check, Mapping):
                continue
            if _normalized(check.get("kind")) not in {"tests", "test", "unit"}:
                continue
            if check.get("exit_status") == 0 and check.get("observed_by") == "opai":
                return True
    return False


# An explicit "I cannot do this" — the model naming its own inability to run,
# execute, access, or perform the thing that was asked. Deliberately narrow: it
# must be a first-person incapability about *doing*, not a passing caveat, and
# it only ever downgrades a run that produced no tool evidence either.
_DECLINE_PATTERNS = (
    r"\b(?:is|are|was|were)\s+(?:outside|beyond|not\s+(?:part|within))\s+(?:of\s+)?"
    r"(?:my|its|our|the)\s+(?:current\s+)?(?:tool\s+)?(?:capabilit|abilit|scope|permission)",
    r"\bi\s*(?:'m|\s+am)\s+(?:not\s+able|unable)\s+to\s+(?:run|execute|access|perform|open|read|fetch|check|verify)",
    r"\bi\s+(?:can(?:no|')t|could\s+not|cannot)\s+(?:run|execute|access|perform|open|fetch|verify)\b",
    r"\bi\s+do(?:n'|\s+no)t\s+have\s+(?:the\s+)?"
    r"(?:abilit(?:y|ies)|capabilit(?:y|ies)|access|permission|tools?)\s+to\b",
    r"\bnot\s+something\s+i\s+(?:can|am\s+able\s+to)\s+(?:do|run|execute)\b",
    r"\bno\s+tool\s+(?:is\s+)?available\s+to\b",
)
_DECLINE = re.compile("|".join(_DECLINE_PATTERNS), re.IGNORECASE)


def _declines_the_request(answer: str) -> bool:
    """True when the answer's substance is "I could not do what you asked"."""

    return bool(_DECLINE.search(str(answer or "")))


def _has_successful_tool_call(payload: Mapping[str, Any]) -> bool:
    """True when at least one tool in the trace actually ran and succeeded."""

    for item in payload.get("tool_trace") or ():
        if not isinstance(item, Mapping):
            continue
        if item.get("ok") is True or _normalized(item.get("status")) in {
            "success",
            "passed",
            "ok",
        }:
            return True
    return False


# Round 5 finding 3: asked to print raw `git status`/`git log`, and to fetch PR
# details from the GitHub API, the model twice replied with only a claim —
# "Retrieved and printed the requested git status…" — and no data whatsoever.
# That is the false-completion pattern applied to data retrieval, and it is
# detectable: a claim to have produced output, with no output present.
_OUTPUT_CLAIM = re.compile(
    r"(?:^|\b)(?:retrieved|fetched|printed|displayed|output|outputted|dumped|listed|"
    r"shown|showed|returned)\b[^.\n]{0,80}\b(?:output|status|log|details?|data|"
    r"response|result|contents?|diff|json|api)\b"
    r"|\bhere\s+(?:is|are)\s+the\s+(?:raw\s+)?(?:output|status|log|contents?|data)\b"
    r"|\b(?:i|opai)\s+(?:have\s+)?(?:retrieved|fetched|printed|displayed|ran|run)\b"
    r"[^.\n]{0,80}\b(?:output|status|log|details?|data|response|command)\b",
    re.IGNORECASE,
)

# What actual output looks like in a reply: a fenced block, an indented block, or
# a quoted line. Any of these means the model showed *something*, so the claim is
# not bare and this gate stays out of the way.
_OUTPUT_EVIDENCE = re.compile(r"```|~~~|(?:^|\n)(?: {4}|\t)\S|(?:^|\n)>\s*\S")

# A bare claim is short by nature — one or two sentences of "done". A long reply
# with prose analysis is a different thing (possibly a summary the user wanted),
# and this gate does not judge it.
_BARE_CLAIM_MAX_CHARS = 400
_BARE_CLAIM_MAX_LINES = 3

# "The command returned no output." is an honest, complete answer that happens to
# match the claim shape above. Reporting an absence is not hiding data, so a reply
# that says the output was empty is never treated as a missing one.
_EMPTY_OUTPUT_REPORT = re.compile(
    r"\b(?:no|empty|zero|nothing|none)\b[^.\n]{0,40}"
    r"\b(?:output|results?|matches|lines?|changes|commits?|entries)\b"
    r"|\b(?:output|result|log|status)\b[^.\n]{0,40}\b(?:was|is)\s+empty\b"
    r"|\bnothing\s+(?:to\s+(?:show|report|print)|was\s+returned)\b"
    r"|\b(?:returned|produced|printed)\s+(?:no|nothing|zero|an?\s+empty)\b",
    re.IGNORECASE,
)


def claims_output_without_showing_it(answer: str) -> bool:
    """True when a reply claims it produced output but contains none.

    Deliberately narrow, because the cost of a false positive is telling a user
    their good answer is unverified: the reply must claim output *and* be a short,
    block-free note. A reply that includes any fenced/indented/quoted output, or
    that is long enough to be real content, is never flagged.
    """

    text = str(answer or "").strip()
    if not text or len(text) > _BARE_CLAIM_MAX_CHARS:
        return False
    if _OUTPUT_EVIDENCE.search(text) or _EMPTY_OUTPUT_REPORT.search(text):
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > _BARE_CLAIM_MAX_LINES:
        return False
    return bool(_OUTPUT_CLAIM.search(text))


# First-person assertions that the requested work landed. Used only to detect
# disagreement with a non-completed verdict — never to award a completion.
_SUCCESS_CLAIM = re.compile(
    r"\b(?:has|have|was|were|is|been)\s+(?:successfully\s+)?"
    r"(?:pushed|committed|merged|created|deleted|updated|applied|deployed|posted|opened)\b"
    r"|\b(?:successfully|already)\s+"
    r"(?:pushed|committed|merged|created|deleted|updated|applied|deployed|posted|opened)\b"
    r"|\bi\s+(?:have\s+)?(?:pushed|committed|merged|posted|deployed|opened\s+a\s+pr)\b"
    r"|\b(?:push|commit|merge|deployment|pull\s+request)\s+(?:was\s+)?"
    r"(?:succeeded|successful|complete[d]?)\b",
    re.IGNORECASE,
)


def answer_contradicts_verdict(answer: str, verdict: Any) -> bool:
    """True when the reply asserts success that the verdict could not confirm.

    Round 5 finding 2: one push turn showed a red "Failed" pill above the words
    "has been successfully pushed to the origin remote". A user reading the pill
    and a user reading the prose walked away with opposite conclusions. OPai
    cannot know which is right from prose alone — but it can refuse to present
    the claim as settled, which is what this flag drives in the UI.

    Only ever set alongside a non-completed, non-cancelled verdict, so a genuinely
    verified run is never annotated.
    """

    value = str(getattr(verdict, "value", verdict) or "").strip().lower()
    if value in {
        CompletionVerdict.COMPLETED.value,
        CompletionVerdict.CANCELLED.value,
        "",
    }:
        return False
    return bool(_SUCCESS_CLAIM.search(str(answer or "")))


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
    # A provider CLI runs git in its own shell, and committing *clears* the dirty
    # paths a run created — so a genuinely successful commit produced no changed
    # files and no diff review, and was stamped PARTIAL ("no changed-file or diff
    # evidence") on real work. The pipeline measures the repository across the
    # run and reports it here; a moved HEAD is proof the commit landed.
    if not refs:
        repo_change = payload.get("repo_change")
        if isinstance(repo_change, Mapping) and repo_change.get("changed"):
            refs.append(
                EvidenceRef(
                    "diff",
                    str(
                        repo_change.get("detail")
                        or "Repository changed during this run"
                    ),
                )
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


def evidence_payload(
    measured: Mapping[str, Any], *, extra: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Build the payload the verdict may read, by allowlist.

    The call site used to spread the whole result — ``{**payload, ...}`` —
    which meant the verdict read whatever keys the provider path happened to
    put there. That is the wrong default for a function whose entire job is to
    decide whether work was really done: it makes "can a provider manufacture
    completion?" a question about what keys exist *today* rather than a
    property of the design.

    Allowlisting inverts it. A new field is invisible to the verdict until
    someone adds it to :data:`MEASURED_EVIDENCE_KEYS`, which is a deliberate,
    reviewable act — so the safe outcome is the default and the unsafe one
    requires a decision.
    """
    payload = {
        key: value for key, value in measured.items() if key in MEASURED_EVIDENCE_KEYS
    }
    # Evidence manifests are deliberately accepted from ``extra`` only.  A
    # provider can construct a self-consistent JSON digest, but only the
    # pipeline may add the manifest it executed and persisted itself.
    internal_only = {"verification_manifest"}
    for key, value in (extra or {}).items():
        if key in MEASURED_EVIDENCE_KEYS or key in internal_only:
            payload[key] = value
    return payload


def _manifest_verdict(payload: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return a validated #539 manifest verdict, never a provider claim."""

    raw = payload.get("verification_manifest")
    if not isinstance(raw, Mapping):
        return None
    try:
        from .verification_execution import (
            VerificationVerdict,
            verification_manifest_from_dict,
            verification_verdict,
        )

        value = verification_verdict(verification_manifest_from_dict(raw))
    except (TypeError, ValueError):
        return "invalid", "Verification evidence could not be validated."
    if value is VerificationVerdict.VERIFIED:
        return None
    messages = {
        VerificationVerdict.PARTIALLY_VERIFIED: "Required verification is only partially satisfied.",
        VerificationVerdict.BLOCKED: "Verification was blocked before required checks completed.",
        VerificationVerdict.FAILED: "A required verification check failed.",
        VerificationVerdict.CANCELLED: "Verification was cancelled before required checks completed.",
        VerificationVerdict.TIMEOUT: "A required verification check timed out.",
        VerificationVerdict.UNVERIFIED: "Required verification evidence is missing, unavailable, or damaged.",
    }
    return value.value, messages[value]


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
        failure = classify_failure_reason(result)
        reason_text, next_action = _FAILURE_COPY[failure]
        error = result.get("error")
        if isinstance(error, Mapping):
            actionable = str(error.get("userMessage") or "").strip()
            if actionable:
                reason_text = actionable
        return _verdict(
            CompletionVerdict.FAILED,
            failure.value,
            reason_text,
            objective,
            evidence,
            next_action,
        )

    if (manifest_outcome := _manifest_verdict(result)) is not None:
        manifest_status, manifest_reason = manifest_outcome
        if manifest_status == "blocked":
            verdict = CompletionVerdict.BLOCKED
        elif manifest_status == "failed":
            verdict = CompletionVerdict.FAILED
        elif manifest_status == "cancelled":
            verdict = CompletionVerdict.CANCELLED
        elif manifest_status == "timeout":
            verdict = CompletionVerdict.TIMEOUT
        else:
            verdict = CompletionVerdict.PARTIAL
        return _verdict(
            verdict,
            "verification_" + manifest_status,
            manifest_reason,
            objective,
            evidence,
            "Repair or rerun the required verification checks.",
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
    # Round 2, the other direction of the status bug: an answer-only run whose
    # answer *is* "I can't do that" was stamped Completed, because a non-empty
    # response satisfied the only requirement. A declined request is not a met
    # objective. Prose alone never decides this — the downgrade applies only when
    # the run also produced no successful tool call, so a model that actually did
    # the work and merely narrated a limitation still verifies as Completed.
    if (
        AcceptanceRequirement.ANSWER_PRESENT in objective.acceptance
        and kinds <= {"answer"}
        and _declines_the_request(str(result.get("answer") or ""))
        and not _has_successful_tool_call(result)
    ):
        return _verdict(
            CompletionVerdict.PARTIAL,
            "provider_declined",
            "OPai replied, but said it could not carry out the request — the "
            "objective is not verified.",
            objective,
            evidence,
            "Retry, or ask for the specific output you need.",
        )
    # Round 5 finding 3: "Retrieved and printed the requested git status…" with
    # no git status in the message is a claim, not an answer, and it was stamped
    # Completed because the claim itself is non-empty text. A reply whose entire
    # substance is "I produced the output you asked for", with the output absent,
    # has not delivered the answer — say so, and tell the user the one phrasing
    # that reliably fixes it.
    if (
        AcceptanceRequirement.ANSWER_PRESENT in objective.acceptance
        and claims_output_without_showing_it(str(result.get("answer") or ""))
    ):
        return _verdict(
            CompletionVerdict.PARTIAL,
            "claimed_output_missing",
            "OPai said it retrieved the requested output, but the response does "
            "not contain any of it.",
            objective,
            evidence,
            'Ask again for the data itself: "reply with only the raw output".',
        )
    if AcceptanceRequirement.ANSWER_PRESENT in objective.acceptance:
        return _verdict(
            CompletionVerdict.COMPLETED,
            "answer_delivered",
            "Provider returned a complete response; its content was not independently verified.",
            objective,
            evidence,
            "Review the response and its cited evidence.",
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


def result_meets_objective(
    objective: ObjectiveRecord, result: Mapping[str, Any] | None
) -> bool:
    """True only when the completion verdict is COMPLETED (#381 savings gate).

    Stricter than :func:`result_is_completed`: a run can be canonically
    COMPLETED (answered, no stop reason) yet fail objective verification — no
    diff for an edit, no passing tests, no answer — which is a
    :attr:`CompletionVerdict.PARTIAL` verdict.  Savings are the product's proof,
    so only a run that actually met its declared objective may claim them; a
    partial/blocked/timeout run shows its actual spend with no savings claim.
    """

    return evaluate_completion(objective, result).verdict is CompletionVerdict.COMPLETED


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
