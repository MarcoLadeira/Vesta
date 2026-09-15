"""Policy-bound, mockable MCP tool discovery and invocation for the agent runtime."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping
from typing import Any, Iterable

from .state import effective_mcp_servers
from .team_policy import load_team_policy
from .workflow_ledger import redact_structure

_READ_PREFIXES = ("get_", "list_", "read_", "search_", "find_", "status", "diff", "log")
_DESTRUCTIVE_TERMS = ("delete", "drop", "destroy", "reset", "clean", "force", "remove")


def _error(code: str, message: str, **data: Any) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "message": message, **data}


def _bounded(
    value: Any, *, max_chars: int, depth: int = 0, budget: list[int] | None = None
) -> Any:
    if budget is None:
        budget = [max(0, int(max_chars))]
    if budget[0] <= 0:
        return "[truncated]"
    safe = redact_structure(value)
    if depth > 8:
        return "[truncated]"
    if isinstance(safe, str):
        text = safe[: budget[0]]
        budget[0] -= len(text)
        return text
    if isinstance(safe, dict):
        return {
            str(key)[:120]: _bounded(
                item, max_chars=max_chars, depth=depth + 1, budget=budget
            )
            for key, item in list(safe.items())[:100]
            if budget[0] > 0
        }
    if isinstance(safe, list):
        return [
            _bounded(item, max_chars=max_chars, depth=depth + 1, budget=budget)
            for item in safe[:100]
            if budget[0] > 0
        ]
    return safe


class MCPRuntime:
    """Expose existing enabled MCP servers through an explicit policy boundary.

    Clients are injected and implement ``list_tools(cancel=...)`` and
    ``call_tool(name, arguments, cancel=...)``. Vesta does not launch arbitrary
    registry commands here; protocol transports can plug in behind this seam.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        clients: Mapping[str, Any],
        servers: Iterable[dict[str, Any]] | None = None,
        team_policy: dict[str, Any] | None = None,
        max_tools: int = 100,
        max_output_chars: int = 40_000,
    ) -> None:
        self.project_root = project_root.expanduser().resolve()
        self.clients = dict(clients)
        self.servers = (
            list(servers)
            if servers is not None
            else effective_mcp_servers(self.project_root)
        )
        self.team_policy = (
            load_team_policy(self.project_root) if team_policy is None else team_policy
        )
        self.max_tools = max(1, int(max_tools))
        self.max_output_chars = max(1_000, int(max_output_chars))

    def _server(self, server_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.servers if str(item.get("id")) == server_id), None
        )

    def _approved(self, server_id: str) -> bool:
        approved = (self.team_policy or {}).get("approved_mcp_servers")
        return not isinstance(approved, list) or server_id in {
            str(item) for item in approved
        }

    @staticmethod
    def _remote(server: Mapping[str, Any]) -> bool:
        return str(server.get("transport") or "").lower() in {
            "http",
            "https",
            "remote",
            "stdio-or-http",
            "http-or-stdio",
        }

    def _descriptor(
        self, server: Mapping[str, Any], raw: Mapping[str, Any]
    ) -> dict[str, Any]:
        name = str(raw.get("name") or "").strip()
        annotations = (
            raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {}
        )
        read_hint = annotations.get("readOnlyHint")
        read_only = (
            bool(read_hint)
            if isinstance(read_hint, bool)
            else name.lower().startswith(_READ_PREFIXES)
        )
        destructive_hint = annotations.get("destructiveHint")
        destructive = bool(destructive_hint) or any(
            term in name.lower() for term in _DESTRUCTIVE_TERMS
        )
        confirmation_terms = [
            str(item).lower() for item in server.get("requires_confirmation") or []
        ]
        requires_confirmation = (
            destructive
            or not read_only
            or any(
                term and term in name.lower().replace("_", " ")
                for term in confirmation_terms
            )
        )
        return {
            "server_id": str(server.get("id") or ""),
            "server_name": str(server.get("name") or server.get("id") or ""),
            "name": name,
            "description": str(redact_structure(str(raw.get("description") or "")))[
                :500
            ],
            "input_schema": _bounded(raw.get("inputSchema") or {}, max_chars=2_000),
            "read_only": read_only,
            "destructive": destructive,
            "remote": self._remote(server),
            "requires_confirmation": requires_confirmation,
            "permission_level": str(server.get("permission_level") or "unknown"),
        }

    def discover(
        self,
        server_id: str | None = None,
        *,
        allow_remote: bool = False,
        cancel: Any = None,
    ) -> dict[str, Any]:
        if cancel is not None and cancel.is_set():
            return _error("CANCELLED", "MCP discovery was cancelled", tools=[])
        tools: list[dict[str, Any]] = []
        blocked: list[str] = []
        errors: list[dict[str, str]] = []
        for server in self.servers:
            sid = str(server.get("id") or "")
            if server_id and sid != server_id:
                continue
            client = self.clients.get(sid)
            if (
                not server.get("effective_enabled")
                or not self._approved(sid)
                or client is None
                or (self._remote(server) and not allow_remote)
            ):
                blocked.append(sid)
                continue
            try:
                raw_tools = client.list_tools(cancel=cancel)
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                errors.append(
                    {"server_id": sid, "message": str(redact_structure(str(exc)))[:500]}
                )
                continue
            for raw in raw_tools or []:
                if (
                    not isinstance(raw, Mapping)
                    or not str(raw.get("name") or "").strip()
                ):
                    continue
                tools.append(self._descriptor(server, raw))
                if len(tools) >= self.max_tools:
                    break
            if len(tools) >= self.max_tools:
                break
        return {
            "ok": not errors,
            "tools": tools,
            "blocked_servers": sorted(set(blocked)),
            "errors": errors,
            "truncated": len(tools) >= self.max_tools,
        }

    def _validate_paths(
        self, server: Mapping[str, Any], arguments: Mapping[str, Any]
    ) -> bool:
        forbidden = {
            str(item).replace("\\", "/").strip("/").lower()
            for item in server.get("forbidden_paths") or []
        }

        def values(value: Any, key: str = ""):
            if isinstance(value, Mapping):
                for child_key, child in value.items():
                    yield from values(child, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    yield from values(child, key)
            elif isinstance(value, str) and any(
                term in key.lower() for term in ("path", "file", "root", "directory")
            ):
                yield value

        for raw in values(arguments):
            candidate = Path(raw).expanduser()
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (self.project_root / candidate).resolve()
            )
            try:
                relative = resolved.relative_to(self.project_root).as_posix().lower()
            except ValueError:
                return False
            if any(
                relative == item or relative.startswith(item + "/")
                for item in forbidden
            ):
                return False
        return True

    def invoke(
        self,
        server_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        allow_write: bool = False,
        allow_remote: bool = False,
        cancel: Any = None,
    ) -> dict[str, Any]:
        if cancel is not None and cancel.is_set():
            return _error("CANCELLED", "MCP invocation was cancelled")
        server = self._server(server_id)
        if server is None:
            return _error("MCP_SERVER_UNKNOWN", "MCP server is not registered")
        if not server.get("effective_enabled") or not self._approved(server_id):
            return _error(
                "MCP_SERVER_NOT_APPROVED", "MCP server is disabled or not team-approved"
            )
        if self._remote(server) and not allow_remote:
            return _error(
                "MCP_REMOTE_CONFIRMATION_REQUIRED",
                "Remote MCP calls require explicit current-task authorization",
            )
        client = self.clients.get(server_id)
        if client is None:
            return _error(
                "MCP_CLIENT_UNAVAILABLE", "No MCP transport client is attached"
            )
        discovery = self.discover(server_id, allow_remote=allow_remote, cancel=cancel)
        descriptor = next(
            (
                item
                for item in discovery.get("tools") or []
                if item["name"] == tool_name
            ),
            None,
        )
        if descriptor is None:
            return _error(
                "MCP_TOOL_UNKNOWN",
                "MCP tool was not discovered from the approved server",
            )
        if descriptor["destructive"]:
            return _error(
                "MCP_DESTRUCTIVE_TOOL",
                "Destructive MCP tools require a separate dangerous-action boundary",
            )
        if not descriptor["read_only"] and not allow_write:
            return _error(
                "MCP_WRITE_CONFIRMATION_REQUIRED",
                "Write-capable MCP tools require current-task write authorization",
            )
        if not self._validate_paths(server, arguments):
            return _error(
                "MCP_PATH_OUTSIDE_REPO",
                "MCP path arguments must stay inside approved project roots",
            )
        if cancel is not None and cancel.is_set():
            return _error("CANCELLED", "MCP invocation was cancelled")
        try:
            result = client.call_tool(tool_name, dict(arguments), cancel=cancel)
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            return _error("MCP_CALL_FAILED", str(redact_structure(str(exc)))[:500])
        return {
            "ok": True,
            "server_id": server_id,
            "tool": descriptor,
            "result": _bounded(result, max_chars=self.max_output_chars),
        }
