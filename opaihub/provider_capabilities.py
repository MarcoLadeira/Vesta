"""One capability record and one health-state machine per provider (#168).

Provider knowledge used to be scattered — ``ACCOUNT_SPECS`` in accounts.py,
``FREE_MODEL_SPECS`` in free_models.py, adapter kinds in provider_adapters.py,
and ad-hoc ``authStatus`` strings — so every surface (picker, settings, doctor,
router) reasoned about providers differently. This module is the single source
of truth those surfaces read:

* :class:`ProviderProfile` — what a provider can do and what it needs.
* :class:`ProviderHealth` — one lifecycle enum with defined transitions.

It does **not** introduce a second model registry: the profiles are derived from
provider kind and the existing specs, and :class:`opaihub.provider_adapters.ProviderAdapter`
grows ``.profile`` / ``.health()`` accessors over this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


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
        return {
            "provider_id": self.provider_id,
            "kind": self.kind,
            "capabilities": self.capabilities(),
            "requirements": self.requirements(),
            "supports_cancellation": self.supports_cancellation,
        }


def _account_profile(provider_id: str, *, repo_editing: bool) -> ProviderProfile:
    """Signed-in CLI providers (claude/codex/copilot): native tools + streaming,
    OAuth via their CLI. Editing depends on the provider (Copilot fails closed)."""
    return ProviderProfile(
        provider_id=provider_id,
        kind="account",
        chat=True,
        code_execution=True,
        repo_editing=repo_editing,
        streaming=True,
        tool_calling=True,
        requires_api_key=False,
        requires_oauth=True,
        requires_cli=True,
        requires_git_repo=True,
        supports_cancellation=True,
    )


def _free_profile(provider_id: str) -> ProviderProfile:
    """Free public-API models (gemini/groq/mistral): OPai drives edits via its own
    bounded tools, so no native tool-calling and no streaming; needs an API key."""
    return ProviderProfile(
        provider_id=provider_id,
        kind="free",
        chat=True,
        code_execution=True,
        repo_editing=True,
        streaming=False,
        tool_calling=False,
        requires_api_key=True,
        requires_oauth=False,
        requires_cli=False,
        requires_git_repo=True,
        supports_cancellation=True,
    )


def _local_profile(provider_id: str) -> ProviderProfile:
    """Local runtimes (ollama/openai-compatible): answer-only today — no repo
    editing, and no streaming yet (#154). No key, no OAuth, no CLI required."""
    return ProviderProfile(
        provider_id=provider_id,
        kind="local",
        chat=True,
        code_execution=False,
        repo_editing=False,
        streaming=False,
        tool_calling=False,
        requires_api_key=False,
        requires_oauth=False,
        requires_cli=False,
        requires_git_repo=False,
        supports_cancellation=True,
    )


# The single capability table. Derived from provider kind + the honest current
# behaviour; keep this the only place these truths live.
_PROFILES: dict[str, ProviderProfile] = {
    "claude": _account_profile("claude", repo_editing=True),
    "codex": _account_profile("codex", repo_editing=True),
    # Copilot's repository edits fail closed in OPai — the picker must say so.
    "copilot": _account_profile("copilot", repo_editing=False),
    "gemini": _free_profile("gemini"),
    "groq": _free_profile("groq"),
    "mistral": _free_profile("mistral"),
    "ollama": _local_profile("ollama"),
    "openai-compatible": _local_profile("openai-compatible"),
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
