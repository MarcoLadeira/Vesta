from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _release_module():
    return importlib.import_module("opai.release_identity")


def test_project_version_is_the_canonical_application_version() -> None:
    release = _release_module().read_project_release(ROOT / "pyproject.toml")

    assert release.application_version == "0.2.1a1"
    assert release.release_channel == "alpha"
    assert release.release_stage == "alpha.1"
    assert release.display_name == "OPai 0.2.1 Alpha.1"
    assert release.published_tag == "v0.2.1a1"


def test_generated_runtime_projection_matches_the_canonical_version() -> None:
    generated = importlib.import_module("opai._generated_release")
    release = _release_module().read_project_release(ROOT / "pyproject.toml")

    assert generated.APPLICATION_VERSION == release.application_version
    assert generated.RELEASE_CHANNEL == release.release_channel
    assert generated.RELEASE_STAGE == release.release_stage
    assert generated.DISPLAY_NAME == release.display_name
    assert generated.PUBLISHED_TAG == release.published_tag


def test_package_version_exports_are_generated_projections() -> None:
    opai = importlib.import_module("opai")
    opaihub = importlib.import_module("opaihub")
    generated = importlib.import_module("opai._generated_release")

    assert opai.__version__ == generated.APPLICATION_VERSION
    assert opai.__release_stage__ == generated.RELEASE_STAGE
    assert opaihub.__version__ == generated.APPLICATION_VERSION


def test_explicit_packaged_identity_ignores_neighbouring_checkout(
    tmp_path: Path,
) -> None:
    embedded = tmp_path / "package" / "release-identity.json"
    embedded.parent.mkdir()
    embedded.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.2.1a1",
                "build_id": "a" * 40,
                "channel": "alpha",
                "platform": "windows",
                "architecture": "x86_64",
                "install_type": "windows_msix",
            }
        ),
        encoding="utf-8",
    )
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    (checkout / "pyproject.toml").write_text(
        '[project]\nname = "opai"\nversion = "9.9.9"\n', encoding="utf-8"
    )

    identity = _release_module().load_release_identity(
        identity_paths=(embedded,),
        distribution_version=None,
        source_root=checkout,
    )

    assert identity.application_version == "0.2.1a1"
    assert identity.build_id == "a" * 40
    assert identity.release_channel == "alpha"
    assert identity.metadata_source == str(embedded.resolve())


def test_packaged_runtime_discovers_only_its_exact_windows_bundle_identity(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "package"
    executable = bundle / "cli" / "opai.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"native")
    embedded = bundle / "release-identity.json"
    embedded.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "application_version": "0.2.1a1",
                "build_id": "d" * 40,
                "release_channel": "alpha",
                "platform": "windows",
                "architecture": "x86_64",
                "install_type": "windows_msix",
            }
        ),
        encoding="utf-8",
    )

    unrelated = tmp_path / "release-identity.json"
    unrelated.write_text('{"schema_version": 999}', encoding="utf-8")
    release_module = _release_module()
    paths = release_module.packaged_metadata_paths(
        "release-identity.json", executable_path=executable
    )
    identity = release_module.load_release_identity(
        identity_paths=paths,
        distribution_version=None,
        source_root=bundle,
    )

    assert paths == (embedded,)
    assert unrelated not in paths
    assert identity.build_id == "d" * 40
    assert identity.install_type == "windows_msix"


def test_packaged_metadata_discovery_does_not_fall_back_to_an_ancestor(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "parent" / "bundle"
    executable = bundle / "gui" / "opai-gui.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"native")
    ancestor = tmp_path / "parent" / "release-identity.json"
    ancestor.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "application_version": "0.2.1a1",
                "build_id": "e" * 40,
                "release_channel": "alpha",
            }
        ),
        encoding="utf-8",
    )
    release_module = _release_module()
    paths = release_module.packaged_metadata_paths(
        "release-identity.json", executable_path=executable
    )

    assert paths == (bundle / "release-identity.json",)
    assert ancestor not in paths
    with pytest.raises(release_module.ReleaseIdentityError):
        release_module.load_release_identity(
            identity_paths=paths,
            distribution_version=None,
            source_root=bundle,
        )


def test_packaged_metadata_discovery_uses_macos_resources_directory(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "OPai.app" / "Contents" / "MacOS" / "opai-gui"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"native")

    paths = _release_module().packaged_metadata_paths(
        "release-identity.json", executable_path=executable
    )

    assert paths == (
        tmp_path / "OPai.app" / "Contents" / "Resources" / "release-identity.json",
    )


def test_development_projection_is_honest_when_no_build_identity_exists() -> None:
    identity = _release_module().load_release_identity(
        identity_paths=(), distribution_version=None, source_root=ROOT
    )

    assert identity.application_version == "0.2.1a1"
    assert identity.build_id == "development"


def test_explicit_packaged_identity_boundary_rejects_a_missing_manifest(
    tmp_path: Path,
) -> None:
    release_module = _release_module()

    with pytest.raises(
        release_module.ReleaseIdentityError, match="usable embedded release identity"
    ):
        release_module.load_release_identity(
            identity_paths=(tmp_path / "missing-release-identity.json",),
            distribution_version=None,
            source_root=ROOT,
        )


def test_explicit_packaged_identity_boundary_rejects_a_corrupt_manifest(
    tmp_path: Path,
) -> None:
    embedded = tmp_path / "release-identity.json"
    embedded.write_text("{not-json", encoding="utf-8")
    release_module = _release_module()

    with pytest.raises(
        release_module.ReleaseIdentityError, match="release-identity.json"
    ):
        release_module.load_release_identity(
            identity_paths=(embedded,),
            distribution_version=None,
            source_root=ROOT,
        )


def test_source_checkout_does_not_adopt_unrelated_installed_metadata() -> None:
    identity = _release_module().load_release_identity(
        identity_paths=(),
        embedded_build_paths=(),
        distribution_version="9.9.9",
        source_root=ROOT,
    )

    assert identity.application_version == "0.2.1a1"
    assert identity.install_type == "source_checkout"
    assert identity.build_id == "development"
    assert identity.metadata_source == "generated-development-projection"


def test_generated_projection_script_reports_clean_repository() -> None:
    generator = importlib.import_module("scripts.generate_release_identity")

    assert generator.main(["--check", "--root", str(ROOT)]) == 0
