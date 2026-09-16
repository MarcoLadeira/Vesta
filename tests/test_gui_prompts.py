"""Tests for the prompt library (Qt-free)."""

from __future__ import annotations

import unittest

from vesta.gui_modes import task_modes
from vesta.gui_prompts import (
    CATEGORIES,
    PROMPTS,
    categories_present,
    filter_prompts,
    find_prompt,
)


class PromptLibraryTests(unittest.TestCase):
    def test_categories_present_subset_with_data(self):
        present = categories_present()
        self.assertTrue(present)
        for cat in present:
            self.assertIn(cat, CATEGORIES)
            self.assertTrue(filter_prompts(category=cat))

    def test_empty_query_returns_all(self):
        self.assertEqual(len(filter_prompts("")), len(PROMPTS))

    def test_query_is_and_match_across_fields(self):
        results = filter_prompts("test")
        self.assertTrue(results)
        for prompt in results:
            hay = " ".join(
                [prompt["title"], prompt["desc"], prompt["category"], *prompt["tags"]]
            ).lower()
            self.assertIn("test", hay)

    def test_category_filter(self):
        results = filter_prompts(category="Debugging")
        self.assertTrue(results)
        self.assertTrue(all(p["category"] == "Debugging" for p in results))

    def test_no_match_is_empty(self):
        self.assertEqual(filter_prompts("zzznotaprompt"), [])

    def test_find_prompt(self):
        self.assertIsNone(find_prompt("nope"))
        self.assertEqual(find_prompt("find_bug")["category"], "Debugging")

    def test_every_prompt_well_formed_and_mode_is_real(self):
        mode_ids = {m["id"] for m in task_modes()}
        ids = [p["id"] for p in PROMPTS]
        self.assertEqual(len(ids), len(set(ids)))  # unique
        for prompt in PROMPTS:
            for field in ("id", "title", "category", "desc", "template", "mode"):
                self.assertTrue(prompt.get(field), (prompt["id"], field))
            self.assertIn(prompt["mode"], mode_ids)
            self.assertIn(prompt["category"], CATEGORIES)


if __name__ == "__main__":
    unittest.main()
