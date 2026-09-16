"""Generate client MCP configs that enforce path and write policy (#15).

The MCP registry declares permission fields (allowed/forbidden paths,
confirmation requirements, permission level), but earlier generated configs
passed only command/args/env/notes — turning enforceable policy into advisory
text. A generated filesystem config could then expose ``.git``/``.env`` or
grant writes despite Vesta's safety promise.

This module renders those fields into an enforceable ``policy`` block on every
server, defaults path-granting servers to read-only, always blocks
security-sensitive paths (``.git``, ``.env``, secrets, caches) unless a server
explicitly opts in, and scopes filesystem roots to the declared allowed paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .state import effective_mcp_servers, state_dir

# Servers that grant raw file access and must be read-only + path-scoped by
# default. Identified by id or by the filesystem MCP package in their args.
_FILESYSTEM_PACKAGE = "server-filesystem"

# Always-forbidden paths for any path-scoped server unless it explicitly opts
# in. These are the paths Vesta's docs promise are safe: VCS internals, secret
# material, and generated/dependency caches.
_SECURITY_FORBIDDEN: tuple[str, ...] = (
    ".git",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "secrets",
    "credentials.json",
    ".vestahub",
    ".opcoding",
    ".opcoding-tools",
    "node_modules",
    "__pycache__",
    ".venv",
    "dist",
    "build",
)


def _render_arg(value: str, project_root: Path) -> str:
    return value.replace("${PROJECT_ROOT}", str(project_root.resolve()))


def _is_filesystem_server(server: dict[str, Any]) -> bool:
    if server.get("id") == "filesystem":
        return True
    return any(_FILESYSTEM_PACKAGE in str(arg) for arg in server.get("args", []))


def _is_path_scoped(server: dict[str, Any]) -> bool:
    """True when the server grants filesystem access that must be bounded."""
    return _is_filesystem_server(server) or bool(server.get("allowed_paths"))


def _merged_forbidden(server: dict[str, Any], project_root: Path) -> list[str]:
    """Registry forbidden paths plus the always-on security defaults."""
    declared = [str(item) for item in server.get("forbidden_paths", [])]
    # A server can only relax a security default by naming it under
    # ``write_allowed`` / ``allow_forbidden`` — otherwise it always applies.
    opted_in = {str(item).lower() for item in server.get("allow_forbidden", [])}
    merged: list[str] = []
    for item in [*declared, *_SECURITY_FORBIDDEN]:
        rendered = _render_arg(item, project_root)
        if item.lower() in opted_in:
            continue
        if rendered not in merged:
            merged.append(rendered)
    return merged


def _server_policy(server: dict[str, Any], project_root: Path) -> dict[str, Any]:
    path_scoped = _is_path_scoped(server)
    # Writes are opt-in: a path-granting server is read-only unless the entry
    # explicitly sets write_enabled (a project overlay decision).
    read_only = path_scoped and not bool(server.get("write_enabled"))
    allowed = [
        _render_arg(str(item), project_root) for item in server.get("allowed_paths", [])
    ]
    return {
        "permissionLevel": server.get("permission_level", "medium"),
        "pathScoped": path_scoped,
        "readOnly": read_only,
        "writeEnabled": bool(server.get("write_enabled")),
        "allowedPaths": allowed,
        "forbiddenPaths": _merged_forbidden(server, project_root)
        if path_scoped
        else [
            _render_arg(str(item), project_root)
            for item in server.get("forbidden_paths", [])
        ],
        "requiresConfirmation": [
            str(item) for item in server.get("requires_confirmation", [])
        ],
    }


def _filesystem_args(
    server: dict[str, Any], project_root: Path, policy: dict[str, Any]
) -> list[str]:
    """Scope filesystem roots to the declared allowed paths (never system-wide).

    The upstream ``server-filesystem`` restricts access to exactly the directory
    args it is given, so replacing any user-supplied roots with the allowed
    paths is real enforcement, not advice. Falls back to the project root.
    """
    prefix: list[str] = []
    for arg in server.get("args", []):
        prefix.append(_render_arg(str(arg), project_root))
        if _FILESYSTEM_PACKAGE in str(arg):
            break
    roots = policy["allowedPaths"] or [str(project_root.resolve())]
    return [*prefix, *roots]


def render_mcp_config(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    servers: dict[str, Any] = {}
    for server in effective_mcp_servers(root):
        if not server.get("effective_enabled"):
            continue
        command = server.get("command", "")
        if not command:
            continue
        policy = _server_policy(server, root)
        if _is_filesystem_server(server):
            args = _filesystem_args(server, root, policy)
        else:
            args = [_render_arg(str(arg), root) for arg in server.get("args", [])]
        servers[server["id"]] = {
            "command": command,
            "args": args,
            "env": server.get("env", {}),
            "notes": server.get("notes", ""),
            "policy": policy,
        }
    return {
        "mcpServers": servers,
        "policy": (
            "Generated from Vesta Hub effective MCP state with enforced path and "
            "write policy. Path-granting servers are read-only and block .git, "
            ".env, secrets, and caches unless explicitly enabled. Review before use."
        ),
    }


def validate_mcp_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return policy violations in a generated config (empty = compliant)."""
    violations: list[dict[str, str]] = []
    required_blocks = {".git", ".env"}
    for server_id, server in (config.get("mcpServers") or {}).items():
        policy = server.get("policy") or {}
        if not policy.get("pathScoped"):
            continue
        forbidden = {str(item) for item in policy.get("forbiddenPaths", [])}
        forbidden_names = {Path(item).name for item in forbidden} | forbidden
        for needed in required_blocks:
            if needed not in forbidden_names:
                violations.append(
                    {
                        "server": server_id,
                        "code": "MISSING_FORBIDDEN_PATH",
                        "detail": f"{needed} is not blocked",
                    }
                )
        if not policy.get("readOnly") and not policy.get("writeEnabled"):
            violations.append(
                {
                    "server": server_id,
                    "code": "WRITE_WITHOUT_OPT_IN",
                    "detail": "path-scoped server is writable without write_enabled",
                }
            )
    return {"ok": not violations, "violations": violations}


def write_mcp_config(project_root: Path) -> Path:
    path = state_dir(project_root) / "generated" / "mcp_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(render_mcp_config(project_root), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
