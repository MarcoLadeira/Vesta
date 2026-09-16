"""Paid direct-API model registry for Vesta (#673).

Sibling to ``free_models.py``, same shape (picker entries keyed by
``CredentialStore`` presence), deliberately a separate module rather than an
extension of it: ``free_models.py``'s entire contract is that its models are
genuinely free — ``$0`` is the actual cost, not an estimate — and every
consumer of ``FREE_MODEL_SPECS`` relies on that. Folding a real per-token
provider into the same list would make ``paid: False`` a lie for it, or
force every existing free-model consumer to branch on a new field it never
had to check before. A new module keeps both contracts true by construction.

DeepSeek (#673) is the first entry. Real, sourced pricing lives in
``deepseek_pricing.py``, not inline here — this module is the *picker*
spec (id, label, credential), matching what ``free_models.py`` owns for its
tier.

Scope note: ``deepseek-v4-flash``/``deepseek-v4-pro`` in non-thinking mode
were the whole of #673 Phase 1. Thinking mode's continuity requirement
(``thinking_supported=True`` below is honest about the *provider's*
capability) — preserving ``reasoning_content`` across tool-call turns or the
provider rejects the continuation — is #673 workstream A4, and is now
implemented (#674): ``vestahub.local_runner.ThinkingControl`` and
``vestahub.tool_loop.ToolProtocolAtom``/``ReasoningContinuityError``. What
remains is A5's picker surface (Auto/On/Off + effort exposed in the model
picker UI) — ``spec_for_model_id`` here still returns no thinking-mode
selection, so a caller wanting it constructs ``ThinkingControl`` directly
and passes it to ``runner_for_model``.
"""

from __future__ import annotations

from typing import Any

from .credentials import CredentialStore

PAID_MODEL_SPECS: list[dict[str, Any]] = [
    {
        "id": "paid:deepseek:deepseek-v4-flash",
        "label": "DeepSeek · V4 Flash",
        "advanced_label": "DeepSeek V4 Flash via DeepSeek API (paid, per-token)",
        "provider": "deepseek",
        "model_id": "deepseek-v4-flash",
        "api_base": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "group": "paid",
        "cost_level": "low",
        "kind": "paid",
        "paid": True,
        "context_tokens": 1_000_000,
        "thinking_supported": True,
        "setup_hint": (
            "Set DEEPSEEK_API_KEY env var. Create a key at platform.deepseek.com"
        ),
    },
    {
        "id": "paid:deepseek:deepseek-v4-pro",
        "label": "DeepSeek · V4 Pro",
        "advanced_label": "DeepSeek V4 Pro via DeepSeek API (paid, per-token)",
        "provider": "deepseek",
        "model_id": "deepseek-v4-pro",
        "api_base": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "group": "paid",
        "cost_level": "moderate",
        "kind": "paid",
        "paid": True,
        "context_tokens": 1_000_000,
        "thinking_supported": True,
        "setup_hint": (
            "Set DEEPSEEK_API_KEY env var. Create a key at platform.deepseek.com"
        ),
    },
]

_SPEC_BY_ID: dict[str, dict[str, Any]] = {s["id"]: s for s in PAID_MODEL_SPECS}


def list_paid_api_models() -> list[dict[str, Any]]:
    """Picker entries for all paid direct-API models.

    Same availability contract as ``list_free_models``: every model appears
    regardless of key presence, grayed with a setup hint when unconfigured.
    Unlike the free tier, ``paid`` is always ``True`` here — a caller must
    never assume a model returned by this function is free to invoke.
    """

    options: list[dict[str, Any]] = []
    credentials = CredentialStore()
    for spec in PAID_MODEL_SPECS:
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
    """Return a spec for an id like ``'paid:deepseek:deepseek-v4-flash'``.

    Returns ``None`` for unknown ids or empty/None input.
    """
    return _SPEC_BY_ID.get(str(model_id or ""))
