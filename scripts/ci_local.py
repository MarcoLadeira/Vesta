"""Run OPai CI profiles locally and write fail-closed qualification evidence.

Profiles mirror the repository's CI topology:

* ``fast`` is the credential-free pull-request gate.
* ``full`` adds network-dependent supply-chain and secret scans.
* ``native`` validates an isolated wheel install on the current operating system.
* ``provider-canary`` and ``release`` deliberately fail closed unless their
  protected evidence is supplied by their owning workflow.

Every invocation writes a JSON manifest.  A required check that cannot run is
not a pass: it is recorded as unavailable and the profile exits non-zero.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import subprocess  # nosec B404 - fixed argv, never a shell
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1
PROFILE_VERSION = 1
DEFAULT_MANIFEST = ROOT / ".opaihub" / "ci-evidence" / "local.json"


@dataclass(frozen=True)
class Step:
    """One explicitly declared quality check.

    ``required_modules`` is preflighted before the command runs so the common
    ``python -m tool`` case cannot turn a missing package into a false green.
    """

    name: str
    argv: list[str]
    required_modules: tuple[str, ...] = ()
    network: bool = False
    required: bool = True


FAST_STEPS = (
    Step(
        "ruff-format",
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        required_modules=("ruff",),
    ),
    Step(
        "ruff-lint",
        [sys.executable, "-m", "ruff", "check", "."],
        required_modules=("ruff",),
    ),
    Step(
        "lifecycle-projection-drift",
        [sys.executable, "scripts/generate_lifecycle.py", "--check"],
    ),
    Step(
        "python-unittest", [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
    ),
    Step(
        "registry-validation",
        [sys.executable, "-m", "opaihub", "validate"],
        required_modules=("opaihub",),
    ),
    Step(
        "bandit",
        [sys.executable, "-m", "bandit", "-r", "opai", "opaihub", "opcoding", "-q"],
        required_modules=("bandit",),
    ),
)

NETWORK_STEPS = (
    Step(
        "python-pytest",
        [sys.executable, "-m", "pytest", "tests", "-q"],
        required_modules=("pytest",),
    ),
    Step(
        "pip-audit",
        [
            sys.executable,
            "-m",
            "pip_audit",
            ".",
            "--progress-spinner",
            "off",
            "--skip-editable",
        ],
        required_modules=("pip_audit",),
        network=True,
    ),
    Step(
        "detect-secrets",
        [
            sys.executable,
            "-m",
            "detect_secrets",
            "scan",
            "--all-files",
            "--exclude-files",
            r"(^|[\\/])\.opcoding-tools([\\/]|$)|(^|[\\/])\.ruff_cache([\\/]|$)|(^|[\\/])\.opcoding([\\/]|$)|(^|[\\/])\.opaihub([\\/]|$)|(^|[\\/])opai[\\/]assets[\\/].*\.png$",
            "--exclude-lines",
            "MORPH_API_KEY|api_key_env|api_key_present",
        ],
        required_modules=("detect_secrets",),
        network=True,
    ),
)

NATIVE_STEPS = (
    Step(
        "isolated-wheel-smoke",
        [sys.executable, "scripts/smoke-install.py"],
        required_modules=("setuptools",),
    ),
)

PROFILE_STEPS: dict[str, tuple[Step, ...]] = {
    "fast": FAST_STEPS,
    "full": FAST_STEPS + NETWORK_STEPS,
    "native": NATIVE_STEPS,
    # This profile's secret-bearing execution belongs only to a protected
    # workflow.  Its empty step list is made a required blocked check below.
    "provider-canary": (),
    "release": FAST_STEPS + NETWORK_STEPS + NATIVE_STEPS,
}

_MODULE_DISTRIBUTIONS = {
    "bandit": "bandit",
    "detect_secrets": "detect-secrets",
    "opaihub": "opai",
    "pip_audit": "pip-audit",
    "pytest": "pytest",
    "ruff": "ruff",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git_revision() -> str | None:
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _tool_versions(steps: tuple[Step, ...]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {"python": sys.version.split()[0]}
    for module in sorted(
        {module for step in steps for module in step.required_modules}
    ):
        distribution = _MODULE_DISTRIBUTIONS.get(module, module)
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def _missing_modules(step: Step) -> list[str]:
    return [
        module
        for module in step.required_modules
        if importlib.util.find_spec(module) is None
    ]


def _check_record(
    step: Step,
    *,
    execution: str,
    outcome: str,
    duration_seconds: float,
    message: str | None = None,
    returncode: int | None = None,
    failure_class: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": step.name,
        "required": step.required,
        "network": step.network,
        "command": step.argv,
        "status": {"execution": execution, "outcome": outcome},
        "duration_seconds": round(duration_seconds, 3),
    }
    if message:
        record["message"] = message
    if returncode is not None:
        record["returncode"] = returncode
    if failure_class is not None:
        record["failure_class"] = failure_class
    return record


def _run(step: Step) -> dict[str, Any]:
    """Execute a check and classify missing tooling as an explicit failure."""

    missing = _missing_modules(step)
    if missing:
        message = f"required module unavailable: {', '.join(missing)}"
        print(f"  ! {step.name}: {message}", flush=True)
        return _check_record(
            step,
            execution="unavailable",
            outcome="failed" if step.required else "skipped",
            duration_seconds=0.0,
            message=message,
            failure_class="infrastructure",
        )

    start = time.monotonic()
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            step.argv,
            cwd=str(ROOT),
            check=False,
        )
    except FileNotFoundError:
        duration = time.monotonic() - start
        message = f"required executable unavailable: {step.argv[0]}"
        print(f"  ! {step.name}: {message}", flush=True)
        return _check_record(
            step,
            execution="unavailable",
            outcome="failed" if step.required else "skipped",
            duration_seconds=duration,
            message=message,
            failure_class="infrastructure",
        )
    except OSError as error:
        duration = time.monotonic() - start
        message = f"infrastructure error starting check: {error}"
        print(f"  ! {step.name}: {message}", flush=True)
        return _check_record(
            step,
            execution="unavailable",
            outcome="failed" if step.required else "skipped",
            duration_seconds=duration,
            message=message,
            failure_class="infrastructure",
        )

    duration = time.monotonic() - start
    return _check_record(
        step,
        execution="executed",
        outcome="passed" if completed.returncode == 0 else "failed",
        duration_seconds=duration,
        returncode=completed.returncode,
        failure_class="product" if completed.returncode else None,
    )


def _blocked_record(check_id: str, message: str) -> dict[str, Any]:
    return {
        "id": check_id,
        "required": True,
        "network": False,
        "command": [],
        "status": {"execution": "unavailable", "outcome": "failed"},
        "duration_seconds": 0.0,
        "message": message,
        "failure_class": "infrastructure",
    }


def _candidate_record(
    candidate_sha: str | None, revision: str | None
) -> dict[str, Any] | None:
    if candidate_sha is None:
        return _blocked_record(
            "candidate-sha",
            "release qualification requires --candidate-sha for the immutable candidate",
        )
    if revision is None:
        return _blocked_record(
            "candidate-sha", "could not determine the checked-out commit SHA"
        )
    if candidate_sha != revision:
        return {
            "id": "candidate-sha",
            "required": True,
            "network": False,
            "command": ["git", "rev-parse", "HEAD"],
            "status": {"execution": "executed", "outcome": "failed"},
            "duration_seconds": 0.0,
            "message": f"candidate SHA {candidate_sha} does not match checked-out SHA {revision}",
            "failure_class": "infrastructure",
        }
    return {
        "id": "candidate-sha",
        "required": True,
        "network": False,
        "command": ["git", "rev-parse", "HEAD"],
        "status": {"execution": "executed", "outcome": "passed"},
        "duration_seconds": 0.0,
        "message": "candidate SHA matches checked-out SHA",
    }


def _write_manifest(path: Path, evidence: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as temporary_file:
        json.dump(evidence, temporary_file, indent=2, sort_keys=True)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)
    temporary_path.replace(path)


def _verdict(checks: list[dict[str, Any]], profile: str) -> tuple[str, str, str]:
    unavailable = [
        check
        for check in checks
        if check["required"] and check["status"]["execution"] == "unavailable"
    ]
    failures = [
        check
        for check in checks
        if check["required"] and check["status"]["outcome"] == "failed"
    ]
    if profile in {"release", "provider-canary"} and failures:
        if any(check["id"] == "candidate-sha" for check in failures):
            return "infrastructure_blocked", "candidate_sha_mismatch", "infrastructure"
        return (
            "infrastructure_blocked",
            "protected_qualification_unavailable",
            "infrastructure",
        )
    if unavailable:
        return "failed", "required_check_unavailable", "infrastructure"
    if failures:
        return "failed", "required_check_failed", "product"
    return "passed", "all_required_checks_passed", "none"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a fail-closed OPai CI profile locally."
    )
    parser.add_argument("--profile", choices=sorted(PROFILE_STEPS), default="fast")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--candidate-sha",
        help="Immutable commit SHA required by the release profile.",
    )
    compatibility = parser.add_mutually_exclusive_group()
    compatibility.add_argument("--fast", action="store_true", help=argparse.SUPPRESS)
    compatibility.add_argument("--full", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    profile = "fast" if args.fast else "full" if args.full else args.profile
    steps = PROFILE_STEPS[profile]
    started_at = _now()
    started = time.monotonic()
    revision = _git_revision()
    checks: list[dict[str, Any]] = []

    if profile == "release":
        candidate = _candidate_record(args.candidate_sha, revision)
        if candidate is not None:
            checks.append(candidate)
    if profile in {"provider-canary", "release"}:
        checks.append(
            _blocked_record(
                "provider-canary-evidence",
                "provider canaries may only run in the protected provider workflow; "
                "attach its signed evidence before qualifying this profile",
            )
        )

    for step in steps:
        print(f"\n=== {step.name} ===", flush=True)
        checks.append(_run(step))

    verdict, reason, classification = _verdict(checks, profile)
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "profile": {"name": profile, "version": PROFILE_VERSION},
        "commit_sha": revision,
        "candidate_sha": args.candidate_sha,
        "started_at": started_at,
        "finished_at": _now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "environment": {
            "platform": platform.platform(),
            "python_implementation": platform.python_implementation(),
            "ci": bool(os.environ.get("CI")),
        },
        "tool_versions": _tool_versions(steps),
        "checks": checks,
        "artifacts": {"manifest": str(args.manifest.resolve())},
        "verdict": verdict,
        "reason": reason,
        "classification": classification,
    }
    _write_manifest(args.manifest, evidence)

    print("\n" + "=" * 72)
    for check in checks:
        status = check["status"]
        print(
            f"  {status['execution'].upper():<11} {status['outcome'].upper():<7} "
            f"{check['id']:<28} {check['duration_seconds']:6.1f}s"
        )
    print("=" * 72)
    print(f"  {verdict.upper()} - {reason}; evidence: {args.manifest.resolve()}")
    return 0 if verdict == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
