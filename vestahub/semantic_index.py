"""Deterministic local hash embeddings for repository-scoped context retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess  # nosec B404 - fixed git argv, no shell
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Iterator

from .command_runner import redact
from .proc import no_window_kwargs
from .state import state_dir

MODEL_ID = "vesta-local-hash-v1"
INDEX_VERSION = 3
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_INDEX_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_TERM_HASHES = 512
DEFAULT_MAX_INVENTORY_BYTES = 16 * 1024 * 1024
_HASH_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_HASH_16_PATTERN = re.compile(r"^[0-9a-f]{16}$")
_STABLE_INDEX_FIELDS = (
    "schema_version",
    "model",
    "dimension",
    "chunk_lines",
    "overlap_lines",
    "max_file_bytes",
    "max_files",
    "max_chunks",
    "max_total_bytes",
    "max_index_bytes",
    "max_term_hashes",
    "max_inventory_bytes",
    "coverage",
    "coverage_reason",
    "truncation",
    "files",
    "chunks",
)
_TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".md",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_IGNORED_DIRS = {
    ".git",
    ".vestahub",
    ".opcoding",
    ".opcoding-tools",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "venv",
    "__pycache__",
}
_SECRET_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
}
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_WINDOWS_FORBIDDEN_CHARS = frozenset('<>:"|?*')
_NORMALIZE = {
    "authentication": "auth",
    "authorization": "auth",
    "authorise": "auth",
    "authorize": "auth",
    "routing": "route",
    "routed": "route",
    "repositories": "repo",
    "repository": "repo",
    "validation": "validate",
    "validator": "validate",
}
_EXACT_QUERY_STOPWORDS = {
    "add",
    "and",
    "build",
    "change",
    "code",
    "create",
    "ensure",
    "feature",
    "fix",
    "for",
    "from",
    "implement",
    "improve",
    "into",
    "its",
    "make",
    "modify",
    "please",
    "refactor",
    "remove",
    "support",
    "that",
    "the",
    "this",
    "through",
    "to",
    "update",
    "with",
}

_DEFINITION_PATTERN = re.compile(
    r"^[ \t]*(?:(?:export|default|public|private|protected|internal|abstract|"
    r"static|async|pub|data|sealed|record)\s+)*"
    r"(?:class|def|function|interface|struct|enum|trait|type)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_QUERY_IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_EXACT_CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
}
_HASH_COMMENT_EXTENSIONS = {
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".sh",
}
_SLASH_COMMENT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
}
_BLOCK_COMMENT_EXTENSIONS = _SLASH_COMMENT_EXTENSIONS | {".css"}
_INDEX_LOCKS: dict[str, threading.RLock] = {}
_INDEX_LOCKS_GUARD = threading.Lock()


def _index_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _INDEX_LOCKS_GUARD:
        return _INDEX_LOCKS.setdefault(key, threading.RLock())


def _try_lock_file(handle: BinaryIO) -> bool:
    """Acquire one byte of an advisory file lock without blocking."""

    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _process_lock(path: Path, *, timeout_seconds: float = 60.0) -> Iterator[None]:
    """Serialize index writers across processes; OS locks release after a crash."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while not (acquired := _try_lock_file(handle)):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for semantic index lock: {path}")
            time.sleep(0.05)
        yield
    finally:
        try:
            if acquired:
                _unlock_file(handle)
        finally:
            handle.close()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        return False
    raw_parts = value.split("/")
    if any(
        not part
        or part in {".", ".."}
        or part.endswith((".", " "))
        or any(ord(character) < 32 for character in part)
        or bool(_WINDOWS_FORBIDDEN_CHARS.intersection(part))
        or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        for part in raw_parts
    ):
        return False
    path = PurePosixPath(value)
    lowered = {part.lower() for part in path.parts}
    name = path.name.lower()
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and not (lowered & _IGNORED_DIRS)
        and name not in _SECRET_NAMES
        and not name.startswith(".env.")
        and path.suffix.lower() in _TEXT_EXTENSIONS
    )


def _stable_index_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stable = {field: payload.get(field) for field in _STABLE_INDEX_FIELDS}
    stable["files"] = {
        relative: {
            "content_hash": str(metadata.get("content_hash") or ""),
            "chunk_count": int(metadata.get("chunk_count") or 0),
        }
        for relative, metadata in sorted((payload.get("files") or {}).items())
    }
    return stable


def _stable_index_hash(payload: dict[str, Any]) -> str:
    return _sha(
        json.dumps(
            _stable_index_payload(payload),
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _tokens(value: str) -> list[str]:
    split_camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", split_camel)
    return [_NORMALIZE.get(word.lower(), word.lower()) for word in words]


def _embedding(value: str, *, dimension: int) -> dict[int, float]:
    counts = Counter(_tokens(value))
    vector: dict[int, float] = {}
    for token, count in counts.items():
        slot = (
            int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:4], "big")
            % dimension
        )
        vector[slot] = vector.get(slot, 0.0) + 1.0 + math.log(count)
    norm = math.sqrt(sum(weight * weight for weight in vector.values()))
    if norm:
        vector = {slot: weight / norm for slot, weight in vector.items()}
    return vector


def _packed(vector: dict[int, float]) -> list[list[float | int]]:
    return [[slot, round(weight, 8)] for slot, weight in sorted(vector.items())]


def _unpacked(vector: Iterable[Iterable[float | int]]) -> dict[int, float]:
    return {int(item[0]): float(item[1]) for item in vector}


def _cosine(left: dict[int, float], right: dict[int, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(weight * right.get(slot, 0.0) for slot, weight in left.items())


def _term_hashes(value: str) -> set[str]:
    return {_sha(token)[:16] for token in _tokens(value)}


def _exact_query_hashes(value: str) -> set[str]:
    """Hash meaningful exact terms while leaving semantic embeddings unchanged."""

    tokens = {token for token in _tokens(value) if token not in _EXACT_QUERY_STOPWORDS}
    for identifier in _QUERY_IDENTIFIER_PATTERN.findall(value):
        tail = identifier[1:]
        if "_" in identifier or (
            any(character.isupper() for character in tail)
            and any(character.islower() for character in tail)
        ):
            tokens.update(_tokens(identifier))
    return {_sha(token)[:16] for token in tokens}


def _code_without_comments(value: str, *, suffix: str = "") -> str:
    """Mask comments and quoted prose while preserving every source offset."""

    extension = suffix.lower()
    all_syntax = not extension
    hash_comments = all_syntax or extension in _HASH_COMMENT_EXTENSIONS
    slash_comments = all_syntax or extension in _SLASH_COMMENT_EXTENSIONS
    block_comments = all_syntax or extension in _BLOCK_COMMENT_EXTENSIONS
    sql_comments = all_syntax or extension == ".sql"
    html_comments = all_syntax or extension == ".html"
    powershell_blocks = all_syntax or extension == ".ps1"
    python_triples = all_syntax or extension == ".py"
    output = list(value)
    length = len(value)

    def mask(start: int, end: int) -> None:
        for position in range(start, min(end, length)):
            if value[position] not in "\r\n":
                output[position] = " "

    def line_end(start: int) -> int:
        newline = value.find("\n", start)
        return length if newline < 0 else newline

    def quoted_end(start: int, quote: str) -> int:
        cursor = start + 1
        while cursor < length:
            if value[cursor] == "\\":
                cursor += 2
                continue
            if value[cursor] == quote:
                return cursor + 1
            if quote != "`" and value[cursor] in "\r\n":
                return -1
            cursor += 1
        return -1

    def matching_brace(opening: int, boundary: int) -> int:
        depth = 1
        cursor = opening + 1
        while cursor < boundary:
            quote = value[cursor]
            if quote in {"'", '"', "`"}:
                end = quoted_end(cursor, quote)
                if end > 0:
                    cursor = end
                    continue
            if value[cursor] == "{":
                depth += 1
            elif value[cursor] == "}":
                depth -= 1
                if depth == 0:
                    return cursor
            cursor += 1
        return -1

    def python_f_string(quote_position: int) -> bool:
        if not (all_syntax or extension == ".py"):
            return False
        cursor = quote_position - 1
        while cursor >= 0 and value[cursor].lower() in {"r", "u", "b", "f"}:
            cursor -= 1
        prefix = value[cursor + 1 : quote_position].lower()
        return (
            "f" in prefix
            and len(prefix) <= 3
            and (cursor < 0 or not (value[cursor].isalnum() or value[cursor] == "_"))
        )

    def restore_interpolations(start: int, end: int, *, javascript: bool) -> None:
        cursor = start
        while cursor < end:
            if javascript:
                marker = value.find("${", cursor, end)
                if marker < 0:
                    return
                escapes = 0
                lookbehind = marker - 1
                while lookbehind >= start and value[lookbehind] == "\\":
                    escapes += 1
                    lookbehind -= 1
                if escapes % 2:
                    cursor = marker + 2
                    continue
                opening = marker + 1
                expression_start = marker + 2
            else:
                marker = value.find("{", cursor, end)
                if marker < 0:
                    return
                if marker + 1 < end and value[marker + 1] == "{":
                    cursor = marker + 2
                    continue
                opening = marker
                expression_start = marker + 1
            closing = matching_brace(opening, end)
            if closing < 0:
                return
            cleaned = _code_without_comments(
                value[expression_start:closing], suffix=suffix
            )
            output[expression_start:closing] = cleaned
            cursor = closing + 1

    position = 0
    while position < length:
        if block_comments and value.startswith("/*", position):
            closing = value.find("*/", position + 2)
            end = length if closing < 0 else closing + 2
            mask(position, end)
            position = end
            continue
        if html_comments and value.startswith("<!--", position):
            closing = value.find("-->", position + 4)
            end = length if closing < 0 else closing + 3
            mask(position, end)
            position = end
            continue
        if powershell_blocks and value.startswith("<#", position):
            closing = value.find("#>", position + 2)
            end = length if closing < 0 else closing + 2
            mask(position, end)
            position = end
            continue
        if slash_comments and value.startswith("//", position):
            end = line_end(position)
            mask(position, end)
            position = end
            continue
        if sql_comments and value.startswith("--", position):
            end = line_end(position)
            mask(position, end)
            position = end
            continue
        if hash_comments and value[position] == "#":
            end = line_end(position)
            mask(position, end)
            position = end
            continue

        triple = value[position : position + 3]
        if python_triples and triple in {'"""', "'''"}:
            closing = value.find(triple, position + 3)
            end = length if closing < 0 else closing + 3
            mask(position, end)
            if python_f_string(position):
                restore_interpolations(
                    position + 3,
                    length if closing < 0 else closing,
                    javascript=False,
                )
            position = end
            continue

        quote = value[position]
        if quote not in {"'", '"', "`"}:
            position += 1
            continue
        closing = quoted_end(position, quote)
        if closing < 0:
            # A Rust lifetime or malformed/incomplete string is safer left as
            # code than masking the rest of the line as a false literal.
            position += 1
            continue
        mask(position, closing)
        if python_f_string(position):
            restore_interpolations(position + 1, closing - 1, javascript=False)
        elif quote == "`" and (
            all_syntax or extension in {".html", ".js", ".jsx", ".sh", ".ts", ".tsx"}
        ):
            restore_interpolations(position + 1, closing - 1, javascript=True)
        position = closing
    return "".join(output)


def _definition_hashes(value: str, *, suffix: str = "") -> set[str]:
    hashes: set[str] = set()
    for match in _DEFINITION_PATTERN.finditer(
        _code_without_comments(value, suffix=suffix)
    ):
        hashes.update(_term_hashes(match.group(1)))
    return hashes


def _relevant_excerpt(
    source: str,
    query: str,
    limit: int,
    *,
    preferred_definition_hashes: Iterable[str] = (),
    suffix: str = "",
) -> tuple[str, int, int, int, int]:
    """Return bounded text with 1-based, end-exclusive code-point provenance."""

    cap = max(0, int(limit))
    if len(source) <= cap:
        lines = source.splitlines()
        return source, 1, max(1, len(lines)), 1, len(lines[-1]) + 1 if lines else 1
    if cap <= 0:
        return "", 1, 1, 1, 1
    searchable = _code_without_comments(source, suffix=suffix)
    lowered = searchable.lower()
    preferred = set(preferred_definition_hashes)
    definition_positions = [
        match.start(1)
        for match in _DEFINITION_PATTERN.finditer(searchable)
        if preferred.intersection(_term_hashes(match.group(1)))
    ]
    if definition_positions:
        centre = definition_positions[0]
    else:
        # Preserve compound identifiers from the raw query. Tokenization splits
        # PaymentGateway into two useful terms, but the exact code occurrence is
        # the strongest place to centre a bounded usage excerpt.
        identifiers = [
            identifier
            for identifier in _QUERY_IDENTIFIER_PATTERN.findall(query)
            if "_" in identifier
            or any(char.isupper() for char in identifier[1:])
            or (identifier[:1].isupper() and any(char.islower() for char in identifier))
        ]
        exact_positions = []
        for identifier in set(identifiers):
            match = re.search(
                rf"\b{re.escape(identifier)}\b", searchable, re.IGNORECASE
            )
            if match:
                exact_positions.append((-len(identifier), match.start()))
        if exact_positions:
            centre = min(exact_positions)[1]
        else:
            centre = -1
        # Prefer a rare/long query component over the earliest generic word.
        # This keeps an exact symbol use in view when a common component appears
        # in an earlier comment or prose block.
        if centre < 0:
            candidates = []
            for token in set(_tokens(query)):
                position = lowered.find(token)
                if position >= 0:
                    candidates.append((lowered.count(token), -len(token), position))
            centre = min(candidates)[2] if candidates else 0
    lines = source.splitlines()
    target = source[:centre].count("\n")
    target = min(max(0, target), len(lines) - 1)
    if len(lines[target]) > cap:
        line_start = sum(len(line) + 1 for line in lines[:target])
        within_line = max(0, centre - line_start)
        start = max(0, min(len(lines[target]) - cap, within_line - cap // 2))
        return (
            lines[target][start : start + cap],
            target + 1,
            target + 1,
            start + 1,
            start + cap + 1,
        )

    first = last = target
    used = len(lines[target])
    while True:
        candidates: list[tuple[int, str]] = []
        if first > 0:
            candidates.append((len(lines[first - 1]) + 1, "left"))
        if last + 1 < len(lines):
            candidates.append((len(lines[last + 1]) + 1, "right"))
        fitting = [candidate for candidate in candidates if used + candidate[0] <= cap]
        if not fitting:
            break
        added, side = min(fitting, key=lambda item: (item[0], item[1] != "left"))
        used += added
        if side == "left":
            first -= 1
        else:
            last += 1
    return (
        "\n".join(lines[first : last + 1]),
        first + 1,
        last + 1,
        1,
        len(lines[last]) + 1,
    )


class LocalSemanticIndex:
    """Build/search a local index that persists vectors and provenance, not code."""

    def __init__(
        self,
        repo_root: Path,
        *,
        dimension: int = 256,
        chunk_lines: int = 60,
        overlap_lines: int = 8,
        max_file_bytes: int = 512_000,
        max_files: int = 10_000,
        max_chunks: int = 50_000,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_index_bytes: int = DEFAULT_MAX_INDEX_BYTES,
        max_term_hashes: int = DEFAULT_MAX_TERM_HASHES,
        max_inventory_bytes: int = DEFAULT_MAX_INVENTORY_BYTES,
    ) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self.dimension = max(32, int(dimension))
        self.chunk_lines = max(5, int(chunk_lines))
        self.overlap_lines = min(max(0, int(overlap_lines)), self.chunk_lines - 1)
        self.max_file_bytes = max(1_024, int(max_file_bytes))
        self.max_files = max(1, int(max_files))
        self.max_chunks = max(1, int(max_chunks))
        self.max_total_bytes = max(1, int(max_total_bytes))
        self.max_index_bytes = max(4_096, int(max_index_bytes))
        self.max_term_hashes = max(1, int(max_term_hashes))
        self.max_inventory_bytes = max(4_096, int(max_inventory_bytes))
        self.path = state_dir(self.repo_root) / "agent" / "semantic-index.json"
        self.building_path = self.path.with_suffix(".building")
        self.lock_path = self.path.with_suffix(".lock")
        self._validate_state_paths()

    def _configuration(self) -> dict[str, int]:
        return {
            "dimension": self.dimension,
            "chunk_lines": self.chunk_lines,
            "overlap_lines": self.overlap_lines,
            "max_file_bytes": self.max_file_bytes,
            "max_files": self.max_files,
            "max_chunks": self.max_chunks,
            "max_total_bytes": self.max_total_bytes,
            "max_index_bytes": self.max_index_bytes,
            "max_term_hashes": self.max_term_hashes,
            "max_inventory_bytes": self.max_inventory_bytes,
        }

    def _validate_state_paths(self) -> None:
        candidates = (
            self.path.parent,
            self.path,
            self.building_path,
            self.lock_path,
            self.path.with_suffix(".tmp"),
        )
        for candidate in candidates:
            if candidate.is_symlink() or (
                hasattr(candidate, "is_junction") and candidate.is_junction()
            ):
                raise OSError(f"unsafe semantic index state path link: {candidate}")
            try:
                candidate.resolve(strict=False).relative_to(self.repo_root)
            except (OSError, ValueError) as exc:
                raise OSError(
                    f"unsafe semantic index state path outside project: {candidate}"
                ) from exc

    def _eligible(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.repo_root).as_posix()
        except ValueError:
            return False
        if not _valid_relative_path(relative):
            return False
        try:
            path.resolve().relative_to(self.repo_root)
        except (OSError, ValueError):
            return False
        try:
            source_stat = path.stat()
            return (
                path.is_file()
                and int(getattr(source_stat, "st_nlink", 1)) <= 1
                and source_stat.st_size <= self.max_file_bytes
            )
        except OSError:
            return False

    @staticmethod
    def _candidate_name_allowed(relative: str) -> bool:
        return _valid_relative_path(relative)

    def _git_inventory(self) -> tuple[list[str], bool] | None:
        kwargs: dict[str, Any] = {
            "cwd": str(self.repo_root),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            **no_window_kwargs(),
        }
        try:
            process = subprocess.Popen(  # nosec B603 B607 - fixed git argv
                [
                    "git",
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ],
                **kwargs,
            )
        except OSError:
            return None

        def close_streams() -> None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

        inventory_limit = self.max_files + 1
        raw_name_limit = min(100_000, max(inventory_limit, self.max_files * 4 + 1_024))
        names: list[str] = []
        pending = bytearray()
        bytes_read = 0
        truncated = False
        reader_error: list[BaseException] = []

        def consume() -> None:
            nonlocal bytes_read, truncated
            try:
                if process.stdout is None:
                    return
                while True:
                    allowance = self.max_inventory_bytes - bytes_read
                    if allowance <= 0:
                        truncated = True
                        process.kill()
                        return
                    chunk = process.stdout.read(min(65_536, allowance + 1))
                    if not chunk:
                        return
                    bytes_read += len(chunk)
                    if bytes_read > self.max_inventory_bytes:
                        chunk = chunk[
                            : len(chunk) - (bytes_read - self.max_inventory_bytes)
                        ]
                        truncated = True
                    pending.extend(chunk)
                    while b"\0" in pending:
                        raw, _, remainder = pending.partition(b"\0")
                        pending[:] = remainder
                        if raw:
                            names.append(raw.decode("utf-8", errors="replace"))
                            if len(names) >= raw_name_limit:
                                truncated = True
                                process.kill()
                                return
                    if truncated:
                        process.kill()
                        return
            except (OSError, ValueError) as exc:
                reader_error.append(exc)
                try:
                    process.kill()
                except OSError:
                    pass

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        reader.join(timeout=20.0)
        if reader.is_alive():
            try:
                process.kill()
            except OSError:
                pass
            reader.join(timeout=2.0)
            try:
                process.wait(timeout=2.0)
            except (OSError, subprocess.SubprocessError):
                pass
            close_streams()
            return None
        try:
            returncode = process.wait(timeout=2.0)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
                process.wait(timeout=2.0)
            except (OSError, subprocess.SubprocessError):
                pass
            close_streams()
            return None
        if reader_error or (returncode != 0 and not truncated):
            close_streams()
            return None
        close_streams()
        return names, truncated

    def _repo_files(self) -> tuple[list[Path], bool]:
        inventory_limit = self.max_files + 1
        git_inventory = self._git_inventory()
        inventory_truncated = False
        files: list[Path] = []
        if git_inventory is not None:
            names, inventory_truncated = git_inventory
            for item in names:
                if not item or not self._candidate_name_allowed(item):
                    continue
                path = self.repo_root / item
                if self._eligible(path):
                    files.append(path)
                    if len(files) >= inventory_limit:
                        inventory_truncated = True
                        break
        else:
            raw_limit = min(100_000, max(inventory_limit, self.max_files * 4 + 1_024))
            inspected = 0
            directories = [self.repo_root]
            while directories and not inventory_truncated:
                directory = directories.pop()
                children: list[Path] = []
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            inspected += 1
                            if inspected > raw_limit:
                                inventory_truncated = True
                                break
                            try:
                                if entry.is_symlink():
                                    continue
                                if entry.is_dir(follow_symlinks=False):
                                    if entry.name.lower() not in _IGNORED_DIRS:
                                        children.append(Path(entry.path))
                                    continue
                                if not entry.is_file(follow_symlinks=False):
                                    continue
                            except OSError:
                                continue
                            path = Path(entry.path)
                            try:
                                relative = path.relative_to(self.repo_root).as_posix()
                            except ValueError:
                                continue
                            if self._candidate_name_allowed(
                                relative
                            ) and self._eligible(path):
                                files.append(path)
                                if len(files) >= inventory_limit:
                                    inventory_truncated = True
                                    break
                except OSError:
                    continue
                directories.extend(
                    sorted(children, key=lambda path: path.as_posix(), reverse=True)
                )
        files.sort(key=lambda path: path.relative_to(self.repo_root).as_posix())
        return files, inventory_truncated

    def _chunks_for(self, relative: str, text: str) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        step = max(1, self.chunk_lines - self.overlap_lines)
        lines = text.splitlines()
        for offset in range(0, max(1, len(lines)), step):
            block = lines[offset : offset + self.chunk_lines]
            if not block:
                break
            source = "\n".join(block)
            if not _tokens(source):
                continue
            suffix = Path(relative).suffix.lower()
            code = (
                _code_without_comments(source, suffix=suffix)
                if suffix in _EXACT_CODE_EXTENSIONS
                else ""
            )
            all_terms = _term_hashes(code)
            all_definitions = _definition_hashes(code, suffix=suffix)
            definitions = sorted(all_definitions)[: self.max_term_hashes]
            terms = (
                definitions
                + [
                    value
                    for value in sorted(all_terms - set(definitions))
                    if value not in definitions
                ][: max(0, self.max_term_hashes - len(definitions))]
            )
            chunks.append(
                {
                    "path": relative,
                    "start_line": offset + 1,
                    "end_line": offset + len(block),
                    "content_hash": _sha(source),
                    "vector": _packed(
                        _embedding(f"{relative} {source}", dimension=self.dimension)
                    ),
                    # Exact symbol/use matching without persisting identifiers or code.
                    "term_hashes": sorted(terms),
                    "definition_hashes": definitions,
                    "term_hashes_omitted": max(0, len(all_terms) - len(terms)),
                }
            )
        return chunks

    @staticmethod
    def _stat(path: Path) -> dict[str, int]:
        source_stat = path.stat()
        if int(getattr(source_stat, "st_nlink", 1)) > 1:
            raise OSError(f"unsafe multi-hardlink source file: {path}")
        return {
            "size": int(source_stat.st_size),
            "mtime_ns": int(source_stat.st_mtime_ns),
        }

    def _valid_payload(self, data: dict[str, Any]) -> bool:
        files = data.get("files")
        chunks = data.get("chunks")
        truncation = data.get("truncation")
        if (
            not isinstance(files, dict)
            or not isinstance(chunks, list)
            or len(chunks) > self.max_chunks
            or not isinstance(truncation, dict)
            or data.get("coverage") not in {"full", "partial"}
            or not isinstance(data.get("coverage_reason"), str)
            or data.get("source_persisted") is not False
        ):
            return False
        integer_truncation = (
            "discovered_files",
            "files_omitted",
            "chunks_omitted",
            "bytes_omitted",
            "term_hashes_omitted",
        )
        if any(
            not isinstance(truncation.get(field), int)
            or isinstance(truncation.get(field), bool)
            or truncation[field] < 0
            for field in integer_truncation
        ) or not isinstance(truncation.get("inventory_truncated"), bool):
            return False

        chunk_counts: Counter[str] = Counter()
        total_source_bytes = 0
        for relative, metadata in files.items():
            if (
                not _valid_relative_path(relative)
                or not isinstance(metadata, dict)
                or not isinstance(metadata.get("size"), int)
                or isinstance(metadata.get("size"), bool)
                or not 0 <= metadata["size"] <= self.max_file_bytes
                or not isinstance(metadata.get("mtime_ns"), int)
                or isinstance(metadata.get("mtime_ns"), bool)
                or metadata["mtime_ns"] < 0
                or not isinstance(metadata.get("chunk_count"), int)
                or isinstance(metadata.get("chunk_count"), bool)
                or metadata["chunk_count"] < 0
                or not _HASH_64_PATTERN.fullmatch(
                    str(metadata.get("content_hash") or "")
                )
            ):
                return False
            total_source_bytes += metadata["size"]
        if len(files) > self.max_files or total_source_bytes > self.max_total_bytes:
            return False

        for chunk in chunks:
            if not isinstance(chunk, dict):
                return False
            relative = chunk.get("path")
            start = chunk.get("start_line")
            end = chunk.get("end_line")
            vector = chunk.get("vector")
            term_hashes = chunk.get("term_hashes")
            definition_hashes = chunk.get("definition_hashes")
            omitted = chunk.get("term_hashes_omitted")
            if (
                relative not in files
                or not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < 1
                or end < start
                or end - start + 1 > self.chunk_lines
                or not _HASH_64_PATTERN.fullmatch(str(chunk.get("content_hash") or ""))
                or not isinstance(vector, list)
                or len(vector) > self.dimension
                or not isinstance(term_hashes, list)
                or len(term_hashes) > self.max_term_hashes
                or not isinstance(definition_hashes, list)
                or len(definition_hashes) > self.max_term_hashes
                or not isinstance(omitted, int)
                or isinstance(omitted, bool)
                or omitted < 0
            ):
                return False
            slots: set[int] = set()
            for item in vector:
                if not isinstance(item, list) or len(item) != 2:
                    return False
                slot, weight = item
                if (
                    not isinstance(slot, int)
                    or isinstance(slot, bool)
                    or not 0 <= slot < self.dimension
                    or slot in slots
                    or not isinstance(weight, (int, float))
                    or isinstance(weight, bool)
                    or not math.isfinite(float(weight))
                    or abs(float(weight)) > 1.000001
                ):
                    return False
                slots.add(slot)
            if (
                term_hashes != sorted(set(term_hashes))
                or definition_hashes != sorted(set(definition_hashes))
                or any(
                    not isinstance(value, str) or not _HASH_16_PATTERN.fullmatch(value)
                    for value in (*term_hashes, *definition_hashes)
                )
                or not set(definition_hashes).issubset(term_hashes)
            ):
                return False
            chunk_counts[str(relative)] += 1

        expected_chunks = sum(
            int(metadata["chunk_count"]) for metadata in files.values()
        )
        if any(
            chunk_counts[relative] > int(metadata["chunk_count"])
            for relative, metadata in files.items()
        ):
            return False
        if data.get("coverage") == "full":
            if (
                expected_chunks != len(chunks)
                or truncation["inventory_truncated"]
                or any(truncation[field] for field in integer_truncation[1:])
            ):
                return False
        elif expected_chunks - len(chunks) > truncation["chunks_omitted"]:
            return False

        index_hash = data.get("index_hash")
        return (
            isinstance(index_hash, str)
            and bool(_HASH_64_PATTERN.fullmatch(index_hash))
            and index_hash == _stable_index_hash(data)
        )

    def _encode_payload(
        self,
        files: dict[str, dict[str, Any]],
        chunks: list[dict[str, Any]],
        truncation: dict[str, int | bool],
        *,
        requested_paths_only: bool,
    ) -> tuple[dict[str, Any], bytes]:
        truncated = bool(
            truncation.get("inventory_truncated")
            or any(
                int(truncation.get(field) or 0)
                for field in (
                    "files_omitted",
                    "chunks_omitted",
                    "bytes_omitted",
                    "term_hashes_omitted",
                )
            )
        )
        coverage = "partial" if requested_paths_only or truncated else "full"
        coverage_reason = (
            "requested_paths_only"
            if requested_paths_only
            else ("index_limits" if coverage == "partial" else "current")
        )
        payload: dict[str, Any] = {
            "schema_version": INDEX_VERSION,
            "model": MODEL_ID,
            **self._configuration(),
            "coverage": coverage,
            "coverage_reason": coverage_reason,
            "truncation": truncation,
            "files": files,
            "chunks": chunks,
            "created_at": _now(),
            "source_persisted": False,
        }
        payload["index_hash"] = _stable_index_hash(payload)
        encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        return payload, encoded

    def _load_with_state(self) -> tuple[dict[str, Any] | None, str]:
        self._validate_state_paths()
        if not self.path.exists():
            return None, "missing"
        try:
            if self.path.stat().st_size > self.max_index_bytes:
                return None, "corrupt"
        except OSError:
            return None, "corrupt"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, "corrupt"
        if not isinstance(data, dict):
            return None, "corrupt"
        if data.get("schema_version") != INDEX_VERSION or data.get("model") != MODEL_ID:
            return None, "legacy"
        if any(
            data.get(field) != value for field, value in self._configuration().items()
        ):
            return None, "incompatible"
        if not self._valid_payload(data):
            return None, "corrupt"
        return data, "partial" if data["coverage"] == "partial" else "ready"

    def status(self) -> dict[str, Any]:
        """Report observable index readiness without mutating repository state."""

        self._validate_state_paths()
        if self.building_path.exists():
            try:
                age = max(0.0, time.time() - self.building_path.stat().st_mtime)
            except OSError:
                age = 0.0
            return {
                "state": "building" if age < 300 else "stale",
                "reason": "refresh_in_progress" if age < 300 else "interrupted_refresh",
            }
        data, state = self._load_with_state()
        if data is None:
            return {"state": state, "reason": f"index_{state}"}
        if data.get("coverage") == "partial":
            truncation = dict(data.get("truncation") or {})
            return {
                "state": "partial",
                "reason": str(data.get("coverage_reason") or "index_limits"),
                "index_hash": str(data.get("index_hash") or ""),
                "files": len(data.get("files") or {}),
                "chunks": len(data.get("chunks") or []),
                "truncated": bool(
                    truncation.get("inventory_truncated")
                    or truncation.get("files_omitted")
                    or truncation.get("chunks_omitted")
                    or truncation.get("bytes_omitted")
                    or truncation.get("term_hashes_omitted")
                ),
                "truncation": truncation,
            }
        try:
            inventory, inventory_truncated = self._repo_files()
            if inventory_truncated:
                return {"state": "stale", "reason": "repository_changed"}
            current = {
                path.relative_to(self.repo_root).as_posix(): self._stat(path)
                for path in inventory
            }
        except OSError:
            return {"state": "stale", "reason": "repository_changed"}
        stored = data.get("files") or {}
        if set(current) != set(stored):
            return {"state": "stale", "reason": "repository_changed"}
        for relative, metadata in current.items():
            saved = stored.get(relative) or {}
            if any(saved.get(key) != metadata[key] for key in ("size", "mtime_ns")):
                return {"state": "stale", "reason": "repository_changed"}
            try:
                source_hash = _sha(
                    (self.repo_root / relative).read_text(
                        encoding="utf-8", errors="strict"
                    )
                )
            except (OSError, UnicodeError):
                return {"state": "stale", "reason": "repository_changed"}
            if saved.get("content_hash") != source_hash:
                return {"state": "stale", "reason": "repository_changed"}
        return {
            "state": "ready",
            "reason": "current",
            "index_hash": str(data.get("index_hash") or ""),
            "files": len(stored),
            "chunks": len(data.get("chunks") or []),
        }

    def build(self, paths: Iterable[str] | None = None) -> dict[str, Any]:
        """Incrementally refresh the local index and atomically replace its cache."""

        self._validate_state_paths()
        selected = (
            None
            if paths is None
            else {str(Path(item).as_posix()).strip("/") for item in paths}
        )
        lock = _index_lock(self.path)
        with lock, _process_lock(self.lock_path):
            previous, previous_state = self._load_with_state()
            previous_files = dict((previous or {}).get("files") or {})
            previous_chunks: dict[str, list[dict[str, Any]]] = {}
            for chunk in (previous or {}).get("chunks") or []:
                if isinstance(chunk, dict) and chunk.get("path"):
                    previous_chunks.setdefault(str(chunk["path"]), []).append(chunk)

            self.building_path.parent.mkdir(parents=True, exist_ok=True)
            self.building_path.write_text(
                json.dumps({"started_at": _now()}) + "\n", encoding="utf-8"
            )
            try:
                inventory, inventory_truncated = self._repo_files()
                discovered_files = len(inventory)
                if selected is None:
                    limited = inventory[: self.max_files]
                else:
                    limited = []
                    for name in sorted(selected)[: self.max_files]:
                        candidate = self.repo_root / name
                        if self._eligible(candidate):
                            limited.append(candidate)
                current_paths = {
                    path.relative_to(self.repo_root).as_posix(): path
                    for path in limited
                }
                target_names = set(current_paths) if selected is None else set(selected)
                files: dict[str, dict[str, Any]] = {}
                chunks: list[dict[str, Any]] = []
                reused_files = 0
                updated_files = 0
                total_bytes = 0
                bytes_omitted = 0
                preservation_files_omitted = 0

                for relative in sorted(current_paths):
                    path = current_paths[relative]
                    if selected is not None and relative not in target_names:
                        if relative in previous_files:
                            files[relative] = dict(previous_files[relative])
                            chunks.extend(previous_chunks.get(relative, ()))
                        continue
                    try:
                        metadata = self._stat(path)
                    except OSError:
                        continue
                    if total_bytes + metadata["size"] > self.max_total_bytes:
                        bytes_omitted += metadata["size"]
                        continue
                    total_bytes += metadata["size"]
                    try:
                        source = path.read_text(encoding="utf-8", errors="strict")
                        if self._stat(path) != metadata:
                            continue
                    except (OSError, UnicodeError):
                        continue
                    if "\0" in source:
                        continue
                    content_hash = _sha(source)
                    saved = previous_files.get(relative) or {}
                    cached_chunks = previous_chunks.get(relative, ())
                    cached_chunk_count = saved.get("chunk_count")
                    if (
                        saved.get("content_hash") == content_hash
                        and isinstance(cached_chunk_count, int)
                        and len(cached_chunks) == cached_chunk_count
                    ):
                        files[relative] = {
                            **metadata,
                            "content_hash": content_hash,
                            "chunk_count": cached_chunk_count,
                        }
                        chunks.extend(cached_chunks)
                        reused_files += 1
                        continue
                    fresh_chunks = self._chunks_for(relative, source)
                    files[relative] = {
                        **metadata,
                        "content_hash": content_hash,
                        "chunk_count": len(fresh_chunks),
                    }
                    chunks.extend(fresh_chunks)
                    updated_files += 1

                if selected is not None:
                    for relative in sorted(set(previous_files) - target_names):
                        if relative not in files:
                            metadata = dict(previous_files[relative])
                            size = int(metadata["size"])
                            if len(files) >= self.max_files:
                                preservation_files_omitted += 1
                                continue
                            if total_bytes + size > self.max_total_bytes:
                                bytes_omitted += size
                                preservation_files_omitted += 1
                                continue
                            files[relative] = metadata
                            chunks.extend(previous_chunks.get(relative, ()))
                            total_bytes += size

                deleted_candidates = set(previous_files) - set(current_paths)
                deleted_files = len(
                    deleted_candidates
                    if selected is None
                    else deleted_candidates.intersection(target_names)
                )
                chunks = sorted(
                    chunks,
                    key=lambda item: (
                        str(item.get("path") or ""),
                        int(item.get("start_line") or 0),
                    ),
                )
                chunks_before_limit = len(chunks)
                term_hashes_omitted = sum(
                    int(item.get("term_hashes_omitted") or 0) for item in chunks
                )
                chunks = chunks[: self.max_chunks]
                files_omitted = max(
                    1 if inventory_truncated else 0,
                    discovered_files - len(files),
                    max(0, len(selected or ()) - self.max_files),
                    preservation_files_omitted,
                )
                chunks_omitted = max(0, chunks_before_limit - len(chunks))
                truncation: dict[str, int | bool] = {
                    "discovered_files": discovered_files,
                    "files_omitted": files_omitted,
                    "chunks_omitted": chunks_omitted,
                    "bytes_omitted": bytes_omitted,
                    "term_hashes_omitted": term_hashes_omitted,
                    "inventory_truncated": inventory_truncated,
                }
                payload, encoded = self._encode_payload(
                    files,
                    chunks,
                    truncation,
                    requested_paths_only=selected is not None,
                )
                if len(encoded) > self.max_index_bytes:
                    original_chunks = list(chunks)
                    base_omitted = chunks_omitted
                    low = 0
                    high = len(original_chunks)
                    best: tuple[list[dict[str, Any]], dict[str, Any], bytes] | None = (
                        None
                    )
                    while low <= high:
                        count = (low + high) // 2
                        candidate_chunks = original_chunks[:count]
                        candidate_truncation = {
                            **truncation,
                            "chunks_omitted": base_omitted
                            + len(original_chunks)
                            - count,
                        }
                        candidate_payload, candidate_encoded = self._encode_payload(
                            files,
                            candidate_chunks,
                            candidate_truncation,
                            requested_paths_only=selected is not None,
                        )
                        if len(candidate_encoded) <= self.max_index_bytes:
                            best = (
                                candidate_chunks,
                                candidate_payload,
                                candidate_encoded,
                            )
                            low = count + 1
                        else:
                            high = count - 1
                    if best is None:
                        raise ValueError(
                            "semantic index metadata exceeds serialized cache limit"
                        )
                    chunks, payload, encoded = best
                    truncation = dict(payload["truncation"])

                coverage = str(payload["coverage"])
                coverage_reason = str(payload["coverage_reason"])
                index_hash = str(payload["index_hash"])
                temporary = self.path.with_suffix(".tmp")
                temporary.write_bytes(encoded)
                temporary.replace(self.path)
            finally:
                try:
                    self.building_path.unlink(missing_ok=True)
                except OSError:
                    pass

        return {
            "state": "ready" if coverage == "full" else "partial",
            "reason": coverage_reason,
            "previous_state": previous_state,
            "model": MODEL_ID,
            "index_hash": index_hash,
            "files": len(files),
            "chunks": len(chunks),
            "dimension": self.dimension,
            "path": str(self.path),
            "source_persisted": False,
            "truncated": coverage == "partial",
            "truncation": truncation,
            "index_bytes": len(encoded),
            "cache_hit": updated_files == 0 and deleted_files == 0,
            "reused_files": reused_files,
            "updated_files": updated_files,
            "deleted_files": deleted_files,
        }

    def _load(self) -> dict[str, Any] | None:
        return self._load_with_state()[0]

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        min_score: float = 0.01,
        max_chars: int = 12_000,
    ) -> list[dict[str, Any]]:
        data = self._load()
        if not data or not str(query).strip():
            return []
        dimension = int(data.get("dimension") or self.dimension)
        query_vector = _embedding(query, dimension=dimension)
        query_hashes = _exact_query_hashes(query)
        ranked = sorted(
            (
                (
                    _cosine(query_vector, _unpacked(item.get("vector") or []))
                    + min(
                        1.5,
                        0.75
                        * len(
                            query_hashes.intersection(
                                item.get("definition_hashes") or ()
                            )
                        ),
                    )
                    + min(
                        0.6,
                        0.2
                        * len(query_hashes.intersection(item.get("term_hashes") or ())),
                    ),
                    item,
                )
                for item in data.get("chunks") or []
            ),
            key=lambda pair: (
                -pair[0],
                str(pair[1].get("path")),
                int(pair[1].get("start_line") or 0),
            ),
        )
        results: list[dict[str, Any]] = []
        remaining = max(0, int(max_chars))
        for score, item in ranked:
            if (
                score < float(min_score)
                or len(results) >= max(1, int(limit))
                or remaining <= 0
            ):
                break
            path = self.repo_root / str(item.get("path") or "")
            try:
                resolved = path.resolve()
                resolved.relative_to(self.repo_root)
                before = self._stat(resolved)
                source_text = resolved.read_text(encoding="utf-8", errors="strict")
                if self._stat(resolved) != before:
                    continue
                lines = source_text.splitlines()
            except (OSError, UnicodeError, ValueError):
                continue
            start = max(1, int(item.get("start_line") or 1))
            end = min(len(lines), max(start, int(item.get("end_line") or start)))
            source = "\n".join(lines[start - 1 : end])
            if _sha(source) != item.get("content_hash"):
                continue
            definitions = query_hashes.intersection(item.get("definition_hashes") or ())
            terms = query_hashes.intersection(item.get("term_hashes") or ())
            usage_count = len(terms - definitions)
            (
                excerpt,
                excerpt_start,
                excerpt_end,
                excerpt_start_column,
                excerpt_end_column,
            ) = _relevant_excerpt(
                source,
                query,
                remaining,
                preferred_definition_hashes=definitions,
                suffix=resolved.suffix.lower(),
            )
            text = redact(excerpt)[:remaining]
            remaining -= len(text)
            results.append(
                {
                    "path": str(item["path"]),
                    "start_line": start + excerpt_start - 1,
                    "end_line": start + excerpt_end - 1,
                    # Columns are 1-based and end-exclusive.
                    "start_column": excerpt_start_column,
                    "end_column": excerpt_end_column,
                    "score": round(score, 6),
                    "model": MODEL_ID,
                    "content_hash": str(item["content_hash"]),
                    "text": text,
                    "truncated": len(text) < len(source),
                    "index_hash": str(data.get("index_hash") or ""),
                    "match_kind": (
                        "definition"
                        if definitions
                        else ("usage" if usage_count else "semantic")
                    ),
                    "definition_match_count": len(definitions),
                    "usage_match_count": usage_count,
                    "matched_query_term_hashes": sorted(terms),
                }
            )
        return results

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 8,
        min_score: float = 0.01,
        max_chars: int = 12_000,
    ) -> dict[str, Any]:
        """Refresh then search, returning evidence suitable for GUI/CLI parity."""

        refresh = self.build()
        results = self.search(
            query, limit=limit, min_score=min_score, max_chars=max_chars
        )
        query_hashes = _exact_query_hashes(query)
        query_terms = len(query_hashes)
        matched_hashes: set[str] = set()
        for item in results:
            matched_hashes.update(item.get("matched_query_term_hashes") or ())
        matched = len(query_hashes.intersection(matched_hashes))
        quality = {
            "query_terms": query_terms,
            "matched_query_terms": min(query_terms, int(matched)),
            "coverage": (
                round(min(query_terms, int(matched)) / query_terms, 4)
                if query_terms
                else 0.0
            ),
            "definition_hits": sum(
                1 for item in results if item.get("definition_match_count", 0)
            ),
            "usage_hits": sum(
                1 for item in results if item.get("usage_match_count", 0)
            ),
            "top_score": float(results[0]["score"]) if results else 0.0,
            "result_count": len(results),
            "index_complete": refresh.get("state") == "ready",
            "index_truncated": bool(refresh.get("truncated")),
        }
        return {"index": refresh, "results": results, "quality": quality}
