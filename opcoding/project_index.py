from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .utils import (
    is_probably_text,
    iter_project_files,
    now_iso,
    project_op_dir,
    read_limited,
    safe_rel,
    write_json,
)


CODE_EXTS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".cs",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".php",
}

SYMBOL_PATTERNS = [
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][\w]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_][\w]*)", re.M),
    re.compile(r"^\s*def\s+([A-Za-z_][\w]*)", re.M),
    re.compile(r"^\s*class\s+([A-Za-z_][\w]*)", re.M),
    re.compile(r"^\s*func\s+([A-Za-z_][\w]*)", re.M),
    re.compile(
        r"^\s*(?:public|private|internal|protected)?\s*(?:static\s+)?(?:class|interface|record)\s+([A-Za-z_][\w]*)",
        re.M,
    ),
]


def build_index(root: Path, max_files: int = 5000) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in iter_project_files(root, max_files=max_files):
        if path.suffix.lower() not in CODE_EXTS and path.name.lower() not in {
            "package.json",
            "pyproject.toml",
        }:
            continue
        if not is_probably_text(path):
            continue
        text = read_limited(path, 24000)
        symbols: list[str] = []
        for pattern in SYMBOL_PATTERNS:
            symbols.extend(match.group(1) for match in pattern.finditer(text))
        imports = [
            line.strip()[:160]
            for line in text.splitlines()
            if line.strip().startswith(("import ", "from ", "using ", "require("))
        ][:20]
        entries.append(
            {
                "path": safe_rel(path, root),
                "ext": path.suffix.lower(),
                "lines": text.count("\n") + 1,
                "symbols": sorted(set(symbols))[:80],
                "imports": imports,
            }
        )
    index = {
        "generated_at": now_iso(),
        "root": str(root),
        "files": entries,
        "file_count": len(entries),
    }
    write_json(project_op_dir(root) / "cache" / "project-index.json", index)
    return index


def search_index(root: Path, query: str) -> dict[str, Any]:
    index_path = project_op_dir(root) / "cache" / "project-index.json"
    if not index_path.exists():
        build_index(root)
    import json

    index = json.loads(index_path.read_text(encoding="utf-8"))
    q = query.lower()
    matches = []
    for entry in index.get("files", []):
        haystack = " ".join(
            [entry["path"], *entry.get("symbols", []), *entry.get("imports", [])]
        ).lower()
        if q in haystack:
            matches.append(entry)
    return {"query": query, "matches": matches[:50], "match_count": len(matches)}
