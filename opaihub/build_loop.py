"""The Vesta Build customization loop (#276): scaffold → cheap targeted edits.

``opai build "make the heading purple"`` turns a request into a small, cheap
model call and a safe, deterministic file write:

1. **Select context** — only the app files relevant to the request are sent
   (scored by filename mentions and keyword→file-type hints, trimmed to a
   budget). Sending less is the saving: Lovable-style tools resend and
   regenerate whole projects; Vesta sends a slice and gets back a diff-sized
   answer.
2. **Ask through the normal pipeline** — the same ``handle_gui_message`` path
   as chat, so routing, the cost firewall, activity events, and the savings
   receipt all apply. The model gets NO tool access; it must answer with
   complete updated files in fenced ``file:`` blocks.
3. **Apply deterministically** — Vesta parses the blocks and writes them itself:
   paths confined to the app root, protected files refused, text-only
   allowlist, size caps, and a timestamped backup of every overwritten file.

Qt-free and network-free; tests drive it with a fake runner.
"""

from __future__ import annotations

import difflib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

MANIFEST_NAME = ".opai-app.json"
BACKUP_DIR = ".opai-backups"
BUILD_LOG_NAME = ".opai-build-log.jsonl"
DEFAULT_BUDGET_CHARS = 24_000
MAX_FILE_CHARS = 512_000
MAX_BUILD_PROMPT_CHARS = 128_000
_BUILD_LOG_LOCKS: dict[str, threading.RLock] = {}
_BUILD_LOG_LOCKS_GUARD = threading.Lock()

_PRIVATE_PATH_PARTS = {
    BACKUP_DIR,
    ".git",
    ".opaihub",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
_PRIVATE_FILE_NAMES = {
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
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_WINDOWS_FORBIDDEN_CHARS = frozenset('<>:"|?*')

# Only text files an app scaffold plausibly contains may be written.
TEXT_EXTENSIONS = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".svg",
    ".ts",
    ".tsx",
    ".txt",
}

# Read-only context spans real polyglot repositories. Keep the model-writable
# allowlist above deliberately narrower; retrieval must still see definitions
# and callers in languages Vesta Build does not write directly.
CONTEXT_EXTENSIONS = TEXT_EXTENSIONS | {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".kt",
    ".php",
    ".ps1",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}

# Keyword → extension hints for context selection. Deterministic and dumb on
# purpose: filename mentions always win, these only break ties.
_KEYWORD_HINTS: dict[str, tuple[str, ...]] = {
    ".css": (
        "style",
        "styles",
        "css",
        "color",
        "colour",
        "theme",
        "font",
        "layout",
        "spacing",
        "dark",
        "light",
    ),
    ".html": (
        "html",
        "page",
        "markup",
        "heading",
        "title",
        "header",
        "footer",
        "section",
        "meta",
    ),
    ".js": (
        "logic",
        "feature",
        "function",
        "state",
        "click",
        "button",
        "implement",
        "add",
        "remove",
        "save",
        "load",
        "input",
        "form",
        "list",
    ),
}


def load_app_manifest(app_root: Path) -> dict[str, Any] | None:
    """The Vesta Build manifest for a directory, or None if it isn't one."""
    try:
        path = _safe_internal_path(Path(app_root), MANIFEST_NAME)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("files") else None


def save_app_manifest(app_root: Path, manifest: dict[str, Any]) -> None:
    _safe_internal_path(Path(app_root), MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _score_file(rel: str, request_lower: str) -> int:
    path = Path(rel)
    stem = path.stem.lower()
    score = 0
    if rel.lower() in request_lower or (len(stem) > 2 and stem in request_lower):
        score += 100  # the user named the file — always in
    for ext, words in _KEYWORD_HINTS.items():
        if path.suffix.lower() == ext and any(w in request_lower for w in words):
            score += 30
    base = {".js": 20, ".ts": 20, ".py": 20, ".html": 15, ".css": 12, ".md": 2}
    score += base.get(path.suffix.lower(), 5)
    return score


def _has_explicit_symbol_intent(request: str) -> bool:
    """Distinguish identifier-shaped requests from ordinary prose.

    Exact definition/use matches deserve a strong ranking boost when the user
    names an identifier such as ``PaymentGateway`` or ``render()``.  Applying
    that boost to generic prose (for example, "theme colors") lets an
    incidental function name outrank the authoritative file-type signal.
    """

    if re.search(
        r"`[A-Za-z_$][A-Za-z0-9_$]*(?:[.:][A-Za-z_$][A-Za-z0-9_$]*)*`", request
    ):
        return True
    for token in re.findall(
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:[.:][A-Za-z_$][A-Za-z0-9_$]*)*(?:\(\))?", request
    ):
        if "_" in token or "." in token or ":" in token or token.endswith("()"):
            return True
        bare = token.rstrip("()")
        if any(character.isupper() for character in bare[1:]):
            return True
        if len(bare) > 1 and bare.isupper():
            return True
    return False


def _safe_relative_candidate(rel: object) -> tuple[Path | None, str]:
    """Normalize one untrusted app-relative path and reject private components."""

    if not isinstance(rel, (str, Path)):
        return None, "invalid path"
    raw = str(rel).strip().replace("\\", "/")
    if not raw:
        return None, "empty path"
    candidate = Path(raw)
    if candidate.is_absolute() or raw.startswith("~"):
        return None, "absolute paths are not allowed"
    if ".." in candidate.parts:
        return None, "path traversal is not allowed"
    if any(_unsafe_portable_path_part(part) for part in candidate.parts):
        return None, "non-portable or aliased paths are not allowed"
    lowered = tuple(part.lower() for part in candidate.parts)
    if (
        not lowered
        or any(part.startswith(".") for part in lowered)
        or any(part in _PRIVATE_PATH_PARTS for part in lowered)
        or lowered[-1] in _PRIVATE_FILE_NAMES
    ):
        return None, "protected file"
    return candidate, ""


def _unsafe_portable_path_part(part: str) -> bool:
    """Reject Windows aliases/ADS even when running on another platform."""

    if (
        not part
        or part.endswith((".", " "))
        or bool(_WINDOWS_FORBIDDEN_CHARS.intersection(part))
    ):
        return True
    if any(ord(char) < 32 for char in part):
        return True
    device_name = part.rstrip(". ").split(".", 1)[0].upper()
    return device_name in _WINDOWS_RESERVED_NAMES


def _multiply_linked_file(path: Path) -> bool:
    """Hardlinks erase path provenance, so mutable/readable inputs must be unique."""

    try:
        return path.is_file() and int(path.stat().st_nlink) > 1
    except OSError:
        return True


def _contained_path(root: Path, candidate: Path) -> Path | None:
    """Resolve a candidate and prove containment without string-prefix tricks."""

    resolved_root = root.expanduser().resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )
    except OSError:
        return True


def _safe_internal_path(root: Path, relative: str | Path) -> Path:
    """Return a Vesta-owned path only when no component redirects elsewhere."""

    resolved_root = root.expanduser().resolve()
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or any(_unsafe_portable_path_part(part) for part in candidate.parts)
    ):
        raise OSError(f"unsafe internal app path: {relative}")
    current = resolved_root
    for part in candidate.parts:
        current = current / part
        if _is_link_or_junction(current) or _multiply_linked_file(current):
            raise OSError(f"unsafe linked internal app path: {current}")
    try:
        current.resolve(strict=False).relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise OSError(f"unsafe internal app path outside project: {current}") from exc
    return current


def _excerpt_end_position(
    text: str, *, start_line: int, start_column: int
) -> tuple[int, int]:
    """Return a 1-based, end-exclusive source coordinate for an excerpt."""

    lines = text.split("\n")
    if len(lines) == 1:
        return start_line, start_column + len(text)
    return start_line + len(lines) - 1, 1 + len(lines[-1])


def select_context(
    app_root: Path,
    request: str,
    manifest: dict[str, Any],
    *,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> dict[str, Any]:
    """Pick the app files worth sending for this request, within a budget.

    Returns bounded files/slices plus index and retrieval-quality evidence.
    Never selects the manifest, backups, or hidden files. Source stays in the
    prompt only; the persistent index stores hashes/vectors, never source.
    """
    root = Path(app_root).expanduser().resolve()
    request_lower = str(request or "").lower()
    explicit_symbol_intent = _has_explicit_symbol_intent(str(request or ""))
    raw_files = manifest.get("files") or []
    if not isinstance(raw_files, (list, tuple, set)):
        raw_files = []
    manifest_files = sorted(
        {str(item) for item in raw_files if isinstance(item, (str, Path))}
    )
    index_evidence: dict[str, Any] = {
        "index": {"state": "unavailable", "reason": "not_built"},
        "results": [],
        "quality": {
            "query_terms": 0,
            "matched_query_terms": 0,
            "coverage": 0.0,
            "definition_hits": 0,
            "usage_hits": 0,
            "top_score": 0.0,
            "result_count": 0,
        },
    }
    try:
        from .semantic_index import LocalSemanticIndex

        index_evidence = LocalSemanticIndex(root).retrieve(
            request,
            limit=max(8, len(manifest_files)),
            max_chars=max(1, int(budget_chars)),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        index_evidence["index"] = {
            "state": "unavailable",
            "reason": type(exc).__name__,
        }
    hits: dict[str, dict[str, Any]] = {}
    for hit in index_evidence.get("results") or []:
        relative = str(hit.get("path") or "")
        if relative and relative not in hits:
            hits[relative] = hit

    candidates: list[tuple[int, str, str, dict[str, Any] | None]] = []
    chars_total = 0
    for raw_rel in manifest_files:
        candidate, _reason = _safe_relative_candidate(raw_rel)
        if candidate is None or candidate.suffix.lower() not in CONTEXT_EXTENSIONS:
            continue
        target = _contained_path(root, candidate)
        if target is None or not target.is_file() or _multiply_linked_file(target):
            continue
        rel = candidate.as_posix()
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        chars_total += len(content)
        hit = hits.get(rel)
        # Explicit filenames and file-type intent remain authoritative. Indexed
        # relevance is bounded below those signals, while an exact hashed symbol
        # definition/use must still beat an unrelated file-extension default.
        semantic_boost = min(15, int(round(float((hit or {}).get("score") or 0.0) * 8)))
        match_kind = (hit or {}).get("match_kind")
        if explicit_symbol_intent and match_kind == "definition":
            semantic_boost += 80
        elif explicit_symbol_intent and match_kind == "usage":
            semantic_boost += 60
        candidates.append(
            (_score_file(rel, request_lower) + semantic_boost, rel, content, hit)
        )
    candidates.sort(key=lambda item: (-item[0], item[1]))

    selected: list[dict[str, Any]] = []
    used = 0
    budget = max(0, int(budget_chars))
    for score, rel, content, hit in candidates:
        remaining = budget - used
        if remaining <= 0:
            break
        chosen = content
        kind = "file"
        truncated = False
        if len(chosen) > remaining:
            if selected and hit is None:
                continue
            excerpt = str((hit or {}).get("text") or "")
            chosen = (excerpt or content)[:remaining]
            kind = "indexed_slice" if excerpt else "bounded_file"
            truncated = True
        if not chosen:
            continue
        start_line = int((hit or {}).get("start_line") or 1)
        start_column = int((hit or {}).get("start_column") or 1)
        end_line = int((hit or {}).get("end_line") or 0)
        end_column = int((hit or {}).get("end_column") or 0)
        if truncated:
            end_line, end_column = _excerpt_end_position(
                chosen,
                start_line=start_line,
                start_column=start_column,
            )
        selected.append(
            {
                "path": rel,
                "content": chosen,
                "chars": len(chosen),
                "source_chars": len(content),
                "score": score,
                "kind": kind,
                "truncated": truncated,
                "start_line": start_line,
                "end_line": end_line,
                "start_column": start_column,
                "end_column": end_column,
                "match_kind": str((hit or {}).get("match_kind") or "filename"),
            }
        )
        used += len(chosen)
    saved_pct = 0 if chars_total == 0 else round(100 * (1 - used / chars_total))
    return {
        "files": selected,
        "chars_selected": used,
        "chars_total": chars_total,
        "saved_pct": max(0, saved_pct),
        "budget_chars": budget,
        "within_budget": used <= budget,
        # ``budget_chars`` is the privacy/cost envelope for repository source,
        # not the fixed edit contract or the user's own request. The assembled
        # build prompt has a separate pre-pipeline safety cap below.
        "budget_scope": "source_context",
        "context_budget_chars": budget,
        "within_context_budget": used <= budget,
        "index": dict(index_evidence.get("index") or {}),
        "retrieval_quality": dict(index_evidence.get("quality") or {}),
        "retrieval": [
            {
                key: hit.get(key)
                for key in (
                    "path",
                    "start_line",
                    "end_line",
                    "start_column",
                    "end_column",
                    "score",
                    "match_kind",
                    "content_hash",
                )
            }
            for hit in index_evidence.get("results") or []
        ],
    }


def build_edit_prompt(
    request: str, manifest: dict[str, Any], selection: dict[str, Any]
) -> str:
    """A tight edit prompt: app context, selected files, strict output format."""
    parts = [
        f"You are editing the app '{manifest.get('name', 'app')}' — {manifest.get('description', '')}".rstrip(),
        f"User request: {request}",
        "",
        "Current files (only the relevant ones are shown):",
    ]
    for item in selection["files"]:
        if item.get("truncated") or item.get("kind") == "indexed_slice":
            start_line = item.get("start_line", 1)
            end_line = item.get("end_line") or "?"
            start_column = int(item.get("start_column") or 0)
            end_column = int(item.get("end_column") or 0)
            if start_column > 0 and end_column > 0:
                label = (
                    f"context:{item['path']}#L{start_line}C{start_column}-"
                    f"L{end_line}C{end_column}"
                )
            else:
                label = f"context:{item['path']}#L{start_line}-L{end_line}"
        else:
            label = f"file:{item['path']}"
        parts.append(f"\n```{label}\n{item['content']}```")
    if any(
        item.get("truncated") or item.get("kind") == "indexed_slice"
        for item in selection["files"]
    ):
        parts.append(
            "\nEntries labelled context: are bounded indexed excerpts. Use them "
            "to locate relevant code, but never return an excerpt as if it were "
            "a complete replacement file."
        )
    parts.append(
        "\nReturn ONLY the complete updated content of each file you change, "
        "each in its own fenced block that starts with ```file:<path> and ends "
        "with ```. Do not use placeholders or omit unchanged sections inside a "
        "changed file. Do not include any file you did not change. Keep the "
        "change minimal and focused on the request."
    )
    return "\n".join(parts)


_FILE_BLOCK = re.compile(r"```file:[ \t]*([^\n`]+?)[ \t]*\r?\n(.*?)```", re.DOTALL)


def parse_file_blocks(answer: str) -> dict[str, str]:
    """Extract ``file:`` fenced blocks → ``{path: full content}`` (last wins)."""
    edits: dict[str, str] = {}
    for match in _FILE_BLOCK.finditer(str(answer or "")):
        path = match.group(1).strip()
        if path:
            edits[path] = match.group(2)
    return edits


def _safe_target(root: Path, rel: str) -> tuple[Path | None, str]:
    """Resolve a model-proposed path inside the app root, or explain why not."""
    candidate, reason = _safe_relative_candidate(rel)
    if candidate is None:
        return None, reason
    if candidate.suffix.lower() not in TEXT_EXTENSIONS:
        return None, f"file type '{candidate.suffix}' is not allowed"
    resolved = _contained_path(root, candidate)
    if resolved is None:
        return None, "resolves outside the app"
    if _multiply_linked_file(resolved):
        return None, "hardlinked files are not allowed"
    return resolved, ""


def _diffstat(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def apply_edits(
    app_root: Path, edits: dict[str, str], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Write model-proposed files safely; back up anything overwritten.

    Returns ``{applied: [{path, action, added, removed}], rejected:
    [{path, reason}], backup_dir}``. The manifest's file list gains any new
    files so future context selection sees them.
    """
    root = Path(app_root).resolve()
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    backup_root: Path | None = None
    try:
        _safe_internal_path(root, MANIFEST_NAME)
    except OSError:
        return {
            "applied": [],
            "rejected": [
                {"path": str(rel), "reason": "unsafe app metadata path"}
                for rel in edits
            ],
            "backup_dir": None,
        }
    for rel, content in edits.items():
        target, reason = _safe_target(root, rel)
        if target is None:
            rejected.append({"path": rel, "reason": reason})
            continue
        if len(content) > MAX_FILE_CHARS:
            rejected.append({"path": rel, "reason": "file too large"})
            continue
        normalized = str(Path(rel).as_posix())
        if target.exists():
            old = target.read_text(encoding="utf-8")
            if backup_root is None:
                try:
                    backup_root = _safe_internal_path(
                        root, Path(BACKUP_DIR) / time.strftime("%Y%m%d-%H%M%S")
                    )
                except OSError:
                    rejected.append({"path": rel, "reason": "unsafe backup path"})
                    continue
            try:
                backup_path = _safe_internal_path(
                    root, backup_root.relative_to(root) / normalized
                )
            except (OSError, ValueError):
                rejected.append({"path": rel, "reason": "unsafe backup path"})
                continue
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(old, encoding="utf-8")
            action = "updated"
        else:
            old = ""
            action = "created"
        added, removed = _diffstat(old, content)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        applied.append(
            {"path": normalized, "action": action, "added": added, "removed": removed}
        )
        if normalized not in (manifest.get("files") or []):
            manifest.setdefault("files", []).append(normalized)
    if applied:
        manifest["files"] = sorted(set(manifest["files"]))
        save_app_manifest(root, manifest)
    return {
        "applied": applied,
        "rejected": rejected,
        "backup_dir": str(backup_root) if backup_root else None,
    }


def record_build_entry(app_root: Path, entry: dict[str, Any]) -> None:
    """Append one build run to the app's local, append-only build log.

    Lives inside the user's own app (like git history), but never stores the
    raw build request. A stable one-way fingerprint preserves correlation for
    receipts without persisting prompts or secrets.
    """
    path = _safe_internal_path(Path(app_root), BUILD_LOG_NAME)
    safe_entry = _privacy_safe_build_entry(entry)
    with _build_log_lock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe_entry, sort_keys=True) + "\n")


def _build_log_lock(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve(strict=False))
    with _BUILD_LOG_LOCKS_GUARD:
        return _BUILD_LOG_LOCKS.setdefault(key, threading.RLock())


def _privacy_safe_build_entry(entry: dict[str, Any]) -> dict[str, Any]:
    safe = dict(entry)
    raw_request = safe.pop("request", None)
    if raw_request is not None:
        from .ledger import task_fingerprint

        request = str(raw_request)
        safe.setdefault("request_fingerprint", task_fingerprint(request))
        safe.setdefault("request_chars", len(request))
        safe["schema"] = 2
    return safe


def scrub_build_log_requests(app_root: Path) -> int:
    """Remove legacy raw request fields while preserving receipt evidence."""

    root = Path(app_root).expanduser().resolve()
    path = _safe_internal_path(root, BUILD_LOG_NAME)
    with _build_log_lock(path):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return 0
        sanitized: list[dict[str, Any]] = []
        scrubbed = 0
        for line in lines:
            try:
                value = json.loads(line)
            except ValueError:
                # A corrupt line cannot contribute receipt evidence and may
                # contain arbitrary legacy prompt text; clearing history drops it.
                scrubbed += 1
                continue
            if not isinstance(value, dict):
                scrubbed += 1
                continue
            if "request" in value:
                scrubbed += 1
            sanitized.append(_privacy_safe_build_entry(value))
        if not scrubbed:
            return 0
        temporary = _safe_internal_path(
            root, f"{BUILD_LOG_NAME}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                "".join(json.dumps(item, sort_keys=True) + "\n" for item in sanitized),
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return scrubbed


def read_build_log(app_root: Path) -> list[dict[str, Any]]:
    """All build entries, tolerating corrupt lines (never crash a receipt)."""
    entries: list[dict[str, Any]] = []
    try:
        path = _safe_internal_path(Path(app_root), BUILD_LOG_NAME)
        with _build_log_lock(path):
            lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return entries
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            entries.append(_privacy_safe_build_entry(data))
    return entries


def app_receipt(app_root: Path) -> dict[str, Any]:
    """The aggregate, honest cost story of one app (#276).

    Sums the free scaffold boilerplate and every build's context slicing into
    "tokens never sent", and keeps measured vs estimated spend in separate
    buckets (the cost-telemetry discipline: never blend real dollars with
    model math).
    """
    root = Path(app_root).expanduser().resolve()
    manifest = load_app_manifest(root)
    if manifest is None:
        return {
            "ok": False,
            "status": "not_an_app",
            "error": f"{root} has no {MANIFEST_NAME}.",
        }
    entries = read_build_log(root)
    spend_actual = spend_estimated = savings_estimated = 0.0
    context_chars_avoided = 0
    lines_added = lines_removed = 0
    applied_builds = 0
    for entry in entries:
        confidence = str(entry.get("confidence") or "estimated")
        spend = float(entry.get("spend_usd") or 0.0)
        if confidence == "actual":
            spend_actual += spend
        else:
            spend_estimated += spend
        savings_estimated += float(entry.get("savings_usd") or 0.0)
        context_chars_avoided += max(
            0,
            int(entry.get("chars_total") or 0) - int(entry.get("chars_selected") or 0),
        )
        if entry.get("status") == "applied":
            applied_builds += 1
            lines_added += int(entry.get("added") or 0)
            lines_removed += int(entry.get("removed") or 0)
    boilerplate_tokens = int(manifest.get("boilerplate_tokens_avoided") or 0)
    context_tokens_avoided = round(context_chars_avoided / 4)
    return {
        "ok": True,
        "app": manifest.get("name"),
        "kind": manifest.get("kind"),
        "created_at": manifest.get("created_at"),
        "files": len(manifest.get("files") or []),
        "builds": len(entries),
        "applied_builds": applied_builds,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "spend_usd_actual": round(spend_actual, 6),
        "spend_usd_estimated": round(spend_estimated, 6),
        "savings_usd_estimated": round(savings_estimated, 6),
        "boilerplate_tokens_avoided": boilerplate_tokens,
        "context_tokens_avoided": context_tokens_avoided,
        "tokens_never_sent": boilerplate_tokens + context_tokens_avoided,
        "privacy": (
            "This local log stores build metrics and one-way request fingerprints, "
            "never raw build prompts."
        ),
    }


def rollback_edits(
    app_root: Path, outcome: dict[str, Any], manifest: dict[str, Any]
) -> list[str]:
    """Undo one ``apply_edits`` outcome: restore updates from the backup,
    delete created files, and put the manifest back. Returns restored paths."""
    root = Path(app_root).resolve()
    backup_dir = outcome.get("backup_dir")
    backup_relative: Path | None = None
    if backup_dir:
        try:
            candidate = Path(str(backup_dir)).expanduser().resolve()
            relative = candidate.relative_to(root)
            if not relative.parts or relative.parts[0].lower() != BACKUP_DIR:
                raise ValueError("backup is outside the protected backup directory")
            if _safe_internal_path(root, relative).resolve() != candidate:
                raise ValueError("backup path changed during rollback")
            backup_relative = relative
        except (OSError, ValueError):
            backup_relative = None
    restored: list[str] = []
    for item in outcome.get("applied") or []:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "")
        target, _reason = _safe_target(root, rel)
        if target is None:
            continue
        if item["action"] == "created":
            target.unlink(missing_ok=True)
            if rel in (manifest.get("files") or []):
                manifest["files"].remove(rel)
            restored.append(rel)
        elif backup_relative is not None:
            try:
                backup_path = _safe_internal_path(root, backup_relative / rel)
            except OSError:
                continue
            if backup_path.exists():
                target.write_text(
                    backup_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
                restored.append(rel)
    if restored:
        save_app_manifest(root, manifest)
    return restored


def run_build_request(
    app_root: Path,
    request: str,
    *,
    model: str | None = None,
    account_runner: Any = None,
    dry_run: bool = False,
    strict: bool = False,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    on_text: Callable[[str], None] | None = None,
    cancel: Any = None,
    resume_context: dict[str, Any] | None = None,
    allow_cloud: bool = False,
    allow_limit: bool = False,
) -> dict[str, Any]:
    """One turn of the customization loop against a scaffolded app.

    Runs through the same pipeline as chat (routing, cost firewall, receipt),
    then applies the parsed edits deterministically. Every apply is followed by
    structural verification (entrypoint wiring, brace balance, JSON parse —
    the honest "does it still stand" check); with ``strict`` a failed
    verification rolls the whole edit back from the backups. Honest statuses:
    a blockless answer touches nothing (``no_edits``), a rolled-back edit says
    so (``rolled_back``), and an incomplete rollback preserves the remaining
    changed-file evidence (``partial_rollback`` or ``rollback_failed``).
    """
    root = Path(app_root).expanduser().resolve()
    manifest = load_app_manifest(root)
    if manifest is None:
        return {
            "ok": False,
            "status": "not_an_app",
            "error": (
                f"{root} has no {MANIFEST_NAME}. Scaffold with `opai new` first."
            ),
        }
    if not dry_run:
        try:
            _safe_internal_path(root, BUILD_LOG_NAME)
        except OSError:
            return {
                "ok": False,
                "status": "unsafe_app_state",
                "error": (
                    "The app's local build log resolves outside the app. "
                    "Replace the linked log with a regular in-app file and retry."
                ),
            }
    selection = select_context(root, request, manifest, budget_chars=budget_chars)
    prompt = build_edit_prompt(request, manifest, selection)
    context_stats = {
        "files": [item["path"] for item in selection["files"]],
        "chars_selected": selection["chars_selected"],
        "chars_total": selection["chars_total"],
        "saved_pct": selection["saved_pct"],
        "budget_scope": "source_context",
        "context_budget_chars": selection["budget_chars"],
        "within_context_budget": selection["within_budget"],
        "build_prompt_chars": len(prompt),
        "build_prompt_budget_chars": MAX_BUILD_PROMPT_CHARS,
        "within_build_prompt_budget": len(prompt) <= MAX_BUILD_PROMPT_CHARS,
        "build_prompt_overhead_chars": max(
            0, len(prompt) - selection["chars_selected"]
        ),
    }
    if len(prompt) > MAX_BUILD_PROMPT_CHARS:
        return {
            "ok": False,
            "status": "prompt_too_large",
            "error": (
                "The assembled build prompt exceeds the pre-pipeline safety cap. "
                "Narrow the request or lower the source-context selection."
            ),
            "build_prompt_chars": len(prompt),
            "build_prompt_budget_chars": MAX_BUILD_PROMPT_CHARS,
            "context": context_stats,
        }
    if dry_run:
        return {
            "ok": True,
            "status": "dry_run",
            "prompt": prompt,
            "context": context_stats,
        }

    from opai.cli_stream import normalize_model_choice
    from opaihub.gui_pipeline import handle_gui_message

    result = handle_gui_message(
        root,
        prompt,
        model_id=normalize_model_choice(model),
        mode="ask",
        account_runner=account_runner,
        on_event=on_event,
        on_text=on_text,
        cancel=cancel,
        allow_cloud=allow_cloud,
        allow_limit=allow_limit,
        resume_context=resume_context,
        # `opai build` drives the same pipeline as chat, and the
        # canonical record should say which one asked (#818 AC2).
        surface="automation",
        defer_checkpoint_finalization=True,
    )
    status = str(result.get("status") or "error")
    answer = str(result.get("answer") or "")
    receipt = result.get("receipt") or {}

    def _pipeline_completion_state() -> str:
        """Map the shared pipeline verdict to the checkpoint's terminal state."""
        verdict = result.get("completion_verdict")
        verdict_name = (
            str(verdict.get("verdict") or "").lower()
            if isinstance(verdict, dict)
            else ""
        )
        return {
            "completed": "answered",
            "partial": "partial",
            "blocked": "blocked",
            "failed": "failed",
            "cancelled": "cancelled_before_edit",
            "timeout": "timeout",
        }.get(verdict_name, "failed")

    def _continuity(
        *,
        final_status: str | None = None,
        changed_files: tuple[str, ...] = (),
        completion_state: str = "answered",
        diff_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Keep the pipeline's checkpoint authoritative through deterministic edits."""

        checkpoint_id = str(result.get("checkpoint_id") or "")
        checkpoint_payload = (
            dict(result.get("checkpoint") or {})
            if isinstance(result.get("checkpoint"), dict)
            else {}
        )
        workflow_payload = (
            dict(result.get("workflow") or {})
            if isinstance(result.get("workflow"), dict)
            else {}
        )
        if checkpoint_id and final_status is not None:
            from opaihub.checkpoints import finalize_run_checkpoint
            from opaihub.workflow_state import relink_workflow_checkpoint

            checkpoint = finalize_run_checkpoint(
                root,
                checkpoint_id,
                completion_state=completion_state,
                outcome=final_status,
                changed_files=changed_files,
                diff_summary=diff_summary or {},
            )
            workflow = relink_workflow_checkpoint(
                root,
                checkpoint_id=checkpoint_id,
                changed_files=changed_files,
            )
            checkpoint_payload = {
                **checkpoint_payload,
                "id": checkpoint.checkpoint_id,
                "edit_capable": checkpoint.edit_capable,
                "completion_state": checkpoint.completion_state,
                "git_head": checkpoint.git.get("head", ""),
                "changed_files": list(checkpoint.result_changed_files),
                "changed_during_run": list(checkpoint.changed_during_run),
            }
            workflow_payload = workflow.to_dict()
        return {
            "checkpoint_id": checkpoint_id,
            "checkpoint": checkpoint_payload,
            "workflow": workflow_payload,
        }

    def _log_turn(turn_status: str, applied_items: list[dict[str, Any]]) -> None:
        # Every model-invoking turn joins the app's local build log so the
        # per-app receipt can tell the whole cost story (#276).
        from .ledger import task_fingerprint

        request_text = str(request or "")
        record_build_entry(
            root,
            {
                "schema": 2,
                "ts": int(time.time()),
                "request_fingerprint": task_fingerprint(request_text),
                "request_chars": len(request_text),
                "status": turn_status,
                "files_changed": len(applied_items),
                "added": sum(int(i.get("added") or 0) for i in applied_items),
                "removed": sum(int(i.get("removed") or 0) for i in applied_items),
                "context_files": len(context_stats["files"]),
                "chars_selected": context_stats["chars_selected"],
                "chars_total": context_stats["chars_total"],
                "spend_usd": float(receipt.get("estimated_actual_usd") or 0.0),
                "savings_usd": float(receipt.get("estimated_savings_usd") or 0.0),
                "confidence": str(receipt.get("confidence") or "estimated"),
            },
        )

    answered = status in {
        "answered",
        "cache_hit",
        "answered_by_account",
        "answered_locally",
    }
    if not answered:
        # Build is a presentation wrapper around the shared chat pipeline. Keep
        # the pipeline's safe, structured gate metadata intact so the GUI can
        # render the exact reviewed model/limit and the same honest Blocked
        # verdict. Never forward the raw provider payload or tool internals.
        gate_fields = {
            key: result[key]
            for key in (
                "fallbackModelId",
                "fallbackModelLabel",
                "cloudStarted",
                "usage",
                "receipt",
                "completion_verdict",
                "agent_policy",
                "next_actions",
                "warnings",
            )
            if key in result
        }
        model_id = str(result.get("model_id") or "").strip()
        if not model_id and isinstance(result.get("raw_result"), dict):
            model_id = str(result["raw_result"].get("model_id") or "").strip()
        if model_id:
            gate_fields["model_id"] = model_id
        return {
            "ok": False,
            "status": status,
            "answer": answer,
            "error": result.get("error"),
            "context": context_stats,
            **gate_fields,
            **_continuity(
                final_status=status,
                completion_state=_pipeline_completion_state(),
            ),
        }
    edits = parse_file_blocks(answer)
    if not edits:
        _log_turn("no_edits", [])
        return {
            "ok": False,
            "status": "no_edits",
            "answer": answer,
            "context": context_stats,
            "receipt": receipt,
            **_continuity(final_status="no_edits", completion_state="answered"),
        }
    outcome = apply_edits(root, edits, manifest)
    verify: dict[str, Any] | None = None
    status_out = "applied" if outcome["applied"] else "all_edits_rejected"
    if outcome["applied"]:
        from opaihub.app_verify import verify_app

        verify = verify_app(
            root,
            entrypoint=str(manifest.get("entrypoint") or "") or None,
            files=[item["path"] for item in outcome["applied"]],
        )
        if not verify["ok"]:
            if strict:
                restored = rollback_edits(root, outcome, manifest)
                restored_set = {str(path) for path in restored}
                remaining_items = [
                    item
                    for item in outcome["applied"]
                    if str(item.get("path") or "") not in restored_set
                ]
                remaining_paths = tuple(
                    str(item.get("path") or "")
                    for item in remaining_items
                    if item.get("path")
                )
                if not remaining_paths:
                    rollback_status = "rolled_back"
                elif restored_set:
                    rollback_status = "partial_rollback"
                else:
                    rollback_status = "rollback_failed"
                _log_turn(rollback_status, remaining_items)
                rollback_diff = {
                    "files": len(remaining_paths),
                    "additions": sum(
                        int(item.get("added") or 0) for item in remaining_items
                    ),
                    "deletions": sum(
                        int(item.get("removed") or 0) for item in remaining_items
                    ),
                }
                return {
                    "ok": False,
                    "status": rollback_status,
                    "verify": verify,
                    "rolled_back": restored,
                    "rollback_complete": not remaining_paths,
                    "remaining_changed_files": list(remaining_paths),
                    "applied": remaining_items,
                    "rejected": outcome["rejected"],
                    "backup_dir": outcome["backup_dir"],
                    "context": context_stats,
                    "receipt": receipt,
                    **_continuity(
                        final_status=rollback_status,
                        changed_files=remaining_paths,
                        completion_state="failed",
                        diff_summary=rollback_diff,
                    ),
                }
            # Non-strict keeps recoverable edits on disk, but verification is
            # still a failed outcome—not an applied/verified success.
            status_out = "verification_failed"
    _log_turn(status_out, outcome["applied"])
    changed_files = tuple(
        str(item["path"]) for item in outcome["applied"] if item.get("path")
    )
    diff_summary = {
        "files": len(changed_files),
        "additions": sum(int(item.get("added") or 0) for item in outcome["applied"]),
        "deletions": sum(int(item.get("removed") or 0) for item in outcome["applied"]),
    }
    return {
        "ok": bool(outcome["applied"]) and status_out == "applied",
        "status": status_out,
        **outcome,
        "verify": verify,
        "context": context_stats,
        "receipt": receipt,
        "receipt_so_far": app_receipt(root),
        "preview_cmd": manifest.get("preview_cmd") or "python -m http.server 8000",
        **_continuity(
            final_status=status_out,
            changed_files=changed_files,
            completion_state=(
                "answered" if changed_files and status_out == "applied" else "failed"
            ),
            diff_summary=diff_summary,
        ),
    }
