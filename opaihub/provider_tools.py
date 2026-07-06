"""Bounded repository tools for non-native provider agent loops."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from .aci import AgentComputerInterface, Observation
from .command_runner import redact
from .repo_context import classify_dirty_paths, resolve_repo_context

READ_TOOLS = ("find_files", "search_code", "read_file")
WRITE_TOOLS = ("apply_patch", "run_tests")
MAX_TOOL_CALLS = 12
MAX_PATCH_CHARS = 120_000


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def _error(code: str, message: str, *, kind: str = "provider_tool") -> dict[str, Any]:
    return Observation(
        kind,
        False,
        error_code=code,
        message=redact(message),
    ).to_dict()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


def _patch_paths(patch: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("+++ "):
            continue
        value = line[4:].split("\t", 1)[0].strip().strip('"')
        if value == "/dev/null":
            continue
        if value.startswith("b/"):
            value = value[2:]
        normalized = str(PurePosixPath(value.replace("\\", "/")))
        if normalized and normalized not in paths:
            paths.append(normalized)
    return tuple(paths)


class RepositoryToolExecutor:
    """Execute a small provider tool vocabulary inside one repository."""

    def __init__(
        self,
        repo_root: Path,
        *,
        allow_edits: bool,
        aci: AgentComputerInterface | None = None,
        max_patch_chars: int = MAX_PATCH_CHARS,
    ) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self.allow_edits = bool(allow_edits)
        self.aci = aci or AgentComputerInterface(self.repo_root)
        self.max_patch_chars = max(1, int(max_patch_chars))
        self.initial_dirty_paths = resolve_repo_context(self.repo_root).dirty_paths
        self.test_commands = self._test_commands()

    def _test_commands(self) -> dict[str, list[str]]:
        commands: dict[str, list[str]] = {}
        if (self.repo_root / "tests").is_dir() or (
            self.repo_root / "pyproject.toml"
        ).is_file():
            commands["python-unittest"] = [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
            ]
        if (self.repo_root / "package.json").is_file():
            commands["npm-test"] = ["npm", "test"]
        if (self.repo_root / "Cargo.toml").is_file():
            commands["cargo-test"] = ["cargo", "test"]
        if (self.repo_root / "go.mod").is_file():
            commands["go-test"] = ["go", "test", "./..."]
        return commands

    def schemas(self) -> list[dict[str, Any]]:
        schemas = [
            _schema(
                "find_files",
                "List repository files matching a glob without leaving the repository.",
                {
                    "pattern": {"type": "string", "default": "*"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
            ),
            _schema(
                "search_code",
                "Search repository text with ripgrep in optional repository-relative paths.",
                {
                    "query": {"type": "string"},
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                required=("query",),
            ),
            _schema(
                "read_file",
                "Read a bounded line range from a repository-relative file.",
                {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                required=("path",),
            ),
        ]
        if not self.allow_edits:
            return schemas
        schemas.append(
            _schema(
                "apply_patch",
                "Validate and apply a unified Git patch inside the repository.",
                {"patch": {"type": "string"}},
                required=("patch",),
            )
        )
        if self.test_commands:
            schemas.append(
                _schema(
                    "run_tests",
                    "Run one repository-detected test command by its fixed identifier.",
                    {
                        "command_id": {
                            "type": "string",
                            "enum": sorted(self.test_commands),
                        },
                        "scope": {"type": "string", "default": "selected"},
                    },
                    required=("command_id",),
                )
            )
        return schemas

    def _safe_paths(self, paths: Any) -> tuple[str, ...] | None:
        if paths is None:
            return ()
        if not isinstance(paths, list) or not all(
            isinstance(item, str) for item in paths
        ):
            return None
        safe: list[str] = []
        for value in paths:
            candidate = (self.repo_root / value).resolve()
            try:
                relative = candidate.relative_to(self.repo_root).as_posix()
            except ValueError:
                return None
            safe.append(relative)
        return tuple(safe)

    def _apply_patch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        patch = arguments.get("patch")
        if not isinstance(patch, str) or not patch.strip():
            return _error("INVALID_TOOL_ARGUMENTS", "Patch must be a non-empty string")
        if re.search(r"(?m)^deleted file mode |^\+\+\+ /dev/null$", patch):
            return _error("DESTRUCTIVE_PATCH", "Provider patches may not delete files")
        if len(patch) > self.max_patch_chars:
            return _error(
                "PATCH_TOO_LARGE", "Provider patch exceeds the configured limit"
            )
        paths = _patch_paths(patch)
        if not paths:
            return _error("INVALID_PATCH", "Patch does not name a target file")
        safe_paths = self._safe_paths(list(paths))
        if safe_paths is None:
            return _error("PATH_OUTSIDE_REPO", "Patch target is outside the repository")
        dirty = classify_dirty_paths(self.initial_dirty_paths, safe_paths)
        if not dirty.can_proceed:
            return _error(
                "DIRTY_PATH_CONFLICT",
                "Patch overlaps pre-existing user changes: "
                + ", ".join(dirty.conflicting_paths),
            )
        checked = self.aci.apply_patch(patch, check_only=True)
        if not checked.ok:
            return Observation(
                "patch_apply",
                False,
                {"paths": list(safe_paths), "validation": checked.to_dict()},
                "PATCH_CHECK_FAILED",
                checked.message,
                checked.duration_ms,
            ).to_dict()
        applied = self.aci.apply_patch(patch)
        return Observation(
            "patch_apply",
            applied.ok,
            {"paths": list(safe_paths), "command": applied.data},
            applied.error_code,
            applied.message,
            applied.duration_ms,
        ).to_dict()

    def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        cancel: Any = None,
    ) -> dict[str, Any]:
        if cancel is not None and cancel.is_set():
            return _error("CANCELLED", "Provider tool call was cancelled")
        allowed = set(READ_TOOLS)
        if self.allow_edits:
            allowed.update(WRITE_TOOLS)
        if name not in allowed:
            return _error("TOOL_NOT_ALLOWED", f"Tool {name!r} is not allowed")
        if not isinstance(arguments, dict):
            return _error("INVALID_TOOL_ARGUMENTS", "Tool arguments must be an object")
        if name == "find_files":
            pattern = str(arguments.get("pattern") or "*")
            if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                return _error(
                    "INVALID_TOOL_ARGUMENTS",
                    "File pattern must be repository-relative",
                )
            limit = _bounded_int(
                arguments.get("limit"), default=200, minimum=1, maximum=500
            )
            if limit is None:
                return _error("INVALID_TOOL_ARGUMENTS", "Invalid file result limit")
            return self.aci.find_files(pattern, limit=limit).to_dict()
        if name == "search_code":
            query = arguments.get("query")
            if not isinstance(query, str) or not query:
                return _error("INVALID_TOOL_ARGUMENTS", "Search query is required")
            paths = self._safe_paths(arguments.get("paths"))
            if paths is None:
                return _error(
                    "PATH_OUTSIDE_REPO", "Search path is outside the repository"
                )
            limit = _bounded_int(
                arguments.get("limit"), default=200, minimum=1, maximum=200
            )
            if limit is None:
                return _error("INVALID_TOOL_ARGUMENTS", "Invalid search result limit")
            return self.aci.search(query, paths=paths, limit=limit).to_dict()
        if name == "read_file":
            path = arguments.get("path")
            if not isinstance(path, str) or not path:
                return _error("INVALID_TOOL_ARGUMENTS", "File path is required")
            start_line = _bounded_int(
                arguments.get("start_line"),
                default=1,
                minimum=1,
                maximum=1_000_000,
            )
            end_line = _bounded_int(
                arguments.get("end_line"),
                default=1_000_000,
                minimum=1,
                maximum=1_000_000,
            )
            if start_line is None or end_line is None:
                return _error("INVALID_TOOL_ARGUMENTS", "Invalid file line range")
            return self.aci.read_slice(
                path,
                start_line=start_line,
                end_line=(end_line if arguments.get("end_line") is not None else None),
            ).to_dict()
        if name == "apply_patch":
            return self._apply_patch(arguments)
        command_id = arguments.get("command_id")
        if not isinstance(command_id, str) or command_id not in self.test_commands:
            return _error(
                "TEST_COMMAND_NOT_ALLOWED",
                "Only repository-detected test command identifiers are allowed",
            )
        scope = str(arguments.get("scope") or command_id)[:120]
        return self.aci.run_tests(self.test_commands[command_id], scope=scope).to_dict()

    def invoke_call(
        self, call: dict[str, Any], *, cancel: Any = None
    ) -> dict[str, Any]:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict):
            return _error("INVALID_TOOL_CALL", "Provider tool call has no function")
        name = str(function.get("name") or "")
        raw_arguments = function.get("arguments", {})
        try:
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else raw_arguments
            )
        except json.JSONDecodeError:
            return _error(
                "INVALID_TOOL_ARGUMENTS", "Provider tool arguments are not valid JSON"
            )
        return self.invoke(name, arguments, cancel=cancel)
