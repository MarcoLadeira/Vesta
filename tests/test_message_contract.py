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
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, make_repo
from opai import app_state as A
from opaihub.deadlines import TASK_DEADLINE, timeout_event
from opaihub.gui_pipeline import handle_gui_message
from opaihub.checkpoints import load_run_checkpoint
from opaihub.ledger import EVENT_OPERATION_INTENT, read_events

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

    def test_account_task_deadline_is_not_reported_as_provider_silence(self):
        class TaskDeadlineRunner(FakeAccountRunner):
            def complete(self, prompt, **kwargs):
                self.calls.append({"prompt": prompt, **kwargs})
                (kwargs["project_root"] / "retained.txt").write_text(
                    "partial change\n", encoding="utf-8"
                )
                return {
                    "text": "partial work",
                    "cost": 0.04,
                    "timed_out": True,
                    "timeout_event": timeout_event(
                        origin=TASK_DEADLINE,
                        owner="account_runner",
                        configured_seconds=1200.0,
                        elapsed_seconds=1200.0,
                        provider_responsive=True,
                        last_activity_seconds_ago=4.0,
                        phase="stream",
                        budget=kwargs.get("deadline_budget"),
                        operation_id=kwargs.get("operation_id"),
                        progress_observed=True,
                        external_effect_possible=True,
                        teardown_state="terminated",
                        cost_state="observed",
                        verification_state="incomplete",
                    ),
                }

        events = []
        with mock.patch(
            "opaihub.provider_reliability.record_provider_outcome"
        ) as record_provider_outcome:
            res = handle_gui_message(
                self.root,
                "implement a multi-file feature and run tests",
                model_id="account:claude:opus",
                mode="full-auto",
                account_runner=TaskDeadlineRunner(),
                on_event=events.append,
            )

        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["error"]["code"], "TASK_DEADLINE")
        self.assertEqual(res["raw_result"]["timeout_origin"], TASK_DEADLINE)
        self.assertEqual(res["raw_result"]["provider_condition"], "responsive")
        self.assertEqual(res["raw_result"]["partial_answer"], "partial work")
        self.assertEqual(res["raw_result"]["cost_usd"], 0.04)
        self.assertEqual(res["raw_result"]["cost_integrity"], "unreconciled")
        self.assertTrue(res["raw_result"]["cost_unreconciled"])
        self.assertTrue(
            any(path.endswith("retained.txt") for path in res["changed_files"])
        )
        self.assertEqual(res["partial_answer"], "partial work")
        self.assertEqual(res["completion_verdict"]["reason_code"], TASK_DEADLINE)
        self.assertEqual(res["run_result"]["lifecycle"]["state"], "timeout")
        self.assertFalse(res["run_result"]["recovery"]["automatic_retry"])
        self.assertEqual(res["run_result"]["recovery"]["reason"], "manual_review")
        timeout_authority = res["run_result"]["authority"]["timeout"]
        self.assertEqual(timeout_authority["origin"], TASK_DEADLINE)
        self.assertEqual(timeout_authority["provider_condition"], "responsive")
        self.assertEqual(timeout_authority["record_ref"]["kind"], "usage_ledger")
        self.assertEqual(res["timeout_event"]["checkpoint_id"], res["checkpoint_id"])
        record_provider_outcome.assert_not_called()
        retry_events = [
            event
            for event in events
            if (event.get("metadata") or {}).get("reason") == TASK_DEADLINE
        ]
        self.assertEqual(len(retry_events), 1)
        self.assertFalse(retry_events[0]["metadata"]["automaticRetry"])
        self.assertNotIn("did not receive a response", res["answer"].lower())
        self.assertNotIn("smaller", res["answer"].lower())

    def test_account_deadline_persists_progress_before_runner_teardown(self):
        class CheckpointingDeadlineRunner(FakeAccountRunner):
            def complete(self, prompt, **kwargs):
                self.calls.append({"prompt": prompt, **kwargs})
                (kwargs["project_root"] / "retained-before-stop.py").write_text(
                    "value = 1\n", encoding="utf-8"
                )
                event = timeout_event(
                    origin=TASK_DEADLINE,
                    owner="account_runner",
                    configured_seconds=kwargs["timeout"],
                    elapsed_seconds=kwargs["timeout"],
                    provider_responsive=True,
                    phase="complete",
                    budget=kwargs.get("deadline_budget"),
                    operation_id=kwargs.get("operation_id"),
                    progress_observed=True,
                    external_effect_possible=True,
                    teardown_state="requested",
                    verification_state="incomplete",
                )
                timeout_checkpoint = kwargs["on_timeout"](
                    {
                        "timeout_event": event,
                        "progress_evidence": {
                            "score": 12,
                            "distinct_observations": 7,
                        },
                        "verification_state": "incomplete",
                        "partial_answer_retained": True,
                    }
                )
                return {
                    "text": "partial work",
                    "cost": 0.04,
                    "timed_out": True,
                    "timeout_event": {
                        **event,
                        "teardown_state": "terminated",
                    },
                    "timeout_checkpoint": timeout_checkpoint,
                }

        res = handle_gui_message(
            self.root,
            "implement a multi-file feature and run tests",
            model_id="account:claude:opus",
            mode="full-auto",
            account_runner=CheckpointingDeadlineRunner(),
        )

        checkpoint = load_run_checkpoint(self.root, res["checkpoint_id"])
        snapshot = checkpoint.timeout["pre_teardown"]
        self.assertEqual(checkpoint.completion_state, "timeout")
        self.assertEqual(snapshot["timeout_event"]["timeout_origin"], TASK_DEADLINE)
        self.assertEqual(snapshot["progress_evidence"]["score"], 12)
        self.assertIn(
            "retained-before-stop.py",
            snapshot["repository"]["changed_during_run"],
        )
        self.assertTrue(res["raw_result"]["timeout_checkpoint"]["persisted"])

    def test_failed_pre_teardown_snapshot_never_promises_saved_continuation(self):
        class FailedSnapshotRunner(FakeAccountRunner):
            def complete(self, prompt, **kwargs):
                self.calls.append({"prompt": prompt, **kwargs})
                event = timeout_event(
                    origin=TASK_DEADLINE,
                    owner="account_runner",
                    configured_seconds=kwargs["timeout"],
                    elapsed_seconds=kwargs["timeout"],
                    provider_responsive=True,
                    phase="complete",
                    budget=kwargs.get("deadline_budget"),
                    operation_id=kwargs.get("operation_id"),
                    progress_observed=True,
                    external_effect_possible=True,
                    teardown_state="terminated",
                    verification_state="incomplete",
                )
                return {
                    "text": "partial work",
                    "cost": 0.04,
                    "timed_out": True,
                    "timeout_event": event,
                    "timeout_checkpoint": {
                        "state": "failed",
                        "persisted": False,
                        "recorded_before_teardown": False,
                    },
                }

        res = handle_gui_message(
            self.root,
            "implement a multi-file feature and run tests",
            model_id="account:claude:opus",
            mode="full-auto",
            account_runner=FailedSnapshotRunner(),
        )

        self.assertEqual(res["completion_verdict"]["reason_code"], TASK_DEADLINE)
        self.assertIn("pre-teardown progress snapshot", res["answer"].lower())
        self.assertNotIn("continue from the saved state", res["answer"].lower())
        self.assertFalse(
            res["raw_result"]["retained_progress"]["pre_teardown_snapshot"]
        )
        self.assertFalse(res["run_result"]["recovery"]["automatic_retry"])

    def test_unproven_task_deadline_teardown_never_projects_to_timeout(self):
        class UnconfirmedDeadlineRunner(FakeAccountRunner):
            def complete(self, prompt, **kwargs):
                self.calls.append({"prompt": prompt, **kwargs})
                return {
                    "text": "partial work",
                    "cost": 0.04,
                    "timed_out": True,
                    "status": "needs_attention",
                    "completion_state": "needs_attention",
                    "stopped_reason": "timeout_teardown_unconfirmed",
                    "timeout_event": timeout_event(
                        origin=TASK_DEADLINE,
                        owner="account_runner",
                        configured_seconds=1200.0,
                        elapsed_seconds=1200.0,
                        provider_responsive=True,
                        phase="stream",
                        budget=kwargs.get("deadline_budget"),
                        operation_id=kwargs.get("operation_id"),
                        progress_observed=True,
                        external_effect_possible=True,
                        teardown_state="force_terminating",
                        cost_state="observed",
                        verification_state="incomplete",
                    ),
                    "cancellation": {"phase": "force_terminating"},
                }

        res = handle_gui_message(
            self.root,
            "implement a multi-file feature and run tests",
            model_id="account:claude:opus",
            mode="full-auto",
            account_runner=UnconfirmedDeadlineRunner(),
        )

        self.assertEqual(res["status"], "needs_attention")
        self.assertEqual(res["run_result"]["lifecycle"]["state"], "needs_attention")
        self.assertEqual(
            res["raw_result"]["stopped_reason"], "timeout_teardown_unconfirmed"
        )
        self.assertEqual(res["timeout_event"]["timeout_origin"], TASK_DEADLINE)
        self.assertNotIn("did not receive a response", res["answer"].lower())
        self.assertNotIn("smaller", res["answer"].lower())

    def test_account_dispatch_uses_policy_deadline_and_records_operation_intent(self):
        contract = SimpleNamespace(
            lane="long_horizon",
            reason="test policy",
            task_type="feature_build",
            agent_mode="implement",
            allow_provider_fallback=True,
            max_transient_retries=0,
            max_tool_calls=40,
            max_active_seconds=42.0,
            isolate_context=False,
            requires_confirmation=False,
            matched_signals=(),
            to_dict=lambda: {
                "lane": "long_horizon",
                "maxActiveSeconds": 42.0,
            },
        )
        fake = FakeAccountRunner(text="done", cost=0.01)

        with mock.patch(
            "opaihub.gui_pipeline.resolve_message_contract", return_value=contract
        ):
            res = handle_gui_message(
                self.root,
                "implement a multi-file feature",
                model_id="account:claude:opus",
                mode="full-auto",
                account_runner=fake,
            )

        self.assertEqual(res["status"], "answered")
        self.assertEqual(fake.calls[0]["timeout"], 42.0)
        deadline_budget = fake.calls[0]["deadline_budget"]
        self.assertEqual(deadline_budget.task_deadline_seconds, 42.0)
        self.assertEqual(deadline_budget.provider_idle_timeout_seconds, 300.0)
        self.assertEqual(deadline_budget.lane, "long_horizon")
        self.assertTrue(fake.calls[0]["operation_id"])
        intent_events = [
            event
            for event in read_events(self.root)
            if event.get("event_type") == EVENT_OPERATION_INTENT
        ]
        self.assertEqual(len(intent_events), 1)
        self.assertEqual(intent_events[0]["operation_kind"], "model_call_paid")
        self.assertEqual(
            intent_events[0]["operation_id"], fake.calls[0]["operation_id"]
        )

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
