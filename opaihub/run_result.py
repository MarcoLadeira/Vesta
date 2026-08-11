"""Versioned, immutable terminal evidence envelope (#612, #618)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from types import MappingProxyType
from typing import Any, Mapping

from .generated_lifecycle import (
    ACCEPTED_LEGACY_SCHEMA_VERSIONS,
    DEGRADED_INPUTS,
    REASON_CLASSES,
    SCHEMA_VERSION,
    STATE_SPECS,
    TERMINAL_STATE_IDS,
)


_ACCEPTED_SCHEMA_VERSIONS = frozenset(
    {SCHEMA_VERSION, *ACCEPTED_LEGACY_SCHEMA_VERSIONS}
)
_RETRY_REASONS = frozenset(
    {
        "none",
        "manual_review",
        "network",
        "provider_transient",
        "rate_limit",
        "timeout",
        "user_requested",
    }
)
_AUTOMATIC_RETRY_REASONS = frozenset(
    {"network", "provider_transient", "rate_limit", "timeout"}
)
_VERIFIED = frozenset({"passed", "verified"})
_DELIVERED = frozenset({"delivered", "verified"})
_COST_RECONCILED = frozenset({"reconciled", "verified"})
_REFERENCE_KEYS = frozenset({"digest", "id", "path", "uri"})
_REFERENCE_ID_KEYS = frozenset({"id", "path", "uri"})
_REFERENCE_FIELDS = _REFERENCE_KEYS | {"kind"}
_PROVIDER_FIELDS = frozenset({"adapter_id", "model_id", "provider_id", "record_ref"})
_SNAPSHOT_KEYS = frozenset(
    {
        "messages",
        "metadata",
        "output",
        "payload",
        "raw",
        "raw_response",
        "response",
        "snapshot",
        "transcript",
    }
)
_AUTOMATIC_RETRY_STATES = frozenset({"failed", "timeout"})
_DIAGNOSTIC_FIELDS = frozenset({"codes", "count", "record_refs", "truncated"})


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
    raise TypeError("RunResult values must be finite JSON-compatible data")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _mapping(value: Mapping[str, Any] | None, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _validate_reference(value: Any, field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must contain a stable record reference")
    keys = {str(key) for key in value}
    if not keys or not keys <= _REFERENCE_FIELDS or not keys & _REFERENCE_ID_KEYS:
        raise ValueError(f"{field_name} has an invalid record reference shape")
    for item in value.values():
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"{field_name} reference values must be nonempty scalar strings"
            )


def _contains_snapshot_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _normalized(key) in _SNAPSHOT_KEYS or _contains_snapshot_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_snapshot_key(item) for item in value)
    return False


def _validate_provider(mapping: Mapping[str, Any]) -> None:
    if not mapping:
        return
    if _contains_snapshot_key(mapping):
        raise ValueError("provider raw output/payload/metadata snapshots are forbidden")
    keys = {str(key) for key in mapping}
    if not keys <= _PROVIDER_FIELDS:
        raise ValueError("provider contains an unlisted field; use record_ref")
    if "record_ref" not in mapping:
        raise ValueError("provider must contain a stable record reference")
    _validate_reference(mapping["record_ref"], "provider.record_ref")
    for key in keys - {"record_ref"}:
        value = mapping[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"provider.{key} must be a nonempty scalar string")


def _validate_diagnostics(mapping: Mapping[str, Any]) -> None:
    if _contains_snapshot_key(mapping):
        raise ValueError("diagnostics raw output/payload/metadata is forbidden")
    keys = {str(key) for key in mapping}
    if not keys <= _DIAGNOSTIC_FIELDS:
        raise ValueError("diagnostics contains an unlisted field")

    refs = mapping.get("record_refs", ())
    if not isinstance(refs, (list, tuple)):
        raise TypeError("diagnostics.record_refs must be a sequence")
    for reference in refs:
        _validate_reference(reference, "diagnostics.record_refs entry")

    count = mapping.get("count", 0)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise TypeError("diagnostics.count must be a non-negative integer")
    truncated = mapping.get("truncated", False)
    if not isinstance(truncated, bool):
        raise TypeError("diagnostics.truncated must be a boolean")
    codes = mapping.get("codes", ())
    if not isinstance(codes, (list, tuple)) or any(
        not isinstance(code, str) or not code.strip() for code in codes
    ):
        raise TypeError("diagnostics.codes must contain nonempty scalar strings")


def _validate_optional_record(
    mapping: Mapping[str, Any], field_name: str, *, require_reference: bool = False
) -> None:
    if not mapping:
        return
    if _contains_snapshot_key(mapping):
        raise ValueError(f"{field_name} snapshot data is forbidden; use record_ref")
    if require_reference and "record_ref" not in mapping:
        raise ValueError(f"{field_name} must contain a stable record reference")
    if "record_ref" in mapping:
        _validate_reference(mapping["record_ref"], f"{field_name}.record_ref")


def _validate_timestamp(value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        raise ValueError("terminal lifecycle requires final transition timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "final transition timestamp must be timezone-qualified ISO 8601"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError("final transition timestamp must include a timezone")


def _safe_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    try:
        _validate_timestamp(text)
    except ValueError:
        return "1970-01-01T00:00:00Z"
    return text


def _safe_identity(value: Mapping[str, Any] | None) -> dict[str, Any]:
    identity = _mapping(value, "identity")
    try:
        _freeze(identity)
    except TypeError:
        return {}
    return identity


def _schema_marker(value: Any) -> Any:
    if value is None or isinstance(value, (str, int)) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return type(value).__name__


def _presentation(state: str) -> dict[str, str]:
    metadata = STATE_SPECS[state]
    return {
        "category": str(metadata["presentation_category"]),
        "label": str(metadata["label"]),
    }


@dataclass(frozen=True)
class RunResult:
    """The canonical, provider-neutral record of one reconciled run outcome.

    Nested values are recursively frozen. Provider results and supporting
    evidence are represented by stable ``record_ref`` mappings; mutable provider
    response snapshots do not belong in this persisted envelope.
    """

    schema_version: int
    identity: Mapping[str, Any]
    lifecycle: Mapping[str, Any]
    provider: Mapping[str, Any]
    recovery: Mapping[str, Any]
    verification: Mapping[str, Any]
    delivery: Mapping[str, Any]
    economics: Mapping[str, Any]
    authority: Mapping[str, Any]
    diagnostics: Mapping[str, Any]
    presentation: Mapping[str, Any]
    compatibility: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version not in _ACCEPTED_SCHEMA_VERSIONS
        ):
            raise ValueError("unsupported RunResult schema_version")

        fields = {
            "identity": _mapping(self.identity, "identity"),
            "lifecycle": _mapping(self.lifecycle, "lifecycle"),
            "provider": _mapping(self.provider, "provider"),
            "recovery": _mapping(self.recovery, "recovery"),
            "verification": _mapping(self.verification, "verification"),
            "delivery": _mapping(self.delivery, "delivery"),
            "economics": _mapping(self.economics, "economics"),
            "authority": _mapping(self.authority, "authority"),
            "diagnostics": _mapping(self.diagnostics, "diagnostics"),
            "presentation": _mapping(self.presentation, "presentation"),
            "compatibility": _mapping(self.compatibility, "compatibility"),
        }
        for field_name, field_value in fields.items():
            if field_name != "provider" and _contains_snapshot_key(field_value):
                raise ValueError(
                    f"{field_name} raw output/payload/metadata is forbidden"
                )

        state = _normalized(fields["lifecycle"].get("state"))
        if state not in TERMINAL_STATE_IDS:
            raise ValueError("RunResult requires a canonical terminal state")

        mutating = fields["authority"].get("mutating")
        if not isinstance(mutating, bool):
            raise TypeError("authority.mutating must be a boolean")

        self._validate_completed_evidence(state, fields)

        reason = _normalized(fields["lifecycle"].get("reason"))
        if reason != "terminal_resolution" or reason not in REASON_CLASSES:
            raise ValueError("terminal lifecycle requires terminal_resolution reason")
        if not str(fields["lifecycle"].get("reason_detail") or "").strip():
            raise ValueError("terminal lifecycle requires reason detail")
        _validate_timestamp(fields["lifecycle"].get("final_transition_at"))
        if _normalized(fields["lifecycle"].get("reconciliation")) != "reconciled":
            raise ValueError("terminal lifecycle requires reconciliation")

        retry = fields["recovery"].get("automatic_retry", False)
        if not isinstance(retry, bool):
            raise TypeError("recovery.automatic_retry must be a boolean")
        retry_reason = _normalized(fields["recovery"].get("reason") or "none")
        if retry_reason not in _RETRY_REASONS:
            if retry:
                raise ValueError(
                    "automatic retry has an unknown or disallowed retry reason"
                )
            retry_reason = "manual_review"
        compatibility_retry = fields["compatibility"].get("automatic_retry", False)
        if not isinstance(compatibility_retry, bool):
            raise TypeError("compatibility.automatic_retry must be a boolean")
        if compatibility_retry is not retry:
            raise ValueError(
                "automatic retry truth must match recovery and compatibility metadata"
            )
        compatibility_state = _normalized(fields["compatibility"].get("state"))
        safe_retry_pair = (
            state in _AUTOMATIC_RETRY_STATES
            and retry_reason in _AUTOMATIC_RETRY_REASONS
            and compatibility_state != "incompatible"
        )
        if retry and not safe_retry_pair:
            raise ValueError(
                "automatic retry is incompatible with this terminal lifecycle state"
            )
        if compatibility_retry and (not retry or not safe_retry_pair):
            raise ValueError(
                "compatibility automatic retry requires a retry-safe terminal pair"
            )
        fields["recovery"]["automatic_retry"] = retry
        fields["recovery"]["reason"] = retry_reason

        _validate_provider(fields["provider"])
        for field_name in ("verification", "delivery", "economics", "authority"):
            _validate_optional_record(fields[field_name], field_name)
        _validate_diagnostics(fields["diagnostics"])

        expected_presentation = _presentation(state)
        if fields["presentation"] != expected_presentation:
            raise ValueError("presentation must match canonical lifecycle state")

        for field_name, value in fields.items():
            object.__setattr__(self, field_name, _freeze(value))

    @staticmethod
    def _validate_completed_evidence(
        state: str, fields: Mapping[str, dict[str, Any]]
    ) -> None:
        if state != "completed":
            return
        verification = fields["verification"]
        delivery = fields["delivery"]
        economics = fields["economics"]
        mutating = fields["authority"]["mutating"]

        verification_verdict = _normalized(
            verification.get("verdict") or verification.get("status")
        )
        verification_applicable = verification.get("applicable") is True
        if mutating and (
            not verification_applicable or verification_verdict not in _VERIFIED
        ):
            raise ValueError(
                "completed mutating result requires reconciled verification evidence"
            )
        if verification_applicable:
            if verification_verdict not in _VERIFIED:
                raise ValueError(
                    "completed result has conflicting verification evidence"
                )
            _validate_reference(
                verification.get("record_ref"), "verification.record_ref"
            )
        elif verification.get("applicable") is not False or (
            verification_verdict != "not_applicable"
        ):
            raise ValueError(
                "completed result must record verification as not_applicable"
            )

        delivery_verdict = _normalized(
            delivery.get("verdict") or delivery.get("status")
        )
        if delivery.get("applicable") is not True or delivery_verdict not in _DELIVERED:
            raise ValueError("completed result requires reconciled delivery evidence")
        _validate_reference(delivery.get("record_ref"), "delivery.record_ref")

        if _normalized(economics.get("integrity")) not in _COST_RECONCILED:
            raise ValueError(
                "completed result requires reconciled cost integrity evidence"
            )
        _validate_reference(economics.get("record_ref"), "economics.record_ref")

    @classmethod
    def from_payload(
        cls,
        *,
        state: str,
        reason: str = "terminal_resolution",
        reason_detail: str = "",
        final_transition_at: str = "",
        reconciled: bool = True,
        mutating: bool = False,
        identity: Mapping[str, Any] | None = None,
        provider: Mapping[str, Any] | None = None,
        recovery: Mapping[str, Any] | None = None,
        verification: Mapping[str, Any] | None = None,
        delivery: Mapping[str, Any] | None = None,
        economics: Mapping[str, Any] | None = None,
        cost: Mapping[str, Any] | None = None,
        authority: Mapping[str, Any] | None = None,
        diagnostics: Mapping[str, Any] | None = None,
        presentation: Mapping[str, Any] | None = None,
        compatibility: Mapping[str, Any] | None = None,
        schema_version: int = SCHEMA_VERSION,
    ) -> "RunResult":
        if not isinstance(mutating, bool):
            raise TypeError("mutating must be a boolean")
        if not isinstance(reconciled, bool):
            raise TypeError("reconciled must be a boolean")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            return cls._degraded(
                source_schema_version=schema_version,
                reason_detail="RunResult schema version is incompatible.",
                final_transition_at=final_transition_at,
                identity=identity,
            )
        normalized_state = _normalized(state)
        if schema_version not in _ACCEPTED_SCHEMA_VERSIONS:
            return cls._degraded(
                source_schema_version=schema_version,
                reason_detail="RunResult schema version is incompatible.",
                final_transition_at=final_transition_at,
                identity=identity,
            )
        if normalized_state not in STATE_SPECS:
            return cls._degraded(
                source_schema_version=schema_version,
                reason_detail="RunResult lifecycle state is incompatible.",
                final_transition_at=final_transition_at,
                identity=identity,
            )

        authority_value = _mapping(authority, "authority")
        if "mutating" in authority_value and not isinstance(
            authority_value["mutating"], bool
        ):
            raise TypeError("authority.mutating must be a boolean")
        if (
            "mutating" in authority_value
            and authority_value["mutating"] is not mutating
        ):
            raise ValueError("authority mutating flag conflicts with payload")
        authority_value["mutating"] = mutating
        economics_value = economics if economics is not None else cost
        recovery_value = _mapping(recovery, "recovery") or {
            "automatic_retry": False,
            "reason": "none",
        }
        compatibility_value = _mapping(compatibility, "compatibility")
        if compatibility_value:
            compatibility_value.setdefault(
                "automatic_retry", recovery_value.get("automatic_retry", False)
            )
        else:
            compatibility_value = {
                "state": (
                    "compatible"
                    if schema_version == SCHEMA_VERSION
                    else "compatible_previous"
                ),
                "source_schema_version": schema_version,
                "automatic_retry": recovery_value.get("automatic_retry", False),
            }
        lifecycle = {
            "state": normalized_state,
            "reason": _normalized(reason),
            "reason_detail": str(reason_detail or "").strip(),
            "final_transition_at": str(final_transition_at or "").strip(),
            "reconciliation": "reconciled" if reconciled else "pending",
        }
        return cls(
            schema_version=schema_version,
            identity=_mapping(identity, "identity"),
            lifecycle=lifecycle,
            provider=_mapping(provider, "provider"),
            recovery=recovery_value,
            verification=_mapping(verification, "verification"),
            delivery=_mapping(delivery, "delivery"),
            economics=_mapping(economics_value, "economics"),
            authority=authority_value,
            diagnostics=_mapping(diagnostics, "diagnostics") or {"record_refs": []},
            presentation=_mapping(presentation, "presentation")
            or _presentation(normalized_state),
            compatibility=compatibility_value,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunResult":
        if not isinstance(payload, Mapping):
            raise TypeError("RunResult payload must be a mapping")
        version = payload.get("schema_version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version not in _ACCEPTED_SCHEMA_VERSIONS
        ):
            lifecycle = payload.get("lifecycle")
            timestamp = (
                str(lifecycle.get("final_transition_at") or "")
                if isinstance(lifecycle, Mapping)
                else ""
            )
            return cls._degraded(
                source_schema_version=version,
                reason_detail="RunResult schema version is incompatible.",
                final_transition_at=timestamp,
                identity=(
                    payload.get("identity")
                    if isinstance(payload.get("identity"), Mapping)
                    else None
                ),
            )
        lifecycle = payload.get("lifecycle")
        state = (
            _normalized(lifecycle.get("state"))
            if isinstance(lifecycle, Mapping)
            else ""
        )
        if state not in STATE_SPECS:
            return cls._degraded(
                source_schema_version=version,
                reason_detail="RunResult lifecycle state is incompatible.",
                final_transition_at=(
                    str(lifecycle.get("final_transition_at") or "")
                    if isinstance(lifecycle, Mapping)
                    else ""
                ),
                identity=(
                    payload.get("identity")
                    if isinstance(payload.get("identity"), Mapping)
                    else None
                ),
            )
        required = (
            "identity",
            "lifecycle",
            "provider",
            "recovery",
            "verification",
            "delivery",
            "economics",
            "authority",
            "diagnostics",
            "presentation",
            "compatibility",
        )
        missing = [name for name in required if name not in payload]
        if missing:
            raise ValueError(f"RunResult payload missing fields: {', '.join(missing)}")
        return cls(
            schema_version=version,
            **{name: payload[name] for name in required},
        )

    @classmethod
    def _degraded(
        cls,
        *,
        source_schema_version: Any,
        reason_detail: str,
        final_transition_at: str,
        identity: Mapping[str, Any] | None,
    ) -> "RunResult":
        degradation = DEGRADED_INPUTS["unknown_schema_version"]
        return cls(
            schema_version=SCHEMA_VERSION,
            identity=_safe_identity(identity),
            lifecycle={
                "state": degradation["state"],
                "reason": "terminal_resolution",
                "reason_detail": reason_detail,
                "final_transition_at": _safe_timestamp(final_transition_at),
                "reconciliation": "reconciled",
            },
            provider={},
            recovery={"automatic_retry": False, "reason": "manual_review"},
            verification={},
            delivery={},
            economics={},
            authority={"mutating": False},
            diagnostics={"record_refs": []},
            presentation=_presentation(str(degradation["state"])),
            compatibility={
                "state": degradation["compatibility"],
                "source_schema_version": _schema_marker(source_schema_version),
                "automatic_retry": degradation["automatic_retry"],
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity": _thaw(self.identity),
            "lifecycle": _thaw(self.lifecycle),
            "provider": _thaw(self.provider),
            "recovery": _thaw(self.recovery),
            "verification": _thaw(self.verification),
            "delivery": _thaw(self.delivery),
            "economics": _thaw(self.economics),
            "authority": _thaw(self.authority),
            "diagnostics": _thaw(self.diagnostics),
            "presentation": _thaw(self.presentation),
            "compatibility": _thaw(self.compatibility),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True)
class TerminalPresentation:
    """Validated, surface-safe fields projected from one canonical RunResult."""

    state: str
    label: str
    category: str
    reason: str
    automatic_retry: bool
    retry_reason: str


def terminal_presentation(payload: Any) -> TerminalPresentation:
    """Return terminal display truth without letting a surface re-derive it.

    Explicit but malformed canonical payloads fail closed to ``needs_attention``.
    Compatibility callers decide whether to use a legacy source only when the
    ``run_result`` field is absent; once present, this adapter is authoritative.
    """

    try:
        canonical = RunResult.from_dict(payload)
    except (TypeError, ValueError):
        state = str(DEGRADED_INPUTS["unknown_state"]["state"])
        presentation = _presentation(state)
        return TerminalPresentation(
            state=state,
            label=presentation["label"],
            category=presentation["category"],
            reason="Canonical run result was invalid and needs manual review.",
            automatic_retry=False,
            retry_reason="manual_review",
        )

    return TerminalPresentation(
        state=str(canonical.lifecycle["state"]),
        label=str(canonical.presentation["label"]),
        category=str(canonical.presentation["category"]),
        reason=str(canonical.lifecycle["reason_detail"]),
        automatic_retry=bool(canonical.recovery["automatic_retry"]),
        retry_reason=str(canonical.recovery["reason"]),
    )
