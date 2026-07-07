"""Bounded repository tools for non-native provider agent loops."""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - fixed git argv, never a shell
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from .aci import AgentComputerInterface, Observation
from .command_runner import redact
from .proc import no_window_kwargs
from .repo_context import classify_dirty_paths, resolve_repo_context

READ_TOOLS = ("find_files", "search_code", "read_file", "git_status")
WRITE_TOOLS = (
    "apply_patch",
    "write_file",
    "run_tests",
    "git_create_branch",
    "git_commit",
)
# Outward-facing tools: available only with a connected GitHub account AND the
# persisted `opai github allow-push on` consent (see github_connector).
GIT_OPS_TOOLS = ("git_push", "open_pr")
MAX_TOOL_CALLS = 12
MAX_PATCH_CHARS = 120_000
MAX_WRITE_CHARS = 200_000

# write_file may never touch VCS internals, OPai state, env/secret files, or
# generated trees — matching the MCP path policy (#15).
_BLOCKED_WRITE_PREFIXES = (
    ".git/",
    ".opaihub/",
    ".opcoding/",
    "node_modules/",
    ".venv/",
    "venv/",
)
_BLOCKED_WRITE_NAMES = re.compile(
    r"(?:^|/)(?:\.env(?:\..*)?|.*\.pem|.*\.key|id_rsa.*|secrets?\.(?:json|ya?ml|toml))$",
    re.IGNORECASE,
)
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$")


def _valid_branch(name: Any) -> str | None:
    value = str(name or "").strip()
    if (
        not _BRANCH_RE.match(value)
        or ".." in value
        or value.endswith((".lock", "/", "."))
        or "@{" in value
        or "//" in value
    ):
        return None
    return value


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


def available_tool_names(
    repo_root: Path,
    *,
    allow_edits: bool,
    allow_git_ops: bool | None = None,
) -> tuple[str, ...]:
    """The exact tool vocabulary a provider loop gets for this repository.

    Used to render a truthful capability contract: the model is told the tools
    it can literally call, not abstract capability nouns.
    """
    executor = RepositoryToolExecutor(
        repo_root, allow_edits=allow_edits, allow_git_ops=allow_git_ops
    )
    return tuple(schema["function"]["name"] for schema in executor.schemas())


class RepositoryToolExecutor:
    """Execute a small provider tool vocabulary inside one repository."""

    def __init__(
        self,
        repo_root: Path,
        *,
        allow_edits: bool,
        aci: AgentComputerInterface | None = None,
        max_patch_chars: int = MAX_PATCH_CHARS,
        allow_git_ops: bool | None = None,
        git_run: Any = None,
    ) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self.allow_edits = bool(allow_edits)
        self.aci = aci or AgentComputerInterface(self.repo_root)
        self.max_patch_chars = max(1, int(max_patch_chars))
        self.initial_dirty_paths = resolve_repo_context(self.repo_root).dirty_paths
        self.test_commands = self._test_commands()
        self._git_run = git_run or subprocess.run
        # Paths this run created/changed; git_commit stages exactly these by
        # default so a commit can never sweep up unrelated user work.
        self.written_paths: list[str] = []
        if allow_git_ops is None:
            # Push/PR need edits enabled, a connected GitHub token, AND the
            # persisted `opai github allow-push on` consent — all three.
            allow_git_ops = False
            if self.allow_edits:
                try:
                    from .github_connector import push_allowed, stored_github_token

                    allow_git_ops = push_allowed() and bool(stored_github_token()[0])
                except Exception:  # noqa: BLE001 - consent lookup must fail closed
                    allow_git_ops = False
        self.allow_git_ops = bool(allow_git_ops) and self.allow_edits

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
            _schema(
                "git_status",
                "Show the repository's current branch and changed files.",
                {},
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
        schemas.append(
            _schema(
                "write_file",
                "Create a new file or fully replace an existing repository file "
                "with the given content. Prefer this over apply_patch for new "
                "files or whole-file rewrites.",
                {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                required=("path", "content"),
            )
        )
        schemas.append(
            _schema(
                "git_create_branch",
                "Create and switch to a new git branch for this change.",
                {"name": {"type": "string"}},
                required=("name",),
            )
        )
        schemas.append(
            _schema(
                "git_commit",
                "Commit the files this run created or changed. Stages only "
                "those paths, never unrelated user work.",
                {
                    "message": {"type": "string"},
                    "paths": {"type": "array", "items": {"type": "string"}},
                },
                required=("message",),
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
        if self.allow_git_ops:
            schemas.append(
                _schema(
                    "git_push",
                    "Push the current branch to origin (never force).",
                    {"branch": {"type": "string"}},
                )
            )
            schemas.append(
                _schema(
                    "open_pr",
                    "Open a GitHub pull request from the pushed branch.",
                    {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "base": {"type": "string", "default": "main"},
                    },
                    required=("title",),
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
        if applied.ok:
            for path in safe_paths:
                if path not in self.written_paths:
                    self.written_paths.append(path)
        return Observation(
            "patch_apply",
            applied.ok,
            {"paths": list(safe_paths), "command": applied.data},
            applied.error_code,
            applied.message,
            applied.duration_ms,
        ).to_dict()

    def _write_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Create or fully replace one repository file (never delete).

        Whole-file writes are far more reliable than unified patches for small
        models — a malformed diff was the main reason free-tier runs "couldn't
        touch code". Same guardrails as apply_patch: repository-relative only,
        protected paths blocked, pre-existing user changes never overwritten.
        """
        path_value = arguments.get("path")
        content = arguments.get("content")
        if not isinstance(path_value, str) or not path_value.strip():
            return _error("INVALID_TOOL_ARGUMENTS", "File path is required")
        if not isinstance(content, str):
            return _error("INVALID_TOOL_ARGUMENTS", "File content must be a string")
        if len(content) > MAX_WRITE_CHARS:
            return _error("WRITE_TOO_LARGE", "File content exceeds the size limit")
        safe = self._safe_paths([path_value.strip()])
        if not safe:
            return _error("PATH_OUTSIDE_REPO", "Write target is outside the repository")
        relative = safe[0]
        lowered = relative.lower()
        if lowered.startswith(_BLOCKED_WRITE_PREFIXES) or _BLOCKED_WRITE_NAMES.search(
            lowered
        ):
            return _error("PATH_BLOCKED", f"Writing to {relative} is not allowed")
        dirty = classify_dirty_paths(self.initial_dirty_paths, (relative,))
        if not dirty.can_proceed and relative not in self.written_paths:
            return _error(
                "DIRTY_PATH_CONFLICT",
                f"{relative} has pre-existing user changes; refusing to overwrite",
            )
        target = self.repo_root / relative
        created = not target.exists()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        except OSError as exc:
            return _error("WRITE_FAILED", f"Could not write {relative}: {exc}")
        if relative not in self.written_paths:
            self.written_paths.append(relative)
        return Observation(
            "file_write",
            True,
            {
                "path": relative,
                "created": created,
                "bytes": len(content.encode("utf-8")),
            },
            message=f"{'Created' if created else 'Replaced'} {relative}",
        ).to_dict()

    def _git(self, argv: list[str], *, timeout: float = 60.0) -> dict[str, Any]:
        """Run one fixed git command in the repository; argv only, no shell."""
        try:
            completed = self._git_run(
                ["git", *argv],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "output": redact(str(exc))}
        output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
        return {"ok": completed.returncode == 0, "output": redact(output)[-2_000:]}

    def _git_create_branch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = _valid_branch(arguments.get("name"))
        if name is None:
            return _error("INVALID_BRANCH_NAME", "Branch name is not a valid git ref")
        result = self._git(["checkout", "-b", name])
        return Observation(
            "git_branch",
            result["ok"],
            {"branch": name},
            "" if result["ok"] else "GIT_BRANCH_FAILED",
            result["output"] if not result["ok"] else f"Created branch {name}",
        ).to_dict()

    def _git_commit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        message = redact(str(arguments.get("message") or "").strip())[:500]
        if not message:
            return _error("INVALID_TOOL_ARGUMENTS", "A commit message is required")
        raw_paths = arguments.get("paths")
        if raw_paths is None:
            paths: tuple[str, ...] | None = tuple(self.written_paths)
        else:
            paths = self._safe_paths(raw_paths)
        if paths is None:
            return _error("PATH_OUTSIDE_REPO", "Commit path is outside the repository")
        if not paths:
            return _error(
                "NOTHING_TO_COMMIT",
                "No files were changed by this run; nothing to commit",
            )
        staged = self._git(["add", "--", *paths])
        if not staged["ok"]:
            return _error("GIT_ADD_FAILED", staged["output"])
        committed = self._git(["commit", "-m", message, "--", *paths])
        if not committed["ok"]:
            return _error("GIT_COMMIT_FAILED", committed["output"])
        head = self._git(["rev-parse", "--short", "HEAD"])
        return Observation(
            "git_commit",
            True,
            {"paths": list(paths), "sha": head["output"] if head["ok"] else ""},
            message=f"Committed {len(paths)} file(s)",
        ).to_dict()

    def _current_branch(self) -> str:
        result = self._git(["branch", "--show-current"])
        return result["output"].strip() if result["ok"] else ""

    def _git_push(self, arguments: dict[str, Any]) -> dict[str, Any]:
        branch = arguments.get("branch")
        name = _valid_branch(branch) if branch else self._current_branch()
        if not name:
            return _error("INVALID_BRANCH_NAME", "No valid branch to push")
        result = self._git(["push", "-u", "origin", name], timeout=120.0)
        return Observation(
            "git_push",
            result["ok"],
            {"branch": name},
            "" if result["ok"] else "GIT_PUSH_FAILED",
            result["output"] if not result["ok"] else f"Pushed {name} to origin",
        ).to_dict()

    def _open_pr(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .github_connector import create_pull_request

        title = str(arguments.get("title") or "").strip()
        if not title:
            return _error("INVALID_TOOL_ARGUMENTS", "A PR title is required")
        head = self._current_branch()
        if not head:
            return _error("GIT_PR_FAILED", "Could not resolve the current branch")
        result = create_pull_request(
            self.repo_root,
            title=title,
            body=str(arguments.get("body") or ""),
            head=head,
            base=str(arguments.get("base") or "main"),
        )
        if not result.get("ok"):
            return _error("GIT_PR_FAILED", str(result.get("error") or "PR failed"))
        return Observation(
            "open_pr",
            True,
            {"url": result.get("url", ""), "number": result.get("number")},
            message=f"Opened PR {result.get('url', '')}",
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
        if self.allow_git_ops:
            allowed.update(GIT_OPS_TOOLS)
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
        if name == "git_status":
            return self.aci.git_status().to_dict()
        if name == "apply_patch":
            return self._apply_patch(arguments)
        if name == "write_file":
            return self._write_file(arguments)
        if name == "git_create_branch":
            return self._git_create_branch(arguments)
        if name == "git_commit":
            return self._git_commit(arguments)
        if name == "git_push":
            return self._git_push(arguments)
        if name == "open_pr":
            return self._open_pr(arguments)
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
