"""One-time free-tier consent: persisted per workspace, sanitized, safe.

Users asked (correctly) to be asked at most once per free model — anything more
is popup fatigue that trains people to click through consent. The pref layer
tolerates the list, rejects paid ids, deduplicates, and caps the size.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _helpers import make_repo

from opaihub.gui_preferences import (
    grant_free_consent,
    load_gui_preferences,
    save_gui_preferences,
)


class FreeConsentPrefTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_default_prefs_start_with_empty_consent(self):
        self.assertEqual(load_gui_preferences(self.root)["free_consent"], [])

    def test_grant_persists_across_load(self):
        grant_free_consent(self.root, "free:gemini:gemini-3.1-flash-lite")
        prefs = load_gui_preferences(self.root)
        self.assertEqual(prefs["free_consent"], ["free:gemini:gemini-3.1-flash-lite"])

    def test_grant_is_idempotent(self):
        for _ in range(3):
            grant_free_consent(self.root, "free:groq:openai/gpt-oss-120b")
        self.assertEqual(
            load_gui_preferences(self.root)["free_consent"],
            ["free:groq:openai/gpt-oss-120b"],
        )

    def test_grant_rejects_non_free_ids(self):
        for bad in ("account:claude:sonnet", "auto", "ollama:llama3.2", "", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    grant_free_consent(self.root, bad)  # type: ignore[arg-type]

    def test_sanitize_drops_non_free_prefixed_entries(self):
        save_gui_preferences(
            self.root,
            {
                "free_consent": [
                    "free:gemini:gemini-3.1-flash-lite",
                    "account:claude:sonnet",  # must be dropped
                    "",  # empty must be dropped
                    123,  # non-string must be dropped
                    "auto",  # non-free must be dropped
                ]
            },
        )
        self.assertEqual(
            load_gui_preferences(self.root)["free_consent"],
            ["free:gemini:gemini-3.1-flash-lite"],
        )

    def test_sanitize_deduplicates_and_caps(self):
        entries = [f"free:gemini:model-{i}" for i in range(40)]
        save_gui_preferences(self.root, {"free_consent": entries + entries})
        stored = load_gui_preferences(self.root)["free_consent"]
        self.assertEqual(len(stored), 32)
        self.assertEqual(len(stored), len(set(stored)))

    def test_multiple_providers_can_be_granted(self):
        grant_free_consent(self.root, "free:gemini:gemini-3.1-flash-lite")
        grant_free_consent(self.root, "free:groq:openai/gpt-oss-120b")
        self.assertEqual(
            sorted(load_gui_preferences(self.root)["free_consent"]),
            [
                "free:gemini:gemini-3.1-flash-lite",
                "free:groq:openai/gpt-oss-120b",
            ],
        )


if __name__ == "__main__":
    unittest.main()
