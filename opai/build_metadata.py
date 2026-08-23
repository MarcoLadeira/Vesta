"""Build-only generation of immutable OPai source identity metadata."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Mapping

from ._generated_release import APPLICATION_VERSION, RELEASE_CHANNEL
from .asset_identity import AssetIntegrityError, asset_manifest
from .compatibility import runtime_compatibility_payload


_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_CHANNELS = frozenset({"development", "alpha", "beta", "stable", "nightly", "canary"})


class BuildMetadataError(RuntimeError):
    """Build inputs cannot produce an honest immutable release identity."""


def _existing_payload(source_root: Path) -> dict[str, object]:
    path = source_root / "opai" / "_embedded_build.json"
    if (source_root / ".git").exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def build_metadata_payload(
    *,
    application_version: str,
    environment: Mapping[str, str] | None = None,
    source_root: Path | None = None,
) -> dict[str, object]:
    """Resolve exact build metadata without consulting Git or mutating source."""

    version = str(application_version or "").strip()
    if version != APPLICATION_VERSION:
        raise BuildMetadataError(
            f"distribution version {version!r} does not match generated application "
            f"version {APPLICATION_VERSION!r}; run python "
            "scripts/generate_release_identity.py"
        )
    values = dict(os.environ if environment is None else environment)
    root = Path(source_root or Path(__file__).resolve().parents[1]).resolve()
    existing = _existing_payload(root)
    raw_build_id = values.get("OPAI_BUILD_ID")
    if raw_build_id is None:
        raw_build_id = str(existing.get("build_id") or "unknown")
    build_id = str(raw_build_id).strip()
    if build_id != "unknown" and _COMMIT_SHA.fullmatch(build_id) is None:
        raise BuildMetadataError("OPAI_BUILD_ID must be an exact lowercase commit SHA")
    channel = str(
        values.get("OPAI_RELEASE_CHANNEL")
        or existing.get("release_channel")
        or RELEASE_CHANNEL
    ).strip()
    if channel not in _CHANNELS:
        raise BuildMetadataError(f"OPAI_RELEASE_CHANNEL is unsupported: {channel!r}")
    existing_version = str(existing.get("application_version") or version)
    if existing_version != version:
        raise BuildMetadataError(
            "source archive build metadata disagrees with the distribution version"
        )
    try:
        assets = asset_manifest(root / "opai" / "assets")
    except AssetIntegrityError as exc:
        raise BuildMetadataError(
            f"packaged asset metadata generation failed ({exc.code})"
        ) from exc
    return {
        "application_version": version,
        "assets": assets,
        "build_id": build_id,
        "compatibility": runtime_compatibility_payload(),
        "release_channel": channel,
        "schema_version": 1,
    }


def write_build_metadata(
    package_dir: Path,
    *,
    application_version: str,
    environment: Mapping[str, str] | None = None,
    source_root: Path | None = None,
) -> Path:
    """Write canonical build JSON into a build or source-archive staging tree."""

    target = Path(package_dir).resolve() / "_embedded_build.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_metadata_payload(
        application_version=application_version,
        environment=environment,
        source_root=source_root,
    )
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return target
