from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from vestahub.command_runner import run_policy_command


IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".opcoding",
    ".opcoding-tools",
    ".opcoding-tool-cache",
    ".opcoding/cache",
    ".opcoding/logs",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    "out",
    "target",
    ".next",
    ".nuxt",
    ".venv",
    "venv",
    "env",
    ".idea",
    ".vscode",
}

IGNORED_FILE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".lockb",
}

SECRET_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{16,})"
    ),
    re.compile(r"(?i)(authorization:\s*bearer\s+)([A-Za-z0-9_\-./+=]{16,})"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
]


@dataclass
class CommandResult:
    command: str
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def combined_output(self) -> str:
        if self.stderr:
            return f"{self.stdout}\n{self.stderr}".strip()
        return self.stdout.strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def project_op_dir(root: Path) -> Path:
    return root / ".opcoding"


def ensure_project_dirs(root: Path) -> Path:
    op_dir = project_op_dir(root)
    for child in [
        op_dir,
        op_dir / "cache",
        op_dir / "logs",
        op_dir / "logs" / "commands",
        op_dir / "memory",
    ]:
        child.mkdir(parents=True, exist_ok=True)
    return op_dir


def resolve_project_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = Path(path or ".").expanduser()
    return raw.resolve()


def find_git_root(path: Path) -> Path | None:
    result = run_command("git rev-parse --show-toplevel", path, timeout=10)
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return None


def find_project_root(path: Path) -> Path:
    git_root = find_git_root(path)
    return git_root or path.resolve()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def should_skip_dir(path: Path, root: Path) -> bool:
    rel = safe_rel(path, root)
    parts = set(path.parts)
    if path.name in IGNORED_DIRS:
        return True
    if rel in IGNORED_DIRS:
        return True
    return any(part in IGNORED_DIRS for part in parts)


def should_skip_file(path: Path, root: Path) -> bool:
    rel = safe_rel(path, root)
    if any(part in IGNORED_DIRS for part in path.parts):
        return True
    if path.suffix.lower() in IGNORED_FILE_SUFFIXES:
        return True
    if rel.startswith(".opcoding/cache/") or rel.startswith(".opcoding/logs/"):
        return True
    return False


def iter_project_files(root: Path, max_files: int = 25000) -> Iterable[Path]:
    count = 0
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        dirnames[:] = [
            d for d in dirnames if not should_skip_dir(current_path / d, root)
        ]
        for filename in filenames:
            path = current_path / filename
            if should_skip_file(path, root):
                continue
            count += 1
            if count > max_files:
                return
            yield path


def is_probably_text(path: Path, sample_size: int = 2048) -> bool:
    try:
        chunk = path.read_bytes()[:sample_size]
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    return True


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:

        def repl(match: re.Match[str]) -> str:
            if match.lastindex and match.lastindex >= 2:
                return match.group(0).replace(
                    match.group(match.lastindex), "[REDACTED]"
                )
            return "[REDACTED_SECRET]"

        redacted = pattern.sub(repl, redacted)
    return redacted


def find_secret_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            hits.append(redact(value[:160]))
    return hits


def run_command(
    command: str | Sequence[str], cwd: Path, timeout: int = 120
) -> CommandResult:
    start = time.perf_counter()
    result = run_policy_command(command, cwd, timeout=timeout)
    return CommandResult(
        command=result.command,
        cwd=result.cwd,
        returncode=result.returncode,
        stdout=redact(result.stdout),
        stderr=redact(result.stderr),
        duration_seconds=time.perf_counter() - start,
        timed_out=result.timed_out,
    )


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, sort_keys=True) + "\n")


def print_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, sort_keys=True) + "\n")


def slugify(text: str, max_len: int = 52) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:max_len].strip("-") or "task"


def read_limited(path: Path, limit: int = 16000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > limit:
        return text[:limit] + "\n[TRUNCATED]\n"
    return text
