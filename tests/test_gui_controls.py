"""Qt-free tests for the desktop GUI control layer (command palette, badges,
header strip, state messages). No display required — the logic lives in
``opai.gui_controls`` precisely so it is testable headlessly.
"""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

from opai.gui_controls import (
    COMMANDS,
    SHORTCUTS,
    empty_state,
    filter_commands,
    friendly_error,
    header_status,
    model_badge,
    privacy_badges,
    session_inspector,
    thinking_text,
)


class CommandPaletteTests(unittest.TestCase):
    def test_empty_query_returns_all_in_order(self):
        self.assertEqual(filter_commands(""), list(COMMANDS))

    def test_filters_by_label(self):
        ids = [c["id"] for c in filter_commands("model")]
        self.assertIn("change_model", ids)
        self.assertNotIn("new_chat", ids)

    def test_filters_by_keyword_not_in_label(self):
        # "claude" is a keyword on connect/change_model, not in their labels.
        ids = [c["id"] for c in filter_commands("claude")]
        self.assertTrue(ids)
        self.assertIn("connect", ids)

    def test_multi_token_is_and_match(self):
        # "safe" + "auto" are both keywords only on change_mode (change_model
        # has "auto" but not "safe"), so the AND match isolates it cleanly.
        ids = [c["id"] for c in filter_commands("safe auto")]
        self.assertEqual(ids, ["change_mode"])

    def test_no_match_returns_empty(self):
        self.assertEqual(filter_commands("zzzznotacommand"), [])

    def test_every_command_has_required_fields(self):
        for cmd in COMMANDS:
            self.assertTrue(cmd["id"])
            self.assertTrue(cmd["label"])
            self.assertIn("hint", cmd)

    def test_command_ids_are_unique(self):
        ids = [c["id"] for c in COMMANDS]
        self.assertEqual(len(ids), len(set(ids)))


class ShortcutsTests(unittest.TestCase):
    def test_shortcuts_cover_core_actions(self):
        labels = " ".join(label for _key, label in SHORTCUTS).lower()
        for needed in ("command palette", "new chat", "send", "stop"):
            self.assertIn(needed, labels)

    def test_shortcut_keys_are_nonempty(self):
        for key, label in SHORTCUTS:
            self.assertTrue(key and label)


class ModelBadgeTests(unittest.TestCase):
    def test_auto_badge(self):
        self.assertIn("cheapest", model_badge({"kind": "auto"}))

    def test_local_badge_is_free_and_private(self):
        badge = model_badge({"kind": "local", "id": "ollama-x"})
        self.assertIn("free", badge)
        self.assertIn("private", badge)

    def test_account_family_badges(self):
        self.assertIn(
            "highest", model_badge({"kind": "account", "model": "claude-opus"})
        )
        self.assertIn("fastest", model_badge({"kind": "account", "model": "haiku"}))
        self.assertIn("high", model_badge({"kind": "account", "model": "sonnet"}))

    def test_unknown_account_falls_back_to_cloud_paid(self):
        self.assertEqual(
            model_badge({"kind": "account", "model": "mystery-9"}), "cloud · paid"
        )

    def test_badge_never_crashes_on_sparse_option(self):
        self.assertIsInstance(model_badge({}), str)


class HeaderStatusTests(unittest.TestCase):
    def test_shows_model_mode_and_spend(self):
        line = header_status("Claude · Sonnet", "Ask", 0.04)
        self.assertIn("Claude", line)
        self.assertIn("Ask", line)
        self.assertIn("$0.04 today", line)

    def test_strips_long_model_label(self):
        line = header_status("Auto · OPai routes the cheapest safe model", "Plan", 0)
        self.assertTrue(line.startswith("Auto"))
        self.assertNotIn("routes the cheapest", line)

    def test_optional_saved_appended(self):
        line = header_status("Sonnet", "Ask", 0.10, saved=1.50)
        self.assertIn("$1.50 saved", line)

    def test_bad_numbers_do_not_crash(self):
        line = header_status("Sonnet", "Ask", None)
        self.assertIn("$0.00 today", line)


class StateMessageTests(unittest.TestCase):
    def test_empty_state_has_title_body_hint(self):
        state = empty_state()
        self.assertTrue(state["title"])
        self.assertTrue(state["body"])
        self.assertIn("Ctrl+K", state["hint"])

    def test_thinking_text_uses_short_model_name(self):
        self.assertEqual(thinking_text("Claude · Sonnet"), "Claude is working…")

    def test_thinking_text_defaults_to_opai(self):
        self.assertEqual(thinking_text(None), "OPai is working…")

    def test_friendly_error_known_status_is_actionable(self):
        msg = friendly_error("account_timeout", "Claude · Sonnet")
        self.assertIn("Claude", msg)
        self.assertIn("time limit", msg)
        # never a raw dump
        self.assertNotIn("Traceback", msg)
        self.assertNotIn("subprocess", msg)

    def test_friendly_error_unknown_status_has_safe_fallback(self):
        msg = friendly_error("some_new_status")
        self.assertTrue(msg)
        self.assertNotIn("{model}", msg)

    def test_friendly_error_never_leaks_template_placeholder(self):
        for status in (
            "account_not_connected",
            "account_timeout",
            "account_error",
            "needs_model",
            "needs_confirmation",
            "blocked",
            "blocked_panic",
            "runner_error",
        ):
            with self.subTest(status=status):
                self.assertNotIn("{model}", friendly_error(status, "Sonnet"))


class PrivacyBadgeTests(unittest.TestCase):
    def test_local_model_is_marked_private(self):
        badges = privacy_badges(model_kind="local", connected=True)
        labels = " ".join(b["label"] for b in badges).lower()
        self.assertIn("local only", labels)
        self.assertIn("no telemetry", labels)

    def test_cloud_account_is_marked_paid_with_warn_tone(self):
        badges = privacy_badges(model_kind="account", connected=True)
        first = badges[0]
        self.assertIn("paid", first["label"].lower())
        self.assertEqual(first["tone"], "warn")

    def test_disconnected_state_is_surfaced(self):
        badges = privacy_badges(model_kind="auto", connected=False)
        self.assertTrue(any("no account" in b["label"].lower() for b in badges))

    def test_every_badge_has_label_and_tone(self):
        for kind in ("auto", "local", "account", None):
            for badge in privacy_badges(model_kind=kind, connected=True):
                self.assertTrue(badge["label"])
                self.assertIn(badge["tone"], {"safe", "info", "warn"})


class SessionInspectorTests(unittest.TestCase):
    def _inspector(self, **over):
        base = dict(
            model_label="Claude · Sonnet",
            model_kind="account",
            run_mode_label="Safe Auto",
            task_summary={
                "focus": "Build",
                "format": "Normal",
                "read_only": False,
            },
            inspector={
                "budget": {"spent_today": 0.25, "daily_limit": 2.0, "pct": 12},
                "workspace": {"text": "8 files indexed · main"},
            },
            permission_summary="3 allowed · 2 ask · 3 blocked",
            connected=True,
        )
        base.update(over)
        return session_inspector(**base)

    def test_rows_cover_model_mode_focus_workspace_permissions(self):
        data = self._inspector()
        keys = {row["label"] for row in data["rows"]}
        for needed in ("Model", "Run mode", "Task focus", "Workspace", "Permissions"):
            self.assertIn(needed, keys)

    def test_budget_meter_text_and_pct(self):
        data = self._inspector()
        self.assertIn("$0.25", data["budget"]["text"])
        self.assertEqual(data["budget"]["pct"], 12)

    def test_no_cap_is_handled(self):
        data = self._inspector(
            inspector={"budget": {"spent_today": 0.4, "daily_limit": None, "pct": 0}}
        )
        self.assertIn("no cap", data["budget"]["text"])

    def test_bad_inputs_do_not_crash(self):
        data = session_inspector(
            model_label="",
            model_kind=None,
            run_mode_label="",
            task_summary=None,
            inspector=None,
            permission_summary="",
            connected=False,
        )
        self.assertIn("rows", data)
        self.assertIn("privacy", data)
        self.assertEqual(data["budget"]["pct"], 0)


@unittest.skipUnless(
    importlib.util.find_spec("PySide6") is not None,
    "PySide6 not installed (desktop GUI extra); skipping headless window smoke",
)
class HeadlessWindowSmokeTests(unittest.TestCase):
    """Construct the real window offscreen so the control wiring (sidebar nav,
    header workspace switcher, control panel, command palette + shortcuts) can't
    crash the renderer — for the chat view AND each surfaced page. Skipped where
    the desktop extra isn't installed (CI)."""

    def test_every_view_renders_without_crashing(self):
        from opai.gui_desktop import render_screenshot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname='demo'\n", encoding="utf-8"
            )
            subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
            for view in ("chat", "home", "firewall", "prompts", "settings"):
                with self.subTest(view=view):
                    out = root / f"shot_{view}.png"
                    result = render_screenshot(
                        root, out, width=1200, height=780, view=view
                    )
                    self.assertEqual(result["status"], "written")
                    self.assertGreater(result["bytes"], 0)


if __name__ == "__main__":
    unittest.main()
