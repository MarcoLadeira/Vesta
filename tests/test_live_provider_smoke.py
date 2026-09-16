"""Explicitly opt-in live provider smoke tests.

These tests are skipped in CI and locally unless the cloud-confirmation gates,
an explicit provider selector, a matching model, and a safe adapter preflight
all pass. They may consume provider allowance, so the model allowlist is
mandatory.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from vestahub.provider_catalog import (
    CATALOG_VERSION,
    PROTOCOL_VERSION,
    provider_ids,
    provider_record,
)
from vestahub.provider_canary import (
    live_provider_prerequisite,
    model_for_provider,
    run_selected_provider_canary,
)
from vestahub.model_identity import canonical_usage_model_id
from vestahub.provider_protocol import ProviderReadiness


class LiveProviderSmokeGateTests(unittest.TestCase):
    def test_each_catalog_provider_explains_the_missing_live_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            for provider_id in provider_ids():
                with self.subTest(provider_id=provider_id):
                    self.assertEqual(
                        live_provider_prerequisite(provider_id),
                        "requires VESTA_LIVE_PROVIDER_SMOKE=1",
                    )

    def test_selected_provider_requires_a_matching_explicit_model(self) -> None:
        environment = {
            "VESTA_LIVE_PROVIDER_SMOKE": "1",
            "VESTA_CONFIRM_CLOUD_TESTS": "YES",
            "VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS": "codex",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                live_provider_prerequisite("codex"),
                "requires a codex model in VESTA_LIVE_MODELS",
            )
        environment["VESTA_LIVE_MODELS"] = "account:codex:gpt-5.6"
        with patch.dict(os.environ, environment, clear=True):
            self.assertIsNone(live_provider_prerequisite("codex"))

    def test_unselected_provider_has_a_provider_specific_prerequisite(self) -> None:
        environment = {
            "VESTA_LIVE_PROVIDER_SMOKE": "1",
            "VESTA_CONFIRM_CLOUD_TESTS": "YES",
            "VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS": "codex",
            "VESTA_LIVE_MODELS": "account:codex:gpt-5.6",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                live_provider_prerequisite("claude"),
                "requires claude in VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS",
            )

    def test_model_allowlist_never_bypasses_the_provider_selector(self) -> None:
        environment = {
            "VESTA_LIVE_PROVIDER_SMOKE": "1",
            "VESTA_CONFIRM_CLOUD_TESTS": "YES",
            "VESTA_LIVE_MODELS": "account:codex:gpt-5.6",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                live_provider_prerequisite("codex"),
                "requires codex in VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS",
            )


def _adapter_with_ready_contract() -> MagicMock:
    record = provider_record("codex")
    adapter = MagicMock()
    adapter.profile = SimpleNamespace(
        catalog_version=CATALOG_VERSION,
        protocol_version=PROTOCOL_VERSION,
        capability_status=dict(record["capabilities"]),
        contract={
            "requirements": dict(record["requirements"]),
            "cancellation": dict(record["cancellation"]),
            "unsupportedBehavior": dict(record["unsupported_behavior"]),
        },
        requires_cli=True,
        requires_api_key=False,
        requires_oauth=True,
    )
    adapter.probe.return_value = {
        "cliPresent": True,
        "detected": True,
        "authStatus": "connected",
    }
    adapter.readiness.return_value = ProviderReadiness(
        provider_id="codex",
        installed=True,
        configured=True,
        authenticated=True,
        healthy=True,
        protocol_version=PROTOCOL_VERSION,
    )
    return adapter


def _api_adapter_with_ready_contract() -> MagicMock:
    record = provider_record("groq")
    adapter = MagicMock()
    adapter.profile = SimpleNamespace(
        catalog_version=CATALOG_VERSION,
        protocol_version=PROTOCOL_VERSION,
        capability_status=dict(record["capabilities"]),
        contract={
            "requirements": dict(record["requirements"]),
            "cancellation": dict(record["cancellation"]),
            "unsupportedBehavior": dict(record["unsupported_behavior"]),
        },
        requires_cli=False,
        requires_api_key=True,
        requires_oauth=False,
    )
    adapter.probe.return_value = {
        "configured": True,
        "connected": True,
    }
    adapter.readiness.return_value = ProviderReadiness(
        provider_id="groq",
        configured=True,
        healthy=True,
        protocol_version=PROTOCOL_VERSION,
    )
    return adapter


class SelectedLiveProviderPreflightTests(unittest.TestCase):
    def _environment(self) -> dict[str, str]:
        return {
            "VESTA_LIVE_PROVIDER_SMOKE": "1",
            "VESTA_CONFIRM_CLOUD_TESTS": "YES",
            "VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS": "codex",
            "VESTA_LIVE_MODELS": "account:codex:gpt-5.6",
        }

    def test_unready_probe_skips_without_calling_ask(self) -> None:
        adapter = _adapter_with_ready_contract()
        adapter.readiness.return_value = ProviderReadiness(
            provider_id="codex",
            installed=False,
            healthy=False,
            degraded_reason="provider_not_installed",
            next_action="Install the Codex CLI, then retry.",
        )
        with (
            patch.dict(os.environ, self._environment(), clear=True),
            patch("vesta.app_state.ask") as ask,
        ):
            reason, result = run_selected_provider_canary(
                "codex",
                "account:codex:gpt-5.6",
                adapter_factory=lambda provider_id: adapter,
            )

        self.assertEqual(result, None)
        self.assertIn("codex", reason or "")
        adapter.probe.assert_called_once_with()
        ask.assert_not_called()

    def test_passed_model_must_belong_to_selected_provider(self) -> None:
        adapter = _adapter_with_ready_contract()
        with (
            patch.dict(os.environ, self._environment(), clear=True),
            patch("vesta.app_state.ask") as ask,
        ):
            reason, result = run_selected_provider_canary(
                "codex",
                "free:groq:openai/gpt-oss-120b",
                adapter_factory=lambda provider_id: adapter,
            )

        self.assertEqual(
            reason,
            "model 'free:groq:openai/gpt-oss-120b' is not owned by 'codex'",
        )
        self.assertIsNone(result)
        ask.assert_not_called()

    def test_passed_model_must_be_exactly_allowlisted(self) -> None:
        adapter = _adapter_with_ready_contract()
        with (
            patch.dict(os.environ, self._environment(), clear=True),
            patch("vesta.app_state.ask") as ask,
        ):
            reason, result = run_selected_provider_canary(
                "codex",
                "account:codex:gpt-5.5",
                adapter_factory=lambda provider_id: adapter,
            )

        self.assertEqual(
            reason,
            "requires exact model account:codex:gpt-5.5 in VESTA_LIVE_MODELS",
        )
        self.assertIsNone(result)
        ask.assert_not_called()

    def test_valid_probe_is_converted_and_contract_checked_before_ask(self) -> None:
        adapter = _adapter_with_ready_contract()
        expected_result = {"status": "answered_by_account", "answer": "OK"}
        with (
            patch.dict(os.environ, self._environment(), clear=True),
            patch("vesta.app_state.ask", return_value=expected_result) as ask,
        ):
            reason, result = run_selected_provider_canary(
                "codex",
                "account:codex:gpt-5.6",
                adapter_factory=lambda provider_id: adapter,
            )

        self.assertIsNone(reason)
        self.assertEqual(
            {key: result[key] for key in expected_result},
            expected_result,
        )
        self.assertIsNone(result["canary_observation"])
        adapter.probe.assert_called_once_with()
        adapter.validate_request.assert_called_once()
        request = adapter.validate_request.call_args.args[0]
        self.assertEqual(request.provider_id, "codex")
        self.assertEqual(request.protocol_version, PROTOCOL_VERSION)
        readiness_input = adapter.readiness.call_args.args[0]
        self.assertTrue(readiness_input["installed"])
        self.assertTrue(readiness_input["configured"])
        self.assertTrue(readiness_input["authenticated"])
        self.assertNotIn("authorised", readiness_input)
        ask.assert_called_once()

    def test_api_key_provider_forces_safe_probe_before_ask(self) -> None:
        adapter = _api_adapter_with_ready_contract()
        environment = {
            "VESTA_LIVE_PROVIDER_SMOKE": "1",
            "VESTA_CONFIRM_CLOUD_TESTS": "YES",
            "VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS": "groq",
            "VESTA_LIVE_MODELS": "free:groq:openai/gpt-oss-120b",
        }
        expected_result = {"status": "answered_by_free_api", "answer": "OK"}
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("vesta.app_state.ask", return_value=expected_result) as ask,
        ):
            reason, result = run_selected_provider_canary(
                "groq",
                "free:groq:openai/gpt-oss-120b",
                adapter_factory=lambda provider_id: adapter,
            )

        self.assertIsNone(reason)
        self.assertEqual(
            {key: result[key] for key in expected_result},
            expected_result,
        )
        self.assertIsNone(result["canary_observation"])
        adapter.probe.assert_called_once_with(force=True)
        self.assertTrue(adapter.readiness.call_args.args[0]["healthy"])
        ask.assert_called_once()


class SelectedLiveProviderSmokeTests(unittest.TestCase):
    def test_each_catalog_provider_requires_its_own_explicit_opt_in(self) -> None:
        for provider_id in provider_ids():
            with self.subTest(provider_id=provider_id):
                prerequisite = live_provider_prerequisite(provider_id)
                if prerequisite is not None:
                    self.skipTest(prerequisite)
                model_id = model_for_provider(provider_id)
                self.assertIsNotNone(model_id)
                prerequisite, result = run_selected_provider_canary(
                    provider_id,
                    model_id or "",
                )
                if prerequisite is not None:
                    self.skipTest(prerequisite)
                self.assertIn(
                    result.get("status"),
                    {
                        "answered_by_account",
                        "answered_by_free_api",
                        "answered_by_paid_api",
                    },
                    result,
                )
                self.assertEqual(result.get("answer"), "OK", result)
                observation = result.get("canary_observation")
                self.assertIsInstance(observation, dict, result)
                self.assertEqual(observation.get("provider_id"), provider_id, result)
                self.assertEqual(observation.get("model_id"), model_id, result)
                self.assertEqual(
                    observation.get("canonical_model_id"),
                    canonical_usage_model_id(model_id or ""),
                    result,
                )
                self.assertIs(observation.get("is_local_route"), False, result)
                self.assertEqual(observation.get("model_calls"), 1, result)
                self.assertEqual(observation.get("measurement"), "provider", result)
                self.assertIs(observation.get("cost_price_known"), True, result)


if __name__ == "__main__":
    unittest.main()
