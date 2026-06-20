"""Per-client activation detection for OPai (issue #35).

Reports whether each supported AI client (Claude, Codex, Copilot, Cursor, Cline)
is active, broken, or missing for a project, and gives a concrete repair command
when something is wrong. Also detects stale/moved install paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from opai.integrations import END_MARKER, START_MARKER, opai_home


REPAIR_COMMAND = "opai activate --repair"


def _client_specs(project_root: Path, home: Path) -> list[dict[str, Any]]:
    return [
        {
            "id": "claude",
            "label": "Claude Code",
            "project_files": [project_root / "CLAUDE.md"],
            "global_files": [home / ".claude" / "CLAUDE.md"],
        },
        {
            "id": "codex",
            "label": "Codex CLI",
            "project_files": [project_root / "AGENTS.md"],
            "global_files": [home / ".agents" / "skills" / "opai" / "SKILL.md"],
        },
        {
            "id": "copilot",
            "label": "GitHub Copilot",
            "project_files": [project_root / ".github" / "copilot-instructions.md"],
            "global_files": [
                opai_home(home) / "integrations" / "copilot-instructions.md"
            ],
        },
        {
            "id": "cursor",
            "label": "Cursor",
            "project_files": [project_root / ".cursor" / "rules" / "opai.mdc"],
            "global_files": [],
        },
        {
            "id": "cline",
            "label": "Cline",
            "project_files": [project_root / ".clinerules" / "opai.md"],
            "global_files": [],
        },
    ]


def _has_opai_block(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return START_MARKER in text and END_MARKER in text


def _client_status(spec: dict[str, Any]) -> dict[str, Any]:
    project_files = spec["project_files"]
    global_files = spec["global_files"]

    project_present = [path for path in project_files if path.exists()]
    project_managed = [path for path in project_files if _has_opai_block(path)]
    global_present = [path for path in global_files if path.exists()]

    if project_managed:
        status = "active"
        reason = "OPai managed block present."
    elif project_present:
        status = "broken"
        reason = "Instruction file exists but is missing the OPai managed block."
    else:
        status = "missing"
        reason = "No OPai instruction file for this client."

    result: dict[str, Any] = {
        "id": spec["id"],
        "label": spec["label"],
        "status": status,
        "reason": reason,
        "project_files": [str(path) for path in project_files],
        "project_managed": [str(path) for path in project_managed],
    }
    if global_files:
        result["global_files"] = [str(path) for path in global_files]
        result["global_ready"] = bool(global_present)
        if not global_present and status == "active":
            result["status"] = "broken"
            result["reason"] = (
                "Project file is managed but global discovery file is missing."
            )
    if result["status"] != "active":
        result["repair"] = REPAIR_COMMAND
    return result


def client_integrations_status(
    project_root: Path, home: Path | None = None
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    user_home = (home or Path.home()).expanduser().resolve()
    clients = [_client_status(spec) for spec in _client_specs(root, user_home)]
    active = [client["id"] for client in clients if client["status"] == "active"]
    broken = [client["id"] for client in clients if client["status"] == "broken"]
    missing = [client["id"] for client in clients if client["status"] == "missing"]
    return {
        "clients": clients,
        "summary": {
            "active": active,
            "broken": broken,
            "missing": missing,
        },
        "repair_command": REPAIR_COMMAND if (broken or missing) else None,
    }


def detect_stale_paths(project_root: Path, home: Path | None = None) -> dict[str, Any]:
    """Detect moved repos or moved OPai installs and surface a repair path."""
    import json

    root = project_root.expanduser().resolve()
    user_home = (home or Path.home()).expanduser().resolve()
    issues: list[dict[str, Any]] = []

    # 1. Activation recorded for a different path (repo was moved/copied).
    activation = root / ".opaihub" / "activation.json"
    if activation.exists():
        try:
            recorded = json.loads(activation.read_text(encoding="utf-8")).get(
                "project_root"
            )
        except (OSError, json.JSONDecodeError):
            recorded = None
        if recorded and Path(recorded).resolve() != root:
            issues.append(
                {
                    "kind": "moved_project",
                    "recorded_root": recorded,
                    "current_root": str(root),
                    "repair": REPAIR_COMMAND,
                }
            )

    # 2. Global manifest references files or a source path that no longer exists.
    manifest = opai_home(user_home) / "global.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        written = [Path(p) for p in data.get("written", []) if isinstance(p, str)]
        missing_written = [str(p) for p in written if not p.exists()]
        if missing_written:
            issues.append(
                {
                    "kind": "missing_global_files",
                    "missing": missing_written[:10],
                    "missing_count": len(missing_written),
                    "repair": "opai integrate install",
                }
            )

    source = opai_home(user_home) / "source"
    source_present = source.exists()

    return {
        "ok": not issues,
        "issues": issues,
        "opai_source": str(source),
        "opai_source_present": source_present,
    }
