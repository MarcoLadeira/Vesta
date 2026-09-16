import tempfile
import unittest
from pathlib import Path

from vestahub.benchmark import run_benchmark
from vestahub.ledger import record_route_decision
from vestahub.proof import (
    build_proof_bundle,
    render_proof_markdown,
    verify_proof_bundle,
)
from vestahub.team_policy import apply_team_policy, init_team_policy


def _seed(root: Path) -> None:
    record_route_decision(root, "fix bug", model_tier="L0", agent="claude", repo="api")
    run_benchmark(root, suite="local", mode="both", write=True)
    init_team_policy(root)
    apply_team_policy(root)


class ProofBundleTests(unittest.TestCase):
    def test_bundle_is_signed_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            bundle = build_proof_bundle(root, sign=True)
            result = verify_proof_bundle(root, bundle)
        self.assertIn("signature", bundle)
        self.assertTrue(result["verified"], result)

    def test_tamper_fails_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            bundle = build_proof_bundle(root, sign=True)
            bundle["savings"]["estimated_savings_usd"] = 999.99
            result = verify_proof_bundle(root, bundle)
        self.assertFalse(result["verified"])

    def test_bundle_has_no_raw_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_route_decision(
                root, "deploy SECRET token=sk-abcdef1234567890", model_tier="L0"
            )
            bundle = build_proof_bundle(root, sign=True, allow_benchmark_run=True)
            blob = render_proof_markdown(bundle) + str(bundle)
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("sk-abcdef1234567890", blob)
        self.assertFalse(bundle["redaction_summary"]["raw_prompts_stored"])

    def test_bundle_reports_governed_agents_and_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            bundle = build_proof_bundle(root, sign=False)
        self.assertIn("claude", bundle["team_report"]["governed_agents"])
        self.assertGreater(bundle["savings"]["estimated_savings_usd"], 0.0)
        self.assertTrue(bundle["benchmark"]["present"])

    def test_missing_benchmark_fails_closed_on_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # No benchmark, and disallow auto-run -> benchmark absent.
            bundle = build_proof_bundle(root, sign=True, allow_benchmark_run=False)
            result = verify_proof_bundle(root, bundle)
        self.assertFalse(bundle["benchmark"]["present"])
        self.assertFalse(result["verified"])
        self.assertIn("benchmark artifact missing", result["problems"])


if __name__ == "__main__":
    unittest.main()
