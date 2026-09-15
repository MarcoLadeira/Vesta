from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest

from _helpers import make_repo

from opai import cli
from opai.cockpit import build_cockpit
from opai.compatibility import runtime_compatibility_payload
from opai.installer import install_project
from opai.release_identity import current_release_identity
from opaihub.gui_pipeline import build_savings_receipt
from opaihub.receipt import build_receipt
from opaihub.support_bundle import build_support_bundle


def _expected_identity() -> dict[str, str]:
    payload = current_release_identity().to_dict()
    payload.pop("metadata_source")
    return payload


def _assert_surface_identity(payload: dict[str, object]) -> None:
    expected = _expected_identity()
    assert payload["version"] == expected["application_version"]
    assert payload["release_stage"] == expected["release_stage"]
    assert payload["release_identity"] == expected
    assert payload["compatibility"] == runtime_compatibility_payload()


def test_machine_readable_cli_version_uses_the_canonical_identity(capsys) -> None:
    assert cli.main(["version", "--json"]) == 0

    _assert_surface_identity(json.loads(capsys.readouterr().out))


def test_top_level_version_flag_reports_version_stage_and_build(capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])

    assert exited.value.code == 0
    output = capsys.readouterr().out
    identity = _expected_identity()
    assert identity["application_version"] in output
    assert identity["release_stage"] in output
    assert identity["build_id"] in output


def test_persisted_support_install_and_receipt_surfaces_share_safe_identity() -> None:
    with (
        tempfile.TemporaryDirectory() as temporary,
        tempfile.TemporaryDirectory() as home,
    ):
        root = make_repo(Path(temporary))
        support = build_support_bundle(root)
        installed = install_project(
            root, install_tools=False, install_superpowers=False, home=Path(home)
        )
        receipt = build_receipt(root, sign=False)
        run_receipt = build_savings_receipt(
            root,
            task="show the exact tested build",
            selected_model="local:test",
            selected_mode="local",
            chosen_tier="L0",
        )
        cockpit = build_cockpit(root)

    for payload in (support, installed, receipt, run_receipt, cockpit):
        _assert_surface_identity(payload)
        assert "metadata_source" not in payload["release_identity"]
