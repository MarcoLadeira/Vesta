"""Explicitly opt-in live provider smoke tests.

These tests are skipped in CI and locally unless both gates are set. They may
consume provider allowance, so the model allowlist is mandatory.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


LIVE_ENABLED = (
    os.environ.get("OPAI_LIVE_PROVIDER_SMOKE") == "1"
    and os.environ.get("OPAI_CONFIRM_CLOUD_TESTS") == "YES"
)


@unittest.skipUnless(LIVE_ENABLED, "live cloud smoke tests require both explicit gates")
class LiveProviderSmokeTests(unittest.TestCase):
    def test_explicit_model_allowlist_returns_nonempty_answers(self) -> None:
        from opai.app_state import ask

        models = [
            item.strip()
            for item in os.environ.get("OPAI_LIVE_MODELS", "").split(",")
            if item.strip()
        ]
        self.assertTrue(models, "OPAI_LIVE_MODELS must explicitly list models to call")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("Live smoke fixture.\n", encoding="utf-8")
            for model_id in models:
                with self.subTest(model=model_id):
                    result = ask(
                        root,
                        "Reply with exactly: OK",
                        model_choice=model_id,
                        allow_cloud=True,
                        allow_edits=False,
                        mode="ask",
                    )
                    self.assertIn(
                        result.get("status"),
                        {"answered_by_account", "answered_by_free_api"},
                        result,
                    )
                    self.assertTrue(str(result.get("answer") or "").strip(), result)


if __name__ == "__main__":
    unittest.main()
