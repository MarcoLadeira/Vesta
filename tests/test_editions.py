import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vestahub.editions import (
    current_edition,
    edition_summary,
    feature_available,
    load_editions,
    require_feature,
    set_edition,
)
from vestahub.loader import load_registry
from vestahub.state import load_state


REPO = Path(__file__).resolve().parents[1]
CATALOGS = [
    REPO / "configs" / "editions.yaml",
    REPO / "hub" / "editions.yaml",
    REPO / "vestahub" / "data" / "hub" / "editions.yaml",
]


class FreePublicAlphaCatalogTests(unittest.TestCase):
    def test_every_checked_in_catalog_describes_one_free_alpha(self):
        for path in CATALOGS:
            text = path.read_text(encoding="utf-8")
            catalog = load_registry(path)

            self.assertIn("launch: free-public-alpha", text, path)
            self.assertNotIn("min_edition:", text, path)
            self.assertNotIn("\n  pro:", text, path)
            self.assertNotIn("\n  team:", text, path)
            self.assertNotIn("\n  enterprise:", text, path)
            self.assertEqual(catalog["editions"], {"free": catalog["editions"]["free"]})
            self.assertEqual(catalog["editions"]["free"]["price"], 0)

    def test_default_alpha_catalog_marks_export_as_implemented(self):
        catalog = load_editions(REPO)
        export = next(
            feature
            for feature in catalog["features"]
            if feature["id"] == "savings_export"
        )
        self.assertEqual(export["availability"], "implemented")

    def test_active_product_docs_never_offer_paid_tier_selection(self):
        for relative in [
            "README.md",
            "hub/docs/PRICING_AND_EDITIONS.md",
            "vestahub/data/hub/docs/PRICING_AND_EDITIONS.md",
            "hub/docs/GOVERNANCE.md",
            "vestahub/data/hub/docs/GOVERNANCE.md",
            "hub/docs/ROADMAP.md",
            "vestahub/data/hub/docs/ROADMAP.md",
            "hub/docs/MONEY_SAVING_ROADMAP.md",
            "vestahub/data/hub/docs/MONEY_SAVING_ROADMAP.md",
        ]:
            text = (REPO / relative).read_text(encoding="utf-8")
            self.assertIn("Free Public Alpha", text, relative)
            self.assertNotIn("vesta edition set", text, relative)
            self.assertNotIn("upgrade hint", text.lower(), relative)
            self.assertNotIn("$19 / user / month", text, relative)
            self.assertNotIn("Private Paid Distribution", text, relative)
            self.assertNotIn("Open-core editions", text, relative)

    def test_runtime_copy_uses_free_alpha_language_for_proof_bundles(self):
        for relative in [
            "vesta/cli.py",
            "vesta/gui_view_model.py",
            "vestahub/dashboard_html.py",
            "vestahub/proof.py",
        ]:
            text = (REPO / relative).read_text(encoding="utf-8")
            self.assertNotIn("buyers and team pilots", text, relative)
            self.assertNotIn("team pilot trust", text, relative)


class FreePublicAlphaAvailabilityTests(unittest.TestCase):
    def test_implemented_features_are_free_without_upgrade_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for feature in [
                "local_first_routing",
                "usage_ledger",
                "savings_report",
                "savings_export",
                "client_activation",
                "guarded_workflows_core",
                "audit_logs",
            ]:
                gate = require_feature(root, feature)
                self.assertTrue(gate["available"], feature)
                self.assertEqual(gate["availability"], "free_alpha", feature)
                self.assertNotIn("upgrade_hint", gate, feature)

    def test_legacy_environment_value_cannot_change_the_free_launch_state(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"VESTA_EDITION": "enterprise"}, clear=False),
        ):
            root = Path(tmp)
            self.assertEqual(current_edition(root), "free")
            self.assertTrue(feature_available(root, "savings_export"))

    def test_legacy_tier_selection_is_a_non_persisting_free_alpha_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = set_edition(root, "pro")

            self.assertEqual(result["status"], "free_alpha")
            self.assertEqual(result["edition"], "free")
            self.assertEqual(result["requested_edition"], "pro")
            self.assertEqual(current_edition(root), "free")
            self.assertNotIn("edition", load_state(root))

    def test_planned_capability_is_not_an_upgrade_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = require_feature(Path(tmp), "sso_rbac")

        self.assertFalse(gate["available"])
        self.assertEqual(gate["status"], "not_implemented")
        self.assertEqual(gate["availability"], "planned")
        self.assertNotIn("upgrade_hint", gate)

    def test_availability_summary_has_one_free_catalog_and_no_locked_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = edition_summary(Path(tmp))

        self.assertEqual(summary["edition"], "free")
        self.assertEqual(summary["launch"], "free-public-alpha")
        self.assertEqual(len(summary["catalog"]), 1)
        self.assertEqual(summary["catalog"][0]["price"], 0)
        self.assertNotIn("locked_features", summary)
        self.assertTrue(summary["planned_features"])

    def test_malformed_free_catalog_entry_falls_back_to_truthful_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hub = root / "hub"
            hub.mkdir()
            (hub / "editions.yaml").write_text(
                "editions:\n  free: malformed\nfeatures: []\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"VESTA_HUB_ROOT": str(hub)}, clear=False):
                summary = edition_summary(root)

        self.assertEqual(summary["catalog"][0]["label"], "Free Public Alpha")
        self.assertEqual(summary["catalog"][0]["price"], 0)


if __name__ == "__main__":
    unittest.main()
