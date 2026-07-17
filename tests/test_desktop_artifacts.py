from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

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

    def test_internal_git_uses_the_resolved_absolute_executable(self):
        from opaihub import desktop_artifacts

        executable = str((Path("C:/tools/git.exe")).resolve())
        completed = CompletedProcess([], 0, stdout="a" * 40 + "\n", stderr="")
        with (
            patch("shutil.which", return_value=executable),
            patch.object(
                desktop_artifacts.subprocess, "run", return_value=completed
            ) as run,
        ):
            result = desktop_artifacts._git(Path("C:/repo"), ["rev-parse", "HEAD"])

        self.assertEqual(result, "a" * 40)
        self.assertEqual(run.call_args.args[0][0], executable)

    def test_artifact_smoke_environment_strips_signing_credentials(self):
        from opaihub.desktop_artifacts import isolated_artifact_environment

        secret_names = (
            "WINDOWS_PFX_BASE64",
            "WINDOWS_PFX_PASSWORD",
            "APPLE_DEVELOPER_ID",
            "APPLE_SIGNING_CERTIFICATE_BASE64",
            "APPLE_SIGNING_CERTIFICATE_PASSWORD",
            "APPLE_NOTARY_PROFILE",
        )
        environment = isolated_artifact_environment(
            Path("C:/artifact-smoke-home"),
            base={name: "must-not-reach-artifact" for name in secret_names},
        )

        for name in secret_names:
            self.assertNotIn(name, environment)

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

    def test_build_metadata_is_preserved_and_bound_by_bundle_evidence(self):
        assert release_ref is not None
        assert verify_bundle is not None
        assert write_bundle_evidence is not None
        build_metadata = {
            "python": {"implementation": "CPython", "version": "3.13.5"},
            "dependencies": {"Nuitka": "4.0", "PySide6": "6.11.1"},
            "lock": {"name": "desktop-build.windows.lock", "sha256": "a" * 64},
        }
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows"
            executable = bundle / "cli" / "opai.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"original executable")
            reference = release_ref(
                bundle,
                run_git=_git_with(commit="e" * 40, tag="v0.2.0a2"),
            )
            write_bundle_evidence(
                bundle,
                reference,
                platform="windows",
                build_metadata=build_metadata,
            )
            provenance_path = bundle / "provenance.json"
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            recorded_build = json.loads(json.dumps(provenance["build"]))
            provenance["build"]["python"]["version"] = "tampered"
            provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
            verified = verify_bundle(bundle)

        self.assertEqual(recorded_build, build_metadata)
        self.assertFalse(verified["ok"])
        self.assertIn("hash mismatch", verified["problems"])

    def test_finalizer_preserves_build_metadata_from_unsigned_evidence(self):
        assert release_ref is not None
        assert write_bundle_evidence is not None
        root = Path(__file__).resolve().parents[1]
        script_path = root / "scripts" / "finalize_desktop_artifact.py"
        spec = spec_from_file_location("opai_artifact_finalizer_test", script_path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        build_metadata = {"lock": {"name": "desktop-build.windows.lock"}}
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows"
            executable = bundle / "cli" / "opai.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"original executable")
            reference = release_ref(
                bundle,
                run_git=_git_with(commit="f" * 40, tag="v0.2.0a2"),
            )
            write_bundle_evidence(
                bundle,
                reference,
                platform="windows",
                build_metadata=build_metadata,
            )
            _release, _platform, preserved = module._release_ref_from_bundle(bundle)

        self.assertEqual(preserved, build_metadata)

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
            "settings.js",
            "onboarding.js",
        ]:
            self.assertIn(f"=opai/assets/web/{asset}", joined_gui_args)
        self.assertIn("=opai/assets/fonts", joined_gui_args)
        self.assertIn("=opai/assets/web/icons", joined_gui_args)
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

    def test_signed_bundle_requires_a_live_platform_signature_verifier(self):
        assert release_ref is not None
        assert verify_bundle is not None
        assert write_bundle_evidence is not None
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows"
            executable = bundle / "cli" / "opai.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"unsigned executable")
            reference = release_ref(
                bundle,
                run_git=_git_with(commit="4" * 40, tag="v0.2.0a2"),
            )
            signing_evidence = {
                "verified": True,
                "tool": "Authenticode",
                "log_sha256": "d" * 64,
            }
            write_bundle_evidence(
                bundle,
                reference,
                platform="windows",
                signing_status="signed",
                signing_evidence=signing_evidence,
            )
            unverified = verify_bundle(bundle)
            verified = verify_bundle(
                bundle,
                signature_verifier=lambda _bundle, _platform: [],
            )

        self.assertFalse(unverified["ok"])
        self.assertFalse(unverified["production_ready"])
        self.assertIn(
            "platform signature verification is required", unverified["problems"]
        )
        self.assertTrue(verified["ok"])
        self.assertTrue(verified["platform_signature_verified"])
        self.assertFalse(verified["production_ready"])
        self.assertTrue(verified["outer_release_authentication_required"])

    def test_native_windows_signature_verifier_checks_all_code_files(self):
        from opaihub import desktop_artifacts

        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows"
            targets = (
                bundle / "gui" / "OPai.exe",
                bundle / "cli" / "opai.exe",
                bundle / "cli" / "runtime.dll",
                bundle / "cli" / "module.pyd",
            )
            for target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"signed test binary")
            executable = str((Path("C:/tools/powershell.exe")).resolve())
            completed = CompletedProcess([], 0, stdout="", stderr="")
            with (
                patch("opaihub.desktop_artifacts.os.name", "nt"),
                patch(
                    "opaihub.desktop_artifacts.shutil.which", return_value=executable
                ),
                patch.object(
                    desktop_artifacts.subprocess, "run", return_value=completed
                ) as run,
            ):
                problems = desktop_artifacts.native_platform_signature_problems(
                    bundle,
                    "windows",
                    windows_signer_thumbprint="A" * 40,
                )

        self.assertEqual(problems, [])
        command = run.call_args.args[0]
        self.assertEqual(command[0], executable)
        self.assertIn("A" * 40, command)
        self.assertIn("Thumbprint", command[4])
        for target in targets:
            self.assertIn(str(target), command)

    def test_native_windows_signature_verifier_requires_a_pinned_identity(self):
        from opaihub import desktop_artifacts

        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "OPai-v0.2.0a2-windows"
            target = bundle / "cli" / "opai.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"signed test binary")
            with patch("opaihub.desktop_artifacts.os.name", "nt"):
                problems = desktop_artifacts.native_platform_signature_problems(
                    bundle, "windows"
                )

        self.assertEqual(problems, ["expected Windows signer thumbprint is required"])

    def test_artifact_smoke_uses_the_native_signature_verifier(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts" / "smoke_desktop_artifacts.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("native_platform_signature_problems", source)
        self.assertIn("partial(", source)
        self.assertIn("signature_verifier=signature_verifier", source)
        self.assertIn("--windows-signer-thumbprint", source)
        self.assertIn("--macos-team-id", source)

    def test_windows_webengine_helper_uses_an_absolute_tasklist_executable(self):
        root = Path(__file__).resolve().parents[1]
        script_path = root / "scripts" / "smoke_desktop_artifacts.py"
        spec = spec_from_file_location("opai_artifact_smoke_test", script_path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        completed = CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(module.subprocess, "run", return_value=completed) as run:
            self.assertEqual(module._webengine_helpers(), set())

        command = run.call_args.args[0]
        self.assertTrue(Path(command[0]).is_absolute())
        self.assertEqual(Path(command[0]).name.casefold(), "tasklist.exe")

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
        matrix = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
        self.assertEqual(
            {entry["os"] for entry in matrix}, {"windows-latest", "macos-latest"}
        )

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
        for requirement in [
            "portable",
            "upgrade",
            "uninstall",
            "rollback",
            "unsigned",
            "opai-production-signing",
            "root of trust",
            "attestation",
            "gh attestation verify",
            "signer thumbprint",
        ]:
            self.assertIn(requirement, runbook)

    def test_production_workflow_scopes_credentials_to_a_protected_sign_job(self):
        root = Path(__file__).resolve().parents[1]
        workflow_path = root / ".github" / "workflows" / "desktop-artifacts.yml"
        workflow = yaml.load(
            workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )
        build = workflow["jobs"]["build"]
        sign = workflow["jobs"].get("sign")

        self.assertIsNotNone(sign, "production signing must use a separate job")
        assert sign is not None
        self.assertEqual(sign["needs"], "build")
        self.assertEqual(sign["environment"]["name"], "opai-production-signing")
        self.assertEqual(sign["environment"]["deployment"], "false")
        self.assertIn("inputs.release_channel == 'production'", sign["if"])
        self.assertIn("github.ref == 'refs/heads/main'", sign["if"])
        self.assertNotIn("secrets.", str(build.get("env", {})))
        self.assertIn("WINDOWS_PFX_BASE64", str(sign["steps"]))
        self.assertIn("APPLE_SIGNING_CERTIFICATE_BASE64", str(sign["steps"]))

    def test_macos_production_workflow_signs_and_verifies_the_cli(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / ".github" / "workflows" / "desktop-artifacts.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('CLI="$BUNDLE/cli/opai"', source)
        self.assertIn(
            '/usr/bin/codesign --force --options runtime --timestamp --keychain "$KEYCHAIN" --sign "$APPLE_DEVELOPER_ID" "$CLI"',
            source,
        )
        self.assertIn('/usr/bin/codesign --verify --strict --verbose=2 "$CLI"', source)
        self.assertIn(
            '/usr/sbin/spctl --assess --type execute --verbose=4 "$CLI"', source
        )
        self.assertIn(
            '/usr/bin/base64 -D > "$CERTIFICATE"',
            source,
        )
        self.assertNotIn("/usr/bin/base64 --decode", source)

    def test_production_workflow_cleans_signing_material_before_artifact_smoke(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / ".github" / "workflows" / "desktop-artifacts.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("finally {", source)
        self.assertIn(
            "Remove-Item -LiteralPath $certificate -Force -ErrorAction SilentlyContinue",
            source,
        )
        self.assertIn("trap cleanup EXIT", source)
        self.assertIn('security delete-keychain "$KEYCHAIN"', source)
        self.assertIn('rm -f "$CERTIFICATE"', source)
        self.assertIn('--keychain "$KEYCHAIN"', source)
        self.assertNotIn('security list-keychain -d user -s "$KEYCHAIN"', source)
        self.assertLess(
            source.index("trap cleanup EXIT"),
            source.index("Smoke signed native artifact outside checkout"),
        )

    def test_production_workflow_pins_supply_chain_trust_and_attests_archive(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / ".github" / "workflows" / "desktop-artifacts.yml").read_text(
            encoding="utf-8"
        )
        workflow = yaml.load(source, Loader=yaml.BaseLoader)
        sign = workflow["jobs"]["sign"]
        attest = workflow["jobs"].get("attest")

        for action in (
            "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5",
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "actions/attest@a1948c3f048ba23858d222213b7c278aabede763",
        ):
            self.assertIn(f"uses: {action}", source)
        self.assertNotRegex(source, r"uses:\\s+actions/[^@]+@v\\d")
        self.assertIn('test "$(git cat-file -t "$RELEASE_TAG")" = "tag"', source)
        self.assertIn(
            'git merge-base --is-ancestor "$EXPECTED_COMMIT" "$GITHUB_SHA"', source
        )
        self.assertIsNotNone(attest, "attestation must be a separate job")
        assert attest is not None
        self.assertEqual(attest["permissions"]["attestations"], "write")
        self.assertEqual(attest["permissions"]["id-token"], "write")
        self.assertEqual(sign["steps"][0]["with"]["fetch-depth"], "0")
        self.assertIn("Attest signed production archive", source)

    def test_production_workflow_treats_release_tags_as_data_not_shell_code(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / ".github" / "workflows" / "desktop-artifacts.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('[[ "$RELEASE_TAG" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+', source)
        self.assertNotIn(
            'powershell -NoProfile -Command "Compress-Archive',
            source,
        )
        self.assertIn("Compress-Archive -LiteralPath $env:BUNDLE", source)

    def test_production_workflow_separates_signing_smoke_and_attestation(self):
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load(
            (root / ".github" / "workflows" / "desktop-artifacts.yml").read_text(
                encoding="utf-8"
            ),
            Loader=yaml.BaseLoader,
        )
        sign = workflow["jobs"]["sign"]
        smoke = workflow["jobs"].get("smoke")
        attest = workflow["jobs"].get("attest")

        self.assertEqual(sign["steps"][0]["with"]["persist-credentials"], "false")
        self.assertNotIn("id-token", sign["permissions"])
        self.assertNotIn("attestations", sign["permissions"])
        self.assertIsNotNone(smoke, "artifact smoke must use an unprivileged job")
        self.assertIsNotNone(attest, "archive attestation must use a separate job")
        assert smoke is not None
        assert attest is not None
        self.assertEqual(smoke["needs"], "sign")
        self.assertEqual(attest["needs"], ["sign", "smoke"])
        self.assertNotIn("id-token", smoke.get("permissions", {}))
        self.assertNotIn("attestations", smoke.get("permissions", {}))
        self.assertIn("Smoke signed native artifact", str(smoke["steps"]))
        self.assertNotIn("Smoke signed native artifact", str(sign["steps"]))
        self.assertIn("Attest signed production archive", str(attest["steps"]))
        self.assertNotIn("Attest signed production archive", str(sign["steps"]))

    def test_release_runbook_pins_attestation_to_the_release_workflow(self):
        root = Path(__file__).resolve().parents[1]
        runbook = (root / "docs" / "DESKTOP_ARTIFACT_RELEASE.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "--signer-workflow MarcoLadeira/OPai/.github/workflows/desktop-artifacts.yml",
            runbook,
        )
        self.assertIn("--source-ref refs/heads/main", runbook)
        self.assertIn("--deny-self-hosted-runners", runbook)

    def test_production_workflow_provisions_notarization_and_pinned_signer_identity(
        self,
    ):
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load(
            (root / ".github" / "workflows" / "desktop-artifacts.yml").read_text(
                encoding="utf-8"
            ),
            Loader=yaml.BaseLoader,
        )
        source = str(workflow)
        smoke = workflow["jobs"]["smoke"]

        for requirement in (
            "APPLE_NOTARY_APPLE_ID",
            "APPLE_NOTARY_TEAM_ID",
            "APPLE_NOTARY_APP_SPECIFIC_PASSWORD",
            "notarytool store-credentials",
            '[[ "$APPLE_NOTARY_TEAM_ID" == "$APPLE_TEAM_ID" ]]',
            "EXPECTED_WINDOWS_SIGNER_THUMBPRINT",
            "SignerCertificate.Thumbprint",
            "APPLE_TEAM_ID",
            "TeamIdentifier=$APPLE_TEAM_ID",
            "opai-publisher-identity.json",
            '--windows-signer-thumbprint "$WINDOWS_SIGNER_THUMBPRINT"',
            '--macos-team-id "$MACOS_TEAM_ID"',
        ):
            self.assertIn(requirement, source)
        # PUBLISHER_IDENTITY is a runner-scoped path (outside the checkout).
        # runner.temp is not allowed in job-level env, so it is provisioned into
        # $GITHUB_ENV from $RUNNER_TEMP by the job's resolve-paths step.
        self.assertIn(
            "PUBLISHER_IDENTITY=$RUNNER_TEMP/opai-publisher-identity.json", source
        )
        self.assertNotIn("EXPECTED_WINDOWS_SIGNER_THUMBPRINT", smoke["env"])
        self.assertNotIn("EXPECTED_MACOS_TEAM_ID", smoke["env"])

    def test_workflow_uses_hash_locked_native_build_inputs(self):
        root = Path(__file__).resolve().parents[1]
        bootstrap_path = root / "requirements" / "desktop-build-bootstrap.lock"
        lock_path = root / "requirements" / "desktop-build.windows.lock"
        workflow = (root / ".github" / "workflows" / "desktop-artifacts.yml").read_text(
            encoding="utf-8"
        )

        self.assertTrue(
            bootstrap_path.is_file(), "native build bootstrap lock is required"
        )
        bootstrap = bootstrap_path.read_text(encoding="utf-8")
        for package in ("pip==", "setuptools==", "wheel=="):
            self.assertIn(package, bootstrap)
        self.assertIn("--hash=sha256:", bootstrap)
        self.assertTrue(lock_path.is_file(), "native build lock is required")
        lock = lock_path.read_text(encoding="utf-8")
        self.assertIn("pip==", lock)
        self.assertIn("setuptools==", lock)
        self.assertIn("--hash=sha256:", lock)
        self.assertIn("requirements/desktop-build-bootstrap.lock", workflow)
        matrix = yaml.load(workflow, Loader=yaml.BaseLoader)["jobs"]["build"][
            "strategy"
        ]["matrix"]["include"]
        lockfiles = {entry["os"]: entry["build_lock"] for entry in matrix}
        self.assertEqual(
            lockfiles,
            {
                "windows-latest": "requirements/desktop-build.windows.lock",
                "macos-latest": "requirements/desktop-build.macos.lock",
            },
        )
        self.assertIn('test -f "$DESKTOP_BUILD_LOCK"', workflow)
        self.assertIn("Missing native build lock", workflow)
        self.assertIn(' -r "$DESKTOP_BUILD_LOCK"', workflow)
        self.assertIn('--lock-file "$DESKTOP_BUILD_LOCK"', workflow)
        self.assertNotIn("requirements/desktop-build.lock", workflow)
        self.assertLess(
            workflow.index("requirements/desktop-build-bootstrap.lock"),
            workflow.index(' -r "$DESKTOP_BUILD_LOCK"'),
        )
        self.assertIn("--require-hashes", workflow)
        self.assertIn("--no-build-isolation", workflow)
        self.assertIn("--no-deps -e .", workflow)
        self.assertNotIn("pip install --upgrade pip", workflow)


if __name__ == "__main__":
    unittest.main()
