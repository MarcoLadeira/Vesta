from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .command_runner import redact
from .state import state_dir

DEFAULT_MODE = "safe-auto"
MODES = ["ask", "plan", "safe-auto", "approve-edits", "full-auto"]

DEFAULT_PREFERENCES: dict[str, Any] = {
    "schema_version": 1,
    "default_model": "auto",
    "default_mode": DEFAULT_MODE,
    "auto_tools": True,
    "safe_auto": {
        "allow_commands": [
            "git status",
            "git diff",
            "git log",
            "python -m unittest",
            "python -m pytest",
            "npm test",
            "ruff check",
        ],
        "deny_commands": [
            "rm -rf",
            "Remove-Item -Recurse",
            "git reset --hard",
            "git clean",
            "npm publish",
            "twine upload",
            "curl",
            "Invoke-WebRequest",
        ],
    },
}

_ALLOWED_KEYS = {
    "schema_version",
    "default_model",
    "default_mode",
    "auto_tools",
    "safe_auto",
}


def preference_path(project_root: Path) -> Path:
    return state_dir(project_root.expanduser().resolve()) / "gui" / "preferences.json"


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    clean = dict(DEFAULT_PREFERENCES)
    for key, value in data.items():
        if key not in _ALLOWED_KEYS:
            continue
        if isinstance(value, str):
            value = redact(value)
        clean[key] = value
    if clean.get("default_mode") not in MODES:
        clean["default_mode"] = DEFAULT_MODE
    if not isinstance(clean.get("default_model"), str) or not clean["default_model"]:
        clean["default_model"] = "auto"
    safe = clean.get("safe_auto")
    if not isinstance(safe, dict):
        clean["safe_auto"] = DEFAULT_PREFERENCES["safe_auto"]
    return clean


def load_gui_preferences(project_root: Path) -> dict[str, Any]:
    path = preference_path(project_root)
    if not path.exists():
        return _sanitize({})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _sanitize({})
    return _sanitize(data if isinstance(data, dict) else {})


def save_gui_preferences(project_root: Path, updates: dict[str, Any]) -> dict[str, Any]:
    current = load_gui_preferences(project_root)
    current.update(updates)
    clean = _sanitize(current)
    path = preference_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return clean
