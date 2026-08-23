from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _check(evidence: dict, check_id: str) -> dict:
    return next(check for check in evidence["checks"] if check["id"] == check_id)


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
    def setUp(self) -> None:
        # GitHub Actions exports CI=true for every step, and ci_local reads it
        # (`explicit_required=bool(os.environ.get("CI"))`) to decide a run must
        # name its candidate SHA explicitly. These tests call ci.main() in this
        # process and deliberately omit --candidate-sha, so the inherited marker
        # made every one of them fail on `candidate_sha_missing` before the
        # behaviour under test ever ran: green on a developer machine, red on
        # hosted CI, for reasons having nothing to do with the assertion.
        #
        # Scrub the ambient markers here rather than per test, so a test added
        # later cannot silently reintroduce the divergence. Tests that want CI
        # semantics set CI themselves with an explicit patch.dict.
        super().setUp()
        hermetic = {
            key: value
            for key, value in os.environ.items()
            if key != "CI" and not key.startswith("GITHUB_")
        }
        ambient = mock.patch.dict(os.environ, hermetic, clear=True)
        ambient.start()
        self.addCleanup(ambient.stop)

    def test_required_missing_executable_fails_instead_of_becoming_a_green_skip(self):
        ci = _load_ci_local_module()
        step = ci.Step("missing executable", ["opai-tool-that-does-not-exist"])

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}):
                result = ci.main(["--profile", "fast", "--manifest", str(manifest)])

            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["verdict"], "infrastructure_blocked")
        check = _check(evidence, "missing executable")
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
        self.assertEqual(evidence["verdict"], "infrastructure_blocked")
        self.assertEqual(evidence["reason"], "required_check_unavailable")
        check = _check(evidence, "missing required tool")
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
        self.assertEqual(evidence["schema_version"], 2)
        self.assertEqual(
            evidence["profile"], {"name": "fast", "version": 2, "component": "all"}
        )
        self.assertEqual(evidence["verdict"], "qualified")
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
        self.assertEqual(evidence["verdict"], "test_failed")
        self.assertEqual(evidence["reason"], "required_test_failed")
        self.assertEqual(evidence["classification"], "test")
        check = _check(evidence, "failing check")
        self.assertEqual(
            check["status"], {"execution": "executed", "outcome": "failed"}
        )
        self.assertEqual(check["returncode"], 7)
        self.assertEqual(check["failure_class"], "test")

    def test_executed_optional_failure_is_recorded_as_failure_not_skip(self):
        ci = _load_ci_local_module()
        step = ci.Step(
            "optional diagnostic",
            [sys.executable, "-c", "raise SystemExit(5)"],
            required=False,
        )

        check = ci._run(step)

        self.assertEqual(
            check["status"], {"execution": "executed", "outcome": "failed"}
        )
        self.assertFalse(check["required"])

    def test_security_failure_remains_distinct_from_test_failure(self):
        ci = _load_ci_local_module()
        step = ci.Step(
            "security check",
            [sys.executable, "-c", "raise SystemExit(9)"],
            failure_class="security",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}):
                result = ci.main(["--profile", "fast", "--manifest", str(manifest)])
            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["verdict"], "security_failed")
        self.assertEqual(evidence["classification"], "security")
        self.assertEqual(
            _check(evidence, "security check")["failure_class"], "security"
        )

    def test_python_profile_runs_release_identity_drift_after_generation_drift(self):
        ci = _load_ci_local_module()
        names = [step.name for step in ci.PYTHON_STEPS]

        assert "release-identity-drift" in names
        assert (
            names.index("release-identity-drift")
            == names.index("lifecycle-projection-drift") + 1
        )
        step = ci.PYTHON_STEPS[names.index("release-identity-drift")]
        assert step.argv == [sys.executable, "scripts/check_release_identity.py"]
        assert step.failure_class == "policy"

    def test_required_timeout_is_typed_infrastructure_blockage(self):
        ci = _load_ci_local_module()
        step = ci.Step(
            "timed check",
            [sys.executable, "-c", "import time; time.sleep(1)"],
            timeout_seconds=0.01,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}):
                result = ci.main(["--profile", "fast", "--manifest", str(manifest)])
            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["verdict"], "infrastructure_blocked")
        self.assertEqual(evidence["reason"], "required_check_timeout")
        self.assertEqual(
            _check(evidence, "timed check")["status"],
            {"execution": "timed_out", "outcome": "failed"},
        )

    def test_failure_diagnostics_are_redacted_and_bounded(self):
        ci = _load_ci_local_module()
        secret = "sk-opai-this-must-never-enter-evidence"
        script = f"print({secret!r}); print('x' * 10000); raise SystemExit(4)"
        step = ci.Step("noisy failure", [sys.executable, "-c", script])

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}):
                ci.main(["--profile", "fast", "--manifest", str(manifest)])
            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        diagnostic = _check(evidence, "noisy failure")["diagnostic"]
        self.assertNotIn(secret, diagnostic)
        self.assertIn("[REDACTED", diagnostic)
        self.assertLessEqual(len(diagnostic), ci.MAX_DIAGNOSTIC_CHARS)

    def test_missing_required_node_executable_is_not_a_green_web_skip(self):
        ci = _load_ci_local_module()
        step = ci.Step(
            "web-unit",
            ["npm", "run", "test:unit"],
            required_executables=("node", "npm"),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            original_which = ci.shutil.which
            with (
                mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}),
                mock.patch.object(
                    ci.shutil,
                    "which",
                    side_effect=lambda name: (
                        None if name in {"node", "npm"} else original_which(name)
                    ),
                ),
            ):
                result = ci.main(["--profile", "fast", "--manifest", str(manifest)])
            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(
            _check(evidence, "web-unit")["status"]["execution"], "unavailable"
        )
        self.assertIn("node", _check(evidence, "web-unit")["message"])

    def test_ci_requires_an_explicit_exact_candidate_sha(self):
        ci = _load_ci_local_module()
        step = ci.Step("passing", [sys.executable, "-c", "raise SystemExit(0)"])

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with (
                mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}),
                mock.patch.object(ci, "_git_revision", return_value="a" * 40),
                mock.patch.dict(os.environ, {"CI": "true"}, clear=True),
            ):
                result = ci.main(["--profile", "fast", "--manifest", str(manifest)])
            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["verdict"], "infrastructure_blocked")
        self.assertEqual(evidence["reason"], "candidate_sha_missing")

    def test_declared_check_inventory_cannot_be_silently_deleted(self):
        ci = _load_ci_local_module()
        step = ci.Step("only-check", [sys.executable, "-c", "raise SystemExit(0)"])

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with (
                mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}),
                mock.patch.object(ci, "_expected_check_ids", return_value=["must-run"]),
            ):
                result = ci.main(["--profile", "fast", "--manifest", str(manifest)])
            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["verdict"], "infrastructure_blocked")
        inventory = next(
            check for check in evidence["checks"] if check["id"] == "check-inventory"
        )
        self.assertIn("must-run", inventory["message"])

    def test_actual_required_python_tools_each_fail_when_missing(self):
        ci = _load_ci_local_module()
        cases = {
            "ruff-format": "ruff",
            "hostile-pytest": "pytest",
            "bandit": "bandit",
        }
        all_steps = {
            step.name: step
            for components in ci.PROFILE_COMPONENT_STEPS.values()
            for steps in components.values()
            for step in steps
        }
        original = ci.importlib.util.find_spec
        for check_id, missing_module in cases.items():
            with (
                self.subTest(check_id=check_id),
                mock.patch.object(
                    ci.importlib.util,
                    "find_spec",
                    side_effect=lambda name, target=missing_module: (
                        None if name == target else original(name)
                    ),
                ),
            ):
                record = ci._run(all_steps[check_id])
            self.assertEqual(
                record["status"], {"execution": "unavailable", "outcome": "failed"}
            )
            self.assertEqual(record["failure_class"], "infrastructure")

    def test_network_outage_is_infrastructure_not_security_failure(self):
        ci = _load_ci_local_module()
        step = ci.Step(
            "network audit",
            [sys.executable, "-c", "print('ENETUNREACH'); raise SystemExit(1)"],
            failure_class="security",
            infrastructure_patterns=("ENETUNREACH",),
        )
        record = ci._run(step)
        self.assertEqual(record["status"]["outcome"], "failed")
        self.assertEqual(record["failure_class"], "infrastructure")

    def test_provider_prerequisites_are_credential_unavailable_not_skipped(self):
        ci = _load_ci_local_module()
        with mock.patch.dict(os.environ, {}, clear=True):
            record = ci._run(ci.PROVIDER_STEPS[0])
        self.assertEqual(
            record["status"], {"execution": "unavailable", "outcome": "failed"}
        )
        self.assertEqual(record["failure_class"], "credential")
        self.assertNotIn("skipped", json.dumps(record))

    def test_provider_canary_has_no_test_runtime_dependency(self):
        ci = _load_ci_local_module()
        step = ci.PROVIDER_STEPS[0]

        self.assertEqual(
            step.argv,
            [sys.executable, "scripts/run_provider_canary.py"],
        )
        self.assertEqual(step.required_modules, ())

    def test_controlled_failure_drills_keep_policy_security_and_test_types(self):
        ci = _load_ci_local_module()
        for check_id, classification in (
            ("lifecycle-projection-drift", "policy"),
            ("bandit", "security"),
            ("python-unittest", "test"),
            ("web-unit", "test"),
        ):
            step = ci.Step(
                check_id,
                [sys.executable, "-c", "raise SystemExit(1)"],
                failure_class=classification,
            )
            with self.subTest(check_id=check_id):
                record = ci._run(step)
                self.assertEqual(record["status"]["outcome"], "failed")
                self.assertEqual(record["failure_class"], classification)

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

    def test_explicit_candidate_evidence_rejects_a_dirty_worktree(self):
        ci = _load_ci_local_module()
        step = ci.Step("passing", [sys.executable, "-c", "raise SystemExit(0)"])

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with (
                mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}),
                mock.patch.object(ci, "_git_revision", return_value="a" * 40),
                mock.patch.object(
                    ci, "_git_workspace_clean", return_value=False, create=True
                ),
                mock.patch.object(ci, "_run", wraps=ci._run) as run,
            ):
                result = ci.main(
                    [
                        "--profile",
                        "fast",
                        "--candidate-sha",
                        "a" * 40,
                        "--manifest",
                        str(manifest),
                    ]
                )
                run.assert_not_called()
            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["verdict"], "infrastructure_blocked")
        self.assertEqual(evidence["reason"], "candidate_workspace_dirty")
        self.assertFalse(evidence["candidate"]["workspace_clean"])
        self.assertFalse(evidence["candidate"]["promotable"])

    def test_explicit_candidate_blocks_when_cleanliness_cannot_be_verified(self):
        ci = _load_ci_local_module()
        step = ci.Step("passing", [sys.executable, "-c", "raise SystemExit(0)"])

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with (
                mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}),
                mock.patch.object(ci, "_git_revision", return_value="a" * 40),
                mock.patch.object(ci, "_git_workspace_clean", return_value=None),
            ):
                result = ci.main(
                    [
                        "--profile",
                        "fast",
                        "--candidate-sha",
                        "a" * 40,
                        "--manifest",
                        str(manifest),
                    ]
                )
            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["reason"], "candidate_workspace_unverified")
        self.assertIsNone(evidence["candidate"]["workspace_clean"])

    def test_pull_request_source_must_be_a_parent_of_the_tested_merge(self):
        ci = _load_ci_local_module()
        step = ci.Step("passing", [sys.executable, "-c", "raise SystemExit(0)"])
        source_sha = "a" * 40
        merge_sha = "b" * 40

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with (
                mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}),
                mock.patch.object(ci, "_git_revision", return_value=merge_sha),
                mock.patch.object(ci, "_git_workspace_clean", return_value=True),
                mock.patch.object(
                    ci,
                    "_git_parent_shas",
                    return_value=("c" * 40, source_sha),
                    create=True,
                ),
            ):
                result = ci.main(
                    [
                        "--profile",
                        "fast",
                        "--candidate-sha",
                        merge_sha,
                        "--source-sha",
                        source_sha,
                        "--manifest",
                        str(manifest),
                    ]
                )
            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(evidence["candidate_sha"], merge_sha)
        self.assertEqual(evidence["release_identity"]["build_id"], merge_sha)
        self.assertEqual(evidence["release_identity"]["application_version"], "0.2.1a1")
        self.assertEqual(evidence["source_sha"], source_sha)
        self.assertTrue(evidence["candidate"]["tests_merge_candidate"])
        self.assertTrue(evidence["candidate"]["promotable"])

    def test_unrelated_source_cannot_be_bound_to_a_tested_candidate(self):
        ci = _load_ci_local_module()
        step = ci.Step("must-not-run", [sys.executable, "-c", "raise SystemExit(0)"])
        source_sha = "a" * 40
        merge_sha = "b" * 40

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "evidence.json"
            with (
                mock.patch.object(ci, "PROFILE_STEPS", {"fast": (step,)}),
                mock.patch.object(ci, "_git_revision", return_value=merge_sha),
                mock.patch.object(ci, "_git_workspace_clean", return_value=True),
                mock.patch.object(
                    ci, "_git_parent_shas", return_value=("c" * 40,), create=True
                ),
                mock.patch.object(ci, "_run", wraps=ci._run) as run,
            ):
                result = ci.main(
                    [
                        "--profile",
                        "fast",
                        "--candidate-sha",
                        merge_sha,
                        "--source-sha",
                        source_sha,
                        "--manifest",
                        str(manifest),
                    ]
                )
                run.assert_not_called()
            evidence = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(result, 1)
        self.assertEqual(evidence["reason"], "candidate_source_mismatch")


if __name__ == "__main__":
    unittest.main()


class LauncherResolutionTests(unittest.TestCase):
    """A present tool must never be reported as unavailable (#621).

    ``shutil.which`` honours PATHEXT and resolves ``npm`` to ``npm.CMD``;
    ``CreateProcess`` -- what ``subprocess`` uses without ``shell=True`` --
    only ever appends ``.exe``. Passing the bare name therefore raised
    ``FileNotFoundError`` on Windows for an installed, working npm, and the
    runner reported "required executable unavailable". Every web check was
    unqualifiable on Windows for that reason alone, so the profile could
    never reach ``qualified`` on a developer machine.
    """

    def setUp(self) -> None:
        self.module = _load_ci_local_module()

    def test_a_bare_launcher_is_resolved_to_an_absolute_path(self) -> None:
        step = self.module.Step("probe", ["npm", "ci"])
        with mock.patch.object(
            self.module.shutil, "which", return_value=r"C:\tools\npm.CMD"
        ):
            argv = self.module._launch_argv(step)
        self.assertEqual(argv, [r"C:\tools\npm.CMD", "ci"])

    def test_arguments_after_the_launcher_are_untouched(self) -> None:
        step = self.module.Step("probe", ["npm", "run", "test:unit"])
        with mock.patch.object(
            self.module.shutil, "which", return_value="/usr/bin/npm"
        ):
            argv = self.module._launch_argv(step)
        self.assertEqual(argv[1:], ["run", "test:unit"])

    def test_an_unresolvable_launcher_is_left_alone_to_fail_honestly(self) -> None:
        """Absence must still reach the FileNotFoundError path and be typed as
        unavailable -- resolution must not mask a genuinely missing tool."""
        step = self.module.Step("probe", ["definitely-not-a-real-tool"])
        with mock.patch.object(self.module.shutil, "which", return_value=None):
            argv = self.module._launch_argv(step)
        self.assertEqual(argv, ["definitely-not-a-real-tool"])

    def test_an_empty_argv_does_not_raise(self) -> None:
        self.assertEqual(self.module._launch_argv(self.module.Step("probe", [])), [])

    def test_every_declared_web_launcher_resolves_on_this_machine(self) -> None:
        """The real regression: with node/npm installed, no web step may be
        reported unavailable. Skips only when the tools genuinely are absent."""
        for step in self.module.WEB_STEPS:
            with self.subTest(step=step.name):
                if self.module._missing_executables(step):
                    self.skipTest(f"{step.name} tooling not installed here")
                argv = self.module._launch_argv(step)
                self.assertTrue(
                    os.path.isabs(argv[0]),
                    f"{step.name} launcher {argv[0]!r} is not an absolute path",
                )
