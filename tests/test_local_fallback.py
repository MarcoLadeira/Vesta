"""Local model fallback (#179): capability-aware, threshold-gated, confirm-first."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vestahub.local_fallback import (
    FallbackDecision,
    capability_score,
    choose_local_fallback,
    observed_quality,
    parse_param_billions,
    plan_workflow_fallback,
    record_fallback_outcome,
)


def _model(model_id: str, name: str) -> dict[str, str]:
    return {"id": model_id, "provider": "ollama", "model": name}


class SizeParsingTests(unittest.TestCase):
    def test_parameter_counts_come_from_the_advertised_name(self):
        self.assertEqual(parse_param_billions("qwen2.5-coder:7b"), 7.0)
        self.assertEqual(parse_param_billions("llama3.2:3b"), 3.0)
        self.assertEqual(parse_param_billions("qwen2.5-coder:1.5b"), 1.5)
        self.assertEqual(parse_param_billions("gpt-oss-20b"), 20.0)
        self.assertEqual(parse_param_billions("phi3:mini"), 3.8)

    def test_unknown_sizes_return_none(self):
        self.assertIsNone(parse_param_billions("llama3"))
        self.assertIsNone(parse_param_billions("my-custom-model"))


class CapabilityTests(unittest.TestCase):
    def test_repair_needs_a_bigger_model_than_classification(self):
        small = _model("ollama:qwen2.5:1.5b", "qwen2.5:1.5b")
        self.assertTrue(capability_score(small, "context_classification")["capable"])
        self.assertFalse(capability_score(small, "repair")["capable"])

    def test_unknown_size_fails_closed(self):
        result = capability_score(_model("ollama:mystery", "mystery"), "planning")
        self.assertFalse(result["capable"])
        self.assertEqual(result["score"], 0.0)
        self.assertIn("fails closed", result["reasons"][0])

    def test_coding_focus_raises_the_score(self):
        coder = capability_score(
            _model("ollama:qwen2.5-coder:7b", "qwen2.5-coder:7b"), "repair"
        )
        generic = capability_score(_model("ollama:llama2:7b", "llama2:7b"), "repair")
        self.assertGreater(coder["score"], generic["score"])

    def test_unknown_task_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            capability_score(_model("x", "x:7b"), "vibes")


class DecisionTests(unittest.TestCase):
    def test_a_capable_local_model_is_used_without_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = choose_local_fallback(
                Path(tmp),
                "repair",
                [
                    _model("ollama:llama3.2:3b", "llama3.2:3b"),
                    _model("ollama:qwen2.5-coder:7b", "qwen2.5-coder:7b"),
                ],
            )
        self.assertTrue(decision.use_local)
        self.assertEqual(decision.model_id, "ollama:qwen2.5-coder:7b")
        self.assertEqual(decision.cloud_escalation, "not_needed")
        self.assertGreaterEqual(decision.score, decision.threshold)

    def test_below_threshold_requires_explicit_cloud_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = choose_local_fallback(
                Path(tmp),
                "repair",
                [_model("ollama:llama3.2:3b", "llama3.2:3b")],
            )
        self.assertFalse(decision.use_local)
        self.assertEqual(decision.cloud_escalation, "requires_confirmation")
        self.assertEqual(decision.model_id, "")

    def test_no_local_models_still_only_asks_for_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = choose_local_fallback(Path(tmp), "planning", [])
        self.assertFalse(decision.use_local)
        self.assertEqual(decision.cloud_escalation, "requires_confirmation")
        self.assertIn("no local models are connected", decision.reasons)

    def test_custom_threshold_overrides_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = choose_local_fallback(
                Path(tmp),
                "planning",
                [_model("ollama:llama3.2:3b", "llama3.2:3b")],
                threshold=0.99,
            )
        self.assertFalse(decision.use_local)
        self.assertEqual(decision.threshold, 0.99)


class ObservedQualityTests(unittest.TestCase):
    def test_outcomes_accumulate_durably(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_fallback_outcome(root, "m", "repair", success=True)
            record_fallback_outcome(root, "m", "repair", success=False)
            observed = observed_quality(root, "m", "repair")
        self.assertEqual(observed["successes"], 1)
        self.assertEqual(observed["failures"], 1)
        self.assertEqual(observed["rate"], 0.5)

    def test_repeated_failures_disqualify_a_previously_capable_model(self):
        model = _model("ollama:qwen2.5-coder:7b", "qwen2.5-coder:7b")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = choose_local_fallback(root, "repair", [model])
            self.assertTrue(before.use_local)

            for _ in range(3):
                record_fallback_outcome(
                    root, "ollama:qwen2.5-coder:7b", "repair", success=False
                )
            after = choose_local_fallback(root, "repair", [model])

        self.assertFalse(after.use_local)
        self.assertEqual(after.cloud_escalation, "requires_confirmation")

    def test_sparse_evidence_does_not_move_the_score(self):
        model = _model("ollama:qwen2.5-coder:7b", "qwen2.5-coder:7b")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_fallback_outcome(
                root, "ollama:qwen2.5-coder:7b", "repair", success=False
            )
            decision = choose_local_fallback(root, "repair", [model])
        self.assertTrue(decision.use_local)

    def test_unknown_task_kind_is_rejected_when_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                record_fallback_outcome(Path(tmp), "m", "vibes", success=True)


class DiscoveryTests(unittest.TestCase):
    def test_plan_workflow_fallback_uses_live_local_discovery_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "vestahub.local_runner.list_local_models",
                return_value=[_model("ollama:qwen2.5-coder:7b", "qwen2.5-coder:7b")],
            ) as discovery:
                decision = plan_workflow_fallback(Path(tmp), "repair")
        discovery.assert_called_once()
        self.assertIsInstance(decision, FallbackDecision)
        self.assertTrue(decision.use_local)

    def test_cli_reports_the_decision(self):
        import contextlib
        import io
        import json

        from vestahub.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("vestahub.local_runner.list_local_models", return_value=[]):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = main(["--project", tmp, "models", "fallback", "planning"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["cloud_escalation"], "requires_confirmation")


if __name__ == "__main__":
    unittest.main()
