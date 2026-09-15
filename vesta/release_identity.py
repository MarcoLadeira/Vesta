"""Canonical Vesta application and build identity.

The only human-edited application version lives in ``pyproject.toml``. Runtime
code imports the generated projection from :mod:`vesta._generated_release`, while
packaged builds may add immutable artifact identity in ``release-identity.json``.
This module intentionally uses only the Python standard library so version and
bootstrap diagnostics remain available before optional runtime dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import json
import platform as platform_module
import re
import sys
from pathlib import Path
from typing import Callable, Iterable, Mapping


_VERSION = re.compile(
    r"^(?P<base>\d+(?:\.\d+){1,2})"
    r"(?:(?P<pre>a|b|rc)(?P<pre_number>\d+))?"
    r"(?P<dev>\.dev(?P<dev_number>\d+))?"
    r"(?:\+(?P<local>[0-9A-Za-z.-]+))?$"
)
_BUILD_ID = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHANNELS = frozenset({"development", "alpha", "beta", "stable", "nightly", "canary"})
_DISTRIBUTION_UNSET = object()


class ReleaseIdentityError(RuntimeError):
    """Canonical or embedded release identity is unreadable or inconsistent."""


def packaged_metadata_paths(
    name: str,
    *,
    executable_path: Path | None = None,
    packaged: bool | None = None,
) -> tuple[Path, ...]:
    """Return the one layout-owned metadata path for a packaged process.

    Generic ancestor walking is forbidden: an absent bundle-local manifest
    must not be replaced by a neighboring checkout or unrelated admin file.
    """

    if not name or Path(name).name != name:
        raise ValueError("packaged metadata name must be one plain filename")
    is_packaged = (
        bool(getattr(sys, "frozen", False) or "__compiled__" in globals())
        if packaged is None
        else packaged
    )
    if executable_path is None and not is_packaged:
        return ()
    executable = Path(executable_path or sys.executable).resolve(strict=False)
    directory = executable if executable.is_dir() else executable.parent
    if directory.name.casefold() in {"cli", "gui"}:
        root = directory.parent
    elif directory.name == "MacOS" and directory.parent.name == "Contents":
        root = directory.parent / "Resources"
    else:
        root = directory
    return (root / name,)


@dataclass(frozen=True)
class ProjectRelease:
    """Values deterministically derived from the editable project version."""

    application_version: str
    release_channel: str
    release_stage: str
    display_name: str
    published_tag: str


@dataclass(frozen=True)
class ReleaseIdentity:
    """Application identity plus immutable build and installation coordinates."""

    application_version: str
    release_channel: str
    release_stage: str
    build_id: str
    display_name: str
    published_tag: str
    platform: str
    architecture: str
    install_type: str
    metadata_source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "application_version": self.application_version,
            "release_channel": self.release_channel,
            "release_stage": self.release_stage,
            "build_id": self.build_id,
            "display_name": self.display_name,
            "published_tag": self.published_tag,
            "platform": self.platform,
            "architecture": self.architecture,
            "install_type": self.install_type,
            "metadata_source": self.metadata_source,
        }


def derive_project_release(version: str) -> ProjectRelease:
    """Derive display/channel/tag values without creating another version input."""

    value = str(version or "").strip()
    match = _VERSION.fullmatch(value)
    if match is None:
        raise ReleaseIdentityError(
            f"canonical application version is not supported PEP 440: {value!r}"
        )
    base = match.group("base")
    pre = match.group("pre")
    pre_number = match.group("pre_number")
    dev_number = match.group("dev_number")
    local = match.group("local")
    if dev_number is not None or local is not None:
        channel = "development"
        stage = f"development.{dev_number or 'local'}"
        display_stage = f"Development.{dev_number or 'local'}"
    elif pre == "a":
        channel = "alpha"
        stage = f"alpha.{pre_number}"
        display_stage = f"Alpha.{pre_number}"
    elif pre == "b":
        channel = "beta"
        stage = f"beta.{pre_number}"
        display_stage = f"Beta.{pre_number}"
    elif pre == "rc":
        channel = "beta"
        stage = f"rc.{pre_number}"
        display_stage = f"RC.{pre_number}"
    else:
        channel = "stable"
        stage = "stable"
        display_stage = ""
    display_name = f"Vesta {base}"
    if display_stage:
        display_name = f"{display_name} {display_stage}"
    return ProjectRelease(
        application_version=value,
        release_channel=channel,
        release_stage=stage,
        display_name=display_name,
        published_tag=f"v{value}",
    )


def _fallback_project_table(text: str) -> dict[str, str]:
    in_project = False
    values: dict[str, str] = {}
    assignment = re.compile(r'^\s*(name|version)\s*=\s*["\']([^"\']+)["\']\s*$')
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if not in_project:
            continue
        match = assignment.fullmatch(line)
        if match is not None:
            values[match.group(1)] = match.group(2)
    return values


def read_project_release(pyproject_path: Path) -> ProjectRelease:
    """Read the canonical ``[project].version`` on every supported Python."""

    path = Path(pyproject_path).expanduser().resolve()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseIdentityError(
            f"cannot read canonical version source: {path}"
        ) from exc
    if len(text.encode("utf-8")) > 1024 * 1024:
        raise ReleaseIdentityError(
            f"canonical version source is unexpectedly large: {path}"
        )
    try:
        import tomllib  # Python 3.11+; the fallback below keeps Python 3.10 supported.
    except ImportError:  # pragma: no cover - exercised by the Python 3.10 CI lane
        project = _fallback_project_table(text)
    else:
        try:
            value = tomllib.loads(text)
            raw_project = value.get("project")
            project = dict(raw_project) if isinstance(raw_project, dict) else {}
        except (TypeError, ValueError) as exc:
            raise ReleaseIdentityError(
                f"canonical version source is invalid TOML: {path}"
            ) from exc
    if project.get("name") != "vesta":
        raise ReleaseIdentityError(
            f"canonical version source does not describe Vesta: {path}"
        )
    return derive_project_release(str(project.get("version") or ""))


def render_generated_release(release: ProjectRelease) -> str:
    """Render the checked-in runtime projection in a stable, reviewable form."""

    values = (
        ("APPLICATION_VERSION", release.application_version),
        ("RELEASE_CHANNEL", release.release_channel),
        ("RELEASE_STAGE", release.release_stage),
        ("DISPLAY_NAME", release.display_name),
        ("PUBLISHED_TAG", release.published_tag),
    )
    lines = [
        '"""Generated from pyproject.toml by scripts/generate_release_identity.py."""',
        "",
        "# Do not edit this file by hand. Change [project].version and regenerate.",
    ]
    lines.extend(f"{name} = {json.dumps(value)}" for name, value in values)
    return "\n".join(lines) + "\n"


def render_documentation_identity(release: ProjectRelease) -> str:
    """Render the generated marker validated in current release documentation."""

    return (
        "<!-- vesta-release-identity: "
        f"application_version={release.application_version}; "
        f"release_stage={release.release_stage}; "
        f"published_tag={release.published_tag} -->"
    )


def render_documentation_projection(release: ProjectRelease, *, surface: str) -> str:
    """Render one complete current-documentation identity block."""

    marker = render_documentation_identity(release)
    if surface == "README.md":
        body = (
            f"The current application release is **{release.display_name}** (package\n"
            f"`{release.application_version}`, channel `{release.release_channel}`, "
            f"canonical tag `{release.published_tag}`). Vesta is a local-first\n"
            "AI coding hub that installs into your terminal and AI coding clients so every\n"
            "project gets better routing, safer automation, reusable context, Superpowers\n"
            "skills, MCP-ready registries, testing workflows, GitOps helpers, governance\n"
            "controls, benchmark proof, and a real cost ledger."
        )
    elif surface == "docs/INSTALL_PROOF.md":
        body = (
            f"- [ ] **CLI resolves.** `vesta version` begins with `Vesta "
            f"{release.application_version} {release.release_stage}`\n"
            "      and reports either the exact packaged build SHA or the honest\n"
            "      `development`/`unknown` fallback."
        )
    elif surface == "site/index.html":
        body = f"      <p>{release.display_name} — MIT Licensed</p>"
    else:
        raise ReleaseIdentityError(
            f"unsupported documentation identity projection: {surface}"
        )
    return f"{marker}\n{body}\n<!-- /vesta-release-identity -->"


def _normal_architecture(value: str | None = None) -> str:
    machine = str(value or platform_module.machine()).casefold()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(
        machine, machine or "unknown"
    )


def _normal_platform(value: str | None = None) -> str:
    system = str(value or platform_module.system()).casefold()
    return "macos" if system == "darwin" else system or "unknown"


def _read_embedded_identity(path: Path) -> dict[str, object]:
    resolved = Path(path).expanduser().resolve()
    try:
        if (
            path.is_symlink()
            or not resolved.is_file()
            or resolved.stat().st_size > 64 * 1024
        ):
            return {}
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _generated_release() -> ProjectRelease:
    from ._generated_release import APPLICATION_VERSION

    return derive_project_release(APPLICATION_VERSION)


def load_release_identity(
    *,
    identity_paths: Iterable[Path] | None = None,
    embedded_build_paths: Iterable[Path] | None = None,
    distribution_version: str | None | object = _DISTRIBUTION_UNSET,
    source_root: Path | None = None,
    platform_name: str | None = None,
    architecture: str | None = None,
    distribution_lookup: Callable[[str], str] = importlib.metadata.version,
) -> ReleaseIdentity:
    """Load identity in packaged, installed, editable, or source order.

    Supplying ``identity_paths`` is an explicit package boundary. A valid first
    identity is authoritative and source/distribution fallbacks are not read.
    """

    generated = _generated_release()
    explicit_identity_paths = tuple(identity_paths or ())
    for raw_path in explicit_identity_paths:
        value = _read_embedded_identity(raw_path)
        if not value:
            if raw_path.exists() or raw_path.is_symlink():
                raise ReleaseIdentityError(
                    f"embedded release identity is unreadable or unsafe: {raw_path}"
                )
            continue
        if value.get("schema_version") != 1:
            raise ReleaseIdentityError(
                f"embedded release identity has unsupported schema: {raw_path}"
            )
        version = str(value.get("application_version") or value.get("version") or "")
        build_id = str(value.get("build_id") or value.get("commit") or "").lower()
        channel = str(value.get("release_channel") or value.get("channel") or "")
        if version != generated.application_version:
            raise ReleaseIdentityError(
                f"embedded application version {version!r} does not match runtime {generated.application_version!r}"
            )
        if _BUILD_ID.fullmatch(build_id) is None:
            raise ReleaseIdentityError(
                "embedded build identity is not an exact commit SHA"
            )
        if channel not in _CHANNELS:
            raise ReleaseIdentityError(
                f"embedded release channel is unsupported: {channel!r}"
            )
        derived = derive_project_release(version)
        return ReleaseIdentity(
            application_version=version,
            release_channel=channel,
            release_stage=derived.release_stage,
            build_id=build_id,
            display_name=derived.display_name,
            published_tag=derived.published_tag,
            platform=str(value.get("platform") or _normal_platform(platform_name)),
            architecture=str(
                value.get("architecture") or _normal_architecture(architecture)
            ),
            install_type=str(value.get("install_type") or "packaged"),
            metadata_source=str(Path(raw_path).expanduser().resolve()),
        )
    if identity_paths is not None and explicit_identity_paths:
        raise ReleaseIdentityError(
            "no usable embedded release identity exists at the explicit package boundary"
        )

    root = (
        Path(source_root or Path(__file__).resolve().parents[1]).expanduser().resolve()
    )
    source_checkout = (root / ".git").exists() and (root / "pyproject.toml").is_file()
    if source_checkout:
        canonical = read_project_release(root / "pyproject.toml")
        if canonical != generated:
            raise ReleaseIdentityError(
                "generated runtime release projection disagrees with pyproject.toml; "
                "run python scripts/generate_release_identity.py"
            )
        return ReleaseIdentity(
            application_version=generated.application_version,
            release_channel=generated.release_channel,
            release_stage=generated.release_stage,
            build_id="development",
            display_name=generated.display_name,
            published_tag=generated.published_tag,
            platform=_normal_platform(platform_name),
            architecture=_normal_architecture(architecture),
            install_type="source_checkout",
            metadata_source="generated-development-projection",
        )

    resolved_distribution: str | None
    if distribution_version is _DISTRIBUTION_UNSET:
        try:
            resolved_distribution = distribution_lookup("vesta")
        except importlib.metadata.PackageNotFoundError:
            resolved_distribution = None
    else:
        resolved_distribution = (
            str(distribution_version) if distribution_version is not None else None
        )
    if (
        resolved_distribution is not None
        and resolved_distribution != generated.application_version
    ):
        raise ReleaseIdentityError(
            f"installed package metadata {resolved_distribution!r} does not match runtime projection {generated.application_version!r}"
        )

    build_paths = (
        tuple(embedded_build_paths)
        if embedded_build_paths is not None
        else (Path(__file__).resolve().with_name("_embedded_build.json"),)
    )
    for raw_path in build_paths:
        value = _read_embedded_identity(raw_path)
        if not value:
            continue
        if value.get("schema_version") != 1:
            raise ReleaseIdentityError(
                f"embedded build identity has unsupported schema: {raw_path}"
            )
        version = str(value.get("application_version") or "")
        build_id = str(value.get("build_id") or "")
        channel = str(value.get("release_channel") or "")
        if version != generated.application_version:
            raise ReleaseIdentityError(
                f"embedded build version {version!r} does not match runtime {generated.application_version!r}"
            )
        if build_id != "unknown" and _BUILD_ID.fullmatch(build_id) is None:
            raise ReleaseIdentityError(
                "embedded build identity is not an exact commit SHA or unknown"
            )
        if channel not in _CHANNELS:
            raise ReleaseIdentityError(
                f"embedded build release channel is unsupported: {channel!r}"
            )
        return ReleaseIdentity(
            application_version=version,
            release_channel=channel,
            release_stage=generated.release_stage,
            build_id=build_id,
            display_name=generated.display_name,
            published_tag=generated.published_tag,
            platform=_normal_platform(platform_name),
            architecture=_normal_architecture(architecture),
            install_type=(
                "source_checkout"
                if source_checkout
                else (
                    "installed_distribution"
                    if resolved_distribution is not None
                    else "source_archive"
                )
            ),
            metadata_source=str(Path(raw_path).expanduser().resolve()),
        )
    install_type = (
        "installed_distribution"
        if resolved_distribution is not None
        else "source_archive"
    )
    return ReleaseIdentity(
        application_version=generated.application_version,
        release_channel=generated.release_channel,
        release_stage=generated.release_stage,
        build_id="unknown",
        display_name=generated.display_name,
        published_tag=generated.published_tag,
        platform=_normal_platform(platform_name),
        architecture=_normal_architecture(architecture),
        install_type=install_type,
        metadata_source=(
            "installed-distribution-metadata"
            if resolved_distribution is not None
            else "generated-source-archive-projection"
        ),
    )


def current_release_identity(
    *,
    source_root: Path | None = None,
    packaged: bool | None = None,
    identity_paths: Iterable[Path] | None = None,
) -> ReleaseIdentity:
    """Return the current process identity without caching mutable environment."""

    root = Path(source_root or Path(__file__).resolve().parents[1]).resolve()
    source_checkout = (root / ".git").exists() and (root / "pyproject.toml").is_file()
    is_packaged = (
        bool(getattr(sys, "frozen", False) or "__compiled__" in globals())
        if packaged is None
        else packaged
    )
    paths = identity_paths
    if paths is None and is_packaged and not source_checkout:
        paths = packaged_metadata_paths("release-identity.json", packaged=is_packaged)
    return load_release_identity(identity_paths=paths, source_root=root)


def identity_payload() -> dict[str, str]:
    """Return JSON-safe canonical identity for CLI/GUI/evidence projections."""

    return current_release_identity().to_dict()


def safe_identity_payload() -> dict[str, str]:
    """Return persistable identity without exposing a local installation path."""

    payload = identity_payload()
    payload.pop("metadata_source", None)
    return payload


def surface_identity_payload(*, brand: str = "Vesta") -> dict[str, object]:
    """Project one identity onto backward-compatible user/evidence fields."""

    from .compatibility import runtime_compatibility_payload

    identity = safe_identity_payload()
    return {
        "brand": brand,
        "compatibility": runtime_compatibility_payload(),
        "version": identity["application_version"],
        "release_stage": identity["release_stage"],
        "release_identity": identity,
    }


def artifact_identity_payload(
    *,
    build_id: str,
    assets: Mapping[str, object],
    platform_name: str | None = None,
    architecture: str | None = None,
    install_type: str = "portable",
) -> dict[str, object]:
    """Bind canonical app identity to one immutable artifact candidate."""

    from .compatibility import runtime_compatibility_payload

    normalized_build = str(build_id or "").strip().lower()
    if _BUILD_ID.fullmatch(normalized_build) is None:
        raise ReleaseIdentityError(
            "artifact build identity must be an exact commit SHA"
        )
    release = _generated_release()
    return {
        "application_version": release.application_version,
        "assets": dict(assets),
        "build_id": normalized_build,
        "compatibility": runtime_compatibility_payload(),
        "architecture": _normal_architecture(architecture),
        "install_type": str(install_type or "unknown"),
        "platform": _normal_platform(platform_name),
        "published_tag": release.published_tag,
        "release_channel": release.release_channel,
        "release_stage": release.release_stage,
        "schema_version": 1,
    }


def validate_artifact_identity(
    value: Mapping[str, object],
    *,
    build_id: str,
    platform_name: str,
    release_tag: str,
    rehearsal: bool,
    application_version: str | None = None,
) -> dict[str, object]:
    """Reject artifact evidence that contradicts canonical release identity."""

    from .asset_identity import ASSET_SCHEMA_VERSION
    from .compatibility import validate_compatibility_coordinates

    actual = dict(value)
    if application_version is not None:
        release = derive_project_release(application_version)
    elif not rehearsal and release_tag.startswith("v"):
        release = derive_project_release(release_tag.removeprefix("v"))
    else:
        release = _generated_release()
    expected = {
        "application_version": release.application_version,
        "build_id": str(build_id).lower(),
        "platform": _normal_platform(platform_name),
        "published_tag": release.published_tag,
        "release_channel": release.release_channel,
        "release_stage": release.release_stage,
        "schema_version": 1,
    }
    mismatched = [
        key
        for key, expected_value in expected.items()
        if actual.get(key) != expected_value
    ]
    expected_release_tag = "untagged-rehearsal" if rehearsal else release.published_tag
    if release_tag != expected_release_tag:
        mismatched.append("release_tag")
    assets = actual.get("assets")
    if not isinstance(assets, Mapping):
        mismatched.append("assets")
    else:
        fingerprint = str(assets.get("fingerprint_sha256") or "")
        count = assets.get("asset_count")
        if (
            assets.get("schema_version") != ASSET_SCHEMA_VERSION
            or assets.get("application_version") != release.application_version
            or _SHA256.fullmatch(fingerprint) is None
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 1
        ):
            mismatched.append("assets")
    compatibility = actual.get("compatibility")
    if not isinstance(compatibility, Mapping):
        mismatched.append("compatibility")
    else:
        try:
            validate_compatibility_coordinates(compatibility)
        except RuntimeError:
            mismatched.append("compatibility")
    if (
        not isinstance(actual.get("architecture"), str)
        or not str(actual.get("architecture")).strip()
    ):
        mismatched.append("architecture")
    if (
        not isinstance(actual.get("install_type"), str)
        or not str(actual.get("install_type")).strip()
    ):
        mismatched.append("install_type")
    if mismatched:
        fields = ", ".join(sorted(set(mismatched)))
        raise ReleaseIdentityError(
            f"artifact identity conflicts with canonical release fields: {fields}"
        )
    return actual


def release_version_text(*, brand: str = "Vesta") -> str:
    """Render a human version string that never hides the exact build identity."""

    identity = safe_identity_payload()
    return (
        f"{brand} {identity['application_version']} {identity['release_stage']} "
        f"(build {identity['build_id']})"
    )
