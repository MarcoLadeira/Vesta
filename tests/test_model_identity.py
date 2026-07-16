"""Stable usage identities across aliases and catalog changes."""

from __future__ import annotations

from opaihub.model_identity import (
    canonical_usage_model_id,
    historical_model_descriptor,
    model_provider,
)


def test_known_account_aliases_share_one_canonical_identity() -> None:
    assert canonical_usage_model_id("account:claude:opus-4.8") == (
        "account:claude:opus"
    )
    assert canonical_usage_model_id("account:claude:claude-opus") == (
        "account:claude:opus"
    )
    assert canonical_usage_model_id("account:claude:opus") == "account:claude:opus"


def test_unknown_account_and_free_or_local_ids_are_not_rewritten() -> None:
    values = (
        "account:claude:retired-model-2024",
        "free:gemini:gemini-3.1-flash-lite",
        "free:groq:llama-3.3-70b-versatile",
        "ollama:qwen2.5-coder:latest",
        "custom-provider-model",
    )

    assert [canonical_usage_model_id(value) for value in values] == list(values)


def test_model_provider_handles_picker_and_local_ids_conservatively() -> None:
    assert model_provider("account:claude:opus") == "claude"
    assert model_provider("free:gemini:flash") == "gemini"
    assert model_provider("ollama:qwen2.5-coder") == "ollama"
    assert model_provider("auto") == ""


def test_historical_descriptor_keeps_raw_id_and_adds_canonical_identity() -> None:
    descriptor = historical_model_descriptor("account:claude:opus-4.8")

    assert descriptor == {
        "id": "account:claude:opus",
        "rawId": "account:claude:opus-4.8",
        "provider": "claude",
        "display": "Opus 4.8",
        "historical": True,
    }


def test_removed_model_remains_a_historical_row_without_false_aliasing() -> None:
    descriptor = historical_model_descriptor("free:gemini:retired-special-sku")

    assert descriptor["id"] == "free:gemini:retired-special-sku"
    assert descriptor["display"] == "retired-special-sku"
    assert descriptor["historical"] is True
