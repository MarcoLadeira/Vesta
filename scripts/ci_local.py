"""Run OPai's CI gate locally — a pipeline you control, no GitHub minutes.

Mirrors the "Test and package" job in `.github/workflows/ci.yml` so you can get a
green/red verdict without depending on GitHub-hosted runners (useful when the
private-repo Actions minutes are capped). Offline-safe checks run by default;
the two network-dependent audits (pip-audit, detect-secrets) are opt-in.

Usage:
    python scripts/ci_local.py            # ruff, tests, validate, bandit
    python scripts/ci_local.py --full     # + pip-audit + detect-secrets (network)
    python scripts/ci_local.py --fast     # skip bandit (ruff + tests + validate)

Exit code is 0 only if every selected step passed — same contract as CI.
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404 - fixed argv, never a shell
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Step:
    def __init__(self, name: str, argv: list[str], *, network: bool = False) -> None:
        self.name = name
        self.argv = argv
        self.network = network


# Offline-safe gate (matches CI order): format, lint, tests, registry, security.
CORE_STEPS = [
    Step(
        "ruff format --check", [sys.executable, "-m", "ruff", "format", "--check", "."]
    ),
    Step("ruff check", [sys.executable, "-m", "ruff", "check", "."]),
    Step("unittest", [sys.executable, "-m", "unittest", "discover", "-s", "tests"]),
    Step("registry validate", [sys.executable, "-m", "opaihub", "validate"]),
    Step(
        "bandit",
        [sys.executable, "-m", "bandit", "-r", "opai", "opaihub", "opcoding", "-q"],
    ),
]

NETWORK_STEPS = [
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
        network=True,
    ),
    Step(
        "detect-secrets",
        [sys.executable, "-m", "detect_secrets", "scan", "--all-files"],
        network=True,
    ),
]


def _run(step: Step) -> tuple[bool, float]:
    start = time.monotonic()
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            step.argv, cwd=str(ROOT), check=False
        )
        ok = completed.returncode == 0
    except FileNotFoundError:
        print(f"  ! {step.name}: tool not installed — skipped", flush=True)
        return True, 0.0
    return ok, time.monotonic() - start


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the OPai CI gate locally.")
    parser.add_argument("--full", action="store_true", help="also run network audits")
    parser.add_argument("--fast", action="store_true", help="skip bandit for speed")
    args = parser.parse_args(argv)

    steps = list(CORE_STEPS)
    if args.fast:
        steps = [s for s in steps if s.name != "bandit"]
    if args.full:
        steps += NETWORK_STEPS

    results: list[tuple[str, bool, float]] = []
    for step in steps:
        print(f"\n=== {step.name} ===", flush=True)
        ok, secs = _run(step)
        results.append((step.name, ok, secs))
        if not ok:
            # Fail fast on the first red step, like a real pipeline gate.
            break

    print("\n" + "=" * 48)
    passed = all(ok for _, ok, _ in results)
    for name, ok, secs in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<22} {secs:6.1f}s")
    ran = len(results)
    print("=" * 48)
    print(
        f"  {'GREEN' if passed and ran == len(steps) else 'RED'} - {ran}/{len(steps)} steps run"
    )
    return 0 if passed and ran == len(steps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
