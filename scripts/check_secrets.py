"""Enforce detect-secrets findings instead of trusting its scan exit code.

`detect-secrets scan` intentionally exits zero when it reports findings. This
wrapper compares non-secret historical fixtures by fingerprint and returns a
distinct security failure for any new finding. Scanner/tool failure has a
separate infrastructure exit code. Secret values and hashes are never printed.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - fixed argv, never a shell
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SECURITY_EXIT = 2
INFRASTRUCTURE_EXIT = 3
SCAN_TIMEOUT_SECONDS = 180
EXCLUDED_FILES = (
    r"(^|[/\\])\.git([/\\]|$)|"
    r"(^|[/\\])\.secrets\.baseline$|"
    r"(^|[/\\])(?:__pycache__|\.venv|node_modules|\.pytest_cache|\.hypothesis|"
    r"\.mypy_cache|test-results|playwright-report|\.playwright|build|dist)([/\\]|$)|"
    r"(^|[/\\])[^/\\]+\.egg-info([/\\]|$)|"
    r"(^|[/\\])\.opcoding-tools([/\\]|$)|"
    r"(^|[/\\])\.ruff_cache([/\\]|$)|"
    r"(^|[/\\])\.opcoding([/\\]|$)|"
    r"(^|[/\\])\.vestahub([/\\]|$)|"
    r"(^|[/\\])vesta[/\\]assets[/\\].*\.png$"
)


def _finding_type(value: dict[str, Any]) -> str:
    return str(value.get("type") or value.get("type_of_secret") or "unknown")


def _fingerprints(document: dict[str, Any]) -> set[tuple[str, str, str]]:
    results = document.get("results")
    if not isinstance(results, dict):
        raise ValueError("detect-secrets document has no results mapping")
    fingerprints: set[tuple[str, str, str]] = set()
    for filename, values in results.items():
        if not isinstance(filename, str) or not isinstance(values, list):
            raise ValueError("detect-secrets results are malformed")
        normalized = filename.replace("\\", "/")
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("detect-secrets finding is malformed")
            hashed_secret = value.get("hashed_secret")
            if not isinstance(hashed_secret, str) or len(hashed_secret) != 40:
                raise ValueError("detect-secrets finding has no SHA-1 fingerprint")
            fingerprints.add((normalized, _finding_type(value), hashed_secret))
    return fingerprints


def unapproved_findings(
    current: dict[str, Any], baseline: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return metadata-only descriptions for findings absent from the baseline."""

    approved = _fingerprints(baseline)
    results = current.get("results")
    if not isinstance(results, dict):
        raise ValueError("detect-secrets document has no results mapping")
    unapproved: list[dict[str, Any]] = []
    for filename, values in results.items():
        normalized = str(filename).replace("\\", "/")
        if not isinstance(values, list):
            raise ValueError("detect-secrets results are malformed")
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("detect-secrets finding is malformed")
            fingerprint = (
                normalized,
                _finding_type(value),
                str(value.get("hashed_secret") or ""),
            )
            if fingerprint not in approved:
                unapproved.append(
                    {
                        "filename": normalized,
                        "line_number": int(value.get("line_number") or 0),
                        "type": _finding_type(value),
                    }
                )
    return sorted(
        unapproved,
        key=lambda value: (value["filename"], value["line_number"], value["type"]),
    )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on new detect-secrets findings")
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        baseline = _load_json(args.baseline)
        _fingerprints(baseline)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"secret scan infrastructure blocked: invalid baseline: {error}")
        return INFRASTRUCTURE_EXIT

    command = [
        sys.executable,
        "-m",
        "detect_secrets",
        "scan",
        "--all-files",
        "--exclude-files",
        EXCLUDED_FILES,
    ]
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, no shell
            command,
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=SCAN_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(
            f"secret scan infrastructure blocked: scanner unavailable: {type(error).__name__}"
        )
        return INFRASTRUCTURE_EXIT
    if completed.returncode != 0:
        print(
            f"secret scan infrastructure blocked: scanner exited {completed.returncode}"
        )
        return INFRASTRUCTURE_EXIT

    try:
        current = json.loads(completed.stdout)
        if not isinstance(current, dict):
            raise ValueError("scanner output is not a JSON object")
        findings = unapproved_findings(current, baseline)
    except (ValueError, json.JSONDecodeError) as error:
        print(f"secret scan infrastructure blocked: unreadable scanner output: {error}")
        return INFRASTRUCTURE_EXIT

    if findings:
        print(f"secret scan failed: {len(findings)} unapproved finding(s)")
        for finding in findings:
            print(
                f"  {finding['filename']}:{finding['line_number']} ({finding['type']})"
            )
        print(
            "Review the value; if it is a deliberate non-secret fixture, update the baseline in a reviewed change."
        )
        return SECURITY_EXIT

    count = sum(len(values) for values in current["results"].values())
    print(f"secret scan passed: {count} reviewed baseline finding(s), no new findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
