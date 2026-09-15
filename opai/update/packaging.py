"""Native package layout preparation for the protected release workflow."""

from __future__ import annotations

import json
import plistlib
import re
import shutil
import subprocess  # nosec B404
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree  # nosec B405

from opaihub.atomic_io import atomic_write_text
from opaihub.proc import no_window_kwargs

from opai.compatibility import validate_compatibility_coordinates
from opai.release_identity import artifact_identity_payload
from .models import InstallType
from .release import ReleaseError, msix_version


def validate_trust_store(value: Mapping[str, Any]) -> dict[str, Any]:
    trust = dict(value)
    keys = trust.get("keys")
    threshold = trust.get("threshold")
    if (
        trust.get("schema_version") != 1
        or not str(trust.get("feed_url") or "").startswith("https://")
        or not isinstance(keys, list)
        or not isinstance(threshold, int)
        or isinstance(threshold, bool)
        or threshold < 1
    ):
        raise ReleaseError("packaged update trust is invalid")
    active = [
        item
        for item in keys
        if isinstance(item, Mapping)
        and item.get("revoked") is not True
        and item.get("key_id")
        and item.get("public_key")
    ]
    if len(active) < threshold:
        raise ReleaseError("packaged update trust has too few active keys")
    return trust


def runtime_identity(
    *,
    version: str,
    build_id: str,
    channel: str,
    platform: str,
    architecture: str,
    install_type: InstallType,
    package_identity: str,
    publisher_identity: str,
    assets: Mapping[str, Any],
    candidate_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", build_id):
        raise ReleaseError("runtime build ID must be an exact commit SHA")
    msix_version(version)
    if channel not in {"stable", "beta", "alpha"}:
        raise ReleaseError("runtime release channel is unsupported")
    asset_identity = dict(assets)
    if (
        asset_identity.get("schema_version") != 1
        or asset_identity.get("application_version") != version
        or not isinstance(asset_identity.get("asset_count"), int)
        or isinstance(asset_identity.get("asset_count"), bool)
        or int(asset_identity["asset_count"]) < 1
        or re.fullmatch(
            r"[0-9a-f]{64}", str(asset_identity.get("fingerprint_sha256") or "")
        )
        is None
    ):
        raise ReleaseError("runtime asset identity is invalid")
    if candidate_identity is None:
        identity = artifact_identity_payload(
            build_id=build_id,
            assets=asset_identity,
            platform_name=platform,
            architecture=architecture,
            install_type=InstallType(install_type).value,
        )
        if (
            version != identity["application_version"]
            or channel != identity["release_channel"]
        ):
            raise ReleaseError(
                "runtime version/channel does not match canonical application identity"
            )
    else:
        identity = dict(candidate_identity)
        if any(
            (
                identity.get("application_version") != version,
                identity.get("build_id") != build_id,
                identity.get("release_channel") != channel,
                identity.get("assets") != asset_identity,
            )
        ):
            raise ReleaseError(
                "candidate runtime identity conflicts with requested package fields"
            )
        normalized_platform = str(platform).casefold()
        if normalized_platform == "darwin":
            normalized_platform = "macos"
        normalized_architecture = str(architecture).casefold()
        normalized_architecture = {
            "amd64": "x86_64",
            "x64": "x86_64",
            "aarch64": "arm64",
        }.get(normalized_architecture, normalized_architecture)
        identity.update(
            {
                "platform": normalized_platform,
                "architecture": normalized_architecture,
                "install_type": InstallType(install_type).value,
            }
        )
    compatibility = identity.get("compatibility")
    if not isinstance(compatibility, Mapping):
        raise ReleaseError("runtime compatibility identity is invalid")
    try:
        compatibility = validate_compatibility_coordinates(compatibility)
    except RuntimeError as exc:
        raise ReleaseError("runtime compatibility identity is invalid") from exc
    return {
        **identity,
        "version": version,
        "channel": channel,
        "package_identity": package_identity,
        "publisher_identity": publisher_identity,
        "updater_protocol_version": compatibility["updater_protocol_version"],
    }


def _runtime_resource_root(bundle: Path, install_type: InstallType) -> Path:
    if install_type is InstallType.WINDOWS_MSIX:
        return bundle
    if install_type is InstallType.MACOS_SPARKLE:
        apps = [path for path in (bundle / "gui").glob("*.app") if path.is_dir()]
        if len(apps) != 1:
            raise ReleaseError("macOS bundle must contain exactly one app")
        return apps[0] / "Contents" / "Resources"
    raise ReleaseError("runtime configuration supports only packaged install types")


def write_runtime_configuration(
    bundle: Path,
    *,
    identity: Mapping[str, Any],
    trust: Mapping[str, Any],
) -> tuple[Path, Path]:
    root = _runtime_resource_root(
        Path(bundle).resolve(), InstallType(identity["install_type"])
    )
    root.mkdir(parents=True, exist_ok=True)
    validated = validate_trust_store(trust)
    identity_path = root / "release-identity.json"
    trust_path = root / "update-trust.json"
    atomic_write_text(
        identity_path, json.dumps(dict(identity), indent=2, sort_keys=True) + "\n"
    )
    atomic_write_text(
        trust_path, json.dumps(validated, indent=2, sort_keys=True) + "\n"
    )
    return identity_path, trust_path


def render_msix_manifest(
    *,
    package_identity: str,
    publisher_identity: str,
    version: str,
    architecture: str,
) -> str:
    foundation = "http://schemas.microsoft.com/appx/manifest/foundation/windows10"
    uap = "http://schemas.microsoft.com/appx/manifest/uap/windows10"
    uap3 = "http://schemas.microsoft.com/appx/manifest/uap/windows10/3"
    desktop = "http://schemas.microsoft.com/appx/manifest/desktop/windows10"
    rescap = "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
    for prefix, namespace in (
        ("", foundation),
        ("uap", uap),
        ("uap3", uap3),
        ("desktop", desktop),
        ("rescap", rescap),
    ):
        ElementTree.register_namespace(prefix, namespace)
    package = ElementTree.Element(
        f"{{{foundation}}}Package",
        {"IgnorableNamespaces": "uap uap3 desktop rescap"},
    )
    ElementTree.SubElement(
        package,
        f"{{{foundation}}}Identity",
        {
            "Name": package_identity,
            "Publisher": publisher_identity,
            "Version": msix_version(version),
            "ProcessorArchitecture": "x64"
            if architecture == "x86_64"
            else architecture,
        },
    )
    properties = ElementTree.SubElement(package, f"{{{foundation}}}Properties")
    ElementTree.SubElement(properties, f"{{{foundation}}}DisplayName").text = "Vesta"
    ElementTree.SubElement(
        properties, f"{{{foundation}}}PublisherDisplayName"
    ).text = "Vesta"
    ElementTree.SubElement(
        properties, f"{{{foundation}}}Logo"
    ).text = "Assets\\StoreLogo.png"
    dependencies = ElementTree.SubElement(package, f"{{{foundation}}}Dependencies")
    ElementTree.SubElement(
        dependencies,
        f"{{{foundation}}}TargetDeviceFamily",
        {
            "Name": "Windows.Desktop",
            "MinVersion": "10.0.17763.0",
            "MaxVersionTested": "10.0.26100.0",
        },
    )
    resources = ElementTree.SubElement(package, f"{{{foundation}}}Resources")
    ElementTree.SubElement(
        resources, f"{{{foundation}}}Resource", {"Language": "en-us"}
    )
    applications = ElementTree.SubElement(package, f"{{{foundation}}}Applications")
    application = ElementTree.SubElement(
        applications,
        f"{{{foundation}}}Application",
        {
            "Id": "OPai",
            "Executable": "gui\\OPai.exe",
            "EntryPoint": "Windows.FullTrustApplication",
        },
    )
    visual = ElementTree.SubElement(
        application,
        f"{{{uap}}}VisualElements",
        {
            "DisplayName": "Vesta",
            "Description": "Vesta desktop",
            "BackgroundColor": "transparent",
            "Square150x150Logo": "Assets\\Square150x150Logo.png",
            "Square44x44Logo": "Assets\\Square44x44Logo.png",
        },
    )
    ElementTree.SubElement(
        visual,
        f"{{{uap}}}DefaultTile",
        {"Wide310x150Logo": "Assets\\Wide310x150Logo.png"},
    )
    extensions = ElementTree.SubElement(application, f"{{{foundation}}}Extensions")
    alias_extension = ElementTree.SubElement(
        extensions,
        f"{{{uap3}}}Extension",
        {
            "Category": "windows.appExecutionAlias",
            "Executable": "cli\\opai.exe",
            "EntryPoint": "Windows.FullTrustApplication",
        },
    )
    aliases = ElementTree.SubElement(alias_extension, f"{{{uap3}}}AppExecutionAlias")
    ElementTree.SubElement(
        aliases, f"{{{desktop}}}ExecutionAlias", {"Alias": "opai.exe"}
    )
    capabilities = ElementTree.SubElement(package, f"{{{foundation}}}Capabilities")
    ElementTree.SubElement(
        capabilities, f"{{{rescap}}}Capability", {"Name": "runFullTrust"}
    )
    return (
        ElementTree.tostring(package, encoding="unicode", xml_declaration=True) + "\n"
    )


def prepare_msix_layout(
    bundle: Path,
    layout: Path,
    *,
    package_identity: str,
    publisher_identity: str,
    version: str,
    architecture: str,
    assets: Path,
) -> Path:
    source = Path(bundle).resolve()
    target = Path(layout).resolve()
    if target.exists() and any(target.iterdir()):
        raise ReleaseError("refusing to overwrite non-empty MSIX layout")
    if (
        not (source / "gui" / "OPai.exe").is_file()
        or not (source / "cli" / "opai.exe").is_file()
    ):
        raise ReleaseError("Windows bundle is missing GUI or CLI executable")
    shutil.copytree(source, target, dirs_exist_ok=True)
    asset_target = target / "Assets"
    shutil.copytree(Path(assets).resolve(), asset_target, dirs_exist_ok=True)
    required = {
        "StoreLogo.png",
        "Square150x150Logo.png",
        "Square44x44Logo.png",
        "Wide310x150Logo.png",
    }
    if not required.issubset(
        {path.name for path in asset_target.iterdir() if path.is_file()}
    ):
        raise ReleaseError("MSIX visual assets are incomplete")
    manifest = target / "AppxManifest.xml"
    atomic_write_text(
        manifest,
        render_msix_manifest(
            package_identity=package_identity,
            publisher_identity=publisher_identity,
            version=version,
            architecture=architecture,
        ),
    )
    return manifest


def make_msix(layout: Path, output: Path, *, makeappx: Path) -> Path:
    destination = Path(output).resolve()
    if destination.exists():
        raise ReleaseError("refusing to overwrite existing MSIX")
    tool = Path(makeappx).resolve()
    if not tool.is_file():
        raise ReleaseError("makeappx.exe is unavailable")
    # makeappx is a trusted Windows SDK binary invoked with a fixed argv shape.
    completed = subprocess.run(  # nosec B603
        [
            str(tool),
            "pack",
            "/d",
            str(Path(layout).resolve()),
            "/p",
            str(destination),
            "/o",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=600,
        **no_window_kwargs(),
    )
    if completed.returncode != 0 or not destination.is_file():
        raise ReleaseError("makeappx failed to create the MSIX package")
    return destination


def prepare_macos_sparkle_bundle(
    bundle: Path,
    *,
    sparkle_app: Path,
    version: str,
    feed_url: str,
    sparkle_public_key: str,
) -> Path:
    source = Path(bundle).resolve()
    apps = [path for path in (source / "gui").glob("*.app") if path.is_dir()]
    if len(apps) != 1:
        raise ReleaseError("macOS bundle must contain exactly one app")
    app = apps[0]
    cli_candidates = [
        path
        for path in (source / "cli").iterdir()
        if path.is_file() and path.name == "opai"
    ]
    if len(cli_candidates) != 1:
        raise ReleaseError("macOS bundle is missing its CLI executable")
    helper = Path(sparkle_app).resolve()
    executable = helper / "Contents" / "MacOS" / "sparkle"
    if helper.suffix != ".app" or not executable.is_file():
        raise ReleaseError("Sparkle 2 command-line helper bundle is invalid")
    resources = app / "Contents" / "Resources"
    updater = resources / "OPaiUpdater"
    updater.mkdir(parents=True, exist_ok=True)
    shutil.copytree(helper, updater / "sparkle.app", dirs_exist_ok=False)
    shutil.copy2(cli_candidates[0], resources / "opai")
    plist = app / "Contents" / "Info.plist"
    try:
        with plist.open("rb") as stream:
            value = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ReleaseError("Vesta app has no valid Info.plist") from exc
    value["CFBundleShortVersionString"] = version
    value["CFBundleVersion"] = msix_version(version)
    value["SUFeedURL"] = feed_url
    value["SUPublicEDKey"] = sparkle_public_key
    value["SUEnableAutomaticChecks"] = False
    with plist.open("wb") as stream:
        plistlib.dump(value, stream, sort_keys=True)
    return app
