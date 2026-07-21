"""Provider reliability memory for OPai Auto routing.

Auto records the recent outcome (success / failure) of every provider it calls
so it can:

* temporarily **deprioritize** a provider that just failed — an auth error,
  rate limit, timeout, unavailable endpoint, or an empty "no answer" — instead
  of routing to it again and again, and
* **round-robin** among equally-good providers (least-recently-used) so it does
  not keep picking whichever one happens to be first in the list.

The store is purely local JSON under the project's ``.opaihub`` state dir. It
records *no prompts and no secrets* — only provider ids, small rolling outcome
counters, a short reason slug, and timestamps. A failing provider is never
*refused*; it is only pushed later in the fallback order, and Auto will still
use it when it is the only option left (honesty over cleverness).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .state import state_dir

# A failure keeps a provider in "cooldown" for this many seconds. During
# cooldown Auto sorts it behind healthy providers of the same cost bucket, but
# never drops it entirely.
COOLDOWN_SECONDS = 180.0
# Rolling window used to weigh recent failures. Older events decay out.
WINDOW_SECONDS = 1800.0
# Bound the per-provider event list so the file stays tiny.
MAX_EVENTS = 12
# Reasons that are transient and worth a short cooldown, versus hard config
# problems that should deprioritize a provider harder/longer.
_HARD_REASONS = {"auth", "unauthenticated", "misconfigured", "unavailable"}

# A closed vocabulary of reason slugs. Anything outside this set is stored as
# the generic "error" so no free-form text — and therefore no secret from a
# raw provider error string — can ever be persisted, even if a caller passes
# an un-slugged reason by mistake.
_ALLOWED_REASONS = frozenset(
    {
        "auth",
        "unauthenticated",
        "misconfigured",
        "unavailable",
        "rate-limit",
        "quota",
        "timeout",
        "capability",
        "no-local",
        "no-answer",
        "runner-error",
        "empty",
        "failed",
        "needs-model",
        "error",
    }
)


def _path(project_root: Path) -> Path:
    return state_dir(project_root) / "health" / "provider_reliability.json"


def _load(project_root: Path) -> dict[str, Any]:
    path = _path(project_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _clean_reason(reason: str) -> str:
    """A short, secret-safe slug for why a call failed (never the raw error).

    Only slugs in the closed ``_ALLOWED_REASONS`` vocabulary are stored; any
    other input collapses to ``"error"`` so no free-form provider text (which
    could embed a token or secret) is ever written to disk.
    """
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(reason or "").lower())
    slug = "-".join(part for part in slug.split("-") if part)
    return slug if slug in _ALLOWED_REASONS else "error"


def record_provider_outcome(
    project_root: Path,
    provider: str,
    ok: bool,
    *,
    reason: str = "",
    now: float | None = None,
) -> None:
    """Append one outcome for ``provider``. Best-effort; never raises."""
    provider = str(provider or "").strip().lower()
    if not provider or provider in {"auto", "local"}:
        # The local-detection sentinel and empty ids are not real providers.
        return
    ts = time.time() if now is None else float(now)
    try:
        store = _load(project_root)
        entry = store.get(provider)
        if not isinstance(entry, dict):
            entry = {"events": [], "last_used": 0.0}
        events = [e for e in entry.get("events", []) if isinstance(e, dict)]
        events.append(
            {"ok": bool(ok), "at": ts, "reason": _clean_reason(reason) if not ok else ""}
        )
        entry["events"] = events[-MAX_EVENTS:]
        entry["last_used"] = ts
        entry["last_ok"] = bool(ok)
        store[provider] = entry
        _atomic_write(
            _path(project_root), json.dumps(store, sort_keys=True, indent=2) + "\n"
        )
    except OSError:
        # Reliability memory is an optimization, never a hard dependency.
        return


def _recent_events(entry: dict[str, Any], now: float) -> list[dict[str, Any]]:
    return [
        e
        for e in entry.get("events", [])
        if isinstance(e, dict) and (now - float(e.get("at", 0.0))) <= WINDOW_SECONDS
    ]


def reliability_penalty(
    project_root: Path, provider: str, *, now: float | None = None
) -> float:
    """A 0.0 (perfect) → 1.0 (all-recent-failed) penalty for ``provider``.

    Weighs only events inside ``WINDOW_SECONDS``; a provider with no recent
    history is treated as neutral-good (0.0) so a brand-new provider is not
    punished for lack of data.
    """
    provider = str(provider or "").strip().lower()
    if not provider:
        return 0.0
    entry = _load(project_root).get(provider)
    if not isinstance(entry, dict):
        return 0.0
    ts = time.time() if now is None else float(now)
    recent = _recent_events(entry, ts)
    if not recent:
        return 0.0
    failures = sum(1 for e in recent if not e.get("ok"))
    penalty = failures / len(recent)
    # A hard-reason most-recent failure weighs a little heavier.
    last = recent[-1]
    if not last.get("ok") and str(last.get("reason", "")) in _HARD_REASONS:
        penalty = min(1.0, penalty + 0.15)
    return round(penalty, 4)


def in_cooldown(project_root: Path, provider: str, *, now: float | None = None) -> bool:
    """True when ``provider``'s most recent event was a failure within cooldown."""
    provider = str(provider or "").strip().lower()
    if not provider:
        return False
    entry = _load(project_root).get(provider)
    if not isinstance(entry, dict):
        return False
    events = [e for e in entry.get("events", []) if isinstance(e, dict)]
    if not events:
        return False
    last = events[-1]
    if last.get("ok"):
        return False
    ts = time.time() if now is None else float(now)
    return (ts - float(last.get("at", 0.0))) <= COOLDOWN_SECONDS


def last_used(project_root: Path, provider: str) -> float:
    """Timestamp of the last call to ``provider`` (0.0 if never), for LRU order."""
    provider = str(provider or "").strip().lower()
    entry = _load(project_root).get(provider)
    if not isinstance(entry, dict):
        return 0.0
    return float(entry.get("last_used", 0.0) or 0.0)


def reliability_snapshot(
    project_root: Path, *, now: float | None = None
) -> dict[str, dict[str, Any]]:
    """Compact per-provider health for internal routing diagnostics."""
    ts = time.time() if now is None else float(now)
    out: dict[str, dict[str, Any]] = {}
    for provider, entry in _load(project_root).items():
        if not isinstance(entry, dict):
            continue
        recent = _recent_events(entry, ts)
        out[provider] = {
            "penalty": reliability_penalty(project_root, provider, now=ts),
            "cooldown": in_cooldown(project_root, provider, now=ts),
            "recent_calls": len(recent),
            "recent_failures": sum(1 for e in recent if not e.get("ok")),
            "last_ok": bool(entry.get("last_ok", True)),
        }
    return out
