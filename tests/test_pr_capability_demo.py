"""Demonstration of PR creation capability — validates basic OPai PR workflows."""

from __future__ import annotations

import unittest

from opai.brand import TAGLINE, boot_brand


class PrCapabilityDemoTests(unittest.TestCase):
    def test_brand_tagline_consistency(self):
        """Verify that brand constants are properly initialized and available."""
        self.assertIsNotNone(TAGLINE)
        self.assertGreater(len(TAGLINE), 0)
        self.assertIn("visible", TAGLINE.lower())

    def test_brand_boot_returns_dict(self):
        """Verify that boot_brand initializes the brand configuration correctly."""
        brand = boot_brand()
        self.assertIsInstance(brand, dict)
        self.assertIn("name", brand)
        self.assertEqual(brand["name"], "OPai")

    def test_pr_workflow_can_validate_brand_data(self):
        """Validate that PR workflows can access and verify core brand data."""
        brand = boot_brand()
        required_keys = ["name", "tagline", "emptyTitle", "emptyBody"]
        for key in required_keys:
            self.assertIn(key, brand, f"Brand key '{key}' should be present")
            self.assertTrue(brand[key], f"Brand key '{key}' should have a non-empty value")


if __name__ == "__main__":
    unittest.main()
