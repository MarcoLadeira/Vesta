from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vestahub.agent_policy import AgentMode, resolve_agent_policy
from vestahub.agent_runtime import AgentRuntime, RuntimePhase
from vestahub.safety_gates import evaluate_safety_gates


class VestaBenchScenarios(unittest.TestCase):
    def test_latest_implementation_request_beats_stale_read_only_text(self):
        policy = resolve_agent_policy(
            "Generic guidance: do not edit files. Latest request: fix the bug and run tests."
        )
        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)

    def test_runtime_cannot_jump_from_intent_to_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = AgentRuntime(Path(tmp), task="merge it")
            runtime.transition(RuntimePhase.INTENT_RESOLVED, message="Ship mode")
            with self.assertRaises(ValueError):
                runtime.transition(RuntimePhase.MERGED, message="unsafe jump")

    def test_merge_is_blocked_when_any_gate_is_unknown(self):
        report = evaluate_safety_gates(changed_files=(), intended_files=())
        self.assertFalse(report.can_ship)
        self.assertIn("tests", report.failed)
        self.assertIn("pr_checks", report.failed)

    def test_negative_safety_rules_are_constraints_not_dangerous_requests(self):
        policy = resolve_agent_policy(
            "Implement the feature. Never force-push; ask before production credential changes."
        )
        self.assertEqual(policy.mode, AgentMode.IMPLEMENT)

    def test_full_vestabench_runner_covers_every_dimension_and_passes(self):
        from vestahub.vestabench import DIMENSIONS, run_vestabench

        with tempfile.TemporaryDirectory() as tmp:
            report = run_vestabench(Path(tmp), write=False)

        self.assertEqual(set(report["dimensions"]), set(DIMENSIONS))
        self.assertEqual(report["totals"]["passed"], report["totals"]["total"])
        self.assertEqual(report["cost"]["cloud_calls"], 0)


if __name__ == "__main__":
    unittest.main()
