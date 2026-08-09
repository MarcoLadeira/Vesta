"""Capture truth guarantees for the inline agent proxy (issues #92/#94)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, make_repo
from opai import app_state
from opai.gui_desktop import run_once
from opai.gui_view_model import build_view_model
from opaihub.ledger import (
    CAPTURE_RATE_DEFINITION,
    EVENT_CAPTURE_SESSION,
    read_events,
    record_capture_session,
    summarize_ledger,
)
from opaihub.proxy import proxy_run
from opaihub.proxy import SUPPORTED_AGENTS


def _capture_events(root: Path) -> list[dict]:
    return [
        event
        for event in read_events(root)
        if event.get("event_type") == EVENT_CAPTURE_SESSION
    ]


class CaptureSessionLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_completed_proxy_attempt_records_exactly_one_capture_event(self):
        result = proxy_run(
            self.root,
            "summarize the repository",
            agent="claude",
            mode="ask",
            runner=FakeAccountRunner(cost=0.02),
        )

        events = _capture_events(self.root)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["capture_id"], result["capture_id"])
        self.assertEqual(events[0]["outcome"], "completed")
        self.assertTrue(events[0]["captured"])
        self.assertTrue(events[0]["spend_accounted"])

    def test_every_supported_account_agent_gets_a_capture_event(self):
        for agent in SUPPORTED_AGENTS:
            with self.subTest(agent=agent):
                result = proxy_run(
                    self.root,
                    f"task for {agent}",
                    agent=agent,
                    runner=FakeAccountRunner(account_id=agent, cost=0.01),
                )
                matches = [
                    event
                    for event in _capture_events(self.root)
                    if event["capture_id"] == result["capture_id"]
                ]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["agent"], agent)

    def test_blocked_attempt_is_captured_without_phantom_spend(self):
        result = proxy_run(
            self.root,
            "rm -rf the project",
            agent="codex",
            mode="safe-auto",
            runner=FakeAccountRunner(cost=0.05),
        )

        event = _capture_events(self.root)[0]
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(event["outcome"], "blocked")
        self.assertTrue(event["captured"])
        self.assertFalse(event["paid"])
        self.assertFalse(event["spend_accounted"])

    def test_cancelled_attempt_has_honest_terminal_state(self):
        runner = FakeAccountRunner(cost=None)
        runner.complete = mock.Mock(
            return_value={"text": "partial", "cost": None, "cancelled": True}
        )

        result = proxy_run(
            self.root,
            "inspect the code",
            agent="claude",
            mode="ask",
            runner=runner,
        )

        event = _capture_events(self.root)[0]
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(event["outcome"], "cancelled")
        self.assertTrue(event["captured"])
        self.assertFalse(event["spend_accounted"])

    def test_provider_error_is_captured_but_not_claimed_as_accounted_spend(self):
        runner = FakeAccountRunner(cost=None)
        runner.complete = mock.Mock(return_value={"text": "", "error": "offline"})

        result = proxy_run(
            self.root,
            "inspect the code",
            agent="copilot",
            runner=runner,
        )

        event = _capture_events(self.root)[0]
        self.assertEqual(result["status"], "account_error")
        self.assertEqual(event["outcome"], "failed")
        self.assertTrue(event["captured"])
        self.assertFalse(event["spend_accounted"])

    def test_provider_timeout_keeps_the_proxy_timeout_contract(self):
        result = proxy_run(
            self.root,
            "inspect the code",
            agent="claude",
            runner=FakeAccountRunner(timed_out=True),
        )

        event = _capture_events(self.root)[0]
        self.assertEqual(result["status"], "account_timeout")
        self.assertEqual(result["error"]["code"], "PROVIDER_TIMEOUT")
        self.assertEqual(event["outcome"], "timeout")
        self.assertTrue(event["captured"])
        self.assertFalse(event["spend_accounted"])

    def test_fail_open_is_visible_as_an_uncaptured_attempt(self):
        runner = FakeAccountRunner(text="raw answer", cost=0.01)
        with mock.patch(
            "opai.app_state._ask_account", side_effect=RuntimeError("proxy failed")
        ):
            result = proxy_run(self.root, "task", agent="claude", runner=runner)

        event = _capture_events(self.root)[0]
        self.assertEqual(result["status"], "fail_open")
        self.assertEqual(event["outcome"], "fail_open")
        self.assertFalse(event["captured"])
        self.assertFalse(event["spend_accounted"])

    def test_model_ledger_failure_is_reported_without_breaking_the_answer(self):
        with (
            mock.patch(
                "opaihub.ledger.record_model_call_finalized",
                side_effect=OSError("disk full"),
            ),
            mock.patch(
                "opaihub.ledger.record_model_call", side_effect=OSError("disk full")
            ),
        ):
            result = proxy_run(
                self.root,
                "task",
                agent="claude",
                runner=FakeAccountRunner(cost=0.01),
            )

        self.assertEqual(result["status"], "answered_by_account")
        self.assertFalse(result["ledger_recorded"])
        self.assertFalse(_capture_events(self.root)[0]["spend_accounted"])

    def test_capture_event_is_idempotent_by_capture_id(self):
        first = record_capture_session(
            self.root,
            "private task",
            capture_id="capture-fixed",
            agent="claude",
            mode="ask",
            outcome="completed",
            captured=True,
            paid=True,
            spend_accounted=True,
        )
        second = record_capture_session(
            self.root,
            "private task",
            capture_id="capture-fixed",
            agent="claude",
            mode="ask",
            outcome="completed",
            captured=True,
            paid=True,
            spend_accounted=True,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(_capture_events(self.root)), 1)

    def test_capture_events_never_store_raw_prompts_or_secrets(self):
        secret = "sk-supersecret9876543210abcdef"
        proxy_run(
            self.root,
            f"review token={secret}",
            agent="claude",
            runner=FakeAccountRunner(cost=0.01),
        )

        persisted = str(read_events(self.root))
        self.assertNotIn(secret, persisted)
        self.assertNotIn("review token", persisted)


class CaptureAggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_zero_attempts_have_unknown_rate_not_zero_percent(self):
        capture = summarize_ledger(self.root)["capture"]
        self.assertIsNone(capture["rate_percent"])
        self.assertEqual(capture["label"], "No proxy sessions observed")

    def test_capture_rate_and_outcomes_are_aggregated(self):
        for capture_id, captured, outcome in (
            ("one", True, "completed"),
            ("two", True, "blocked"),
            ("three", False, "fail_open"),
        ):
            record_capture_session(
                self.root,
                "task",
                capture_id=capture_id,
                agent="claude",
                mode="ask",
                outcome=outcome,
                captured=captured,
                paid=outcome == "completed",
                spend_accounted=outcome == "completed",
            )

        capture = summarize_ledger(self.root)["capture"]
        self.assertEqual(capture["observed_sessions"], 3)
        self.assertEqual(capture["captured_sessions"], 2)
        self.assertEqual(capture["uncaptured_sessions"], 1)
        self.assertAlmostEqual(capture["rate_percent"], 66.7)
        self.assertEqual(capture["outcomes"]["fail_open"], 1)

    def test_gui_state_and_home_view_expose_capture_health(self):
        record_capture_session(
            self.root,
            "task",
            capture_id="one",
            agent="codex",
            mode="ask",
            outcome="completed",
            captured=True,
            paid=True,
            spend_accounted=True,
        )

        overview = app_state.overview(self.root)
        once = run_once(self.root)
        home = next(
            section
            for section in build_view_model(self.root)["sections"]
            if section["id"] == "home"
        )

        self.assertEqual(overview["capture"]["rate_percent"], 100.0)
        self.assertEqual(once["capture_rate_percent"], 100.0)
        self.assertIn("capture", once)
        capture_kpi = next(k for k in home["kpis"] if k["label"] == "Capture health")
        self.assertEqual(capture_kpi["value"], "100%")


class CaptureClaimContractTests(unittest.TestCase):
    """Copy and metric semantics must not exceed measured behavior (#9)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_capture_block_exposes_the_four_named_counts_and_denominator(self):
        capture = summarize_ledger(self.root)["capture"]
        for key in (
            "measurable_sessions",
            "captured_sessions",
            "pass_through_sessions",
            "unmeasured_sessions",
            "denominator",
            "definition",
        ):
            self.assertIn(key, capture, key)
        self.assertEqual(capture["denominator"], "observed proxy sessions")
        self.assertEqual(capture["unmeasured_sessions"], "unknown")

    def test_definition_is_the_single_canonical_source(self):
        capture = summarize_ledger(self.root)["capture"]
        self.assertEqual(capture["definition"], CAPTURE_RATE_DEFINITION)
        self.assertIn("not in the denominator", CAPTURE_RATE_DEFINITION["excludes"])
        self.assertIn(
            "Client readiness is not capture", CAPTURE_RATE_DEFINITION["excludes"]
        )

    def test_pass_through_equals_observed_minus_captured(self):
        for capture_id, captured, outcome in (
            ("a", True, "completed"),
            ("b", False, "fail_open"),
            ("c", False, "fail_open"),
        ):
            record_capture_session(
                self.root,
                "task",
                capture_id=capture_id,
                agent="claude",
                mode="ask",
                outcome=outcome,
                captured=captured,
                paid=False,
                spend_accounted=False,
            )
        capture = summarize_ledger(self.root)["capture"]
        self.assertEqual(capture["measurable_sessions"], 3)
        self.assertEqual(capture["captured_sessions"], 1)
        self.assertEqual(capture["pass_through_sessions"], 2)

    def test_full_client_readiness_never_implies_full_session_capture(self):
        # All clients wired, but zero sessions observed → rate is unknown, not
        # 100%. Readiness and capture are different denominators.
        capture = summarize_ledger(self.root)["capture"]
        self.assertIsNone(capture["rate_percent"])
        self.assertEqual(capture["measurable_sessions"], 0)

    def test_launch_and_readme_copy_carry_the_capture_boundary(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        checklist = (root / "docs" / "LAUNCH_CHECKLIST.md").read_text(encoding="utf-8")
        # The universal-capture claim is gone from the headline copy.
        self.assertNotIn("routes every task", readme)
        self.assertNotIn("routes every task", checklist)
        # The honest boundary is present in the README.
        self.assertIn("pass-through", readme.lower())


if __name__ == "__main__":
    unittest.main()
