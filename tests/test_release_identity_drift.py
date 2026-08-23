from __future__ import annotations

import importlib
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]


def _validation_module():
    return importlib.import_module("opai.release_validation")


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    (root / "opai").mkdir(parents=True)
    (root / "opaihub").mkdir()
    shutil.copy2(ROOT / "pyproject.toml", root / "pyproject.toml")
    for relative in (
        Path("opai/_generated_release.py"),
        Path("opai/__init__.py"),
        Path("opaihub/__init__.py"),
    ):
        shutil.copy2(ROOT / relative, root / relative)
    return root


def test_current_repository_has_no_release_identity_drift() -> None:
    assert _validation_module().validate_release_identity(ROOT) == ()


def test_generated_projection_drift_is_actionable(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    generated = root / "opai" / "_generated_release.py"
    generated.write_text(
        generated.read_text(encoding="utf-8").replace('"0.2.1a1"', '"9.9.9"', 1),
        encoding="utf-8",
    )

    drift = _validation_module().validate_release_identity(root)
    version_drift = next(
        item for item in drift if item.surface == "generated.application_version"
    )

    assert version_drift.expected == "0.2.1a1"
    assert version_drift.actual == "9.9.9"
    assert version_drift.path == generated.resolve()
    assert (
        version_drift.remediation == "Run: python scripts/generate_release_identity.py"
    )


def test_new_human_edited_runtime_version_source_is_rejected(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    duplicate = root / "opai" / "version.py"
    duplicate.write_text('APPLICATION_VERSION = "9.9.9"\n', encoding="utf-8")

    drift = _validation_module().validate_release_identity(root)
    duplicate_drift = next(
        item for item in drift if item.surface == "duplicate_application_version"
    )

    assert duplicate_drift.path == duplicate.resolve()
    assert duplicate_drift.expected == "import the generated canonical projection"
    assert duplicate_drift.actual == "9.9.9"


def test_tool_release_field_cannot_return_as_a_second_source(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + '\n[tool.opai.release_identity]\napplication_version = "9.9.9"\n',
        encoding="utf-8",
    )

    drift = _validation_module().validate_release_identity(root)

    assert any(
        item.surface == "duplicate_pyproject_application_version" for item in drift
    )


def test_drift_cli_prints_expected_actual_surface_and_remediation(
    tmp_path: Path, capsys
) -> None:
    root = _fixture(tmp_path)
    generated = root / "opai" / "_generated_release.py"
    generated.write_text(
        generated.read_text(encoding="utf-8").replace('"0.2.1a1"', '"9.9.9"', 1),
        encoding="utf-8",
    )
    command = importlib.import_module("scripts.check_release_identity")

    assert command.main(["--root", str(root)]) == 1
    output = capsys.readouterr().err
    assert "expected='0.2.1a1'" in output
    assert "actual='9.9.9'" in output
    assert "generated.application_version" in output
    assert str(generated.resolve()) in output
    assert "generate_release_identity.py" in output


def test_current_documentation_identity_drift_is_actionable(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / "docs").mkdir()
    (root / "site").mkdir()
    module = importlib.import_module("opai.release_identity")
    release = module.read_project_release(root / "pyproject.toml")
    readme = module.render_documentation_projection(release, surface="README.md")
    install_projection = module.render_documentation_projection(
        release, surface="docs/INSTALL_PROOF.md"
    )
    (root / "README.md").write_text(readme + "\n", encoding="utf-8")
    install = root / "docs" / "INSTALL_PROOF.md"
    install.write_text(
        install_projection.replace("0.2.1a1", "9.9.9", 1) + "\n",
        encoding="utf-8",
    )
    site_projection = module.render_documentation_projection(
        release, surface="site/index.html"
    )
    (root / "site" / "index.html").write_text(site_projection + "\n", encoding="utf-8")

    drift = _validation_module().validate_release_identity(root)
    documentation = next(
        item for item in drift if item.surface == "documentation.docs/INSTALL_PROOF.md"
    )

    assert "application_version=0.2.1a1" in documentation.expected
    assert "application_version=9.9.9" in documentation.actual
    assert documentation.remediation.endswith("generate_release_identity.py")


def test_site_release_identity_drift_is_actionable(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / "docs").mkdir()
    (root / "site").mkdir()
    module = importlib.import_module("opai.release_identity")
    release = module.read_project_release(root / "pyproject.toml")
    for surface in ("README.md", "docs/INSTALL_PROOF.md", "site/index.html"):
        path = root / surface
        path.parent.mkdir(parents=True, exist_ok=True)
        projection = module.render_documentation_projection(release, surface=surface)
        if surface == "site/index.html":
            projection = projection.replace(release.display_name, "OPai 9.9.9")
        path.write_text(projection + "\n", encoding="utf-8")

    drift = _validation_module().validate_release_identity(root)
    documentation = next(
        item for item in drift if item.surface == "documentation.site/index.html"
    )

    assert release.display_name in documentation.expected
    assert "OPai 9.9.9" in documentation.actual
    assert documentation.remediation.endswith("generate_release_identity.py")
