import tempfile
import unittest
from pathlib import Path

from vestahub.eval_harness import run_eval
from vestahub.local_models import classify_endpoint, discover_local_models
from vestahub.policy import evaluate_action, list_profiles, resolve_policy, set_profile
from vestahub.router import route_task


class PolicyProfileTests(unittest.TestCase):
    def test_four_named_profiles_exist(self):
        profiles = list_profiles(Path.cwd())
        for name in ["solo-cheap", "solo-balanced", "team-safe", "enterprise-strict"]:
            self.assertIn(name, profiles)

    def test_default_profile_resolves_without_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolved = resolve_policy(Path(tmp))
        self.assertEqual(resolved["profile"], "solo-balanced")
        self.assertEqual(resolved["source"], "default")

    def test_set_profile_persists_as_project_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = set_profile(root, "enterprise-strict")
            self.assertEqual(result["status"], "updated")
            self.assertEqual(resolve_policy(root)["profile"], "enterprise-strict")

    def test_unknown_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = set_profile(Path(tmp), "does-not-exist")
        self.assertEqual(result["status"], "error")


class PolicyGateTests(unittest.TestCase):
    def test_expensive_frontier_tier_is_denied_in_strict_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_profile(root, "enterprise-strict")
            gate = evaluate_action(
                root, tier="L4", provider_type="cloud", cost_usd=2.0, paid=True
            )
        self.assertTrue(gate["denied"])
        self.assertEqual(gate["decision"], "deny")

    def test_any_cloud_call_requires_confirmation_regardless_of_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_profile(root, "solo-balanced")
            gate = evaluate_action(
                root, tier="L2", provider_type="cloud", cost_usd=0.01, paid=True
            )
        self.assertTrue(gate["requires_confirmation"])
        self.assertIn(
            "Cloud/paid model requires explicit confirmation.", gate["reasons"]
        )

    def test_destructive_action_fails_closed_in_strict_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_profile(root, "enterprise-strict")
            gate = evaluate_action(root, tier="L0", destructive=True)
        self.assertTrue(gate["denied"])

    def test_destructive_action_confirms_in_balanced_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_profile(root, "solo-balanced")
            gate = evaluate_action(root, tier="L0", destructive=True)
        self.assertEqual(gate["decision"], "confirm")

    def test_oversized_context_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_profile(root, "solo-cheap")
            gate = evaluate_action(root, tier="L0", estimated_tokens=999999)
        self.assertTrue(gate["requires_confirmation"])

    def test_hard_budget_overflow_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_profile(root, "solo-cheap")
            gate = evaluate_action(
                root, tier="L2", provider_type="cloud", cost_usd=999.0, paid=True
            )
        self.assertTrue(gate["denied"])

    def test_paid_disabled_profile_denies_cloud(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_profile(root, "enterprise-strict")
            gate = evaluate_action(
                root, tier="L2", provider_type="cloud", cost_usd=0.01, paid=True
            )
        self.assertTrue(gate["denied"])

    def test_local_deterministic_route_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            set_profile(root, "solo-balanced")
            gate = evaluate_action(root, tier="L0", provider_type="local")
        self.assertTrue(gate["allowed"])


class RouterPolicyTests(unittest.TestCase):
    def test_route_decision_includes_policy_profile_and_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = route_task(Path(tmp), "show git status")
        self.assertIn("policy_profile", decision)
        self.assertIn(decision["policy_decision"], {"allow", "confirm", "deny"})

    def test_deterministic_task_routes_local_and_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = route_task(Path(tmp), "show git status and diff")
        self.assertEqual(decision["model_tier"], "L0")
        self.assertEqual(decision["policy_decision"], "allow")


class LoopbackValidationTests(unittest.TestCase):
    def test_localhost_is_local(self):
        self.assertTrue(classify_endpoint("http://localhost:11434")["is_local"])
        self.assertTrue(classify_endpoint("http://127.0.0.1:1234")["is_local"])

    def test_private_lan_is_local(self):
        self.assertTrue(classify_endpoint("http://192.168.1.50:11434")["is_local"])
        self.assertTrue(classify_endpoint("http://10.0.0.5:8080")["is_local"])

    def test_public_https_is_not_local(self):
        result = classify_endpoint("https://api.openai.com/v1")
        self.assertFalse(result["is_local"])
        self.assertEqual(result["classification"], "public")
        self.assertTrue(result["requires_cloud_confirmation"])

    def test_discovery_does_not_report_public_url_as_available(self):
        import os
        from unittest import mock

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(
                os.environ,
                {"LOCAL_MODEL_URL": "https://remote.example.com/v1"},
                clear=False,
            ),
        ):
            # Ensure no local commands are picked up for this assertion.
            with mock.patch("vestahub.local_models.shutil.which", return_value=None):
                result = discover_local_models(Path(tmp))
        self.assertFalse(result["available"])
        self.assertIn(
            "LOCAL_MODEL_URL", result["remote_endpoints_require_confirmation"]
        )


class EvalHarnessTests(unittest.TestCase):
    def test_eval_writes_redacted_scorecard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scorecard = run_eval(root, write=True)
        self.assertGreater(scorecard["fixtures"], 0)
        self.assertIn("average_score", scorecard)
        self.assertIn("scorecard_path", scorecard)
        # No prompt text persisted - only hashes.
        for item in scorecard["results"]:
            self.assertIn("task_hash", item)
            self.assertNotIn("task", item)

    def test_eval_no_write_does_not_create_file(self):
        from vestahub.eval_harness import eval_path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_eval(root, write=False)
            self.assertFalse(eval_path(root).exists())


if __name__ == "__main__":
    unittest.main()
