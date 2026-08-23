from __future__ import annotations

import json
import plistlib
from pathlib import Path
from xml.etree import ElementTree

import pytest

from opai._generated_release import APPLICATION_VERSION, RELEASE_CHANNEL
from opai.asset_identity import asset_manifest
from opai.compatibility import runtime_compatibility_payload
from opai.update.models import InstallType
from opai.update.packaging import (
    prepare_macos_sparkle_bundle,
    prepare_msix_layout,
    render_msix_manifest,
    runtime_identity,
    validate_trust_store,
    write_runtime_configuration,
)
from opai.update.release import ReleaseError


ROOT = Path(__file__).resolve().parents[1]


def _trust() -> dict[str, object]:
    return {
        "schema_version": 1,
        "threshold": 1,
        "feed_url": "https://updates.example.test/stable/manifest.json",
        "sparkle_public_key": "cHVibGlj",
        "keys": [{"key_id": "root-1", "public_key": "cHVibGlj", "revoked": False}],
    }


def _identity(install_type: InstallType, **overrides: object) -> dict[str, object]:
    values = {
        "version": APPLICATION_VERSION,
        "build_id": "b" * 40,
        "channel": RELEASE_CHANNEL,
        "platform": "windows" if install_type is InstallType.WINDOWS_MSIX else "macos",
        "architecture": "x86_64"
        if install_type is InstallType.WINDOWS_MSIX
        else "arm64",
        "install_type": install_type,
        "package_identity": "OPai.Desktop"
        if install_type is InstallType.WINDOWS_MSIX
        else "com.opai.desktop",
        "publisher_identity": "CN=OPai"
        if install_type is InstallType.WINDOWS_MSIX
        else "ABCDE12345",
        "assets": asset_manifest(ROOT / "opai" / "assets"),
    }
    values.update(overrides)
    return runtime_identity(**values)


def _windows_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    (bundle / "gui").mkdir(parents=True)
    (bundle / "cli").mkdir()
    (bundle / "gui" / "OPai.exe").write_bytes(b"gui")
    (bundle / "cli" / "opai.exe").write_bytes(b"cli")
    return bundle


def _assets(tmp_path: Path) -> Path:
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in (
        "StoreLogo.png",
        "Square150x150Logo.png",
        "Square44x44Logo.png",
        "Wide310x150Logo.png",
    ):
        (assets / name).write_bytes(b"png")
    return assets


def _mac_bundle(tmp_path: Path) -> tuple[Path, Path]:
    bundle = tmp_path / "bundle"
    app = bundle / "gui" / "OPai.app"
    resources = app / "Contents" / "Resources"
    executable = app / "Contents" / "MacOS"
    resources.mkdir(parents=True)
    executable.mkdir()
    (executable / "OPai").write_bytes(b"gui")
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleIdentifier": "com.opai.desktop"}, stream)
    (bundle / "cli").mkdir()
    (bundle / "cli" / "opai").write_bytes(b"cli")
    sparkle = tmp_path / "sparkle.app"
    (sparkle / "Contents" / "MacOS").mkdir(parents=True)
    (sparkle / "Contents" / "MacOS" / "sparkle").write_bytes(b"helper")
    return bundle, sparkle


def test_runtime_identity_is_canonical_and_package_typed():
    value = _identity(InstallType.WINDOWS_MSIX)
    assert value["schema_version"] == 1
    assert value["install_type"] == "windows_msix"
    assert value["updater_protocol_version"] == 1
    assert value["assets"] == asset_manifest(ROOT / "opai" / "assets")
    assert value["compatibility"] == runtime_compatibility_payload()


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": 2},
        {"feed_url": "http://unsafe.test/feed"},
        {"threshold": 2},
        {"keys": "root-1"},
        {"keys": [{"key_id": "root-1", "public_key": "x", "revoked": True}]},
    ],
)
def test_packaged_trust_fails_closed(mutation: dict[str, object]):
    value = {**_trust(), **mutation}
    with pytest.raises(ReleaseError, match="trust"):
        validate_trust_store(value)


def test_windows_runtime_configuration_is_beside_packaged_bundle(tmp_path: Path):
    bundle = _windows_bundle(tmp_path)
    identity_path, trust_path = write_runtime_configuration(
        bundle, identity=_identity(InstallType.WINDOWS_MSIX), trust=_trust()
    )
    assert identity_path == bundle / "release-identity.json"
    assert trust_path == bundle / "update-trust.json"
    assert json.loads(identity_path.read_text())["build_id"] == "b" * 40


def test_macos_runtime_configuration_is_inside_app_resources(tmp_path: Path):
    bundle, _ = _mac_bundle(tmp_path)
    identity_path, trust_path = write_runtime_configuration(
        bundle, identity=_identity(InstallType.MACOS_SPARKLE), trust=_trust()
    )
    assert "OPai.app" in identity_path.parts
    assert identity_path.parent.name == "Resources"
    assert trust_path.parent == identity_path.parent


def test_msix_manifest_ships_gui_and_cli_alias_together():
    root = ElementTree.fromstring(
        render_msix_manifest(
            package_identity="OPai.Desktop",
            publisher_identity="CN=OPai",
            version="0.3.0",
            architecture="x86_64",
        )
    )
    application = next(
        node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Application"
    )
    extension = next(
        node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Extension"
    )
    alias = next(
        node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "ExecutionAlias"
    )
    assert application.attrib["Executable"] == "gui\\OPai.exe"
    assert extension.attrib["Executable"] == "cli\\opai.exe"
    assert alias.attrib["Alias"] == "opai.exe"


def test_msix_layout_contains_complete_bundle_manifest_and_assets(tmp_path: Path):
    bundle = _windows_bundle(tmp_path)
    layout = tmp_path / "layout"
    manifest = prepare_msix_layout(
        bundle,
        layout,
        package_identity="OPai.Desktop",
        publisher_identity="CN=OPai",
        version="0.3.0",
        architecture="x86_64",
        assets=_assets(tmp_path),
    )
    assert manifest.is_file()
    assert (layout / "gui" / "OPai.exe").read_bytes() == b"gui"
    assert (layout / "cli" / "opai.exe").read_bytes() == b"cli"
    assert (layout / "Assets" / "StoreLogo.png").is_file()


def test_msix_layout_refuses_missing_cli(tmp_path: Path):
    bundle = _windows_bundle(tmp_path)
    (bundle / "cli" / "opai.exe").unlink()
    with pytest.raises(ReleaseError, match="GUI or CLI"):
        prepare_msix_layout(
            bundle,
            tmp_path / "layout",
            package_identity="OPai.Desktop",
            publisher_identity="CN=OPai",
            version="0.3.0",
            architecture="x86_64",
            assets=_assets(tmp_path),
        )


def test_sparkle_preparation_embeds_helper_cli_and_feed_identity(tmp_path: Path):
    bundle, sparkle = _mac_bundle(tmp_path)
    app = prepare_macos_sparkle_bundle(
        bundle,
        sparkle_app=sparkle,
        version="0.3.0",
        feed_url="https://updates.example.test/stable/appcast.xml",
        sparkle_public_key="cHVibGlj",
    )
    resources = app / "Contents" / "Resources"
    assert (resources / "opai").read_bytes() == b"cli"
    assert (
        resources / "OPaiUpdater" / "sparkle.app" / "Contents" / "MacOS" / "sparkle"
    ).is_file()
    with (app / "Contents" / "Info.plist").open("rb") as stream:
        value = plistlib.load(stream)
    assert value["SUFeedURL"].endswith("appcast.xml")
    assert value["SUPublicEDKey"] == "cHVibGlj"
    assert value["SUEnableAutomaticChecks"] is False
    assert value["CFBundleShortVersionString"] == "0.3.0"


def test_sparkle_preparation_rejects_unbundled_helper(tmp_path: Path):
    bundle, _ = _mac_bundle(tmp_path)
    with pytest.raises(ReleaseError, match="helper"):
        prepare_macos_sparkle_bundle(
            bundle,
            sparkle_app=tmp_path / "missing.app",
            version="0.3.0",
            feed_url="https://updates.example.test/stable/appcast.xml",
            sparkle_public_key="cHVibGlj",
        )


@pytest.mark.parametrize("build_id", ["short", "A" * 40, "z" * 40])
def test_runtime_identity_rejects_noncanonical_commit(build_id: str):
    with pytest.raises(ReleaseError, match="build ID"):
        _identity(InstallType.WINDOWS_MSIX, build_id=build_id)
