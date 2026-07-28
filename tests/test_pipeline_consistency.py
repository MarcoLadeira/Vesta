"""End-to-end proof that a provider hiccup does not become a failed message.

The unit tests around `provider_blocks` and `auto_router` prove the policy.
These drive the real `handle_gui_message` pipeline so the policy is proven to be
*wired in*: that a transport blip is actually re-attempted, that the second
attempt's answer is what the user sees, and that a genuine failure still names a
model the user can continue with.

Hermetic — no CLI, no model, no network.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import make_repo

from opaihub import auto_router, provider_blocks as blocks
from opaihub.gui_pipeline import handle_gui_message


def _catalog(*models: dict) -> dict:
    return {"models": list(models), "connections": []}


def _free(model_id: str, provider: str, label: str) -> dict:
    return {
        "id": model_id,
        "provider": provider,
        "label": label,
        "kind": "free",
        "available": True,
    }


class TransientRetryPipelineTests(unittest.TestCase):
    """A blip is re-attempted; the user sees the answer, not the blip."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        # No real sleep: the retry delay is policy, not something to wait for.
        self._sleep = mock.patch("opaihub.gui_pipeline.time.sleep")
        self._sleep.start()

    def tearDown(self) -> None:
        self._sleep.stop()
        self._tmp.cleanup()

    def _run(self, results: list[dict], **kwargs):
        calls: list[str] = []

        def fake_ask(project_root, task, model_choice="auto", **kw):
            calls.append(str(model_choice))
            return results[min(len(calls) - 1, len(results) - 1)]

        with mock.patch("opai.app_state.ask", side_effect=fake_ask):
            result = handle_gui_message(
                self.root,
                "explain this repo",
                model_id="free:gemini:3.1-flash-lite",
                mode="ask",
                allow_cloud=True,
                **kwargs,
            )
        return result, calls

    BLIP = {
        "status": "runner_error",
        "error": {
            "code": "PROVIDER_UNAVAILABLE",
            "title": "OPai could not reach this provider.",
            "userMessage": "The provider is unavailable.",
            "provider": "gemini",
            "retryable": True,
        },
    }
    ANSWER = {"status": "answered_by_free_api", "answer": "Here is the tour."}

    def test_a_blip_is_retried_and_the_answer_is_what_the_user_sees(self) -> None:
        result, calls = self._run([self.BLIP, self.ANSWER])
        self.assertEqual(len(calls), 2, "the same request should be re-attempted")
        self.assertEqual(calls[0], calls[1], "the retry stays on the same provider")
        self.assertEqual(result["status"], "answered")
        self.assertIn("Here is the tour.", result["answer"])

    def test_a_second_failure_is_reported_rather_than_retried_forever(self) -> None:
        result, calls = self._run([self.BLIP])
        self.assertEqual(
            len(calls),
            1 + auto_router.MAX_TRANSIENT_RETRIES,
            "a down provider must not spin",
        )
        self.assertNotEqual(result["status"], "answered")

    def test_a_deterministic_refusal_is_never_retried(self) -> None:
        refusal = {
            "status": "runner_error",
            "error": {
                "code": "AUTH_INVALID",
                "title": "This account's sign-in was rejected.",
                "userMessage": "Sign in again.",
                "provider": "gemini",
                "retryable": False,
            },
        }
        _result, calls = self._run([refusal])
        self.assertEqual(len(calls), 1, "retrying a rejected session cannot help")


class DeadEndPipelineTests(unittest.TestCase):
    """A failed turn names a model the user can continue with."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        self._sleep = mock.patch("opaihub.gui_pipeline.time.sleep")
        self._sleep.start()

    def tearDown(self) -> None:
        self._sleep.stop()
        self._tmp.cleanup()

    STALE_CLI = {
        "status": "runner_error",
        "error": {
            "code": "PROVIDER_CLI_OUTDATED",
            "title": "This provider's CLI is out of date.",
            "userMessage": "The installed CLI is too old.",
            "provider": "codex",
            "retryable": False,
        },
    }

    def _run_with_catalog(self, catalog: dict):
        with (
            mock.patch("opai.app_state.ask", return_value=self.STALE_CLI),
            mock.patch("opai.app_state.available_models", return_value=catalog),
        ):
            return handle_gui_message(
                self.root,
                "explain this repo",
                model_id="free:codex:whatever",
                mode="ask",
                allow_cloud=True,
            )

    def test_the_failure_names_a_model_that_still_works(self) -> None:
        result = self._run_with_catalog(
            _catalog(
                _free("free:codex:whatever", "codex", "Codex"),
                _free("free:gemini:3.1-flash-lite", "gemini", "Gemini · 3.1 Flash-Lite"),
            )
        )
        offer = result.get("fallback_offer")
        self.assertIsNotNone(offer, "a usable model existed and was not offered")
        assert offer is not None
        self.assertEqual(offer["id"], "free:gemini:3.1-flash-lite")
        self.assertEqual(offer["label"], "Gemini · 3.1 Flash-Lite")

    def test_no_offer_is_invented_when_nothing_else_can_run(self) -> None:
        result = self._run_with_catalog(
            _catalog(_free("free:codex:whatever", "codex", "Codex"))
        )
        self.assertNotIn("fallback_offer", result)

    def test_the_deterministic_refusal_is_remembered_for_next_time(self) -> None:
        self._run_with_catalog(
            _catalog(
                _free("free:codex:whatever", "codex", "Codex"),
                _free("free:gemini:3.1-flash-lite", "gemini", "Gemini"),
            )
        )
        # The next turn must not spend a call rediscovering the same refusal.
        self.assertTrue(blocks.is_blocked(self.root, "codex"))
        self.assertIn("codex", blocks.block_message(self.root, "codex").lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
