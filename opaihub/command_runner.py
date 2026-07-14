from __future__ import annotations

import os
import re
import shlex
import subprocess  # nosec
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .proc import no_window_kwargs
from .sandbox import classify_command


SECRET_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{16,})"
    ),
    re.compile(r"(?i)(authorization:\s*bearer\s+)([A-Za-z0-9_\-./+=]{16,})"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    # OpenAI project keys (sk-proj-*) and Anthropic keys (sk-ant-api03-*)
    # contain internal hyphens/underscores, unlike legacy sk-* keys.
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
]


@dataclass
class HubCommandResult:
    command: str
    argv: list[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    policy: dict[str, Any]
    executed: bool = True
    timed_out: bool = False

    @property
    def combined_output(self) -> str:
        if self.stderr:
            return f"{self.stdout}\n{self.stderr}".strip()
        return self.stdout.strip()


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:

        def repl(match: re.Match[str]) -> str:
            if match.lastindex and match.lastindex >= 2:
                return match.group(0).replace(
                    match.group(match.lastindex), "[REDACTED]"
                )
            return "[REDACTED_SECRET]"

        redacted = pattern.sub(repl, redacted)
    return redacted


def split_command(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command, posix=True)
    return [str(part) for part in command]


def display_command(command: str | Sequence[str]) -> str:
    if isinstance(command, str):
        return command
    if os.name == "nt":
        return subprocess.list2cmdline([str(part) for part in command])
    return shlex.join(str(part) for part in command)


def run_policy_command(
    command: str | Sequence[str],
    cwd: Path,
    timeout: int = 120,
    *,
    project_root: Path | None = None,
    allow_confirmed: bool = False,
    extra_env: dict[str, str] | None = None,
) -> HubCommandResult:
    start = time.perf_counter()
    display = display_command(command)
    argv = split_command(command)
    policy = classify_command(display, project_root or cwd)

    if not argv:
        return HubCommandResult(
            command=display,
            argv=[],
            cwd=str(cwd),
            returncode=127,
            stdout="",
            stderr="No command provided.",
            duration_seconds=time.perf_counter() - start,
            policy={**policy, "decision": "deny"},
            executed=False,
        )

    if policy["decision"] == "deny":
        return HubCommandResult(
            command=display,
            argv=argv,
            cwd=str(cwd),
            returncode=126,
            stdout="",
            stderr=policy["reason"],
            duration_seconds=time.perf_counter() - start,
            policy=policy,
            executed=False,
        )

    if policy["decision"] == "confirm" and not allow_confirmed:
        return HubCommandResult(
            command=display,
            argv=argv,
            cwd=str(cwd),
            returncode=125,
            stdout="",
            stderr=policy["reason"],
            duration_seconds=time.perf_counter() - start,
            policy=policy,
            executed=False,
        )

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    try:
        completed = subprocess.run(  # nosec
            argv,
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
            # No flashing console window on Windows: this runs git/tests on
            # every message via collect_evidence (issue: burst of terminals).
            **no_window_kwargs(),
        )
        return HubCommandResult(
            command=display,
            argv=argv,
            cwd=str(cwd),
            returncode=completed.returncode,
            stdout=redact(completed.stdout),
            stderr=redact(completed.stderr),
            duration_seconds=time.perf_counter() - start,
            policy=policy,
        )
    except FileNotFoundError as exc:
        return HubCommandResult(
            command=display,
            argv=argv,
            cwd=str(cwd),
            returncode=127,
            stdout="",
            stderr=redact(str(exc)),
            duration_seconds=time.perf_counter() - start,
            policy=policy,
            executed=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        return HubCommandResult(
            command=display,
            argv=argv,
            cwd=str(cwd),
            returncode=124,
            stdout=redact(str(stdout)),
            stderr=redact(str(stderr)),
            duration_seconds=time.perf_counter() - start,
            policy=policy,
            timed_out=True,
        )
