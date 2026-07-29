"""The picker, CLI payload, and doctor expose one catalog-backed contract."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from opai.app_state import available_models
from opai.cli import main
from opai.gui_web import _models, boot_payload
from opaihub.accounts import provider_connection_doctor
from opaihub.provider_capabilities import all_provider_profiles


class ProviderContractSurfaceTests(unittest.TestCase):
    def test_doctor_and_picker_share_the_catalog_contract_for_every_provider(self):
        """Read-only enumeration exposes the same pinned contract everywhere."""

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("opaihub.accounts.list_connected_accounts", return_value=[]),
                mock.patch("opaihub.local_runner.cached_local_models", return_value=[]),
                mock.patch("opaihub.credentials.credential_statuses", return_value=[]),
            ):
                picker = available_models(Path(tmp), discover_local=False)
                doctor = provider_connection_doctor(
                    accounts=[],
                    connections=[],
                    include_cli_versions=False,
                    include_history=False,
                )
        doctor_by_provider = {entry["providerId"]: entry for entry in doctor}

        self.assertEqual(picker["providerCatalogVersion"], "v1")
        self.assertEqual(picker["providerProtocolVersion"], 1)
        for profile in all_provider_profiles():
            provider_id = profile["provider_id"]
            with self.subTest(provider=provider_id):
                self.assertEqual(profile["catalogVersion"], "v1")
                self.assertEqual(profile["protocolVersion"], 1)
                self.assertEqual(
                    picker["providerContracts"][provider_id]["contract"],
                    profile["contract"],
                )
                self.assertEqual(
                    doctor_by_provider[provider_id]["providerContract"]["contract"],
                    profile["contract"],
                )
                self.assertEqual(
                    doctor_by_provider[provider_id]["providerContract"],
                    picker["providerContracts"][provider_id],
                )

    def test_incompatible_observed_protocol_is_actionable_degraded_not_fallback(self):
        """A stale adapter version is reported, never replaced with legacy truth."""

        from opaihub.accounts import provider_contract_payload

        contract = provider_contract_payload(
            "claude", observation={"adapterProtocolVersion": 999}
        )
        state = contract["providerState"]

        self.assertFalse(state["healthy"])
        self.assertEqual(state["degraded_reason"], "protocol_version_incompatible")
        self.assertIn("Update", state["next_action"])
        self.assertEqual(contract["protocolVersion"], 1)

    def test_public_cli_and_web_payloads_preserve_contract_readouts(self):
        """The public model-list, bridge, and initial payloads cannot drop v1."""

        contract_readouts = {
            "providerCatalogVersion": "v1",
            "providerProtocolVersion": 1,
            "providerContracts": {"claude": {"contract": {"example": True}}},
        }
        source = {
            "models": [],
            "accounts": [],
            "connections": [],
            "hint": None,
            **contract_readouts,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname = 'surface-test'\n", encoding="utf-8"
            )
            with mock.patch("opai.app_state.available_models", return_value=source):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = main(["models", "list", "--project", str(root)])
                cli_payload = json.loads(output.getvalue())
                bridge_payload = _models(root, discover_local=False)
                initial_payload = boot_payload(root)

        self.assertEqual(code, 0)
        for surface, payload in (
            ("cli", cli_payload),
            ("web_bridge", bridge_payload),
            ("web_initial", initial_payload),
        ):
            with self.subTest(surface=surface):
                self.assertEqual(
                    payload["providerCatalogVersion"],
                    contract_readouts["providerCatalogVersion"],
                )
                self.assertEqual(
                    payload["providerProtocolVersion"],
                    contract_readouts["providerProtocolVersion"],
                )
                self.assertEqual(
                    payload["providerContracts"],
                    contract_readouts["providerContracts"],
                )

    def test_configured_api_contract_is_identical_across_public_surfaces(self):
        """A safe credential fact is shared without rendering its secret."""

        secret = "must-not-render"
        credential = {
            "provider": "groq",
            "configured": True,
            "source": "environment",
            "envKey": "GROQ_API_KEY",
            "value": secret,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname = 'surface-test'\n", encoding="utf-8"
            )
            with (
                mock.patch("opaihub.accounts.list_connected_accounts", return_value=[]),
                mock.patch(
                    "opaihub.credentials.credential_statuses",
                    return_value=[credential],
                ),
            ):
                doctor = provider_connection_doctor(
                    accounts=[],
                    connections=[],
                    credentials=[credential],
                    include_cli_versions=False,
                    include_history=False,
                )
                picker = available_models(root, discover_local=False)
                output = io.StringIO()
                with redirect_stdout(output):
                    code = main(["models", "list", "--project", str(root)])
                cli_payload = json.loads(output.getvalue())
                bridge_payload = _models(root, discover_local=False)
                initial_payload = boot_payload(root)

        doctor_contract = next(
            entry["providerContract"]
            for entry in doctor
            if entry["providerId"] == "groq"
        )
        self.assertEqual(code, 0)
        self.assertTrue(doctor_contract["providerState"]["configured"])
        for surface, contract in (
            ("picker", picker["providerContracts"]["groq"]),
            ("cli", cli_payload["providerContracts"]["groq"]),
            ("web_bridge", bridge_payload["providerContracts"]["groq"]),
            ("web_initial", initial_payload["providerContracts"]["groq"]),
        ):
            with self.subTest(surface=surface):
                self.assertEqual(contract, doctor_contract)
        self.assertNotIn(secret, json.dumps(doctor))
        self.assertNotIn(secret, json.dumps(cli_payload))
        self.assertNotIn(secret, json.dumps(bridge_payload))
        self.assertNotIn(secret, json.dumps(initial_payload))


if __name__ == "__main__":
    unittest.main()
