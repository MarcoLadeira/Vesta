"""Real per-tool health checks (#2).

``op hub tool health --id ruff`` used to run the generic
``opcoding tools . doctor`` inventory, so a tool could read "healthy" when
OPai had only proved it was *installed*. That is dangerous for a local-first
router: agents trust a stale/broken local tool and then escalate to an
expensive model unnecessarily.

This module runs a cheap, tool-specific command and distinguishes three
levels: **installed** (the binary exists), **runnable** (``--version`` works),
and **passed_on_current_project** (a real check ran clean here). Every check
is argv-only (no shell) and the command runner is injectable, so tests are
hermetic and no real tool runs in CI.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - argv-only tool invocations, never a shell
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .command_runner import redact
from .proc import no_window_kwargs
from .boundary_errors import safe_detail

Runner = Callable[[list[str], Path, int], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class ToolCheckSpec:
    binary: str
    version_argv: tuple[str, ...]
    project_argv: tuple[str, ...]  # cheap project check; () means none
    install_hint: str
    aliases: tuple[str, ...] = ()


# Cheap, read-only, tool-specific commands. project_argv runs in the project
# root and a return code of 0 means "passed on this project".
KNOWN_TOOL_CHECKS: dict[str, ToolCheckSpec] = {
    "ruff": ToolCheckSpec(
        "ruff", ("--version",), ("check", "--quiet", "."), "pip install ruff"
    ),
    "bandit": ToolCheckSpec(
        "bandit", ("--version",), ("-q", "-r", "."), "pip install bandit"
    ),
    "detect-secrets": ToolCheckSpec(
        "detect-secrets",
        ("--version",),
        ("scan",),
        "pip install detect-secrets",
        aliases=("detectsecrets", "detect_secrets"),
    ),
    "markdownlint": ToolCheckSpec(
        "markdownlint-cli2",
        ("--version",),
        (),
        "npm install -g markdownlint-cli2",
        aliases=("markdownlint-cli2",),
    ),
    "pyright": ToolCheckSpec("pyright", ("--version",), (), "npm install -g pyright"),
    "gitleaks": ToolCheckSpec(
        "gitleaks",
        ("version",),
        ("detect", "--no-banner", "--redact"),
        "install gitleaks from github.com/gitleaks/gitleaks",
    ),
    "actionlint": ToolCheckSpec(
        "actionlint",
        ("--version",),
        (),
        "install actionlint from github.com/rhysd/actionlint",
    ),
    "pip-audit": ToolCheckSpec(
        "pip-audit",
        ("--version",),
        ("--progress-spinner", "off", "--skip-editable"),
        "pip install pip-audit",
        aliases=("pip_audit",),
    ),
}


def _resolve_spec(tool_id: str) -> tuple[str, ToolCheckSpec] | None:
    key = str(tool_id or "").strip().lower()
    if key in KNOWN_TOOL_CHECKS:
        return key, KNOWN_TOOL_CHECKS[key]
    for canonical, spec in KNOWN_TOOL_CHECKS.items():
        if key in spec.aliases:
            return canonical, spec
    return None


def known_tool_ids() -> list[str]:
    return sorted(KNOWN_TOOL_CHECKS)


def is_known_tool(tool_id: str) -> bool:
    return _resolve_spec(tool_id) is not None


def _default_runner(
    argv: list[str], cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 - fixed argv list, no shell
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )


def check_tool_health(
    tool_id: str,
    project_root: Path,
    *,
    run: Runner = _default_runner,
    which: Callable[[str], str | None] = shutil.which,
    include_project_check: bool = True,
    timeout: int = 60,
) -> dict[str, Any]:
    """Run a cheap tool-specific health check (#2).

    Returns installed/runnable/passed_on_current_project plus a single
    ``status`` and, when unhealthy, a concrete ``fix``.
    """
    resolved = _resolve_spec(tool_id)
    if resolved is None:
        return {"id": tool_id, "known": False, "status": "unknown"}
    canonical, spec = resolved

    installed = which(spec.binary) is not None
    result: dict[str, Any] = {
        "id": canonical,
        "known": True,
        "binary": spec.binary,
        "installed": installed,
        "runnable": False,
        "passed_on_current_project": None,
        "version": "",
    }
    if not installed:
        result.update(
            status="missing",
            reason=f"{spec.binary} is not installed",
            fix=spec.install_hint,
            command=" ".join([spec.binary, *spec.version_argv]),
        )
        return result

    def _run(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        try:
            return run([spec.binary, *argv], project_root, timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(
                [spec.binary, *argv], 1, "", safe_detail(exc)
            )

    version = _run(spec.version_argv)
    result["runnable"] = version.returncode == 0
    result["version"] = redact((version.stdout or version.stderr or "").strip())[:80]
    if not result["runnable"]:
        result.update(
            status="installed",
            reason=f"{spec.binary} is installed but did not run",
            fix=f"Reinstall {spec.binary}: {spec.install_hint}",
            command=" ".join([spec.binary, *spec.version_argv]),
        )
        return result

    if not (include_project_check and spec.project_argv):
        result.update(status="runnable", command=" ".join([spec.binary, "--version"]))
        return result

    check = _run(spec.project_argv)
    passed = check.returncode == 0
    result["passed_on_current_project"] = passed
    command = " ".join([spec.binary, *spec.project_argv])
    result["command"] = command
    if passed:
        result["status"] = "passed"
    else:
        result.update(
            status="failing",
            reason=f"{spec.binary} reported findings on this project",
            fix=f"Run `{command}` locally and fix the reported issues",
            output_tail=redact((check.stdout or check.stderr or "").strip())[-1000:],
        )
    return result


def tool_health_summary(
    project_root: Path,
    *,
    run: Runner = _default_runner,
    which: Callable[[str], str | None] = shutil.which,
    include_project_check: bool = False,
) -> dict[str, Any]:
    """Aggregate per-tool health for `op doctor`; version-only by default."""
    checks = [
        check_tool_health(
            tool_id,
            project_root,
            run=run,
            which=which,
            include_project_check=include_project_check,
        )
        for tool_id in known_tool_ids()
    ]
    failing = [
        {"id": item["id"], "status": item["status"], "fix": item.get("fix", "")}
        for item in checks
        if item["status"] in {"missing", "installed", "failing"}
    ]
    return {
        "checks": checks,
        "installed": sum(1 for item in checks if item["installed"]),
        "total": len(checks),
        "failing": failing,
    }
