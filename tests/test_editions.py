import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub.editions import (
    current_edition,
    edition_summary,
    feature_available,
    load_editions,
    require_feature,
    set_edition,
)


class EditionConfigTests(unittest.TestCase):
    def test_four_editions_with_recommended_prices(self):
        catalog = load_editions(Path.cwd())
        editions = catalog["editions"]
        self.assertEqual(editions["free"]["price"], 0)
        self.assertEqual(editions["pro"]["price"], 12)
        self.assertEqual(editions["team"]["price"], 19)
        self.assertEqual(editions["enterprise"]["price"], "custom")

    def test_default_edition_is_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(current_edition(Path(tmp)), "free")


class FeatureGateTests(unittest.TestCase):
    def test_free_includes_core_local_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for feature in [
                "local_first_routing",
                "usage_ledger",
                "savings_report",
                "client_activation",
                "policy_profiles_solo",
            ]:
                self.assertTrue(feature_available(root, feature), feature)

    def test_free_does_not_include_pro_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(feature_available(root, "savings_export"))
            gate = require_feature(root, "savings_export")
            self.assertFalse(gate["available"])
            self.assertIn("upgrade_hint", gate)

    def test_setting_pro_unlocks_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_edition(root, "pro")
            self.assertEqual(current_edition(root), "pro")
            self.assertTrue(feature_available(root, "savings_export"))

    def test_env_var_overrides_edition(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"OPAI_EDITION": "enterprise"}, clear=False
        ):
            root = Path(tmp)
            self.assertEqual(current_edition(root), "enterprise")
            self.assertTrue(feature_available(root, "policy_profile_enterprise_strict"))

    def test_enterprise_includes_everything_team_and_below(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_edition(root, "enterprise")
            summary = edition_summary(root)
            self.assertEqual(summary["edition"], "enterprise")
            self.assertEqual(summary["locked_features"], [])

    def test_unknown_edition_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = set_edition(Path(tmp), "platinum")
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
