from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from opaihub.command_runner import run_policy_command

from . import __version__
from .context_manager import load_profile
from .utils import command_exists, resolve_project_path


_TOOLS: dict[str, dict[str, Any]] = {
    "codex": {
        "bin": "codex",
        "label": "OpenAI Codex CLI",
        "config_paths": [
            Path.home() / ".codex" / "config.json",
            Path.home() / ".codex" / "config.yaml",
        ],
        "version_flag": "--version",
    },
    "claude": {
        "bin": "claude",
        "label": "Claude Code",
        "config_paths": [
            Path.home() / ".claude" / "settings.json",
        ],
        "version_flag": "--version",
    },
    "copilot": {
        "bin": "gh",
        "label": "GitHub Copilot (gh copilot)",
        "config_paths": [
            Path.home() / ".config" / "gh" / "config.yml",
        ],
        "version_flag": "copilot --version",
        "launch_argv": ["gh", "copilot"],
    },
}


def _probe_version(bin_name: str, flag: str) -> str:
    path = shutil.which(bin_name)
    if not path:
        return ""
    try:
        result = run_policy_command([bin_name, *flag.split()], Path.cwd(), timeout=5)
        line = (result.stdout.strip() or result.stderr.strip()).splitlines()
        return line[0][:80] if line else ""
    except Exception:
        return ""


def integrate_status() -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for key, meta in _TOOLS.items():
        bin_name: str = meta["bin"]
        available = command_exists(bin_name)
        config_found = [str(p) for p in meta["config_paths"] if p.exists()]
        version = _probe_version(bin_name, meta["version_flag"]) if available else ""
        tools[key] = {
            "label": meta["label"],
            "available": available,
            "version": version,
            "config": config_found,
        }
    ready = [k for k, v in tools.items() if v["available"]]
    return {
        "opai_version": __version__,
        "tools": tools,
        "ready": ready,
        "status": "ok" if ready else "no AI coding tools found in PATH",
    }


def statusline(project: str | None = None) -> str:
    root = resolve_project_path(project or ".")
    profile = load_profile(root)
    name = profile.get("name", root.name) if profile else root.name
    ready: list[str] = []
    for key, meta in _TOOLS.items():
        if command_exists(meta["bin"]):
            ready.append(key)
    tools_str = "+".join(ready) if ready else "no AI tools"
    return f"Vesta {__version__} | {name} | {tools_str}"


def launch_tool(tool: str, extra_args: list[str]) -> int:
    try:
        plan = launch_plan(tool, extra_args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    from opai.cli import main as opai_main

    return opai_main(plan["opai_args"])


def launch_plan(tool: str, extra_args: list[str]) -> dict[str, Any]:
    if tool not in _TOOLS:
        known = ", ".join(_TOOLS)
        raise ValueError(f"Unknown tool '{tool}'. Known: {known}")
    return {
        "tool": tool,
        "tool_args": list(extra_args),
        "opai_args": ["launch", tool, "--", *extra_args],
    }
