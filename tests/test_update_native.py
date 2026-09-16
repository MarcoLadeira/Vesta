from __future__ import annotations

import base64
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vesta.update.models import InstallType, UpdateCandidate
from vesta.update.native import MacOSSparkleAdapter, WindowsMsixAdapter


def _candidate(install_type: InstallType, **overrides: object) -> UpdateCandidate:
    native: dict[str, object]
    if install_type is InstallType.WINDOWS_MSIX:
        native = {
            "package_name": "Vesta.Desktop",
            "windows_signer_thumbprint": "A" * 40,
            "appinstaller_url": "https://updates.example.test/stable/Vesta.appinstaller",
        }
        publisher = "CN=Vesta"
        suffix = "msix"
    else:
        native = {
            "appcast_url": "https://updates.example.test/stable/appcast.xml",
            "sparkle_ed_signature": "",
        }
        publisher = "TEAMID1234"
        suffix = "zip"
    values: dict[str, object] = {
        "version": "0.3.0",
        "build_id": "b" * 40,
        "channel": "stable",
        "release_id": "v0.3.0",
        "published_at": "2026-08-14T10:00:00Z",
        "platform": "windows" if install_type is InstallType.WINDOWS_MSIX else "macos",
        "architecture": "x86_64",
        "install_type": install_type,
        "artifact_url": f"https://updates.example.test/Vesta.{suffix}",
        "artifact_sha256": "a" * 64,
        "artifact_size": 1,
        "publisher_identity": publisher,
        "native": native,
    }
    values.update(overrides)
    return UpdateCandidate(**values)


def _msix(
    path: Path, *, name: str = "Vesta.Desktop", publisher: str = "CN=Vesta"
) -> None:
    manifest = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">'
        f'<Identity Name="{name}" Publisher="{publisher}" Version="0.3.0.0" />'
        '<Applications><Application Id="Vesta" /></Applications></Package>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AppxManifest.xml", manifest)


def test_windows_adapter_requires_manifest_and_native_signer_continuity(tmp_path: Path):
    artifact = tmp_path / "Vesta.msix"
    _msix(artifact)
    calls: list[list[str]] = []

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "Status": "Valid",
                    "Subject": "CN=Vesta",
                    "Thumbprint": "A" * 40,
                }
            ),
            "",
        )

    result = WindowsMsixAdapter(run=run, powershell="pwsh").verify(
        artifact, _candidate(InstallType.WINDOWS_MSIX)
    )

    assert result.verified is True
    assert result.publisher_identity == "CN=Vesta"
    assert calls and "Get-AuthenticodeSignature" in " ".join(calls[0])


def test_windows_authenticates_package_before_parsing_untrusted_xml(tmp_path: Path):
    artifact = tmp_path / "invalid.msix"
    artifact.write_bytes(b"not-a-zip")
    calls: list[list[str]] = []

    result = WindowsMsixAdapter(
        run=lambda command: (
            calls.append(command)
            or subprocess.CompletedProcess(command, 1, "", "invalid")
        ),
        powershell="pwsh",
    ).verify(artifact, _candidate(InstallType.WINDOWS_MSIX))

    assert result.verified is False
    assert result.error_category == "native_signature_invalid"
    assert calls and "Get-AuthenticodeSignature" in " ".join(calls[0])


@pytest.mark.parametrize(
    ("name", "publisher", "thumbprint", "category"),
    [
        ("Attacker.App", "CN=Vesta", "A" * 40, "package_identity_mismatch"),
        ("Vesta.Desktop", "CN=Attacker", "A" * 40, "publisher_mismatch"),
        ("Vesta.Desktop", "CN=Vesta", "B" * 40, "certificate_mismatch"),
    ],
)
def test_windows_adapter_rejects_identity_mix_and_match(
    tmp_path: Path, name: str, publisher: str, thumbprint: str, category: str
):
    artifact = tmp_path / "Vesta.msix"
    _msix(artifact, name=name, publisher=publisher)

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {"Status": "Valid", "Subject": publisher, "Thumbprint": thumbprint}
            ),
            "",
        )

    result = WindowsMsixAdapter(run=run, powershell="pwsh").verify(
        artifact, _candidate(InstallType.WINDOWS_MSIX)
    )

    assert result.verified is False
    assert result.error_category == category


def test_windows_install_uses_native_package_deployment_and_never_a_shell(
    tmp_path: Path,
):
    artifact = tmp_path / "Vesta.msix"
    _msix(artifact)
    launches: list[list[str]] = []

    class Process:
        pid = 741

    adapter = WindowsMsixAdapter(
        run=lambda command: subprocess.CompletedProcess(command, 0, "{}", ""),
        launch=lambda command: launches.append(command) or Process(),
        powershell="pwsh",
    )

    result = adapter.install(
        artifact, _candidate(InstallType.WINDOWS_MSIX), mode="on_quit"
    )

    assert result.started is True
    assert result.transaction_id == "msix-pid-741"
    assert launches[0][0] == "pwsh"
    command = " ".join(launches[0])
    assert str(artifact.resolve()) in launches[0]
    assert "ForceApplicationShutdown" in command
    assert "PackageFamilyName" in command
    assert "shell:AppsFolder" in command
    assert "Start-Process" in command
    assert result.result_path


def test_windows_rollback_uses_observed_helper_and_relaunches_package(tmp_path: Path):
    artifact = tmp_path / "previous.msix"
    _msix(artifact)
    launches: list[list[str]] = []

    class Process:
        pid = 815

    result = WindowsMsixAdapter(
        launch=lambda command: launches.append(command) or Process(),
        powershell="pwsh",
    ).rollback(
        {
            "artifact_path": str(artifact),
            "artifact_sha256": __import__("hashlib")
            .sha256(artifact.read_bytes())
            .hexdigest(),
        }
    )

    assert result.started is True
    assert result.result_path
    command = " ".join(launches[0])
    assert "ForceUpdateFromAnyVersion" in command
    assert "shell:AppsFolder" in command


def _sparkle_archive(path: Path) -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Vesta.app/Contents/MacOS/Vesta", b"binary")
    return path.read_bytes()


def test_sparkle_adapter_verifies_eddsa_and_apple_team_identity(tmp_path: Path):
    artifact = tmp_path / "Vesta.zip"
    body = _sparkle_archive(artifact)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    signature = base64.b64encode(key.sign(body)).decode()
    candidate = _candidate(
        InstallType.MACOS_SPARKLE,
        artifact_size=len(body),
        native={
            "appcast_url": "https://updates.example.test/stable/appcast.xml",
            "sparkle_ed_signature": signature,
        },
    )

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        output = "TeamIdentifier=TEAMID1234" if "-d" in command else ""
        return subprocess.CompletedProcess(command, 0, output, output)

    result = MacOSSparkleAdapter(
        sparkle_public_key=base64.b64encode(public).decode(),
        run=run,
        sparkle_cli=tmp_path / "sparkle",
        app_bundle=tmp_path / "Vesta.app",
    ).verify(artifact, candidate)

    assert result.verified is True
    assert result.publisher_identity == "TEAMID1234"


def test_sparkle_install_delegates_transaction_to_sparkle_cli(tmp_path: Path):
    artifact = tmp_path / "Vesta.zip"
    artifact.write_bytes(b"verified")
    launches: list[list[str]] = []

    class Process:
        pid = 912

    sparkle = tmp_path / "sparkle"
    sparkle.write_bytes(b"helper")
    appcast = (
        b'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
        b'<channel><item><enclosure url="https://updates.example.test/Vesta.zip" '
        b'sparkle:version="0.3.0" sparkle:edSignature="sig" length="8" />'
        b"</item></channel></rss>"
    )
    candidate = _candidate(
        InstallType.MACOS_SPARKLE,
        artifact_size=8,
        artifact_sha256="a" * 64,
        native={
            "appcast_url": "https://updates.example.test/stable/appcast.xml",
            "appcast_sha256": __import__("hashlib").sha256(appcast).hexdigest(),
            "sparkle_ed_signature": "sig",
        },
    )
    adapter = MacOSSparkleAdapter(
        sparkle_public_key=base64.b64encode(b"x" * 32).decode(),
        sparkle_cli=sparkle,
        app_bundle=tmp_path / "Vesta.app",
        appcast_fetcher=lambda _url: appcast,
        launch=lambda command: launches.append(command) or Process(),
    )

    result = adapter.install(artifact, candidate, mode="on_quit")

    assert result.started is True
    assert result.transaction_id == "sparkle-pid-912"
    command = launches[0]
    assert "--check-immediately" in command
    assert "--defer-install" in command
    assert "--feed-url" in command
    assert "--application" in command


def test_sparkle_install_rejects_appcast_not_bound_to_signed_candidate(tmp_path: Path):
    artifact = tmp_path / "Vesta.zip"
    artifact.write_bytes(b"verified")
    sparkle = tmp_path / "sparkle"
    sparkle.write_bytes(b"helper")
    appcast = b'<rss><channel><item><enclosure url="https://attacker.test/other.zip" /></item></channel></rss>'
    launches: list[list[str]] = []
    candidate = _candidate(
        InstallType.MACOS_SPARKLE,
        artifact_size=len(artifact.read_bytes()),
        native={
            "appcast_url": "https://updates.example.test/stable/appcast.xml",
            "appcast_sha256": __import__("hashlib").sha256(appcast).hexdigest(),
            "sparkle_ed_signature": "sig",
        },
    )

    result = MacOSSparkleAdapter(
        sparkle_public_key=base64.b64encode(b"x" * 32).decode(),
        sparkle_cli=sparkle,
        app_bundle=tmp_path / "Vesta.app",
        appcast_fetcher=lambda _url: appcast,
        launch=lambda command: launches.append(command),
    ).install(artifact, candidate, mode="now")

    assert result.started is False
    assert result.error_category == "native_feed_mismatch"
    assert launches == []


def test_sparkle_rollback_uses_verified_recovery_helper_and_relaunches(tmp_path: Path):
    artifact = tmp_path / "previous.zip"
    artifact.write_bytes(b"previous-signed-archive")
    launches: list[list[str]] = []

    class Process:
        pid = 1337

    result = MacOSSparkleAdapter(
        sparkle_public_key=base64.b64encode(b"x" * 32).decode(),
        sparkle_cli=tmp_path / "sparkle",
        app_bundle=tmp_path / "Vesta.app",
        launch=lambda command: launches.append(command) or Process(),
    ).rollback(
        {
            "artifact_path": str(artifact),
            "artifact_sha256": __import__("hashlib")
            .sha256(artifact.read_bytes())
            .hexdigest(),
            "version": "0.2.1a1",
            "build_id": "a" * 40,
            "publisher_identity": "TEAMID1234",
        }
    )

    assert result.started is True
    assert result.result_path
    assert launches[0][0] == "/bin/bash"
    script = Path(launches[0][1]).read_text(encoding="utf-8")
    assert "/usr/bin/codesign --verify --deep --strict" in script
    assert "TeamIdentifier" in script
    assert "/usr/bin/open" in script
