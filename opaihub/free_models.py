"""Free API model registry for Vesta.

Defines FREE_MODEL_SPECS for providers that offer verified free API tiers
(Moonshot Kimi, Google Gemini, Groq, Mistral). Models appear in the picker even
without a key — grayed with a setup hint — so users can discover free options
without any configuration required.

Kimi (Moonshot AI) is listed first, so its flagship K2.6 model is the free-tier
default: it is what Vesta Auto picks as the cheapest safe cloud fallback
(``opaihub.gui_pipeline`` selects the first available free model) and what the
picker offers at the top of the free group.

Execution: free models hit public endpoints and always go through Vesta's
policy confirmation gate (``requires_confirmation=True``), consistent with how
all cloud/paid routes are treated. No network calls happen here; availability
is determined solely by env var presence (no latency in picker enumeration).

Relationship to ``opai.model_registry`` (F2, QA E2E 2026-07-17): this module
owns the *operational* spec for the free tier — API base, env key, setup hint,
picker labels. ``opai.model_registry._REGISTRY`` registers the same providers
(``gemini``/``groq``/``mistral``) and model ids with capability/display
metadata so every picker-visible model resolves through one validation path.
The two modules are pinned together by
``tests/test_free_model_registry.py``: every ``model_id`` here must be
registered there under the same provider, and no free provider may exist in
one module without the other.
"""

from __future__ import annotations

from typing import Any

from .credentials import CredentialStore

FREE_MODEL_SPECS: list[dict[str, Any]] = [
    {
        "id": "free:kimi:kimi-k2.6",
        "label": "Kimi · K2.6 (free tier)",
        "advanced_label": (
            "Moonshot Kimi K2.6 via Moonshot AI API "
            "(free-tier eligible; provider limits apply)"
        ),
        "provider": "kimi",
        "model_id": "kimi-k2.6",
        "api_base": "https://api.moonshot.ai/v1",
        "env_key": "MOONSHOT_API_KEY",
        "group": "free",
        "cost_level": "free-tier",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set MOONSHOT_API_KEY env var. Create a free-tier key at platform.moonshot.ai"
        ),
    },
    {
        "id": "free:kimi:kimi-k2.6-turbo",
        "label": "Kimi · K2.6 Turbo (free tier)",
        "advanced_label": (
            "Moonshot Kimi K2.6 Turbo via Moonshot AI API "
            "(free-tier eligible; provider limits apply)"
        ),
        "provider": "kimi",
        "model_id": "kimi-k2.6-turbo",
        "api_base": "https://api.moonshot.ai/v1",
        "env_key": "MOONSHOT_API_KEY",
        "group": "free",
        "cost_level": "free-tier",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set MOONSHOT_API_KEY env var. Create a free-tier key at platform.moonshot.ai"
        ),
    },
    {
        "id": "free:kimi:kimi-k2",
        "label": "Kimi · K2 (free tier)",
        "advanced_label": (
            "Moonshot Kimi K2 via Moonshot AI API "
            "(free-tier eligible; provider limits apply)"
        ),
        "provider": "kimi",
        "model_id": "kimi-k2",
        "api_base": "https://api.moonshot.ai/v1",
        "env_key": "MOONSHOT_API_KEY",
        "group": "free",
        "cost_level": "free-tier",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set MOONSHOT_API_KEY env var. Create a free-tier key at platform.moonshot.ai"
        ),
    },
    {
        "id": "free:kimi:kimi-latest",
        "label": "Kimi · Latest (free tier)",
        "advanced_label": (
            "Moonshot Kimi Latest via Moonshot AI API "
            "(free-tier eligible; provider limits apply)"
        ),
        "provider": "kimi",
        "model_id": "kimi-latest",
        "api_base": "https://api.moonshot.ai/v1",
        "env_key": "MOONSHOT_API_KEY",
        "group": "free",
        "cost_level": "free-tier",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set MOONSHOT_API_KEY env var. Create a free-tier key at platform.moonshot.ai"
        ),
    },
    {
        "id": "free:gemini:gemini-3.1-flash-lite",
        "label": "Gemini · 3.1 Flash-Lite (free tier)",
        "advanced_label": (
            "Google Gemini 3.1 Flash-Lite via Google AI API "
            "(free-tier eligible; provider limits apply)"
        ),
        "provider": "gemini",
        "model_id": "gemini-3.1-flash-lite",
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GOOGLE_API_KEY",
        "group": "free",
        "cost_level": "free-tier",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set GOOGLE_API_KEY env var. Create a free-tier key at aistudio.google.com"
        ),
    },
    {
        "id": "free:groq:openai/gpt-oss-120b",
        "label": "Groq · GPT-OSS 120B (free tier)",
        "advanced_label": (
            "OpenAI GPT-OSS 120B via Groq API "
            "(free-tier eligible; provider limits apply)"
        ),
        "provider": "groq",
        "model_id": "openai/gpt-oss-120b",
        "api_base": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "group": "free",
        "cost_level": "free-tier",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set GROQ_API_KEY env var. Create a free-plan key at console.groq.com"
        ),
    },
    {
        "id": "free:mistral:mistral-small-latest",
        "label": "Mistral · Small (free tier)",
        "advanced_label": (
            "Mistral Small via Mistral AI API "
            "(free-tier eligible; provider limits apply)"
        ),
        "provider": "mistral",
        "model_id": "mistral-small-latest",
        "api_base": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "group": "free",
        "cost_level": "free-tier",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set MISTRAL_API_KEY env var. Create a free-mode key at console.mistral.ai"
        ),
    },
]

_SPEC_BY_ID: dict[str, dict[str, Any]] = {s["id"]: s for s in FREE_MODEL_SPECS}


def list_free_models() -> list[dict[str, Any]]:
    """Return picker entries for all free API models.

    Every model appears regardless of API key presence. Models without a
    configured key are returned with ``available=False`` and a
    ``disabled_reason`` containing the setup hint, exactly matching the
    pattern used for unavailable account models (e.g. Claude when not
    connected).
    """
    options: list[dict[str, Any]] = []
    credentials = CredentialStore()
    for spec in FREE_MODEL_SPECS:
        api_key = credentials.get(spec["provider"])
        available = bool(api_key)
        options.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "advanced_label": spec["advanced_label"],
                "provider": spec["provider"],
                "model": spec["model_id"],
                "kind": spec["kind"],
                "group": spec["group"],
                "paid": spec["paid"],
                "available": available,
                "disabled_reason": None if available else spec["setup_hint"],
                "api_base": spec["api_base"],
                "env_key": spec["env_key"],
            }
        )
    return options


def spec_for_model_id(model_id: str) -> dict[str, Any] | None:
    """Return a spec for an id like ``'free:gemini:gemini-3.1-flash-lite'``.

    Returns ``None`` for unknown ids or empty/None input.
    """
    return _SPEC_BY_ID.get(str(model_id or ""))
