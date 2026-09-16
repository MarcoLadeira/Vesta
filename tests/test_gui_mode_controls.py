"""Single-source mode semantics (F20) + the classic Full Auto pin flow (F16).

F20: three overlapping concepts — Run mode, Task focus, Agent mode — used to be
recomputed independently per surface and could silently disagree (Full Auto +
Explain focus was read-only in effect). ``gui_modes.describe_controls`` is now
the one definition web, classic, and these tests consume.

F16: the classic composer used to persist a bare ``full-auto`` default that the
sanitizer silently downgraded while the combo kept showing Full Auto.
``gui_modes.plan_mode_selection`` is the Qt-free decision behind the pin flow;
the window only renders it.

Qt-free by design — no display needed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from vesta.gui_controls import live_agent_mode_row
from vesta.gui_modes import describe_controls, plan_mode_selection
from vestahub.autonomy import resolve_startup_mode
from vestahub.gui_preferences import (
    load_gui_preferences,
    preference_path,
    save_gui_preferences,
)


class DescribeControlsTests(unittest.TestCase):
    """F20: one honest reading of Run mode + Task focus + Agent mode."""

    def test_safe_auto_with_explain_focus_is_effectively_read_only(self):
        # The QA root-cause pairing: an edit-capable run mode plus a read-only
        # focus still means the next run cannot edit. Both surfaces must say so.
        controls = describe_controls("safe-auto", "explain")
        self.assertFalse(controls["run_mode_read_only"])
        self.assertTrue(controls["focus_read_only"])
        self.assertTrue(controls["read_only"])
        self.assertEqual(controls["agent_mode_preview"], "explain")

    def test_full_auto_with_build_focus_is_edit_capable(self):
        controls = describe_controls("full-auto", "build")
        self.assertFalse(controls["read_only"])
        self.assertTrue(controls["can_edit"])
        self.assertTrue(controls["can_run_commands"])
        self.assertEqual(controls["edit_state"], "allow")
        self.assertEqual(controls["run_any_state"], "allow")
        self.assertEqual(controls["agent_mode_preview"], "implement")

    def test_full_auto_with_no_specific_focus_previews_implement_not_explain(self):
        # Auto-apply's promise is "acts without asking first" — the preview
        # for an unphrased/ambiguous next message must say so honestly,
        # matching resolve_agent_policy's run_mode_hint fallback.
        controls = describe_controls("full-auto", "general")
        self.assertEqual(controls["agent_mode_preview"], "implement")
        # A read-only focus no longer revokes a pinned Full Auto. This asserted
        # "explain": because the focus is persisted, a stored Explain hint made
        # every Auto-apply turn read-only and the surface still reported
        # read_only=True while telling the user they had full autonomy.
        controls = describe_controls("full-auto", "explain")
        self.assertEqual(controls["agent_mode_preview"], "implement")
        self.assertFalse(controls["read_only"])
        self.assertFalse(controls["focus_read_only"])
        self.assertTrue(controls["can_edit"])

    def test_safe_auto_with_no_specific_focus_still_previews_explain(self):
        # Only full-auto changes the ambiguous-message default; every other
        # run mode keeps the prior, cautious behavior.
        controls = describe_controls("safe-auto", "general")
        self.assertEqual(controls["agent_mode_preview"], "explain")

    def test_read_only_run_modes_cannot_edit_regardless_of_focus(self):
        for mode in ("ask", "plan"):
            controls = describe_controls(mode, "build")
            self.assertTrue(controls["read_only"], mode)
            self.assertFalse(controls["can_edit"], mode)
            self.assertFalse(controls["can_run_commands"], mode)

    def test_run_any_state_mirrors_the_permissions_mapping(self):
        # F17: safe-auto now asks before arbitrary commands — describe_controls
        # must reflect the same _MODE_RULES the panel renders, not a copy.
        from vesta.gui_permissions import permissions_for

        for mode in ("ask", "plan", "safe-auto", "approve-edits", "full-auto"):
            controls = describe_controls(mode, "general")
            panel = {r["id"]: r["state"] for r in permissions_for(mode)}
            self.assertEqual(controls["run_any_state"], panel["run_any"], mode)
            self.assertEqual(controls["edit_state"], panel["edit"], mode)

    def test_agent_mode_preview_matches_the_pipeline_resolver(self):
        # The preview is derived the documented way: resolve_agent_policy with
        # an empty message and the current focus as the hint.
        from vestahub.agent_policy import resolve_agent_policy

        for focus in ("general", "build", "debug", "explain", "review", "plan"):
            controls = describe_controls("safe-auto", focus)
            expected = resolve_agent_policy("", focus_hint=focus).mode.value
            self.assertEqual(controls["agent_mode_preview"], expected, focus)
            self.assertEqual(controls["agent_mode_label"], expected.title(), focus)

    def test_unknown_mode_degrades_to_read_only(self):
        controls = describe_controls("yolo", "build")
        self.assertTrue(controls["read_only"])
        self.assertFalse(controls["can_edit"])

    def test_payload_is_json_serializable(self):
        json.dumps(describe_controls("safe-auto", "explain"))


class PlanModeSelectionTests(unittest.TestCase):
    """Picking a mode persists it. All of them, with no ceremony."""

    def test_every_mode_persists_directly_including_full_auto(self):
        """Full Auto used to return ``confirm_pin`` here.

        That existed because a bare full-auto default would be rewritten to
        Safe Auto on the way back in while the combo still showed Full Auto --
        the F16 lie. The rewrite is gone, so the modal standing in for it is
        gone too; it was firing on every launch for anyone who had chosen the
        mode deliberately.
        """
        for mode in (
            "ask",
            "plan",
            "approve-edits",
            "safe-auto",
            "auto-edits",
            "full-auto",
        ):
            with self.subTest(mode=mode):
                decision = plan_mode_selection(mode, {"default_mode": "safe-auto"})
                self.assertEqual(decision["action"], "persist")
                self.assertFalse(decision["needs_pin_confirmation"])
                self.assertEqual(decision["effective_mode"], mode)

    def test_a_legacy_pinned_preferences_file_still_persists(self):
        prefs = {
            "default_mode": "full-auto",
            "full_auto_pinned": True,
            "full_auto_acknowledged_at": "2026-07-17T00:00:00+00:00",
        }
        decision = plan_mode_selection("full-auto", prefs)
        self.assertEqual(decision["action"], "persist")
        self.assertEqual(decision["effective_mode"], "full-auto")
        self.assertTrue(decision["pinned"])

    def test_a_picked_mode_is_the_mode_the_next_launch_starts_in(self):
        """End to end, on a real preferences file: the reported bug."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            for mode in ("full-auto", "auto-edits", "plan"):
                with self.subTest(mode=mode):
                    decision = plan_mode_selection(mode, load_gui_preferences(root))
                    self.assertEqual(decision["action"], "persist")
                    save_gui_preferences(root, {"default_mode": mode})
                    # A fresh read is what a restart does.
                    prefs = load_gui_preferences(root)
                    self.assertEqual(prefs["default_mode"], mode)
                    self.assertEqual(resolve_startup_mode(prefs).effective_mode, mode)

    def test_the_combo_can_never_display_a_mode_that_is_not_in_force(self):
        """F16's actual invariant, which outlives the downgrade that caused it.

        The desync was possible because the stored mode and the effective mode
        could differ: the combo showed Full Auto while the engine had quietly
        resolved Safe Auto. They cannot differ now -- the combo binds to
        resolve_startup_mode, and resolve_startup_mode returns what is stored.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            path = preference_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            for mode in ("full-auto", "auto-edits", "approve-edits", "safe-auto"):
                with self.subTest(mode=mode):
                    path.write_text(
                        json.dumps({"default_mode": mode}), encoding="utf-8"
                    )
                    prefs = load_gui_preferences(root)
                    self.assertEqual(prefs["default_mode"], mode)
                    self.assertEqual(
                        resolve_startup_mode(prefs).effective_mode,
                        prefs["default_mode"],
                    )


class SurfaceParityTests(unittest.TestCase):
    """F20/F21: web and classic read the same live definition."""

    def test_live_agent_mode_row_is_identical_for_both_surfaces(self):
        # Both inspectors append exactly this helper's row, so the preview can
        # never drift between the web and classic panels.
        row = live_agent_mode_row("safe-auto", "build")
        self.assertEqual(row["label"], "Agent mode (next run)")
        self.assertEqual(
            row["value"], describe_controls("safe-auto", "build")["agent_mode_label"]
        )
        self.assertEqual(row, live_agent_mode_row("safe-auto", "build"))

    def test_boot_controls_equal_the_classic_reading(self):
        # boot_payload's "controls" (web) must be the same dict the classic
        # inspector derives for the same persisted selection.
        from vesta.gui_web import boot_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            web_controls = boot_payload(root)["controls"]
            classic_controls = describe_controls(
                resolve_startup_mode(load_gui_preferences(root)).effective_mode,
                str(load_gui_preferences(root).get("default_task_mode") or "general"),
            )
        self.assertEqual(web_controls, classic_controls)


if __name__ == "__main__":
    unittest.main()
