from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import (
    load_json,
    now_iso,
    project_op_dir,
    sha256_text,
    write_json,
    write_text,
)


def prompt_cache_dir(root: Path) -> Path:
    path = project_op_dir(root) / "cache" / "prompts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def prompt_key(task: str, context_hash: str = "", route: str = "") -> str:
    return sha256_text(f"{route}\n{context_hash}\n{task}")[:24]


def get_cached_bundle(root: Path, key: str) -> dict[str, Any] | None:
    path = prompt_cache_dir(root) / f"{key}.json"
    data = load_json(path, {})
    return data or None


def put_prompt_bundle(root: Path, key: str, bundle: dict[str, Any]) -> Path:
    path = prompt_cache_dir(root) / f"{key}.json"
    write_json(path, {**bundle, "cached_at": now_iso()})
    return path


def write_artifact(root: Path, name: str, text: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in name).strip("-")
    path = project_op_dir(root) / "cache" / "artifacts" / safe
    write_text(path, text)
    return path
