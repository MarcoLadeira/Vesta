from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
import json
from pathlib import Path

import yaml

try:
    from opaihub.desktop_artifacts import (
        ArtifactReleaseError,
        release_ref,
        verify_bundle,
        write_bundle_evidence,
    )
except ModuleNotFoundError:
    ArtifactReleaseError = None
    release_ref = None
    verify_bundle = None
    write_bundle_evidence = None


def _git_with(*, commit: str, tag: str | None):
    def run(args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return commit
        if args == ["describe", "--exact-match", "--tags", "HEAD"]:
            if tag is None:
                raise ArtifactReleaseError("HEAD is not exactly tagged")
            return tag
        raise AssertionError(f"unexpected git command: {args}")

    return run


class DesktopArtifactContractTests(unittest.TestCase):
    def test_release_contract_module_is_available(self):
        self.assertIsNotNone(ArtifactReleaseError)
        self.assertIsNotNone(release_ref)
        self.assertIsNotNone(write_bundle_evidence)
        self.assertIsNotNone(verify_bundle)

    def test_release_ref_requires_an_exact_v_tag(self):
        assert ArtifactReleaseError is not None
        assert release_ref is not None
        with self.assertRaises(ArtifactReleaseError):
            release_ref(
                Path("C:/repo"),
                run_git=_git_with(commit="a" * 40, tag=None),
            )
        with self.assertRaises(ArtifactReleaseError):
            release_ref(
                Path("C:/repo"),
                run_git=_git_with(commit="a" * 40, tag="alpha.2"),
            )

    def test_rehearsal_ref_is_explicitly_labelled_when_untagged(self):
        assert release_ref is not None
        reference = release_ref(
            Path("C:/repo"),
            allow_untagged=True,
            run_git=_git_with(commit="b" * 40, tag=None),
        )

        self.assertEqual(reference.tag, "untagged-rehearsal")
        self.assertEqual(reference.commit, "b" * 40)
        self.assertTrue(reference.rehearsal)

    def test_manifest_rejects_a_modified_distributable_file(self):
        assert release_ref is not None
        assert verify_bundle is not None
        assert write_bundle_evidence is not None
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows"
            executable = bundle / "cli" / "opai.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"original executable")
            reference = release_ref(
                bundle,
                run_git=_git_with(commit="c" * 40, tag="v0.2.0a2"),
            )
            write_bundle_evidence(bundle, reference, platform="windows")
            verified = verify_bundle(bundle)
            executable.write_bytes(b"tampered executable")
            modified = verify_bundle(bundle)

        self.assertTrue(verified["ok"])
        self.assertFalse(modified["ok"])
        self.assertIn("hash mismatch", modified["problems"])

    def test_unsigned_bundle_is_explicitly_prealpha(self):
        assert release_ref is not None
        assert verify_bundle is not None
        assert write_bundle_evidence is not None
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows"
            executable = bundle / "cli" / "opai.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"original executable")
            reference = release_ref(
                bundle,
                run_git=_git_with(commit="d" * 40, tag="v0.2.0a2"),
            )
            write_bundle_evidence(bundle, reference, platform="windows")
            verified = verify_bundle(bundle)

        self.assertEqual(verified["signing_status"], "unsigned-prealpha")
        self.assertTrue(verified["ok"])

    def test_deployment_specs_pin_tools_and_include_runtime_assets(self):
        from opaihub import desktop_artifacts

        root = Path(__file__).resolve().parents[1]
        self.assertTrue(
            hasattr(desktop_artifacts, "deployment_specs"),
            "desktop artifact deployment specs are required",
        )
        self.assertTrue(
            hasattr(desktop_artifacts, "load_build_pins"),
            "desktop build pins are required",
        )
        specs = desktop_artifacts.deployment_specs(root, root / "dist" / "desktop")
        pins = desktop_artifacts.load_build_pins(root)

        self.assertEqual(specs.gui.name, "OPai")
        self.assertEqual(specs.cli.name, "opai")
        self.assertEqual(specs.gui.tool, "pyside6-deploy")
        self.assertEqual(specs.cli.tool, "python -m nuitka")
        self.assertEqual(
            specs.gui.entrypoint, root / "scripts" / "desktop_gui_entry.py"
        )
        self.assertEqual(
            specs.cli.entrypoint, root / "scripts" / "desktop_cli_entry.py"
        )
        self.assertTrue(specs.gui.entrypoint.is_file())
        self.assertTrue(specs.cli.entrypoint.is_file())
        self.assertEqual(pins["PySide6"], "6.11.1")
        self.assertEqual(pins["Nuitka"], "4.0")
        self.assertIn("WebChannel", specs.gui.qt_modules)
        self.assertIn("WebEngineWidgets", specs.gui.qt_modules)
        joined_gui_args = "\n".join(specs.gui.extra_args)
        for asset in [
            "index.html",
            "app.js",
            "styles.css",
            "activity.js",
            "message-state.js",
        ]:
            self.assertIn(f"=opai/assets/web/{asset}", joined_gui_args)
        self.assertIn("=opai/assets/fonts", joined_gui_args)
        self.assertIn("=opai/assets/opai-icon.png", joined_gui_args)
        self.assertIn("=opai/assets/opai-mascot.png", joined_gui_args)
        self.assertIn("=opaihub/data", joined_gui_args)
        self.assertNotIn("__tests__", joined_gui_args)
        self.assertNotIn("\\", joined_gui_args)

    def test_build_and_smoke_commands_are_source_independent_and_explicit(self):
        from opaihub import desktop_artifacts

        required_helpers = (
            "build_commands",
            "render_pyside_deploy_spec",
            "smoke_commands",
            "isolated_artifact_environment",
            "scan_artifact_text",
        )
        self.assertTrue(
            all(hasattr(desktop_artifacts, name) for name in required_helpers),
            "artifact build and smoke helpers are required",
        )
        root = Path(__file__).resolve().parents[1]
        specs = desktop_artifacts.deployment_specs(root, root / "dist" / "desktop")
        build_python = Path("C:/artifact-venv/Scripts/python.exe")
        deploy_script = Path("C:/artifact-venv/Scripts/pyside6-deploy.exe")
        commands = desktop_artifacts.build_commands(
            specs,
            build_python=build_python,
            deploy_script=deploy_script,
            spec_dir=Path("C:/artifact-build/specs"),
        )
        gui_command, cli_command = commands
        gui_config = desktop_artifacts.render_pyside_deploy_spec(
            specs.gui, build_python=build_python
        )
        staged_gui_config = desktop_artifacts.render_pyside_deploy_spec(
            replace(
                specs.gui,
                entrypoint=Path("C:/artifact-build/staging/desktop_gui_entry.py"),
            ),
            build_python=build_python,
        )

        self.assertEqual(gui_command[:2], [str(build_python), str(deploy_script)])
        self.assertIn("--nuitka-version=4.0", gui_command)
        self.assertEqual(cli_command[:3], [str(build_python), "-m", "nuitka"])
        self.assertIn("--standalone", cli_command)
        self.assertNotIn("--assume-yes-for-downloads", cli_command)
        self.assertIn("[app]", gui_config)
        self.assertIn("packages = Nuitka==4.0", gui_config)
        self.assertIn("WebEngineWidgets", gui_config)
        self.assertIn(f"project_dir = {root.as_posix()}", staged_gui_config)

        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows-unsigned-prealpha"
            gui = bundle / "gui" / "OPai.exe"
            cli = bundle / "cli" / "opai.exe"
            gui.parent.mkdir(parents=True)
            cli.parent.mkdir(parents=True)
            gui.write_bytes(b"gui")
            cli.write_bytes(b"cli")
            reference = desktop_artifacts.ReleaseRef("v0.2.0a2", "e" * 40)
            desktop_artifacts.write_bundle_evidence(
                bundle, reference, platform="windows"
            )
            smoke_result = Path(tmp) / "home" / "smoke-result.json"
            smoke = desktop_artifacts.smoke_commands(
                bundle, Path(tmp) / "home", smoke_result
            )
            with self.assertRaises(desktop_artifacts.ArtifactReleaseError):
                desktop_artifacts.smoke_commands(
                    bundle, Path(tmp) / "home", bundle / "smoke-result.json"
                )
            environment = desktop_artifacts.isolated_artifact_environment(
                Path(tmp) / "home",
                {"PYTHONPATH": "C:/source", "GOOGLE_API_KEY": "not-for-smoke"},
            )
            suspicious = bundle / "cli" / "config.txt"
            suspicious.write_text(
                "token=sk-abcdefghijklmnopqrstuvwxyz", encoding="utf-8"
            )
            scan = desktop_artifacts.scan_artifact_text(bundle)

        self.assertEqual(smoke[0][0], str(cli))
        self.assertIn("--artifact-smoke", smoke[-1])
        self.assertIn(str(smoke_result), smoke[-1])
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("GOOGLE_API_KEY", environment)
        self.assertTrue(any(item["kind"] == "secret" for item in scan))

    def test_gui_cli_accepts_an_artifact_smoke_result_contract(self):
        from opai.cli import build_parser

        try:
            args = build_parser().parse_args(
                [
                    "gui",
                    "--artifact-smoke",
                    "--project",
                    "C:/fixture",
                    "--result",
                    "C:/smoke/result.json",
                ]
            )
        except SystemExit:
            args = None

        self.assertIsNotNone(args, "gui artifact-smoke arguments are required")
        assert args is not None
        self.assertTrue(args.artifact_smoke)
        self.assertEqual(args.result, "C:/smoke/result.json")

    def test_build_and_smoke_script_entry_points_exist(self):
        root = Path(__file__).resolve().parents[1]

        self.assertTrue((root / "scripts" / "build_desktop_artifacts.py").is_file())
        self.assertTrue((root / "scripts" / "smoke_desktop_artifacts.py").is_file())
        self.assertTrue((root / "scripts" / "finalize_desktop_artifact.py").is_file())

    def test_signed_status_requires_verified_signing_evidence(self):
        assert release_ref is not None
        assert write_bundle_evidence is not None
        assert ArtifactReleaseError is not None
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows"
            executable = bundle / "cli" / "opai.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"original executable")
            reference = release_ref(
                bundle,
                run_git=_git_with(commit="f" * 40, tag="v0.2.0a2"),
            )

            with self.assertRaises(ArtifactReleaseError):
                write_bundle_evidence(
                    bundle,
                    reference,
                    platform="windows",
                    signing_status="signed",
                )

    def test_bundle_rejects_tampered_signed_verification_metadata(self):
        assert release_ref is not None
        assert verify_bundle is not None
        assert write_bundle_evidence is not None
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows"
            executable = bundle / "cli" / "opai.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"original executable")
            reference = release_ref(
                bundle,
                run_git=_git_with(commit="1" * 40, tag="v0.2.0a2"),
            )
            write_bundle_evidence(
                bundle,
                reference,
                platform="windows",
                signing_status="signed",
                signing_evidence={
                    "verified": True,
                    "tool": "Authenticode",
                    "log_sha256": "a" * 64,
                },
            )
            signing_path = bundle / "signing-status.json"
            signing = json.loads(signing_path.read_text(encoding="utf-8"))
            signing["verification"]["verified"] = False
            signing_path.write_text(json.dumps(signing), encoding="utf-8")
            verified = verify_bundle(bundle)

        self.assertFalse(verified["ok"])
        self.assertIn("invalid signing status", verified["problems"])

    def test_bundle_rejects_inconsistent_signed_production_readiness(self):
        assert release_ref is not None
        assert verify_bundle is not None
        assert write_bundle_evidence is not None
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-macos"
            executable = bundle / "cli" / "opai"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"original executable")
            reference = release_ref(
                bundle,
                run_git=_git_with(commit="2" * 40, tag="v0.2.0a2"),
            )
            write_bundle_evidence(
                bundle,
                reference,
                platform="macos",
                signing_status="signed",
                signing_evidence={
                    "verified": True,
                    "tool": "codesign",
                    "log_sha256": "b" * 64,
                },
            )
            signing_path = bundle / "signing-status.json"
            signing = json.loads(signing_path.read_text(encoding="utf-8"))
            signing["production_ready"] = True
            signing_path.write_text(json.dumps(signing), encoding="utf-8")
            verified = verify_bundle(bundle)

        self.assertFalse(verified["ok"])
        self.assertIn("invalid signing status", verified["problems"])

    def test_checksum_covers_well_formed_signed_metadata(self):
        assert release_ref is not None
        assert verify_bundle is not None
        assert write_bundle_evidence is not None
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows"
            executable = bundle / "cli" / "opai.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"original executable")
            reference = release_ref(
                bundle,
                run_git=_git_with(commit="3" * 40, tag="v0.2.0a2"),
            )
            write_bundle_evidence(
                bundle,
                reference,
                platform="windows",
                signing_status="signed",
                signing_evidence={
                    "verified": True,
                    "tool": "Authenticode",
                    "log_sha256": "c" * 64,
                },
            )
            signing_path = bundle / "signing-status.json"
            signing = json.loads(signing_path.read_text(encoding="utf-8"))
            signing["verification"]["tool"] = "Different verifier"
            signing_path.write_text(json.dumps(signing), encoding="utf-8")
            verified = verify_bundle(bundle)

        self.assertFalse(verified["ok"])
        self.assertIn("hash mismatch", verified["problems"])

    def test_artifact_workflow_is_manual_tagged_and_cross_platform(self):
        root = Path(__file__).resolve().parents[1]
        workflow_path = root / ".github" / "workflows" / "desktop-artifacts.yml"
        runbook_path = root / "docs" / "DESKTOP_ARTIFACT_RELEASE.md"
        self.assertTrue(
            workflow_path.is_file(), "desktop artifact workflow is required"
        )
        self.assertTrue(runbook_path.is_file(), "desktop artifact runbook is required")

        workflow = yaml.load(
            workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        inputs = workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["release_tag"]["required"], "true")
        self.assertIn("unsigned-prealpha", inputs["release_channel"]["options"])
        self.assertIn("production", inputs["release_channel"]["options"])
        matrix = workflow["jobs"]["build"]["strategy"]["matrix"]["os"]
        self.assertEqual(set(matrix), {"windows-latest", "macos-latest"})

        source = workflow_path.read_text(encoding="utf-8")
        for requirement in [
            "git describe --exact-match --tags HEAD",
            "scripts/build_desktop_artifacts.py",
            "scripts/smoke_desktop_artifacts.py",
            "Get-AuthenticodeSignature",
            "codesign --verify",
            "xcrun stapler validate",
            "allow_unsigned_prealpha",
        ]:
            self.assertIn(requirement, source)
        runbook = runbook_path.read_text(encoding="utf-8").casefold()
        for requirement in ["portable", "upgrade", "uninstall", "rollback", "unsigned"]:
            self.assertIn(requirement, runbook)


if __name__ == "__main__":
    unittest.main()
