"""Deterministic generation of signed update metadata from final artifacts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree  # nosec B405

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from packaging.version import InvalidVersion, Version

from opaihub.atomic_io import atomic_write_text

from .models import InstallType, UpdateCandidate
from .native import _read_msix_identity, _validate_sparkle_zip
from .network import parse_https_origins


class ReleaseError(ValueError):
    """A safe release-pipeline validation failure."""


@dataclass(frozen=True)
class ReleaseArtifact:
    path: Path
    url: str
    platform: str
    architecture: str
    install_type: InstallType
    publisher_identity: str
    package_identity: str
    native: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).expanduser().resolve())
        object.__setattr__(self, "install_type", InstallType(self.install_type))
        object.__setattr__(self, "native", dict(self.native))


@dataclass(frozen=True)
class ReleaseFiles:
    manifest: Path
    trust: Path
    appinstaller: Path
    appcast: Path
    inventory: Path


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ReleaseError("release timestamps must include a timezone")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseError("final signed artifact is unreadable") from exc
    return digest.hexdigest()


def _https(value: str, *, label: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ReleaseError(f"{label} must be an HTTPS URL without credentials")
    return value.rstrip("/")


def msix_version(value: str) -> str:
    """Map a PEP 440 package version to a monotonic four-part MSIX version."""

    try:
        version = Version(value)
    except InvalidVersion as exc:
        raise ReleaseError("release version is not valid PEP 440") from exc
    if version.dev is not None or version.post is not None or version.local is not None:
        raise ReleaseError(
            "release version cannot contain dev, post, or local metadata"
        )
    release = tuple(version.release)
    if len(release) > 3 or any(part > 65535 for part in release):
        raise ReleaseError("release version cannot be represented by MSIX")
    major, minor, patch = (release + (0, 0, 0))[:3]
    if version.pre is None:
        revision = 65535
    else:
        phase, number = version.pre
        base = {"a": 1000, "b": 2000, "rc": 3000}.get(phase)
        if base is None or number > 999:
            raise ReleaseError("prerelease cannot be represented by MSIX")
        revision = base + number
    return f"{major}.{minor}.{patch}.{revision}"


def _embedded_identity(artifact: ReleaseArtifact) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(artifact.path) as archive:
            if artifact.install_type is InstallType.WINDOWS_MSIX:
                names = [
                    name
                    for name in archive.namelist()
                    if name == "release-identity.json"
                ]
            else:
                names = [
                    name
                    for name in archive.namelist()
                    if name.endswith(".app/Contents/Resources/release-identity.json")
                ]
            if len(names) != 1 or archive.getinfo(names[0]).file_size > 64 * 1024:
                raise ReleaseError("artifact runtime identity is missing or ambiguous")
            value = json.loads(archive.read(names[0]))
    except (
        OSError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as exc:
        raise ReleaseError("artifact runtime identity is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ReleaseError("artifact runtime identity is invalid")
    return value


def _validate_artifact(
    artifact: ReleaseArtifact,
    *,
    sparkle_public_key: str,
    version: str,
    build_id: str,
    channel: str,
) -> None:
    if not artifact.path.is_file() or artifact.path.is_symlink():
        raise ReleaseError("final signed artifact is missing or linked")
    _https(artifact.url, label="artifact URL")
    if Path(urlparse(artifact.url).path).name != artifact.path.name:
        raise ReleaseError("artifact URL basename does not match final artifact")
    if artifact.install_type is InstallType.WINDOWS_MSIX:
        try:
            package_name, publisher, _application_id = _read_msix_identity(
                artifact.path
            )
        except ValueError as exc:
            raise ReleaseError("MSIX identity is invalid") from exc
        if (
            package_name != artifact.package_identity
            or publisher != artifact.publisher_identity
        ):
            raise ReleaseError("MSIX identity does not match release configuration")
        thumbprint = str(artifact.native.get("windows_signer_thumbprint") or "")
        if len(thumbprint.replace(" ", "")) not in {40, 64}:
            raise ReleaseError("Windows signer thumbprint is missing")
    elif artifact.install_type is InstallType.MACOS_SPARKLE:
        signature = str(artifact.native.get("sparkle_ed_signature") or "")
        try:
            public = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(sparkle_public_key, validate=True)
            )
            public.verify(
                base64.b64decode(signature, validate=True), artifact.path.read_bytes()
            )
            with zipfile.ZipFile(artifact.path) as archive:
                _validate_sparkle_zip(archive)
        except (OSError, ValueError, InvalidSignature, zipfile.BadZipFile) as exc:
            raise ReleaseError(
                "Sparkle archive signature or structure is invalid"
            ) from exc
    else:
        raise ReleaseError("only MSIX and Sparkle release artifacts are publishable")
    identity = _embedded_identity(artifact)
    expected = {
        "version": version,
        "build_id": build_id,
        "channel": channel,
        "platform": artifact.platform,
        "architecture": artifact.architecture,
        "install_type": artifact.install_type.value,
        "package_identity": artifact.package_identity,
        "publisher_identity": artifact.publisher_identity,
    }
    if any(identity.get(key) != value for key, value in expected.items()):
        raise ReleaseError(
            "artifact runtime identity does not match signed release metadata"
        )


def _validated_recovery(
    artifact: ReleaseArtifact,
    *,
    target_version: str,
    channel: str,
) -> dict[str, Any] | None:
    """Validate the complete prior signed candidate embedded for native rollback."""

    raw = artifact.native.get("recovery")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ReleaseError("recovery candidate is invalid")
    try:
        recovery = UpdateCandidate.from_dict(raw)
        if Version(recovery.version) >= Version(target_version):
            raise ReleaseError("recovery candidate must precede the target release")
    except (TypeError, ValueError, InvalidVersion) as exc:
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError("recovery candidate is invalid") from exc
    recovery_url = urlparse(recovery.artifact_url)
    if (
        recovery_url.scheme != "https"
        or not recovery_url.hostname
        or recovery_url.username
        or recovery_url.password
        or recovery.channel != channel
        or recovery.platform.casefold() != artifact.platform.casefold()
        or recovery.architecture.casefold() != artifact.architecture.casefold()
        or recovery.install_type is not artifact.install_type
        or recovery.publisher_identity != artifact.publisher_identity
        or recovery.artifact_url == artifact.url
        or recovery.native.get("recovery") is not None
    ):
        raise ReleaseError("recovery candidate does not match the target artifact")
    if artifact.install_type is InstallType.WINDOWS_MSIX:
        if (
            recovery.native.get("package_name") != artifact.package_identity
            or not recovery.native.get("application_id")
            or not recovery.native.get("windows_signer_thumbprint")
        ):
            raise ReleaseError("Windows recovery identity is incomplete")
    elif (
        recovery.native.get("package_identity") != artifact.package_identity
        or not recovery.native.get("sparkle_ed_signature")
        or not recovery.native.get("appcast_url")
        or len(str(recovery.native.get("appcast_sha256") or "")) != 64
    ):
        raise ReleaseError("macOS recovery identity is incomplete")
    return recovery.to_dict()


def _appinstaller_xml(artifact: ReleaseArtifact, *, version: str, self_url: str) -> str:
    namespace = "http://schemas.microsoft.com/appx/appinstaller/2018"
    ElementTree.register_namespace("", namespace)
    root = ElementTree.Element(
        f"{{{namespace}}}AppInstaller",
        {"Uri": self_url, "Version": msix_version(version)},
    )
    ElementTree.SubElement(
        root,
        f"{{{namespace}}}MainPackage",
        {
            "Name": artifact.package_identity,
            "Publisher": artifact.publisher_identity,
            "Version": msix_version(version),
            "ProcessorArchitecture": (
                "x64" if artifact.architecture == "x86_64" else artifact.architecture
            ),
            "Uri": artifact.url,
        },
    )
    settings = ElementTree.SubElement(root, f"{{{namespace}}}UpdateSettings")
    ElementTree.SubElement(
        settings,
        f"{{{namespace}}}OnLaunch",
        {
            "HoursBetweenUpdateChecks": "4",
            "ShowPrompt": "true",
            "UpdateBlocksActivation": "false",
        },
    )
    return ElementTree.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def _appcast_xml(
    artifact: ReleaseArtifact,
    *,
    version: str,
    channel: str,
    title: str,
    notes_url: str,
) -> str:
    sparkle = "http://www.andymatuschak.org/xml-namespaces/sparkle"
    ElementTree.register_namespace("sparkle", sparkle)
    rss = ElementTree.Element("rss", {"version": "2.0"})
    channel_node = ElementTree.SubElement(rss, "channel")
    ElementTree.SubElement(channel_node, "title").text = "Vesta updates"
    item = ElementTree.SubElement(channel_node, "item")
    ElementTree.SubElement(item, "title").text = title
    ElementTree.SubElement(item, f"{{{sparkle}}}releaseNotesLink").text = notes_url
    ElementTree.SubElement(item, f"{{{sparkle}}}channel").text = channel
    enclosure = {
        "url": artifact.url,
        "length": str(artifact.path.stat().st_size),
        "type": "application/octet-stream",
        f"{{{sparkle}}}version": msix_version(version),
        f"{{{sparkle}}}shortVersionString": version,
        f"{{{sparkle}}}edSignature": str(artifact.native["sparkle_ed_signature"]),
    }
    minimum_os = str(artifact.native.get("minimum_os_version") or "")
    if minimum_os:
        enclosure[f"{{{sparkle}}}minimumSystemVersion"] = minimum_os
    ElementTree.SubElement(item, "enclosure", enclosure)
    return ElementTree.tostring(rss, encoding="unicode", xml_declaration=True) + "\n"


def generate_release_files(
    *,
    output: Path,
    artifacts: Sequence[ReleaseArtifact],
    version: str,
    build_id: str,
    channel: str,
    metadata_version: int,
    generated_at: datetime,
    expires_at: datetime,
    feed_base_url: str,
    release_id: str,
    release_title: str,
    release_notes: str,
    release_notes_url: str,
    metadata_keys: Mapping[str, Ed25519PrivateKey],
    metadata_public_keys: Mapping[str, str],
    signature_threshold: int,
    sparkle_public_key: str,
    rollout_percentage: int = 100,
    cohort_start: int = 0,
    minimum_current_version: str = "",
    maximum_current_version: str = "",
    criticality: str = "normal",
    required_after: str = "",
    revoked_key_ids: Sequence[str] = (),
    native_feed_base_url: str = "",
    native_feed_suffix: str = "",
    feed_redirect_origins: Sequence[str] = (),
) -> ReleaseFiles:
    """Generate native feeds and publish the signed manifest last on disk."""

    destination = Path(output).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ReleaseError("refusing to overwrite a non-empty feed directory")
    if channel not in {"stable", "beta", "alpha"}:
        raise ReleaseError("release channel is unsupported")
    if (
        not isinstance(metadata_version, int)
        or isinstance(metadata_version, bool)
        or metadata_version < 1
    ):
        raise ReleaseError("metadata version must be positive")
    if expires_at <= generated_at:
        raise ReleaseError("metadata expiry must follow generation")
    if not artifacts:
        raise ReleaseError("release has no final artifacts")
    if not metadata_keys or not 1 <= signature_threshold <= len(metadata_keys):
        raise ReleaseError("signature threshold is invalid")
    if not set(metadata_keys).issubset(metadata_public_keys):
        raise ReleaseError("metadata signing key is absent from public trust")
    revoked = set(revoked_key_ids)
    if not revoked.issubset(metadata_public_keys) or revoked.intersection(
        metadata_keys
    ):
        raise ReleaseError("metadata key revocation set is invalid")
    if len(set(metadata_public_keys).difference(revoked)) < signature_threshold:
        raise ReleaseError("metadata public trust has too few active keys")
    feed = _https(feed_base_url, label="feed base URL")
    native_feed = (
        _https(native_feed_base_url, label="native feed base URL")
        if native_feed_base_url
        else feed
    )
    if native_feed_suffix and (
        len(native_feed_suffix) > 64
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_"
            for character in native_feed_suffix
        )
    ):
        raise ReleaseError("native feed suffix is unsafe")
    try:
        parsed_feed_redirects = parse_https_origins(feed_redirect_origins)
    except ValueError as exc:
        raise ReleaseError("feed redirect origin is invalid") from exc
    _https(release_notes_url, label="release notes URL")
    msix_version(version)
    if len(build_id) != 40 or any(
        character not in "0123456789abcdef" for character in build_id
    ):
        raise ReleaseError("build ID must be an exact lowercase commit SHA")
    for key_id, encoded in metadata_public_keys.items():
        try:
            public_bytes = base64.b64decode(encoded, validate=True)
            Ed25519PublicKey.from_public_bytes(public_bytes)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ReleaseError("metadata public key is invalid") from exc
        if not key_id or len(key_id) > 128:
            raise ReleaseError("metadata key ID is invalid")
    for key_id, private in metadata_keys.items():
        public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        if base64.b64encode(public).decode() != metadata_public_keys[key_id]:
            raise ReleaseError("metadata signing key does not match its public key")

    for artifact in artifacts:
        _validate_artifact(
            artifact,
            sparkle_public_key=sparkle_public_key,
            version=version,
            build_id=build_id,
            channel=channel,
        )
    windows = [
        item for item in artifacts if item.install_type is InstallType.WINDOWS_MSIX
    ]
    macos = [
        item for item in artifacts if item.install_type is InstallType.MACOS_SPARKLE
    ]
    if len(windows) != 1 or len(macos) != 1:
        raise ReleaseError(
            "release must contain exactly one MSIX and one Sparkle artifact"
        )

    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "manifest.json"
    trust_path = destination / "update-trust.json"
    suffix = f"-{native_feed_suffix}" if native_feed_suffix else ""
    appinstaller_path = destination / f"OPai{suffix}.appinstaller"
    appcast_path = destination / f"appcast{suffix}.xml"
    inventory_path = destination / "release-inventory.json"
    appinstaller_url = urljoin(native_feed + "/", appinstaller_path.name)
    appcast_url = urljoin(native_feed + "/", appcast_path.name)

    candidates: list[dict[str, Any]] = []
    published = _iso(generated_at)
    for artifact in artifacts:
        native = dict(artifact.native)
        recovery = _validated_recovery(
            artifact,
            target_version=version,
            channel=channel,
        )
        if artifact.install_type is InstallType.WINDOWS_MSIX:
            _package_name, _publisher, application_id = _read_msix_identity(
                artifact.path
            )
            native.update(
                {
                    "appinstaller_url": appinstaller_url,
                    "package_name": artifact.package_identity,
                    "application_id": application_id,
                }
            )
        else:
            native["appcast_url"] = appcast_url
            native["package_identity"] = artifact.package_identity
        candidates.append(
            {
                "version": version,
                "build_id": build_id,
                "channel": channel,
                "release_id": release_id,
                "published_at": published,
                "platform": artifact.platform,
                "architecture": artifact.architecture,
                "install_type": artifact.install_type.value,
                "artifact_url": artifact.url,
                "artifact_sha256": _sha256(artifact.path),
                "artifact_size": artifact.path.stat().st_size,
                "publisher_identity": artifact.publisher_identity,
                "metadata_key_ids": sorted(metadata_keys),
                "release_title": release_title,
                "release_notes": release_notes,
                "release_notes_url": release_notes_url,
                "minimum_current_version": minimum_current_version,
                "maximum_current_version": maximum_current_version,
                "minimum_os_version": str(
                    artifact.native.get("minimum_os_version") or ""
                ),
                "criticality": criticality,
                "required_after": required_after,
                "rollout_percentage": rollout_percentage,
                "cohort_start": cohort_start,
                "minimum_updater_protocol": 1,
                "rollback_compatible": recovery is not None,
                "native": native,
            }
        )

    atomic_write_text(
        appinstaller_path,
        _appinstaller_xml(windows[0], version=version, self_url=appinstaller_url),
    )
    atomic_write_text(
        appcast_path,
        _appcast_xml(
            macos[0],
            version=version,
            channel=channel,
            title=release_title,
            notes_url=release_notes_url,
        ),
    )
    appcast_sha256 = _sha256(appcast_path)
    for candidate in candidates:
        if candidate["install_type"] == InstallType.MACOS_SPARKLE.value:
            candidate["native"]["appcast_sha256"] = appcast_sha256
    trust = {
        "schema_version": 1,
        "threshold": signature_threshold,
        "feed_url": urljoin(feed + "/", manifest_path.name),
        "sparkle_public_key": sparkle_public_key,
        "feed_redirect_origins": [
            f"https://{host}{f':{port}' if port is not None else ''}"
            for _scheme, host, port in parsed_feed_redirects
        ],
        "keys": [
            {
                "key_id": key_id,
                "public_key": metadata_public_keys[key_id],
                "revoked": key_id in revoked,
            }
            for key_id in sorted(metadata_public_keys)
        ],
    }
    atomic_write_text(trust_path, json.dumps(trust, indent=2, sort_keys=True) + "\n")

    signed = {
        "schema_version": 1,
        "metadata_version": metadata_version,
        "generated_at": published,
        "expires_at": _iso(expires_at),
        "channel": channel,
        "releases": candidates,
    }
    signatures = [
        {
            "key_id": key_id,
            "signature": base64.b64encode(private.sign(_canonical(signed))).decode(),
        }
        for key_id, private in sorted(metadata_keys.items())
    ]
    envelope = {"signed": signed, "signatures": signatures}

    publication_order = [
        *(artifact.url for artifact in artifacts),
        appinstaller_url,
        appcast_url,
        urljoin(feed + "/", manifest_path.name),
    ]
    inventory = {
        "schema_version": 1,
        "release_id": release_id,
        "build_id": build_id,
        "metadata_version": metadata_version,
        "artifact_urls": [artifact.url for artifact in artifacts],
        "publication_order": publication_order,
        "manifest_sha256": hashlib.sha256(_canonical(envelope)).hexdigest(),
    }
    atomic_write_text(
        inventory_path, json.dumps(inventory, indent=2, sort_keys=True) + "\n"
    )
    # This is intentionally last: clients never observe metadata for artifacts
    # that have not already been staged and made addressable.
    atomic_write_text(
        manifest_path, json.dumps(envelope, indent=2, sort_keys=True) + "\n"
    )
    return ReleaseFiles(
        manifest=manifest_path,
        trust=trust_path,
        appinstaller=appinstaller_path,
        appcast=appcast_path,
        inventory=inventory_path,
    )
