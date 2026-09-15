from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from opai.update.manifest import verify_manifest
from opai.update.models import InstallType, InstalledBuild
from opai.update.release import (
    ReleaseArtifact,
    ReleaseError,
    generate_release_files,
    msix_version,
)


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def _key() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, base64.b64encode(public).decode()


def _msix(
    path: Path, *, name: str = "OPai.Desktop", publisher: str = "CN=Vesta"
) -> None:
    manifest = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">'
        f'<Identity Name="{name}" Publisher="{publisher}" Version="0.3.0.65535" />'
        '<Applications><Application Id="OPai" /></Applications></Package>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AppxManifest.xml", manifest)
        archive.writestr("gui/OPai.exe", b"signed-code")
        archive.writestr(
            "release-identity.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "version": "0.3.0",
                    "build_id": "b" * 40,
                    "channel": "stable",
                    "platform": "windows",
                    "architecture": "x86_64",
                    "install_type": "windows_msix",
                    "package_identity": name,
                    "publisher_identity": publisher,
                }
            ),
        )


def _mac_zip(path: Path) -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("OPai.app/Contents/MacOS/OPai", b"signed-code")
        archive.writestr("OPai.app/Contents/Resources/opai", b"signed-cli")
        archive.writestr(
            "OPai.app/Contents/Resources/release-identity.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "version": "0.3.0",
                    "build_id": "b" * 40,
                    "channel": "stable",
                    "platform": "macos",
                    "architecture": "arm64",
                    "install_type": "macos_sparkle",
                    "package_identity": "com.opai.desktop",
                    "publisher_identity": "ABCDE12345",
                }
            ),
        )
    return path.read_bytes()


def _artifacts(tmp_path: Path) -> tuple[list[ReleaseArtifact], str]:
    msix = tmp_path / "OPai-0.3.0-x64.msix"
    mac = tmp_path / "OPai-0.3.0-macos.zip"
    _msix(msix)
    mac_bytes = _mac_zip(mac)
    sparkle_key, sparkle_public = _key()
    sparkle_signature = base64.b64encode(sparkle_key.sign(mac_bytes)).decode()
    return [
        ReleaseArtifact(
            path=msix,
            url="https://updates.example.test/releases/v0.3.0/OPai-0.3.0-x64.msix",
            platform="windows",
            architecture="x86_64",
            install_type=InstallType.WINDOWS_MSIX,
            publisher_identity="CN=Vesta",
            package_identity="OPai.Desktop",
            native={"windows_signer_thumbprint": "A" * 40},
        ),
        ReleaseArtifact(
            path=mac,
            url="https://updates.example.test/releases/v0.3.0/OPai-0.3.0-macos.zip",
            platform="macos",
            architecture="arm64",
            install_type=InstallType.MACOS_SPARKLE,
            publisher_identity="ABCDE12345",
            package_identity="com.opai.desktop",
            native={"sparkle_ed_signature": sparkle_signature},
        ),
    ], sparkle_public


def _generate(tmp_path: Path, **overrides: object):
    artifacts, sparkle_public = _artifacts(tmp_path)
    metadata_key, metadata_public = _key()
    values = {
        "output": tmp_path / "feed",
        "artifacts": artifacts,
        "version": "0.3.0",
        "build_id": "b" * 40,
        "channel": "stable",
        "metadata_version": 8,
        "generated_at": NOW,
        "expires_at": NOW + timedelta(days=7),
        "feed_base_url": "https://updates.example.test/stable",
        "release_id": "v0.3.0",
        "release_title": "Vesta 0.3.0",
        "release_notes": "Security and reliability improvements.",
        "release_notes_url": "https://updates.example.test/releases/v0.3.0/notes",
        "metadata_keys": {"root-2": metadata_key},
        "metadata_public_keys": {"root-2": metadata_public},
        "signature_threshold": 1,
        "sparkle_public_key": sparkle_public,
    }
    values.update(overrides)
    return generate_release_files(**values)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.2.3", "1.2.3.65535"),
        ("1.2.3a4", "1.2.3.1004"),
        ("1.2.3b4", "1.2.3.2004"),
        ("1.2.3rc4", "1.2.3.3004"),
    ],
)
def test_msix_version_is_monotonic_within_pep440_release(version: str, expected: str):
    assert msix_version(version) == expected


def test_generation_hashes_final_signed_artifact_bytes(tmp_path: Path):
    result = _generate(tmp_path)
    envelope = json.loads(result.manifest.read_text(encoding="utf-8"))
    candidates = envelope["signed"]["releases"]

    for candidate in candidates:
        artifact = next(
            path
            for path in (tmp_path).iterdir()
            if path.name in candidate["artifact_url"]
        )
        assert candidate["artifact_size"] == artifact.stat().st_size
        assert len(candidate["artifact_sha256"]) == 64


def test_generated_manifest_authenticates_for_each_platform(tmp_path: Path):
    result = _generate(tmp_path)
    payload = result.manifest.read_bytes()
    trust = json.loads(result.trust.read_text(encoding="utf-8"))

    windows = verify_manifest(
        payload,
        trust=trust,
        installed=InstalledBuild(
            version="0.2.1a1",
            build_id="a" * 40,
            channel="stable",
            platform="windows",
            architecture="x86_64",
            install_type=InstallType.WINDOWS_MSIX,
            package_identity="OPai.Desktop",
            publisher_identity="CN=Vesta",
        ),
        cohort=1,
        prior_metadata_version=7,
        now=NOW,
    )
    macos = verify_manifest(
        payload,
        trust=trust,
        installed=InstalledBuild(
            version="0.2.1a1",
            build_id="a" * 40,
            channel="stable",
            platform="macos",
            architecture="arm64",
            install_type=InstallType.MACOS_SPARKLE,
            package_identity="com.opai.desktop",
            publisher_identity="ABCDE12345",
        ),
        cohort=1,
        prior_metadata_version=7,
        now=NOW,
    )

    assert windows.candidate and windows.candidate.native["appinstaller_url"].endswith(
        "OPai.appinstaller"
    )
    assert macos.candidate and macos.candidate.native["appcast_url"].endswith(
        "appcast.xml"
    )


def test_release_advertises_rollback_only_when_a_signed_recovery_candidate_exists(
    tmp_path: Path,
):
    result = _generate(tmp_path)
    envelope = json.loads(result.manifest.read_text(encoding="utf-8"))

    assert all(
        candidate["rollback_compatible"] is False
        for candidate in envelope["signed"]["releases"]
    )


def test_release_embeds_complete_prior_candidates_as_recovery_packages(tmp_path: Path):
    baseline_root = tmp_path / "baseline"
    baseline_root.mkdir()
    baseline = _generate(baseline_root)
    baseline_candidates = json.loads(baseline.manifest.read_text(encoding="utf-8"))[
        "signed"
    ]["releases"]
    recovery_by_platform = {}
    for raw in baseline_candidates:
        recovery = dict(raw)
        recovery["version"] = "0.2.1"
        recovery["build_id"] = "a" * 40
        recovery["artifact_url"] = "https://updates.example.test/releases/v0.2.1/" + (
            "OPai-0.2.1-x64.msix"
            if raw["platform"] == "windows"
            else "OPai-0.2.1-macos.zip"
        )
        recovery["rollback_compatible"] = False
        recovery_by_platform[raw["platform"]] = recovery

    target_root = tmp_path / "target"
    target_root.mkdir()
    artifacts, sparkle_public = _artifacts(target_root)
    artifacts = [
        ReleaseArtifact(
            **{
                **artifact.__dict__,
                "native": {
                    **artifact.native,
                    "recovery": recovery_by_platform[artifact.platform],
                },
            }
        )
        for artifact in artifacts
    ]

    result = _generate(
        target_root,
        artifacts=artifacts,
        sparkle_public_key=sparkle_public,
    )
    candidates = json.loads(result.manifest.read_text(encoding="utf-8"))["signed"][
        "releases"
    ]

    assert all(candidate["rollback_compatible"] is True for candidate in candidates)
    assert all(
        candidate["native"]["recovery"]["version"] == "0.2.1"
        for candidate in candidates
    )


def test_appinstaller_identity_and_uri_match_manifest_candidate(tmp_path: Path):
    result = _generate(tmp_path)
    root = ElementTree.parse(result.appinstaller).getroot()
    package = next(
        node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "MainPackage"
    )

    assert package.attrib["Name"] == "OPai.Desktop"
    assert package.attrib["Publisher"] == "CN=Vesta"
    assert package.attrib["Uri"].endswith(".msix")
    assert package.attrib["Version"] == "0.3.0.65535"


def test_sparkle_appcast_matches_final_archive_and_signature(tmp_path: Path):
    result = _generate(tmp_path)
    root = ElementTree.parse(result.appcast).getroot()
    enclosure = next(
        node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "enclosure"
    )
    mac = tmp_path / "OPai-0.3.0-macos.zip"

    assert enclosure.attrib["url"].endswith(mac.name)
    assert int(enclosure.attrib["length"]) == mac.stat().st_size
    assert any(name.endswith("edSignature") for name in enclosure.attrib)
    envelope = json.loads(result.manifest.read_text(encoding="utf-8"))
    mac = next(
        item
        for item in envelope["signed"]["releases"]
        if item["install_type"] == "macos_sparkle"
    )
    assert (
        mac["native"]["appcast_sha256"]
        == hashlib.sha256(result.appcast.read_bytes()).hexdigest()
    )


def test_manifest_signatures_support_rotation_and_threshold(tmp_path: Path):
    key1, public1 = _key()
    key2, public2 = _key()
    result = _generate(
        tmp_path,
        metadata_keys={"root-2": key1, "root-3": key2},
        metadata_public_keys={"root-2": public1, "root-3": public2},
        signature_threshold=2,
    )
    envelope = json.loads(result.manifest.read_text(encoding="utf-8"))
    trust = json.loads(result.trust.read_text(encoding="utf-8"))

    assert {item["key_id"] for item in envelope["signatures"]} == {"root-2", "root-3"}
    assert trust["threshold"] == 2


def test_revoked_public_key_does_not_require_or_receive_a_private_signature(
    tmp_path: Path,
):
    active, active_public = _key()
    _revoked, revoked_public = _key()
    result = _generate(
        tmp_path,
        metadata_keys={"root-3": active},
        metadata_public_keys={
            "root-2": revoked_public,
            "root-3": active_public,
        },
        revoked_key_ids=("root-2",),
    )
    envelope = json.loads(result.manifest.read_text(encoding="utf-8"))
    trust = json.loads(result.trust.read_text(encoding="utf-8"))

    assert [item["key_id"] for item in envelope["signatures"]] == ["root-3"]
    assert (
        next(item for item in trust["keys"] if item["key_id"] == "root-2")["revoked"]
        is True
    )


def test_generated_trust_pins_feed_redirect_origins(tmp_path: Path):
    result = _generate(
        tmp_path,
        feed_redirect_origins=("https://release-assets.githubusercontent.com",),
    )
    trust = json.loads(result.trust.read_text(encoding="utf-8"))

    assert trust["feed_redirect_origins"] == [
        "https://release-assets.githubusercontent.com"
    ]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("feed_base_url", "http://updates.invalid/stable", "HTTPS"),
        ("expires_at", NOW, "expiry"),
        ("metadata_version", 0, "metadata version"),
        ("channel", "nightly", "channel"),
    ],
)
def test_release_generation_fails_closed_on_invalid_metadata(
    tmp_path: Path, field: str, value: object, message: str
):
    with pytest.raises(ReleaseError, match=message):
        _generate(tmp_path, **{field: value})


def test_release_generation_rejects_msix_identity_mismatch(tmp_path: Path):
    artifacts, sparkle_public = _artifacts(tmp_path)
    artifacts[0] = ReleaseArtifact(
        **{**artifacts[0].__dict__, "package_identity": "Attacker.Desktop"}
    )
    with pytest.raises(ReleaseError, match="MSIX identity"):
        _generate(tmp_path, artifacts=artifacts, sparkle_public_key=sparkle_public)


def test_release_generation_rejects_embedded_runtime_identity_mix_and_match(
    tmp_path: Path,
):
    artifacts, sparkle_public = _artifacts(tmp_path)
    artifacts[0] = ReleaseArtifact(**{**artifacts[0].__dict__, "architecture": "arm64"})

    with pytest.raises(ReleaseError, match="runtime identity"):
        _generate(tmp_path, artifacts=artifacts, sparkle_public_key=sparkle_public)


def test_release_inventory_makes_atomic_publication_order_explicit(tmp_path: Path):
    result = _generate(tmp_path)
    inventory = json.loads(result.inventory.read_text(encoding="utf-8"))

    assert inventory["publication_order"][-1].endswith("manifest.json")
    assert set(inventory["artifact_urls"]) == {
        "https://updates.example.test/releases/v0.3.0/OPai-0.3.0-x64.msix",
        "https://updates.example.test/releases/v0.3.0/OPai-0.3.0-macos.zip",
    }
    assert inventory["metadata_version"] == 8


def test_generation_refuses_to_overwrite_a_nonempty_feed_directory(tmp_path: Path):
    output = tmp_path / "occupied"
    output.mkdir()
    (output / "foreign.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ReleaseError, match="non-empty"):
        _generate(tmp_path, output=output)
