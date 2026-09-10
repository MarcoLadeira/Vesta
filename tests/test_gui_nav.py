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
        # The sidebar is the user's own chat list and nothing else: no nav rows
        # at all. A "Chat" row above your chats is a link to where you already
        # are; New chat is a header action, and any recent chat returns you.
        # Prompt Library and the Insights dashboards live in Settings ->
        # Tools & Insights. Every one of them stays routable through find_nav,
        # so the command palette and deep links are unaffected.
        groups = nav_groups()
        self.assertEqual(groups, [])

        routable = {
            "chat",
            "settings",
            "prompts",
            "home",
            "firewall",
            "context",
            "benchmark",
            "agents",
            "proof",
            "workflows",
        }
        self.assertEqual(sorted(routable), sorted(nav_ids()))
        for item_id in sorted(routable):
            with self.subTest(item=item_id):
                self.assertIsNotNone(find_nav(item_id))

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
