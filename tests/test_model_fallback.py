"""MODEL_UNAVAILABLE auto-fallback recovery (#318).

When a provider CLI rejects the selected model, OPai retries once with the
provider's safe default and answers honestly — instead of failing the turn. All
hermetic: a model-aware fake runner, no paid CLI.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opai import app_state


class _ModelAwareRunner:
    """A fake account runner whose success depends on which model it was given."""

    def __init__(self, model, *, bad, account="claude"):
        self.model = model or ""
        self.name = account
        self._bad = bad

    def available(self):
        return True

    def complete(self, task, *, project_root=None, allow_edits=False, mode=None):
        if self.model in self._bad:
            return {"error": f"unknown model: {self.model}", "returncode": 1}
        return {"text": f"ran with {self.model}", "cost": 0.02}


def _factory(*bad, account="claude"):
    def runner_for_account(account_id, *, model=None, home=None):
        return _ModelAwareRunner(model, bad=set(bad), account=account)

    return runner_for_account


class ModelFallbackTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _ask(self, model):
        return app_state._ask_account(self.root, "do the thing", "claude", model=model)

    def test_rejected_model_recovers_on_the_default(self):
        with mock.patch(
            "opaihub.accounts.runner_for_account",
            side_effect=_factory("claude-sonnet-5"),
        ):
            result = self._ask("claude-sonnet-5")
        self.assertEqual(result["status"], "answered_by_account")
        self.assertEqual(result["model_fallback"]["from"], "claude-sonnet-5")
        self.assertEqual(result["model_fallback"]["to"], "sonnet")
        self.assertEqual(result["model_fallback"]["reason"], "MODEL_UNAVAILABLE")
        self.assertIn("ran with sonnet", result["answer"])
        self.assertIn("unavailable", result["answer"].lower())
        # Accounting reflects the model that actually ran.
        self.assertEqual(result["model"], "sonnet")

    def test_fallback_that_also_fails_surfaces_the_original_error(self):
        # Both the selected model and the default are rejected -> honest failure,
        # no masking, no fallback annotation.
        with mock.patch(
            "opaihub.accounts.runner_for_account",
            side_effect=_factory("claude-sonnet-5", "sonnet"),
        ):
            result = self._ask("claude-sonnet-5")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "MODEL_UNAVAILABLE")
        self.assertNotIn("model_fallback", result)

    def test_only_model_unavailable_triggers_fallback(self):
        # An auth error is not a model problem — it must not silently swap models.
        class _AuthFailRunner(_ModelAwareRunner):
            def complete(self, task, **kwargs):
                return {"error": "401 unauthorized: invalid api key", "returncode": 1}

        with mock.patch(
            "opaihub.accounts.runner_for_account",
            side_effect=lambda a, *, model=None, home=None: _AuthFailRunner(
                model, bad=set()
            ),
        ):
            result = self._ask("claude-sonnet-5")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("model_fallback", result)
        self.assertNotEqual(result["error"]["code"], "MODEL_UNAVAILABLE")

    def test_a_working_model_never_falls_back(self):
        with mock.patch("opaihub.accounts.runner_for_account", side_effect=_factory()):
            result = self._ask("opus")
        self.assertEqual(result["status"], "answered_by_account")
        self.assertNotIn("model_fallback", result)
        self.assertIn("ran with opus", result["answer"])


if __name__ == "__main__":
    unittest.main()
