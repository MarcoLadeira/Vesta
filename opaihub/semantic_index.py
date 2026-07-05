"""Deterministic local hash embeddings for repository-scoped context retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess  # nosec B404 - fixed git argv, no shell
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .command_runner import redact
from .proc import no_window_kwargs
from .state import state_dir

MODEL_ID = "opai-local-hash-v1"
INDEX_VERSION = 1
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
    ".opaihub",
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


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        max_files: int = 2_000,
        max_chunks: int = 10_000,
    ) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self.dimension = max(32, int(dimension))
        self.chunk_lines = max(5, int(chunk_lines))
        self.overlap_lines = min(max(0, int(overlap_lines)), self.chunk_lines - 1)
        self.max_file_bytes = max(1_024, int(max_file_bytes))
        self.max_files = max(1, int(max_files))
        self.max_chunks = max(1, int(max_chunks))
        self.path = state_dir(self.repo_root) / "agent" / "semantic-index.json"

    def _eligible(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.repo_root)
        except ValueError:
            return False
        lowered = {part.lower() for part in relative.parts}
        if lowered & _IGNORED_DIRS:
            return False
        name = relative.name.lower()
        if name in _SECRET_NAMES or name.startswith(".env."):
            return False
        if path.suffix.lower() not in _TEXT_EXTENSIONS:
            return False
        try:
            return path.is_file() and path.stat().st_size <= self.max_file_bytes
        except OSError:
            return False

    def _repo_files(self) -> list[Path]:
        kwargs: dict[str, Any] = {
            "cwd": str(self.repo_root),
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 20.0,
            "check": False,
            **no_window_kwargs(),
        }
        result = subprocess.run(  # nosec B603 B607 - fixed git argv
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            **kwargs,
        )
        if result.returncode == 0:
            candidates = [
                self.repo_root / item for item in result.stdout.split("\0") if item
            ]
        else:
            candidates = list(self.repo_root.rglob("*"))
        return sorted(
            (path for path in candidates if self._eligible(path)),
            key=lambda path: path.relative_to(self.repo_root).as_posix(),
        )[: self.max_files]

    def build(self, paths: Iterable[str] | None = None) -> dict[str, Any]:
        selected = (
            None
            if paths is None
            else {str(Path(item).as_posix()).strip("/") for item in paths}
        )
        chunks: list[dict[str, Any]] = []
        indexed_files = 0
        step = max(1, self.chunk_lines - self.overlap_lines)
        for path in self._repo_files():
            relative = path.relative_to(self.repo_root).as_posix()
            if selected is not None and relative not in selected:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeError):
                continue
            if "\0" in text:
                continue
            lines = text.splitlines()
            indexed_files += 1
            for offset in range(0, max(1, len(lines)), step):
                block = lines[offset : offset + self.chunk_lines]
                if not block:
                    break
                source = "\n".join(block)
                if not _tokens(source):
                    continue
                vector = _embedding(f"{relative} {source}", dimension=self.dimension)
                chunks.append(
                    {
                        "path": relative,
                        "start_line": offset + 1,
                        "end_line": offset + len(block),
                        "content_hash": _sha(source),
                        "vector": _packed(vector),
                    }
                )
                if len(chunks) >= self.max_chunks:
                    break
            if len(chunks) >= self.max_chunks:
                break

        stable = {
            "schema_version": INDEX_VERSION,
            "model": MODEL_ID,
            "dimension": self.dimension,
            "chunk_lines": self.chunk_lines,
            "chunks": chunks,
        }
        index_hash = _sha(json.dumps(stable, sort_keys=True, separators=(",", ":")))
        payload = {**stable, "created_at": _now(), "index_hash": index_hash}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)
        return {
            "model": MODEL_ID,
            "index_hash": index_hash,
            "files": indexed_files,
            "chunks": len(chunks),
            "dimension": self.dimension,
            "path": str(self.path),
            "source_persisted": False,
            "truncated": len(chunks) >= self.max_chunks,
        }

    def _load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("schema_version") != INDEX_VERSION or data.get("model") != MODEL_ID:
            return None
        return data

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
        ranked = sorted(
            (
                (_cosine(query_vector, _unpacked(item.get("vector") or [])), item)
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
                lines = resolved.read_text(
                    encoding="utf-8", errors="strict"
                ).splitlines()
            except (OSError, UnicodeError, ValueError):
                continue
            start = max(1, int(item.get("start_line") or 1))
            end = min(len(lines), max(start, int(item.get("end_line") or start)))
            source = "\n".join(lines[start - 1 : end])
            if _sha(source) != item.get("content_hash"):
                continue
            text = redact(source)[:remaining]
            remaining -= len(text)
            results.append(
                {
                    "path": str(item["path"]),
                    "start_line": start,
                    "end_line": end,
                    "score": round(score, 6),
                    "model": MODEL_ID,
                    "content_hash": str(item["content_hash"]),
                    "text": text,
                    "truncated": len(text) < len(source),
                    "index_hash": str(data.get("index_hash") or ""),
                }
            )
        return results
