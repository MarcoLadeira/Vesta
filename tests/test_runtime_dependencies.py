from __future__ import annotations

import builtins
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from _helpers import isolated_home
from opaihub import loader


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_ENV = {
    "MOONSHOT_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
}
EXTERNAL_STATE_ENV = PROVIDER_ENV | {
    "OPAI_HUB_ROOT",
    "LOCAL_MODEL_URL",
    "LOCAL_MODEL_NAME",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
}


def _load_smoke_module():
    path = ROOT / "scripts" / "smoke-install.py"
    spec = importlib.util.spec_from_file_location("opai_smoke_install", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load smoke-install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeDependencyMetadataTests(unittest.TestCase):
    def test_packaged_skill_registry_stays_in_lockstep_with_the_source_registry(self):
        source_root = ROOT / "hub" / "skills"
        package_root = ROOT / "opaihub" / "data" / "hub" / "skills"
        source_files = {
            path.relative_to(source_root)
            for path in source_root.rglob("*")
            if path.is_file()
        }
        package_files = {
            path.relative_to(package_root)
            for path in package_root.rglob("*")
            if path.is_file()
        }

        self.assertEqual(source_files, package_files)
        for relative_path in sorted(source_files):
            with self.subTest(path=relative_path):
                self.assertEqual(
                    (source_root / relative_path).read_text(encoding="utf-8"),
                    (package_root / relative_path).read_text(encoding="utf-8"),
                )

    def test_pyyaml_is_a_bounded_core_runtime_dependency(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('dependencies = ["PyYAML>=6.0.2,<7"]', pyproject)

    def test_pytest_collection_warnings_are_errors(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn(
            'filterwarnings = ["error::pytest.PytestCollectionWarning"]', pyproject
        )

    def test_production_test_loop_module_is_not_collected_by_pytest(self):
        import opaihub.test_loop as test_loop

        self.assertIs(test_loop.__test__, False)
        self.assertIs(test_loop.TestLoop.__test__, False)

    def test_active_installers_never_bypass_core_dependencies(self):
        installers = [
            "install.ps1",
            "install.sh",
            "CONTRIBUTING.md",
            "hub/install/README.md",
            "hub/install/manifest.json",
            "hub/docs/INSTALL.md",
            "opaihub/data/hub/install/README.md",
            "opaihub/data/hub/install/manifest.json",
            "opaihub/data/hub/docs/INSTALL.md",
        ]

        for relative in installers:
            with self.subTest(path=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("pip install -e . --no-deps", content)
                self.assertNotIn('pip install -e "$ROOT" --no-deps', content)
                self.assertNotIn('pip install -e "$Root" --no-deps', content)


class RegistryLoaderContractTests(unittest.TestCase):
    def test_missing_yaml_dependency_is_actionable_and_names_the_registry(self):
        original_import = builtins.__import__

        def reject_yaml(name, *args, **kwargs):
            if name == "yaml":
                raise ModuleNotFoundError("No module named 'yaml'")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tools.yaml"
            path.write_text("tools:\n  - id: ruff\n", encoding="utf-8")
            with mock.patch("builtins.__import__", side_effect=reject_yaml):
                with self.assertRaisesRegex(
                    RuntimeError, r"tools\.yaml.*PyYAML.*pip install opai"
                ):
                    loader.load_registry(path)

    def test_malformed_yaml_error_names_the_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.yaml"
            path.write_text("tools: [unterminated", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, r"broken\.yaml.*malformed"):
                loader.load_registry(path)

    def test_non_utf8_registry_error_names_the_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binary.yaml"
            path.write_bytes(b"tools:\n  - id: \xff\n")

            with self.assertRaisesRegex(RuntimeError, r"binary\.yaml.*UTF-8"):
                loader.load_registry(path)

    def test_valid_yaml_not_just_json_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tools.yaml"
            path.write_text("tools:\n  - id: ruff\n", encoding="utf-8")

            self.assertEqual(loader.load_registry(path), {"tools": [{"id": "ruff"}]})


class SmokeInstallContractTests(unittest.TestCase):
    def test_wheel_build_collects_runtime_dependency_wheels(self):
        smoke = _load_smoke_module()
        command = smoke.wheel_build_command(
            Path("python"), Path("repo"), Path("wheelhouse")
        )

        self.assertNotIn("--no-deps", command)
        self.assertEqual(command[-2:], ["-w", str(Path("wheelhouse"))])

    def test_smoke_commands_cover_every_clean_install_surface(self):
        smoke = _load_smoke_module()
        commands = smoke.required_smoke_commands(Path("python"))

        self.assertEqual(
            commands,
            [
                ["python", "-m", "opai", "--help"],
                ["python", "-m", "opai", "doctor"],
                ["python", "-m", "opaihub", "validate"],
                ["python", "-m", "opai", "gui", "--once"],
            ],
        )

    def test_smoke_environment_uses_disposable_home_and_no_provider_credentials(self):
        smoke = _load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            base = {name: f"hostile-{name}" for name in EXTERNAL_STATE_ENV}
            base["PATH"] = os.pathsep.join(["user-tools", "system-tools"])

            child = smoke.isolated_environment(
                home, base=base, executable_dir=Path("venv-bin")
            )

        self.assertEqual(child["HOME"], str(home))
        self.assertEqual(child["USERPROFILE"], str(home))
        self.assertTrue(child["PATH"].split(os.pathsep)[0].endswith("venv-bin"))
        self.assertNotIn("user-tools", child["PATH"])
        self.assertTrue(EXTERNAL_STATE_ENV.isdisjoint(child))

    def test_smoke_project_is_created_beneath_the_disposable_home(self):
        smoke = _load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            project = smoke.prepare_smoke_project(home)

            self.assertEqual(project.parent, home)
            self.assertTrue((project / "pyproject.toml").is_file())

    def test_smoke_checks_the_packaged_provider_catalog(self):
        smoke = _load_smoke_module()
        command = smoke.provider_catalog_smoke_command(Path(sys.executable))

        self.assertEqual(command[:3], [sys.executable, "-I", "-c"])
        self.assertIn("provider_catalog.catalog_bytes()", command[3])
        self.assertIn("provider_catalog.all_catalog_records()", command[3])

    def test_catalog_check_runs_before_required_smoke_commands(self):
        smoke = _load_smoke_module()
        python = Path("wheel-python")
        project = Path("outside-project")
        environment = {"PYTHONPATH": "host-checkout", "PYTHONHOME": "host-python"}

        with mock.patch.object(smoke, "run") as run:
            smoke.run_post_install_smoke_checks(python, project, environment)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], smoke.provider_catalog_smoke_command(python))
        self.assertEqual(commands[1:], smoke.required_smoke_commands(python))
        self.assertEqual(run.call_args_list[0].args[1], project)
        self.assertIs(run.call_args_list[0].kwargs["env"], environment)


class HermeticTestEnvironmentTests(unittest.TestCase):
    def test_isolated_home_scrubs_provider_state_and_restores_it_afterwards(self):
        hostile = {name: f"hostile-{name}" for name in PROVIDER_ENV}
        with mock.patch.dict(os.environ, hostile, clear=False):
            with isolated_home():
                self.assertTrue(PROVIDER_ENV.isdisjoint(os.environ))
            for name, value in hostile.items():
                self.assertEqual(os.environ[name], value)


class WorkflowContractTests(unittest.TestCase):
    def test_ci_has_scheduled_three_platform_native_qualification(self):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        native = workflow["jobs"]["scheduled-native"]
        operating_systems = set(native["strategy"]["matrix"]["os"])

        self.assertEqual(
            operating_systems, {"windows-latest", "ubuntu-latest", "macos-latest"}
        )
        command_text = str(native)
        self.assertIn("--profile native", command_text)

    def test_hosted_workflow_automatically_runs_the_required_pr_and_main_gates(self):
        source = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        workflow = yaml.safe_load(source)

        self.assertIn("pull_request:\n    branches: [main]", source)
        self.assertIn("push:\n    branches: [main]", source)
        self.assertIn("schedule:", source)
        self.assertIn("mandatory-python", workflow["jobs"])
        self.assertIn("mandatory-hostile-environment", workflow["jobs"])
        self.assertIn("web-test", workflow["jobs"])
        self.assertIn("--profile fast", str(workflow["jobs"]["mandatory-python"]))
        self.assertEqual(
            workflow["jobs"]["mandatory-python"]["runs-on"], "ubuntu-latest"
        )
        self.assertIn("persist-credentials: false", source)
        self.assertNotIn("actions/checkout@v4", source)

    def test_self_hosted_workflow_never_runs_untrusted_pull_requests(self):
        source = (ROOT / ".github" / "workflows" / "ci-selfhosted.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("push:\n    branches: [main]", source)
        self.assertNotIn("pull_request:", source)
        self.assertIn("--profile fast", source)

    def test_required_check_manifest_matches_the_hosted_workflow_and_governance_docs(
        self,
    ):
        manifest = json.loads(
            (ROOT / ".github" / "required-checks.json").read_text(encoding="utf-8")
        )
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        governance = (ROOT / "docs" / "CI_QUALIFICATION.md").read_text(encoding="utf-8")

        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["protected_branch"], "main")
        self.assertEqual(
            manifest["required_checks"][0], "Required - Python quality (3.13)"
        )
        for check in manifest["required_checks"]:
            with self.subTest(check=check):
                self.assertIn(check, governance)
        self.assertIn(
            "Required - Python quality (${{ matrix.python-version }})", workflow
        )
        self.assertIn("Required - hostile-environment Python suites", workflow)
        self.assertIn("Required - web UI security and E2E", workflow)

    def test_ci_trust_boundary_files_are_code_owned(self):
        codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")

        for path in (
            "/.github/workflows/",
            "/.github/required-checks.json",
            "/scripts/ci_local.py",
            "/requirements-ci.txt",
            "/docs/CI_QUALIFICATION.md",
        ):
            with self.subTest(path=path):
                self.assertIn(path, codeowners)
        self.assertIn("@MarcoLadeira", codeowners)

    def test_qualification_workflow_actions_are_pinned_to_commit_shas(self):
        for filename in ("ci.yml", "ci-selfhosted.yml", "release-preflight.yml"):
            with self.subTest(workflow=filename):
                source = (ROOT / ".github" / "workflows" / filename).read_text(
                    encoding="utf-8"
                )
                actions = re.findall(r"uses:\s+[^\s@]+@([^\s#]+)", source)
                self.assertTrue(actions)
                self.assertTrue(
                    all(re.fullmatch(r"[0-9a-f]{40}", action) for action in actions),
                    actions,
                )

    def test_ci_runs_both_python_harnesses_under_hostile_provider_state(self):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        job = workflow["jobs"]["mandatory-hostile-environment"]
        command_text = str(job)

        for name in PROVIDER_ENV - {"GH_TOKEN", "GITHUB_TOKEN"}:
            self.assertIn(name, job["env"])
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertNotIn("GH_TOKEN", job["env"])
        self.assertNotIn("GITHUB_TOKEN", job["env"])
        self.assertIn("unittest discover -s tests", command_text)
        self.assertIn("pytest tests", command_text)
        self.assertIn("fixtures/hostile_keyring", command_text)


def test_pytest_starts_without_live_provider_credentials_or_keyring():
    from opaihub import credentials

    assert PROVIDER_ENV.isdisjoint(os.environ)
    assert credentials._default_backend() is None


if __name__ == "__main__":
    unittest.main()
