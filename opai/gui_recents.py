"""Recent chat prompts — the sidebar 'chat history'.

Qt-free and unit-tested. Persists the last handful of prompts the user sent so
the web UI can show a clickable history (click to reload a prompt into the
composer). Stored next to the workspace recents, under the user's home.
"""

from __future__ import annotations

import json
from pathlib import Path

MAX_RECENTS = 12


def recents_path() -> Path:
    return Path.home() / ".opai" / "gui_recents.json"


def load_recents() -> list[str]:
    try:
        data = json.loads(recents_path().read_text(encoding="utf-8"))
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


def add_recent(text: str) -> list[str]:
    """Record ``text`` as the most-recent prompt; returns the updated list."""
    clean = str(text or "").strip().replace("\n", " ")
    if not clean:
        return load_recents()
    current = [p for p in load_recents() if p != clean]
    current.insert(0, clean)
    current = current[:MAX_RECENTS]
    try:
        target = recents_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return current
