"""The picker, CLI payload, and doctor expose one catalog-backed contract."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opai.app_state import available_models
from opaihub.accounts import provider_connection_doctor
from opaihub.provider_capabilities import all_provider_profiles


class ProviderContractSurfaceTests(unittest.TestCase):
    def test_doctor_and_picker_share_the_catalog_contract_for_every_provider(self):
        """Read-only enumeration exposes the same pinned contract everywhere."""

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("opaihub.accounts.list_connected_accounts", return_value=[]),
                mock.patch("opaihub.local_runner.cached_local_models", return_value=[]),
            ):
                picker = available_models(Path(tmp), discover_local=False)
        doctor = provider_connection_doctor(
            accounts=[],
            connections=[],
            credentials=[],
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


if __name__ == "__main__":
    unittest.main()
