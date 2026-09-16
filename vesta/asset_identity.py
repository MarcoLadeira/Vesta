"""Deterministic integrity identity for Vesta's packaged desktop assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

from ._generated_release import APPLICATION_VERSION


ASSET_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_INDEX = ".runtime-index.html"
REQUIRED_WEB_ASSETS = (
    "activity.js",
    "agents-workspace.js",
    "agents-workspace.css",
    "app.js",
    "chat-components.js",
    "composer.js",
    "design-tokens-preview.html",
    "design-tokens.css",
    "generated-lifecycle.js",
    "icons.js",
    "index.html",
    "markdown-renderer.js",
    "message-state.js",
    "onboarding.js",
    "run-result.js",
    "settings.js",
    "styles.css",
    "theme.js",
    "vendor/markdown-it-14.1.0.min.js",
    "vendor/markdown-it.LICENSE.txt",
)
REQUIRED_ASSETS = (
    "vesta-icon.png",
    "vesta-mascot.png",
    "fonts/Inter-Variable.ttf",
    "fonts/Nunito-Variable.ttf",
    *(f"web/{name}" for name in REQUIRED_WEB_ASSETS),
)


class AssetIntegrityError(RuntimeError):
    """Packaged UI assets are missing, unreadable, or from another build."""

    def __init__(self, code: str, component: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.component = component


def _production_files(asset_root: Path) -> tuple[Path, ...]:
    root = Path(asset_root).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise AssetIntegrityError(
            "missing_packaged_asset",
            "assets",
            f"packaged asset directory is missing: {root}",
        )
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if "__tests__" in relative.parts or path.name == _RUNTIME_INDEX:
            continue
        if path.is_symlink():
            raise AssetIntegrityError(
                "package_integrity_failure",
                f"assets/{relative.as_posix()}",
                f"packaged asset may not be a symbolic link: {relative.as_posix()}",
            )
        files.append(path)
    return tuple(sorted(files, key=lambda item: item.relative_to(root).as_posix()))


def _assert_required(asset_root: Path) -> None:
    root = Path(asset_root).expanduser().resolve()
    for relative in REQUIRED_ASSETS:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise AssetIntegrityError(
                "missing_packaged_asset",
                f"assets/{relative}",
                f"required packaged asset is missing: assets/{relative}",
            )


def asset_manifest(asset_root: Path) -> dict[str, object]:
    """Hash every production asset by stable package-relative name and bytes."""

    candidate = Path(asset_root).expanduser()
    if candidate.is_symlink():
        raise AssetIntegrityError(
            "package_integrity_failure",
            "assets",
            "packaged asset directory may not be a symbolic link",
        )
    root = candidate.resolve()
    try:
        _assert_required(root)
        files = _production_files(root)
    except OSError as exc:
        raise AssetIntegrityError(
            "package_integrity_failure",
            "assets",
            "packaged assets are unreadable; reinstall Vesta",
        ) from exc
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError as exc:
            raise AssetIntegrityError(
                "package_integrity_failure",
                f"assets/{relative.decode('utf-8')}",
                f"packaged asset is unreadable: assets/{relative.decode('utf-8')}",
            ) from exc
        digest.update(b"\0")
    return {
        "application_version": APPLICATION_VERSION,
        "asset_count": len(files),
        "fingerprint_sha256": digest.hexdigest(),
        "schema_version": ASSET_SCHEMA_VERSION,
    }


def load_metadata_binding(
    paths: Iterable[Path], *, section: str
) -> dict[str, object] | None:
    """Read one object section from embedded build/release metadata strictly."""

    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            continue
        try:
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > 64 * 1024
            ):
                raise ValueError("unsafe metadata path")
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AssetIntegrityError(
                "package_integrity_failure",
                path.name,
                f"packaged asset identity is unreadable: {path.name}",
            ) from exc
        if not isinstance(value, dict):
            raise AssetIntegrityError(
                "package_integrity_failure",
                path.name,
                f"packaged asset identity is unreadable: {path.name}",
            )
        binding = value.get(section)
        if binding is None:
            continue
        if not isinstance(binding, Mapping):
            raise AssetIntegrityError(
                "package_integrity_failure",
                path.name,
                f"packaged {section} identity is unreadable: {path.name}",
            )
        return dict(binding)
    return None


def load_asset_binding(paths: Iterable[Path]) -> dict[str, object] | None:
    """Read the first embedded build/release metadata asset binding strictly."""

    return load_metadata_binding(paths, section="assets")


def verify_asset_binding(
    asset_root: Path,
    *,
    expected: Mapping[str, object] | None,
    require_binding: bool,
) -> dict[str, object]:
    """Fail closed when the live package asset set differs from its build."""

    actual = asset_manifest(asset_root)
    if expected is None:
        if require_binding:
            raise AssetIntegrityError(
                "package_integrity_failure",
                "asset-identity",
                "packaged asset identity is missing; reinstall Vesta",
            )
        return actual
    expected_value = dict(expected)
    version = str(expected_value.get("application_version") or "")
    fingerprint = str(expected_value.get("fingerprint_sha256") or "")
    count = expected_value.get("asset_count")
    if expected_value.get("schema_version") != ASSET_SCHEMA_VERSION:
        raise AssetIntegrityError(
            "package_integrity_failure",
            "asset-identity",
            "packaged asset identity schema is unsupported",
        )
    if version != APPLICATION_VERSION:
        raise AssetIntegrityError(
            "package_integrity_failure",
            "asset-identity",
            f"packaged assets report application version {version!r}; expected {APPLICATION_VERSION!r}",
        )
    if _SHA256.fullmatch(fingerprint) is None or not isinstance(count, int):
        raise AssetIntegrityError(
            "package_integrity_failure",
            "asset-identity",
            "packaged asset identity is malformed",
        )
    if fingerprint != actual["fingerprint_sha256"] or count != actual["asset_count"]:
        raise AssetIntegrityError(
            "package_integrity_failure",
            "assets",
            "packaged asset fingerprint mismatch: "
            f"expected {fingerprint}, actual {actual['fingerprint_sha256']}; "
            "reinstall Vesta",
        )
    return actual
