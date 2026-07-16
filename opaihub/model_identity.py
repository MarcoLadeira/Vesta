"""Stable model identities used only at usage read/write boundaries."""

from __future__ import annotations

from typing import Any

from opai import model_registry


def _picker_parts(model_id: str) -> tuple[str, str, str] | None:
    parts = model_id.split(":", 2)
    if len(parts) != 3 or parts[0].lower() not in {"account", "free"}:
        return None
    return parts[0].lower(), parts[1].lower(), parts[2]


def canonical_usage_model_id(model_id: str) -> str:
    """Resolve declared account aliases while leaving every other ID intact.

    Unknown and historical identifiers are evidence.  They are deliberately
    not normalized heuristically because similarly named provider SKUs can
    have different pricing and quotas.
    """

    raw = str(model_id or "")
    parts = _picker_parts(raw)
    if parts is None:
        return raw
    kind, provider, provider_model = parts
    if kind != "account":
        return raw
    resolved = model_registry.resolve_id(provider, provider_model)
    if resolved is None:
        return raw
    return f"account:{provider}:{resolved}"


def model_provider(model_id: str) -> str:
    """Return the provider encoded in a picker/local ID, or an empty string."""

    raw = str(model_id or "")
    parts = _picker_parts(raw)
    if parts is not None:
        return parts[1]
    if ":" not in raw:
        return ""
    provider = raw.split(":", 1)[0].strip().lower()
    return provider if provider else ""


def historical_model_descriptor(model_id: str) -> dict[str, Any]:
    """Build a safe Settings row for a model seen only in immutable history."""

    raw = str(model_id or "")
    canonical = canonical_usage_model_id(raw)
    provider = model_provider(canonical)
    display = canonical.rsplit(":", 1)[-1] if canonical else "Unknown model"
    parts = _picker_parts(canonical)
    if parts is not None and parts[0] == "account":
        spec = model_registry.find(parts[1], parts[2])
        if spec is not None:
            display = spec.display
    return {
        "id": canonical,
        "rawId": raw,
        "provider": provider,
        "display": display,
        "historical": True,
    }
