from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import load_json, project_op_dir, write_json


DEFAULT_SERVERS = {
    "filesystem-readonly": {
        "type": "local",
        "enabled": True,
        "risk": "medium",
        "permission": "read project root",
    },
    "git-local": {
        "type": "local",
        "enabled": True,
        "risk": "medium",
        "permission": "status/diff/log only",
    },
    "testing-local": {
        "type": "local",
        "enabled": True,
        "risk": "medium",
        "permission": "test commands only",
    },
    "memory-local": {
        "type": "local",
        "enabled": True,
        "risk": "medium",
        "permission": "redacted summaries",
    },
    "github": {
        "type": "remote_or_container",
        "enabled": False,
        "risk": "medium",
        "permission": "minimal scopes",
    },
    "browser-web": {
        "type": "local_browser",
        "enabled": False,
        "risk": "medium",
        "permission": "localhost first",
    },
    "database": {
        "type": "local",
        "enabled": False,
        "risk": "high",
        "permission": "read-only dev DB",
    },
    "package-lookup": {
        "type": "registry",
        "enabled": False,
        "risk": "medium",
        "permission": "read-only",
    },
}


def project_profile(root: Path) -> dict[str, Any]:
    path = project_op_dir(root) / "mcp.profile.json"
    return load_json(
        path,
        {
            "enabled": [
                "filesystem-readonly",
                "git-local",
                "testing-local",
                "memory-local",
            ],
            "roots": [str(root)],
        },
    )


def mcp_doctor(root: Path) -> dict[str, Any]:
    profile = project_profile(root)
    enabled = profile.get("enabled", [])
    roots = [Path(item).expanduser() for item in profile.get("roots", [str(root)])]
    return {
        "profile": profile,
        "servers": {
            name: DEFAULT_SERVERS.get(name, {"enabled": False, "risk": "unknown"})
            for name in enabled
        },
        "roots": [{"path": str(path), "exists": path.exists()} for path in roots],
        "warnings": [
            "GitHub/browser/database are disabled by default; enable per project only when needed."
        ],
    }


def render_codex_mcp_config(root: Path) -> dict[str, Any]:
    profile = project_profile(root)
    servers: dict[str, Any] = {}
    if "filesystem-readonly" in profile.get("enabled", []):
        servers["filesystem"] = {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", str(root)],
        }
    if "github" in profile.get("enabled", []):
        servers["github"] = {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
        }
    rendered = {
        "servers": servers,
        "notes": "Review permissions before enabling write-capable MCP tools.",
    }
    write_json(project_op_dir(root) / "cache" / "mcp.codex.json", rendered)
    return rendered
