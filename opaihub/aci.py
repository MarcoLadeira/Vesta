"""Structured agent-computer interface for bounded repository operations."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - argv-only injected process boundary
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .command_runner import redact
from .proc import no_window_kwargs
from .process_tree import adopt, isolated_group_kwargs, terminate_tree
from .safety_gates import is_destructive_command

# How often a cancel-aware command call re-checks the cancellation token and
# the overall deadline while a subprocess is running. Small enough that
# cancellation acknowledgement latency (#380) stays sub-second; large enough
# not to spin.
_POLL_INTERVAL_SECONDS = 0.05
# Grace window given to a cancelled or timed-out process to exit on its own
# (the "draining" phase, #380) before the whole tree is force-terminated.
_DEFAULT_DRAIN_SECONDS = 5.0


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
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        on_event: Callable[[dict[str, Any]], Any] | None = None,
        timeout: float = 120.0,
        max_output_chars: int = 120_000,
    ) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self._run = run
        # Separate from ``_run``: a cancel-aware call needs to poll a live
        # process rather than block inside one library call, so it is built
        # on Popen instead. Injectable for the same reason ``_run`` is — no
        # test should have to spawn a real process to exercise this path.
        self._popen = popen
        self._on_event = on_event
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

    def build_semantic_index(self, paths: Iterable[str] | None = None) -> Observation:
        """Build a deterministic local index without persisting source text."""
        from .semantic_index import LocalSemanticIndex

        started = time.monotonic()
        try:
            data = LocalSemanticIndex(self.repo_root).build(paths)
        except (OSError, RuntimeError, ValueError) as exc:
            return Observation(
                "semantic_index",
                False,
                error_code="SEMANTIC_INDEX_FAILED",
                message=redact(str(exc)),
            )
        return Observation(
            "semantic_index",
            True,
            data,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def semantic_search(
        self, query: str, *, limit: int = 8, max_chars: int = 12_000
    ) -> Observation:
        """Return bounded, provenance-rich matches from the local index."""
        from .semantic_index import LocalSemanticIndex, MODEL_ID

        started = time.monotonic()
        try:
            index = LocalSemanticIndex(self.repo_root)
            if index.status().get("state") != "ready":
                index.build()
            matches = index.search(query, limit=limit, max_chars=max_chars)
        except (
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            IndexError,
            KeyError,
            OverflowError,
        ) as exc:
            return Observation(
                "semantic_search",
                False,
                {"query": redact(query), "matches": []},
                "SEMANTIC_SEARCH_FAILED",
                redact(str(exc)),
            )
        return Observation(
            "semantic_search",
            True,
            {"query": redact(query), "model": MODEL_ID, "matches": matches},
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def discover_mcp_tools(
        self,
        runtime: Any,
        server_id: str | None = None,
        *,
        allow_remote: bool = False,
        cancel: Any = None,
    ) -> Observation:
        started = time.monotonic()
        data = runtime.discover(server_id, allow_remote=allow_remote, cancel=cancel)
        return Observation(
            "mcp_tools",
            bool(data.get("ok")),
            data,
            str(data.get("error_code") or ""),
            str(data.get("message") or ""),
            int((time.monotonic() - started) * 1000),
        )

    def invoke_mcp_tool(
        self,
        runtime: Any,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        allow_write: bool = False,
        allow_remote: bool = False,
        cancel: Any = None,
    ) -> Observation:
        started = time.monotonic()
        data = runtime.invoke(
            server_id,
            tool_name,
            arguments,
            allow_write=allow_write,
            allow_remote=allow_remote,
            cancel=cancel,
        )
        return Observation(
            "mcp_tool",
            bool(data.get("ok")),
            data,
            str(data.get("error_code") or ""),
            str(data.get("message") or ""),
            int((time.monotonic() - started) * 1000),
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

    def run_tests(
        self, command: Iterable[str], *, scope: str, cancel: Any = None
    ) -> Observation:
        from opai.activity import emit_event

        safe_scope = " ".join(redact(str(scope or "selected")).split())[:120]
        safe_scope = safe_scope or "selected"
        label = safe_scope[:1].upper() + safe_scope[1:]
        started = emit_event(
            self._on_event,
            "validation",
            "running",
            f"Running {safe_scope} tests",
            metadata={"operation": "run_tests", "scope": safe_scope},
        )
        result = self.run_command(command, purpose=f"{scope} tests", cancel=cancel)
        emit_event(
            self._on_event,
            "validation",
            "success" if result.ok else "error",
            f"{label} tests {'passed' if result.ok else 'failed'}",
            event_id=started["id"],
            duration_ms=result.duration_ms,
            metadata={
                "operation": "run_tests",
                "scope": safe_scope,
                "returncode": result.data.get("returncode"),
            },
        )
        return Observation(
            "test_run",
            result.ok,
            {**result.data, "scope": scope},
            result.error_code,
            result.message,
            result.duration_ms,
        )

    def _observation_from_output(
        self,
        argv: list[str],
        purpose: str,
        returncode: int,
        stdout: str,
        stderr: str,
        *,
        started: float,
    ) -> Observation:
        clean_stdout = redact(str(stdout or ""))[: self.max_output_chars]
        clean_stderr = redact(str(stderr or ""))[: self.max_output_chars]
        return Observation(
            "command",
            returncode == 0,
            {
                "command": [redact(item) for item in argv],
                "purpose": purpose,
                "returncode": int(returncode),
                "stdout": clean_stdout,
                "stderr": clean_stderr,
                "truncated": len(str(stdout or "")) + len(str(stderr or ""))
                > self.max_output_chars,
            },
            "COMMAND_FAILED" if returncode else "",
            clean_stderr if returncode else "",
            int((time.monotonic() - started) * 1000),
        )

    def run_command(
        self,
        command: Iterable[str],
        *,
        purpose: str,
        input_text: str | None = None,
        environment: Mapping[str, str] | None = None,
        cancel: Any = None,
        drain_seconds: float = _DEFAULT_DRAIN_SECONDS,
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
        if cancel is not None and cancel.is_set():
            return Observation(
                "command",
                False,
                {"command": argv, "purpose": purpose},
                "CANCELLED",
                "Command was cancelled before it started",
            )
        child_environment: dict[str, str] | None = None
        if environment:
            child_environment = os.environ.copy()
            child_environment.update(
                {str(name): str(value) for name, value in environment.items()}
            )
        if cancel is None:
            return self._run_blocking(
                argv, purpose, input_text=input_text, environment=child_environment
            )
        return self._run_cancellable(
            argv,
            purpose,
            input_text=input_text,
            environment=child_environment,
            cancel=cancel,
            drain_seconds=drain_seconds,
        )

    def _run_blocking(
        self,
        argv: list[str],
        purpose: str,
        *,
        input_text: str | None,
        environment: dict[str, str] | None,
    ) -> Observation:
        """The original, unconditional path: no cancellation to watch for."""
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
        if environment is not None:
            kwargs["env"] = environment
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
        return self._observation_from_output(
            argv,
            purpose,
            completed.returncode,
            completed.stdout,
            completed.stderr,
            started=started,
        )

    def _run_cancellable(
        self,
        argv: list[str],
        purpose: str,
        *,
        input_text: str | None,
        environment: dict[str, str] | None,
        cancel: Any,
        drain_seconds: float = _DEFAULT_DRAIN_SECONDS,
    ) -> Observation:
        """Poll a real process tree so a cancellation actually stops it.

        The blocking path above cannot do this even in principle: a single
        library call either returns or it does not, with no point at which to
        notice the token flipped. This spawns the child isolated in its own
        process group/job (:func:`isolated_group_kwargs`, :func:`adopt`) and
        polls, so a cancellation observed mid-flight reaches every descendant
        via :func:`terminate_tree` (#108) — not just the direct child, and not
        just refusing the *next* tool call the way a pre-flight check alone
        would (#380's own stated problem: "cancel requested" that leaves
        child processes running is not cancellation).
        """
        started = time.monotonic()
        kwargs: dict[str, Any] = {
            "cwd": str(self.repo_root),
            "stdin": subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            **no_window_kwargs(),
            **isolated_group_kwargs(),
        }
        if environment is not None:
            kwargs["env"] = environment
        try:
            proc = self._popen(argv, **kwargs)
        except OSError as exc:
            return Observation(
                "command",
                False,
                {"command": argv, "purpose": purpose},
                "SPAWN_FAILED",
                redact(str(exc)),
            )
        adopt(proc)

        deadline = started + self.timeout
        pending_input = input_text
        stdout = stderr = ""
        outcome = "completed"
        while True:
            try:
                stdout, stderr = proc.communicate(
                    input=pending_input, timeout=_POLL_INTERVAL_SECONDS
                )
                break
            except subprocess.TimeoutExpired:
                pending_input = None  # already sent; communicate() forbids resending
                if cancel.is_set():
                    outcome = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    outcome = "timed_out"
                    break

        if outcome != "completed":
            drain_deadline = time.monotonic() + drain_seconds
            while proc.poll() is None and time.monotonic() < drain_deadline:
                time.sleep(_POLL_INTERVAL_SECONDS)
            if proc.poll() is None:
                terminate_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=max(2.0, drain_seconds))
            except subprocess.TimeoutExpired:
                pass  # best effort: report whatever was captured before this
            if outcome == "cancelled":
                return Observation(
                    "command",
                    False,
                    {
                        "command": argv,
                        "purpose": purpose,
                        "stdout": redact(str(stdout or ""))[: self.max_output_chars],
                        "stderr": redact(str(stderr or ""))[: self.max_output_chars],
                    },
                    "CANCELLED",
                    "Command was cancelled",
                    int((time.monotonic() - started) * 1000),
                )
            return Observation(
                "command",
                False,
                {
                    "command": argv,
                    "purpose": purpose,
                    "stdout": redact(str(stdout or ""))[: self.max_output_chars],
                    "stderr": redact(str(stderr or ""))[: self.max_output_chars],
                },
                "TIMEOUT",
                "Command timed out",
                int((time.monotonic() - started) * 1000),
            )

        returncode = proc.returncode if proc.returncode is not None else 1
        return self._observation_from_output(
            argv, purpose, returncode, stdout, stderr, started=started
        )
