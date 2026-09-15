"""Workspace switching — make the project folder a control, not a fixed label.

The old header showed an uneditable path. This backs a real switcher: a list of
recent project folders the user can jump between, persisted locally. Qt-free
(the file dialog lives in the window); this is the persistence + validation so
it's testable and so a bad/stale path can never crash the picker.
"""

from __future__ import annotations

import json
from pathlib import Path

MAX_RECENTS = 8


def recents_path() -> Path:
    return Path.home() / ".opai" / "gui_workspaces.json"


def is_valid_workspace(path: str | Path) -> bool:
    try:
        p = Path(path).expanduser()
        return p.exists() and p.is_dir()
    except OSError:
        return False


def resolve_gui_workspace(path: str | Path) -> Path:
    """Resolve a selected folder without swallowing a nested Vesta Build app.

    Ordinary folders keep the existing active-repository behaviour: selecting
    somewhere inside a Git worktree opens that worktree.  A scaffolded Vesta
    Build app is intentionally its own workspace, even when it was created
    inside a parent repository, because Build mode and its per-app history live
    at that exact directory.
    """
    selected = Path(path).expanduser().resolve()
    from opaihub.build_loop import load_app_manifest

    if load_app_manifest(selected) is not None:
        return selected
    from opaihub.repo_context import active_repo_context

    return active_repo_context(selected).path


def load_recent_workspaces() -> list[str]:
    try:
        data = json.loads(recents_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    # Keep only still-valid directories, de-duplicated, preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for entry in data:
        s = str(entry)
        if s in seen or not is_valid_workspace(s):
            continue
        seen.add(s)
        out.append(s)
    return out[:MAX_RECENTS]


def add_recent_workspace(path: str | Path) -> list[str]:
    """Record ``path`` as the most-recent workspace; returns the updated list."""
    resolved = str(Path(path).expanduser().resolve())
    if not is_valid_workspace(resolved):
        return load_recent_workspaces()
    current = [p for p in load_recent_workspaces() if p != resolved]
    current.insert(0, resolved)
    current = current[:MAX_RECENTS]
    try:
        target = recents_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return current


def workspace_label(path: str | Path) -> str:
    """Short, friendly label: parent/name so two same-named folders disambiguate."""
    p = Path(path).expanduser()
    parent = p.parent.name
    return f"{parent}/{p.name}" if parent else p.name
