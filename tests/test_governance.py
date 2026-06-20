import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opaihub.audit import (
    audit_path,
    GUARD_DENY,
    export_audit,
    read_audit,
    record_audit_event,
    summarize_audit,
    verify_chain,
)
from opaihub.ci_check import run_policy_check
from opaihub.guarded import build_evidence_packet, verify_evidence_packet
from opaihub.signing import resolve_key, sign, sign_payload, verify, verify_payload
from opaihub.team import team_report
from opaihub.team_policy import (
    apply_team_policy,
    init_team_policy,
    load_team_policy,
    validate_against_team_policy,
)


class SigningTests(unittest.TestCase):
    def test_sign_verify_roundtrip(self):
        payload = {"a": 1, "b": [1, 2, 3]}
        sig = sign_payload(payload, "key123")
        self.assertTrue(verify_payload(payload, sig, "key123"))

    def test_tamper_breaks_signature(self):
        payload = {"a": 1}
        sig = sign_payload(payload, "key123")
        self.assertFalse(verify_payload({"a": 2}, sig, "key123"))

    def test_wrong_key_fails(self):
        payload = {"a": 1}
        sig = sign_payload(payload, "key123")
        self.assertFalse(verify_payload(payload, sig, "other"))

    def test_env_key_takes_precedence(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"OPAI_SIGNING_KEY": "envkey"}, clear=False),
        ):
            key, source = resolve_key(Path(tmp))
            self.assertEqual((key, source), ("envkey", "env"))

    def test_sign_then_verify_via_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signed = sign(root, {"report": "x", "n": 1})
            self.assertIn("signature", signed)
            self.assertTrue(verify(root, signed)["verified"])
            signed["n"] = 2  # tamper
            self.assertFalse(verify(root, signed)["verified"])


class AuditTrailTests(unittest.TestCase):
    def test_chain_is_tamper_evident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_audit_event(root, "policy_deny", action="deploy prod")
            record_audit_event(root, GUARD_DENY, action="git push")
            self.assertTrue(verify_chain(root)["ok"])
            # Tamper with the log on disk.
            from opaihub.audit import audit_path

            lines = audit_path(root).read_text(encoding="utf-8").splitlines()
            entry = json.loads(lines[0])
            entry["action"] = "something else"
            lines[0] = json.dumps(entry, sort_keys=True)
            audit_path(root).write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertFalse(verify_chain(root)["ok"])

    def test_chain_detects_tail_truncation_with_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_audit_event(root, "policy_deny", action="deploy prod")
            record_audit_event(root, GUARD_DENY, action="git push")
            self.assertTrue(verify_chain(root)["ok"])

            lines = audit_path(root).read_text(encoding="utf-8").splitlines()
            audit_path(root).write_text(lines[0] + "\n", encoding="utf-8")

            result = verify_chain(root)
            self.assertFalse(result["ok"])
            self.assertIn("checkpoint", result["reason"].lower())

    def test_audit_does_not_store_raw_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_audit_event(
                root, "policy_deny", detail="token=sk-abcdef1234567890abcd"
            )
            raw = read_audit(root)[0]
        self.assertNotIn("sk-abcdef1234567890abcd", json.dumps(raw))

    def test_summary_counts_denied_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_audit_event(root, GUARD_DENY, action="git push")
            record_audit_event(root, "guard_allow", action="git status")
            summary = summarize_audit(root)
        self.assertEqual(summary["denied_actions"], 1)
        self.assertEqual(summary["event_count"], 2)

    def test_export_is_signed_and_verifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_audit_event(root, GUARD_DENY, action="deploy")
            bundle = export_audit(root, sign=True)
            self.assertIn("signature", bundle)
            self.assertTrue(verify(root, bundle)["verified"])


class TeamPolicyTests(unittest.TestCase):
    def test_init_apply_validate_conforms(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = init_team_policy(root, profile="team-safe", team="acme")
            self.assertEqual(created["status"], "created")
            self.assertEqual(apply_team_policy(root)["applied_profile"], "team-safe")
            result = validate_against_team_policy(root)
        self.assertTrue(result["ok"], result)

    def test_profile_drift_is_a_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_team_policy(root, profile="enterprise-strict")
            # Project never applied the team profile -> drift from default.
            result = validate_against_team_policy(root)
        self.assertFalse(result["ok"])
        checks = [v["check"] for v in result["violations"]]
        self.assertIn("profile", checks)

    def test_mcp_allowlist_enforced_when_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_team_policy(root)
            apply_team_policy(root)
            path = root / "opai-team-policy.yaml"
            path.write_text(
                path.read_text(encoding="utf-8")
                + "\napproved_mcp_servers: [filesystem]\n",
                encoding="utf-8",
            )
            result = validate_against_team_policy(root)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any(v["check"] == "approved_mcp_servers" for v in result["violations"])
        )

    def test_missing_team_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_team_policy(Path(tmp)))
            result = validate_against_team_policy(Path(tmp))
        self.assertFalse(result["ok"])


class CiCheckTests(unittest.TestCase):
    def test_default_after_apply_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_team_policy(root)
            apply_team_policy(root)
            result = run_policy_check(root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["failed"], 0)

    def test_missing_team_policy_does_not_block_but_cloud_and_contract_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_policy_check(Path(tmp))
        # team_policy is skipped when absent; cloud + contract still run.
        names = {c["name"]: c for c in result["checks"]}
        self.assertTrue(names["team_policy"].get("skipped"))
        self.assertTrue(names["cloud_models_gated"]["ok"])
        self.assertTrue(names["guarded_contract"]["ok"])

    def test_missing_team_policy_can_fail_closed_for_ci(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_policy_check(Path(tmp), require_team_policy=True)

        names = {c["name"]: c for c in result["checks"]}
        self.assertFalse(result["ok"])
        self.assertEqual("missing", names["team_policy"]["status"])
        self.assertIn("required", names["team_policy"]["message"])


class SignedEvidenceTests(unittest.TestCase):
    def test_signed_evidence_verifies_and_detects_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = build_evidence_packet(
                root,
                "release_preflight",
                checks=[{"name": "smoke", "ok": True}],
                write=False,
                sign=True,
            )
            self.assertIn("signature", packet)
            self.assertTrue(verify_evidence_packet(root, packet)["verified"])
            packet["contract_valid"] = not packet["contract_valid"]  # tamper
            self.assertFalse(verify_evidence_packet(root, packet)["verified"])

    def test_unsigned_packet_verifies_hash_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = build_evidence_packet(root, "ci_fixer", write=False, sign=False)
            result = verify_evidence_packet(root, packet)
        self.assertTrue(result["hash_ok"])
        self.assertFalse(result["signature"]["verified"])


class TeamReportTests(unittest.TestCase):
    def test_team_report_rolls_up_governance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_team_policy(root)
            apply_team_policy(root)
            record_audit_event(root, GUARD_DENY, action="git push")
            report = team_report(root)
        self.assertEqual(report["policy"]["active_profile"], "team-safe")
        self.assertTrue(report["policy"]["has_team_policy"])
        self.assertEqual(report["governance"]["denied_actions"], 1)
        self.assertTrue(report["governance"]["audit_chain_valid"])


if __name__ == "__main__":
    unittest.main()
