"""Explicitly opt-in live provider smoke tests.

These tests are skipped in CI and locally unless both gates are set. They may
consume provider allowance, so the model allowlist is mandatory.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opaihub.provider_catalog import provider_ids


LIVE_ENABLED = (
    os.environ.get("OPAI_LIVE_PROVIDER_SMOKE") == "1"
    and os.environ.get("OPAI_CONFIRM_CLOUD_TESTS") == "YES"
)


def _live_models() -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.environ.get("OPAI_LIVE_MODELS", "").split(",")
        if item.strip()
    )


def _selected_live_providers() -> frozenset[str]:
    return frozenset(
        item.strip().lower()
        for item in os.environ.get("OPAI_LIVE_PROVIDER_SMOKE_PROVIDERS", "").split(",")
        if item.strip()
    )


def _model_for_provider(provider_id: str) -> str | None:
    from opaihub.model_identity import model_provider

    return next(
        (
            model_id
            for model_id in _live_models()
            if model_provider(model_id) == provider_id
        ),
        None,
    )


def live_provider_prerequisite(provider_id: str) -> str | None:
    """Return the exact opt-in condition that prevents one provider smoke call."""

    if os.environ.get("OPAI_LIVE_PROVIDER_SMOKE") != "1":
        return "requires OPAI_LIVE_PROVIDER_SMOKE=1"
    if os.environ.get("OPAI_CONFIRM_CLOUD_TESTS") != "YES":
        return "requires OPAI_CONFIRM_CLOUD_TESTS=YES"
    if provider_id not in _selected_live_providers():
        return f"requires {provider_id} in OPAI_LIVE_PROVIDER_SMOKE_PROVIDERS"
    if _model_for_provider(provider_id) is None:
        return f"requires a {provider_id} model in OPAI_LIVE_MODELS"
    return None


class LiveProviderSmokeGateTests(unittest.TestCase):
    def test_each_catalog_provider_explains_the_missing_live_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            for provider_id in provider_ids():
                with self.subTest(provider_id=provider_id):
                    self.assertEqual(
                        live_provider_prerequisite(provider_id),
                        "requires OPAI_LIVE_PROVIDER_SMOKE=1",
                    )

    def test_selected_provider_requires_a_matching_explicit_model(self) -> None:
        environment = {
            "OPAI_LIVE_PROVIDER_SMOKE": "1",
            "OPAI_CONFIRM_CLOUD_TESTS": "YES",
            "OPAI_LIVE_PROVIDER_SMOKE_PROVIDERS": "codex",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                live_provider_prerequisite("codex"),
                "requires a codex model in OPAI_LIVE_MODELS",
            )
        environment["OPAI_LIVE_MODELS"] = "account:codex:gpt-5.6"
        with patch.dict(os.environ, environment, clear=True):
            self.assertIsNone(live_provider_prerequisite("codex"))

    def test_unselected_provider_has_a_provider_specific_prerequisite(self) -> None:
        environment = {
            "OPAI_LIVE_PROVIDER_SMOKE": "1",
            "OPAI_CONFIRM_CLOUD_TESTS": "YES",
            "OPAI_LIVE_PROVIDER_SMOKE_PROVIDERS": "codex",
            "OPAI_LIVE_MODELS": "account:codex:gpt-5.6",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                live_provider_prerequisite("claude"),
                "requires claude in OPAI_LIVE_PROVIDER_SMOKE_PROVIDERS",
            )


def _run_sandboxed_provider_smoke(provider_id: str, model_id: str) -> dict:
    """Run one explicitly selected model from an empty temporary directory."""

    from opai.app_state import ask
    from opaihub.model_identity import model_provider

    if model_provider(model_id) != provider_id:
        raise AssertionError(f"model {model_id!r} is not owned by {provider_id!r}")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if any(root.iterdir()):
            raise AssertionError("live smoke sandbox must start empty")
        # The task is fixed, has no repository content, and edits are disabled.
        return ask(
            root,
            "Reply with exactly: OK",
            model_choice=model_id,
            allow_cloud=True,
            allow_edits=False,
            mode="ask",
        )


class SelectedLiveProviderSmokeTests(unittest.TestCase):
    def test_each_catalog_provider_requires_its_own_explicit_opt_in(self) -> None:
        for provider_id in provider_ids():
            with self.subTest(provider_id=provider_id):
                prerequisite = live_provider_prerequisite(provider_id)
                if prerequisite is not None:
                    self.skipTest(prerequisite)
                model_id = _model_for_provider(provider_id)
                self.assertIsNotNone(model_id)
                result = _run_sandboxed_provider_smoke(provider_id, model_id or "")
                self.assertIn(
                    result.get("status"),
                    {
                        "answered_by_account",
                        "answered_by_free_api",
                        "answered_locally",
                    },
                    result,
                )
                self.assertTrue(str(result.get("answer") or "").strip(), result)


@unittest.skipUnless(LIVE_ENABLED, "live cloud smoke tests require both explicit gates")
class LiveProviderSmokeTests(unittest.TestCase):
    def test_explicit_model_allowlist_returns_nonempty_answers(self) -> None:
        if _selected_live_providers():
            self.skipTest("provider-scoped smoke owns explicitly selected providers")
        models = _live_models()
        self.assertTrue(models, "OPAI_LIVE_MODELS must explicitly list models to call")
        for model_id in models:
            with self.subTest(model=model_id):
                from opaihub.model_identity import model_provider

                provider_id = model_provider(model_id)
                self.assertTrue(
                    provider_id, f"model must encode its provider: {model_id}"
                )
                result = _run_sandboxed_provider_smoke(provider_id, model_id)
                self.assertIn(
                    result.get("status"),
                    {
                        "answered_by_account",
                        "answered_by_free_api",
                        "answered_locally",
                    },
                    result,
                )
                self.assertTrue(str(result.get("answer") or "").strip(), result)


if __name__ == "__main__":
    unittest.main()
