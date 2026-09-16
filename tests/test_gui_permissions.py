"""Tests for the tool-permission view derived from the run mode (Qt-free).

These lock the honest mapping: read-only modes never allow edits; safe modes
ask before writing; only full-auto allows edits outright. If a future change
loosened this, the panel would lie about what the AI can do — these stop that.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from vesta.gui_permissions import (
    CAPABILITIES,
    is_read_only,
    permission_summary,
    permissions_for,
)
from vestahub.gui_pipeline import request_tool_authority


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

    def test_full_auto_grants_every_capability(self):
        # Full Auto maps to the `bypass` autonomy level: the panel must report
        # that nothing stops, because nothing does. Pinning "push: ask" here
        # while command_policy ran the push anyway would put the lie back.
        rows = permissions_for("full-auto")
        for capability in ("edit", "run_any", "delete", "push", "network"):
            with self.subTest(capability=capability):
                self.assertEqual(_state(rows, capability), "allow")

    def test_lower_modes_still_ask_before_pushing(self):
        # The confirmation promise still has to hold where it is still made.
        for mode in ("safe-auto", "approve-edits"):
            with self.subTest(mode=mode):
                self.assertEqual(_state(permissions_for(mode), "push"), "ask")

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


class AdvertisedAuthorityMatchesEnforcedTests(unittest.TestCase):
    """#386: the permission panel must never present more authority than the
    engine actually grants. These cross-check the advertised panel against the
    real enforcement seam (``request_tool_authority``) rather than a copy of the
    rules, so a future change that loosens one without the other fails here.

    The runtime selected modes are ``ask``/``plan``/``safe-auto``/``full-auto``;
    ``approve-edits`` is a preference-level alias the pipeline resolves to one of
    those by task intent, so its enforced authority is governed by the runtime
    mode it collapses to (reconciling that display alias is #379/#386 follow-up).
    """

    RUNTIME_MODES = ("ask", "plan", "safe-auto", "full-auto")
    # A concrete edit-intent request (not a read-only "discovery" one), so the
    # engine's authority reflects the mode, not the request shape.
    EDIT_REQUEST = "fix the bug in app.py"

    def _enforced_allows_edits(self, mode: str) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp), files={"app.py": "value = 1\n"}, commit=True)
            return request_tool_authority(
                self.EDIT_REQUEST, selected_mode=mode, repo_root=root
            ).allow_edits

    def test_panel_editing_claim_matches_engine_for_every_runtime_mode(self):
        for mode in self.RUNTIME_MODES:
            enforced = self._enforced_allows_edits(mode)
            edit_state = _state(permissions_for(mode), "edit")
            advertises_editing = edit_state in {"allow", "ask"}
            self.assertEqual(
                advertises_editing,
                enforced,
                f"{mode}: panel edit={edit_state!r} but engine allow_edits={enforced}",
            )

    def test_read_only_enforcement_forbids_advertising_any_mutation(self):
        # The dangerous direction: whenever the engine enforces read-only, the
        # panel must advertise every mutating capability as blocked.
        for mode in self.RUNTIME_MODES:
            if self._enforced_allows_edits(mode):
                continue
            rows = permissions_for(mode)
            for mutation in ("edit", "create", "delete"):
                self.assertEqual(
                    _state(rows, mutation),
                    "block",
                    f"{mode} is read-only but advertises {mutation} as non-block",
                )
            self.assertTrue(is_read_only(mode))


if __name__ == "__main__":
    unittest.main()
