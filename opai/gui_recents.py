"""Recent chat prompts — the sidebar 'chat history' (#145).

Privacy contract: chat history is local, per-workspace, redacted, and
clearable. Prompts typed in one workspace never appear in another; entries
pass through the shared secret scrubber before touching disk; and the
legacy *global* ``gui_recents.json`` (verbatim prompts from every
workspace mixed together) is deleted on first use rather than migrated,
because its entries cannot be attributed to a workspace safely.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MAX_RECENTS = 12


def legacy_recents_path() -> Path:
    """The pre-#145 global history file; only ever looked at to delete it."""

    return Path.home() / ".opai" / "gui_recents.json"


def _workspace_key(workspace_root: str | Path) -> str:
    resolved = str(Path(workspace_root).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def recents_path(workspace_root: str | Path) -> Path:
    return Path.home() / ".opai" / "recents" / f"{_workspace_key(workspace_root)}.json"


def _discard_legacy_global_history() -> None:
    try:
        legacy_recents_path().unlink(missing_ok=True)
    except OSError:
        pass


def load_recents(workspace_root: str | Path) -> list[str]:
    _discard_legacy_global_history()
    try:
        data = json.loads(recents_path(workspace_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for entry in data:
        text = str(entry).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out[:MAX_RECENTS]


def add_recent(workspace_root: str | Path, text: str) -> list[str]:
    """Record ``text`` as this workspace's most-recent prompt (redacted)."""

    from opaihub.command_runner import redact

    clean = redact(str(text or "").strip().replace("\n", " "))
    if not clean:
        return load_recents(workspace_root)
    current = [p for p in load_recents(workspace_root) if p != clean]
    current.insert(0, clean)
    current = current[:MAX_RECENTS]
    try:
        target = recents_path(workspace_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return current


def clear_recents(workspace_root: str | Path) -> list[str]:
    """Delete this workspace's history (and any legacy global file)."""

    _discard_legacy_global_history()
    try:
        recents_path(workspace_root).unlink(missing_ok=True)
    except OSError:
        pass
    return []
