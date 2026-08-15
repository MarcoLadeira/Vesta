"""Production composition root for the one OPai update service."""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Callable, Iterable, Mapping

from opai import __version__

from .identity import detect_install_type
from .models import InstallType, InstalledBuild
from .adapters import DeveloperGitUpdateAdapter, UnsupportedUpdateAdapter
from .download import SecureDownloader
from .native import MacOSSparkleAdapter, WindowsMsixAdapter
from .network import fetch_manifest, parse_https_origins
from .runtime import probe_active_work
from .service import UpdateService
from .storage import UpdateStore, UpdaterPaths


def _read_object(path: Path) -> dict[str, object]:
    try:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _nearby(name: str) -> tuple[Path, ...]:
    starts = [Path(sys.executable).resolve(strict=False), Path(__file__).resolve()]
    candidates: list[Path] = []
    for start in starts:
        directory = start if start.is_dir() else start.parent
        for parent in (directory, *tuple(directory.parents)[:6]):
            candidates.extend(
                (
                    parent / name,
                    parent / "Resources" / name,
                    parent / "resources" / name,
                    parent / "opai" / "update" / name,
                )
            )
    return tuple(dict.fromkeys(candidates))


def _architecture() -> str:
    machine = platform.machine().casefold()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(
        machine, machine
    )


def load_installed_build(
    *, identity_paths: Iterable[Path] | None = None
) -> InstalledBuild:
    paths = (
        tuple(identity_paths)
        if identity_paths is not None
        else _nearby("release-identity.json")
    )
    value = next((item for path in paths if (item := _read_object(path))), {})
    install_type = detect_install_type()
    if value:
        try:
            install_type = InstallType(
                str(value.get("install_type") or install_type.value)
            )
        except ValueError:
            install_type = InstallType.UNKNOWN
    system = platform.system().casefold()
    platform_name = "macos" if system == "darwin" else system
    return InstalledBuild(
        version=str(value.get("version") or __version__),
        build_id=str(value.get("build_id") or value.get("commit") or "development"),
        channel=str(value.get("channel") or "stable"),
        platform=str(value.get("platform") or platform_name),
        architecture=str(value.get("architecture") or _architecture()),
        install_type=install_type,
        package_identity=str(value.get("package_identity") or ""),
        publisher_identity=str(value.get("publisher_identity") or ""),
        artifact_sha256=str(value.get("artifact_sha256") or ""),
    )


def load_trust_store(*, paths: Iterable[Path] | None = None) -> dict[str, object]:
    candidates = tuple(paths) if paths is not None else _nearby("update-trust.json")
    value = next((item for path in candidates if (item := _read_object(path))), {})
    if value.get("schema_version") != 1:
        return {}
    feed = str(value.get("feed_url") or "")
    if not feed.startswith("https://"):
        return {}
    return value


def _managed_policy_paths() -> tuple[Path, ...]:
    if platform.system().casefold() == "windows":
        base = Path(os.environ.get("ProgramData") or "C:/ProgramData")
        return (base / "OPai" / "update-policy.json",)
    if platform.system().casefold() == "darwin":
        return (Path("/Library/Managed Preferences/com.opai.desktop.update.json"),)
    return (Path("/etc/opai/update-policy.json"),)


def load_managed_update_configuration(
    *, paths: Iterable[Path] | None = None
) -> dict[str, object]:
    """Load machine-administered policy from fixed privileged locations only."""

    aliases = {
        "disableUpdateChecks": "disable_update_checks",
        "disableAutoUpdates": "disable_auto_updates",
        "automaticDownloads": "automatic_downloads",
        "automaticInstallOnQuit": "automatic_install_on_quit",
        "allowedReleaseChannel": "channel",
        "updateOwner": "owner",
        "maximumDeferralHours": "maximum_deferral_hours",
        "mandatoryInstallAfter": "mandatory_install_after",
        "feedOverride": "feed_url",
    }
    for path in tuple(paths) if paths is not None else _managed_policy_paths():
        try:
            if (
                not path.is_file()
                or path.is_symlink()
                or (hasattr(path, "is_junction") and path.is_junction())
            ):
                continue
            if path.stat().st_size > 64 * 1024:
                continue
        except OSError:
            continue
        raw = _read_object(path)
        if raw.get("schema_version") != 1:
            continue
        result = {
            aliases.get(key, key): value
            for key, value in raw.items()
            if key != "schema_version"
        }
        result["management_source"] = str(raw.get("management_source") or path)
        feed = result.get("feed_url")
        if feed is not None and not str(feed).startswith("https://"):
            result.pop("feed_url", None)
        return result
    return {}


def _app_bundle() -> Path:
    executable = Path(sys.executable).resolve(strict=False)
    for parent in (executable.parent, *executable.parents):
        if parent.suffix.casefold() == ".app":
            return parent
    return executable.parent / "OPai.app"


def _adapter_for(installed: InstalledBuild, trust: Mapping[str, object]):
    if installed.install_type is InstallType.WINDOWS_MSIX:
        return WindowsMsixAdapter()
    if installed.install_type is InstallType.MACOS_SPARKLE:
        bundle = _app_bundle()
        helper = (
            bundle
            / "Contents"
            / "Resources"
            / "OPaiUpdater"
            / "sparkle.app"
            / "Contents"
            / "MacOS"
            / "sparkle"
        )
        return MacOSSparkleAdapter(
            sparkle_public_key=str(trust.get("sparkle_public_key") or ""),
            sparkle_cli=helper,
            app_bundle=bundle,
        )
    if installed.install_type is InstallType.SOURCE_CHECKOUT:
        return DeveloperGitUpdateAdapter(Path(__file__).resolve().parents[2])
    return UnsupportedUpdateAdapter(
        "manual-update-required",
        "This installation cannot be replaced transactionally; download a verified package from the OPai release page.",
    )


def create_update_service(
    *,
    workspaces: Iterable[Path] = (),
    home: Path | None = None,
    installed: InstalledBuild | None = None,
    trust: Mapping[str, object] | None = None,
    manifest_fetcher: Callable[[str], bytes] | None = None,
) -> UpdateService:
    """Build the exact service used by every product surface."""

    roots: list[Path] = []
    for raw in workspaces:
        try:
            roots.append(Path(raw).expanduser().resolve(strict=False))
        except OSError:
            continue
    try:
        from opai.gui_workspace import load_recent_workspaces

        roots.extend(Path(path) for path in load_recent_workspaces())
    except (ImportError, OSError, ValueError):
        pass
    roots = list(dict.fromkeys(roots))
    build = installed or load_installed_build()
    trust_store = dict(trust) if trust is not None else load_trust_store()
    managed = load_managed_update_configuration()
    if managed.get("feed_url"):
        trust_store["feed_url"] = managed["feed_url"]
    raw_redirects = trust_store.get("feed_redirect_origins") or []
    try:
        feed_redirect_origins = parse_https_origins(
            raw_redirects if isinstance(raw_redirects, list) else ()
        )
    except ValueError:
        feed_redirect_origins = ()
    store = UpdateStore(
        UpdaterPaths.for_home(home or Path.home()),
        managed_policy=managed,
    )
    # The one-time legacy consent migration happens at composition, when the
    # current/recent workspace set is actually known.
    store.load_policy(legacy_workspaces=roots)
    return UpdateService(
        store=store,
        installed=build,
        trust=trust_store,
        manifest_fetcher=manifest_fetcher
        or (lambda url: fetch_manifest(url, allowed_origins=feed_redirect_origins)),
        downloader=SecureDownloader(),
        adapter=_adapter_for(build, trust_store),
        runtime_probe=lambda: probe_active_work(roots),
    )
