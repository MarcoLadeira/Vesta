"""Native update adapters for Windows MSIX and macOS Sparkle 2."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess  # nosec B404
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from xml.etree import ElementTree  # nosec B405

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from vestahub.atomic_io import atomic_write_text
from vestahub.proc import no_window_kwargs

from .adapters import AdapterInstallResult, AdapterVerification
from .models import InstallType, InstalledBuild, UpdateCandidate
from .network import fetch_manifest, parse_https_origins


Run = Callable[[list[str]], subprocess.CompletedProcess[str]]
Launch = Callable[[list[str]], Any]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    # Native verifier commands use fixed binaries and argv without a shell.
    return subprocess.run(  # nosec B603
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
        **no_window_kwargs(),
    )


def _launch(command: list[str]) -> subprocess.Popen[bytes]:
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)  # nosec B603


def _powershell() -> str:
    return shutil.which("powershell") or shutil.which("pwsh") or ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("unsafe archive member")
    return path


def _read_msix_identity(path: Path) -> tuple[str, str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.casefold() == "appxmanifest.xml"
            ]
            if names != ["AppxManifest.xml"]:
                raise ValueError("missing or duplicate AppxManifest.xml")
            info = archive.getinfo(names[0])
            if info.file_size > 1024 * 1024:
                raise ValueError("oversized AppxManifest.xml")
            root = ElementTree.fromstring(archive.read(info))  # nosec B314
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
        raise ValueError("invalid MSIX manifest") from exc
    identity = next(
        (
            element
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "Identity"
        ),
        None,
    )
    if identity is None:
        raise ValueError("missing MSIX identity")
    application = next(
        (
            element
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "Application"
        ),
        None,
    )
    if application is None or not application.attrib.get("Id"):
        raise ValueError("missing MSIX application identity")
    return (
        str(identity.attrib.get("Name") or ""),
        str(identity.attrib.get("Publisher") or ""),
        str(application.attrib["Id"]),
    )


class WindowsMsixAdapter:
    name = "windows-msix-app-installer"

    def __init__(
        self,
        *,
        run: Run = _run,
        launch: Launch = _launch,
        powershell: str | None = None,
    ) -> None:
        self._run = run
        self._launch = launch
        self._powershell = _powershell() if powershell is None else powershell

    def doctor(self, installed: InstalledBuild) -> dict[str, object]:
        return {
            "available": bool(self._powershell)
            and installed.install_type is InstallType.WINDOWS_MSIX,
            "name": self.name,
            "package_identity": installed.package_identity,
            "publisher_identity": installed.publisher_identity,
            "mechanism": "MSIX Package Deployment / App Installer",
        }

    def verify(self, artifact: Path, candidate: UpdateCandidate) -> AdapterVerification:
        if candidate.install_type is not InstallType.WINDOWS_MSIX:
            return AdapterVerification(False, error_category="install_type_mismatch")
        expected_thumbprint = (
            str(candidate.native.get("windows_signer_thumbprint") or "")
            .replace(" ", "")
            .upper()
        )
        if not self._powershell or len(expected_thumbprint) not in {40, 64}:
            return AdapterVerification(
                False, error_category="native_trust_unconfigured"
            )
        script = (
            "$s=Get-AuthenticodeSignature -LiteralPath $args[0]; "
            "[pscustomobject]@{Status=[string]$s.Status;Subject=[string]$s.SignerCertificate.Subject;"
            "Thumbprint=[string]$s.SignerCertificate.Thumbprint}|ConvertTo-Json -Compress"
        )
        try:
            completed = self._run(
                [
                    self._powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                    str(artifact.resolve()),
                ]
            )
            value = json.loads(completed.stdout) if completed.returncode == 0 else {}
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            value = {}
        if value.get("Status") != "Valid":
            return AdapterVerification(False, error_category="native_signature_invalid")
        subject = str(value.get("Subject") or "")
        if subject != candidate.publisher_identity:
            return AdapterVerification(False, error_category="publisher_mismatch")
        thumbprint = str(value.get("Thumbprint") or "").replace(" ", "").upper()
        if thumbprint != expected_thumbprint:
            return AdapterVerification(False, error_category="certificate_mismatch")
        try:
            package_name, manifest_publisher, application_id = _read_msix_identity(
                artifact
            )
        except ValueError:
            return AdapterVerification(False, error_category="native_signature_invalid")
        expected_name = str(candidate.native.get("package_name") or "")
        if not expected_name or package_name != expected_name:
            return AdapterVerification(
                False, error_category="package_identity_mismatch"
            )
        if manifest_publisher != candidate.publisher_identity:
            return AdapterVerification(False, error_category="publisher_mismatch")
        expected_application = str(candidate.native.get("application_id") or "Vesta")
        if application_id != expected_application:
            return AdapterVerification(
                False, error_category="package_identity_mismatch"
            )
        return AdapterVerification(
            True, publisher_identity=subject, evidence=f"authenticode:{thumbprint}"
        )

    def install(
        self, artifact: Path, candidate: UpdateCandidate, *, mode: str
    ) -> AdapterInstallResult:
        if not self._powershell or not artifact.is_file():
            return AdapterInstallResult(False, error_category="installer_unavailable")
        try:
            package_name, _publisher, application_id = _read_msix_identity(artifact)
        except ValueError:
            return AdapterInstallResult(
                False, error_category="native_signature_invalid"
            )
        result_path = artifact.parent / "native-result.json"
        script = (
            "$ErrorActionPreference='Stop'; $ok=$false; $category='installer_failed'; "
            "try { Add-AppxPackage -LiteralPath $args[0] -ForceApplicationShutdown "
            "-ErrorAction Stop; $ok=$true; $category=''; } catch {} "
            "$pkg=Get-AppxPackage -Name $args[2] | Sort-Object Version -Descending | "
            "Select-Object -First 1; $tmp=$args[1]+'.tmp'; "
            "[pscustomobject]@{schema_version=1;success=$ok;error_category=$category;"
            "package_full_name=[string]$pkg.PackageFullName}|ConvertTo-Json -Compress|"
            "Set-Content -LiteralPath $tmp -Encoding utf8; Move-Item -LiteralPath $tmp "
            "-Destination $args[1] -Force; if ($pkg) { Start-Process explorer.exe "
            "('shell:AppsFolder\\'+$pkg.PackageFamilyName+'!'+$args[3]); }"
        )
        command = [
            self._powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
            str(artifact.resolve()),
            str(result_path.resolve()),
            package_name,
            application_id,
        ]
        try:
            process = self._launch(command)
        except (OSError, subprocess.SubprocessError):
            return AdapterInstallResult(False, error_category="installer_failed")
        return AdapterInstallResult(
            True,
            transaction_id=f"msix-pid-{int(process.pid)}",
            result_path=str(result_path),
        )

    def rollback(self, last_known_good: Mapping[str, Any]) -> AdapterInstallResult:
        artifact = Path(str(last_known_good.get("artifact_path") or ""))
        if not artifact.is_file() or not self._powershell:
            return AdapterInstallResult(
                False, error_category="rollback_artifact_unavailable"
            )
        expected = str(last_known_good.get("artifact_sha256") or "")
        try:
            digest = _sha256(artifact)
        except OSError:
            return AdapterInstallResult(
                False, error_category="rollback_artifact_unavailable"
            )
        if len(expected) != 64 or digest != expected:
            return AdapterInstallResult(
                False, error_category="rollback_artifact_invalid"
            )
        try:
            package_name, _publisher, application_id = _read_msix_identity(artifact)
        except ValueError:
            return AdapterInstallResult(
                False, error_category="rollback_artifact_invalid"
            )
        result_path = artifact.parent / "rollback-result.json"
        script = (
            "$ErrorActionPreference='Stop'; $ok=$false; $category='rollback_failed'; "
            "try { Add-AppxPackage -LiteralPath $args[0] -ForceUpdateFromAnyVersion "
            "-ForceApplicationShutdown -ErrorAction Stop; $ok=$true; $category=''; } catch {} "
            "$pkg=Get-AppxPackage -Name $args[2] | Sort-Object Version -Descending | "
            "Select-Object -First 1; $tmp=$args[1]+'.tmp'; "
            "[pscustomobject]@{schema_version=1;success=$ok;error_category=$category;"
            "package_full_name=[string]$pkg.PackageFullName}|ConvertTo-Json -Compress|"
            "Set-Content -LiteralPath $tmp -Encoding utf8; Move-Item -LiteralPath $tmp "
            "-Destination $args[1] -Force; if ($pkg) { Start-Process explorer.exe "
            "('shell:AppsFolder\\'+$pkg.PackageFamilyName+'!'+$args[3]); }"
        )
        try:
            process = self._launch(
                [
                    self._powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                    str(artifact.resolve()),
                    str(result_path.resolve()),
                    package_name,
                    application_id,
                ]
            )
        except (OSError, subprocess.SubprocessError):
            return AdapterInstallResult(False, error_category="rollback_failed")
        return AdapterInstallResult(
            True,
            transaction_id=f"msix-rollback-pid-{int(process.pid)}",
            result_path=str(result_path),
        )


def _validate_sparkle_zip(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if not infos or len(infos) > 100_000:
        raise ValueError("invalid archive member count")
    total = 0
    seen: set[str] = set()
    for info in infos:
        path = _zip_member(info.filename)
        key = "/".join(path.parts).casefold()
        if key in seen:
            raise ValueError("duplicate archive member")
        seen.add(key)
        total += int(info.file_size)
        if total > 8 * 1024 * 1024 * 1024:
            raise ValueError("archive expands beyond limit")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            target = PurePosixPath(archive.read(info).decode("utf-8"))
            if target.is_absolute() or ".." in target.parts:
                raise ValueError("archive symlink escapes bundle")


class MacOSSparkleAdapter:
    name = "macos-sparkle-2"

    def __init__(
        self,
        *,
        sparkle_public_key: str,
        sparkle_cli: Path,
        app_bundle: Path,
        run: Run = _run,
        launch: Launch = _launch,
        appcast_fetcher: Callable[[str], bytes] | None = None,
    ) -> None:
        self._public_key = sparkle_public_key
        self._sparkle_cli = Path(sparkle_cli)
        self._app_bundle = Path(app_bundle)
        self._run = run
        self._launch = launch
        self._appcast_fetcher = appcast_fetcher

    def doctor(self, installed: InstalledBuild) -> dict[str, object]:
        return {
            "available": self._sparkle_cli.is_file()
            and self._app_bundle.suffix == ".app",
            "name": self.name,
            "bundle": self._app_bundle.name,
            "publisher_identity": installed.publisher_identity,
            "mechanism": "Sparkle 2 external bundle updater",
        }

    def verify(self, artifact: Path, candidate: UpdateCandidate) -> AdapterVerification:
        try:
            public = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(self._public_key, validate=True)
            )
            signature = base64.b64decode(
                str(candidate.native.get("sparkle_ed_signature") or ""), validate=True
            )
            public.verify(signature, artifact.read_bytes())
        except (OSError, ValueError, InvalidSignature):
            return AdapterVerification(
                False, error_category="sparkle_signature_invalid"
            )
        try:
            with zipfile.ZipFile(artifact) as archive:
                _validate_sparkle_zip(archive)
                with tempfile.TemporaryDirectory(
                    prefix="vesta-sparkle-verify-"
                ) as temporary:
                    root = Path(temporary)
                    archive.extractall(root)
                    apps = [path for path in root.rglob("*.app") if path.is_dir()]
                    if len(apps) != 1:
                        return AdapterVerification(
                            False, error_category="native_signature_invalid"
                        )
                    app = apps[0]
                    commands = (
                        [
                            "/usr/bin/codesign",
                            "--verify",
                            "--deep",
                            "--strict",
                            str(app),
                        ],
                        ["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)],
                        ["/usr/bin/codesign", "-d", "--verbose=4", str(app)],
                    )
                    output = ""
                    for command in commands:
                        completed = self._run(command)
                        output += f"\n{completed.stdout}\n{completed.stderr}"
                        if completed.returncode != 0:
                            return AdapterVerification(
                                False, error_category="native_signature_invalid"
                            )
        except (OSError, ValueError, zipfile.BadZipFile):
            return AdapterVerification(False, error_category="native_signature_invalid")
        marker = f"TeamIdentifier={candidate.publisher_identity}"
        if marker not in output:
            return AdapterVerification(False, error_category="publisher_mismatch")
        return AdapterVerification(
            True,
            publisher_identity=candidate.publisher_identity,
            evidence=f"sparkle-eddsa+apple-team:{candidate.publisher_identity}",
        )

    def install(
        self, artifact: Path, candidate: UpdateCandidate, *, mode: str
    ) -> AdapterInstallResult:
        feed = str(candidate.native.get("appcast_url") or "")
        if not feed.startswith("https://"):
            return AdapterInstallResult(False, error_category="native_feed_invalid")
        expected_appcast = str(candidate.native.get("appcast_sha256") or "")
        try:
            appcast = (
                self._appcast_fetcher(feed)
                if self._appcast_fetcher is not None
                else fetch_manifest(
                    feed,
                    allowed_origins=parse_https_origins(
                        candidate.native.get("allowed_redirect_origins") or []
                    ),
                )
            )
            if (
                len(appcast) > 1024 * 1024
                or hashlib.sha256(appcast).hexdigest() != expected_appcast
            ):
                raise ValueError("appcast digest mismatch")
            root = ElementTree.fromstring(appcast)  # nosec B314
            enclosures = [
                element
                for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] == "enclosure"
            ]
            if len(enclosures) != 1:
                raise ValueError("appcast enclosure mismatch")
            enclosure = enclosures[0]
            attributes = {
                name.rsplit("}", 1)[-1]: value
                for name, value in enclosure.attrib.items()
            }
            if (
                enclosure.attrib.get("url") != candidate.artifact_url
                or attributes.get("version") != candidate.version
                or attributes.get("edSignature")
                != str(candidate.native.get("sparkle_ed_signature") or "")
                or int(enclosure.attrib.get("length") or -1) != candidate.artifact_size
            ):
                raise ValueError("appcast candidate mismatch")
        except (OSError, ValueError, ElementTree.ParseError):
            return AdapterInstallResult(False, error_category="native_feed_mismatch")
        command = [
            str(self._sparkle_cli),
            str(self._app_bundle),
            "--application",
            str(self._app_bundle),
            "--check-immediately",
            "--feed-url",
            feed,
            "--channels",
            candidate.channel,
            "--user-agent-name",
            "Vesta",
        ]
        if mode in {"on_quit", "when_idle"}:
            command.append("--defer-install")
        elif mode == "now":
            command.append("--interactive")
        try:
            process = self._launch(command)
        except (OSError, subprocess.SubprocessError):
            return AdapterInstallResult(False, error_category="installer_failed")
        return AdapterInstallResult(
            True, transaction_id=f"sparkle-pid-{int(process.pid)}"
        )

    def rollback(self, last_known_good: Mapping[str, Any]) -> AdapterInstallResult:
        artifact = Path(str(last_known_good.get("artifact_path") or ""))
        expected_sha = str(last_known_good.get("artifact_sha256") or "")
        version = str(last_known_good.get("version") or "")
        build_id = str(last_known_good.get("build_id") or "")
        publisher = str(last_known_good.get("publisher_identity") or "")
        try:
            valid = (
                artifact.is_file()
                and len(expected_sha) == 64
                and _sha256(artifact) == expected_sha
                and bool(version)
                and len(build_id) == 40
                and bool(publisher)
            )
        except OSError:
            valid = False
        if not valid:
            return AdapterInstallResult(
                False, error_category="rollback_artifact_unavailable"
            )
        helper = artifact.parent / "vesta-macos-recovery.sh"
        result_path = artifact.parent / "rollback-result.json"
        script = """#!/bin/bash
set -u
ARCHIVE="$1"
APPLICATION="$2"
EXPECTED_TEAM="$3"
EXPECTED_VERSION="$4"
EXPECTED_BUILD="$5"
RESULT="$6"
ROOT="$(/usr/bin/mktemp -d -t vesta-recovery)" || exit 1
BACKUP="${APPLICATION}.vesta-recovery-backup-$$"
finish() {
  /bin/rm -rf "$ROOT"
}
trap finish EXIT
write_failure() {
  /usr/bin/printf '%s\n' '{"schema_version":1,"success":false,"error_category":"rollback_failed"}' > "${RESULT}.tmp"
  /bin/mv -f "${RESULT}.tmp" "$RESULT"
  /usr/bin/open "$APPLICATION" >/dev/null 2>&1 || true
  exit 1
}
/usr/bin/ditto -x -k "$ARCHIVE" "$ROOT" || write_failure
APPS=("$ROOT"/*.app)
[ "${#APPS[@]}" -eq 1 ] || write_failure
CANDIDATE="${APPS[0]}"
/usr/bin/codesign --verify --deep --strict "$CANDIDATE" || write_failure
DETAILS="$(/usr/bin/codesign -d --verbose=4 "$CANDIDATE" 2>&1)"
/usr/bin/grep -F "TeamIdentifier=${EXPECTED_TEAM}" <<<"$DETAILS" >/dev/null || write_failure
IDENTITY="$CANDIDATE/Contents/Resources/release-identity.json"
[ -f "$IDENTITY" ] || write_failure
[ "$(/usr/bin/plutil -extract version raw -o - "$IDENTITY")" = "$EXPECTED_VERSION" ] || write_failure
[ "$(/usr/bin/plutil -extract build_id raw -o - "$IDENTITY")" = "$EXPECTED_BUILD" ] || write_failure
/usr/bin/osascript -e 'tell application "Vesta" to quit' >/dev/null 2>&1 || true
for _attempt in 1 2 3 4 5; do
  /usr/bin/pgrep -x Vesta >/dev/null 2>&1 || break
  /bin/sleep 1
done
/usr/bin/pkill -TERM -x Vesta >/dev/null 2>&1 || true
/bin/mv "$APPLICATION" "$BACKUP" || write_failure
if ! /bin/mv "$CANDIDATE" "$APPLICATION"; then
  /bin/mv "$BACKUP" "$APPLICATION" || true
  write_failure
fi
/bin/rm -rf "$BACKUP"
/usr/bin/printf '%s\n' '{"schema_version":1,"success":true,"error_category":""}' > "${RESULT}.tmp"
/bin/mv -f "${RESULT}.tmp" "$RESULT"
/usr/bin/open "$APPLICATION"
"""
        try:
            atomic_write_text(helper, script, mode=0o700)
            process = self._launch(
                [
                    "/bin/bash",
                    str(helper),
                    str(artifact.resolve()),
                    str(self._app_bundle.resolve()),
                    publisher,
                    version,
                    build_id,
                    str(result_path.resolve()),
                ]
            )
        except (OSError, subprocess.SubprocessError):
            return AdapterInstallResult(False, error_category="rollback_failed")
        return AdapterInstallResult(
            True,
            transaction_id=f"sparkle-rollback-pid-{int(process.pid)}",
            result_path=str(result_path),
        )
