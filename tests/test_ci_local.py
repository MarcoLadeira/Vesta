from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_ci_local_module():
    path = ROOT / "scripts" / "ci_local.py"
    spec = importlib.util.spec_from_file_location("opai_ci_local", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load ci_local.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LocalCiEvidenceTests(unittest.TestCase):
    def test_required_missing_executable_fails_instead_of_becoming_a_green_skip(self):
        ci = _load_ci_local_module()
        step = ci.Step("missing executable", ["opai-tool-that-does-not-exist"])

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}):
                result = ci.main(["--profile", "fast", "--manifest", str(manifest)])

            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["verdict"], "failed")
        check = evidence["checks"][0]
        self.assertEqual(
            check["status"], {"execution": "unavailable", "outcome": "failed"}
        )
        self.assertIn("opai-tool-that-does-not-exist", check["message"])

    def test_required_missing_tool_fails_and_records_unavailable_evidence(self):
        ci = _load_ci_local_module()
        step = ci.Step(
            "missing required tool",
            ["opai-tool-that-does-not-exist"],
            required_modules=("opai_tool_that_does_not_exist",),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}):
                result = ci.main(["--profile", "fast", "--manifest", str(manifest)])

            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["verdict"], "failed")
        self.assertEqual(evidence["reason"], "required_check_unavailable")
        check = evidence["checks"][0]
        self.assertEqual(
            check["status"], {"execution": "unavailable", "outcome": "failed"}
        )
        self.assertTrue(check["required"])
        self.assertIn("opai_tool_that_does_not_exist", check["message"])

    def test_fast_profile_writes_machine_readable_passing_evidence(self):
        ci = _load_ci_local_module()
        step = ci.Step("passing check", [sys.executable, "-c", "raise SystemExit(0)"])

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}):
                result = ci.main(["--profile", "fast", "--manifest", str(manifest)])

            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["profile"], {"name": "fast", "version": 1})
        self.assertEqual(evidence["verdict"], "passed")
        self.assertEqual(
            evidence["checks"][0]["status"],
            {"execution": "executed", "outcome": "passed"},
        )
        self.assertTrue(evidence["checks"][0]["required"])
        self.assertIn("python", evidence["tool_versions"])
        self.assertIn("duration_seconds", evidence)

    def test_required_command_failure_is_product_failure_not_infrastructure(self):
        ci = _load_ci_local_module()
        step = ci.Step("failing check", [sys.executable, "-c", "raise SystemExit(7)"])

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}):
                result = ci.main(["--profile", "fast", "--manifest", str(manifest)])

            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["verdict"], "failed")
        self.assertEqual(evidence["reason"], "required_check_failed")
        self.assertEqual(evidence["classification"], "product")
        check = evidence["checks"][0]
        self.assertEqual(
            check["status"], {"execution": "executed", "outcome": "failed"}
        )
        self.assertEqual(check["returncode"], 7)
        self.assertEqual(check["failure_class"], "product")

    def test_release_profile_rejects_an_evidence_sha_for_another_commit(self):
        ci = _load_ci_local_module()
        passing_step = ci.Step(
            "passing check", [sys.executable, "-c", "raise SystemExit(0)"]
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with (
                mock.patch.object(ci, "PROFILE_STEPS", {"release": (passing_step,)}),
                mock.patch.object(ci, "_git_revision", return_value="a" * 40),
            ):
                result = ci.main(
                    [
                        "--profile",
                        "release",
                        "--candidate-sha",
                        "b" * 40,
                        "--manifest",
                        str(manifest),
                    ]
                )

            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["verdict"], "infrastructure_blocked")
        self.assertEqual(evidence["reason"], "candidate_sha_mismatch")
        self.assertEqual(evidence["checks"][0]["id"], "candidate-sha")
        self.assertEqual(
            evidence["checks"][0]["status"],
            {"execution": "executed", "outcome": "failed"},
        )


if __name__ == "__main__":
    unittest.main()
