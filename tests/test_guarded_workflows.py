import tempfile
import unittest
from pathlib import Path

from opaihub.guarded import (
    build_evidence_packet,
    check_path_lock,
    guard_action,
    get_template,
    load_contract,
    validate_all_templates,
    validate_workflow,
)


class ContractTests(unittest.TestCase):
    def test_contract_declares_required_fields(self):
        contract = load_contract(Path.cwd())
        for field in [
            "path_locks",
            "evidence_artifacts",
            "stop_conditions",
            "fail_closed",
        ]:
            self.assertIn(field, contract["required_fields"])

    def test_all_templates_satisfy_contract(self):
        result = validate_all_templates(Path.cwd())
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["count"], 6)
        self.assertEqual(result["reference_implementation"], "mobile_readiness")

    def test_incomplete_workflow_is_flagged(self):
        result = validate_workflow({"id": "incomplete"}, Path.cwd())
        self.assertFalse(result["ok"])
        self.assertIn("path_locks", result["missing_fields"])

    def test_mobile_readiness_is_the_reference(self):
        template = get_template(Path.cwd(), "mobile_readiness")
        self.assertIsNotNone(template)
        self.assertIn("app store submit", template["fail_closed"])


class FailClosedTests(unittest.TestCase):
    def test_risky_push_is_denied_without_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = guard_action(Path(tmp), "git push origin main")
        self.assertEqual(result["decision"], "deny")
        self.assertTrue(result["fail_closed"])

    def test_risky_push_requires_confirmation_when_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = guard_action(Path(tmp), "git push origin main", confirmed=True)
        self.assertEqual(result["decision"], "confirm")

    def test_app_store_submit_fails_closed_for_mobile(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = guard_action(
                Path(tmp), "app store submit build", template_id="mobile_readiness"
            )
        self.assertEqual(result["decision"], "deny")

    def test_safe_action_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = guard_action(Path(tmp), "git status --short")
        self.assertEqual(result["decision"], "allow")


class PathLockTests(unittest.TestCase):
    def test_git_and_env_are_forbidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(check_path_lock(root, ".git/config")["allowed"])
            self.assertFalse(check_path_lock(root, ".env")["allowed"])

    def test_normal_source_path_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(check_path_lock(Path(tmp), "src/app.py")["allowed"])


class EvidencePacketTests(unittest.TestCase):
    def test_evidence_packet_is_hashable_and_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = build_evidence_packet(
                root, "release_preflight", checks=[{"name": "smoke", "ok": True}]
            )
            self.assertEqual(len(packet["packet_sha256"]), 64)
            self.assertTrue(packet["contract_valid"])
            self.assertIn("evidence_path", packet)
            self.assertTrue(Path(packet["evidence_path"]).exists())

    def test_evidence_packet_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = build_evidence_packet(Path(tmp), "ci_fixer", write=False)
        self.assertNotIn("evidence_path", packet)


if __name__ == "__main__":
    unittest.main()
