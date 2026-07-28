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
        # No real wait: the retry delay is policy, not something to sit through.
        # Zero the delay constant rather than patching `time.sleep` — the
        # pipeline imports the stdlib module, so patching its attribute would
        # replace `time.sleep` for the whole process, and any other test's
        # background thread calling it would accumulate mock call records until
        # the run dies of MemoryError. Constant-patching is exact and local.
        self._delay = mock.patch.object(
            auto_router, "TRANSIENT_RETRY_DELAY_SECONDS", 0.0
        )
        self._delay.start()

    def tearDown(self) -> None:
        self._delay.stop()
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
        self._delay = mock.patch.object(
            auto_router, "TRANSIENT_RETRY_DELAY_SECONDS", 0.0
        )
        self._delay.start()

    def tearDown(self) -> None:
        self._delay.stop()
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


class GovernedLanePipelineTests(unittest.TestCase):
    """The governed lane's rule, proven through the real pipeline.

    Automatic recovery is the whole point of the rest of this work — which is
    exactly why the one place it must not apply needs a test. Re-running a
    publish or a delete somewhere else is a second attempt at something the
    user approved once, for one route.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        self._delay = mock.patch.object(
            auto_router, "TRANSIENT_RETRY_DELAY_SECONDS", 0.0
        )
        self._delay.start()

    def tearDown(self) -> None:
        self._delay.stop()
        self._tmp.cleanup()

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

    def _run(self, message: str):
        calls: list[str] = []

        def fake_ask(project_root, task, model_choice="auto", **kw):
            calls.append(str(model_choice))
            return self.BLIP

        catalog = _catalog(
            _free("free:gemini:3.1-flash-lite", "gemini", "Gemini"),
            _free("free:groq:llama", "groq", "Groq"),
        )
        with (
            mock.patch("opai.app_state.ask", side_effect=fake_ask),
            mock.patch("opai.app_state.available_models", return_value=catalog),
        ):
            result = handle_gui_message(
                self.root,
                message,
                model_id="free:gemini:3.1-flash-lite",
                mode="ask",
                allow_cloud=True,
            )
        return result, calls

    def test_a_governed_request_is_not_silently_retried(self) -> None:
        _result, calls = self._run("publish the release to production")
        self.assertEqual(len(calls), 1, "an irreversible action must not repeat itself")

    def test_a_governed_failure_offers_no_automatic_reroute(self) -> None:
        result, _calls = self._run("publish the release to production")
        self.assertNotIn("fallback_offer", result)
        self.assertEqual(result["message_contract"]["lane"], "governed")
        self.assertFalse(result["message_contract"]["allowProviderFallback"])

    def test_the_same_failure_on_a_routine_request_does_recover(self) -> None:
        # The contrast that proves the restriction is the lane's, not a
        # regression in recovery.
        result, calls = self._run("explain this repo")
        self.assertEqual(len(calls), 2, "a routine request still absorbs a blip")
        self.assertEqual(result["message_contract"]["lane"], "stable")
        self.assertIn("fallback_offer", result)

    def test_every_turn_reports_the_lane_it_ran_in(self) -> None:
        result, _calls = self._run("explain this repo")
        contract = result["message_contract"]
        self.assertTrue(contract["laneLabel"])
        self.assertTrue(contract["reason"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
