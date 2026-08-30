"""One effective-mode rule everywhere, and a mode that stays picked (#137).

Full Auto used to be conditional: honoured only while a separate pin flag and
an acknowledgement timestamp were both present, and silently rewritten to Safe
Auto otherwise -- in ``effective_mode``, and again in the preferences
sanitiser on every load and every save. Two layers, both invisible to the UI,
which is why no amount of fixing the composer made a chosen mode survive a
restart. It also meant a modal on every launch for anyone whose chosen mode
was Full Auto.

These tests now pin the opposite contract: a valid mode is returned unchanged
and stored unchanged, whatever it is, and only an unrecognised id falls back.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from opaihub.autonomy import (
    AutonomyDecision,
    effective_mode,
    resolve_startup_mode,
)
from opaihub.gui_preferences import (
    load_gui_preferences,
    pin_full_auto,
    preference_path,
    save_gui_preferences,
    unpin_full_auto,
)

from tests._helpers import make_repo


class EffectiveModeTests(unittest.TestCase):
    def test_full_auto_is_honoured_with_no_pin_of_any_kind(self):
        """The mode a user picked is the mode they get, with no ceremony."""
        decision = effective_mode("full-auto", {})
        self.assertEqual(decision.effective_mode, "full-auto")
        self.assertFalse(decision.downgraded)
        self.assertEqual(decision.requested_mode, "full-auto")

    def test_a_legacy_pinned_preferences_file_still_means_the_same_thing(self):
        prefs = {"full_auto_pinned": True, "full_auto_acknowledged_at": "2026-07-06"}
        decision = effective_mode("full-auto", prefs)
        self.assertEqual(decision.effective_mode, "full-auto")
        self.assertFalse(decision.downgraded)

    def test_every_mode_is_honoured_from_the_stored_default_alone(self):
        """The restart case: nothing is requested, so the default decides."""
        for mode in (
            "ask",
            "plan",
            "approve-edits",
            "safe-auto",
            "auto-edits",
            "full-auto",
        ):
            with self.subTest(mode=mode):
                decision = effective_mode(None, {"default_mode": mode})
                self.assertEqual(decision.effective_mode, mode)
                self.assertFalse(decision.downgraded)

    def test_other_modes_pass_through(self):
        for mode in ("ask", "plan", "safe-auto", "approve-edits", "auto-edits"):
            self.assertEqual(effective_mode(mode, {}).effective_mode, mode)

    def test_unknown_mode_falls_back_to_safe_auto(self):
        decision = effective_mode("yolo", {})
        self.assertEqual(decision.effective_mode, "safe-auto")
        self.assertTrue(decision.downgraded)

    def test_startup_uses_stored_default(self):
        prefs = {"default_mode": "approve-edits"}
        self.assertEqual(resolve_startup_mode(prefs).effective_mode, "approve-edits")

    def test_decision_serializes(self):
        payload = effective_mode("full-auto", {}).to_dict()
        self.assertEqual(payload["effective_mode"], "full-auto")
        self.assertEqual(payload["effective_label"], "Bypass Permissions")
        self.assertIn("reason", payload)
        self.assertIsInstance(
            AutonomyDecision("full-auto", "safe-auto", False, True, "x"),
            AutonomyDecision,
        )


class PreferenceMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_raw(self, data: dict) -> None:
        path = preference_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_fresh_preferences_start_in_safe_auto_unpinned(self):
        prefs = load_gui_preferences(self.root)
        self.assertEqual(prefs["default_mode"], "safe-auto")
        self.assertFalse(prefs["full_auto_pinned"])
        self.assertEqual(prefs["schema_version"], 3)

    def test_a_persisted_mode_survives_a_load_without_any_pin(self):
        """The reported bug, at the layer that actually caused it.

        The sanitiser rewrote an unpinned full-auto default back to Safe Auto
        on load, underneath every surface. So the app forgot a deliberate
        choice on every launch and no UI fix could have made it stick.
        """
        for mode in ("full-auto", "auto-edits", "approve-edits", "plan"):
            with self.subTest(mode=mode):
                self._write_raw({"schema_version": 2, "default_mode": mode})
                self.assertEqual(load_gui_preferences(self.root)["default_mode"], mode)

    def test_an_unrecognised_stored_mode_still_falls_back(self):
        """The one rewrite left, and it is not a policy judgement."""
        self._write_raw({"schema_version": 2, "default_mode": "yolo"})
        self.assertEqual(load_gui_preferences(self.root)["default_mode"], "safe-auto")

    def test_legacy_pin_fields_still_round_trip(self):
        self._write_raw(
            {
                "default_mode": "full-auto",
                "full_auto_pinned": True,
                "full_auto_acknowledged_at": "2026-07-06T00:00:00+00:00",
            }
        )
        prefs = load_gui_preferences(self.root)
        self.assertEqual(prefs["default_mode"], "full-auto")
        self.assertTrue(prefs["full_auto_pinned"])

    def test_pin_and_unpin_roundtrip(self):
        pinned = pin_full_auto(self.root)
        self.assertTrue(pinned["full_auto_pinned"])
        self.assertTrue(pinned["full_auto_acknowledged_at"])
        self.assertEqual(pinned["default_mode"], "full-auto")
        self.assertEqual(resolve_startup_mode(pinned).effective_mode, "full-auto")

        unpinned = unpin_full_auto(self.root)
        self.assertFalse(unpinned["full_auto_pinned"])
        self.assertEqual(unpinned["default_mode"], "safe-auto")
        self.assertEqual(resolve_startup_mode(unpinned).effective_mode, "safe-auto")

    def test_saving_a_mode_sticks_including_full_auto(self):
        """The other half of the same bug: the save was rewritten too."""
        for mode in ("full-auto", "auto-edits", "ask"):
            with self.subTest(mode=mode):
                saved = save_gui_preferences(self.root, {"default_mode": mode})
                self.assertEqual(saved["default_mode"], mode)
                self.assertEqual(load_gui_preferences(self.root)["default_mode"], mode)

    def test_saving_preferences_uses_one_locked_atomic_transaction(self):
        path = preference_path(self.root)
        with (
            mock.patch(
                "opaihub.gui_preferences.interprocess_transaction"
            ) as transaction,
            mock.patch("opaihub.gui_preferences.atomic_write_text") as atomic_write,
        ):
            transaction.return_value.__enter__.return_value = None
            save_gui_preferences(self.root, {"density": "compact"})

        transaction.assert_called_once_with(path)
        atomic_write.assert_called_once()
        self.assertEqual(atomic_write.call_args.args[0], path)


class SurfaceParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pipeline_runs_the_mode_it_was_asked_for(self):
        from opaihub.gui_pipeline import handle_gui_message
        from tests._helpers import FakeAccountRunner

        result = handle_gui_message(
            self.root,
            "help me",
            model_id="account:claude:sonnet",
            mode="full-auto",
            account_runner=FakeAccountRunner(text="done", cost=0.01),
        )
        self.assertEqual(result["autonomy"]["requested_mode"], "full-auto")
        self.assertEqual(result["autonomy"]["effective_mode"], "full-auto")
        self.assertFalse(result["autonomy"]["downgraded"])

    def test_web_and_desktop_boot_report_the_same_effective_mode(self):
        from opai.gui_desktop import run_once
        from opai.gui_web import boot_payload

        # A bare full-auto default, with no pin and no acknowledgement: the
        # exact file shape that used to be rewritten on the way in. Both
        # surfaces must now boot into it, and must agree.
        save_gui_preferences(self.root, {"default_mode": "safe-auto"})
        path = preference_path(self.root)
        path.write_text(json.dumps({"default_mode": "full-auto"}), encoding="utf-8")

        web = boot_payload(self.root)
        desktop = run_once(self.root)
        self.assertEqual(web["prefs"]["mode"], "full-auto")
        self.assertEqual(web["autonomy"]["effective_mode"], "full-auto")
        self.assertEqual(desktop["mode"], "full-auto")
        self.assertEqual(desktop["mode"], web["prefs"]["mode"])

    def test_cli_pin_unpin_parity(self):
        import contextlib
        import io

        from opai.cli import main

        def run(args):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(args)
            return code, json.loads(out.getvalue())

        code, status = run(["autonomy", "status", "--project", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(status["effective_mode"], "safe-auto")

        code, pinned = run(["autonomy", "pin", "--project", str(self.root)])
        self.assertEqual(pinned["effective_mode"], "full-auto")
        self.assertTrue(pinned["full_auto_pinned"])

        code, unpinned = run(["autonomy", "unpin", "--project", str(self.root)])
        self.assertEqual(unpinned["effective_mode"], "safe-auto")


if __name__ == "__main__":
    unittest.main()
