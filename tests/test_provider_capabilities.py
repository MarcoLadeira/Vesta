"""Provider capability metadata + health-state machine (#168).

One capability record and one health enum per provider, read by every surface.
All hermetic — no network, CLI, or credential access.
"""

from __future__ import annotations

import json
import unittest

from opaihub.provider_adapters import (
    ProviderCapabilities,
    SUPPORTED_PROVIDERS,
    adapter_for,
)
from opaihub.provider_capabilities import (
    HEALTH_TRANSITIONS,
    ProviderHealth,
    all_provider_profiles,
    can_transition,
    canonical_health,
    health_from_connection,
    provider_profile,
)


class ProfileTruthTests(unittest.TestCase):
    def test_every_supported_provider_has_a_profile(self):
        for provider in SUPPORTED_PROVIDERS:
            profile = provider_profile(provider)
            self.assertEqual(profile.provider_id, provider)
        # And the serialized table covers exactly the supported providers.
        ids = {p["provider_id"] for p in all_provider_profiles()}
        self.assertEqual(ids, set(SUPPORTED_PROVIDERS))

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            provider_profile("totally-made-up")

    def test_account_providers_expose_repo_editing_with_runtime_gates(self):
        # Copilot is version-gated at runtime; supported CLIs get bounded tools
        # and legacy CLIs fail closed before launch.
        self.assertTrue(provider_profile("copilot").repo_editing)
        self.assertTrue(provider_profile("claude").repo_editing)
        self.assertTrue(provider_profile("codex").repo_editing)

    def test_account_providers_need_cli_and_oauth_not_a_key(self):
        for provider in ("claude", "codex", "copilot"):
            profile = provider_profile(provider)
            self.assertTrue(profile.requires_cli)
            self.assertTrue(profile.requires_oauth)
            self.assertFalse(profile.requires_api_key)
            self.assertTrue(profile.streaming)
            self.assertTrue(profile.supports_cancellation)

    def test_free_providers_need_a_key_and_do_not_stream(self):
        for provider in ("gemini", "groq", "mistral"):
            profile = provider_profile(provider)
            self.assertTrue(profile.requires_api_key)
            self.assertFalse(profile.requires_oauth)
            self.assertFalse(profile.requires_cli)
            self.assertFalse(profile.streaming)
            # They use the OpenAI-compatible tool loop, incl. git_push/open_pr.
            self.assertTrue(profile.tool_calling)
            # OPai drives edits through its own bounded tools.
            self.assertTrue(profile.repo_editing)

    def test_local_providers_are_answer_only_today(self):
        for provider in ("ollama", "openai-compatible"):
            profile = provider_profile(provider)
            self.assertTrue(profile.chat)
            self.assertFalse(profile.repo_editing)
            self.assertFalse(profile.code_execution)
            self.assertFalse(profile.streaming)  # #154
            self.assertFalse(profile.requires_api_key)
            self.assertFalse(profile.requires_oauth)

    def test_profile_dict_shape_is_serializable(self):
        payload = provider_profile("claude").to_dict()
        for key in ("provider_id", "kind", "capabilities", "requirements"):
            self.assertIn(key, payload)
        self.assertIn("supports_cancellation", payload)
        self.assertIn("repo_editing", payload["capabilities"])
        self.assertIn("requires_oauth", payload["requirements"])
        json.dumps(all_provider_profiles())  # must not raise


class HealthStateMachineTests(unittest.TestCase):
    def test_enum_has_the_defined_states(self):
        self.assertEqual(
            {h.value for h in ProviderHealth},
            {
                "not_installed",
                "not_configured",
                "configured",
                "authenticated",
                "degraded",
                "rate_limited",
                "failed",
                "unknown",
            },
        )

    def test_transitions_reference_only_valid_states(self):
        for state, targets in HEALTH_TRANSITIONS.items():
            self.assertIsInstance(state, ProviderHealth)
            for target in targets:
                self.assertIsInstance(target, ProviderHealth)

    def test_forward_and_recovery_transitions_allowed(self):
        self.assertTrue(
            can_transition(ProviderHealth.CONFIGURED, ProviderHealth.AUTHENTICATED)
        )
        self.assertTrue(
            can_transition(ProviderHealth.AUTHENTICATED, ProviderHealth.RATE_LIMITED)
        )
        # Recovery back to working.
        self.assertTrue(
            can_transition(ProviderHealth.DEGRADED, ProviderHealth.AUTHENTICATED)
        )

    def test_illegal_jumps_are_rejected(self):
        # Cannot authenticate a provider that isn't even installed/configured.
        self.assertFalse(
            can_transition(ProviderHealth.NOT_INSTALLED, ProviderHealth.AUTHENTICATED)
        )
        self.assertFalse(
            can_transition(ProviderHealth.NOT_CONFIGURED, ProviderHealth.AUTHENTICATED)
        )

    def test_unknown_may_resolve_to_anything_and_self_is_allowed(self):
        for target in ProviderHealth:
            self.assertTrue(can_transition(ProviderHealth.UNKNOWN, target))
        self.assertTrue(
            can_transition(ProviderHealth.AUTHENTICATED, ProviderHealth.AUTHENTICATED)
        )


class CanonicalHealthMappingTests(unittest.TestCase):
    def test_missing_account_cli_is_not_installed(self):
        # A missing binary outranks any stale auth string.
        self.assertEqual(
            canonical_health(
                auth_status="connected", cli_installed=False, kind="account"
            ),
            ProviderHealth.NOT_INSTALLED,
        )

    def test_auth_status_maps_to_lifecycle(self):
        cases = {
            "connected": ProviderHealth.AUTHENTICATED,
            "detected": ProviderHealth.CONFIGURED,
            "unknown": ProviderHealth.CONFIGURED,
            "not_configured": ProviderHealth.NOT_CONFIGURED,
            "misconfigured": ProviderHealth.DEGRADED,
            "provider_unavailable": ProviderHealth.DEGRADED,
            "invalid": ProviderHealth.FAILED,
            "expired": ProviderHealth.FAILED,
        }
        for status, expected in cases.items():
            self.assertEqual(
                canonical_health(auth_status=status, cli_installed=True), expected
            )

    def test_rate_limit_code_wins_over_auth_status(self):
        self.assertEqual(
            canonical_health(
                auth_status="connected", cli_installed=True, error_code="RATE_LIMIT"
            ),
            ProviderHealth.RATE_LIMITED,
        )

    def test_unrecognised_status_is_unknown_not_assumed_healthy(self):
        self.assertEqual(
            canonical_health(auth_status="wat", cli_installed=True),
            ProviderHealth.UNKNOWN,
        )

    def test_health_from_connection_reads_entry(self):
        entry = {
            "kind": "account",
            "authStatus": "connected",
            "cliInstalled": True,
            "lastErrorCode": "",
        }
        self.assertEqual(health_from_connection(entry), ProviderHealth.AUTHENTICATED)


class AdapterIntegrationTests(unittest.TestCase):
    def test_adapter_exposes_profile(self):
        self.assertTrue(adapter_for("claude").profile.repo_editing)
        self.assertTrue(adapter_for("copilot").profile.repo_editing)
        self.assertEqual(adapter_for("gemini").profile.kind, "free")

    def test_adapter_health_is_pure_over_status(self):
        claude = adapter_for("claude")
        self.assertEqual(claude.health(None), ProviderHealth.UNKNOWN)
        self.assertEqual(
            claude.health({"authStatus": "connected", "cliInstalled": True}),
            ProviderHealth.AUTHENTICATED,
        )
        self.assertEqual(
            claude.health({"kind": "account", "cliInstalled": False}),
            ProviderHealth.NOT_INSTALLED,
        )

    def test_existing_execution_capabilities_are_unchanged(self):
        # Regression: the narrow execution caps used to build tools must not move.
        self.assertEqual(
            adapter_for("claude").capabilities,
            ProviderCapabilities(True, True, True, True, True),
        )
        self.assertEqual(
            adapter_for("gemini").capabilities,
            ProviderCapabilities(True, True, True, False, False),
        )
        self.assertEqual(
            adapter_for("ollama").capabilities,
            ProviderCapabilities(False, False, False, False, True),
        )


class DoctorWiringTests(unittest.TestCase):
    """Every doctor entry carries the one health + capability truth (#168)."""

    def _accounts(self):
        return [
            {
                "id": "claude",
                "label": "Claude",
                "vendor": "Anthropic",
                "cli": "claude",
                "cli_path": "/usr/bin/claude",
                "cli_present": True,
                "authenticated": True,
                "connected": True,
                "login_hint": "",
            },
            {
                "id": "copilot",
                "label": "Copilot",
                "vendor": "GitHub",
                "cli": "copilot",
                "cli_path": None,
                "cli_present": False,
                "authenticated": False,
                "connected": False,
                "login_hint": "",
            },
        ]

    def test_entries_get_canonical_health_and_capabilities(self):
        from opaihub.accounts import provider_connection_doctor

        entries = provider_connection_doctor(
            accounts=self._accounts(),
            credentials=[
                {"provider": "github", "configured": True, "source": "environment"}
            ],
            include_cli_versions=False,
            include_history=False,
        )
        by_id = {e["providerId"]: e for e in entries}

        valid = {h.value for h in ProviderHealth}
        for entry in entries:
            self.assertIn(entry["healthState"], valid)

        # Copilot has no CLI installed -> not_installed. The profile describes
        # the adapter capability; its installed CLI is gated separately.
        self.assertEqual(by_id["copilot"]["healthState"], "not_installed")
        self.assertTrue(
            by_id["copilot"]["capabilities"]["capabilities"]["repo_editing"]
        )

        # github is the git/PR connector, not an AI provider — no profile.
        self.assertIn("github", by_id)
        self.assertIsNone(by_id["github"]["capabilities"])

    def test_doctor_entries_stay_json_serializable(self):
        from opaihub.accounts import provider_connection_doctor

        entries = provider_connection_doctor(
            accounts=self._accounts(),
            credentials=[],
            include_cli_versions=False,
            include_history=False,
        )
        json.dumps(entries)  # must not raise


if __name__ == "__main__":
    unittest.main()
