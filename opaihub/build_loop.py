"""The OPai Build customization loop (#276): scaffold → cheap targeted edits.

``opai build "make the heading purple"`` turns a request into a small, cheap
model call and a safe, deterministic file write:

1. **Select context** — only the app files relevant to the request are sent
   (scored by filename mentions and keyword→file-type hints, trimmed to a
   budget). Sending less is the saving: Lovable-style tools resend and
   regenerate whole projects; OPai sends a slice and gets back a diff-sized
   answer.
2. **Ask through the normal pipeline** — the same ``handle_gui_message`` path
   as chat, so routing, the cost firewall, activity events, and the savings
   receipt all apply. The model gets NO tool access; it must answer with
   complete updated files in fenced ``file:`` blocks.
3. **Apply deterministically** — OPai parses the blocks and writes them itself:
   paths confined to the app root, protected files refused, text-only
   allowlist, size caps, and a timestamped backup of every overwritten file.

Qt-free and network-free; tests drive it with a fake runner.
"""

from __future__ import annotations

import difflib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

MANIFEST_NAME = ".opai-app.json"
BACKUP_DIR = ".opai-backups"
BUILD_LOG_NAME = ".opai-build-log.jsonl"
DEFAULT_BUDGET_CHARS = 24_000
MAX_FILE_CHARS = 512_000

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
    """The OPai Build manifest for a directory, or None if it isn't one."""
    path = Path(app_root) / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("files") else None


def save_app_manifest(app_root: Path, manifest: dict[str, Any]) -> None:
    (Path(app_root) / MANIFEST_NAME).write_text(
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


def select_context(
    app_root: Path,
    request: str,
    manifest: dict[str, Any],
    *,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> dict[str, Any]:
    """Pick the app files worth sending for this request, within a budget.

    Returns ``{files: [{path, content, chars}], chars_selected, chars_total,
    saved_pct}``. Never selects the manifest, backups, or hidden files. At
    least one file is always selected (the top-scoring one).
    """
    root = Path(app_root)
    request_lower = str(request or "").lower()
    candidates: list[tuple[int, str, str]] = []
    chars_total = 0
    for rel in sorted(set(manifest.get("files") or [])):
        if rel == MANIFEST_NAME or rel.startswith((BACKUP_DIR, ".")):
            continue
        try:
            content = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        chars_total += len(content)
        candidates.append((_score_file(rel, request_lower), rel, content))
    candidates.sort(key=lambda item: (-item[0], item[1]))

    selected: list[dict[str, Any]] = []
    used = 0
    for score, rel, content in candidates:
        if selected and used + len(content) > budget_chars:
            continue
        selected.append(
            {"path": rel, "content": content, "chars": len(content), "score": score}
        )
        used += len(content)
    saved_pct = 0 if chars_total == 0 else round(100 * (1 - used / chars_total))
    return {
        "files": selected,
        "chars_selected": used,
        "chars_total": chars_total,
        "saved_pct": max(0, saved_pct),
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
        parts.append(f"\n```file:{item['path']}\n{item['content']}```")
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
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw:
        return None, "empty path"
    candidate = Path(raw)
    if candidate.is_absolute() or raw.startswith("~"):
        return None, "absolute paths are not allowed"
    if ".." in candidate.parts:
        return None, "path traversal is not allowed"
    first = candidate.parts[0] if candidate.parts else ""
    if raw in {MANIFEST_NAME, BUILD_LOG_NAME} or first in {BACKUP_DIR, ".git"}:
        return None, "protected file"
    if candidate.suffix.lower() not in TEXT_EXTENSIONS:
        return None, f"file type '{candidate.suffix}' is not allowed"
    resolved = (root / candidate).resolve()
    if not str(resolved).startswith(str(root.resolve())):
        return None, "resolves outside the app"
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
                backup_root = root / BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S")
            backup_path = backup_root / normalized
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

    Lives inside the user's own app (like git history), so the truncated
    request text is theirs to keep. OPai's global ledger still stores only
    fingerprints — this log never leaves the app directory.
    """
    path = Path(app_root) / BUILD_LOG_NAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def read_build_log(app_root: Path) -> list[dict[str, Any]]:
    """All build entries, tolerating corrupt lines (never crash a receipt)."""
    path = Path(app_root) / BUILD_LOG_NAME
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return entries
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            entries.append(data)
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
        "privacy": "This log lives only inside your app directory.",
    }


def rollback_edits(
    app_root: Path, outcome: dict[str, Any], manifest: dict[str, Any]
) -> list[str]:
    """Undo one ``apply_edits`` outcome: restore updates from the backup,
    delete created files, and put the manifest back. Returns restored paths."""
    root = Path(app_root).resolve()
    backup_dir = outcome.get("backup_dir")
    restored: list[str] = []
    for item in outcome.get("applied") or []:
        rel = item["path"]
        target = root / rel
        if item["action"] == "created":
            target.unlink(missing_ok=True)
            if rel in (manifest.get("files") or []):
                manifest["files"].remove(rel)
            restored.append(rel)
        elif backup_dir:
            backup_path = Path(backup_dir) / rel
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
) -> dict[str, Any]:
    """One turn of the customization loop against a scaffolded app.

    Runs through the same pipeline as chat (routing, cost firewall, receipt),
    then applies the parsed edits deterministically. Every apply is followed by
    structural verification (entrypoint wiring, brace balance, JSON parse —
    the honest "does it still stand" check); with ``strict`` a failed
    verification rolls the whole edit back from the backups. Honest statuses:
    a blockless answer touches nothing (``no_edits``), a rolled-back edit says
    so (``rolled_back``).
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
    selection = select_context(root, request, manifest, budget_chars=budget_chars)
    prompt = build_edit_prompt(request, manifest, selection)
    context_stats = {
        "files": [item["path"] for item in selection["files"]],
        "chars_selected": selection["chars_selected"],
        "chars_total": selection["chars_total"],
        "saved_pct": selection["saved_pct"],
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
        allow_cloud=False,
    )
    status = str(result.get("status") or "error")
    answer = str(result.get("answer") or "")
    receipt = result.get("receipt") or {}

    def _log_turn(turn_status: str, applied_items: list[dict[str, Any]]) -> None:
        # Every model-invoking turn joins the app's local build log so the
        # per-app receipt can tell the whole cost story (#276).
        record_build_entry(
            root,
            {
                "schema": 1,
                "ts": int(time.time()),
                "request": str(request or "")[:200],
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
        return {
            "ok": False,
            "status": status,
            "answer": answer,
            "error": result.get("error"),
            "context": context_stats,
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
        if strict and not verify["ok"]:
            restored = rollback_edits(root, outcome, manifest)
            _log_turn("rolled_back", [])
            return {
                "ok": False,
                "status": "rolled_back",
                "verify": verify,
                "rolled_back": restored,
                "rejected": outcome["rejected"],
                "backup_dir": outcome["backup_dir"],
                "context": context_stats,
                "receipt": receipt,
            }
    _log_turn(status_out, outcome["applied"])
    return {
        "ok": bool(outcome["applied"]),
        "status": status_out,
        **outcome,
        "verify": verify,
        "context": context_stats,
        "receipt": receipt,
        "receipt_so_far": app_receipt(root),
        "preview_cmd": manifest.get("preview_cmd") or "python -m http.server 8000",
    }
