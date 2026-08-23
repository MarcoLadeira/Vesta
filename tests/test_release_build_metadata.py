from __future__ import annotations

import email.parser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_SHA = "c" * 40


def _source_copy(destination: Path) -> Path:
    source = destination / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(
            ".git",
            ".worktrees",
            ".opaihub",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
            "*.egg-info",
        ),
    )
    return source


def _build(
    source: Path, output: Path, *, build_id: str | None
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    if build_id is None:
        environment.pop("OPAI_BUILD_ID", None)
    else:
        environment["OPAI_BUILD_ID"] = build_id
    return subprocess.run(  # nosec B603 - isolated local packaging fixture
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--sdist",
            "--no-isolation",
            "--outdir",
            str(output),
        ],
        cwd=source,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )


@pytest.fixture(scope="module")
def built_distributions(tmp_path_factory):
    temporary = tmp_path_factory.mktemp("canonical-release-build")
    source = _source_copy(temporary)
    output = temporary / "dist"
    result = _build(source, output, build_id=CANDIDATE_SHA)
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next(output.glob("opai-*.whl"))
    sdist = next(output.glob("opai-*.tar.gz"))
    return source, wheel, sdist


def test_wheel_embeds_exact_candidate_sha_and_canonical_version(
    built_distributions,
) -> None:
    _source, wheel, _sdist = built_distributions
    with zipfile.ZipFile(wheel) as archive:
        embedded = json.loads(archive.read("opai/_embedded_build.json"))
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = email.parser.Parser().parsestr(archive.read(metadata_name).decode())

    assert embedded == {
        "application_version": "0.2.1a1",
        "build_id": CANDIDATE_SHA,
        "release_channel": "alpha",
        "schema_version": 1,
    }
    assert metadata["Version"] == embedded["application_version"]


def test_source_archive_carries_the_same_candidate_identity(
    built_distributions,
) -> None:
    _source, _wheel, sdist = built_distributions
    with tarfile.open(sdist, "r:gz") as archive:
        member = next(
            item
            for item in archive.getmembers()
            if item.name.endswith("/opai/_embedded_build.json")
        )
        stream = archive.extractfile(member)
        assert stream is not None
        embedded = json.loads(stream.read())

    assert embedded["application_version"] == "0.2.1a1"
    assert embedded["build_id"] == CANDIDATE_SHA


def test_distribution_build_does_not_mutate_the_source_checkout(
    built_distributions,
) -> None:
    source, _wheel, _sdist = built_distributions

    assert not (source / "opai" / "_embedded_build.json").exists()


def test_installed_wheel_reports_distribution_and_candidate_identity(
    built_distributions,
) -> None:
    _source, wheel, _sdist = built_distributions
    install_root = wheel.parent / "installed"
    installed = subprocess.run(  # nosec B603 - local wheel, isolated target
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--target",
            str(install_root),
            str(wheel),
        ],
        cwd=wheel.parent,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(install_root)
    environment["PYTHONNOUSERSITE"] = "1"
    probe = subprocess.run(  # nosec B603 - fixed local interpreter probe
        [
            sys.executable,
            "-c",
            (
                "import importlib.metadata as m, json; "
                "from opai.release_identity import current_release_identity; "
                "i=current_release_identity(); "
                "print(json.dumps({'distribution': m.version('opai'), "
                "'identity': i.to_dict()}))"
            ),
        ],
        cwd=wheel.parent,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    payload = json.loads(probe.stdout)

    assert payload["distribution"] == "0.2.1a1"
    assert payload["identity"]["application_version"] == "0.2.1a1"
    assert payload["identity"]["build_id"] == CANDIDATE_SHA
    assert payload["identity"]["install_type"] == "installed_distribution"
    assert payload["identity"]["metadata_source"].endswith(
        "opai\\_embedded_build.json"
    ) or payload["identity"]["metadata_source"].endswith("opai/_embedded_build.json")


def test_installed_wheel_without_dependencies_reports_missing_pyyaml_at_bootstrap(
    built_distributions,
) -> None:
    _source, wheel, _sdist = built_distributions
    install_root = wheel.parent / "bootstrap-no-deps"
    installed = subprocess.run(  # nosec B603 - local wheel, isolated target
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--target",
            str(install_root),
            str(wheel),
        ],
        cwd=wheel.parent,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(install_root)
    environment["PYTHONNOUSERSITE"] = "1"
    probe = subprocess.run(  # nosec B603 - fixed local interpreter probe
        [sys.executable, "-S", "-m", "opai", "doctor", "--json"],
        cwd=wheel.parent,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert probe.returncode == 78, probe.stdout + probe.stderr
    payload = json.loads(probe.stdout)
    assert payload["category"] == "missing_dependency"
    assert payload["component"] == "PyYAML"
    assert payload["startup_mode"] == "installed_distribution"
    assert "invalid JSON" not in probe.stdout + probe.stderr
    assert "Traceback" not in probe.stdout + probe.stderr


def test_invalid_build_identity_fails_before_an_artifact_is_created() -> None:
    from opai.build_metadata import BuildMetadataError, build_metadata_payload

    with pytest.raises(
        BuildMetadataError, match="OPAI_BUILD_ID must be an exact lowercase commit SHA"
    ):
        build_metadata_payload(
            application_version="0.2.1a1",
            environment={"OPAI_BUILD_ID": "not-a-commit"},
        )


def test_runtime_reads_embedded_wheel_identity_without_git(tmp_path: Path) -> None:
    embedded = tmp_path / "_embedded_build.json"
    embedded.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "application_version": "0.2.1a1",
                "build_id": CANDIDATE_SHA,
                "release_channel": "alpha",
            }
        ),
        encoding="utf-8",
    )
    from opai.release_identity import load_release_identity

    identity = load_release_identity(
        identity_paths=(),
        embedded_build_paths=(embedded,),
        distribution_version="0.2.1a1",
        source_root=tmp_path,
    )

    assert identity.build_id == CANDIDATE_SHA
    assert identity.install_type == "installed_distribution"
    assert identity.metadata_source == str(embedded.resolve())


def test_local_build_without_candidate_sha_reports_unknown(tmp_path: Path) -> None:
    from opai.build_metadata import build_metadata_payload

    embedded = build_metadata_payload(
        application_version="0.2.1a1", environment={}, source_root=tmp_path
    )

    assert embedded["build_id"] == "unknown"
