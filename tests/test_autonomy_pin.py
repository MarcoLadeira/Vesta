"""Pin-aware Full Auto autonomy (#137): one effective-mode rule everywhere."""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from opaihub.autonomy import (
    AutonomyDecision,
    effective_mode,
    is_full_auto_pinned,
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
    def test_unpinned_full_auto_downgrades_to_safe_auto(self):
        decision = effective_mode("full-auto", {"full_auto_pinned": False})
        self.assertEqual(decision.effective_mode, "safe-auto")
        self.assertTrue(decision.downgraded)
        self.assertEqual(decision.requested_mode, "full-auto")

    def test_pinned_full_auto_is_honored(self):
        prefs = {"full_auto_pinned": True, "full_auto_acknowledged_at": "2026-07-06"}
        decision = effective_mode("full-auto", prefs)
        self.assertEqual(decision.effective_mode, "full-auto")
        self.assertFalse(decision.downgraded)

    def test_pin_flag_without_acknowledgement_is_not_pinned(self):
        prefs = {"full_auto_pinned": True, "full_auto_acknowledged_at": ""}
        self.assertFalse(is_full_auto_pinned(prefs))
        self.assertEqual(effective_mode("full-auto", prefs).effective_mode, "safe-auto")

    def test_other_modes_pass_through(self):
        for mode in ("ask", "plan", "safe-auto", "approve-edits"):
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
        self.assertEqual(payload["effective_mode"], "safe-auto")
        self.assertEqual(payload["effective_label"], "Safe Auto")
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

    def test_persisted_full_auto_without_pin_is_reset_on_load(self):
        # A legacy schema-2 file that persisted the unsafe default.
        self._write_raw({"schema_version": 2, "default_mode": "full-auto"})
        prefs = load_gui_preferences(self.root)
        self.assertEqual(prefs["default_mode"], "safe-auto")
        self.assertFalse(prefs["full_auto_pinned"])

    def test_pin_flag_without_timestamp_is_not_trusted(self):
        self._write_raw(
            {
                "default_mode": "full-auto",
                "full_auto_pinned": True,
                "full_auto_acknowledged_at": "",
            }
        )
        prefs = load_gui_preferences(self.root)
        self.assertEqual(prefs["default_mode"], "safe-auto")
        self.assertFalse(prefs["full_auto_pinned"])

    def test_properly_pinned_full_auto_survives_load(self):
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

    def test_saving_full_auto_default_without_pin_does_not_stick(self):
        prefs = save_gui_preferences(self.root, {"default_mode": "full-auto"})
        self.assertEqual(prefs["default_mode"], "safe-auto")

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

    def test_pipeline_downgrades_unpinned_full_auto_request(self):
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
        self.assertEqual(result["autonomy"]["effective_mode"], "safe-auto")
        self.assertTrue(result["autonomy"]["downgraded"])

    def test_web_and_desktop_boot_report_the_same_effective_mode(self):
        from opai.gui_desktop import run_once
        from opai.gui_web import boot_payload

        # Legacy unsafe default persisted.
        save_gui_preferences(self.root, {"default_mode": "safe-auto"})
        path = preference_path(self.root)
        path.write_text(json.dumps({"default_mode": "full-auto"}), encoding="utf-8")

        web = boot_payload(self.root)
        desktop = run_once(self.root)
        self.assertEqual(web["prefs"]["mode"], "safe-auto")
        self.assertFalse(web["prefs"]["fullAutoPinned"])
        self.assertEqual(web["autonomy"]["effective_mode"], "safe-auto")
        self.assertEqual(desktop["mode"], "safe-auto")
        self.assertFalse(desktop["auto_policy"]["full_auto_pinned"])

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
