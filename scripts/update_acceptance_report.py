"""Emit the traceable #633 acceptance scenario inventory from executable tests."""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MINIMUMS = {"A": 45, "B": 30, "C": 30, "D": 24, "E": 32, "F": 14, "G": 10}
FILES = {
    "A": {"test_update_domain.py"},
    "B": {
        "test_update_download.py",
        "test_update_native.py",
        "test_update_network.py",
    },
    "C": {"test_update_service.py"},
    "F": {
        "test_update_release.py",
        "test_update_release_pipeline.py",
    },
}

CHAOS_NAMES = (
    "test_new_discovery_cannot_reuse_a_previous_candidates_staged_artifact",
    "test_signed_metadata_floor_survives_operation_state_loss",
    "test_digest_mismatch_never_becomes_ready_to_install",
    "test_install_rejects_a_deferred_artifact_outside_the_staging_root",
    "test_native_helper_failure_is_reconciled_without_claiming_restart",
    "test_startup_health_reads_real_persisted_schema_and_asset_files",
    "test_failed_post_update_health_quarantines_and_requests_rollback",
    "test_rollback_is_not_claimed_until_the_restored_build_confirms_health",
    "test_rollback_refuses_recovery_artifact_outside_staging_root",
    "test_rollback_failure_never_claims_the_old_version_is_restored",
    "test_retriable_download_can_retry_same_candidate",
)


def _run(command: list[str]) -> list[str]:
    # Commands below are fixed repository test-listing tools.
    completed = subprocess.run(  # nosec B603
        command,
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError("acceptance scenario discovery failed")
    return completed.stdout.splitlines()


def _pytest_scenarios() -> dict[str, list[str]]:
    paths = sorted(
        str(ROOT / "tests" / name) for names in FILES.values() for name in names
    )
    lines = _run([sys.executable, "-m", "pytest", *paths, "--collect-only", "-q"])
    result = {category: [] for category in FILES}
    for line in lines:
        if "::" not in line:
            continue
        filename = Path(line.split("::", 1)[0]).name
        for category, names in FILES.items():
            if filename in names:
                result[category].append(line.replace("\\", "/"))
                break
    return result


def _native_scenarios() -> list[str]:
    tree = ast.parse(
        (ROOT / "scripts" / "qualify_native_update.py").read_text(encoding="utf-8")
    )
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
    return [
        f"{platform}::{name}" for platform in ("windows", "macos") for name in names
    ]


def _extract_chaos_scenarios(categories: dict[str, list[str]]) -> list[str]:
    service = categories["C"]
    chaos = [
        scenario
        for scenario in service
        if any(name in scenario for name in CHAOS_NAMES)
    ]
    categories["C"] = [scenario for scenario in service if scenario not in chaos]
    return chaos


def _web_scenarios() -> tuple[list[str], list[str]]:
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("npx is required to inventory browser scenarios")
    playwright = [
        line.strip()
        for line in _run(
            [
                npx,
                "playwright",
                "test",
                "opai/assets/web/__tests__/e2e/update-check.spec.js",
                "--list",
            ]
        )
        if "[chromium]" in line and "update-check.spec.js:" in line
    ]
    settings = [
        line.strip()
        for line in _run(
            [npx, "vitest", "list", "opai/assets/web/__tests__/settings.test.js"]
        )
        if line.startswith("opai/assets/web/__tests__/settings.test.js >")
    ]
    return playwright, settings


def build_report() -> dict[str, object]:
    categories = _pytest_scenarios()
    categories["G"] = _extract_chaos_scenarios(categories)
    categories["E"] = _native_scenarios()
    playwright, settings = _web_scenarios()
    categories["D"] = [*playwright, *settings]
    for category, minimum in MINIMUMS.items():
        scenarios = categories.get(category, [])
        if len(scenarios) < minimum or len(scenarios) != len(set(scenarios)):
            raise RuntimeError(f"acceptance category {category} is incomplete")
    return {
        "schema_version": 1,
        "minimums": MINIMUMS,
        "counts": {
            category: len(categories[category]) for category in sorted(categories)
        },
        "total": sum(len(scenarios) for scenarios in categories.values()),
        "native_execution_required": True,
        "categories": categories,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.dumps(build_report(), indent=2, sort_keys=True) + "\n"
    except (OSError, RuntimeError, subprocess.SubprocessError, SyntaxError):
        return 2
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
