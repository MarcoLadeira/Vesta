"""Tool capability registry and version gating (#569).

The registry exists so Vesta stops discovering upstream breakage the expensive
way — a failed command, a confusing 400, a burnt provider turn, then usually a
retry of the same broken call.
"""

from __future__ import annotations

import unittest

from vestahub.tool_capabilities import (
    REGISTRY_PATH,
    CapabilityState,
    check_capability,
    parse_version,
    preferred_alternative,
)


class VersionParsingTests(unittest.TestCase):
    def test_dotted_versions_compare_numerically_not_lexically(self) -> None:
        # "2.9.0" > "2.80.0" lexically but not numerically; getting this wrong
        # would mark a fixed tool as broken (or vice versa).
        self.assertGreater(parse_version("2.82.1"), parse_version("2.80.0"))
        self.assertGreater(parse_version("2.100.0"), parse_version("2.9.0"))

    def test_prefixed_and_suffixed_versions_still_parse(self) -> None:
        self.assertEqual(parse_version("v2.80.0"), (2, 80, 0))
        self.assertEqual(parse_version("gh version 2.80.0 (2026-01-01)"), (2, 80, 0))

    def test_unreadable_versions_are_none_not_zero(self) -> None:
        # None is meaningful: an unreadable version must not be treated as new
        # enough to clear a broken_below bound.
        for text in ("", "unknown", None):
            with self.subTest(text=text):
                self.assertIsNone(parse_version(text))


class CapabilityGatingTests(unittest.TestCase):
    def test_the_reports_worked_example_is_degraded_with_a_fallback(self) -> None:
        verdict = check_capability(
            "github-cli", "issue_read", detected_version="2.80.0"
        )
        self.assertIs(verdict.state, CapabilityState.DEGRADED)
        self.assertFalse(verdict.usable)
        self.assertEqual(verdict.reason, "projects_classic_removed")
        self.assertEqual(preferred_alternative(verdict), "github_rest_api")

    def test_a_fixed_upstream_version_is_supported_again(self) -> None:
        verdict = check_capability(
            "github-cli", "issue_read", detected_version="2.82.1"
        )
        self.assertIs(verdict.state, CapabilityState.SUPPORTED)
        self.assertTrue(verdict.usable)
        self.assertEqual(verdict.alternatives, ())  # nothing to fall back to

    def test_an_unaffected_capability_on_the_same_tool_stays_supported(self) -> None:
        # Degradation is per-capability: a broken `issue_read` must not
        # quarantine `pr_create` on the same binary.
        verdict = check_capability("github-cli", "pr_create", detected_version="2.80.0")
        self.assertIs(verdict.state, CapabilityState.SUPPORTED)

    def test_an_unregistered_tool_is_unknown_not_supported(self) -> None:
        verdict = check_capability("some-new-tool", "anything")
        self.assertIs(verdict.state, CapabilityState.UNKNOWN)
        self.assertFalse(verdict.usable)

    def test_an_unregistered_capability_on_a_known_tool_is_unknown(self) -> None:
        verdict = check_capability("github-cli", "not_a_real_capability")
        self.assertIs(verdict.state, CapabilityState.UNKNOWN)

    def test_an_unreadable_version_does_not_clear_a_broken_bound(self) -> None:
        # Fail closed: if we cannot tell which version is installed, we cannot
        # claim it is the fixed one.
        verdict = check_capability("github-cli", "issue_read", detected_version="")
        self.assertIs(verdict.state, CapabilityState.DEGRADED)


class AlternativeSelectionTests(unittest.TestCase):
    def test_registry_order_is_preference_order(self) -> None:
        verdict = check_capability(
            "github-cli", "issue_read", detected_version="2.80.0"
        )
        self.assertEqual(
            preferred_alternative(
                verdict, available=["vesta_github_connector", "github_rest_api"]
            ),
            "github_rest_api",
        )

    def test_an_unavailable_first_choice_falls_through(self) -> None:
        verdict = check_capability(
            "github-cli", "issue_read", detected_version="2.80.0"
        )
        self.assertEqual(
            preferred_alternative(verdict, available=["vesta_github_connector"]),
            "vesta_github_connector",
        )

    def test_no_available_alternative_returns_empty_not_a_guess(self) -> None:
        verdict = check_capability(
            "github-cli", "issue_read", detected_version="2.80.0"
        )
        self.assertEqual(
            preferred_alternative(verdict, available=["something_else"]), ""
        )

    def test_a_supported_capability_has_no_alternative(self) -> None:
        verdict = check_capability("github-cli", "pr_create", detected_version="2.90.0")
        self.assertEqual(preferred_alternative(verdict), "")


class RegistryFileTests(unittest.TestCase):
    def test_the_shipped_registry_parses_and_is_wired_to_the_package(self) -> None:
        self.assertTrue(REGISTRY_PATH.is_file(), "capability registry must ship")
        verdict = check_capability("git", "commit", detected_version="2.45.0")
        self.assertIs(verdict.state, CapabilityState.SUPPORTED)

    def test_verdicts_are_json_safe(self) -> None:
        import json

        payload = check_capability(
            "github-cli", "issue_read", detected_version="2.80.0"
        ).to_dict()
        json.dumps(payload)
        self.assertEqual(payload["state"], "degraded")


if __name__ == "__main__":
    unittest.main()
