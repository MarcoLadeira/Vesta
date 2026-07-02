"""Free API model registry for OPai.

Defines FREE_MODEL_SPECS for providers that offer verified free API tiers
(Google Gemini, Groq, Mistral). Models appear in the picker even without a key
— grayed with a setup hint — so users can discover free options without any
configuration required.

Execution: free models hit public endpoints and always go through OPai's
policy confirmation gate (``requires_confirmation=True``), consistent with how
all cloud/paid routes are treated. No network calls happen here; availability
is determined solely by env var presence (no latency in picker enumeration).
"""

from __future__ import annotations

import os
from typing import Any

FREE_MODEL_SPECS: list[dict[str, Any]] = [
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
    for spec in FREE_MODEL_SPECS:
        api_key = os.environ.get(spec["env_key"], "").strip()
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
