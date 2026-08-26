"""Typed, redacted failures at external execution boundaries (#622).

Raw exceptions stay in-process.  This module converts them into a bounded,
JSON-safe record before a result, journal, receipt, or UI payload can observe
them.  Retry safety reuses the exact-once operation policy from #616 so error
normalisation cannot silently create a competing continuity decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable

from .command_runner import redact
from .operation_class import dispatch_proof, retry_decision

BOUNDARY_ERROR_SCHEMA_VERSION = 1
MAX_TECHNICAL_MESSAGE_CHARS = 2_000
_LABEL = re.compile(r"[^a-z0-9_.-]+")


def _label(value: Any, *, fallback: str) -> str:
    cleaned = _LABEL.sub("_", str(value or "").strip().lower()).strip("_")
    return (cleaned or fallback)[:96]


def _safe_text(value: Any, *, limit: int) -> tuple[str, str]:
    try:
        return redact(str(value or ""))[:limit], "redacted"
    except Exception:  # noqa: BLE001 - redaction failure must fail closed
        return "[REDACTION_FAILED]", "failed_closed"


@dataclass(frozen=True)
class BoundaryError:
    """A schema-valid failure record safe to cross a process/UI boundary."""

    category: str
    code: str
    source: str
    user_message: str
    technical_message: str
    operation_id: str = ""
    effect_continuity: str = "unknown"
    retryable: bool = False
    automatic_retry_safe: bool = False
    recovery_actions: tuple[str, ...] = ()
    redaction_status: str = "redacted"
    schema_version: int = BOUNDARY_ERROR_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        category: str,
        code: str,
        source: str,
        detail: Any,
        user_message: str,
        operation_id: str = "",
        operation_kind: str = "",
        retryable: bool = False,
        recovery_actions: Iterable[str] = (),
    ) -> BoundaryError:
        technical_message, redaction_status = _safe_text(
            detail, limit=MAX_TECHNICAL_MESSAGE_CHARS
        )
        safe_operation_id, operation_redaction = _safe_text(operation_id, limit=128)
        safe_user_message, user_redaction = _safe_text(user_message, limit=500)
        safe_actions: list[str] = []
        action_redactions: set[str] = set()
        for action in recovery_actions:
            safe_action, action_redaction = _safe_text(action, limit=96)
            safe_actions.append(_label(safe_action, fallback="review"))
            action_redactions.add(action_redaction)
        proof = dispatch_proof(code)
        retry = retry_decision(
            operation_kind,
            error_code=code,
            attempts=0,
            max_attempts=1,
        )
        return cls(
            category=_label(category, fallback="unknown_internal"),
            code=_label(code, fallback="unknown").upper(),
            source=_label(source, fallback="unknown_boundary"),
            user_message=safe_user_message,
            technical_message=technical_message,
            operation_id=safe_operation_id,
            effect_continuity=proof.value,
            retryable=bool(retryable),
            automatic_retry_safe=bool(retryable and retry.allowed),
            recovery_actions=tuple(safe_actions[:8]),
            redaction_status=(
                "failed_closed"
                if "failed_closed"
                in {
                    redaction_status,
                    operation_redaction,
                    user_redaction,
                    *action_redactions,
                }
                else "redacted"
            ),
        )

    @classmethod
    def from_provider_exception(
        cls,
        exc: BaseException,
        *,
        source: str,
        provider: str,
        model: str = "",
        operation_id: str = "",
        operation_kind: str = "",
    ) -> BoundaryError:
        safe_detail, redaction_status = _safe_text(
            exc, limit=MAX_TECHNICAL_MESSAGE_CHARS
        )
        if redaction_status == "failed_closed":
            return cls.create(
                category="unknown_internal",
                code="UNKNOWN",
                source=source,
                detail=safe_detail,
                user_message="OPai could not safely prepare the provider diagnostic.",
                operation_id=operation_id,
                operation_kind=operation_kind,
            )

        try:
            from opai.provider_contract import normalize_provider_error

            normalized = normalize_provider_error(
                provider,
                safe_detail,
                model=model,
            )
        except Exception:  # noqa: BLE001 - normalization also fails closed
            return cls.create(
                category="unknown_internal",
                code="UNKNOWN",
                source=source,
                detail="[NORMALIZATION_FAILED]",
                user_message="OPai could not safely classify the provider failure.",
                operation_id=operation_id,
                operation_kind=operation_kind,
            )

        code = str(normalized.get("code") or "UNKNOWN")
        return cls.create(
            category=_provider_category(code),
            code=code,
            source=source,
            detail=normalized.get("technicalMessage") or safe_detail,
            user_message=str(
                normalized.get("userMessage") or "Provider request failed."
            ),
            operation_id=operation_id,
            operation_kind=operation_kind,
            retryable=bool(normalized.get("retryable")),
            recovery_actions=normalized.get("recoveryActions") or (),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["recovery_actions"] = list(self.recovery_actions)
        return payload


def _provider_category(code: str) -> str:
    value = str(code or "").upper()
    if value.startswith("AUTH_"):
        return "provider_authentication"
    if value in {"PROVIDER_RATE_LIMITED", "PROVIDER_QUOTA_EXHAUSTED"}:
        return "provider_quota_or_rate_limit"
    if value in {"NETWORK_ERROR", "PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"}:
        return "provider_transport"
    if value in {
        "MODEL_UNAVAILABLE",
        "CONFIG_INVALID",
        "PROVIDER_CLI_OUTDATED",
    }:
        return "adapter_incompatible"
    if value in {"STREAM_ABORTED", "NO_RESPONSE"}:
        return "provider_malformed_or_partial_output"
    if value == "TASK_DEADLINE":
        return "timeout"
    return "unknown_internal"
