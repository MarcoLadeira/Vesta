"""Local result cache for OPai answers (efficiency: free repeat work).

When OPai answers a task locally, it caches the answer keyed by the *normalized*
task plus the repo state and model. A near-duplicate task in the same repo state
returns instantly for $0 - a genuinely avoided model call, not an estimate. The
raw task is never stored, only a one-way key; answers stay on disk locally.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_cache import repo_fingerprint
from .state import state_dir


def normalize_task(task: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation for near-dup matching."""
    lowered = (task or "").lower().strip()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return " ".join(lowered.split())


def cache_dir(project_root: Path) -> Path:
    return state_dir(project_root) / "answers"


def cache_key(project_root: Path, task: str, model: str) -> str:
    fingerprint = repo_fingerprint(project_root.expanduser().resolve())
    basis = f"{normalize_task(task)}\n{model}\n{fingerprint}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def lookup(project_root: Path, task: str, model: str) -> dict[str, Any] | None:
    root = project_root.expanduser().resolve()
    path = cache_dir(root) / f"{cache_key(root, task, model)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def store(project_root: Path, task: str, model: str, answer: str) -> Path:
    root = project_root.expanduser().resolve()
    key = cache_key(root, task, model)
    path = cache_dir(root) / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "key": key,
                "model": model,
                "created_at": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat(),
                "answer": answer,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path
