"""Vesta brand module — one canonical voice for every surface."""

from __future__ import annotations

import random
import subprocess  # noqa: S404 - test-only: fresh interpreters stand in for launches
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opai import brand
from opai.brand import (
    TAGLINE,
    VESTA_MOTTOS,
    boot_brand,
    cli_header,
    cli_mirror,
    empty_title,
    provider_display,
)

_OLD_CLAIM = "Better. Faster. Cheaper."


class BrandConstantsTests(unittest.TestCase):
    def test_core_identity_present_and_honest(self):
        self.assertIn("visible", TAGLINE.lower())
        self.assertIn("accounted", TAGLINE.lower())
        # The empty-state moment is ownable, not the generic prompt.
        self.assertNotIn("what do you want to build", empty_title().lower())

    def test_boot_brand_shape(self):
        brand = boot_brand()
        for key in ("name", "tagline", "emptyTitle", "composerPlaceholder"):
            self.assertTrue(brand.get(key), key)
        self.assertEqual(brand["name"], "Vesta")

    def test_the_empty_state_carries_no_second_sentence(self):
        """The body and the keyboard hint were removed, not renamed.

        Shipping them in the payload while the front end ignores them is how a
        deleted string comes back: the next person wires up the key they find
        rather than the screen they are looking at.
        """
        brand = boot_brand()

        self.assertNotIn("emptyBody", brand)
        self.assertNotIn("emptyHint", brand)


class SessionMottoTests(unittest.TestCase):
    """The empty-state headline is a Vesta motto, drawn once per launch."""

    def test_the_generic_claim_is_gone(self):
        self.assertNotIn(_OLD_CLAIM, VESTA_MOTTOS)
        self.assertNotEqual(empty_title(), _OLD_CLAIM)
        self.assertNotEqual(boot_brand()["emptyTitle"], _OLD_CLAIM)
        # Not kept around under its old name for someone to wire back up.
        self.assertFalse(hasattr(brand, "EMPTY_TITLE"))

    def test_the_collection_is_fifteen_distinct_lines(self):
        self.assertEqual(len(VESTA_MOTTOS), 15)
        self.assertEqual(len(set(VESTA_MOTTOS)), len(VESTA_MOTTOS))
        for motto in VESTA_MOTTOS:
            self.assertEqual(motto, motto.strip())
            self.assertTrue(motto.endswith("."), motto)

    def test_the_headline_is_an_approved_motto(self):
        self.assertIn(empty_title(), VESTA_MOTTOS)
        self.assertEqual(boot_brand()["emptyTitle"], empty_title())

    def test_the_motto_holds_for_the_whole_run(self):
        # Every boot, reload, and window in one process asks again; the answer
        # must not change underneath the user.
        first = empty_title()
        for _ in range(200):
            self.assertEqual(empty_title(), first)
            self.assertEqual(boot_brand()["emptyTitle"], first)

    def test_the_draw_is_over_the_whole_collection_unweighted(self):
        with mock.patch("opai.brand.random.choice", return_value="x") as choice:
            self.assertEqual(brand._draw_motto(), "x")
        choice.assert_called_once_with(VESTA_MOTTOS)

    def test_every_motto_has_an_equal_chance(self):
        # A seeded generator in place of the module's: deterministic, and it
        # still exercises the real draw. 15,000 draws put each line near 1,000;
        # +/-150 is about five standard deviations.
        with mock.patch("opai.brand.random", random.Random(20260916)):
            counts = Counter(brand._draw_motto() for _ in range(15_000))
        self.assertEqual(set(counts), set(VESTA_MOTTOS))
        for motto, count in counts.items():
            self.assertTrue(850 <= count <= 1150, (motto, count))

    def test_each_launch_draws_its_own_motto(self):
        # A launch is a new process. Eight of them all landing on one line by
        # chance is a 1-in-170-million event, so agreement means no fresh draw.
        repo = Path(__file__).resolve().parents[1]
        script = "from opai.brand import empty_title; print(empty_title())"
        seen = [
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                [sys.executable, "-c", script],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            ).stdout.strip()
            for _ in range(8)
        ]
        for motto in seen:
            self.assertIn(motto, VESTA_MOTTOS)
        self.assertGreater(len(set(seen)), 1, seen)


class ProviderDisplayTests(unittest.TestCase):
    def test_known_providers(self):
        self.assertEqual(provider_display("claude"), "Claude")
        self.assertEqual(provider_display("codex"), "Codex")
        self.assertEqual(provider_display("copilot"), "Copilot")

    def test_unknown_and_empty_are_safe(self):
        self.assertEqual(provider_display("mystery"), "Mystery")
        self.assertEqual(provider_display(None), "Auto")


class CliMirrorTests(unittest.TestCase):
    def test_account_model_uses_shorthand(self):
        cmd = cli_mirror("account:claude:opus", "plan", "fix the tests")
        self.assertEqual(
            cmd, 'vesta ask --model claude:opus --mode plan "fix the tests"'
        )

    def test_default_ask_mode_is_omitted(self):
        cmd = cli_mirror("account:codex:gpt-5.5", "ask", "hello")
        self.assertNotIn(" --mode ", cmd)  # spaces: '--mode' is inside '--model'

    def test_long_task_truncated_and_quotes_sanitized(self):
        cmd = cli_mirror("auto", "ask", 'say "hi" ' + "x" * 100)
        self.assertNotIn('"hi"', cmd)  # inner double quotes become single
        self.assertIn("...", cmd)

    def test_empty_task_uses_placeholder(self):
        self.assertIn("<your task>", cli_mirror("auto", "ask", ""))

    def test_header_is_branded(self):
        self.assertEqual(
            cli_header("plan", "account:claude:opus"),
            "Vesta · plan · account:claude:opus",
        )


class BootPayloadBrandTests(unittest.TestCase):
    def test_boot_payload_carries_brand(self):
        from opai.gui_web import boot_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            payload = boot_payload(root)
        self.assertEqual(payload["brand"]["name"], "Vesta")
        self.assertIn(payload["brand"]["emptyTitle"], VESTA_MOTTOS)

    def test_every_boot_in_one_run_carries_the_same_motto(self):
        from opai.gui_web import boot_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp))
            first = boot_payload(root)["brand"]["emptyTitle"]
            second = boot_payload(root)["brand"]["emptyTitle"]
        self.assertEqual(first, second)
        self.assertEqual(first, empty_title())


if __name__ == "__main__":
    unittest.main()
