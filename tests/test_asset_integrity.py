from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from opai.asset_identity import (
    AssetIntegrityError,
    REQUIRED_WEB_ASSETS,
    asset_manifest,
    load_asset_binding,
    verify_asset_binding,
)
from opai.compatibility import (
    RuntimeCompatibilityError,
    runtime_compatibility_payload,
    validate_runtime_compatibility,
)


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "opai" / "assets"
MARKDOWN_IT_SHA256 = "38c70a1e7ca91ab40e2d9e6e60129851a717ed1c7d4acbbdd41bf9503791cf68"


def _copy_assets(tmp_path: Path) -> Path:
    target = tmp_path / "assets"
    shutil.copytree(ASSETS, target)
    return target


def test_complete_asset_manifest_is_deterministic_and_version_bound() -> None:
    first = asset_manifest(ASSETS)
    second = asset_manifest(ASSETS)

    assert first == second
    assert first["application_version"] == "0.2.1a1"
    assert first["schema_version"] == 1
    assert first["asset_count"] > 20
    assert len(first["fingerprint_sha256"]) == 64


def test_pinned_markdown_runtime_is_required_and_byte_exact() -> None:
    required = set(REQUIRED_WEB_ASSETS)
    assert "markdown-renderer.js" in required
    assert "vendor/markdown-it-14.1.0.min.js" in required
    assert "vendor/markdown-it.LICENSE.txt" in required

    bundle = ASSETS / "web" / "vendor" / "markdown-it-14.1.0.min.js"
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == MARKDOWN_IT_SHA256


def test_modified_asset_fails_closed_with_expected_and_actual_hashes(
    tmp_path: Path,
) -> None:
    expected = asset_manifest(ASSETS)
    assets = _copy_assets(tmp_path)
    (assets / "web" / "app.js").write_text("changed", encoding="utf-8")

    with pytest.raises(AssetIntegrityError) as raised:
        verify_asset_binding(assets, expected=expected, require_binding=True)

    assert raised.value.code == "package_integrity_failure"
    assert expected["fingerprint_sha256"] in str(raised.value)
    assert asset_manifest(assets)["fingerprint_sha256"] in str(raised.value)


def test_missing_required_asset_has_its_own_stable_category(tmp_path: Path) -> None:
    assets = _copy_assets(tmp_path)
    (assets / "web" / "index.html").unlink()

    with pytest.raises(AssetIntegrityError) as raised:
        verify_asset_binding(
            assets, expected=asset_manifest(ASSETS), require_binding=True
        )

    assert raised.value.code == "missing_packaged_asset"
    assert raised.value.component == "assets/web/index.html"


def test_corrupt_or_version_mismatched_binding_is_rejected(tmp_path: Path) -> None:
    corrupt = tmp_path / "release-identity.json"
    corrupt.write_text("not-json", encoding="utf-8")
    with pytest.raises(AssetIntegrityError, match="unreadable"):
        load_asset_binding((corrupt,))

    mismatched = tmp_path / "embedded.json"
    value = asset_manifest(ASSETS)
    value["application_version"] = "9.9.9"
    mismatched.write_text(json.dumps({"assets": value}), encoding="utf-8")
    with pytest.raises(AssetIntegrityError, match="9.9.9"):
        verify_asset_binding(
            ASSETS,
            expected=load_asset_binding((mismatched,)),
            require_binding=True,
        )


def test_compatibility_contract_is_independent_and_fails_on_schema_mismatch() -> None:
    expected = runtime_compatibility_payload()
    assert expected == {
        "lifecycle_schema_version": 1,
        "provider_catalog_version": "v1",
        "provider_protocol_version": 1,
        "project_state_schema_version": 1,
        "update_schema_version": 1,
        "updater_protocol_version": 1,
    }

    incompatible = {**expected, "update_schema_version": 999}
    with pytest.raises(RuntimeCompatibilityError) as raised:
        validate_runtime_compatibility(incompatible)

    assert raised.value.expected["update_schema_version"] == 1
    assert raised.value.actual["update_schema_version"] == 999
