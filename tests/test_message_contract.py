"""Message-flow status contract.

Every result OPai's chat can produce must be a clear, human, non-crashing
message - never a canned template, a raw dict, a subprocess command, or a stack
trace. These tests drive the real pipeline (``handle_gui_message`` ->
``app_state.ask`` -> ``_ask_account`` / ``run_ask``) with fakes, so no real
(paid) CLI, model, or network is ever touched.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, make_repo
from opai import app_state as A
from opaihub.gui_pipeline import handle_gui_message

# Tokens that must never appear in a user-facing answer.
_BANNED = [
    "timed out after",
    "--dangerously-skip-permissions",
    "Traceback (most recent call last)",
    "CompletedProcess",
    "TimeoutExpired",
    "'-p'",
    "subprocess.",
    "{'status'",
    "Review the selected local evidence",  # the old canned Plan template
]


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assertClean(self, answer: str) -> None:
        self.assertTrue(answer and answer.strip(), "answer must be non-empty")
        for token in _BANNED:
            self.assertNotIn(token, answer, f"answer leaked {token!r}")


class AccountStatusContractTests(_Base):
    def test_answered_by_account_is_clean(self):
        fake = FakeAccountRunner(text="1. Dark-mode toggle\n2. Tighter spacing")
        res = handle_gui_message(
            self.root,
            "list ui upgrades",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
        )
        self.assertEqual(res["status"], "answered")
        self.assertClean(res["answer"])
        self.assertIn("Dark-mode toggle", res["answer"])

    def test_account_timeout_is_clean_and_actionable(self):
        fake = FakeAccountRunner(timed_out=True)
        res = handle_gui_message(
            self.root,
            "rebuild everything",
            model_id="account:claude:opus",
            mode="full-auto",
            account_runner=fake,
        )
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["error"]["code"], "PROVIDER_TIMEOUT")
        self.assertClean(res["answer"])
        self.assertIn("smaller", res["answer"].lower())

    def test_account_error_is_clean_not_a_raw_exception(self):
        fake = FakeAccountRunner(raises=RuntimeError("kaboom internal"))
        res = handle_gui_message(
            self.root,
            "do x",
            model_id="account:codex",
            mode="full-auto",
            account_runner=fake,
        )
        self.assertClean(res["answer"])
        self.assertNotIn("kaboom internal", res["answer"])  # raw exc never shown

    def test_empty_account_response_is_failed_not_completed(self):
        fake = FakeAccountRunner(text="")
        events = []

        res = handle_gui_message(
            self.root,
            "do x",
            model_id="account:claude:sonnet",
            mode="ask",
            account_runner=fake,
            on_event=events.append,
        )

        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["error"]["code"], "NO_RESPONSE")
        self.assertNotIn("completed", [event["type"] for event in events])

    def test_account_not_connected_points_to_sign_in(self):
        missing = {
            "authStatus": "not_configured",
            "safeDiagnostic": "No provider sign-in was detected.",
            "lastError": None,
            "lastErrorCode": None,
        }
        with mock.patch(
            "opaihub.accounts.test_account_connection", return_value=missing
        ):
            res = handle_gui_message(
                self.root,
                "do x",
                model_id="account:codex",
                mode="ask",
            )
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["error"]["code"], "AUTH_MISSING")
        self.assertClean(res["answer"])
        self.assertIn("Settings", res["answer"])

    def test_panic_blocks_paid_account_with_clear_message(self):
        A.set_panic(self.root, True)
        fake = FakeAccountRunner(text="should not run")
        res = handle_gui_message(
            self.root,
            "do x",
            model_id="account:claude:sonnet",
            mode="full-auto",
            account_runner=fake,
        )
        self.assertEqual(res["status"], "blocked_panic")
        self.assertClean(res["answer"])
        self.assertEqual(fake.calls, [])  # never reached the model


class ModeContractTests(_Base):
    def test_current_explicit_intent_controls_edit_permission(self):
        for mode in ("ask", "plan", "approve-edits", "safe-auto", "full-auto"):
            with self.subTest(mode=mode):
                explain_runner = FakeAccountRunner(text="summary")
                handle_gui_message(
                    self.root,
                    "summarize my changes",
                    model_id="account:claude:sonnet",
                    mode=mode,
                    account_runner=explain_runner,
                )
                self.assertFalse(explain_runner.calls[-1].get("allow_edits"))

                implement_runner = FakeAccountRunner(text="fixed")
                handle_gui_message(
                    self.root,
                    "fix issue #1 and make a PR",
                    model_id="account:claude:sonnet",
                    mode=mode,
                    account_runner=implement_runner,
                )
                self.assertTrue(implement_runner.calls[-1].get("allow_edits"))

    def test_plan_mode_runs_model_not_canned_template(self):
        fake = FakeAccountRunner(text="Real plan: add a settings page, then tests.")
        res = handle_gui_message(
            self.root,
            "make a list of ui upgrades",
            model_id="account:claude:haiku",
            mode="plan",
            account_runner=fake,
        )
        self.assertIn("Real plan", res["answer"])
        self.assertClean(res["answer"])  # banned list includes the old template
        self.assertFalse(fake.calls[-1].get("allow_edits"))

    def test_destructive_request_blocked_before_any_model_call(self):
        fake = FakeAccountRunner(text="should not run")
        res = handle_gui_message(
            self.root,
            "please rm -rf the build folder",
            model_id="account:claude:sonnet",
            mode="safe-auto",
            account_runner=fake,
        )
        self.assertEqual(res["status"], "blocked")
        self.assertClean(res["answer"])
        self.assertIn("rm -rf", res["answer"])  # states the reason
        self.assertEqual(fake.calls, [])


class LocalRouteContractTests(_Base):
    """Drive the Auto/local branch by patching run_ask to each status."""

    def _run(self, ask_result):
        with mock.patch("opaihub.ask.run_ask", return_value=ask_result):
            return handle_gui_message(self.root, "x", model_id="auto", mode="ask")

    def test_answered_locally_is_clean(self):
        res = self._run(
            {"status": "answered_locally", "answer": "local says hi", "free": True}
        )
        self.assertEqual(res["status"], "answered")
        self.assertClean(res["answer"])

    def test_cache_hit_is_clean(self):
        res = self._run(
            {"status": "cache_hit", "answer": "cached answer", "free": True}
        )
        self.assertEqual(res["status"], "answered")
        self.assertClean(res["answer"])

    def test_no_local_model_offers_named_account_fallback(self):
        catalog = {
            "models": [
                {
                    "id": "account:claude:haiku",
                    "label": "Claude · Haiku 4.5",
                    "kind": "account",
                    "available": True,
                }
            ]
        }
        with mock.patch("opai.app_state.available_models", return_value=catalog):
            res = self._run(
                {"status": "no_local_model", "hint": "h", "next_command": "c"}
            )
        self.assertEqual(res["status"], "needs_auto_confirmation")
        self.assertClean(res["answer"])
        self.assertIn("Haiku", res["answer"])

    def test_confirmation_required_guides_to_account(self):
        # With no configured free model or connected account, Auto cannot
        # escalate, so a local "needs a paid tier" result guides the user to
        # connect one. (Pin an empty catalog so the test is independent of any
        # account connected on the host running it.)
        with mock.patch("opai.app_state.available_models", return_value={"models": []}):
            res = self._run({"status": "confirmation_required", "reason": "cloud tier"})
        self.assertEqual(res["status"], "needs_confirmation")
        self.assertClean(res["answer"])
        self.assertIn("account", res["answer"].lower())

    def test_runner_error_is_clean_not_empty(self):
        res = self._run({"status": "runner_error", "error": "boom internal trace"})
        self.assertClean(res["answer"])
        self.assertNotIn("boom internal trace", res["answer"])


if __name__ == "__main__":
    unittest.main()
