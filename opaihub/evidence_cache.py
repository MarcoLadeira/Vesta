"""Stable evidence caching for the OPai router (power-efficiency roadmap Phase 2).

The evidence collector re-scans the whole repository (a recursive language walk
plus several git calls) on every ``opai route``. The raw ``cache_key`` baked
into the evidence payload includes a timestamp, so it never matches and nothing
is ever reused.

This module adds a *stable* fingerprint (git HEAD + working-tree state, or
marker mtimes for non-git projects) so identical repo states reuse a cached
evidence pack instead of re-scanning. Cache reads are side-effect free (they
never create files), so ``opai route`` stays read-only by default (#12); only an
explicit ``write=True`` persists a pack.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any

from .evidence import MARKERS, collect_evidence
from .state import state_dir


EVIDENCE_CACHE_VERSION = 2
DEFAULT_TTL_SECONDS = 3600


def _git_output(root: Path, args: list[str]) -> str | None:
    git = shutil.which("git")
    if not git:
        return None
    try:
        completed = subprocess.run(  # nosec B603
            [git, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def repo_fingerprint(project_root: Path) -> str:
    """Cheap, stable fingerprint of the current repo state (no full repo walk)."""
    root = project_root.expanduser().resolve()
    # `git status --porcelain` succeeds (returncode 0) for any git work tree,
    # including repos with no commits, and lists tracked + untracked changes.
    porcelain = _git_output(root, ["status", "--porcelain"])
    if porcelain is not None:
        head = (_git_output(root, ["rev-parse", "HEAD"]) or "").strip()
        # OPai's own state dirs must not invalidate the cache (writing the cache
        # would otherwise change the fingerprint on the next call).
        lines = [
            line
            for line in porcelain.splitlines()
            if ".opaihub" not in line and ".opcoding" not in line
        ]
        basis = "git\n" + head + "\n" + "\n".join(lines)
    else:
        # Non-git: fingerprint the present project markers and their mtimes.
        parts = []
        for marker in MARKERS:
            path = root / marker
            if path.exists():
                try:
                    parts.append(f"{marker}:{int(path.stat().st_mtime)}")
                except OSError:
                    parts.append(f"{marker}:?")
        basis = "nogit\n" + "\n".join(sorted(parts))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def evidence_cache_key(project_root: Path, task: str) -> str:
    root = project_root.expanduser().resolve()
    fingerprint = repo_fingerprint(root)
    basis = f"v{EVIDENCE_CACHE_VERSION}\n{root}\n{task}\n{fingerprint}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def cache_path(project_root: Path, key: str) -> Path:
    return state_dir(project_root) / "cache" / "evidence" / f"{key}.json"


def collect_evidence_cached(
    project_root: Path,
    task: str = "",
    *,
    write: bool = False,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (evidence, meta), reusing a cached pack when the repo is unchanged.

    ``meta`` carries ``cache_hit``, ``cache_key``, and ``age_seconds``. Reads
    never write; pass ``write=True`` to persist a freshly collected pack.
    """
    root = project_root.expanduser().resolve()
    key = evidence_cache_key(root, task)
    path = cache_path(root, key)

    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            cached_at = float(stored.get("cached_at", 0))
            age = time.time() - cached_at
            if age <= ttl_seconds and isinstance(stored.get("evidence"), dict):
                return stored["evidence"], {
                    "cache_hit": True,
                    "cache_key": key,
                    "age_seconds": int(age),
                }
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    evidence = collect_evidence(root, task)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"cached_at": time.time(), "key": key, "evidence": evidence},
                sort_keys=True,
                default=str,
            ),
            encoding="utf-8",
        )
    return evidence, {"cache_hit": False, "cache_key": key, "age_seconds": 0}
