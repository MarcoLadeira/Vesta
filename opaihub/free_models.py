"""Free API model registry for OPai.

Defines FREE_MODEL_SPECS for providers that offer free API tiers (DeepSeek,
Google Gemini, Groq, Mistral). Models appear in the picker even without a key
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
        "id": "free:deepseek:deepseek-chat",
        "label": "DeepSeek · V3 Chat (free)",
        "advanced_label": "DeepSeek V3 Chat via DeepSeek API (free tier)",
        "provider": "deepseek",
        "model_id": "deepseek-chat",
        "api_base": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
        "group": "free",
        "cost_level": "free",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set DEEPSEEK_API_KEY env var. "
            "Get a free key at platform.deepseek.com"
        ),
    },
    {
        "id": "free:deepseek:deepseek-reasoner",
        "label": "DeepSeek · R1 Reasoner (free)",
        "advanced_label": "DeepSeek R1 Reasoning Model via DeepSeek API (free tier)",
        "provider": "deepseek",
        "model_id": "deepseek-reasoner",
        "api_base": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
        "group": "free",
        "cost_level": "free",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set DEEPSEEK_API_KEY env var. "
            "Get a free key at platform.deepseek.com"
        ),
    },
    {
        "id": "free:gemini:gemini-2.0-flash",
        "label": "Gemini · 2.0 Flash (free)",
        "advanced_label": "Google Gemini 2.0 Flash via Google AI API (free tier)",
        "provider": "gemini",
        "model_id": "gemini-2.0-flash",
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GOOGLE_API_KEY",
        "group": "free",
        "cost_level": "free",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set GOOGLE_API_KEY env var. "
            "Get a free key at aistudio.google.com"
        ),
    },
    {
        "id": "free:groq:llama-3.3-70b-versatile",
        "label": "Groq · Llama 3.3 (free)",
        "advanced_label": "Meta Llama 3.3 70B via Groq API (free tier, very fast inference)",
        "provider": "groq",
        "model_id": "llama-3.3-70b-versatile",
        "api_base": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "group": "free",
        "cost_level": "free",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set GROQ_API_KEY env var. "
            "Get a free key at console.groq.com"
        ),
    },
    {
        "id": "free:mistral:mistral-small-latest",
        "label": "Mistral · Small (free)",
        "advanced_label": "Mistral Small via Mistral AI API (free/low-cost tier)",
        "provider": "mistral",
        "model_id": "mistral-small-latest",
        "api_base": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "group": "free",
        "cost_level": "free",
        "kind": "free",
        "paid": False,
        "setup_hint": (
            "Set MISTRAL_API_KEY env var. "
            "Get a free key at console.mistral.ai"
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
    """Return the spec dict for a picker id like ``'free:deepseek:deepseek-chat'``.

    Returns ``None`` for unknown ids or empty/None input.
    """
    return _SPEC_BY_ID.get(str(model_id or ""))
