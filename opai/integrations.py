from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess  # nosec B404
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opai import __brand__, __release_stage__, __version__
from opai.terminal_ui import render_badge
from opaihub.state import attach_project, state_dir


STATUS_TEXT = "Using OPai"
START_MARKER = "<!-- OPai managed block: start -->"
END_MARKER = "<!-- OPai managed block: end -->"
PS_START_MARKER = "# OPai managed block: start"
PS_END_MARKER = "# OPai managed block: end"
SUPERPOWERS_REPO = "https://github.com/obra/superpowers.git"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def opai_home(home: Path | None = None) -> Path:
    return (home or Path.home()).expanduser().resolve() / ".opai"


def render_statusline(width: int | None = None, color: bool = True) -> str:
    return render_badge(
        width or shutil.get_terminal_size((80, 20)).columns, color=color
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _python_executable() -> str:
    return sys.executable or "python"


def instruction_text(project_root: Path | None = None) -> str:
    project_line = (
        f"\nCurrent OPai activation root: `{project_root}`.\n"
        if project_root
        else "\nUse the current working directory as the active project root.\n"
    )
    return f"""# OPai Active

Status text: `{STATUS_TEXT}`

OPai {__version__} {__release_stage__} is active.
{project_line}

Use OPai as the local-first routing layer for coding and automation:

1. Inspect local files, diffs, tests, logs, registries, and caches before asking a model to reason deeply.
2. Prefer `opai doctor`, `opai scan`, `opai tools`, and `opai hub <command>` for local evidence.
3. Do not use paid/cloud tools unless the user explicitly confirms.
4. Do not run destructive shell, Git, deploy, publish, or cloud commands without explicit confirmation.
5. Keep context small: diffs, summaries, and targeted files before broad file dumps.
6. If Superpowers skills are available, use them as part of OPai: start with `superpowers:using-superpowers`, use `superpowers:systematic-debugging` for bugs, and use `superpowers:test-driven-development` for code changes.

If the active project has `.opaihub/project-instructions.md` or an OPai managed block in `AGENTS.md`, `CLAUDE.md`, or `.github/copilot-instructions.md`, treat that project-local guidance as the session entrypoint.

When the client supports a status line or session badge, show `{STATUS_TEXT}`.
"""


def codex_skill_text(project_root: Path | None = None) -> str:
    body = instruction_text(project_root)
    return f"""---
name: opai
description: Use when starting a coding session, when OPai is installed, or when local-first AI tooling, coding agents, MCP routing, cost controls, or project workflows are relevant.
---

{body}
"""


def copilot_instruction_text(project_root: Path | None = None) -> str:
    return (
        instruction_text(project_root)
        + "\nFor GitHub Copilot project use, copy or include this in `.github/copilot-instructions.md`.\n"
    )


def project_instruction_text(project_root: Path) -> str:
    return f"""{START_MARKER}
# OPai Project Active

OPai {__version__} {__release_stage__} is active for this project: `{project_root}`.

Default workflow for AI coding in this project:

1. Treat this OPai block as the first session checklist, even when other project instructions exist below it.
2. Run or reason from `opai route "<task>"` before expensive model work.
3. Use local evidence first: git status/diff, project profile, tests, logs, linters, registry metadata, and cached context.
4. Use Superpowers as part of OPai when available: `superpowers:using-superpowers`, `superpowers:systematic-debugging`, `superpowers:test-driven-development`, and verification before completion.
5. Prefer OPai tools and workflows before cloud calls: `opai doctor`, `opai scan`, `opai tools`, `opai hub list-tools`, `opai hub analytics status`.
6. Do not push, merge, deploy, delete, run destructive commands, or use paid/cloud AI without explicit user confirmation.

Session badge/status text: {STATUS_TEXT}
{END_MARKER}"""


def _write(path: Path, text: str, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        try:
            path.chmod(path.stat().st_mode | 0o755)
        except OSError:
            pass
    return path


def _replace_block(
    existing: str, block: str, start_marker: str, end_marker: str
) -> str:
    start = existing.find(start_marker)
    end = existing.find(end_marker)
    if start != -1 and end != -1 and end > start:
        end += len(end_marker)
        updated = (
            existing[:start].rstrip()
            + "\n\n"
            + block
            + "\n\n"
            + existing[end:].lstrip()
        )
        return updated.strip() + "\n"
    return (existing.rstrip() + "\n\n" + block + "\n").lstrip()


def _replace_managed_block(existing: str, block: str) -> str:
    return _replace_block(existing, block, START_MARKER, END_MARKER)


def _replace_managed_block_at_top(existing: str, block: str) -> str:
    start = existing.find(START_MARKER)
    end = existing.find(END_MARKER)
    if start != -1 and end != -1 and end > start:
        end += len(END_MARKER)
        remainder = (existing[:start] + existing[end:]).strip()
    else:
        remainder = existing.strip()
    if remainder:
        return f"{block}\n\n{remainder}\n"
    return block + "\n"


def _replace_shell_block(existing: str, block: str) -> str:
    without_html = _replace_block(existing, "", START_MARKER, END_MARKER).strip()
    return _replace_block(without_html, block, PS_START_MARKER, PS_END_MARKER)


def _write_claude_memory(project_root: Path, home: Path) -> Path:
    claude = home / ".claude" / "CLAUDE.md"
    existing = claude.read_text(encoding="utf-8") if claude.exists() else ""
    block = f"""{START_MARKER}
OPai is installed. Read `{opai_home(home) / "instructions" / "OPAI.md"}` for local-first routing, cost controls, and safety policy.

Session badge/status text: {STATUS_TEXT}
{END_MARKER}"""
    return _write(claude, _replace_managed_block_at_top(existing, block))


def _write_project_instructions(project_root: Path) -> list[str]:
    block = project_instruction_text(project_root)
    written: list[str] = []
    for path in [
        project_root / "AGENTS.md",
        project_root / "CLAUDE.md",
        project_root / ".github" / "copilot-instructions.md",
    ]:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        written.append(
            str(_write(path, _replace_managed_block_at_top(existing, block)))
        )
    written.append(
        str(_write(project_root / ".opaihub" / "project-instructions.md", block))
    )
    return written


def _planned_project_files(project_root: Path) -> list[str]:
    return [
        str(project_root / "AGENTS.md"),
        str(project_root / "CLAUDE.md"),
        str(project_root / ".github" / "copilot-instructions.md"),
        str(project_root / ".opaihub" / "project-instructions.md"),
        str(project_root / ".opaihub" / "project.json"),
        str(project_root / ".opaihub" / "activation.json"),
    ]


def _install_superpowers_source(
    repo_root: Path, repo_url: str = SUPERPOWERS_REPO, timeout: int = 120
) -> dict[str, Any]:
    git = shutil.which("git")
    if not git:
        return {
            "status": "missing_git",
            "reason": "Git is required to install Superpowers automatically.",
        }
    repo_root.parent.mkdir(parents=True, exist_ok=True)
    if (repo_root / ".git").exists():
        command = [git, "-C", str(repo_root), "pull", "--ff-only"]
    elif repo_root.exists():
        return {
            "status": "blocked",
            "reason": f"Superpowers target already exists but is not a Git checkout: {repo_root}",
        }
    else:
        command = [git, "clone", "--depth", "1", repo_url, str(repo_root)]
    try:
        completed = subprocess.run(  # nosec
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except OSError as exc:
        return {"status": "failed", "reason": str(exc)}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "reason": "Superpowers install timed out."}
    return {
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "command": command,
        "output_tail": (completed.stdout + completed.stderr)[-1600:],
    }


def ensure_superpowers_bridge(
    home: Path | None = None,
    auto_install: bool = False,
    timeout: int = 120,
) -> dict[str, Any]:
    user_home = (home or Path.home()).expanduser().resolve()
    repo_root = user_home / ".codex" / "superpowers"
    source = repo_root / "skills"
    target = user_home / ".agents" / "skills" / "superpowers"
    install_result = None
    if auto_install and not source.exists():
        install_result = _install_superpowers_source(repo_root, timeout=timeout)
    result: dict[str, Any] = {
        "source": str(source),
        "target": str(target),
        "available": source.exists(),
        "enabled": False,
        "mode": "missing",
        "auto_install": auto_install,
        "install": install_result,
    }
    if target.exists():
        return {**result, "enabled": True, "mode": "existing"}
    if not source.exists():
        return {
            **result,
            "reason": "Superpowers source not found. Install or clone Superpowers, then run opai activate.",
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(source, target_is_directory=True)
        return {**result, "enabled": True, "mode": "symlink"}
    except OSError:
        shutil.copytree(source, target, dirs_exist_ok=True)
        return {**result, "enabled": True, "mode": "copy"}


def activate_project(
    project_root: Path,
    home: Path | None = None,
    install_global: bool = True,
    install_shell_aliases: bool = False,
    install_superpowers: bool = False,
    dry_run: bool = False,
    repair: bool = False,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    user_home = (home or Path.home()).expanduser().resolve()
    if dry_run:
        return {
            "status": "planned",
            "project_root": str(root),
            "project_files": _planned_project_files(root),
            "global_integrations": install_global,
            "shell_aliases": install_shell_aliases,
            "install_superpowers": install_superpowers,
            "repair": repair,
            "superpowers": {
                "source": str(user_home / ".codex" / "superpowers" / "skills"),
                "target": str(user_home / ".agents" / "skills" / "superpowers"),
                "available": (user_home / ".codex" / "superpowers" / "skills").exists(),
                "enabled": (user_home / ".agents" / "skills" / "superpowers").exists(),
            },
        }
    attached = attach_project(root)
    project_files = _write_project_instructions(root)
    superpowers = ensure_superpowers_bridge(user_home, auto_install=install_superpowers)
    global_result = (
        install_global_integrations(
            root,
            home=user_home,
            targets=["codex", "claude", "copilot", "shell"],
            install_shell_aliases=install_shell_aliases,
            ensure_superpowers=True,
            install_superpowers=False,
        )
        if install_global
        else None
    )
    activation = {
        "status": "active",
        "project_root": str(root),
        "state_path": attached["state_path"],
        "project_files": project_files,
        "superpowers": superpowers,
        "global_integrations": global_result,
        "next_steps": [
            "Restart Codex/Claude/Copilot sessions after first activation so skills are rediscovered.",
            "Launch AI CLIs through OPai wrappers so this activation runs in every project.",
            'Run opai route "<task>" to collect local evidence before model use.',
        ],
    }
    _write(
        state_dir(root) / "activation.json",
        json.dumps(activation, indent=2, sort_keys=True) + "\n",
    )
    return activation


def project_status(project_root: Path, home: Path | None = None) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    user_home = (home or Path.home()).expanduser().resolve()
    state = root / ".opaihub" / "project.json"
    activation = root / ".opaihub" / "activation.json"
    instruction_files = {
        "agents": root / "AGENTS.md",
        "claude": root / "CLAUDE.md",
        "copilot": root / ".github" / "copilot-instructions.md",
    }
    superpowers_target = user_home / ".agents" / "skills" / "superpowers"
    superpowers_source = user_home / ".codex" / "superpowers" / "skills"
    wrappers = {
        tool: opai_home(user_home) / "bin" / f"opai-{tool}.ps1"
        for tool in ["codex", "claude", "copilot"]
    }
    global_status = load_global_status(user_home)
    instruction_status = {}
    for name, path in instruction_files.items():
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        instruction_status[name] = {
            "path": str(path),
            "exists": path.exists(),
            "opai_block": START_MARKER in text and END_MARKER in text,
            "opai_block_at_top": text.lstrip().startswith(START_MARKER),
            "superpowers_reference": "Superpowers" in text,
        }
    return {
        "brand": __brand__,
        "version": __version__,
        "release_stage": __release_stage__,
        "project": {
            "root": str(root),
            "activated": state.exists() and activation.exists(),
            "state": str(state),
            "activation": str(activation),
            "instructions": instruction_status,
        },
        "global": {
            "installed": bool(global_status.get("installed")),
            "status_text": global_status.get("status_text", STATUS_TEXT),
            "manifest": str(opai_home(user_home) / "global.json"),
            "wrappers": {
                name: {"path": str(path), "exists": path.exists()}
                for name, path in wrappers.items()
            },
        },
        "superpowers": {
            "available": superpowers_source.exists(),
            "enabled": superpowers_target.exists(),
            "source": str(superpowers_source),
            "target": str(superpowers_target),
        },
        "next_steps": [
            "Run opai activate --repair if any activation field is false.",
            "Restart AI clients after global skill changes.",
            "Use op or opai; both launch OPai.",
        ],
    }


def _powershell_wrapper(tool: str) -> str:
    python = _ps_quote(_python_executable())
    fallback = ""
    if tool == "copilot":
        fallback = """
if (-not $Command) {
    $Gh = Get-Command gh -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Gh) {
        & $Gh.Source copilot @Args
        exit $LASTEXITCODE
    }
}
"""
    return f"""param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Args
)

$OpaiPython = {python}
$env:OPAI_ACTIVE = "1"
$env:OPAI_STATUS = "{STATUS_TEXT}"
& $OpaiPython -m opai activate --quiet --project .
if ($LASTEXITCODE -ne 0) {{
    Write-Warning "OPai activation failed; launching {tool} in degraded mode."
}}
& $OpaiPython -m opai welcome --compact --animate --frames 7 --delay 0.045
if ($LASTEXITCODE -ne 0) {{
    Write-Warning "OPai welcome failed; continuing with {tool}."
}}

$Command = Get-Command {tool} -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
{fallback}
if (-not $Command) {{
    Write-Error "{tool} command not found on PATH."
    exit 127
}}

& $Command.Source @Args
exit $LASTEXITCODE
"""


def _posix_wrapper(tool: str) -> str:
    python = shlex.quote(_python_executable())
    if tool == "copilot":
        return f"""#!/usr/bin/env sh
OPAI_PYTHON={python}
export OPAI_ACTIVE=1
export OPAI_STATUS="{STATUS_TEXT}"
"$OPAI_PYTHON" -m opai activate --quiet --project . || printf '%s\\n' "OPai activation failed; launching {tool} in degraded mode." >&2
"$OPAI_PYTHON" -m opai welcome --compact --animate --frames 7 --delay 0.045 || printf '%s\\n' "OPai welcome failed; continuing with {tool}." >&2
if command -v copilot >/dev/null 2>&1; then
  exec copilot "$@"
fi
exec gh copilot "$@"
"""
    return f"""#!/usr/bin/env sh
OPAI_PYTHON={python}
export OPAI_ACTIVE=1
export OPAI_STATUS="{STATUS_TEXT}"
"$OPAI_PYTHON" -m opai activate --quiet --project . || printf '%s\\n' "OPai activation failed; launching {tool} in degraded mode." >&2
"$OPAI_PYTHON" -m opai welcome --compact --animate --frames 7 --delay 0.045 || printf '%s\\n' "OPai welcome failed; continuing with {tool}." >&2
exec {tool} "$@"
"""


def _write_shell_wrappers(home: Path) -> list[str]:
    written = []
    for tool in ["codex", "claude", "copilot"]:
        written.append(
            str(
                _write(
                    opai_home(home) / "bin" / f"opai-{tool}.ps1",
                    _powershell_wrapper(tool),
                )
            )
        )
        written.append(
            str(
                _write(
                    opai_home(home) / "bin" / f"opai-{tool}",
                    _posix_wrapper(tool),
                    executable=True,
                )
            )
        )
    return written


def _write_shell_aliases(home: Path) -> list[Path]:
    powershell_profiles = [
        home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1",
        home / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1",
    ]
    written = []
    bin_dir = opai_home(home) / "bin"
    python_ps = _ps_quote(_python_executable())
    powershell_block = f"""{PS_START_MARKER}
function op {{ & {python_ps} -m opai @args }}
function opai {{ & {python_ps} -m opai @args }}
function codex {{ & "{bin_dir / "opai-codex.ps1"}" @args }}
function claude {{ & "{bin_dir / "opai-claude.ps1"}" @args }}
function copilot {{ & "{bin_dir / "opai-copilot.ps1"}" @args }}
{PS_END_MARKER}"""
    for profile in powershell_profiles:
        existing = profile.read_text(encoding="utf-8") if profile.exists() else ""
        written.append(
            _write(profile, _replace_shell_block(existing, powershell_block))
        )

    posix_profiles = [home / ".profile", home / ".bashrc", home / ".zshrc"]
    posix_bin = opai_home(home) / "bin"
    python_sh = shlex.quote(_python_executable())
    posix_block = f"""{PS_START_MARKER}
op() {{ {python_sh} -m opai "$@"; }}
opai() {{ {python_sh} -m opai "$@"; }}
codex() {{ "{posix_bin / "opai-codex"}" "$@"; }}
claude() {{ "{posix_bin / "opai-claude"}" "$@"; }}
copilot() {{ "{posix_bin / "opai-copilot"}" "$@"; }}
{PS_END_MARKER}"""
    for profile in posix_profiles:
        existing = profile.read_text(encoding="utf-8") if profile.exists() else ""
        written.append(_write(profile, _replace_shell_block(existing, posix_block)))
    return written


def install_global_integrations(
    project_root: Path,
    home: Path | None = None,
    targets: list[str] | None = None,
    install_shell_aliases: bool = False,
    ensure_superpowers: bool = True,
    install_superpowers: bool = False,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    user_home = (home or Path.home()).expanduser().resolve()
    selected = set(targets or ["codex", "claude", "copilot", "shell"])
    if "all" in selected:
        selected = {"codex", "claude", "copilot", "shell"}

    base = opai_home(user_home)
    written: list[str] = []
    written.append(str(_write(base / "status.txt", STATUS_TEXT + "\n")))
    written.append(str(_write(base / "instructions" / "OPAI.md", instruction_text())))

    if "codex" in selected:
        written.append(
            str(
                _write(
                    user_home / ".agents" / "skills" / "opai" / "SKILL.md",
                    codex_skill_text(),
                )
            )
        )
    if "claude" in selected:
        written.append(
            str(_write(base / "integrations" / "claude-code.md", instruction_text()))
        )
        written.append(str(_write_claude_memory(root, user_home)))
    if "copilot" in selected:
        written.append(
            str(
                _write(
                    base / "integrations" / "copilot-instructions.md",
                    copilot_instruction_text(),
                )
            )
        )
    if "shell" in selected:
        written.extend(_write_shell_wrappers(user_home))
        if install_shell_aliases:
            written.extend(str(path) for path in _write_shell_aliases(user_home))
    superpowers = (
        ensure_superpowers_bridge(user_home, auto_install=install_superpowers)
        if ensure_superpowers
        else None
    )

    manifest = {
        "brand": __brand__,
        "version": __version__,
        "release_stage": __release_stage__,
        "status_text": STATUS_TEXT,
        "project_root": str(root),
        "targets": sorted(selected),
        "shell_aliases_installed": install_shell_aliases,
        "installed_at": now_iso(),
        "written": written,
        "superpowers": superpowers,
        "notes": [
            "Codex can discover the OPai skill from ~/.agents/skills/opai.",
            "OPai ensures ~/.agents/skills/superpowers when ~/.codex/superpowers/skills is available.",
            "Claude Code receives a managed global memory block when target claude is selected.",
            "Copilot support is instruction-file based; client UI support varies.",
            "Closed desktop apps may not expose a status badge surface. Use OPai instructions where supported.",
        ],
    }
    manifest_path = base / "global.json"
    _write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {"status": "installed", "manifest": str(manifest_path), **manifest}


def load_global_status(home: Path | None = None) -> dict[str, Any]:
    path = opai_home(home) / "global.json"
    if not path.exists():
        return {
            "brand": __brand__,
            "version": __version__,
            "release_stage": __release_stage__,
            "status_text": STATUS_TEXT,
            "installed": False,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    data["installed"] = True
    data["superpowers_enabled"] = (
        opai_home(home).parent / ".agents" / "skills" / "superpowers"
    ).exists()
    return data


def shell_environment() -> dict[str, str]:
    return {"OPAI_ACTIVE": "1", "OPAI_STATUS": STATUS_TEXT, **os.environ}
