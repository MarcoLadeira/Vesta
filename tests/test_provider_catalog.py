"""Tests for the static, versioned provider capability catalog."""

import json
import unittest
from importlib import metadata
from pathlib import Path

from vestahub import provider_catalog
from vestahub.provider_catalog import render_coverage_matrix


EXPECTED_PROVIDER_IDS = (
    "claude",
    "codex",
    "copilot",
    "kimi",
    "gemini",
    "groq",
    "mistral",
    "deepseek",
    "ollama",
    "openai-compatible",
)
EXPECTED_CAPABILITIES = {
    "cancellation",
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
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPOSITORY_ROOT / "docs" / "PROVIDER_ADAPTER_CONFORMANCE.md"
CHANGELOG_PATH = REPOSITORY_ROOT / "CHANGELOG.md"
MATRIX_COLUMNS = (
    "Provider",
    "Stream",
    "Cancel",
    "Usage",
    "Tools",
    "Structured output",
    "Smoke",
)
MATRIX_STATUSES = {"supported", "partial", "unsupported"}


class ProviderCatalogTests(unittest.TestCase):
    def test_committed_coverage_matrix_is_rendered_from_the_v1_catalog(self):
        self.assertEqual(
            MATRIX_PATH.read_text(encoding="utf-8"),
            render_coverage_matrix(),
        )

    def test_coverage_matrix_has_explicit_statuses_for_every_catalog_provider(self):
        matrix = render_coverage_matrix()
        table_rows = [
            line
            for line in matrix.splitlines()
            if line.startswith("| ") and not line.startswith("| ---")
        ]
        self.assertEqual(
            tuple(cell.strip() for cell in table_rows[0].split("|")[1:-1]),
            MATRIX_COLUMNS,
        )

        providers_in_matrix = []
        for row in table_rows[1:]:
            cells = tuple(cell.strip() for cell in row.split("|")[1:-1])
            self.assertEqual(len(cells), len(MATRIX_COLUMNS))
            providers_in_matrix.append(cells[0])
            self.assertTrue(
                set(cells[1:]) <= MATRIX_STATUSES,
                f"matrix row must use explicit statuses: {row}",
            )
        self.assertEqual(tuple(providers_in_matrix), provider_catalog.provider_ids())
        self.assertIn("Generated from the immutable catalog", matrix)
        self.assertIn("not inferred from provider or model names", matrix)

    def test_protocol_v1_migration_and_degraded_compatibility_are_recorded(self):
        changelog = CHANGELOG_PATH.read_text(encoding="utf-8")

        self.assertIn("Provider adapter protocol v1", changelog)
        self.assertIn("catalog v1", changelog)
        self.assertIn("degraded", changelog.lower())
        self.assertIn("silent fallback", changelog.lower())

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
        requirements = metadata.requires("vesta") or []

        self.assertIn('hypothesis==6.160.0; extra == "test"', requirements)
        self.assertIn('pytest==9.0.3; extra == "test"', requirements)

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
            self.assertEqual(record["capabilities"]["cancellation"], "supported")
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
            self.assertTrue(record["pricing"]["source"])
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

    def test_pricing_source_is_required_and_provenance_is_rejected(self):
        def malformed_catalog(mutate):
            catalog = json.loads(provider_catalog.catalog_bytes())
            mutate(catalog[0]["pricing"])
            return json.dumps(catalog).encode("utf-8")

        cases = {
            "missing_source": lambda pricing: pricing.pop("source", None),
            "unknown_provenance": lambda pricing: pricing.update(
                provenance="deprecated"
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                provider_catalog._parse_catalog(malformed_catalog(mutate))

    def test_missing_cancellation_capability_status_fails_closed(self):
        catalog = json.loads(provider_catalog.catalog_bytes())
        catalog[0]["capabilities"].pop("cancellation", None)

        with self.assertRaises(ValueError):
            provider_catalog._parse_catalog(json.dumps(catalog).encode("utf-8"))

    def test_capability_and_pricing_arrays_fail_closed_with_value_error(self):
        cases = {
            "capabilities_object": lambda record: record.update(capabilities=[]),
            "capability_status": lambda record: record["capabilities"].update(chat=[]),
            "pricing_object": lambda record: record.update(pricing=[]),
            "pricing_measurement": lambda record: record["pricing"].update(
                measurement=[]
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                catalog = json.loads(provider_catalog.catalog_bytes())
                mutate(catalog[0])

                with self.assertRaises(ValueError):
                    provider_catalog._parse_catalog(json.dumps(catalog).encode("utf-8"))

    def test_pricing_measurement_and_price_usd_must_agree(self):
        def catalog_with_pricing(measurement, price_usd):
            catalog = json.loads(provider_catalog.catalog_bytes())
            catalog[0]["pricing"].update(measurement=measurement, price_usd=price_usd)
            return json.dumps(catalog).encode("utf-8")

        invalid_cases = (
            ("actual", None),
            ("actual", -0.01),
            ("actual", True),
            ("actual", "1.00"),
            ("derived", None),
            ("derived", -0.01),
            ("derived", True),
            ("derived", "1.00"),
            ("estimated", None),
            ("estimated", -0.01),
            ("estimated", True),
            ("estimated", "1.00"),
            ("unavailable", 0),
            ("unavailable", -1),
            ("unavailable", True),
            ("unavailable", "1.00"),
        )
        for measurement, price_usd in invalid_cases:
            with self.subTest(measurement=measurement, price_usd=price_usd):
                with self.assertRaises(ValueError):
                    provider_catalog._parse_catalog(
                        catalog_with_pricing(measurement, price_usd)
                    )

        valid_cases = (
            ("unavailable", None),
            ("actual", 0),
            ("derived", 1.25),
            ("estimated", 3),
        )
        for measurement, price_usd in valid_cases:
            with self.subTest(measurement=measurement, price_usd=price_usd):
                record = provider_catalog._parse_catalog(
                    catalog_with_pricing(measurement, price_usd)
                )[0]
                self.assertEqual(record["pricing"]["price_usd"], price_usd)

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
