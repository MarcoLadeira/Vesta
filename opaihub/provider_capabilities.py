"""Catalog-derived provider profiles plus the legacy health-state machine.

The versioned provider catalog is the source of truth for adapter capability
facts.  ``ProviderHealth`` remains a compatibility view for existing surfaces;
it is deliberately separate from the protocol's richer ``ProviderReadiness``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

from .provider_catalog import CATALOG_VERSION, PROTOCOL_VERSION, all_catalog_records
from .provider_protocol import ProviderReadiness


class ProviderHealth(str, Enum):
    """The lifecycle of a provider, from "no binary on disk" to "working".

    Ordered by readiness. ``degraded``/``rate_limited``/``failed`` are recoverable
    trouble states; ``unknown`` means OPai has not learned anything yet (it is
    never a synthesised "healthy"). Being a ``str`` enum keeps it JSON-safe.
    """

    NOT_INSTALLED = "not_installed"
    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"
    AUTHENTICATED = "authenticated"
    DEGRADED = "degraded"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"
    UNKNOWN = "unknown"


# Allowed transitions. The lifecycle is not strictly linear: a working provider
# can degrade, hit a rate limit, fail, or be signed out, and any of those can
# recover. ``unknown`` may resolve to anything (we simply had no reading yet),
# and any state may re-observe itself (an idempotent refresh).
HEALTH_TRANSITIONS: dict[ProviderHealth, set[ProviderHealth]] = {
    ProviderHealth.NOT_INSTALLED: {
        ProviderHealth.NOT_CONFIGURED,
        ProviderHealth.CONFIGURED,
        ProviderHealth.UNKNOWN,
    },
    ProviderHealth.NOT_CONFIGURED: {
        ProviderHealth.NOT_INSTALLED,
        ProviderHealth.CONFIGURED,
        ProviderHealth.UNKNOWN,
    },
    ProviderHealth.CONFIGURED: {
        ProviderHealth.NOT_CONFIGURED,
        ProviderHealth.AUTHENTICATED,
        ProviderHealth.DEGRADED,
        ProviderHealth.RATE_LIMITED,
        ProviderHealth.FAILED,
        ProviderHealth.UNKNOWN,
    },
    ProviderHealth.AUTHENTICATED: {
        ProviderHealth.NOT_CONFIGURED,
        ProviderHealth.DEGRADED,
        ProviderHealth.RATE_LIMITED,
        ProviderHealth.FAILED,
        ProviderHealth.UNKNOWN,
    },
    ProviderHealth.DEGRADED: {
        ProviderHealth.AUTHENTICATED,
        ProviderHealth.RATE_LIMITED,
        ProviderHealth.FAILED,
        ProviderHealth.NOT_CONFIGURED,
        ProviderHealth.UNKNOWN,
    },
    ProviderHealth.RATE_LIMITED: {
        ProviderHealth.AUTHENTICATED,
        ProviderHealth.DEGRADED,
        ProviderHealth.FAILED,
        ProviderHealth.NOT_CONFIGURED,
        ProviderHealth.UNKNOWN,
    },
    ProviderHealth.FAILED: {
        ProviderHealth.AUTHENTICATED,
        ProviderHealth.CONFIGURED,
        ProviderHealth.NOT_CONFIGURED,
        ProviderHealth.DEGRADED,
        ProviderHealth.UNKNOWN,
    },
    ProviderHealth.UNKNOWN: set(ProviderHealth),
}


def can_transition(current: ProviderHealth, nxt: ProviderHealth) -> bool:
    """True if ``current`` may move to ``nxt`` (self-transitions always allowed)."""
    current = ProviderHealth(current)
    nxt = ProviderHealth(nxt)
    if current is nxt:
        return True
    return nxt in HEALTH_TRANSITIONS.get(current, set())


@dataclass(frozen=True)
class ProviderProfile:
    """What one provider can do (capabilities) and what it needs (requirements).

    Capabilities describe the honest *current* reality the picker should show —
    e.g. Copilot cannot edit a repository through OPai (its edits fail closed),
    and local runners do not stream yet (#154) — so ``repo_editing`` and
    ``streaming`` are ``False`` for those rather than aspirational ``True``.
    """

    provider_id: str
    kind: str
    # Capabilities
    chat: bool
    code_execution: bool
    repo_editing: bool
    streaming: bool
    tool_calling: bool
    # Requirements
    requires_api_key: bool
    requires_oauth: bool
    requires_cli: bool
    requires_git_repo: bool
    # Lifecycle
    supports_cancellation: bool
    # Additive catalog/protocol facts. Defaults keep direct legacy construction
    # source-compatible while catalog-created profiles always carry the truth.
    catalog_version: str = CATALOG_VERSION
    protocol_version: int = PROTOCOL_VERSION
    capability_status: Mapping[str, str] = field(default_factory=dict)
    provider_state: ProviderReadiness | None = None
    contract: Mapping[str, Any] = field(default_factory=dict)

    def capabilities(self) -> dict[str, bool]:
        return {
            "chat": self.chat,
            "code_execution": self.code_execution,
            "repo_editing": self.repo_editing,
            "streaming": self.streaming,
            "tool_calling": self.tool_calling,
        }

    def requirements(self) -> dict[str, bool]:
        return {
            "requires_api_key": self.requires_api_key,
            "requires_oauth": self.requires_oauth,
            "requires_cli": self.requires_cli,
            "requires_git_repo": self.requires_git_repo,
        }

    def to_dict(self) -> dict[str, Any]:
        state = self.provider_state or ProviderReadiness(
            self.provider_id, protocol_version=self.protocol_version
        )
        return {
            "provider_id": self.provider_id,
            "kind": self.kind,
            "capabilities": self.capabilities(),
            "requirements": self.requirements(),
            "supports_cancellation": self.supports_cancellation,
            "catalogVersion": self.catalog_version,
            "protocolVersion": self.protocol_version,
            "capabilityStatus": _thaw_catalog_value(self.capability_status),
            "providerState": state.to_dict(),
            "contract": _thaw_catalog_value(self.contract),
        }


def _freeze_catalog_value(value: Any) -> Any:
    """Copy immutable catalog data without leaking mapping proxies to JSON callers."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_catalog_value(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_catalog_value(item) for item in value)
    return value


def _thaw_catalog_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_catalog_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_catalog_value(item) for item in value]
    return value


def _kind_from_record(record: Mapping[str, Any]) -> str:
    """Derive the historical adapter kind from catalog requirements only."""

    requirements = record["requirements"]
    if requirements["local_service"]:
        return "local"
    if requirements["cli"]:
        return "account"
    return "free"


def _supported(capabilities: Mapping[str, str], name: str) -> bool:
    return capabilities[name] == "supported"


def _profile_from_record(record: Mapping[str, Any]) -> ProviderProfile:
    capabilities = record["capabilities"]
    requirements = record["requirements"]
    return ProviderProfile(
        provider_id=record["provider_id"],
        kind=_kind_from_record(record),
        chat=_supported(capabilities, "chat"),
        code_execution=_supported(capabilities, "code_execution"),
        repo_editing=_supported(capabilities, "repo_editing"),
        streaming=_supported(capabilities, "streaming"),
        tool_calling=_supported(capabilities, "tool_calling"),
        requires_api_key=requirements["api_key"],
        requires_oauth=requirements["oauth"],
        requires_cli=requirements["cli"],
        requires_git_repo=requirements["git_repository"],
        supports_cancellation=_supported(capabilities, "cancellation"),
        catalog_version=record["catalog_version"],
        protocol_version=record["protocol_version"],
        capability_status=_freeze_catalog_value(capabilities),
        provider_state=ProviderReadiness(
            record["provider_id"], protocol_version=record["protocol_version"]
        ),
        contract=_freeze_catalog_value(
            {
                "requirements": requirements,
                "cancellation": record["cancellation"],
                "unsupportedBehavior": record["unsupported_behavior"],
            }
        ),
    )


_PROFILES: dict[str, ProviderProfile] = {
    record["provider_id"]: _profile_from_record(record)
    for record in all_catalog_records()
}


def provider_profile(provider_id: str) -> ProviderProfile:
    """The capability profile for one provider. Raises for unknown providers so a
    typo can never silently masquerade as a capable provider."""
    provider = str(provider_id or "").strip().lower()
    profile = _PROFILES.get(provider)
    if profile is None:
        raise ValueError(f"Unsupported AI provider: {provider_id!r}")
    return profile


def all_provider_profiles() -> list[dict[str, Any]]:
    """Every provider's profile as serializable dicts, provider-id sorted."""
    return [_PROFILES[key].to_dict() for key in sorted(_PROFILES)]


# authStatus vocabulary (from accounts.connection_for_account / credentials) →
# the one canonical health enum. Keeps the existing per-surface strings working
# while giving every surface a single lifecycle truth to read.
_AUTH_STATUS_HEALTH = {
    "connected": ProviderHealth.AUTHENTICATED,
    "authenticated": ProviderHealth.AUTHENTICATED,
    "detected": ProviderHealth.CONFIGURED,
    "configured": ProviderHealth.CONFIGURED,
    "unknown": ProviderHealth.CONFIGURED,
    "not_configured": ProviderHealth.NOT_CONFIGURED,
    "misconfigured": ProviderHealth.DEGRADED,
    "provider_unavailable": ProviderHealth.DEGRADED,
    "degraded": ProviderHealth.DEGRADED,
    "rate_limited": ProviderHealth.RATE_LIMITED,
    "invalid": ProviderHealth.FAILED,
    "expired": ProviderHealth.FAILED,
    "disconnected": ProviderHealth.FAILED,
    "failed": ProviderHealth.FAILED,
}

_RATE_LIMIT_CODES = {
    "RATE_LIMIT",
    "RATE_LIMITED",
    "TOO_MANY_REQUESTS",
    "QUOTA_EXCEEDED",
}


def canonical_health(
    *,
    auth_status: str | None,
    cli_installed: bool | None = None,
    error_code: str | None = None,
    kind: str = "",
) -> ProviderHealth:
    """Fold OPai's existing connection signals into the one health enum.

    A missing CLI for an account provider means the binary is not installed,
    which outranks any auth string. An explicit rate-limit error code always wins
    (a rate-limited provider is authenticated but throttled). Everything else maps
    from ``authStatus``; anything unrecognised is honestly ``unknown``.
    """
    if str(kind or "").lower() == "account" and cli_installed is False:
        return ProviderHealth.NOT_INSTALLED
    if str(error_code or "").strip().upper() in _RATE_LIMIT_CODES:
        return ProviderHealth.RATE_LIMITED
    return _AUTH_STATUS_HEALTH.get(
        str(auth_status or "").strip().lower(), ProviderHealth.UNKNOWN
    )


def health_from_connection(entry: dict[str, Any]) -> ProviderHealth:
    """Canonical health for a settings/doctor connection entry."""
    return canonical_health(
        auth_status=entry.get("authStatus"),
        cli_installed=entry.get("cliInstalled"),
        error_code=entry.get("lastErrorCode"),
        kind=entry.get("kind", ""),
    )
