from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .command_runner import redact
from .state import state_dir

DEFAULT_MODE = "safe-auto"
MODES = ["ask", "plan", "safe-auto", "approve-edits", "full-auto"]

DEFAULT_PREFERENCES: dict[str, Any] = {
    "schema_version": 2,
    "default_model": "auto",
    "default_mode": DEFAULT_MODE,
    "default_task_mode": "general",
    "default_output_format": "normal",
    # Simple by default: the Inspector is powerful but optional — first-time
    # users get a clean chat; power users toggle it (Ctrl+I / header pill).
    "show_control_panel": False,
    "auto_tools": True,
    "usage_limits": {},
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
    "default_task_mode",
    "default_output_format",
    "show_control_panel",
    "auto_tools",
    "usage_limits",
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
    raw_limits = clean.get("usage_limits")
    clean_limits: dict[str, dict[str, Any]] = {}
    if isinstance(raw_limits, dict):
        for model_id, value in raw_limits.items():
            if not isinstance(model_id, str) or not isinstance(value, dict):
                continue
            metric = str(value.get("metric") or "tokens")
            window = str(value.get("window") or "month")
            limit = value.get("limit")
            if (
                metric in {"tokens", "requests"}
                and window in {"minute", "day", "month"}
                and isinstance(limit, (int, float))
                and limit > 0
            ):
                clean_limits[redact(model_id)] = {
                    "metric": metric,
                    "limit": int(limit),
                    "window": window,
                }
    clean["usage_limits"] = clean_limits
    clean["schema_version"] = 2
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


def save_usage_limit(
    project_root: Path,
    model_id: str,
    *,
    metric: str,
    limit: int,
    window: str,
) -> dict[str, Any]:
    """Persist one validated per-model soft limit without storing prompt data."""

    if metric not in {"tokens", "requests"}:
        raise ValueError("Usage metric must be tokens or requests")
    if window not in {"minute", "day", "month"}:
        raise ValueError("Usage window must be minute, day, or month")
    if int(limit) <= 0:
        raise ValueError("Usage limit must be greater than zero")
    current = load_gui_preferences(project_root)
    limits = dict(current.get("usage_limits") or {})
    limits[str(model_id)] = {
        "metric": metric,
        "limit": int(limit),
        "window": window,
    }
    return save_gui_preferences(project_root, {"usage_limits": limits})
