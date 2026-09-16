"""Evidence-based progress scoring (#569).

The scenario that motivated this module: a run spends 60+ steps on searches
and failed commands, learns plenty, and is stopped by a guard that only ever
counted "calls since the last edit". These tests pin the distinction the
counter could not make — *learning* keeps its budget, *repeating* does not.
"""

from __future__ import annotations

import unittest

from vestahub.progress_evidence import (
    SCORE_MUTATION,
    SCORE_NEW_EVIDENCE,
    SCORE_REPEAT,
    SCORE_REPEATED_FAILURE,
    ProgressLedger,
    observation_fingerprint,
)


def read(path: str, content: str, ok: bool = True) -> dict:
    return {"tool": "read_file", "arguments": path, "content": content, "ok": ok}


def edit(patch: str = "p1") -> dict:
    return {"tool": "apply_patch", "arguments": patch, "content": "applied", "ok": True}


class ScoringTests(unittest.TestCase):
    def test_an_edit_scores_highest_and_counts_a_milestone(self) -> None:
        ledger = ProgressLedger()
        self.assertEqual(ledger.record(edit()), SCORE_MUTATION)
        self.assertEqual(ledger.milestones, 1)

    def test_new_evidence_scores_but_less_than_an_edit(self) -> None:
        ledger = ProgressLedger()
        self.assertEqual(ledger.record(read("a.py", "alpha")), SCORE_NEW_EVIDENCE)
        self.assertLess(SCORE_NEW_EVIDENCE, SCORE_MUTATION)

    def test_re_reading_the_same_unchanged_file_scores_nothing(self) -> None:
        # The core anti-loop rule: identical action, identical result, no gain.
        ledger = ProgressLedger()
        ledger.record(read("a.py", "alpha"))
        self.assertEqual(ledger.record(read("a.py", "alpha")), SCORE_REPEAT)

    def test_re_reading_a_changed_file_is_new_evidence(self) -> None:
        # Fingerprints are content-based, so the same path with new content
        # genuinely is new information.
        ledger = ProgressLedger()
        ledger.record(read("a.py", "alpha"))
        self.assertEqual(ledger.record(read("a.py", "beta")), SCORE_NEW_EVIDENCE)

    def test_cross_tool_semantic_repeat_scores_nothing(self) -> None:
        ledger = ProgressLedger()
        first = read("a.py", "native output")
        first["action_fingerprint"] = "semantic-file-read"
        wrapper = {
            "tool": "run_command",
            "arguments": "cat a.py",
            "content": "different wrapper formatting",
            "ok": True,
            "action_fingerprint": "semantic-file-read",
        }
        ledger.record(first)
        self.assertEqual(ledger.record(wrapper), SCORE_REPEAT)

    def test_the_same_failure_twice_scores_negative(self) -> None:
        ledger = ProgressLedger()
        first = ledger.record(read("missing.py", "", ok=False))
        second = ledger.record(read("missing.py", "", ok=False))
        self.assertGreater(first, 0)  # a new failure is information
        self.assertEqual(second, SCORE_REPEATED_FAILURE)

    def test_a_success_clears_the_failure_streak_for_that_action(self) -> None:
        ledger = ProgressLedger()
        ledger.record(read("a.py", "", ok=False))
        ledger.record(read("a.py", "", ok=False))
        self.assertEqual(ledger.repeated_failure_count(), 2)
        ledger.record(read("a.py", "now readable"))
        self.assertEqual(ledger.repeated_failure_count(), 0)


class StagnationTests(unittest.TestCase):
    def test_a_long_productive_investigation_is_not_stagnant(self) -> None:
        # 60 distinct reads with no edit: exactly the run the old counter
        # killed. Each one teaches something, so it keeps its budget.
        ledger = ProgressLedger()
        for i in range(60):
            ledger.record(read(f"file{i}.py", f"contents {i}"))
        self.assertFalse(ledger.is_stagnant(patience=12))
        self.assertEqual(ledger.milestones, 0)  # and still no edit occurred

    def test_a_loop_on_one_file_becomes_stagnant_quickly(self) -> None:
        ledger = ProgressLedger()
        ledger.record(read("a.py", "alpha"))
        for _ in range(12):
            ledger.record(read("a.py", "alpha"))
        self.assertTrue(ledger.is_stagnant(patience=12))

    def test_repeated_failures_drive_the_score_down_not_flat(self) -> None:
        ledger = ProgressLedger()
        for _ in range(6):
            ledger.record(read("missing.py", "", ok=False))
        self.assertLess(ledger.score, ledger.best_score)
        self.assertTrue(ledger.is_stagnant(patience=4))

    def test_progress_resets_patience(self) -> None:
        ledger = ProgressLedger()
        for _ in range(10):
            ledger.record(read("a.py", "alpha"))  # repeats, no gain
        ledger.record(edit())  # real progress
        self.assertFalse(ledger.is_stagnant(patience=12))
        self.assertEqual(ledger.steps_since_best, 0)


class FingerprintTests(unittest.TestCase):
    def test_fingerprints_are_stable_and_content_sensitive(self) -> None:
        a = observation_fingerprint("read_file", "a.py", "alpha")
        self.assertEqual(a, observation_fingerprint("read_file", "a.py", "alpha"))
        self.assertNotEqual(a, observation_fingerprint("read_file", "a.py", "beta"))
        self.assertNotEqual(a, observation_fingerprint("read_file", "b.py", "alpha"))

    def test_huge_output_is_bounded_and_does_not_raise(self) -> None:
        self.assertEqual(
            len(observation_fingerprint("read_file", "big", "x" * 5_000_000)), 32
        )

    def test_unicode_content_is_handled(self) -> None:
        self.assertEqual(len(observation_fingerprint("读取", "路径", "内容" * 100)), 32)


class SummaryTests(unittest.TestCase):
    def test_summary_is_json_safe_and_explains_the_decision(self) -> None:
        import json

        ledger = ProgressLedger()
        ledger.record(edit())
        ledger.record(read("a.py", "alpha"))
        ledger.record(read("a.py", "alpha"))
        summary = ledger.summary()
        json.dumps(summary)  # must not raise
        self.assertEqual(summary["milestones"], 1)
        self.assertEqual(summary["distinct_observations"], 2)
        self.assertIn("steps_since_best", summary)


if __name__ == "__main__":
    unittest.main()
