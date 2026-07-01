"""Tests for the chat-history recents module (Qt-free, isolated HOME)."""

from __future__ import annotations

import unittest

from _helpers import isolated_home

from opai.gui_recents import MAX_RECENTS, add_recent, load_recents


class RecentsTests(unittest.TestCase):
    def test_add_then_load_roundtrips(self):
        with isolated_home():
            add_recent("summarize my changes")
            self.assertIn("summarize my changes", load_recents())

    def test_most_recent_first_and_deduped(self):
        with isolated_home():
            add_recent("a")
            add_recent("b")
            add_recent("a")  # re-sending moves to front, no duplicate
            recents = load_recents()
        self.assertEqual(recents[0], "a")
        self.assertEqual(recents.count("a"), 1)

    def test_empty_is_ignored(self):
        with isolated_home():
            add_recent("   ")
            self.assertEqual(load_recents(), [])

    def test_newlines_collapsed(self):
        with isolated_home():
            add_recent("line one\nline two")
            self.assertEqual(load_recents()[0], "line one line two")

    def test_capped(self):
        with isolated_home():
            for i in range(MAX_RECENTS + 8):
                add_recent(f"prompt {i}")
            self.assertLessEqual(len(load_recents()), MAX_RECENTS)


if __name__ == "__main__":
    unittest.main()
