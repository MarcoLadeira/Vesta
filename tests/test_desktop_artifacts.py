from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
