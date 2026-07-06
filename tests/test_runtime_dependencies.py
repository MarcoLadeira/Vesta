from __future__ import annotations

import builtins
import importlib.util
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import yaml

from _helpers import isolated_home
from opaihub import loader


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_ENV = {
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
    def test_pyyaml_is_a_bounded_core_runtime_dependency(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]
        dependencies = project["dependencies"]

        self.assertIn("PyYAML>=6.0.2,<7", dependencies)

    def test_pytest_collection_warnings_are_errors(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        warnings = config["tool"]["pytest"]["ini_options"]["filterwarnings"]

        self.assertIn("error::pytest.PytestCollectionWarning", warnings)

    def test_production_test_loop_is_not_a_pytest_test_class(self):
        from opaihub.test_loop import TestLoop

        self.assertIs(TestLoop.__test__, False)

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


class HermeticTestEnvironmentTests(unittest.TestCase):
    def test_isolated_home_scrubs_provider_state_and_restores_it_afterwards(self):
        hostile = {name: f"hostile-{name}" for name in PROVIDER_ENV}
        with mock.patch.dict(os.environ, hostile, clear=False):
            with isolated_home():
                self.assertTrue(PROVIDER_ENV.isdisjoint(os.environ))
            for name, value in hostile.items():
                self.assertEqual(os.environ[name], value)


class WorkflowContractTests(unittest.TestCase):
    def test_ci_has_three_platform_clean_install_matrix(self):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        clean_install = workflow["jobs"]["clean-install"]
        operating_systems = set(clean_install["strategy"]["matrix"]["os"])

        self.assertEqual(
            operating_systems, {"windows-latest", "ubuntu-latest", "macos-latest"}
        )
        command_text = str(clean_install)
        self.assertIn("scripts/smoke-install.py", command_text)

    def test_ci_runs_both_python_harnesses_under_hostile_provider_state(self):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        job = workflow["jobs"]["hermetic-tests"]
        command_text = str(job)

        for name in PROVIDER_ENV:
            self.assertIn(name, job["env"])
        self.assertIn("unittest discover -s tests", command_text)
        self.assertIn("pytest tests", command_text)
        self.assertIn("fixtures/hostile_keyring", command_text)


def test_pytest_starts_without_live_provider_credentials_or_keyring():
    from opaihub import credentials

    assert PROVIDER_ENV.isdisjoint(os.environ)
    assert credentials._default_backend() is None


if __name__ == "__main__":
    unittest.main()
