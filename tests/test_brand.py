"""Vesta brand module — one canonical voice for every surface."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from opai.brand import (
    EMPTY_TITLE,
    TAGLINE,
    boot_brand,
    cli_header,
    cli_mirror,
    provider_display,
)


class BrandConstantsTests(unittest.TestCase):
    def test_core_identity_present_and_honest(self):
        self.assertIn("visible", TAGLINE.lower())
        self.assertIn("accounted", TAGLINE.lower())
        # The empty-state moment is ownable, not the generic prompt.
        self.assertNotIn("what do you want to build", EMPTY_TITLE.lower())

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
        self.assertTrue(payload["brand"]["emptyTitle"])


if __name__ == "__main__":
    unittest.main()
