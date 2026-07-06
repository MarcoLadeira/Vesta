"""Inline-capture proxy (#91, Epic A #85): route agent calls through OPai.

Fakes only — no real CLI, no network, no spend. Asserts the three guarantees:
classify+route+record, destructive gate before any paid call, and fail-open.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeAccountRunner, isolated_home, make_repo
from opaihub.budget import budget_status
from opaihub.ledger import EVENT_MODEL_CALL, read_events
from opaihub.proxy import SUPPORTED_AGENTS, proxy_run


def _model_calls(root: Path) -> list[dict]:
    return [e for e in read_events(root) if e.get("event_type") == EVENT_MODEL_CALL]


class RouteAndRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_happy_path_routes_and_marks_captured(self):
        fake = FakeAccountRunner(text="hello", cost=0.03)
        result = proxy_run(
            self.root, "summarize my changes", agent="claude", mode="ask", runner=fake
        )
        self.assertEqual(result["status"], "answered_by_account")
        self.assertTrue(result["captured"])
        self.assertEqual(result["agent"], "claude")
        self.assertEqual(len(fake.calls), 1)

    def test_records_exactly_one_model_call_with_real_cost(self):
        fake = FakeAccountRunner(text="x", cost=0.042)
        proxy_run(self.root, "do a thing", agent="claude", mode="ask", runner=fake)
        calls = _model_calls(self.root)
        self.assertEqual(len(calls), 1, "exactly one model_call must be recorded")
        self.assertAlmostEqual(calls[0]["estimated_actual_usd"], 0.042, places=4)
        self.assertGreater(budget_status(self.root)["spent"]["today_usd"], 0)

    def test_codex_with_unknown_cost_still_records(self):
        fake = FakeAccountRunner(account_id="codex", text="answer", cost=None)
        result = proxy_run(self.root, "task", agent="codex", mode="ask", runner=fake)
        self.assertTrue(result["captured"])
        self.assertEqual(len(_model_calls(self.root)), 1)

    def test_read_only_modes_do_not_allow_edits(self):
        for mode in ("ask", "plan", "approve-edits"):
            with self.subTest(mode=mode):
                fake = FakeAccountRunner(text="x", cost=0.01)
                proxy_run(self.root, "task", agent="claude", mode=mode, runner=fake)
                self.assertFalse(fake.calls[0]["allow_edits"])

    def test_auto_modes_allow_edits(self):
        for mode in ("safe-auto", "full-auto"):
            with self.subTest(mode=mode):
                fake = FakeAccountRunner(text="x", cost=0.01)
                proxy_run(
                    self.root, "make a change", agent="claude", mode=mode, runner=fake
                )
                self.assertTrue(fake.calls[0]["allow_edits"])

    def test_secret_in_task_never_persisted(self):
        fake = FakeAccountRunner(text="done", cost=0.02)
        proxy_run(
            self.root,
            "deploy with token=sk-supersecret9876543210abcd now",
            agent="claude",
            mode="ask",
            runner=fake,
        )
        blob = "".join(str(e) for e in read_events(self.root))
        self.assertNotIn("sk-supersecret9876543210abcd", blob)


class DestructiveGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_destructive_task_blocked_before_any_paid_call(self):
        fake = FakeAccountRunner(text="x", cost=0.05)
        result = proxy_run(
            self.root,
            "rm -rf the whole project",
            agent="claude",
            mode="ask",
            runner=fake,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["captured"])
        self.assertFalse(result["paid"])
        self.assertEqual(len(fake.calls), 0, "runner must not be called when blocked")
        self.assertEqual(len(_model_calls(self.root)), 0, "no spend recorded on block")

    def test_destructive_blocked_in_safe_auto_too(self):
        fake = FakeAccountRunner(text="x", cost=0.05)
        result = proxy_run(
            self.root,
            "delete everything with rm -rf",
            agent="claude",
            mode="safe-auto",
            runner=fake,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(fake.calls), 0)

    def test_full_auto_bypasses_gate(self):
        fake = FakeAccountRunner(text="done", cost=0.05)
        result = proxy_run(
            self.root,
            "rm -rf build artifacts",
            agent="claude",
            mode="full-auto",
            runner=fake,
        )
        self.assertEqual(result["status"], "answered_by_account")
        self.assertEqual(len(fake.calls), 1)


class FailOpenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_fail_open_runs_raw_agent_when_opai_path_errors(self):
        fake = FakeAccountRunner(text="raw answer", cost=0.01)
        with mock.patch(
            "opai.app_state._ask_account", side_effect=RuntimeError("opai broke")
        ):
            result = proxy_run(self.root, "task", agent="claude", runner=fake)
        self.assertEqual(result["status"], "fail_open")
        self.assertFalse(result["captured"])
        self.assertEqual(result["answer"], "raw answer")

    def test_fail_open_unavailable_when_no_runner(self):
        class Broken:
            def available(self):
                return False

        with mock.patch(
            "opai.app_state._ask_account", side_effect=RuntimeError("opai broke")
        ):
            result = proxy_run(self.root, "task", agent="claude", runner=Broken())
        self.assertEqual(result["status"], "fail_open_unavailable")
        self.assertFalse(result["captured"])

    def test_fail_open_never_raises_even_if_runner_explodes(self):
        class Bomb:
            def available(self):
                raise RuntimeError("kaboom")

        # _ask_account itself calls available() -> raises -> proxy catches ->
        # _fail_open also hits the bomb -> clean error, never propagates.
        result = proxy_run(self.root, "task", agent="claude", runner=Bomb())
        self.assertIn("fail_open", result["status"])
        self.assertFalse(result["captured"])


class AgentSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_supported_agents_include_gemini(self):
        # Gemini one-shot prompts can now enter the same capture-aware wrapper;
        # unsupported or interactive invocations still pass through unchanged.
        self.assertEqual(
            set(SUPPORTED_AGENTS), {"claude", "codex", "copilot", "gemini"}
        )

    def test_unsupported_agent_is_clean_and_never_calls_runner(self):
        fake = FakeAccountRunner(text="x", cost=0.01)
        result = proxy_run(self.root, "task", agent="cursor", runner=fake)
        self.assertEqual(result["status"], "unsupported_agent")
        self.assertFalse(result["captured"])
        self.assertEqual(len(fake.calls), 0)


class ProxyCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cli_unsupported_agent_exits_nonzero(self):
        from opai.cli import main

        rc = main(["proxy", "cursor", "do x", "--project", str(self.root)])
        self.assertEqual(rc, 1)

    def test_cli_no_connected_account_exits_cleanly(self):
        from opai.cli import main

        # Hermetic: isolate HOME so no real account is ever detected (and a real
        # claude/codex CLI is never invoked) -> account_not_connected, exit 1,
        # no crash and no spend recorded. This is also the CI parity case.
        with isolated_home():
            rc = main(["proxy", "claude", "summarize", "--project", str(self.root)])
        self.assertEqual(rc, 1)
        self.assertEqual(len(_model_calls(self.root)), 0)


if __name__ == "__main__":
    unittest.main()
