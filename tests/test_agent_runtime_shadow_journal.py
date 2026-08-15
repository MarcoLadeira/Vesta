"""#613 Stage 2: agent runtime state mirrors into the shadow journal.

``opaihub/agent_runtime.py`` is Stage 1's "runs: agent process state". It is
the third table shape to adopt the shared helper (after ``leases`` and
``operations``), and the first whose record carries an *accumulating* field:
``history`` grows with every phase transition.

That matters for the mirror. The helper stores whole snapshots and lets the
latest win, so a growing history is reconstructed correctly without any
per-field reduce logic -- but only if every write is mirrored. A dropped
intermediate write would leave the shadow with a *shorter* history than the
file while every other field still matched, which is exactly the kind of
quiet divergence the contradiction report has to catch. These tests pin that.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opaihub.agent_runtime import AgentRuntime, RuntimePhase


class AgentRuntimeShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _runtime(self, task_id: str = "task-1") -> AgentRuntime:
        return AgentRuntime(self.root, task="do the thing", task_id=task_id)

    def test_the_opening_idle_state_is_mirrored(self):
        """A run that never left idle must still leave a trace.

        The constructor's first write is the only record an orphaned run has.
        Dropping it -- the trap that silently lost every checkpoint's
        ``pending`` record until its tests caught it -- would make exactly the
        runs #613 exists to reconstruct invisible.
        """
        runtime = self._runtime()

        projection = runtime.shadow_journal_projection()

        self.assertEqual(projection["phase"], RuntimePhase.IDLE.value)
        self.assertEqual(projection, runtime.state.to_dict())
        self.assertIsNone(runtime.contradiction_report())

    def test_each_transition_is_mirrored_and_agrees_with_the_file(self):
        runtime = self._runtime()

        runtime.transition(RuntimePhase.INTENT_RESOLVED, message="intent resolved")
        runtime.transition(RuntimePhase.REPO_RESOLVED, message="repo resolved")

        self.assertEqual(runtime.shadow_journal_projection(), runtime.state.to_dict())
        self.assertIsNone(runtime.contradiction_report())

    def test_the_accumulating_history_is_reconstructed_in_full(self):
        """Whole-snapshot mirroring must not truncate a growing field."""
        runtime = self._runtime()
        runtime.transition(RuntimePhase.INTENT_RESOLVED, message="one")
        runtime.transition(RuntimePhase.REPO_RESOLVED, message="two")
        runtime.transition(RuntimePhase.PLANNING, message="three")

        projection = runtime.shadow_journal_projection()

        self.assertEqual(len(projection["history"]), len(runtime.state.history))
        self.assertEqual(
            [event["phase"] for event in projection["history"]],
            [event.phase for event in runtime.state.history],
        )
        self.assertIsNone(runtime.contradiction_report())

    def test_a_refused_transition_writes_neither_side(self):
        runtime = self._runtime()
        runtime.transition(RuntimePhase.INTENT_RESOLVED, message="ok")
        before = runtime.shadow_journal_projection()

        with self.assertRaises(Exception):
            # idle -> merged is not a permitted forward transition.
            runtime.transition(RuntimePhase.MERGED, message="illegal jump")

        self.assertEqual(runtime.shadow_journal_projection(), before)
        self.assertIsNone(runtime.contradiction_report())

    def test_reopening_the_same_task_does_not_diverge(self):
        """The constructor reads existing state instead of rewriting it."""
        first = self._runtime()
        first.transition(RuntimePhase.INTENT_RESOLVED, message="ok")

        second = self._runtime()

        self.assertEqual(second.state.phase, RuntimePhase.INTENT_RESOLVED)
        self.assertIsNone(second.contradiction_report())
        self.assertEqual(second.shadow_journal_projection(), second.state.to_dict())

    def test_an_out_of_band_write_is_reported(self):
        runtime = self._runtime()
        runtime.transition(RuntimePhase.INTENT_RESOLVED, message="ok")
        tampered = {**runtime.state.to_dict(), "phase": "merged", "message": "x"}
        runtime.path.write_text(json.dumps(tampered), encoding="utf-8")

        report = runtime.contradiction_report()

        self.assertIsNotNone(report)
        self.assertEqual(report["task_id"], "task-1")
        self.assertIn("phase", report["mismatched_fields"])

    def test_a_shorter_shadow_history_is_reported_as_a_divergence(self):
        """The quiet failure this shape is most exposed to.

        If a mirrored write were ever dropped, the shadow would carry a
        shorter history while every other field still matched. That must read
        as a contradiction, not as agreement.
        """
        runtime = self._runtime()
        runtime.transition(RuntimePhase.INTENT_RESOLVED, message="one")
        runtime.transition(RuntimePhase.REPO_RESOLVED, message="two")

        current = runtime.state.to_dict()
        truncated = {**current, "history": current["history"][:1]}
        runtime.path.write_text(json.dumps(truncated), encoding="utf-8")

        report = runtime.contradiction_report()

        self.assertIsNotNone(report)
        self.assertIn("history", report["mismatched_fields"])


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
