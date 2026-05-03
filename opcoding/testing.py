from __future__ import annotations

from pathlib import Path
from typing import Any

from .context_manager import load_profile
from .gitops import changed_files
from .scanner import scan_project
from .utils import append_jsonl, now_iso, project_op_dir, run_command, write_text


def detect_test_command(root: Path, full: bool = False) -> str | None:
    profile = load_profile(root) or scan_project(root)
    commands = profile.get("commands", {})
    if full and commands.get("test"):
        return commands["test"]
    files = changed_files(root)
    test_files = [
        file for file in files if "test" in file.lower() or "spec" in file.lower()
    ]
    if test_files and commands.get("targeted_test"):
        return commands["targeted_test"].replace("{files}", " ".join(test_files))
    if commands.get("test"):
        return commands["test"]
    return None


def run_tests(root: Path, command: str, timeout: int = 240) -> dict[str, Any]:
    result = run_command(command, root, timeout=timeout)
    log = {
        "created_at": now_iso(),
        "command": command,
        "returncode": result.returncode,
        "duration_seconds": round(result.duration_seconds, 3),
        "timed_out": result.timed_out,
    }
    op_dir = project_op_dir(root)
    append_jsonl(op_dir / "logs" / "test-runs.jsonl", log)
    logfile = (
        op_dir / "logs" / "commands" / f"test-{int(__import__('time').time())}.log"
    )
    write_text(
        logfile,
        f"$ {command}\n\n# stdout\n{result.stdout}\n\n# stderr\n{result.stderr}\n",
    )
    analysis = analyze_failure(result.combined_output) if result.returncode != 0 else []
    return {
        **log,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
        "analysis": analysis,
        "logfile": str(logfile),
    }


def analyze_failure(output: str) -> list[str]:
    lines = output.splitlines()
    useful: list[str] = []
    markers = [
        "error",
        "failed",
        "failure",
        "traceback",
        "assert",
        "exception",
        "cannot find",
        "expected",
        "received",
    ]
    for line in lines:
        lowered = line.lower()
        if any(marker in lowered for marker in markers):
            stripped = line.strip()
            if stripped and stripped not in useful:
                useful.append(stripped[:300])
        if len(useful) >= 25:
            break
    return useful


def flaky_note(root: Path, test_name: str, reason: str) -> Path:
    path = project_op_dir(root) / "logs" / "flaky-tests.jsonl"
    append_jsonl(path, {"created_at": now_iso(), "test": test_name, "reason": reason})
    return path
