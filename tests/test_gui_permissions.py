"""Tests for the tool-permission view derived from the run mode (Qt-free).

These lock the honest mapping: read-only modes never allow edits; safe modes
ask before writing; only full-auto allows edits outright. If a future change
loosened this, the panel would lie about what the AI can do — these stop that.
"""

from __future__ import annotations

import unittest

from opai.gui_permissions import (
    CAPABILITIES,
    is_read_only,
    permission_summary,
    permissions_for,
)


def _state(rows, cap_id):
    return next(r["state"] for r in rows if r["id"] == cap_id)


class PermissionMappingTests(unittest.TestCase):
    def test_read_only_modes_block_all_writes(self):
        for mode in ("ask", "plan"):
            rows = permissions_for(mode)
            self.assertEqual(_state(rows, "read"), "allow")
            self.assertEqual(_state(rows, "search"), "allow")
            for write in ("edit", "create", "run_any", "delete", "network"):
                self.assertEqual(_state(rows, write), "block", (mode, write))
            self.assertTrue(is_read_only(mode))

    def test_safe_auto_allows_safe_commands_asks_before_edits(self):
        rows = permissions_for("safe-auto")
        self.assertEqual(_state(rows, "run_safe"), "allow")
        self.assertEqual(_state(rows, "edit"), "ask")
        self.assertEqual(_state(rows, "create"), "ask")
        self.assertEqual(_state(rows, "delete"), "block")
        self.assertEqual(_state(rows, "network"), "block")
        self.assertFalse(is_read_only("safe-auto"))

    def test_safe_auto_asks_before_arbitrary_commands_instead_of_blocking(self):
        # F17: a hard "run any command = block" left users with no escalation
        # path; Safe Auto now asks in-context (needs_command_approval flow),
        # like it already does for edits. Destructive/network stay blocked.
        rows = permissions_for("safe-auto")
        self.assertEqual(_state(rows, "run_any"), "ask")
        note = next(r["note"] for r in rows if r["id"] == "run_any")
        self.assertEqual(note, "Asks you first")

    def test_approve_edits_asks_before_every_edit(self):
        rows = permissions_for("approve-edits")
        self.assertEqual(_state(rows, "edit"), "ask")
        self.assertEqual(_state(rows, "delete"), "block")

    def test_full_auto_allows_edits_but_still_flags_destruction(self):
        rows = permissions_for("full-auto")
        self.assertEqual(_state(rows, "edit"), "allow")
        self.assertEqual(_state(rows, "run_any"), "allow")
        self.assertEqual(_state(rows, "delete"), "ask")

    def test_unknown_mode_degrades_to_read_only(self):
        rows = permissions_for("mystery")
        self.assertEqual(_state(rows, "edit"), "block")

    def test_safe_auto_note_names_allowed_commands(self):
        rows = permissions_for(
            "safe-auto", safe_auto={"allow_commands": ["git status", "pytest"]}
        )
        note = next(r["note"] for r in rows if r["id"] == "run_safe")
        self.assertIn("git status", note)

    def test_every_row_well_formed(self):
        rows = permissions_for("safe-auto")
        ids = {cap_id for cap_id, _label in CAPABILITIES}
        self.assertEqual({r["id"] for r in rows}, ids)
        for row in rows:
            self.assertIn(row["state"], {"allow", "ask", "block"})
            self.assertTrue(row["label"])
            self.assertTrue(row["note"])

    def test_summary_counts(self):
        summary = permission_summary("safe-auto")
        self.assertIn("allowed", summary)
        self.assertIn("ask", summary)
        self.assertIn("blocked", summary)


if __name__ == "__main__":
    unittest.main()
