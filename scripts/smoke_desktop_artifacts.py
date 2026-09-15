"""Verify a finished Vesta desktop artifact outside the source checkout."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess  # nosec B404 - fixed local artifact commands only
import sys
import tempfile
import time
from functools import partial
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vestahub.desktop_artifacts import (  # noqa: E402
    ArtifactReleaseError,
    isolated_artifact_environment,
    native_platform_signature_problems,
    scan_artifact_text,
    smoke_commands,
    verify_bundle,
)
from vestahub.proc import no_window_kwargs  # noqa: E402


def _is_windows() -> bool:
    """Keep OS-specific smoke behavior testable without mutating ``os.name``."""

    return os.name == "nt"


def _system_executable(name: str) -> str:
    """Resolve a platform command before invoking it from release verification."""
    if _is_windows():
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        if system_root:
            candidate = Path(system_root) / "System32" / name
            if candidate.is_file():
                return str(candidate.resolve())
    resolved = shutil.which(name)
    if resolved:
        return str(Path(resolved).resolve())
    raise OSError(f"required system executable is unavailable: {name}")


def _webengine_helpers() -> set[str]:
    if _is_windows():
        completed = subprocess.run(  # nosec B603 - fixed Windows process query
            [
                _system_executable("tasklist.exe"),
                "/FI",
                "IMAGENAME eq QtWebEngineProcess.exe",
                "/FO",
                "CSV",
                "/NH",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
            **no_window_kwargs(),
        )
        rows = csv.reader(io.StringIO(completed.stdout))
        return {
            row[1]
            for row in rows
            if len(row) >= 2 and row[0].casefold() == "qtwebengineprocess.exe"
        }
    completed = subprocess.run(  # nosec B603 - fixed POSIX process query
        [_system_executable("ps"), "-axo", "pid=,comm="],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    return {
        line.split(maxsplit=1)[0]
        for line in completed.stdout.splitlines()
        if "QtWebEngineProcess" in line and line.split(maxsplit=1)
    }


def _run(
    command: list[str], *, cwd: Path, env: dict[str, str], timeout: int
) -> dict[str, object]:
    completed = subprocess.run(  # nosec B603 - command resolves inside verified bundle
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
        **no_window_kwargs(),
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def _remaining_helpers(before: set[str], *, attempts: int = 10) -> set[str]:
    remaining: set[str] = set()
    for _ in range(attempts):
        remaining = _webengine_helpers() - before
        if not remaining:
            return set()
        time.sleep(0.5)
    return remaining


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test a finished Vesta portable desktop artifact."
    )
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument(
        "--windows-signer-thumbprint",
        help="Expected Authenticode signer thumbprint from trusted release metadata",
    )
    parser.add_argument(
        "--macos-team-id",
        help="Expected Developer ID Team ID from trusted release metadata",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    bundle = args.bundle.expanduser().resolve()
    try:
        signature_verifier = partial(
            native_platform_signature_problems,
            windows_signer_thumbprint=args.windows_signer_thumbprint,
            macos_team_id=args.macos_team_id,
        )
        verification = verify_bundle(
            bundle,
            signature_verifier=signature_verifier,
        )
        artifact_findings = scan_artifact_text(bundle)
        if not verification["ok"] or artifact_findings:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "verification": verification,
                        "artifact_findings": artifact_findings,
                    },
                    indent=2,
                )
            )
            return 1
        owned_work = args.work_dir is None
        temporary = (
            tempfile.TemporaryDirectory(prefix="vesta-artifact-smoke-")
            if owned_work
            else None
        )
        work = (
            Path(temporary.name)
            if temporary is not None
            else args.work_dir.expanduser().resolve()
        )
        work.mkdir(parents=True, exist_ok=True)
        home = work / "home"
        fixture = home / "fixture-project"
        fixture.mkdir(parents=True, exist_ok=True)
        (fixture / "pyproject.toml").write_text(
            "[project]\nname = 'vesta-artifact-smoke'\nversion = '0'\n", encoding="utf-8"
        )
        (fixture / "app.py").write_text("value = 1\n", encoding="utf-8")
        environment = isolated_artifact_environment(home)
        result_path = home / "artifact-gui-result.json"
        commands = smoke_commands(bundle, home, result_path)
        logs_dir = work / "logs"
        logs_dir.mkdir(exist_ok=True)
        executions: list[dict[str, object]] = []
        before_helpers = _webengine_helpers()
        for index, command in enumerate(commands):
            run = _run(command, cwd=fixture, env=environment, timeout=args.timeout)
            executions.append(run)
            (logs_dir / f"{index}.log").write_text(
                str(run["stdout"]) + "\n" + str(run["stderr"]), encoding="utf-8"
            )
            if run["returncode"] != 0:
                break
        remaining_helpers = _remaining_helpers(before_helpers)
        try:
            gui_result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            gui_result = {"ok": False, "status": "missing_or_invalid_result"}
        log_findings = scan_artifact_text(logs_dir)
        ok = (
            all(run["returncode"] == 0 for run in executions)
            and len(executions) == len(commands)
            and bool(gui_result.get("ok"))
            and not remaining_helpers
            and not log_findings
        )
        report = {
            "ok": ok,
            "verification": verification,
            "executions": executions,
            "gui_result": gui_result,
            "remaining_webengine_helpers": sorted(remaining_helpers),
            "artifact_findings": artifact_findings,
            "log_findings": log_findings,
        }
        print(json.dumps(report, indent=2))
        if temporary is not None:
            temporary.cleanup()
        return 0 if ok else 1
    except (ArtifactReleaseError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
