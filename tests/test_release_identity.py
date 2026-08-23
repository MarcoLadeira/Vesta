from __future__ import annotations

import importlib
import json
from pathlib import Path


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


def test_development_projection_is_honest_when_no_build_identity_exists() -> None:
    identity = _release_module().load_release_identity(
        identity_paths=(), distribution_version=None, source_root=ROOT
    )

    assert identity.application_version == "0.2.1a1"
    assert identity.build_id == "development"


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
