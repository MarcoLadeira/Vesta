from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

from .atomic_io import atomic_write_text, interprocess_transaction
from .command_runner import redact
from .state import state_dir

DEFAULT_MODE = "safe-auto"
MODES = ["ask", "plan", "approve-edits", "safe-auto", "auto-edits", "full-auto"]

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
    # Activity copy: lets a user drag-select and copy the whole AI activity
    # rail (stage line + step-by-step timeline), not just the final answer —
    # much easier to hand someone for debugging than retyping what happened.
    # On by default; "off" restores the app's normal no-select chrome there.
    "activity_copy": "on",
    # First-run onboarding (#250): the three-step tour shows once on a fresh
    # profile and never again after it is completed or skipped.
    "onboarding_seen": False,
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
    "activity_copy",
    "onboarding_seen",
    "usage_limits",
    "free_consent",
    "safe_auto",
}


def _normalized_usage_limit(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0 or not number.is_integer():
        return None
    return int(number)


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
    if clean.get("activity_copy") not in {"on", "off"}:
        clean["activity_copy"] = "on"
    clean["onboarding_seen"] = bool(clean.get("onboarding_seen"))
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
            limit = _normalized_usage_limit(value.get("limit"))
            if (
                metric in {"tokens", "requests"}
                and window in {"minute", "day", "month"}
                and limit is not None
            ):
                clean_limits[redact(model_id)] = {
                    "metric": metric,
                    "limit": limit,
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
    # The stored mode is kept exactly as stored. It used to be rewritten here:
    # a persisted full-auto default was reset to Safe Auto on every load and
    # every save unless a separate pin flag and acknowledgement timestamp were
    # both present. That is what made a deliberately chosen mode fail to
    # survive a restart -- the rewrite happened underneath every surface, so no
    # amount of fixing the UI could have made the choice stick. The only
    # validation left is the one above: an id that is not a real mode falls
    # back, because nothing can render it.
    #
    # The pin fields stay in the schema so an existing preferences file still
    # round-trips, but they no longer decide anything.
    clean["full_auto_pinned"] = clean.get("full_auto_pinned") is True
    ack = clean.get("full_auto_acknowledged_at")
    clean["full_auto_acknowledged_at"] = str(ack) if isinstance(ack, str) else ""
    clean["schema_version"] = 3
    return clean


def grant_free_consent(project_root: Path, model_id: str) -> dict[str, Any]:
    """Persist that the user has consented to send to ``model_id`` (free tier).

    Only accepts ``free:`` ids so this cannot be used to grant consent for a
    paid model. Returns the updated preferences.
    """
    if not isinstance(model_id, str) or not model_id.startswith("free:"):
        raise ValueError("Only free:<provider>:<model> ids may be granted consent")

    def add_consent(current: dict[str, Any]) -> dict[str, Any]:
        consent = list(current.get("free_consent") or [])
        if model_id not in consent:
            consent.append(model_id)
        return {"free_consent": consent}

    return _mutate_gui_preferences(project_root, add_consent)


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
    return _mutate_gui_preferences(project_root, lambda _current: updates)


def _mutate_gui_preferences(
    project_root: Path, updates_for: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    path = preference_path(project_root)
    with interprocess_transaction(path):
        current = load_gui_preferences(project_root)
        current.update(updates_for(current))
        clean = _sanitize(current)
        atomic_write_text(path, json.dumps(clean, indent=2, sort_keys=True) + "\n")
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
    normalized_limit = _normalized_usage_limit(limit)
    if normalized_limit is None:
        raise ValueError("Usage limit must be a finite positive integer")

    def add_limit(current: dict[str, Any]) -> dict[str, Any]:
        limits = dict(current.get("usage_limits") or {})
        limits[str(model_id)] = {
            "metric": metric,
            "limit": normalized_limit,
            "window": window,
        }
        return {"usage_limits": limits}

    return _mutate_gui_preferences(project_root, add_limit)
