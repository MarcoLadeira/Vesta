"""Tests for the navigation model (Qt-free)."""

from __future__ import annotations

import unittest

from opai.gui_nav import (
    DEFAULT_VIEW,
    dashboard_sections,
    find_nav,
    nav_groups,
    nav_ids,
)
from opai.gui_view_model import SECTIONS


class NavModelTests(unittest.TestCase):
    def test_default_view_is_chat(self):
        self.assertEqual(DEFAULT_VIEW, "chat")
        self.assertIsNotNone(find_nav("chat"))

    def test_ids_are_unique(self):
        ids = nav_ids()
        self.assertEqual(len(ids), len(set(ids)))

    def test_core_views_present(self):
        for needed in ("chat", "prompts", "settings", "home", "firewall"):
            self.assertIn(needed, nav_ids())

    def test_groups_are_simple_by_default(self):
        # ChatGPT-simple top level: an unlabeled workspace pair, then ONE
        # folded Insights group. Settings is hidden from the list (it lives as
        # the fixed footer control) but stays routable via find_nav.
        from opai.gui_nav import group_collapsed

        groups = nav_groups()
        names = [g for g, _items in groups]
        self.assertEqual(names, ["", "Insights"])
        self.assertFalse(group_collapsed(""))
        self.assertTrue(group_collapsed("Insights"))
        # Visible items cover everything except hidden ones (settings).
        flat = [item["id"] for _g, items in groups for item in items]
        self.assertNotIn("settings", flat)
        self.assertEqual(sorted(flat + ["settings"]), sorted(nav_ids()))
        self.assertIsNotNone(find_nav("settings"))

    def test_dashboard_sections_are_real_view_model_sections(self):
        known = {key for key, _label in SECTIONS}
        for section in dashboard_sections():
            self.assertIn(section, known)

    def test_dashboard_items_carry_a_section(self):
        for _group, items in nav_groups():
            for item in items:
                if item.get("kind") == "dashboard":
                    self.assertTrue(item.get("section"))

    def test_find_nav_unknown_is_none(self):
        self.assertIsNone(find_nav("does-not-exist"))


if __name__ == "__main__":
    unittest.main()
