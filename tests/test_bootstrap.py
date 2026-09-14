from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from opai import bootstrap


ROOT = Path(__file__).resolve().parents[1]


class SpecFinder:
    def __init__(self, *missing: str) -> None:
        self.missing = frozenset(missing)

    def __call__(self, name: str):
        return None if name in self.missing else SimpleNamespace(name=name)


def _installed_version(_name: str) -> str:
    return "0.2.1a1"


def test_bootstrap_module_has_no_optional_dependency_imports() -> None:
    imported = {
        value.split(".", 1)[0]
        for value in bootstrap.__dict__
        if value in {"yaml", "cryptography", "packaging", "PySide6"}
    }
    assert imported == set()


def test_missing_pyyaml_stops_before_deep_import_and_is_not_invalid_json(
    capsys,
) -> None:
    imported: list[str] = []

    def importer(name: str):
        imported.append(name)
        raise AssertionError("deep application import must not run")

    code = bootstrap.run_cli(
        ["doctor"],
        source_root=ROOT,
        spec_finder=SpecFinder("yaml"),
        distribution_lookup=_installed_version,
        importer=importer,
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    assert imported == []
    error = capsys.readouterr().err
    assert "missing_dependency" in error
    assert "PyYAML" in error
    assert "invalid JSON" not in error
    assert "Traceback" not in error


def test_machine_readable_bootstrap_failure_has_a_stable_safe_schema(capsys) -> None:
    code = bootstrap.run_cli(
        ["doctor", "--json"],
        source_root=ROOT,
        spec_finder=SpecFinder("yaml"),
        distribution_lookup=_installed_version,
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "category": "missing_dependency",
        "component": "PyYAML",
        "message": "Vesta requires PyYAML before configuration can be loaded.",
        "ok": False,
        "remediation": "Run `python -m pip install -e .` and retry.",
        "schema_version": 1,
        "startup_mode": "source_checkout",
    }


def test_version_json_remains_available_without_optional_dependencies(capsys) -> None:
    code = bootstrap.run_cli(
        ["version", "--json"],
        source_root=ROOT,
        spec_finder=SpecFinder("yaml", "cryptography", "packaging"),
        distribution_lookup=_installed_version,
        importer=lambda _name: pytest.fail("version must not import opai.cli"),
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == "0.2.1a1"
    assert payload["release_identity"]["build_id"] == "development"


def test_cli_help_remains_available_without_runtime_dependencies() -> None:
    received: list[str] = []

    def main(arguments: list[str]) -> int:
        received.extend(arguments)
        return 0

    code = bootstrap.run_cli(
        ["ask", "--help"],
        source_root=ROOT,
        spec_finder=SpecFinder("yaml", "cryptography", "packaging"),
        distribution_lookup=_installed_version,
        importer=lambda _name: SimpleNamespace(main=main),
    )

    assert code == 0
    assert received == ["ask", "--help"]


@pytest.mark.parametrize(
    ("missing", "component"),
    [
        (("PySide6",), "PySide6"),
        (("PySide6.QtWebEngineWidgets",), "QtWebEngine"),
        (("PySide6.QtWebChannel",), "QtWebEngine"),
    ],
)
def test_desktop_dependencies_fail_with_the_exact_component(
    missing: tuple[str, ...], component: str, capsys
) -> None:
    code = bootstrap.run_desktop(
        [],
        source_root=ROOT,
        spec_finder=SpecFinder(*missing),
        distribution_lookup=_installed_version,
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    error = capsys.readouterr().err
    assert "missing_dependency" in error
    assert component in error


def test_windowed_launcher_receives_the_safe_failure_for_native_display() -> None:
    displayed: list[bootstrap.BootstrapFailure] = []

    code = bootstrap.run_desktop(
        [],
        source_root=ROOT,
        spec_finder=SpecFinder("PySide6"),
        distribution_lookup=_installed_version,
        failure_handler=displayed.append,
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    assert [failure.category for failure in displayed] == ["missing_dependency"]
    assert "Traceback" not in displayed[0].message


def test_classic_desktop_does_not_require_qt_webengine() -> None:
    imported: list[str] = []

    def importer(name: str):
        imported.append(name)
        return SimpleNamespace(gui_main=lambda: 0)

    assert (
        bootstrap.run_desktop(
            ["--classic"],
            source_root=ROOT,
            spec_finder=SpecFinder(
                "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel"
            ),
            distribution_lookup=_installed_version,
            importer=importer,
        )
        == 0
    )
    assert imported == ["opai.cli"]


def test_headless_gui_smoke_does_not_require_qt() -> None:
    module = SimpleNamespace(main=lambda _argv: 0)

    assert (
        bootstrap.run_cli(
            ["gui", "--once"],
            source_root=ROOT,
            spec_finder=SpecFinder("PySide6"),
            distribution_lookup=_installed_version,
            importer=lambda _name: module,
        )
        == 0
    )


def test_raw_source_without_distribution_metadata_is_explicitly_unsupported(
    tmp_path: Path, capsys
) -> None:
    package = tmp_path / "raw-copy"
    package.mkdir()

    code = bootstrap.run_cli(
        ["doctor"],
        source_root=package,
        spec_finder=SpecFinder(),
        distribution_lookup=lambda _name: (_ for _ in ()).throw(
            importlib.metadata.PackageNotFoundError("opai")
        ),
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    error = capsys.readouterr().err
    assert "unsupported_startup_mode" in error
    assert "python -m pip install" in error


def test_corrupt_distribution_metadata_has_its_own_category(
    tmp_path: Path, capsys
) -> None:
    code = bootstrap.run_cli(
        ["doctor"],
        source_root=tmp_path,
        spec_finder=SpecFinder(),
        distribution_lookup=lambda _name: (_ for _ in ()).throw(ValueError("bad")),
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    assert "package_metadata_unavailable" in capsys.readouterr().err


def test_packaged_payload_without_dist_info_is_not_misclassified_as_raw_source(
    tmp_path: Path, capsys
) -> None:
    package = tmp_path / "site-packages"
    embedded = package / "opai" / "_embedded_build.json"
    embedded.parent.mkdir(parents=True)
    embedded.write_text("{}\n", encoding="utf-8")

    code = bootstrap.run_cli(
        ["doctor"],
        source_root=package,
        spec_finder=SpecFinder(),
        distribution_lookup=lambda _name: (_ for _ in ()).throw(
            importlib.metadata.PackageNotFoundError("opai")
        ),
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    error = capsys.readouterr().err
    assert "package_metadata_unavailable" in error
    assert "unsupported_startup_mode" not in error


def test_installed_package_never_uses_neighboring_checkout_identity(
    tmp_path: Path,
) -> None:
    package = tmp_path / "site-packages"
    package.mkdir()
    neighboring = tmp_path / "neighbor"
    neighboring.mkdir()
    (neighboring / ".git").mkdir()
    (neighboring / "pyproject.toml").write_text(
        '[project]\nname="opai"\nversion="9.9.9"\n', encoding="utf-8"
    )
    original = Path.cwd()
    try:
        os.chdir(neighboring)
        context = bootstrap.preflight_startup(
            [],
            source_root=package,
            spec_finder=SpecFinder(),
            distribution_lookup=_installed_version,
            validate_integrity=False,
        )
    finally:
        os.chdir(original)

    assert context.startup_mode == "installed_distribution"
    assert context.application_version == "0.2.1a1"


def test_source_checkout_ignores_an_unrelated_installed_distribution() -> None:
    context = bootstrap.preflight_startup(
        [],
        source_root=ROOT,
        spec_finder=SpecFinder(),
        distribution_lookup=lambda _name: "9.9.9",
    )

    assert context.startup_mode == "source_checkout"
    assert context.application_version == "0.2.1a1"


def test_missing_packaged_asset_stops_before_qt_import(tmp_path: Path, capsys) -> None:
    assets = tmp_path / "assets"
    shutil.copytree(ROOT / "opai" / "assets", assets)
    (assets / "web" / "index.html").unlink()
    imported: list[str] = []

    code = bootstrap.run_desktop(
        [],
        source_root=ROOT,
        asset_root=assets,
        spec_finder=SpecFinder(),
        distribution_lookup=_installed_version,
        importer=lambda name: imported.append(name),
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    assert imported == []
    error = capsys.readouterr().err
    assert "missing_packaged_asset" in error
    assert "assets/web/index.html" in error


def test_mismatched_installed_assets_fail_before_application_import(
    tmp_path: Path, capsys
) -> None:
    from opai.asset_identity import asset_manifest
    from opai.compatibility import runtime_compatibility_payload

    assets = tmp_path / "assets"
    shutil.copytree(ROOT / "opai" / "assets", assets)
    metadata = tmp_path / "_embedded_build.json"
    metadata.write_text(
        json.dumps(
            {
                "assets": asset_manifest(assets),
                "compatibility": runtime_compatibility_payload(),
            }
        ),
        encoding="utf-8",
    )
    (assets / "web" / "app.js").write_text("partial update", encoding="utf-8")

    code = bootstrap.run_desktop(
        [],
        source_root=tmp_path,
        asset_root=assets,
        metadata_paths=(metadata,),
        spec_finder=SpecFinder(),
        distribution_lookup=_installed_version,
        importer=lambda _name: pytest.fail("application import must not run"),
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    assert "package_integrity_failure" in capsys.readouterr().err


def test_incompatible_schema_fails_before_any_runtime_mutation(
    tmp_path: Path, capsys
) -> None:
    from opai.asset_identity import asset_manifest
    from opai.compatibility import runtime_compatibility_payload

    assets = tmp_path / "assets"
    shutil.copytree(ROOT / "opai" / "assets", assets)
    compatibility = runtime_compatibility_payload()
    compatibility["update_schema_version"] = 999
    metadata = tmp_path / "release-identity.json"
    metadata.write_text(
        json.dumps({"assets": asset_manifest(assets), "compatibility": compatibility}),
        encoding="utf-8",
    )

    code = bootstrap.run_desktop(
        [],
        source_root=tmp_path,
        asset_root=assets,
        metadata_paths=(metadata,),
        spec_finder=SpecFinder(),
        distribution_lookup=_installed_version,
        importer=lambda _name: pytest.fail("runtime mutation boundary was crossed"),
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    error = capsys.readouterr().err
    assert "incompatible_schema" in error
    assert "update_schema_version" in error


def test_newer_persisted_project_schema_fails_before_runtime_mutation(
    tmp_path: Path, capsys
) -> None:
    project = tmp_path / "project"
    state_path = project / ".opaihub" / "project.json"
    state_path.parent.mkdir(parents=True)
    persisted = {"schema_version": 999, "sentinel": "must-remain"}
    state_path.write_text(json.dumps(persisted), encoding="utf-8")
    imported: list[str] = []

    code = bootstrap.run_cli(
        ["doctor", "--project", str(project)],
        source_root=ROOT,
        spec_finder=SpecFinder(),
        distribution_lookup=_installed_version,
        importer=lambda name: imported.append(name),
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    assert imported == []
    assert json.loads(state_path.read_text(encoding="utf-8")) == persisted
    error = capsys.readouterr().err
    assert "incompatible_schema" in error
    assert "project-state" in error


def test_implicit_nested_project_schema_fails_before_runtime_import(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    project = tmp_path / "project"
    nested = project / "src" / "package"
    nested.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "1"\n', encoding="utf-8"
    )
    state_path = project / ".opaihub" / "project.json"
    state_path.parent.mkdir()
    state_path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    imported: list[str] = []
    monkeypatch.chdir(nested)

    code = bootstrap.run_cli(
        ["doctor"],
        source_root=ROOT,
        spec_finder=SpecFinder(),
        distribution_lookup=_installed_version,
        importer=lambda name: imported.append(name),
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    assert imported == []
    assert "incompatible_schema" in capsys.readouterr().err


def test_malformed_user_configuration_is_classified_without_a_traceback(
    capsys,
) -> None:
    module = SimpleNamespace(
        main=lambda _argv: (_ for _ in ()).throw(
            json.JSONDecodeError("secret raw config", "{}", 0)
        )
    )

    code = bootstrap.run_cli(
        ["doctor"],
        source_root=ROOT,
        spec_finder=SpecFinder(),
        distribution_lookup=_installed_version,
        importer=lambda _name: module,
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    error = capsys.readouterr().err
    assert "malformed_user_configuration" in error
    assert "secret raw config" not in error
    assert "Traceback" not in error


def test_real_malformed_yaml_is_classified_as_user_configuration(
    tmp_path: Path, capsys
) -> None:
    from opaihub import loader

    registry = tmp_path / "tools.yaml"
    registry.write_text("tools: [unterminated", encoding="utf-8")
    module = SimpleNamespace(main=lambda _argv: loader.load_registry(registry))

    code = bootstrap.run_cli(
        ["doctor"],
        source_root=ROOT,
        spec_finder=SpecFinder(),
        distribution_lookup=_installed_version,
        importer=lambda _name: module,
    )

    assert code == bootstrap.BOOTSTRAP_EXIT_CODE
    error = capsys.readouterr().err
    assert "malformed_user_configuration" in error
    assert "Traceback" not in error


def test_all_opai_entrypoints_use_the_bootstrap_boundary() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'opai = "opai.bootstrap:cli_main"' in pyproject
    assert 'op = "opai.bootstrap:cli_main"' in pyproject
    assert 'opai-gui = "opai.bootstrap:desktop_main"' in pyproject
    assert "bootstrap" in (ROOT / "opai" / "__main__.py").read_text(encoding="utf-8")
    assert "bootstrap" in (ROOT / "scripts" / "desktop_cli_entry.py").read_text(
        encoding="utf-8"
    )
    assert "bootstrap" in (ROOT / "scripts" / "desktop_gui_entry.py").read_text(
        encoding="utf-8"
    )
