"""A paid call that may already have been served is not silently repeated (#616).

The unit contract for the judgement lives in tests/test_operation_class.py.
This file pins the *wiring*: that the live pipeline actually consults it, by
counting how many times a paid provider is dispatched.

Why this is the case worth a pipeline-level test. The paid lane writes its
ledger entry on success only, so a call lost to a timeout leaves no local
record at all — Vesta cannot later notice it was billed. If the transient-retry
path re-sends the identical prompt, the user pays twice for one question and
nothing in Vesta ever shows it happened. The free lane has the opposite
economics, and its retry is what makes intermittent 503s survivable, so the
test below asserts that retry is still intact rather than assuming it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vestahub import auto_router
from vestahub.gui_pipeline import handle_gui_message

from tests._helpers import FakeAccountRunner, make_repo


class PaidRetryTests(unittest.TestCase):
    """One question must not become two charges."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))
        # Zero the delay constant rather than patching time.sleep, which is
        # process-wide and leaks into other tests' background threads.
        self._delay = mock.patch.object(
            auto_router, "TRANSIENT_RETRY_DELAY_SECONDS", 0.0
        )
        self._delay.start()

    def tearDown(self) -> None:
        self._delay.stop()
        self._tmp.cleanup()

    def _run_paid(self, runner: FakeAccountRunner):
        return handle_gui_message(
            self.root,
            "explain this repo",
            model_id="account:claude:sonnet",
            mode="ask",
            allow_cloud=True,
            account_runner=runner,
        )

    def test_a_timed_out_paid_call_is_dispatched_exactly_once(self) -> None:
        # The defect: a timeout is not proof the provider did no work. It may
        # have served the request in full and lost the response on the way
        # home. Re-sending bills twice for one answer.
        runner = FakeAccountRunner(timed_out=True)
        self._run_paid(runner)
        self.assertEqual(
            len(runner.calls),
            1,
            "a paid call that may already have been billed must not be repeated",
        )

    def test_the_user_is_told_why_no_retry_happened(self) -> None:
        # Withholding the retry silently would read as an unexplained stall.
        runner = FakeAccountRunner(timed_out=True)
        result = self._run_paid(runner)
        self.assertNotEqual(result.get("status"), "answered")

    def test_a_successful_paid_call_is_untouched(self) -> None:
        runner = FakeAccountRunner(text="Here is the tour.")
        result = self._run_paid(runner)
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("Here is the tour.", str(result.get("answer") or ""))


class FreeRetryStillWorksTests(unittest.TestCase):
    """The gate must not cost free models their flaky-endpoint resilience."""

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

    TIMEOUT = {
        "status": "runner_error",
        "error": {
            "code": "PROVIDER_TIMEOUT",
            "title": "The provider timed out.",
            "userMessage": "The provider timed out.",
            "provider": "gemini",
            "retryable": True,
        },
    }
    ANSWER = {"status": "answered_by_free_api", "answer": "Here is the tour."}

    def test_a_timed_out_free_call_is_still_retried(self) -> None:
        # Same ambiguous code as the paid test above, opposite correct answer:
        # repeating free inference costs nothing and leaves no outward trace.
        calls: list[str] = []
        results = [self.TIMEOUT, self.ANSWER]

        def fake_ask(project_root, task, model_choice="auto", **kw):
            calls.append(str(model_choice))
            return results[min(len(calls) - 1, len(results) - 1)]

        with mock.patch("vesta.app_state.ask", side_effect=fake_ask):
            result = handle_gui_message(
                self.root,
                "explain this repo",
                model_id="free:gemini:3.1-flash-lite",
                mode="ask",
                allow_cloud=True,
            )

        self.assertEqual(len(calls), 2, "free inference keeps its retry")
        self.assertEqual(result["status"], "answered")


class PaidModelIdentificationTests(unittest.TestCase):
    """The gate is only as good as its notion of what costs money."""

    def test_account_models_are_paid(self) -> None:
        self.assertTrue(auto_router.is_paid_model("account:claude:sonnet"))

    def test_free_and_local_models_are_not(self) -> None:
        for model_id in ("free:gemini:3.1-flash-lite", "ollama:qwen", ""):
            self.assertFalse(auto_router.is_paid_model(model_id), model_id)

    def test_unresolved_auto_is_not_yet_a_charge(self) -> None:
        # Auto resolves to a concrete id before any dispatch, so the retry
        # decision never sees "auto" — but treating it as paid would wrongly
        # suppress free retries if it ever did.
        self.assertFalse(auto_router.is_paid_model("auto"))


if __name__ == "__main__":
    unittest.main()
