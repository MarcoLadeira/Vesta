from __future__ import annotations

import json
import locale
import os
import shlex
import shutil
import subprocess  # nosec B404
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vesta import __brand__, legacy
from vesta.context_slim import (
    AI_IGNORE_FILES,
    AI_IGNORE_PATTERNS,
    write_ai_ignore_files,
)
from vesta.release_identity import release_version_text, surface_identity_payload
from vesta.terminal_ui import render_badge
from vestahub import shadow_journal
from vestahub.atomic_io import atomic_write_text, interprocess_transaction
from vestahub.loader import hub_root
from vestahub.proc import no_window_kwargs
from vestahub.skills import skill_items
from vestahub.state import attach_project, state_dir
from vestahub.boundary_errors import safe_detail


STATUS_TEXT = "Using Vesta"
START_MARKER = "<!-- Vesta managed block: start -->"
END_MARKER = "<!-- Vesta managed block: end -->"
PS_START_MARKER = "# Vesta managed block: start"
PS_END_MARKER = "# Vesta managed block: end"
# The markers blocks were written with before the rebrand to Vesta. Every read
# upgrades them to the markers above first, so an existing block is still found,
# replaced in place and uninstalled -- never left behind with a second block
# appended after it.
LEGACY_MARKERS = {
    START_MARKER: legacy.LEGACY_START_MARKER,
    END_MARKER: legacy.LEGACY_END_MARKER,
    PS_START_MARKER: legacy.LEGACY_PS_START_MARKER,
    PS_END_MARKER: legacy.LEGACY_PS_END_MARKER,
}
SUPERPOWERS_REPO = "https://github.com/obra/superpowers.git"


def upgrade_legacy_markers(text: str) -> str:
    """Rewrite managed-block markers from before the rebrand to the current ones."""
    for current, previous in LEGACY_MARKERS.items():
        text = text.replace(previous, current)
    return text


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def vesta_home(home: Path | None = None) -> Path:
    return (home or Path.home()).expanduser().resolve() / ".vesta"


def render_statusline(
    width: int | None = None, color: bool = True, project_root: Path | None = None
) -> str:
    if project_root is None:
        return render_badge(
            width or shutil.get_terminal_size((80, 20)).columns, color=color
        )
    from vesta.cockpit import build_cockpit, compact_statusline
    from vesta.terminal_ui import colorize

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
    return f"""# Vesta Active
{STATUS_TEXT}. {release_version_text()}. {project_line}Vesta manages routing, cost controls, and safety policy for this session. Never run `vesta` CLI commands inside an AI task — recursive self-invocation is blocked by Vesta (F12). The latest explicit request controls: fix/build/test/refactor/PR authorizes repo edits, a branch, tests, commit, push, and opening a pull request; do not ask again for those requested steps. Explain/review stays read-only. Ask before paid/cloud, destructive or irreversible actions, secret exposure, production credentials, or force-push. Protect unrelated changes. No generated dirs in context: `.git`, `.opcoding*`, `.vestahub/cache|logs|generated|install-test-*`, `node_modules`, venvs, `build`, `dist`. Use Superpowers if available.
"""


def codex_skill_text(project_root: Path | None = None) -> str:
    body = instruction_text(project_root)
    return f"""---
name: vesta
description: Use when starting a coding session, when Vesta is installed, or when local-first AI tooling, coding agents, MCP routing, cost controls, or project workflows are relevant.
---

{body}
"""


def ensure_vesta_skill_library(
    project_root: Path, home: Path | None = None
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    user_home = (home or Path.home()).expanduser().resolve()
    source_hub = hub_root(root)
    source_skills = source_hub / "skills"
    target_root = user_home / ".agents" / "skills" / "vesta"
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
# Vesta Active
{STATUS_TEXT}. Root: current repository.
Vesta is active and manages this session's routing and safety. Never run `vesta` CLI commands inside an AI task — recursive self-invocation is blocked by Vesta.
The latest explicit request controls. A fix/build/test/refactor/PR request allows Vesta to edit files, create a branch, test, commit, push, and open a pull request; do not ask again for those requested steps. Explain/review is read-only. Ask before paid/cloud, destructive or irreversible actions, secret exposure, production credentials, or force-push. Protect unrelated changes. No generated dirs in context: `.git`, `.opcoding*`, `.vestahub/cache|logs|generated|install-test-*`, `node_modules`, venvs, `build`, `dist`.
Use Superpowers when available.
{END_MARKER}"""


def cursor_rule_text(project_root: Path) -> str:
    """Cursor reads `.cursor/rules/*.mdc`. Folder form avoids clobbering user rules."""
    return f"""---
description: Vesta local-first, cost-aware routing and safety policy
alwaysApply: true
---
{project_instruction_text(project_root)}
"""


def cline_rule_text(project_root: Path) -> str:
    """Cline reads `.clinerules` file or `.clinerules/` folder. Use the folder form."""
    return f"""{project_instruction_text(project_root)}

For Cline: prefer Vesta local-first routing before model escalation.
"""


def _write(path: Path, text: str, executable: bool = False) -> Path:
    atomic_write_text(path, text)
    if executable:
        try:
            path.chmod(path.stat().st_mode | 0o755)
        except OSError:
            pass
    return path


def _replace_block(
    existing: str, block: str, start_marker: str, end_marker: str
) -> str:
    existing = upgrade_legacy_markers(existing)
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
    existing = upgrade_legacy_markers(existing)
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
Vesta is installed. Read `{vesta_home(home) / "instructions" / "VESTA.md"}` for local-first routing, cost controls, and safety policy.

Session badge/status text: {STATUS_TEXT}
{END_MARKER}"""
    return _write(claude, _replace_managed_block_at_top(existing, block))


def _write_project_instructions(project_root: Path) -> list[str]:
    block = project_instruction_text(project_root)
    written: list[str] = []
    for path in [
        project_root / "AGENTS.md",
        project_root / "CLAUDE.md",
        project_root / "GEMINI.md",
        project_root / ".github" / "copilot-instructions.md",
    ]:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        written.append(
            str(_write(path, _replace_managed_block_at_top(existing, block)))
        )
    # Cursor and Cline read their own rule locations. Folder forms are used so
    # Vesta never overwrites a user's existing single-file rules (issue #35).
    written.append(
        str(
            _write(
                project_root / ".cursor" / "rules" / "vesta.mdc",
                cursor_rule_text(project_root),
            )
        )
    )
    written.append(
        str(
            _write(
                project_root / ".clinerules" / "vesta.md",
                cline_rule_text(project_root),
            )
        )
    )
    written.append(
        str(_write(project_root / ".vestahub" / "project-instructions.md", block))
    )
    return written


def upgrade_legacy_project_blocks(project_root: Path) -> list[str]:
    """Rewrite the managed blocks an install from before the rename left here.

    Only files that already hold an old-marker block change; the block is
    replaced in place of the old one and the user's text is kept. Never raises:
    it runs at startup for every known project.
    """

    written: list[str] = []
    try:
        root = Path(project_root).expanduser().resolve()
        block = project_instruction_text(root)
        for name in legacy.PROJECT_INSTRUCTION_FILES:
            path = root / name
            if path.is_symlink() or not legacy.has_legacy_instruction_block(path):
                continue
            try:
                existing = path.read_text(encoding="utf-8")
                written.append(
                    str(_write(path, _replace_managed_block_at_top(existing, block)))
                )
            except (OSError, UnicodeError):
                continue
        written.extend(remove_legacy_project_files(root))
    except Exception:  # noqa: BLE001 - startup must never fail here
        pass
    return written


def remove_legacy_project_files(project_root: Path) -> list[str]:
    """Delete the pre-rename rule and ignore files Vesta generated here.

    Only files holding nothing but Vesta's own generated content go; a rule or
    ignore file a user added lines to is left exactly as it is.
    """
    from vestahub.context_engine import managed_ignore_lines

    try:
        return legacy.remove_legacy_project_artifacts(
            project_root,
            ignore_lines=[*AI_IGNORE_PATTERNS, *managed_ignore_lines()],
        )
    except Exception:  # noqa: BLE001 - cleanup must never block activation
        return []


def _legacy_project_files(project_root: Path) -> list[Path]:
    """What an uninstall removes of a project set up before the rename."""
    from vestahub.context_engine import managed_ignore_lines

    state = legacy.legacy_project_state_dir(project_root)
    owned = [state / "project-instructions.md", state / "activation.json"]
    try:
        owned.extend(
            legacy.legacy_project_artifacts(
                project_root,
                ignore_lines=[*AI_IGNORE_PATTERNS, *managed_ignore_lines()],
            )
        )
    except Exception:  # noqa: BLE001 - never block an uninstall
        pass
    return owned


def _planned_project_files(project_root: Path) -> list[str]:
    return [
        str(project_root / "AGENTS.md"),
        str(project_root / "CLAUDE.md"),
        str(project_root / "GEMINI.md"),
        str(project_root / ".github" / "copilot-instructions.md"),
        str(project_root / ".cursor" / "rules" / "vesta.mdc"),
        str(project_root / ".clinerules" / "vesta.md"),
        str(project_root / ".vestahub" / "project-instructions.md"),
        str(project_root / ".vestahub" / "project.json"),
        str(project_root / ".vestahub" / "activation.json"),
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
            **no_window_kwargs(),  # no flashing console window on Windows
        )
    except OSError as exc:
        return {"status": "failed", "reason": safe_detail(exc)}
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
            "reason": "Superpowers source not found. Install or clone Superpowers, then run vesta activate.",
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
    legacy_removed = remove_legacy_project_files(root)
    superpowers = ensure_superpowers_bridge(user_home, auto_install=install_superpowers)
    global_result = (
        install_global_integrations(
            root,
            home=user_home,
            targets=["codex", "claude", "copilot", "gemini", "shell"],
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
        "legacy_files_removed": legacy_removed,
        "superpowers": superpowers,
        "global_integrations": global_result,
        "next_steps": [
            "Restart Codex/Claude/Copilot/Gemini sessions after first activation so instructions are rediscovered.",
            "Launch AI CLIs through Vesta wrappers so this activation runs in every project.",
            'Run vesta route "<task>" to collect local evidence before model use.',
            "Run vesta slim --clean to remove generated caches from this project.",
        ],
    }
    _write(
        state_dir(root) / "activation.json",
        json.dumps(activation, indent=2, sort_keys=True) + "\n",
    )
    return activation


def _activation_home(root: Path, fallback: Path) -> Path:
    activation = root / ".vestahub" / "activation.json"
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
                and manifest_path.parent.name == ".vesta"
            ):
                return manifest_path.parent.parent.resolve()

    return fallback


def project_status(project_root: Path, home: Path | None = None) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    fallback_home = (home or Path.home()).expanduser().resolve()
    user_home = (
        fallback_home if home is not None else _activation_home(root, fallback_home)
    )
    state = root / ".vestahub" / "project.json"
    activation = root / ".vestahub" / "activation.json"
    instruction_files = {
        "agents": root / "AGENTS.md",
        "claude": root / "CLAUDE.md",
        "gemini": root / "GEMINI.md",
        "copilot": root / ".github" / "copilot-instructions.md",
    }
    superpowers_target = user_home / ".agents" / "skills" / "superpowers"
    superpowers_source = user_home / ".codex" / "superpowers" / "skills"
    wrappers = {
        tool: vesta_home(user_home) / "bin" / f"vesta-{tool}.ps1"
        for tool in ["codex", "claude", "copilot", "gemini"]
    }
    global_status = load_global_status(user_home)
    instruction_status = {}
    for name, path in instruction_files.items():
        text = (
            upgrade_legacy_markers(path.read_text(encoding="utf-8"))
            if path.exists()
            else ""
        )
        instruction_status[name] = {
            "path": str(path),
            "exists": path.exists(),
            "vesta_block": START_MARKER in text and END_MARKER in text,
            "vesta_block_at_top": text.lstrip().startswith(START_MARKER),
            "superpowers_reference": "Superpowers" in text,
        }
    # Deferred import avoids an import cycle (clients imports from this module).
    from vesta.clients import client_integrations_status, detect_stale_paths

    client_status = client_integrations_status(root, user_home)
    stale = detect_stale_paths(root, user_home)

    def wrapper_info(path: Path) -> dict[str, Any]:
        capture_mode = "missing"
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                capture_mode = (
                    "selective_proxy"
                    if "agent-launch --project" in content
                    and "PassthroughExit = 125" in content
                    else "legacy_passthrough"
                )
            except OSError:
                capture_mode = "unreadable"
        return {
            "path": str(path),
            "exists": path.exists(),
            "capture_mode": capture_mode,
        }

    return {
        **surface_identity_payload(brand=__brand__),
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
            "manifest": str(vesta_home(user_home) / "global.json"),
            "shell_aliases_installed": bool(
                global_status.get("shell_aliases_installed")
            ),
            "wrappers": {name: wrapper_info(path) for name, path in wrappers.items()},
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
            "Run vesta activate --repair if any activation field is false.",
            "Restart AI clients after global skill changes.",
            "Use op or vesta; both launch Vesta.",
        ],
    }


def _powershell_wrapper(tool: str) -> str:
    python = _ps_quote(_python_executable())
    fallback = "$RawPrefix = @()"
    if tool == "copilot":
        fallback = """$RawPrefix = @()
if (-not $Command) {
    $Gh = Get-Command gh -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Gh) {
        $Command = $Gh
        $RawPrefix = @("copilot")
    }
}
"""
    return f"""param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $AgentArgs
)

$VestaPython = {python}
$PassthroughExit = 125
$env:VESTA_ACTIVE = "1"
$env:VESTA_STATUS = "{STATUS_TEXT}"
& $VestaPython -m vesta activate --quiet --project .
$ActivationOk = $LASTEXITCODE -eq 0
if (-not $ActivationOk) {{
    Write-Warning "Vesta activation failed; launching {tool} in degraded mode."
}}
if ($env:VESTA_WELCOME -eq "1") {{
    & $VestaPython -m vesta welcome --compact --frames 4 --delay 0.035 --animate |
        ForEach-Object {{ [Console]::Error.WriteLine([string]$_) }}
}} else {{
    & $VestaPython -m vesta statusline |
        ForEach-Object {{ [Console]::Error.WriteLine([string]$_) }}
}}

$Command = Get-Command {tool} -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
{fallback}
if (-not $Command) {{
    Write-Error "{tool} command not found on PATH."
    exit 127
}}

$RawArgs = @($RawPrefix) + @($AgentArgs)
if ($ActivationOk) {{
    & $VestaPython -m vesta agent-launch --project . {tool} -- @AgentArgs
    $VestaExit = $LASTEXITCODE
    if ($VestaExit -ne $PassthroughExit) {{
        exit $VestaExit
    }}
}}

& $Command.Source @RawArgs
exit $LASTEXITCODE
"""


def _posix_wrapper(tool: str) -> str:
    python = shlex.quote(_python_executable())
    if tool == "copilot":
        return f"""#!/usr/bin/env sh
VESTA_PYTHON={python}
PASSTHROUGH_EXIT=125
export VESTA_ACTIVE=1
export VESTA_STATUS="{STATUS_TEXT}"
ACTIVATION_OK=1
"$VESTA_PYTHON" -m vesta activate --quiet --project . || ACTIVATION_OK=0
if [ "$ACTIVATION_OK" -eq 0 ]; then
  printf '%s\\n' "Vesta activation failed; launching {tool} in degraded mode." >&2
fi
if [ "$VESTA_WELCOME" = "1" ]; then
  "$VESTA_PYTHON" -m vesta welcome --compact --frames 4 --delay 0.035 --animate >&2
else
  "$VESTA_PYTHON" -m vesta statusline >&2
fi
if command -v copilot >/dev/null 2>&1; then
  COMMAND=$(command -v copilot)
  RAW_PREFIX=
elif command -v gh >/dev/null 2>&1; then
  COMMAND=$(command -v gh)
  RAW_PREFIX=copilot
else
  printf '%s\\n' "copilot command not found on PATH." >&2
  exit 127
fi
if [ "$ACTIVATION_OK" -eq 1 ]; then
  "$VESTA_PYTHON" -m vesta agent-launch --project . copilot -- "$@"
  VESTA_EXIT=$?
  if [ "$VESTA_EXIT" -ne "$PASSTHROUGH_EXIT" ]; then
    exit "$VESTA_EXIT"
  fi
fi
if [ -n "$RAW_PREFIX" ]; then
  exec "$COMMAND" "$RAW_PREFIX" "$@"
fi
exec "$COMMAND" "$@"
"""
    return f"""#!/usr/bin/env sh
VESTA_PYTHON={python}
PASSTHROUGH_EXIT=125
export VESTA_ACTIVE=1
export VESTA_STATUS="{STATUS_TEXT}"
ACTIVATION_OK=1
"$VESTA_PYTHON" -m vesta activate --quiet --project . || ACTIVATION_OK=0
if [ "$ACTIVATION_OK" -eq 0 ]; then
  printf '%s\\n' "Vesta activation failed; launching {tool} in degraded mode." >&2
fi
if [ "$VESTA_WELCOME" = "1" ]; then
  "$VESTA_PYTHON" -m vesta welcome --compact --frames 4 --delay 0.035 --animate >&2
else
  "$VESTA_PYTHON" -m vesta statusline >&2
fi
COMMAND=$(command -v {tool})
if [ -z "$COMMAND" ]; then
  printf '%s\\n' "{tool} command not found on PATH." >&2
  exit 127
fi
if [ "$ACTIVATION_OK" -eq 1 ]; then
  "$VESTA_PYTHON" -m vesta agent-launch --project . {tool} -- "$@"
  VESTA_EXIT=$?
  if [ "$VESTA_EXIT" -ne "$PASSTHROUGH_EXIT" ]; then
    exit "$VESTA_EXIT"
  fi
fi
exec "$COMMAND" "$@"
"""


def _write_shell_wrappers(home: Path) -> list[str]:
    written = []
    for tool in ["codex", "claude", "copilot", "gemini"]:
        written.append(
            str(
                _write(
                    vesta_home(home) / "bin" / f"vesta-{tool}.ps1",
                    _powershell_wrapper(tool),
                )
            )
        )
        written.append(
            str(
                _write(
                    vesta_home(home) / "bin" / f"vesta-{tool}",
                    _posix_wrapper(tool),
                    executable=True,
                )
            )
        )
    return written


def _powershell_profiles(home: Path) -> list[Path]:
    return [
        home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1",
        home / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1",
    ]


def _posix_profiles(home: Path) -> list[Path]:
    return [home / ".profile", home / ".bashrc", home / ".zshrc"]


def _powershell_alias_block(home: Path) -> str:
    bin_dir = vesta_home(home) / "bin"
    python_ps = _ps_quote(_python_executable())
    powershell_block = f"""{PS_START_MARKER}
function op {{ & {python_ps} -m vesta @args }}
function vesta {{ & {python_ps} -m vesta @args }}
function codex {{ & "{bin_dir / "vesta-codex.ps1"}" @args }}
function claude {{ & "{bin_dir / "vesta-claude.ps1"}" @args }}
function copilot {{ & "{bin_dir / "vesta-copilot.ps1"}" @args }}
function gemini {{ & "{bin_dir / "vesta-gemini.ps1"}" @args }}
{PS_END_MARKER}"""
    return powershell_block


def _posix_alias_block(home: Path) -> str:
    posix_bin = vesta_home(home) / "bin"
    python_sh = shlex.quote(_python_executable())
    posix_block = f"""{PS_START_MARKER}
op() {{ {python_sh} -m vesta "$@"; }}
vesta() {{ {python_sh} -m vesta "$@"; }}
codex() {{ "{posix_bin / "vesta-codex"}" "$@"; }}
claude() {{ "{posix_bin / "vesta-claude"}" "$@"; }}
copilot() {{ "{posix_bin / "vesta-copilot"}" "$@"; }}
gemini() {{ "{posix_bin / "vesta-gemini"}" "$@"; }}
{PS_END_MARKER}"""
    return posix_block


def _read_profile(profile: Path) -> tuple[str, str] | None:
    """A shell profile's text and the encoding to write it back in.

    Profiles are the user's own files and are often saved in the system code
    page (Windows PowerShell 5.1 reads BOM-less files that way), so one that
    is not UTF-8 is rewritten in the encoding it was read with, and one that
    cannot be decoded at all is left alone rather than stopping the others.
    """

    try:
        raw = profile.read_bytes() if profile.exists() else b""
    except OSError:
        return None
    for encoding in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _write_profile(profile: Path, text: str, encoding: str) -> Path | None:
    try:
        atomic_write_text(profile, text, encoding=encoding)
    except (OSError, UnicodeEncodeError):
        return None
    return profile


def _write_shell_aliases(home: Path) -> list[Path]:
    written = []
    for profiles, block in (
        (_powershell_profiles(home), _powershell_alias_block(home)),
        (_posix_profiles(home), _posix_alias_block(home)),
    ):
        for profile in profiles:
            read = _read_profile(profile)
            if read is None:
                continue
            existing, encoding = read
            path = _write_profile(
                profile, _replace_shell_block(existing, block), encoding
            )
            if path is not None:
                written.append(path)
    return written


def _upgrade_legacy_shell_aliases(home: Path) -> list[Path]:
    """Re-render only the profiles still holding a pre-rename alias block.

    Those blocks call the old-name wrappers through the old package module,
    neither of which exists any more, so the user's ``claude``/``codex``
    commands fail until rewritten. A profile without such a block is not
    created or touched.
    """
    written: list[Path] = []
    for profiles, block in (
        (_powershell_profiles(home), _powershell_alias_block(home)),
        (_posix_profiles(home), _posix_alias_block(home)),
    ):
        for profile in profiles:
            read = _read_profile(profile) if profile.exists() else None
            if read is None:
                continue
            existing, encoding = read
            current = upgrade_legacy_markers(existing)
            start = current.find(PS_START_MARKER)
            end = current.find(PS_END_MARKER)
            if start == -1 or end <= start:
                continue
            if legacy.LEGACY_WRAPPER_PREFIX not in current[start:end] and (
                legacy.LEGACY_PS_START_MARKER not in existing
            ):
                continue
            path = _write_profile(
                profile, _replace_shell_block(existing, block), encoding
            )
            if path is not None:
                written.append(path)
    return written


def _claude_memory_references_legacy_home(home: Path) -> bool:
    claude = home / ".claude" / "CLAUDE.md"
    try:
        text = upgrade_legacy_markers(claude.read_text(encoding="utf-8"))
    except OSError:
        return False
    start = text.find(START_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end <= start:
        return False
    return legacy.LEGACY_INSTRUCTIONS_FILENAME in text[start:end]


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
    selected = set(targets or ["codex", "claude", "copilot", "gemini", "shell"])
    if "all" in selected:
        selected = {"codex", "claude", "copilot", "gemini", "shell"}

    base = vesta_home(user_home)
    previous_global = load_global_status(user_home)
    written: list[str] = []
    written.append(str(_write(base / "status.txt", STATUS_TEXT + "\n")))
    written.append(str(_write(base / "instructions" / "VESTA.md", instruction_text())))

    if "codex" in selected:
        written.append(
            str(
                _write(
                    user_home / ".agents" / "skills" / "vesta" / "SKILL.md",
                    codex_skill_text(),
                )
            )
        )
        vesta_skills = ensure_vesta_skill_library(root, user_home)
        written.extend(vesta_skills["written"])
    else:
        vesta_skills = None
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
    if "gemini" in selected:
        written.append(
            str(
                _write(
                    base / "integrations" / "gemini-instructions.md",
                    instruction_text(),
                )
            )
        )
    if "shell" in selected:
        written.extend(_write_shell_wrappers(user_home))
        if install_shell_aliases:
            written.extend(str(path) for path in _write_shell_aliases(user_home))
        else:
            written.extend(
                str(path) for path in _upgrade_legacy_shell_aliases(user_home)
            )
    if "claude" not in selected and _claude_memory_references_legacy_home(user_home):
        # An old global memory block points at instructions/OPAI.md, which is
        # about to be removed: rewrite the block the user already consented to.
        written.append(str(_write_claude_memory(root, user_home)))
    legacy_removed = legacy.remove_legacy_global_artifacts(
        user_home,
        claude_memory_upgraded=not _claude_memory_references_legacy_home(user_home),
    )
    superpowers = (
        ensure_superpowers_bridge(user_home, auto_install=install_superpowers)
        if ensure_superpowers
        else None
    )

    manifest = {
        **surface_identity_payload(brand=__brand__),
        "status_text": STATUS_TEXT,
        "project_root": str(root),
        "targets": sorted(selected),
        "shell_aliases_installed": bool(
            install_shell_aliases or previous_global.get("shell_aliases_installed")
        ),
        "installed_at": now_iso(),
        "written": written,
        "vesta_skills": vesta_skills,
        "legacy_files_removed": legacy_removed,
        "superpowers": superpowers,
        "notes": [
            "Codex can discover the Vesta skill from ~/.agents/skills/vesta.",
            "The Vesta skill library is copied into ~/.agents/skills/vesta for cross-repo discovery.",
            "Vesta ensures ~/.agents/skills/superpowers when ~/.codex/superpowers/skills is available.",
            "Claude Code receives a managed global memory block when target claude is selected.",
            "Copilot support is instruction-file based; client UI support varies.",
            "Gemini CLI receives GEMINI.md plus Vesta shell-wrapper activation.",
            "Closed desktop apps may not expose a status badge surface. Use Vesta instructions where supported.",
        ],
    }
    manifest_path = base / "global.json"
    with interprocess_transaction(manifest_path):
        latest_global = load_global_status(user_home)
        manifest["targets"] = sorted(
            set(manifest["targets"]) | set(latest_global.get("targets") or [])
        )
        manifest["shell_aliases_installed"] = bool(
            manifest["shell_aliases_installed"]
            or latest_global.get("shell_aliases_installed")
        )
        _write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        # #613 Stage 2: mirror the consent manifest, inside the lock that
        # already serialises the read-merge-write above.
        #
        # Deliberately here rather than inside `_write`: that helper is shared
        # with instruction-file emission (CLAUDE.md, GEMINI.md and friends),
        # which are generated content, not runtime truth. Mirroring every
        # `_write` would journal documents Stage 1 classifies as
        # NOT_RUNTIME_STATE and bury the one record that matters.
        shadow_journal.record_snapshot(
            manifest_path, manifest, is_valid_record=_valid_manifest_record
        )
    return {"status": "installed", "manifest": str(manifest_path), **manifest}


def upgrade_legacy_global_install(home: Path | None = None) -> dict[str, Any]:
    """Re-render the global integrations an install from before the rename left.

    Old-name wrappers run the old package module, which no longer exists, so
    they fail until regenerated. This re-installs exactly the targets recorded
    in the consent manifest (no new targets, no Superpowers changes), and the
    install removes the legacy files. Without a manifest nothing was consented
    and nothing is written.
    """
    user_home = (home or Path.home()).expanduser().resolve()
    if not legacy.legacy_global_artifacts_present(user_home):
        return {"status": "current"}
    manifest = load_global_status(user_home)
    known = {"codex", "claude", "copilot", "gemini", "shell"}
    targets = [
        str(target) for target in manifest.get("targets") or [] if str(target) in known
    ]
    if not manifest.get("installed") or not targets:
        # Nothing to re-render, but the old wrappers cannot run any more:
        # remove the ones carrying the old installer's signature.
        removed = legacy.remove_legacy_global_artifacts(
            user_home,
            claude_memory_upgraded=not _claude_memory_references_legacy_home(user_home),
        )
        return {"status": "not_installed", "legacy_files_removed": removed}
    project_root = Path(str(manifest.get("project_root") or user_home))
    if not project_root.is_dir():
        project_root = user_home
    result = install_global_integrations(
        project_root,
        home=user_home,
        targets=targets,
        install_shell_aliases=False,
        ensure_superpowers=False,
    )
    return {
        "status": "upgraded",
        "targets": targets,
        "legacy_files_removed": result.get("legacy_files_removed", []),
    }


def _valid_manifest_record(record) -> bool:
    """A mirrored consent manifest must carry the targets a reader needs.

    An empty target list is valid and deliberately so: removing the last
    integration is a consent withdrawal, and dropping it would leave the
    shadow asserting consent the user has revoked. Fifth module where
    rejecting the empty state would have discarded exactly the record #613
    needs -- and the only one where the discarded record is a permission.
    """

    return isinstance(record.get("targets"), list)


def _read_manifest_raw(path: Path) -> dict[str, Any]:
    """Read the manifest as persisted, without the loader's normalisation."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def global_manifest_projection(home: Path | None = None) -> dict[str, Any]:
    """Rebuild the connected-service consent manifest from its shadow journal."""

    # vesta_home() is the module's own resolver and applies .resolve(); a
    # re-derived path here would read a different file on any platform where
    # the resolved and unresolved forms differ, which is exactly where a
    # comparator quietly comparing the wrong file would be hardest to notice.
    return shadow_journal.projection(
        vesta_home(home) / "global.json", is_valid_record=_valid_manifest_record
    )


def global_manifest_contradiction_report(
    home: Path | None = None,
) -> dict[str, Any] | None:
    """``None`` when the consent manifest and its shadow agree, else what differs."""

    manifest_path = vesta_home(home) / "global.json"
    return shadow_journal.contradiction_report(
        manifest_path,
        lambda: _read_manifest_raw(manifest_path),
        is_valid_record=_valid_manifest_record,
    )


def load_global_status(home: Path | None = None) -> dict[str, Any]:
    path = vesta_home(home) / "global.json"
    if not path.exists():
        return {
            **surface_identity_payload(brand=__brand__),
            "status_text": STATUS_TEXT,
            "installed": False,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    data["installed"] = True
    data["superpowers_enabled"] = (
        vesta_home(home).parent / ".agents" / "skills" / "superpowers"
    ).exists()
    return data


def shell_environment() -> dict[str, str]:
    return {"VESTA_ACTIVE": "1", "VESTA_STATUS": STATUS_TEXT, **os.environ}


def _strip_block(text: str, start_marker: str, end_marker: str) -> str:
    text = upgrade_legacy_markers(text)
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start != -1 and end != -1 and end > start:
        end += len(end_marker)
        return (text[:start].rstrip() + "\n" + text[end:].lstrip()).strip() + "\n"
    return text


def update_vesta_source(home: Path | None = None, timeout: int = 120) -> dict[str, Any]:
    """Update the Vesta checkout that is actually running (issue #28).

    Most installs live under ``~/.vesta/source`` (what ``install.ps1``/
    ``install.sh`` create); running ``install.ps1`` from inside an existing
    dev clone instead points the editable install straight at that clone.
    With no explicit ``home``, this resolves the real running location
    (``vesta.updater.install_root()``) rather than assuming the former, so
    ``vesta update`` fixes the checkout that is actually in use. Passing
    ``home`` explicitly (as tests do) keeps the exact ``home/.vesta/source``
    behavior.
    """
    if home is not None:
        user_home = home.expanduser().resolve()
        source = legacy.home_item(user_home, "source")
    else:
        from vesta.updater import install_root

        source = install_root()
    git = shutil.which("git")
    if not source.exists():
        return {
            "status": "missing_source",
            "source": str(source),
            "reason": "No Vesta source checkout found. Re-run the installer to set one up.",
        }
    if not (source / ".git").exists():
        return {
            "status": "not_a_git_checkout",
            "source": str(source),
            "reason": "Vesta source exists but is not a Git checkout; update manually.",
        }
    if not git:
        return {"status": "missing_git", "reason": "Git is required to update Vesta."}
    command = [git, "-C", str(source), "pull", "--ff-only"]
    try:
        completed = subprocess.run(  # nosec
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **no_window_kwargs(),  # no flashing console window on Windows
        )
    except OSError as exc:
        return {"status": "failed", "reason": safe_detail(exc)}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "reason": "Vesta update timed out."}
    return {
        "status": "ok" if completed.returncode == 0 else "failed",
        "source": str(source),
        "returncode": completed.returncode,
        "output_tail": (completed.stdout + completed.stderr)[-1200:],
    }


def uninstall_vesta(
    project_root: Path,
    home: Path | None = None,
    dry_run: bool = True,
    remove_project_files: bool = True,
) -> dict[str, Any]:
    """Remove Vesta-managed blocks, wrappers, and discovery files (issue #28).

    Defaults to a dry run. User content outside Vesta-managed blocks is never
    touched, and the operation is idempotent.
    """
    root = project_root.expanduser().resolve()
    user_home = (home or Path.home()).expanduser().resolve()
    base = vesta_home(user_home)

    # Whole files/dirs Vesta fully owns and can remove.
    owned_paths = [
        base / "bin",
        base / "integrations",
        base / "instructions",
        base / "status.txt",
        base / "global.json",
        user_home / ".agents" / "skills" / "vesta",
    ]
    if remove_project_files:
        owned_paths.extend(
            [
                root / ".cursor" / "rules" / "vesta.mdc",
                root / ".clinerules" / "vesta.md",
                root / ".vestahub" / "project-instructions.md",
                root / ".vestahub" / "activation.json",
                # A project not opened since the rename still has the old names.
                *_legacy_project_files(root),
            ]
        )

    # Files where Vesta owns only a managed block and must preserve user content.
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
                (root / "GEMINI.md", START_MARKER, END_MARKER),
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
        if path.exists()
        and start
        in upgrade_legacy_markers(path.read_text(encoding="utf-8", errors="replace"))
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
            text = upgrade_legacy_markers(
                path.read_text(encoding="utf-8", errors="replace")
            )
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
            "User content outside Vesta-managed blocks is preserved.",
            "Re-run with --confirm to apply. Safe to run repeatedly (idempotent).",
        ],
    }
