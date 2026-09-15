from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
import yaml

from opai._generated_release import APPLICATION_VERSION, RELEASE_CHANNEL
from opai.asset_identity import asset_manifest
from opai.update.models import InstallType
from opai.update.packaging import runtime_identity
from scripts import prepare_native_update, qualify_native_update


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-packaged-update.yml"
HARNESS = ROOT / "scripts" / "qualify_native_update.py"


def _workflow() -> dict[str, object]:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_desktop_release_delegates_only_after_signed_attestation():
    workflow = yaml.load(
        (ROOT / ".github" / "workflows" / "desktop-artifacts.yml").read_text(
            encoding="utf-8"
        ),
        Loader=yaml.BaseLoader,
    )
    publish = workflow["jobs"]["publish-packaged-update"]

    assert set(publish["needs"]) == {"source-qualification", "attest"}
    assert publish["uses"] == "./.github/workflows/publish-packaged-update.yml"
    assert publish["secrets"] == "inherit"


def test_production_signing_binds_assets_from_the_candidate_provenance():
    source = (ROOT / ".github" / "workflows" / "desktop-artifacts.yml").read_text(
        encoding="utf-8"
    )

    assert '--candidate-provenance "$BUNDLE/provenance.json"' in source
    assert "--candidate-platform windows" in source
    assert "--candidate-platform darwin" in source
    assert '--asset-root "$GITHUB_WORKSPACE/opai/assets"' not in source


def test_native_runtime_configuration_uses_validated_candidate_assets(
    tmp_path: Path, monkeypatch
):
    build_id = "a" * 40
    candidate_version = "0.2.0a1"
    candidate_assets = asset_manifest(ROOT / "opai" / "assets")
    candidate_assets["application_version"] = candidate_version
    candidate_assets["fingerprint_sha256"] = "f" * 64
    candidate_compatibility = {
        "lifecycle_schema_version": 1,
        "provider_catalog_version": "v1",
        "provider_protocol_version": 1,
        "update_schema_version": 1,
        "updater_protocol_version": 1,
    }
    provenance = tmp_path / "provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "tag": "v0.2.0a1",
                "commit": build_id,
                "rehearsal": False,
                "platform": "windows",
                "artifact_identity": {
                    "application_version": candidate_version,
                    "assets": candidate_assets,
                    "build_id": build_id,
                    "compatibility": candidate_compatibility,
                    "architecture": "x86_64",
                    "install_type": "portable",
                    "platform": "windows",
                    "published_tag": "v0.2.0a1",
                    "release_channel": "alpha",
                    "release_stage": "alpha.1",
                    "schema_version": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    trust = tmp_path / "trust.json"
    trust.write_text('{"schema_version": 1}', encoding="utf-8")
    captured: dict[str, object] = {}

    def capture(_bundle, *, identity, trust):
        captured["identity"] = identity
        captured["trust"] = trust

    monkeypatch.setattr(prepare_native_update, "write_runtime_configuration", capture)
    result = prepare_native_update.main(
        [
            "configure",
            "--bundle",
            str(tmp_path / "bundle"),
            "--trust",
            str(trust),
            "--candidate-provenance",
            str(provenance),
            "--release-tag",
            "v0.2.0a1",
            "--candidate-platform",
            "windows",
            "--version",
            candidate_version,
            "--build-id",
            build_id,
            "--channel",
            RELEASE_CHANNEL,
            "--platform",
            "windows",
            "--architecture",
            "x86_64",
            "--install-type",
            "windows_msix",
            "--package-identity",
            "OPai.Desktop",
            "--publisher-identity",
            "CN=Vesta",
        ]
    )

    assert result == 0
    assert captured["identity"]["assets"] == candidate_assets
    assert captured["identity"]["application_version"] == candidate_version
    assert captured["identity"]["compatibility"] == candidate_compatibility


def test_macos_native_artifact_has_one_canonical_name_across_release_jobs():
    desktop = (ROOT / ".github" / "workflows" / "desktop-artifacts.yml").read_text(
        encoding="utf-8"
    )
    publication = WORKFLOW.read_text(encoding="utf-8")

    assert 'NATIVE_PACKAGE="$NATIVE_OUTPUT/OPai-${RELEASE_TAG}-macos.zip"' in desktop
    assert "macos-$(uname -m).zip" not in desktop
    assert 'MACOS_ARCHIVE="$MACOS_STAGING/OPai-${RELEASE_TAG}-macos.zip"' in publication


def test_publication_requires_both_real_native_matrix_hosts():
    jobs = _workflow()["jobs"]
    native = jobs["native"]
    publish = jobs["publish"]
    matrix = native["strategy"]["matrix"]["include"]

    assert {(item["platform"], item["os"]) for item in matrix} == {
        ("windows", "windows-latest"),
        ("macos", "macos-latest"),
    }
    assert set(publish["needs"]) == {"stage", "native"}
    native_commands = "\n".join(str(step.get("run", "")) for step in native["steps"])
    assert "qualify_native_update.py" in native_commands
    assert "native_execution" in native_commands
    assert "--expected-build-id" in native_commands
    assert "artifact_identity" in native_commands
    assert 'report.get("passed", 0) < 16' in native_commands
    assert "skip" not in native_commands.casefold()


def test_native_qualification_rejects_an_artifact_for_another_build(tmp_path: Path):
    commit = "a" * 40
    identity = runtime_identity(
        version=APPLICATION_VERSION,
        build_id=commit,
        channel=RELEASE_CHANNEL,
        platform="windows",
        architecture="x86_64",
        install_type=InstallType.WINDOWS_MSIX,
        package_identity="OPai.Desktop",
        publisher_identity="CN=Vesta",
        assets=asset_manifest(ROOT / "opai" / "assets"),
    )
    package = tmp_path / "candidate.msix"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("release-identity.json", json.dumps(identity))
    host = SimpleNamespace(
        platform="windows",
        package_identity="OPai.Desktop",
        publisher_identity="CN=Vesta",
    )

    qualified = qualify_native_update._qualified_candidate_identity(
        package,
        host=host,
        expected_version=APPLICATION_VERSION,
        expected_build_id=commit,
    )
    assert qualified["build_id"] == commit

    with pytest.raises(qualify_native_update.QualificationError, match="build_id"):
        qualify_native_update._qualified_candidate_identity(
            package,
            host=host,
            expected_version=APPLICATION_VERSION,
            expected_build_id="b" * 40,
        )


def test_native_harness_defines_at_least_sixteen_named_executed_scenarios():
    tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
    names = [
        call.args[0].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "prove"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    ]

    assert len(names) >= 16
    assert len(names) == len(set(names))
    assert {
        "signed-feed-discovers-candidate",
        "download-hash-and-native-signature-verify",
        "native-installer-starts-from-canonical-operation",
        "new-gui-confirms-post-update-health",
        "native-platform-rejects-downgrade",
        "offline-check-is-not-reported-up-to-date",
    }.issubset(names)


def test_native_harness_exercises_real_concurrency_recovery_and_non_deferred_downgrade():
    source = HARNESS.read_text(encoding="utf-8")

    assert "concurrent-process-cannot-acquire-update-operation" in source
    assert "active-work-blocks-native-replacement" in source
    assert "native-rollback-restores-baseline" in source
    assert "replayed-or-mixed-feed-is-rejected" in source
    downgrade = source[
        source.index("def assert_downgrade_rejected") : source.index("@contextmanager")
    ]
    assert "--defer-install" not in downgrade


def test_reruns_resolve_exact_artifact_ids_instead_of_merging_patterns():
    jobs = _workflow()["jobs"]
    for job_name in ("stage", "native", "publish"):
        job = jobs[job_name]
        commands = "\n".join(str(step.get("run", "")) for step in job["steps"])
        downloads = [
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("actions/download-artifact@")
        ]
        assert "resolve-artifact" in commands
        assert downloads
        assert all("artifact-ids" in step["with"] for step in downloads)
        assert all("pattern" not in step["with"] for step in downloads)


def test_live_manifest_is_the_final_remote_mutation():
    publish = _workflow()["jobs"]["publish"]
    final_step = publish["steps"][-1]
    commands = str(final_step["run"])

    assert final_step["name"] == (
        "Advance channel manifest only after every artifact is addressable"
    )
    assert 'gh release upload "$FEED_TAG" "$FEED/manifest.json" --clobber' in commands
    assert "qualified-manifest.json" not in commands
