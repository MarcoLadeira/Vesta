"""Safe provider connection, error, and display-name contracts.

This module is deliberately framework-free. Provider processes feed it raw
diagnostics and every UI receives the same redacted, OPai-first dictionary.
Credential values are never accepted or returned as structured fields.
"""

from __future__ import annotations

import re
from typing import Any

from opai.model_registry import display_map as _display_map


AUTH_STATUSES = (
    "unknown",
    "not_configured",
    "connected",
    "invalid",
    "expired",
    "disconnected",
    "rate_limited",
    "provider_unavailable",
    "misconfigured",
    "checking",
)

ERROR_CODES = (
    "AUTH_MISSING",
    "AUTH_INVALID",
    "AUTH_EXPIRED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_TIMEOUT",
    "PROVIDER_UNAVAILABLE",
    "NETWORK_ERROR",
    "MODEL_UNAVAILABLE",
    "CONFIG_INVALID",
    "CONTEXT_TOO_LARGE",
    "STREAM_ABORTED",
    "USER_CANCELLED",
    "NO_RESPONSE",
    "UNKNOWN",
)

_ERROR_SPECS: dict[str, dict[str, Any]] = {
    "AUTH_MISSING": {
        "authStatus": "not_configured",
        "title": "OPai needs a valid connection.",
        "userMessage": "Connect a provider account in Settings before sending this request.",
        "actions": ["open_settings", "reconnect"],
        "retryable": False,
    },
    "AUTH_INVALID": {
        "authStatus": "invalid",
        "title": "This account's sign-in was rejected by the provider.",
        "userMessage": (
            "OPai detected a signed-in session for this account, but the "
            "request itself was refused by the provider (a 401). This "
            "usually means the session expired or was revoked outside "
            "OPai — sign-in checks can look fine locally right up until a "
            "real request fails. Disconnect this account, then sign in "
            "again to get a fresh session."
        ),
        "actions": ["disconnect", "reconnect", "open_settings", "show_details"],
        "retryable": False,
    },
    "AUTH_EXPIRED": {
        "authStatus": "expired",
        "title": "This account's session has expired.",
        "userMessage": (
            "The provider rejected this request because the session expired. "
            "Disconnect this account, then sign in again to get a fresh "
            "session — Retry alone will keep failing with the same session."
        ),
        "actions": ["disconnect", "reconnect", "open_settings", "show_details"],
        "retryable": False,
    },
    "PROVIDER_RATE_LIMITED": {
        "authStatus": "rate_limited",
        "title": "OPai is being rate limited.",
        "userMessage": "Wait a moment, then retry or choose another connection.",
        "actions": ["retry", "open_settings"],
        "retryable": True,
    },
    "PROVIDER_TIMEOUT": {
        "authStatus": "unknown",
        "title": "OPai did not receive a response in time.",
        "userMessage": "Retry with a smaller request or choose a faster mode.",
        "actions": ["retry", "change_mode"],
        "retryable": True,
    },
    "PROVIDER_UNAVAILABLE": {
        "authStatus": "provider_unavailable",
        "title": "OPai could not reach this provider.",
        "userMessage": "The provider is unavailable. Retry later or choose another connection.",
        "actions": ["retry", "open_settings", "show_details"],
        "retryable": True,
    },
    "NETWORK_ERROR": {
        "authStatus": "unknown",
        "title": "OPai could not connect.",
        "userMessage": "Check your network connection and retry.",
        "actions": ["retry", "show_details"],
        "retryable": True,
    },
    "MODEL_UNAVAILABLE": {
        "authStatus": "connected",
        "title": "This OPai mode is unavailable.",
        "userMessage": "Choose another mode or update the provider connection.",
        "actions": ["change_mode", "open_settings", "show_details"],
        "retryable": False,
    },
    "CONFIG_INVALID": {
        "authStatus": "misconfigured",
        "title": "OPai found a problem in this provider's config.",
        "userMessage": (
            "The provider CLI's config file has an invalid setting, so it won't "
            "start. Run the one-click repair (it backs up your config first), "
            "then retry."
        ),
        "actions": ["repair_config", "open_settings", "show_details"],
        "retryable": False,
    },
    "CONTEXT_TOO_LARGE": {
        "authStatus": "connected",
        "title": "This request contains too much context.",
        "userMessage": "Reduce the prompt or attached context, then retry.",
        "actions": ["edit", "retry"],
        "retryable": True,
    },
    "STREAM_ABORTED": {
        "authStatus": "unknown",
        "title": "OPai lost the response stream.",
        "userMessage": "The partial response is preserved. Retry to start a new request.",
        "actions": ["retry", "show_details"],
        "retryable": True,
    },
    "USER_CANCELLED": {
        "authStatus": "unknown",
        "title": "OPai stopped safely.",
        "userMessage": "Generation was stopped by you.",
        "actions": ["retry", "edit"],
        "retryable": True,
    },
    "NO_RESPONSE": {
        "authStatus": "connected",
        "title": "OPai received no response.",
        "userMessage": "Retry or choose another connection.",
        "actions": ["retry", "open_settings"],
        "retryable": True,
    },
    "UNKNOWN": {
        "authStatus": "unknown",
        "title": "OPai could not complete this request.",
        "userMessage": "Retry, or open technical details if the problem continues.",
        "actions": ["retry", "show_details"],
        "retryable": True,
    },
}

_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|session(?:[_-]?token)?|token|cookie)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_SECRET = re.compile(r"(?i)(authorization\s*:\s*bearer\s+|bearer\s+)([^\s,;]+)")
_RAW_SECRET = re.compile(r"(?i)\b(?:sk|token)[-_][A-Za-z0-9._-]{8,}\b")


def redact_secrets(detail: Any) -> str:
    """Redact common credential shapes without logging or interpreting them."""

    text = str(detail or "")
    text = _BEARER_SECRET.sub(lambda match: match.group(1) + "[REDACTED]", text)
    text = _ASSIGNMENT_SECRET.sub(
        lambda match: match.group(1) + match.group(2) + "[REDACTED]", text
    )
    return _RAW_SECRET.sub("[REDACTED]", text)


def dedupe_error_text(detail: Any) -> str:
    """Collapse adjacent duplicate lines and exact concatenated repetitions."""

    text = str(detail or "").strip()
    if not text:
        return ""
    if len(text) % 2 == 0:
        midpoint = len(text) // 2
        if text[:midpoint] == text[midpoint:]:
            text = text[:midpoint].strip()
    unique_lines: list[str] = []
    for line in text.splitlines():
        clean = line.strip()
        if clean and (not unique_lines or clean != unique_lines[-1]):
            unique_lines.append(clean)
    return "\n".join(unique_lines)[:2000]


def classify_error_code(
    detail: Any, *, returncode: int | None = None, timed_out: bool = False
) -> str:
    """Classify a provider diagnostic into the stable OPai error vocabulary."""

    if timed_out:
        return "PROVIDER_TIMEOUT"
    low = str(detail or "").lower()
    if "expired" in low and any(
        word in low for word in ("token", "oauth", "session", "credential")
    ):
        return "AUTH_EXPIRED"
    if any(
        word in low
        for word in (
            "no credentials",
            "missing credential",
            "not configured",
            "no api key",
            # The claude CLI's own signed-out sentinel ("Not logged in ·
            # Please run /login"): there is no session at all, so this is
            # missing auth — telling the user to Disconnect (AUTH_INVALID's
            # remedy) would be nonsense here.
            "please run /login",
        )
    ):
        return "AUTH_MISSING"
    if any(
        word in low
        for word in (
            "401",
            "unauthorized",
            "invalid authentication",
            "not logged in",
            "sign in required",
            "forbidden",
            "403",
        )
    ):
        return "AUTH_INVALID"
    if any(
        word in low
        for word in ("429", "rate limit", "too many requests", "quota exceeded")
    ):
        return "PROVIDER_RATE_LIMITED"
    if "context" in low and any(
        word in low for word in ("large", "limit", "exceed", "too long")
    ):
        return "CONTEXT_TOO_LARGE"
    # Provider CLI config is broken before it can even authenticate, e.g. Codex's
    # `Error loading configuration: …unknown variant 'default', expected 'fast'
    # or 'flex'`. Catch it so the user gets the one-click repair, not a generic
    # "could not complete this request".
    if "error loading configuration" in low or (
        "unknown variant" in low and "expected" in low
    ):
        return "CONFIG_INVALID"
    if any(
        word in low
        for word in ("unknown model", "model not found", "model unavailable")
    ):
        return "MODEL_UNAVAILABLE"
    if any(
        word in low
        for word in ("connection reset", "network", "dns", "name resolution", "offline")
    ):
        return "NETWORK_ERROR"
    if any(
        word in low
        for word in (
            "service unavailable",
            "provider unavailable",
            "502",
            "503",
            "maintenance",
        )
    ):
        return "PROVIDER_UNAVAILABLE"
    if any(
        word in low for word in ("stream aborted", "broken pipe", "incomplete stream")
    ):
        return "STREAM_ABORTED"
    if not low and returncode == 0:
        return "NO_RESPONSE"
    return "UNKNOWN"


def normalize_provider_error(
    provider: str,
    detail: Any,
    *,
    model: str | None = None,
    returncode: int | None = None,
    timed_out: bool = False,
) -> dict[str, Any]:
    """Return one redacted error payload for backend, bridge, and browser use."""

    safe_detail = dedupe_error_text(redact_secrets(detail))
    code = classify_error_code(safe_detail, returncode=returncode, timed_out=timed_out)
    spec = _ERROR_SPECS[code]
    return {
        "code": code,
        "authStatus": spec["authStatus"],
        "title": spec["title"],
        "userMessage": spec["userMessage"],
        "recoveryActions": list(spec["actions"]),
        "technicalMessage": safe_detail,
        "provider": str(provider or "unknown"),
        "model": str(model or ""),
        "statusCode": 401 if "401" in safe_detail else None,
        "returnCode": returncode,
        "retryable": bool(spec["retryable"]),
    }


# Lookup tables for the simple (non-advanced) picker label.
# Picker display names derive from the single source of truth in
# opai.model_registry (#170) — kept identical to the previous hardcoded tables.
_CLAUDE_DISPLAY: dict[str, str] = _display_map("claude")
_CODEX_DISPLAY: dict[str, str] = _display_map("codex")
_COPILOT_DISPLAY: dict[str, str] = _display_map("copilot")


def provider_display_name(
    provider: str, model: str | None = None, *, advanced: bool = False
) -> str:
    """Return a picker label for a provider/model pair.

    Simple (``advanced=False``): provider-prefixed model name used in the model
    picker — e.g. ``"Claude · Sonnet 4.6"``, ``"Codex · GPT-5.5"``.

    Advanced (``advanced=True``): full diagnostic string for the inspector and
    hover tooltip — e.g. ``"Claude Sonnet 4.6 via Anthropic account connector"``.
    The advanced format is unchanged from the previous implementation.
    """
    provider_id = str(provider or "").lower()
    model_id = str(model or "").lower()

    if advanced:
        if provider_id == "claude":
            models = {"haiku": "Haiku 4.5", "sonnet": "Sonnet 4.6", "opus": "Opus 4.8"}
            return f"Claude {models.get(model_id, model or 'account')} via Anthropic account connector"
        if provider_id == "codex":
            return f"Codex {model or 'account'} via OpenAI account connector"
        if provider_id == "copilot":
            return f"GitHub Copilot {model or 'account'} via account connector"
        if provider_id == "local":
            return f"{model or 'Local model'} on this device"
        return f"{provider or 'Automatic'} {model or ''}".strip()

    # Simple picker label: provider-prefixed model name.
    if provider_id == "claude":
        display = _CLAUDE_DISPLAY.get(model_id, model or "model")
        return f"Claude · {display}"
    if provider_id == "codex":
        display = _CODEX_DISPLAY.get(model_id, model or "model")
        return f"Codex · {display}"
    if provider_id == "copilot":
        display = _COPILOT_DISPLAY.get(model_id, model or "model")
        return f"Copilot · {display}"
    if provider_id == "local":
        return "OPai · Local mode"
    if provider_id in {"auto", ""}:
        return "OPai · Auto mode"
    # Generic fallback keeps OPai branding for any unrecognized provider.
    return f"OPai · {(model or provider or 'model').strip()}"
