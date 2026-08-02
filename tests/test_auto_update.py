"""Opt-in automatic updates never touch uncommitted work.

Manual updating already existed behind a button. The toggle makes it
unattended, and the entire design question is what unattended is allowed to do.

``apply_update(force=True)`` stashes local changes, updates, and pops them back.
That is fine behind a button the user just pressed — they are present, and they
can see the result. It is not fine for someone who enabled a toggle weeks ago
and is not watching: the pop can conflict, and resolving a conflict in *their*
uncommitted work, triggered by a background task at launch, is a bad trade for
saving one click.

So the guarantee tested here is narrow and absolute: automatic updates apply
only a clean fast-forward, and a dirty checkout is reported rather than
rewritten.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from opai.auto_update import (
    APPLIED,
    BLOCKED_DIRTY,
    FAILED,
    SKIPPED_DISABLED,
    SKIPPED_UNKNOWN,
    UP_TO_DATE,
    run_auto_update,
)

ROOT = Path("/repo")


def _boom(*_args, **_kwargs):
    raise AssertionError("must not be called")


class AutoUpdateSafetyTests(unittest.TestCase):
    """The property the whole feature rests on."""

    def test_it_never_asks_the_updater_to_force(self) -> None:
        seen: list[object] = []

        def apply(_root, **kwargs):
            seen.append(kwargs.get("force"))
            return {"ok": True}

        run_auto_update(
            ROOT,
            enabled=True,
            check=lambda _r: {"checked": True, "up_to_date": False},
            apply=apply,
        )
        self.assertEqual(seen, [False])

    def test_a_dirty_checkout_is_reported_not_rewritten(self) -> None:
        # apply_update signals this with dirty=True; auto-update must surface it
        # and stop, not retry with force.
        calls: list[dict] = []

        def apply(_root, **kwargs):
            calls.append(kwargs)
            return {"ok": False, "dirty": True, "error": "uncommitted changes"}

        result = run_auto_update(
            ROOT,
            enabled=True,
            check=lambda _r: {"checked": True, "up_to_date": False},
            apply=apply,
        )
        self.assertEqual(result["outcome"], BLOCKED_DIRTY)
        self.assertFalse(result["applied"])
        self.assertEqual(len(calls), 1, "must not retry with force")
        self.assertIn("never touch", result["message"])


class AutoUpdateFlowTests(unittest.TestCase):
    def test_disabled_does_nothing_at_all(self) -> None:
        # Not even a check: an update check reaches the network, and a user who
        # turned this off did not ask for that on every launch.
        result = run_auto_update(ROOT, enabled=False, check=_boom, apply=_boom)
        self.assertEqual(result["outcome"], SKIPPED_DISABLED)

    def test_being_up_to_date_applies_nothing(self) -> None:
        result = run_auto_update(
            ROOT,
            enabled=True,
            check=lambda _r: {"checked": True, "up_to_date": True},
            apply=_boom,
        )
        self.assertEqual(result["outcome"], UP_TO_DATE)

    def test_an_update_is_applied_and_asks_for_a_restart(self) -> None:
        # The running process still has the old code in memory, so claiming the
        # update is live would be false.
        result = run_auto_update(
            ROOT,
            enabled=True,
            check=lambda _r: {"checked": True, "up_to_date": False},
            apply=lambda _r, **_k: {"ok": True, "message": "Updated."},
        )
        self.assertEqual(result["outcome"], APPLIED)
        self.assertTrue(result["applied"])
        self.assertTrue(result["restart_required"])

    def test_an_unavailable_check_is_skipped_with_its_reason(self) -> None:
        result = run_auto_update(
            ROOT,
            enabled=True,
            check=lambda _r: {"checked": False, "reason": "You may be offline."},
            apply=_boom,
        )
        self.assertEqual(result["outcome"], SKIPPED_UNKNOWN)
        self.assertIn("offline", result["message"])

    def test_a_failed_update_is_reported_not_silently_swallowed(self) -> None:
        result = run_auto_update(
            ROOT,
            enabled=True,
            check=lambda _r: {"checked": True, "up_to_date": False},
            apply=lambda _r, **_k: {"ok": False, "error": "history diverged"},
        )
        self.assertEqual(result["outcome"], FAILED)
        self.assertIn("diverged", result["message"])


class AutoUpdateRobustnessTests(unittest.TestCase):
    """This runs at launch, so it must not be able to break starting up."""

    def test_a_raising_check_does_not_escape(self) -> None:
        def raiser(_root):
            raise OSError("git exploded")

        result = run_auto_update(ROOT, enabled=True, check=raiser, apply=_boom)
        self.assertEqual(result["outcome"], SKIPPED_UNKNOWN)
        self.assertIn("git exploded", result["message"])

    def test_a_raising_apply_does_not_escape(self) -> None:
        def raiser(_root, **_kwargs):
            raise OSError("disk full")

        result = run_auto_update(
            ROOT,
            enabled=True,
            check=lambda _r: {"checked": True, "up_to_date": False},
            apply=raiser,
        )
        self.assertEqual(result["outcome"], FAILED)
        self.assertIn("disk full", result["message"])

    def test_a_nonsense_check_result_is_not_trusted(self) -> None:
        for bogus in (None, "yes", 42, []):
            with self.subTest(value=bogus):
                result = run_auto_update(
                    ROOT, enabled=True, check=lambda _r, v=bogus: v, apply=_boom
                )
                self.assertEqual(result["outcome"], SKIPPED_UNKNOWN)

    def test_a_nonsense_apply_result_is_not_read_as_success(self) -> None:
        result = run_auto_update(
            ROOT,
            enabled=True,
            check=lambda _r: {"checked": True, "up_to_date": False},
            apply=lambda _r, **_k: None,
        )
        self.assertEqual(result["outcome"], FAILED)
        self.assertFalse(result["applied"])


class AutoUpdateConsentTests(unittest.TestCase):
    def test_an_unreadable_preference_is_not_consent(self) -> None:
        # Defaulting to "on" when preferences cannot be read would enable a
        # mutating background action nobody agreed to.
        import tempfile
        from unittest import mock

        from opai.auto_update import auto_update_enabled

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "opaihub.gui_preferences.load_gui_preferences",
                side_effect=OSError("unreadable"),
            ):
                self.assertFalse(auto_update_enabled(Path(tmp)))

    def test_the_default_is_off(self) -> None:
        from opaihub.gui_preferences import DEFAULT_PREFERENCES

        self.assertFalse(DEFAULT_PREFERENCES["auto_update"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
