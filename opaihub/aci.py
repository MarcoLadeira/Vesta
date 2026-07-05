"""Structured agent-computer interface for bounded repository operations."""

from __future__ import annotations

import subprocess  # nosec B404 - argv-only injected process boundary
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .command_runner import redact
from .proc import no_window_kwargs
from .safety_gates import is_destructive_command


@dataclass(frozen=True)
class Observation:
    kind: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    message: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentComputerInterface:
    """Repository-scoped operations returning observations, never prose blobs."""

    def __init__(
        self,
        repo_root: Path,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout: float = 120.0,
        max_output_chars: int = 120_000,
    ) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self._run = run
        self.timeout = timeout
        self.max_output_chars = max_output_chars

    def _path(self, relative: str | Path) -> Path | None:
        candidate = (self.repo_root / relative).resolve()
        try:
            candidate.relative_to(self.repo_root)
        except ValueError:
            return None
        return candidate

    def repo_overview(self) -> Observation:
        files = self.find_files(limit=200)
        status = self.git_status()
        return Observation(
            "repo_overview",
            files.ok and status.ok,
            {
                "root": str(self.repo_root),
                "files": files.data.get("files", []),
                "status": status.data,
            },
            status.error_code if not status.ok else files.error_code,
        )

    def find_files(self, pattern: str = "*", *, limit: int = 500) -> Observation:
        started = time.monotonic()
        files = []
        for path in self.repo_root.rglob(pattern):
            if not path.is_file() or any(
                part
                in {".git", "node_modules", "build", "dist", ".opaihub", ".opcoding"}
                for part in path.parts
            ):
                continue
            files.append(path.relative_to(self.repo_root).as_posix())
            if len(files) >= limit:
                break
        return Observation(
            "file_list",
            True,
            {"pattern": pattern, "files": files, "truncated": len(files) >= limit},
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def search(
        self, query: str, *, paths: Iterable[str] = (), limit: int = 200
    ) -> Observation:
        command = [
            "rg",
            "--line-number",
            "--no-heading",
            "--color",
            "never",
            "--max-count",
            str(limit),
            "--",
            query,
        ]
        command.extend(str(path) for path in paths)
        result = self.run_command(command, purpose="repository search")
        return Observation(
            "search",
            result.ok,
            {
                "query": query,
                "matches": result.data.get("stdout", "").splitlines(),
                "command": result.data.get("command", []),
            },
            result.error_code,
            result.message,
            result.duration_ms,
        )

    def read_slice(
        self,
        path: str | Path,
        *,
        start_line: int = 1,
        end_line: int | None = None,
        max_chars: int = 40_000,
    ) -> Observation:
        started = time.monotonic()
        resolved = self._path(path)
        if resolved is None:
            return Observation(
                "file_slice",
                False,
                error_code="PATH_OUTSIDE_REPO",
                message="Path is outside the active repository",
            )
        if not resolved.is_file():
            return Observation(
                "file_slice",
                False,
                error_code="FILE_NOT_FOUND",
                message="File does not exist",
            )
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        first = max(1, int(start_line))
        last = (
            len(lines)
            if end_line is None
            else min(len(lines), max(first, int(end_line)))
        )
        text = "\n".join(lines[first - 1 : last])
        truncated = len(text) > max_chars
        return Observation(
            "file_slice",
            True,
            {
                "path": resolved.relative_to(self.repo_root).as_posix(),
                "start_line": first,
                "end_line": last,
                "text": text[:max_chars],
                "truncated": truncated,
                "total_lines": len(lines),
            },
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def propose_patch(self, patch: str) -> Observation:
        return Observation(
            "patch_proposal",
            bool(patch.strip()),
            {"patch": redact(patch[: self.max_output_chars]), "chars": len(patch)},
            "EMPTY_PATCH" if not patch.strip() else "",
        )

    def apply_patch(self, patch: str, *, check_only: bool = False) -> Observation:
        command = [
            "git",
            "apply",
            "--check" if check_only else "--whitespace=nowarn",
            "-",
        ]
        return self.run_command(
            command,
            purpose="validate patch" if check_only else "apply patch",
            input_text=patch,
        )

    def git_status(self) -> Observation:
        result = self.run_command(
            ["git", "status", "--porcelain=v1", "--branch"], purpose="git status"
        )
        return Observation(
            "git_status",
            result.ok,
            {"lines": result.data.get("stdout", "").splitlines()},
            result.error_code,
            result.message,
            result.duration_ms,
        )

    def diff(self, *, staged: bool = False) -> Observation:
        command = ["git", "diff"] + (["--cached"] if staged else []) + ["--no-ext-diff"]
        result = self.run_command(command, purpose="review diff")
        return Observation(
            "diff",
            result.ok,
            {"text": result.data.get("stdout", ""), "staged": staged},
            result.error_code,
            result.message,
            result.duration_ms,
        )

    def run_tests(self, command: Iterable[str], *, scope: str) -> Observation:
        result = self.run_command(command, purpose=f"{scope} tests")
        return Observation(
            "test_run",
            result.ok,
            {**result.data, "scope": scope},
            result.error_code,
            result.message,
            result.duration_ms,
        )

    def run_command(
        self,
        command: Iterable[str],
        *,
        purpose: str,
        input_text: str | None = None,
    ) -> Observation:
        argv = [str(item) for item in command]
        if not argv:
            return Observation(
                "command", False, error_code="EMPTY_COMMAND", message="Command is empty"
            )
        if is_destructive_command(argv):
            return Observation(
                "command",
                False,
                {"command": [redact(item) for item in argv], "purpose": purpose},
                "DESTRUCTIVE_COMMAND",
                "Destructive commands require a separate explicit approval boundary",
            )
        started = time.monotonic()
        kwargs: dict[str, Any] = {
            "cwd": str(self.repo_root),
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": self.timeout,
            "check": False,
            **no_window_kwargs(),
        }
        if input_text is not None:
            kwargs["input"] = input_text
        try:
            completed = self._run(argv, **kwargs)
        except subprocess.TimeoutExpired:
            return Observation(
                "command",
                False,
                {"command": argv, "purpose": purpose},
                "TIMEOUT",
                "Command timed out",
            )
        except OSError as exc:
            return Observation(
                "command",
                False,
                {"command": argv, "purpose": purpose},
                "SPAWN_FAILED",
                redact(str(exc)),
            )
        stdout = redact(str(completed.stdout or ""))[: self.max_output_chars]
        stderr = redact(str(completed.stderr or ""))[: self.max_output_chars]
        return Observation(
            "command",
            completed.returncode == 0,
            {
                "command": [redact(item) for item in argv],
                "purpose": purpose,
                "returncode": int(completed.returncode),
                "stdout": stdout,
                "stderr": stderr,
                "truncated": len(str(completed.stdout or ""))
                + len(str(completed.stderr or ""))
                > self.max_output_chars,
            },
            "COMMAND_FAILED" if completed.returncode else "",
            stderr if completed.returncode else "",
            int((time.monotonic() - started) * 1000),
        )
