from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .command_runner import redact
from .state import state_dir

DEFAULT_MODE = "safe-auto"
MODES = ["ask", "plan", "safe-auto", "approve-edits", "full-auto"]

DEFAULT_PREFERENCES: dict[str, Any] = {
    "schema_version": 3,
    "default_model": "auto",
    "default_mode": DEFAULT_MODE,
    # Full Auto pin contract (#137): Full Auto is the effective mode only when
    # explicitly pinned with a recorded acknowledgement. A stale persisted
    # full-auto default is reset to Safe Auto on load unless it is pinned.
    "full_auto_pinned": False,
    "full_auto_acknowledged_at": "",
    "default_task_mode": "general",
    "default_output_format": "normal",
    # Simple by default: the Inspector is powerful but optional — first-time
    # users get a clean chat; power users toggle it (Ctrl+I / header pill).
    "show_control_panel": False,
    "auto_tools": True,
    # Appearance (#241): density scales spacing; reduced_motion overrides the
    # OS media query ("system" defers to it, "on" force-disables animations,
    # "off" force-enables them). Applied live by the GUI, no restart.
    "density": "comfortable",
    "reduced_motion": "system",
    "usage_limits": {},
    # Free-model ids the user has already consented to send to. One-time
    # confirmation per free provider is enough; asking on every message is a
    # trust-badgering pattern that trains users to click through popups.
    "free_consent": [],
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
    "full_auto_pinned",
    "full_auto_acknowledged_at",
    "default_task_mode",
    "default_output_format",
    "show_control_panel",
    "auto_tools",
    "density",
    "reduced_motion",
    "usage_limits",
    "free_consent",
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
    if clean.get("density") not in {"comfortable", "compact"}:
        clean["density"] = "comfortable"
    if clean.get("reduced_motion") not in {"system", "on", "off"}:
        clean["reduced_motion"] = "system"
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
    # Free-model consent list: strings only, deduplicated, capped.
    raw_consent = clean.get("free_consent")
    seen: set[str] = set()
    consent: list[str] = []
    if isinstance(raw_consent, list):
        for entry in raw_consent:
            if not isinstance(entry, str):
                continue
            model_id = redact(entry).strip()
            if not model_id or model_id in seen:
                continue
            # Only accept ids from the free-tier namespace so this pref can
            # never quietly grant consent for a paid or arbitrary model id.
            if not model_id.startswith("free:"):
                continue
            seen.add(model_id)
            consent.append(model_id)
    clean["free_consent"] = consent[:32]
    # Full Auto pin contract (#137): the pin is real only with an
    # acknowledgement timestamp; a persisted full-auto default that is not
    # pinned is reset to Safe Auto so a fresh session never reopens with
    # broader authority than the user explicitly kept.
    clean["full_auto_pinned"] = bool(clean.get("full_auto_pinned"))
    ack = clean.get("full_auto_acknowledged_at")
    clean["full_auto_acknowledged_at"] = str(ack) if isinstance(ack, str) else ""
    pinned = clean["full_auto_pinned"] and bool(
        clean["full_auto_acknowledged_at"].strip()
    )
    if not pinned:
        clean["full_auto_pinned"] = False
        clean["full_auto_acknowledged_at"] = ""
        if clean.get("default_mode") == "full-auto":
            clean["default_mode"] = DEFAULT_MODE
    clean["schema_version"] = 3
    return clean


def grant_free_consent(project_root: Path, model_id: str) -> dict[str, Any]:
    """Persist that the user has consented to send to ``model_id`` (free tier).

    Only accepts ``free:`` ids so this cannot be used to grant consent for a
    paid model. Returns the updated preferences.
    """
    if not isinstance(model_id, str) or not model_id.startswith("free:"):
        raise ValueError("Only free:<provider>:<model> ids may be granted consent")
    current = load_gui_preferences(project_root)
    consent = list(current.get("free_consent") or [])
    if model_id not in consent:
        consent.append(model_id)
    return save_gui_preferences(project_root, {"free_consent": consent})


def pin_full_auto(project_root: Path) -> dict[str, Any]:
    """Explicitly pin Full Auto with a recorded acknowledgement time (#137)."""

    from datetime import datetime, timezone

    return save_gui_preferences(
        project_root,
        {
            "full_auto_pinned": True,
            "full_auto_acknowledged_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "default_mode": "full-auto",
        },
    )


def unpin_full_auto(project_root: Path) -> dict[str, Any]:
    """Clear the Full Auto pin and fall back to Safe Auto (#137)."""

    current = load_gui_preferences(project_root)
    updates: dict[str, Any] = {
        "full_auto_pinned": False,
        "full_auto_acknowledged_at": "",
    }
    if current.get("default_mode") == "full-auto":
        updates["default_mode"] = DEFAULT_MODE
    return save_gui_preferences(project_root, updates)


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
