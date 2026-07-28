"""Tests for the static, versioned provider capability catalog."""

import json
import unittest
from importlib import metadata
from pathlib import Path

from opaihub import provider_catalog


EXPECTED_PROVIDER_IDS = (
    "claude",
    "codex",
    "copilot",
    "kimi",
    "gemini",
    "groq",
    "mistral",
    "ollama",
    "openai-compatible",
)
EXPECTED_CAPABILITIES = {
    "chat",
    "code_execution",
    "repo_read",
    "repo_editing",
    "run_tests",
    "streaming",
    "tool_calling",
    "structured_output",
}
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "provider_catalog" / "v1.json"


class ProviderCatalogTests(unittest.TestCase):
    def test_catalog_has_the_pinned_provider_inventory(self):
        self.assertEqual(provider_catalog.CATALOG_VERSION, "v1")
        self.assertEqual(provider_catalog.PROTOCOL_VERSION, 1)
        self.assertEqual(provider_catalog.provider_ids(), EXPECTED_PROVIDER_IDS)

    def test_protocol_version_must_be_an_exact_non_bool_integer(self):
        for value in (b"true", b"1.0"):
            with self.subTest(value=value):
                raw_catalog = provider_catalog.catalog_bytes().replace(
                    b'"protocol_version": 1', b'"protocol_version": ' + value, 1
                )

                with self.assertRaises(ValueError):
                    provider_catalog._parse_catalog(raw_catalog)

        valid_catalog = provider_catalog.catalog_bytes().replace(
            b'"protocol_version": 1', b'"protocol_version": 1', 1
        )
        self.assertEqual(
            provider_catalog._parse_catalog(valid_catalog)[0]["protocol_version"], 1
        )

    def test_test_extra_includes_pytest_and_pinned_hypothesis(self):
        requirements = metadata.requires("opai") or []

        self.assertIn('hypothesis==6.160.0; extra == "test"', requirements)
        self.assertIn('pytest; extra == "test"', requirements)

    def test_fixture_is_the_exact_replayable_catalog_bytes(self):
        self.assertEqual(
            provider_catalog.catalog_path().read_bytes(),
            provider_catalog.catalog_bytes(),
        )
        self.assertEqual(FIXTURE_PATH.read_bytes(), provider_catalog.catalog_bytes())

    def test_catalog_records_are_complete_immutable_and_never_route_on_price(self):
        records = provider_catalog.all_catalog_records()

        self.assertEqual(
            tuple(record["provider_id"] for record in records), EXPECTED_PROVIDER_IDS
        )
        self.assertNotIn("mock", provider_catalog.provider_ids())
        for record in records:
            self.assertEqual(record["catalog_version"], "v1")
            self.assertEqual(record["protocol_version"], 1)
            self.assertEqual(set(record["capabilities"]), EXPECTED_CAPABILITIES)
            self.assertTrue(
                set(record["capabilities"].values())
                <= {"supported", "partial", "unsupported"}
            )
            self.assertIn("api_key", record["requirements"])
            self.assertIn("mode", record["cancellation"])
            self.assertGreater(record["cancellation"]["slo_seconds"], 0)
            self.assertEqual(record["unsupported_behavior"]["mode"], "fail_closed")
            self.assertIn(
                record["pricing"]["measurement"],
                {
                    "actual",
                    "derived",
                    "estimated",
                    "unavailable",
                },
            )
            self.assertTrue(record["pricing"]["provenance"])
            self.assertIn("observed_at", record["pricing"])
            self.assertIn("expiry", record["pricing"])
            self.assertFalse(record["pricing"]["routing_eligible"])
            self.assertIsNone(record["pricing"]["price_usd"])

        with self.assertRaises(TypeError):
            records[0]["provider_id"] = "mock"
        with self.assertRaises(TypeError):
            records[0]["capabilities"]["chat"] = "unsupported"

    def test_catalog_pricing_expiry_is_a_timestamp_after_observation(self):
        for record in provider_catalog.all_catalog_records():
            pricing = record["pricing"]

            self.assertIsInstance(pricing["expiry"], str)
            self.assertIn("T", pricing["observed_at"])
            self.assertIn("T", pricing["expiry"])
            self.assertGreater(pricing["expiry"], pricing["observed_at"])

    def test_missing_null_and_invalid_pricing_expiry_fail_closed(self):
        def malformed_catalog(mutate):
            catalog = json.loads(provider_catalog.catalog_bytes())
            mutate(catalog[0]["pricing"])
            return json.dumps(catalog).encode("utf-8")

        cases = {
            "missing": lambda pricing: pricing.pop("expiry"),
            "null": lambda pricing: pricing.update(expiry=None),
            "invalid": lambda pricing: pricing.update(expiry="not-a-timestamp"),
            "not_after_observed_at": lambda pricing: pricing.update(
                expiry=pricing["observed_at"]
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                provider_catalog._parse_catalog(malformed_catalog(mutate))

    def test_unknown_provider_fails_closed(self):
        with self.assertRaises(ValueError):
            provider_catalog.provider_record("mock")

    def test_malformed_duplicate_and_incomplete_catalogs_fail_closed(self):
        with self.assertRaises(ValueError):
            provider_catalog._parse_catalog(b"not-json")

        duplicate = json.loads(provider_catalog.catalog_bytes())
        duplicate[1]["provider_id"] = duplicate[0]["provider_id"]
        with self.assertRaises(ValueError):
            provider_catalog._parse_catalog(json.dumps(duplicate).encode("utf-8"))

        incomplete = json.loads(provider_catalog.catalog_bytes())
        del incomplete[0]["pricing"]["expiry"]
        with self.assertRaises(ValueError):
            provider_catalog._parse_catalog(json.dumps(incomplete).encode("utf-8"))

    def test_duplicate_json_object_keys_fail_closed(self):
        raw_catalog = provider_catalog.catalog_bytes().replace(
            b'"provider_id": "claude",',
            b'"provider_id": "claude",\n    "provider_id": "claude",',
            1,
        )

        with self.assertRaises(ValueError):
            provider_catalog._parse_catalog(raw_catalog)

    def test_non_finite_cancellation_slos_fail_closed(self):
        for value in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(value=value):
                raw_catalog = provider_catalog.catalog_bytes().replace(
                    b'"slo_seconds": 5', b'"slo_seconds": ' + value, 1
                )

                with self.assertRaises(ValueError):
                    provider_catalog._parse_catalog(raw_catalog)

    def test_non_standard_pricing_constants_fail_closed(self):
        for value in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(value=value):
                raw_catalog = provider_catalog.catalog_bytes().replace(
                    b'"price_usd": null', b'"price_usd": ' + value, 1
                )

                with self.assertRaises(ValueError):
                    provider_catalog._parse_catalog(raw_catalog)

    def test_overflowed_pricing_numbers_fail_closed(self):
        for value in (b"1e309", b"-1e309"):
            with self.subTest(value=value):
                raw_catalog = provider_catalog.catalog_bytes().replace(
                    b'"price_usd": null', b'"price_usd": ' + value, 1
                )

                with self.assertRaises(ValueError):
                    provider_catalog._parse_catalog(raw_catalog)


if __name__ == "__main__":
    unittest.main()
