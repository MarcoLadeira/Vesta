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

from opai.gui_controls import live_agent_mode_row
from opai.gui_modes import describe_controls, plan_mode_selection
from opaihub.autonomy import resolve_startup_mode
from opaihub.gui_preferences import (
    load_gui_preferences,
    pin_full_auto,
    preference_path,
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

    def test_read_only_run_modes_cannot_edit_regardless_of_focus(self):
        for mode in ("ask", "plan"):
            controls = describe_controls(mode, "build")
            self.assertTrue(controls["read_only"], mode)
            self.assertFalse(controls["can_edit"], mode)
            self.assertFalse(controls["can_run_commands"], mode)

    def test_run_any_state_mirrors_the_permissions_mapping(self):
        # F17: safe-auto now asks before arbitrary commands — describe_controls
        # must reflect the same _MODE_RULES the panel renders, not a copy.
        from opai.gui_permissions import permissions_for

        for mode in ("ask", "plan", "safe-auto", "approve-edits", "full-auto"):
            controls = describe_controls(mode, "general")
            panel = {r["id"]: r["state"] for r in permissions_for(mode)}
            self.assertEqual(controls["run_any_state"], panel["run_any"], mode)
            self.assertEqual(controls["edit_state"], panel["edit"], mode)

    def test_agent_mode_preview_matches_the_pipeline_resolver(self):
        # The preview is derived the documented way: resolve_agent_policy with
        # an empty message and the current focus as the hint.
        from opaihub.agent_policy import resolve_agent_policy

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
    """F16: the classic pin flow's Qt-free decision layer."""

    def test_unpinned_full_auto_requires_the_pin_acknowledgement(self):
        prefs = {"default_mode": "safe-auto", "full_auto_pinned": False}
        decision = plan_mode_selection("full-auto", prefs)
        self.assertEqual(decision["action"], "confirm_pin")
        self.assertTrue(decision["needs_pin_confirmation"])
        # Declining must revert the combo to the EFFECTIVE mode — never leave
        # it displaying an unpinned Full Auto.
        self.assertEqual(decision["effective_mode"], "safe-auto")

    def test_pinned_full_auto_persists_without_another_dialog(self):
        prefs = {
            "default_mode": "full-auto",
            "full_auto_pinned": True,
            "full_auto_acknowledged_at": "2026-07-17T00:00:00+00:00",
        }
        decision = plan_mode_selection("full-auto", prefs)
        self.assertEqual(decision["action"], "persist")
        self.assertEqual(decision["effective_mode"], "full-auto")
        self.assertTrue(decision["pinned"])

    def test_other_modes_persist_directly(self):
        for mode in ("ask", "plan", "safe-auto", "approve-edits"):
            decision = plan_mode_selection(mode, {"full_auto_pinned": False})
            self.assertEqual(decision["action"], "persist", mode)
            self.assertFalse(decision["needs_pin_confirmation"], mode)
            self.assertEqual(decision["effective_mode"], mode, mode)

    def test_pin_accept_flow_persists_and_effective_mode_becomes_full_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            decision = plan_mode_selection("full-auto", load_gui_preferences(root))
            self.assertEqual(decision["action"], "confirm_pin")
            # Accept: pin, then the combo syncs to the new effective mode.
            pin_full_auto(root)
            prefs = load_gui_preferences(root)
            self.assertTrue(prefs["full_auto_pinned"])
            self.assertEqual(prefs["default_mode"], "full-auto")
            self.assertEqual(resolve_startup_mode(prefs).effective_mode, "full-auto")

    def test_pin_decline_flow_leaves_safe_auto_as_the_effective_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            # Decline: nothing is persisted; the combo reverts to the effective
            # mode of the untouched preferences.
            prefs = load_gui_preferences(root)
            decision = plan_mode_selection("full-auto", prefs)
            self.assertEqual(
                decision["effective_mode"],
                resolve_startup_mode(prefs).effective_mode,
            )
            self.assertEqual(resolve_startup_mode(prefs).effective_mode, "safe-auto")

    def test_sanitized_downgrade_never_leaves_a_lying_combo_target(self):
        # A stale persisted full-auto default (legacy/unsafe) is downgraded on
        # load; the combo binds to resolve_startup_mode, so it can only ever
        # display the effective Safe Auto — the F16 desync is impossible.
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            path = preference_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"default_mode": "full-auto"}), encoding="utf-8")
            prefs = load_gui_preferences(root)
            self.assertEqual(prefs["default_mode"], "safe-auto")
            self.assertEqual(resolve_startup_mode(prefs).effective_mode, "safe-auto")


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
        from opai.gui_web import boot_payload

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
