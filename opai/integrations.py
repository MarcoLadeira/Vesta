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
from opai.context_slim import AI_IGNORE_FILES, write_ai_ignore_files
from opai.terminal_ui import render_badge
from opaihub.loader import hub_root
from opaihub.skills import skill_items
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


def render_statusline(
    width: int | None = None, color: bool = True, project_root: Path | None = None
) -> str:
    if project_root is None:
        return render_badge(
            width or shutil.get_terminal_size((80, 20)).columns, color=color
        )
    from opai.cockpit import build_cockpit, compact_statusline
    from opai.terminal_ui import colorize

    text = compact_statusline(build_cockpit(project_root))
    columns = width or shutil.get_terminal_size((80, 20)).columns
    padding = max(0, columns - len(text))
    return " " * padding + colorize(text, enabled=color)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _python_executable() -> str:
    return sys.executable or "python"


def instruction_text(project_root: Path | None = None) -> str:
    project_line = f"Root: `{project_root}`.\n" if project_root else "Root: cwd.\n"
    return f"""# OPai Active
{STATUS_TEXT}. OPai {__version__} {__release_stage__}. {project_line}Local first: `opai route "<task>"`; use full evidence only when needed. No paid/cloud/destructive ops without confirmation. No generated dirs in context: `.git`, `.opcoding*`, `.opaihub/cache|logs|generated|install-test-*`, `node_modules`, venvs, `build`, `dist`. Use Superpowers if available.
"""


def codex_skill_text(project_root: Path | None = None) -> str:
    body = instruction_text(project_root)
    return f"""---
name: opai
description: Use when starting a coding session, when OPai is installed, or when local-first AI tooling, coding agents, MCP routing, cost controls, or project workflows are relevant.
---

{body}
"""


def ensure_opai_skill_library(
    project_root: Path, home: Path | None = None
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    user_home = (home or Path.home()).expanduser().resolve()
    source_hub = hub_root(root)
    source_skills = source_hub / "skills"
    target_root = user_home / ".agents" / "skills" / "opai"
    registry_source = source_skills / "registry.yaml"
    written: list[str] = []
    missing: list[str] = []

    target_root.mkdir(parents=True, exist_ok=True)
    if registry_source.exists():
        written.append(
            str(
                _write(
                    target_root / "registry.yaml",
                    registry_source.read_text(encoding="utf-8"),
                )
            )
        )
    else:
        missing.append("registry.yaml")

    for skill in skill_items(root):
        skill_id = str(skill.get("id", "")).strip()
        relative_path = str(skill.get("path", "")).strip()
        if not skill_id or not relative_path:
            missing.append(skill_id or "<missing-id>")
            continue
        source_path = source_hub / relative_path
        if not source_path.exists():
            missing.append(skill_id)
            continue
        written.append(
            str(
                _write(
                    target_root / skill_id / "SKILL.md",
                    source_path.read_text(encoding="utf-8"),
                )
            )
        )

    return {
        "status": "installed" if not missing else "partial",
        "source": str(source_skills),
        "target": str(target_root),
        "count": len(skill_items(root)),
        "missing": missing,
        "written": written,
    }


def copilot_instruction_text(project_root: Path | None = None) -> str:
    return (
        instruction_text(project_root)
        + "\nFor GitHub Copilot project use, copy or include this in `.github/copilot-instructions.md`.\n"
    )


def project_instruction_text(project_root: Path) -> str:
    return f"""{START_MARKER}
# OPai Active
{STATUS_TEXT}. Root: current repository.
OPai is active; run `opai cockpit` if unsure.
Local first: `opai route "<task>"`; `opai slim` if context grows.
No paid/cloud/destructive ops without confirmation. No generated dirs in context: `.git`, `.opcoding*`, `.opaihub/cache|logs|generated|install-test-*`, `node_modules`, venvs, `build`, `dist`.
Use Superpowers when available.
{END_MARKER}"""


def cursor_rule_text(project_root: Path) -> str:
    """Cursor reads `.cursor/rules/*.mdc`. Folder form avoids clobbering user rules."""
    return f"""---
description: OPai local-first, cost-aware routing and safety policy
alwaysApply: true
---
{project_instruction_text(project_root)}
"""


def cline_rule_text(project_root: Path) -> str:
    """Cline reads `.clinerules` file or `.clinerules/` folder. Use the folder form."""
    return f"""{project_instruction_text(project_root)}

For Cline: prefer OPai local-first routing before model escalation.
"""


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
    # Cursor and Cline read their own rule locations. Folder forms are used so
    # OPai never overwrites a user's existing single-file rules (issue #35).
    written.append(
        str(
            _write(
                project_root / ".cursor" / "rules" / "opai.mdc",
                cursor_rule_text(project_root),
            )
        )
    )
    written.append(
        str(
            _write(
                project_root / ".clinerules" / "opai.md",
                cline_rule_text(project_root),
            )
        )
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
        str(project_root / ".cursor" / "rules" / "opai.mdc"),
        str(project_root / ".clinerules" / "opai.md"),
        str(project_root / ".opaihub" / "project-instructions.md"),
        str(project_root / ".opaihub" / "project.json"),
        str(project_root / ".opaihub" / "activation.json"),
        *[str(project_root / name) for name in AI_IGNORE_FILES],
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
            "home": str(user_home),
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
    ai_ignore_files = write_ai_ignore_files(root)
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
        "home": str(user_home),
        "state_path": attached["state_path"],
        "project_files": project_files,
        "ai_ignore_files": ai_ignore_files,
        "superpowers": superpowers,
        "global_integrations": global_result,
        "next_steps": [
            "Restart Codex/Claude/Copilot sessions after first activation so skills are rediscovered.",
            "Launch AI CLIs through OPai wrappers so this activation runs in every project.",
            'Run opai route "<task>" to collect local evidence before model use.',
            "Run opai slim --clean to remove generated caches from this project.",
        ],
    }
    _write(
        state_dir(root) / "activation.json",
        json.dumps(activation, indent=2, sort_keys=True) + "\n",
    )
    return activation


def _activation_home(root: Path, fallback: Path) -> Path:
    activation = root / ".opaihub" / "activation.json"
    if not activation.exists():
        return fallback
    try:
        data = json.loads(activation.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback

    home = data.get("home")
    if isinstance(home, str) and home.strip():
        return Path(home).expanduser().resolve()

    global_integrations = data.get("global_integrations")
    if isinstance(global_integrations, dict):
        manifest = global_integrations.get("manifest")
        if isinstance(manifest, str) and manifest.strip():
            manifest_path = Path(manifest).expanduser()
            if (
                manifest_path.name == "global.json"
                and manifest_path.parent.name == ".opai"
            ):
                return manifest_path.parent.parent.resolve()

    return fallback


def project_status(project_root: Path, home: Path | None = None) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    fallback_home = (home or Path.home()).expanduser().resolve()
    user_home = (
        fallback_home if home is not None else _activation_home(root, fallback_home)
    )
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
    # Deferred import avoids an import cycle (clients imports from this module).
    from opai.clients import client_integrations_status, detect_stale_paths

    client_status = client_integrations_status(root, user_home)
    stale = detect_stale_paths(root, user_home)
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
            "shell_aliases_installed": bool(
                global_status.get("shell_aliases_installed")
            ),
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
        "client_integrations": client_status,
        "stale_paths": stale,
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
if ($env:OPAI_WELCOME -eq "1") {{
    & $OpaiPython -m opai welcome --compact --frames 4 --delay 0.035 --animate
}} else {{
    & $OpaiPython -m opai statusline
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
if [ "$OPAI_WELCOME" = "1" ]; then
  "$OPAI_PYTHON" -m opai welcome --compact --frames 4 --delay 0.035 --animate
else
  "$OPAI_PYTHON" -m opai statusline
fi
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
if [ "$OPAI_WELCOME" = "1" ]; then
  "$OPAI_PYTHON" -m opai welcome --compact --frames 4 --delay 0.035 --animate
else
  "$OPAI_PYTHON" -m opai statusline
fi
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
    previous_global = load_global_status(user_home)
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
        opai_skills = ensure_opai_skill_library(root, user_home)
        written.extend(opai_skills["written"])
    else:
        opai_skills = None
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
        "shell_aliases_installed": bool(
            install_shell_aliases or previous_global.get("shell_aliases_installed")
        ),
        "installed_at": now_iso(),
        "written": written,
        "opai_skills": opai_skills,
        "superpowers": superpowers,
        "notes": [
            "Codex can discover the OPai skill from ~/.agents/skills/opai.",
            "The OPai skill library is copied into ~/.agents/skills/opai for cross-repo discovery.",
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


def _strip_block(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start != -1 and end != -1 and end > start:
        end += len(end_marker)
        return (text[:start].rstrip() + "\n" + text[end:].lstrip()).strip() + "\n"
    return text


def update_opai_source(home: Path | None = None, timeout: int = 120) -> dict[str, Any]:
    """Update the installed OPai checkout under ~/.opai/source (issue #28)."""
    user_home = (home or Path.home()).expanduser().resolve()
    source = opai_home(user_home) / "source"
    git = shutil.which("git")
    if not source.exists():
        return {
            "status": "missing_source",
            "source": str(source),
            "reason": "No OPai source checkout found. Re-run the installer to set one up.",
        }
    if not (source / ".git").exists():
        return {
            "status": "not_a_git_checkout",
            "source": str(source),
            "reason": "OPai source exists but is not a Git checkout; update manually.",
        }
    if not git:
        return {"status": "missing_git", "reason": "Git is required to update OPai."}
    command = [git, "-C", str(source), "pull", "--ff-only"]
    try:
        completed = subprocess.run(  # nosec
            command, check=False, capture_output=True, text=True, timeout=timeout
        )
    except OSError as exc:
        return {"status": "failed", "reason": str(exc)}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "reason": "OPai update timed out."}
    return {
        "status": "ok" if completed.returncode == 0 else "failed",
        "source": str(source),
        "returncode": completed.returncode,
        "output_tail": (completed.stdout + completed.stderr)[-1200:],
    }


def uninstall_opai(
    project_root: Path,
    home: Path | None = None,
    dry_run: bool = True,
    remove_project_files: bool = True,
) -> dict[str, Any]:
    """Remove OPai-managed blocks, wrappers, and discovery files (issue #28).

    Defaults to a dry run. User content outside OPai-managed blocks is never
    touched, and the operation is idempotent.
    """
    root = project_root.expanduser().resolve()
    user_home = (home or Path.home()).expanduser().resolve()
    base = opai_home(user_home)

    # Whole files/dirs OPai fully owns and can remove.
    owned_paths = [
        base / "bin",
        base / "integrations",
        base / "instructions",
        base / "status.txt",
        base / "global.json",
        user_home / ".agents" / "skills" / "opai",
    ]
    if remove_project_files:
        owned_paths.extend(
            [
                root / ".cursor" / "rules" / "opai.mdc",
                root / ".clinerules" / "opai.md",
                root / ".opaihub" / "project-instructions.md",
                root / ".opaihub" / "activation.json",
            ]
        )

    # Files where OPai owns only a managed block and must preserve user content.
    block_files = [
        (user_home / ".claude" / "CLAUDE.md", START_MARKER, END_MARKER),
        (
            user_home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1",
            PS_START_MARKER,
            PS_END_MARKER,
        ),
        (
            user_home
            / "Documents"
            / "WindowsPowerShell"
            / "Microsoft.PowerShell_profile.ps1",
            PS_START_MARKER,
            PS_END_MARKER,
        ),
        (user_home / ".profile", PS_START_MARKER, PS_END_MARKER),
        (user_home / ".bashrc", PS_START_MARKER, PS_END_MARKER),
        (user_home / ".zshrc", PS_START_MARKER, PS_END_MARKER),
    ]
    if remove_project_files:
        block_files.extend(
            [
                (root / "AGENTS.md", START_MARKER, END_MARKER),
                (root / "CLAUDE.md", START_MARKER, END_MARKER),
                (
                    root / ".github" / "copilot-instructions.md",
                    START_MARKER,
                    END_MARKER,
                ),
            ]
        )

    planned_path_removals = [str(path) for path in owned_paths if path.exists()]
    planned_block_strips = [
        str(path)
        for path, start, end in block_files
        if path.exists() and start in path.read_text(encoding="utf-8", errors="replace")
    ]

    removed: list[str] = []
    stripped: list[str] = []
    if not dry_run:
        for path in owned_paths:
            if not path.exists():
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(str(path))
            except OSError:
                pass
        for path, start, end in block_files:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if start not in text:
                continue
            cleaned = _strip_block(text, start, end)
            if cleaned.strip():
                path.write_text(cleaned, encoding="utf-8")
            else:
                try:
                    path.unlink()
                except OSError:
                    pass
            stripped.append(str(path))

    return {
        "status": "planned" if dry_run else "removed",
        "dry_run": dry_run,
        "project_root": str(root),
        "home": str(user_home),
        "planned_path_removals": planned_path_removals,
        "planned_block_strips": planned_block_strips,
        "removed_paths": removed,
        "stripped_blocks": stripped,
        "notes": [
            "User content outside OPai-managed blocks is preserved.",
            "Re-run with --confirm to apply. Safe to run repeatedly (idempotent).",
        ],
    }
