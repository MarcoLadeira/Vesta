"""Explicitly opt-in live provider smoke tests.

These tests are skipped in CI and locally unless the cloud-confirmation gates,
an explicit provider selector, a matching model, and a safe adapter preflight
all pass. They may consume provider allowance, so the model allowlist is
mandatory.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from opaihub.provider_adapters import ProviderAdapter
from opaihub.provider_catalog import (
    CATALOG_VERSION,
    PROTOCOL_VERSION,
    provider_ids,
    provider_record,
)
from opaihub.provider_protocol import (
    AdapterRequest,
    AdapterSLO,
    ProtocolViolation,
    ProviderReadiness,
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


def _safe_readiness_input(
    adapter: ProviderAdapter,
    probe: Mapping[str, Any],
) -> dict[str, bool]:
    """Project only typed local diagnostic facts into protocol readiness.

    This deliberately never derives authorisation, verification, a completion
    verdict, or cost from provider prose or SDK status.  A missing fact remains
    unknown and prevents the smoke request from being sent.
    """

    profile = adapter.profile
    requirements = profile.contract["requirements"]
    assert isinstance(requirements, Mapping)
    source: dict[str, bool] = {}

    cli_present = probe.get("cliPresent", probe.get("cli_present"))
    if profile.requires_cli and isinstance(cli_present, bool):
        source["installed"] = cli_present

    configured = probe.get("configured")
    if isinstance(configured, bool):
        source["configured"] = configured

    auth_status = str(probe.get("authStatus") or "").strip().lower()
    if auth_status == "connected":
        source.setdefault("configured", True)
        source["authenticated"] = True
        source["healthy"] = True
    elif auth_status in {"not_configured", "invalid", "expired", "disconnected"}:
        source.setdefault("configured", False)
        source["authenticated"] = False
        source["healthy"] = False

    available = probe.get("available")
    if requirements.get("local_service") is True and isinstance(available, bool):
        source["installed"] = available
        source["configured"] = available
        source["healthy"] = available

    connected = probe.get("connected")
    if profile.requires_api_key and isinstance(connected, bool):
        # A forced free-provider diagnostic has made a safe, prompt-free
        # endpoint request. Its typed connection result is health evidence;
        # it does not assert authorisation or any run outcome.
        source["healthy"] = connected

    return source


def _contract_prerequisite(adapter: ProviderAdapter, provider_id: str) -> str | None:
    """Validate the adapter's pinned catalog, protocol, capability, and SLO facts."""

    profile = adapter.profile
    record = provider_record(provider_id)
    if profile.catalog_version != CATALOG_VERSION:
        return f"{provider_id} catalog version is incompatible"
    if profile.protocol_version != PROTOCOL_VERSION:
        return f"{provider_id} protocol version is incompatible"
    if dict(profile.capability_status) != dict(record["capabilities"]):
        return f"{provider_id} capability contract does not match the pinned catalog"
    contract = profile.contract
    if not isinstance(contract, Mapping):
        return f"{provider_id} has no readable adapter contract"
    if dict(contract.get("requirements") or {}) != dict(record["requirements"]):
        return f"{provider_id} requirements contract does not match the pinned catalog"
    cancellation = contract.get("cancellation")
    if not isinstance(cancellation, Mapping) or dict(cancellation) != dict(
        record["cancellation"]
    ):
        return f"{provider_id} cancellation contract does not match the pinned catalog"
    try:
        slo = AdapterSLO(cancel_ack_seconds=cancellation["slo_seconds"])
    except (KeyError, ProtocolViolation, TypeError):
        return f"{provider_id} cancellation SLO is invalid"
    if slo.cancel_ack_seconds != float(record["cancellation"]["slo_seconds"]):
        return f"{provider_id} cancellation SLO is incompatible"
    if dict(contract.get("unsupportedBehavior") or {}) != dict(
        record["unsupported_behavior"]
    ):
        return f"{provider_id} unsupported-capability contract does not fail closed"
    try:
        adapter.validate_request(
            AdapterRequest(
                provider_id,
                f"live-smoke-preflight-{provider_id}",
                ("chat",),
                protocol_version=PROTOCOL_VERSION,
            )
        )
    except (ProtocolViolation, ValueError, TypeError):
        return f"{provider_id} adapter request contract is incompatible"
    return None


def _readiness_prerequisite(
    provider_id: str,
    readiness: ProviderReadiness,
    profile: Any,
) -> str | None:
    if not isinstance(readiness, ProviderReadiness):
        return f"{provider_id} readiness probe returned no protocol readiness"
    if (
        readiness.provider_id != provider_id
        or readiness.protocol_version != PROTOCOL_VERSION
    ):
        return f"{provider_id} readiness protocol version is incompatible"
    if profile.requires_cli and readiness.installed is not True:
        return f"requires {provider_id} CLI installed and detected"
    if (
        profile.requires_api_key or profile.requires_oauth
    ) and readiness.configured is not True:
        return f"requires {provider_id} credentials configured"
    if profile.requires_oauth and readiness.authenticated is not True:
        return f"requires {provider_id} authentication verified by its safe diagnostic"
    if readiness.authorised is False:
        return f"requires {provider_id} authorisation before live smoke"
    if readiness.healthy is not True:
        return f"requires a healthy {provider_id} non-completion diagnostic"
    return None


def _adapter_prerequisite(
    provider_id: str,
    *,
    adapter_factory: Callable[[str], ProviderAdapter] = ProviderAdapter,
) -> str | None:
    """Probe a selected adapter, then require catalog-backed protocol readiness."""

    try:
        adapter = adapter_factory(provider_id)
        probe = (
            adapter.probe(force=True)
            if adapter.profile.requires_api_key
            else adapter.probe()
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        return f"requires a safe {provider_id} adapter diagnostic"
    if not isinstance(probe, Mapping):
        return f"requires a safe {provider_id} adapter diagnostic"
    contract_reason = _contract_prerequisite(adapter, provider_id)
    if contract_reason is not None:
        return contract_reason
    readiness = adapter.readiness(_safe_readiness_input(adapter, probe))
    return _readiness_prerequisite(provider_id, readiness, adapter.profile)


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

    def test_model_allowlist_never_bypasses_the_provider_selector(self) -> None:
        environment = {
            "OPAI_LIVE_PROVIDER_SMOKE": "1",
            "OPAI_CONFIRM_CLOUD_TESTS": "YES",
            "OPAI_LIVE_MODELS": "account:codex:gpt-5.6",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(
                live_provider_prerequisite("codex"),
                "requires codex in OPAI_LIVE_PROVIDER_SMOKE_PROVIDERS",
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
            "OPAI_LIVE_PROVIDER_SMOKE": "1",
            "OPAI_CONFIRM_CLOUD_TESTS": "YES",
            "OPAI_LIVE_PROVIDER_SMOKE_PROVIDERS": "codex",
            "OPAI_LIVE_MODELS": "account:codex:gpt-5.6",
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
            patch("opai.app_state.ask") as ask,
        ):
            reason, result = _run_selected_provider_smoke(
                "codex",
                "account:codex:gpt-5.6",
                adapter_factory=lambda provider_id: adapter,
            )

        self.assertEqual(result, None)
        self.assertIn("codex", reason or "")
        adapter.probe.assert_called_once_with()
        ask.assert_not_called()

    def test_valid_probe_is_converted_and_contract_checked_before_ask(self) -> None:
        adapter = _adapter_with_ready_contract()
        expected_result = {"status": "answered_by_account", "answer": "OK"}
        with (
            patch.dict(os.environ, self._environment(), clear=True),
            patch("opai.app_state.ask", return_value=expected_result) as ask,
        ):
            reason, result = _run_selected_provider_smoke(
                "codex",
                "account:codex:gpt-5.6",
                adapter_factory=lambda provider_id: adapter,
            )

        self.assertIsNone(reason)
        self.assertEqual(result, expected_result)
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
            "OPAI_LIVE_PROVIDER_SMOKE": "1",
            "OPAI_CONFIRM_CLOUD_TESTS": "YES",
            "OPAI_LIVE_PROVIDER_SMOKE_PROVIDERS": "groq",
            "OPAI_LIVE_MODELS": "free:groq:openai/gpt-oss-120b",
        }
        expected_result = {"status": "answered_by_free_api", "answer": "OK"}
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("opai.app_state.ask", return_value=expected_result) as ask,
        ):
            reason, result = _run_selected_provider_smoke(
                "groq",
                "free:groq:openai/gpt-oss-120b",
                adapter_factory=lambda provider_id: adapter,
            )

        self.assertIsNone(reason)
        self.assertEqual(result, expected_result)
        adapter.probe.assert_called_once_with(force=True)
        self.assertTrue(adapter.readiness.call_args.args[0]["healthy"])
        ask.assert_called_once()


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


def _run_selected_provider_smoke(
    provider_id: str,
    model_id: str,
    *,
    adapter_factory: Callable[[str], ProviderAdapter] = ProviderAdapter,
) -> tuple[str | None, dict | None]:
    """Run only a selected provider that passed its safe adapter preflight."""

    prerequisite = live_provider_prerequisite(provider_id)
    if prerequisite is not None:
        return prerequisite, None
    prerequisite = _adapter_prerequisite(
        provider_id,
        adapter_factory=adapter_factory,
    )
    if prerequisite is not None:
        return prerequisite, None
    return None, _run_sandboxed_provider_smoke(provider_id, model_id)


class SelectedLiveProviderSmokeTests(unittest.TestCase):
    def test_each_catalog_provider_requires_its_own_explicit_opt_in(self) -> None:
        for provider_id in provider_ids():
            with self.subTest(provider_id=provider_id):
                prerequisite = live_provider_prerequisite(provider_id)
                if prerequisite is not None:
                    self.skipTest(prerequisite)
                model_id = _model_for_provider(provider_id)
                self.assertIsNotNone(model_id)
                prerequisite, result = _run_selected_provider_smoke(
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
                        "answered_locally",
                    },
                    result,
                )
                self.assertTrue(str(result.get("answer") or "").strip(), result)


if __name__ == "__main__":
    unittest.main()
