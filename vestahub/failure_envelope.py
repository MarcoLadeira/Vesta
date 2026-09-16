"""Structured failure diagnosis for stalled or failed runs (#569).

The report's complaint is precise: "The UI only shows 'provider failed', hiding
the actual cause of stall." Every distinct way a run can end badly — a
deprecated CLI subcommand, an expired token, a quota ceiling, a genuine loop —
collapsed into one opaque string, which is useless to the user *and* useless to
any recovery logic that wants to react to the cause.

A :class:`FailureEnvelope` is the honest replacement: a typed category, the
evidence that led to it, what recovery was attempted and what happened. It is
JSON-safe and free of raw prompt text so it can travel into receipts, the
activity feed and support bundles.

Two rules keep it trustworthy:

* **Unknown stays unknown.** Text that matches no known signature classifies as
  ``UNKNOWN``, never as the nearest-looking category. A confident wrong
  diagnosis is worse than an admitted one, because recovery acts on it.
* **The envelope never claims success.** It records what was *attempted* and
  the observed outcome; whether the run completed remains the completion
  state's decision, not the diagnostician's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Mapping, Sequence

from .command_runner import redact

MAX_EVIDENCE_ITEMS = 10
MAX_DETAIL_CHARS = 400


class FailureCategory(str, Enum):
    """Why a run stopped. Drawn from the report's taxonomy."""

    TOOL_COMPATIBILITY = "tool_compatibility"  # the tool changed under us
    AUTHENTICATION = "authentication"
    QUOTA_OR_RATE_LIMIT = "quota_or_rate_limit"
    NETWORK = "network"
    PERMISSION_DENIED = "permission_denied"
    NO_PROGRESS = "no_progress"  # a genuine loop / stagnation
    REPEATED_FAILURE = "repeated_failure"  # same action failing over and over
    PROVIDER_ERROR = "provider_error"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# Ordered most-specific first: the first match wins, so a message that mentions
# both "rate limit" and a generic "error" classifies as the rate limit.
_SIGNATURES: tuple[tuple[FailureCategory, re.Pattern[str]], ...] = (
    (
        FailureCategory.TOOL_COMPATIBILITY,
        re.compile(
            r"(?i)\b(unknown (field|command|flag)|no longer supported|deprecat\w*|"
            r"projects_classic_removed|unsupported api version|unrecognized argument)\b"
        ),
    ),
    (
        FailureCategory.AUTHENTICATION,
        re.compile(
            r"(?i)\b(401|unauthor\w+|authentication failed|invalid (api )?key|"
            r"token (expired|invalid)|not logged in|gh auth login)\b"
        ),
    ),
    (
        FailureCategory.QUOTA_OR_RATE_LIMIT,
        re.compile(
            r"(?i)\b(429|rate.?limit\w*|quota (exceeded|exhausted)|"
            r"insufficient (quota|credit)|billing (hard )?limit)\b"
        ),
    ),
    (
        FailureCategory.PERMISSION_DENIED,
        re.compile(
            r"(?i)\b(403|permission denied|forbidden|access denied|"
            r"resource not accessible|protected branch)\b"
        ),
    ),
    (
        FailureCategory.NETWORK,
        re.compile(
            r"(?i)\b(connection (refused|reset|timed out)|network is unreachable|"
            r"temporary failure in name resolution|dns|econnreset|etimedout|"
            r"could not resolve host)\b"
        ),
    ),
)


def classify_failure(text: str) -> FailureCategory:
    """Map error text to a category, or UNKNOWN when nothing matches.

    Deliberately conservative: no fuzzy "closest category" fallback. Recovery
    logic keys off this value, so guessing here would turn a diagnosis problem
    into a wrong-action problem.
    """

    haystack = str(text or "")
    if not haystack.strip():
        return FailureCategory.UNKNOWN
    for category, pattern in _SIGNATURES:
        if pattern.search(haystack):
            return category
    return FailureCategory.UNKNOWN


@dataclass(frozen=True)
class FailureEvidence:
    """One observed fact supporting the diagnosis."""

    step: int
    action: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        # Evidence quotes real tool output, which is exactly where a leaked
        # credential would appear, so it is redacted and bounded on the way out.
        return {
            "step": int(self.step),
            "action": str(self.action)[:120],
            "detail": redact(str(self.detail))[:MAX_DETAIL_CHARS],
        }


@dataclass(frozen=True)
class FailureEnvelope:
    """Why a run stopped, with the evidence and what was tried about it."""

    primary_cause: FailureCategory
    tool: str = ""
    tool_version: str = ""
    error_signature: str = ""
    evidence: tuple[FailureEvidence, ...] = ()
    recovery_attempted: tuple[str, ...] = ()
    outcome: str = ""
    progress: Mapping[str, Any] | None = None
    schema_version: int = 1

    @classmethod
    def diagnose(
        cls,
        *,
        error_text: str = "",
        tool: str = "",
        tool_version: str = "",
        evidence: Sequence[FailureEvidence] = (),
        recovery_attempted: Sequence[str] = (),
        outcome: str = "",
        progress: Mapping[str, Any] | None = None,
        category: FailureCategory | None = None,
    ) -> FailureEnvelope:
        """Build an envelope, classifying the text unless a category is given."""

        resolved = category or classify_failure(error_text)
        return cls(
            primary_cause=resolved,
            tool=str(tool or ""),
            tool_version=str(tool_version or ""),
            # The signature is what recovery recipes match on, so it keeps the
            # shape of the error but never its (possibly secret) payload.
            error_signature=redact(str(error_text or ""))[:200],
            evidence=tuple(evidence)[:MAX_EVIDENCE_ITEMS],
            recovery_attempted=tuple(str(item) for item in recovery_attempted),
            outcome=str(outcome or ""),
            progress=dict(progress) if progress else None,
        )

    @property
    def is_actionable(self) -> bool:
        """True when the cause suggests a specific remedy worth attempting.

        UNKNOWN is not actionable by design — that is the point of keeping it
        separate rather than collapsing it into PROVIDER_ERROR.
        """

        return self.primary_cause not in {
            FailureCategory.UNKNOWN,
            FailureCategory.CANCELLED,
        }

    def user_message(self) -> str:
        """One honest sentence for the UI, in place of 'provider failed'."""

        return _USER_MESSAGES.get(
            self.primary_cause, _USER_MESSAGES[FailureCategory.UNKNOWN]
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": int(self.schema_version),
            "primary_cause": self.primary_cause.value,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "error_signature": self.error_signature,
            "evidence": [item.to_dict() for item in self.evidence],
            "recovery_attempted": list(self.recovery_attempted),
            "outcome": self.outcome,
            "user_message": self.user_message(),
            "actionable": self.is_actionable,
        }
        if self.progress:
            payload["progress"] = dict(self.progress)
        return payload


_USER_MESSAGES: dict[FailureCategory, str] = {
    FailureCategory.TOOL_COMPATIBILITY: (
        "A tool Vesta used has changed and no longer supports that command."
    ),
    FailureCategory.AUTHENTICATION: "Vesta could not authenticate with the provider.",
    FailureCategory.QUOTA_OR_RATE_LIMIT: (
        "The provider refused the request because a rate limit or quota was reached."
    ),
    FailureCategory.NETWORK: "Vesta could not reach the provider.",
    FailureCategory.PERMISSION_DENIED: (
        "Vesta was not permitted to perform that action."
    ),
    FailureCategory.NO_PROGRESS: (
        "Vesta stopped because it was no longer making progress on the task."
    ),
    FailureCategory.REPEATED_FAILURE: (
        "Vesta stopped because the same action kept failing the same way."
    ),
    FailureCategory.PROVIDER_ERROR: "The provider returned an error.",
    FailureCategory.CANCELLED: "Stopped by you.",
    FailureCategory.UNKNOWN: (
        "Vesta stopped for a reason it could not identify. The evidence below is "
        "what it observed."
    ),
}


@dataclass
class EvidenceCollector:
    """Accumulates bounded evidence while a run executes."""

    items: list[FailureEvidence] = field(default_factory=list)
    first_failure_step: int | None = None

    def observe(self, step: int, action: str, *, ok: bool, detail: str = "") -> None:
        if ok:
            return
        if self.first_failure_step is None:
            self.first_failure_step = int(step)
        # Keep the *first* failures: the critical error is usually the earliest
        # one, and later noise is often a consequence of it.
        if len(self.items) < MAX_EVIDENCE_ITEMS:
            self.items.append(FailureEvidence(step=step, action=action, detail=detail))

    def as_tuple(self) -> tuple[FailureEvidence, ...]:
        return tuple(self.items)
