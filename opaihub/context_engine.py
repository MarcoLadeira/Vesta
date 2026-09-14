"""10x Context Engine: profile context waste and slim every client (#51).

Context waste is the easiest money Vesta saves. This profiles a repo into ranked
waste sources (generated files, dependency folders, caches, logs, build output,
large binaries), shows a before/after bytes/tokens/cost report, and generates
per-client ignore files (.cursorignore, .claudeignore, .copilotignore,
.clineignore) without clobbering a user's own rules.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_text, interprocess_transaction
from .command_runner import redact
from .cost_model import estimate_tokens_for_chars, load_cost_model, tier_cost
from .state import state_dir

# Whole directories that are almost always context waste.
WASTE_DIRS = {
    "dependency": [
        "node_modules",
        "vendor",
        "bower_components",
        ".venv",
        "venv",
        "env",
        "site-packages",
        ".pnpm-store",
    ],
    "build": [
        "build",
        "dist",
        ".next",
        ".nuxt",
        "out",
        "target",
        "__pycache__",
        ".gradle",
        ".dart_tool",
    ],
    "cache": [
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        ".cache",
        ".turbo",
        ".parcel-cache",
    ],
    "vcs": [".git", ".hg", ".svn"],
    "opai_state": [".opaihub", ".opcoding", ".opcoding-tools"],
}
# Single-file waste by extension.
BINARY_EXTS = {
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
    ".tgz",
    ".7z",
    ".mp4",
    ".mov",
    ".woff",
    ".woff2",
    ".ttf",
    ".so",
    ".dll",
    ".dylib",
    ".bin",
    ".wasm",
    ".exe",
    ".jar",
    ".class",
    ".pyc",
}
LOG_EXTS = {".log"}
LARGE_FILE_BYTES = 256 * 1024

CLIENT_IGNORE_FILES = {
    "cursor": ".cursorignore",
    "claude": ".claudeignore",
    "copilot": ".copilotignore",
    "cline": ".clineignore",
    "opai": ".opaiignore",
}

_MANAGED_START = "# OPai context-slimming rules (managed)"
_MANAGED_END = "# end OPai rules"
# An ignore file belongs to the user; never hold its lock longer than a UI call.
_IGNORE_LOCK_TIMEOUT_SECONDS = 30.0
# Re-merge this many times when an outside editor beats us to the publish.
_PUBLISH_ATTEMPTS = 3
_MANAGED_PATTERNS = [
    ".git/",
    ".opaihub/",
    ".opcoding/",
    ".opcoding-tools/",
    "node_modules/",
    "**/node_modules/",
    ".venv/",
    "venv/",
    "env/",
    "**/.venv/",
    "vendor/",
    "build/",
    "dist/",
    ".next/",
    ".nuxt/",
    "out/",
    "target/",
    "__pycache__/",
    "**/__pycache__/",
    ".ruff_cache/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".cache/",
    "*.egg-info/",
    "coverage/",
    "htmlcov/",
    "*.log",
]


def _classify_dir(name: str) -> str | None:
    for category, names in WASTE_DIRS.items():
        if name in names:
            return category
    return None


def _classify_file(path: Path, size: int) -> str | None:
    suffix = path.suffix.lower()
    if suffix in LOG_EXTS:
        return "log"
    if suffix in BINARY_EXTS:
        return "binary"
    if size >= LARGE_FILE_BYTES:
        return "large_file"
    return None


def profile_context(
    project_root: Path, *, max_files: int = 20000, top_n: int = 15
) -> dict[str, Any]:
    """Rank context-waste sources and estimate the token/cost they cost. Read-only."""
    root = project_root.expanduser().resolve()
    cost_model = load_cost_model(root)

    by_category: dict[str, int] = {}
    sources: list[dict[str, Any]] = []
    total_bytes = 0
    total_files = 0
    waste_bytes = 0
    counted = 0

    def add_source(rel: str, size: int, category: str) -> None:
        nonlocal waste_bytes
        waste_bytes += size
        by_category[category] = by_category.get(category, 0) + size
        sources.append({"path": rel, "bytes": size, "category": category})

    if max_files > 0:
        for current_dir, dir_names, file_names in os.walk(root, followlinks=False):
            if counted >= max_files:
                break
            current = Path(current_dir)
            dir_names.sort()
            file_names.sort()

            # Account known-waste subtrees once, then prune them from the main
            # walk. The previous rglob implementation enumerated every member
            # to build and sort a global list before scanning the same subtree
            # again for its byte total.
            retained_dirs: list[str] = []
            for name in dir_names:
                category = _classify_dir(name)
                if category is None:
                    retained_dirs.append(name)
                    continue
                path = current / name
                size = _dir_size(path)
                total_bytes += size
                relative = path.relative_to(root).as_posix() + "/"
                add_source(relative, size, category)
            dir_names[:] = retained_dirs

            for name in file_names:
                if counted >= max_files:
                    break
                path = current / name
                if not path.is_file():
                    continue
                counted += 1
                total_files += 1
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                total_bytes += size
                category = _classify_file(path, size)
                if category:
                    add_source(path.relative_to(root).as_posix(), size, category)

    sources.sort(key=lambda item: item["bytes"], reverse=True)
    for source in sources:
        source["estimated_tokens"] = estimate_tokens_for_chars(
            source["bytes"], cost_model
        )

    waste_tokens = estimate_tokens_for_chars(waste_bytes, cost_model)
    baseline_tier = str(cost_model.get("baseline_tier", "L3"))
    return {
        "report": "opai-context-profile",
        "project": str(root),
        "total_bytes": total_bytes,
        "total_files": total_files,
        "waste_bytes": waste_bytes,
        "waste_share": round(waste_bytes / total_bytes, 4) if total_bytes else 0.0,
        "by_category": dict(
            sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)
        ),
        "top_sources": sources[:top_n],
        "estimated_tokens_wasted": waste_tokens,
        "estimated_cost_wasted_usd": tier_cost(baseline_tier, waste_tokens, cost_model),
        "before_after": _before_after(total_bytes, waste_bytes, cost_model),
        "notes": [
            "Deterministic scan; no model used. Generate ignores with: opai context ignores",
        ],
    }


def _dir_size(path: Path) -> int:
    total = 0
    for current_dir, _dir_names, file_names in os.walk(path, followlinks=False):
        current = Path(current_dir)
        for name in file_names:
            child = current / name
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
    return total


def _before_after(
    total_bytes: int, waste_bytes: int, cost_model: dict[str, Any]
) -> dict[str, Any]:
    after_bytes = max(0, total_bytes - waste_bytes)
    baseline_tier = str(cost_model.get("baseline_tier", "L3"))
    before_tokens = estimate_tokens_for_chars(total_bytes, cost_model)
    after_tokens = estimate_tokens_for_chars(after_bytes, cost_model)
    return {
        "before": {
            "bytes": total_bytes,
            "estimated_tokens": before_tokens,
            "estimated_cost_usd": tier_cost(baseline_tier, before_tokens, cost_model),
        },
        "after": {
            "bytes": after_bytes,
            "estimated_tokens": after_tokens,
            "estimated_cost_usd": tier_cost(baseline_tier, after_tokens, cost_model),
        },
        "reduction": {
            "bytes": waste_bytes,
            "estimated_tokens": before_tokens - after_tokens,
            "percent": round(waste_bytes / total_bytes * 100, 1)
            if total_bytes
            else 0.0,
        },
    }


def _merge_ignore_text(existing: str) -> str:
    """Append the managed block to the user's rules without dropping any of them."""
    block = "\n".join([_MANAGED_START, *_MANAGED_PATTERNS, _MANAGED_END])
    return (existing.rstrip() + "\n\n" + block + "\n").lstrip()


def _default_file_mode() -> int:
    """Permissions a plain text write would create here (umask applied)."""
    with tempfile.TemporaryDirectory() as probe_dir:
        probe = Path(probe_dir) / "probe"
        probe.write_text("", encoding="utf-8")
        return stat.S_IMODE(probe.stat().st_mode)


def _read_ignore(path: Path) -> tuple[str | None, int | None]:
    """Return the file's text and permission bits, or (None, None) when absent."""
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None, None
    return text, stat.S_IMODE(path.stat().st_mode)


def _ignore_lock_path(root: Path, name: str) -> Path:
    """Lock beside Vesta state, not beside the user's file, so no stray lock is left in the repo."""
    return state_dir(root) / "locks" / f"ignore-{name}"


def _publish_ignore(
    path: Path, expected: str | None, text: str, mode: int | None
) -> bool:
    """Publish atomically, but only while the file still holds what we merged from."""
    current, _ = _read_ignore(path)
    if current != expected:
        return False
    atomic_write_text(
        path, text, mode=mode if mode is not None else _default_file_mode()
    )
    return True


def _apply_client_ignore(root: Path, client: str, name: str) -> dict[str, Any]:
    path = root / name
    with interprocess_transaction(
        _ignore_lock_path(root, name), timeout_seconds=_IGNORE_LOCK_TIMEOUT_SECONDS
    ):
        for _ in range(_PUBLISH_ATTEMPTS):
            existing, mode = _read_ignore(path)
            if existing is not None and _MANAGED_START in existing:
                return {"client": client, "file": name, "status": "already_managed"}
            base = existing or ""
            # Preserve the user's existing rules; append the managed block.
            if not _publish_ignore(path, existing, _merge_ignore_text(base), mode):
                # An editor wrote between our read and our publish: merge again
                # from their content instead of overwriting it.
                continue
            return {
                "client": client,
                "file": name,
                "status": "updated" if base else "created",
                "preserved_user_lines": len(
                    [line for line in base.splitlines() if line.strip()]
                ),
            }
    return {
        "client": client,
        "file": name,
        "status": "conflict",
        "error": "file kept changing while publishing; left it as the editor wrote it",
    }


def generate_client_ignores(
    project_root: Path, clients: list[str] | None = None
) -> dict[str, Any]:
    """Write/refresh per-client ignore files without clobbering user rules (#51)."""
    root = project_root.expanduser().resolve()
    selected = clients or list(CLIENT_IGNORE_FILES.keys())
    results: list[dict[str, Any]] = []
    for client in selected:
        name = CLIENT_IGNORE_FILES.get(client)
        if not name:
            results.append({"client": client, "status": "unknown_client"})
            continue
        try:
            results.append(_apply_client_ignore(root, client, name))
        except (OSError, UnicodeDecodeError) as exc:
            # One unreadable or unwritable file must not abort the other clients,
            # and the file it failed on keeps whatever the user had in it.
            results.append(
                {
                    "client": client,
                    "file": name,
                    "status": "failed",
                    # #622 AC9: never interpolate a caught exception raw — an
                    # OSError can carry a full filesystem path. redact() is the
                    # sanctioned boundary this result crosses on its way to the
                    # GUI/CLI payload.
                    "error": redact(str(exc)),
                }
            )
    return {
        "report": "opai-context-ignores",
        "project": str(root),
        "results": results,
        "notes": ["User-authored rules are preserved; Vesta appends a managed block."],
    }


def render_profile_markdown(profile: dict[str, Any]) -> str:
    ba = profile["before_after"]
    lines = [
        "# Vesta Context Profile",
        "",
        f"- Total: {profile['total_bytes']:,} bytes across {profile['total_files']:,} files",
        f"- Waste: {profile['waste_bytes']:,} bytes ({profile['waste_share'] * 100:.1f}%)",
        f"- Estimated tokens wasted: {profile['estimated_tokens_wasted']:,}",
        "",
        "## Before / After",
        f"- Before: {ba['before']['bytes']:,} bytes (~{ba['before']['estimated_tokens']:,} tokens)",
        f"- After: {ba['after']['bytes']:,} bytes (~{ba['after']['estimated_tokens']:,} tokens)",
        f"- Reduction: {ba['reduction']['percent']}%",
        "",
        "## Top waste sources",
        "",
    ]
    for source in profile["top_sources"]:
        lines.append(
            f"- `{source['path']}` - {source['bytes']:,} bytes ({source['category']})"
        )
    return "\n".join(lines) + "\n"
