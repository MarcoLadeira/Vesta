"""Per-provider credit/balance truth for Vesta.

Answers one question for every configured AI tool: *how much money/credit is
left on it?* — so the Settings page can show honest balances, the model picker
can hide a tool that cannot possibly answer, and Auto routing never wastes a
call on a provider that is out of credit.

Three honest sources, in trust order:

1. ``provider`` — a live balance API. Only Moonshot (Kimi) exposes one today;
   the fetcher registry makes adding more a one-entry change.
2. ``observed`` — a real run was refused for insufficient balance/quota (or
   succeeded, which proves credit exists and clears the flag).
3. ``manual`` — the amount the user typed from their provider console (e.g.
   "Claude: €85 extra usage" — subscription providers publish no balance API).

No source → ``unknown``, never a fabricated number. The store is local JSON
under ``.vestahub/health`` and records **no prompts and no secrets** — only
amounts, currency codes, closed-vocabulary source slugs, and timestamps.

An out-of-credit verdict expires after ``EXHAUSTED_TTL_SECONDS`` so a recharge
made outside Vesta is eventually rediscovered even if the user never presses
"Test": the tool reappears, the next real call either works (clearing the flag)
or re-observes the exhaustion.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable

from .state import state_dir

# Live probes are cached for this long so opening Settings twice in a row does
# not re-hit the network.
PROBE_TTL_SECONDS = 900.0
# An exhausted verdict older than this no longer excludes the provider: the
# user may have recharged outside Vesta. The next real call re-proves it.
EXHAUSTED_TTL_SECONDS = 6 * 3600.0
# Below this fraction of the reference amount the status turns "low".
LOW_FRACTION = 0.15

_SOURCES = ("provider", "observed", "manual", "none")

# Where to top up, per provider — shown when a tool runs out of credit.
RECHARGE_HINTS = {
    "kimi": "Top up at platform.moonshot.ai (Billing).",
    "claude": "Manage usage credits at claude.ai/settings/usage.",
    "codex": "Manage billing at platform.openai.com/settings/organization/billing.",
    "copilot": "Manage Copilot billing at github.com/settings/billing.",
    "gemini": "Check your plan at aistudio.google.com.",
    "groq": "Check your plan at console.groq.com.",
    "mistral": "Check your plan at console.mistral.ai.",
}

_DISPLAY_NAMES = {
    "kimi": "Kimi (Moonshot)",
    "claude": "Claude",
    "codex": "Codex (OpenAI)",
    "copilot": "GitHub Copilot",
    "gemini": "Gemini",
    "groq": "Groq",
    "mistral": "Mistral",
}


def provider_display_name(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    return _DISPLAY_NAMES.get(provider, provider.capitalize() or "Provider")


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def _path(project_root: Path) -> Path:
    return state_dir(project_root) / "health" / "provider_balance.json"


def _load(project_root: Path) -> dict[str, Any]:
    path = _path(project_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(project_root: Path, store: dict[str, Any]) -> None:
    path = _path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(store, sort_keys=True, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _entry(store: dict[str, Any], provider: str) -> dict[str, Any]:
    entry = store.get(provider)
    return entry if isinstance(entry, dict) else {}


def _clean_provider(provider: str) -> str:
    return str(provider or "").strip().lower()


def _amount(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _currency(value: Any, default: str = "USD") -> str:
    text = str(value or "").strip().upper()
    # Currency codes only — a closed shape, never free text.
    return text if text.isalpha() and 2 <= len(text) <= 4 else default


# ---------------------------------------------------------------------------
# recording (observed evidence + manual entry)
# ---------------------------------------------------------------------------


def record_exhausted(
    project_root: Path,
    provider: str,
    *,
    source: str = "observed",
    now: float | None = None,
) -> None:
    """Mark ``provider`` out of credit from real evidence. Never raises."""
    provider = _clean_provider(provider)
    if not provider or provider in {"auto", "local"}:
        return
    ts = time.time() if now is None else float(now)
    try:
        store = _load(project_root)
        entry = _entry(store, provider)
        entry["exhausted"] = True
        entry["exhausted_at"] = ts
        entry["exhausted_source"] = source if source in _SOURCES else "observed"
        if source == "observed":
            # A refused call proves the *spendable* amount is effectively zero.
            entry["amount"] = 0.0
            entry["source"] = "observed"
            entry["checked_at"] = ts
        store[provider] = entry
        _save(project_root, store)
    except OSError:
        return


def record_success(
    project_root: Path, provider: str, *, now: float | None = None
) -> None:
    """A real call succeeded — proof there is credit; clear the exhausted flag."""
    provider = _clean_provider(provider)
    if not provider:
        return
    try:
        store = _load(project_root)
        entry = _entry(store, provider)
        if not entry.get("exhausted"):
            return
        entry["exhausted"] = False
        entry.pop("exhausted_at", None)
        entry.pop("exhausted_source", None)
        if entry.get("source") == "observed":
            # The observed zero is disproven; fall back to whatever else we know.
            entry.pop("amount", None)
            entry.pop("source", None)
        store[provider] = entry
        _save(project_root, store)
    except OSError:
        return


def set_manual_balance(
    project_root: Path,
    provider: str,
    amount: float,
    *,
    currency: str = "USD",
    now: float | None = None,
) -> dict[str, Any]:
    """Store the balance the user read from their provider console.

    ``amount`` must be a finite number ≥ 0. Zero is honest ("I'm out") and
    marks the provider exhausted; a positive amount clears any exhaustion.
    Returns the fresh snapshot. Raises ``ValueError`` on invalid input.
    """
    provider = _clean_provider(provider)
    if not provider:
        raise ValueError("provider is required")
    value = _amount(amount)
    if value is None or value < 0 or value != value or value == float("inf"):
        raise ValueError("amount must be a number of at least 0")
    ts = time.time() if now is None else float(now)
    store = _load(project_root)
    entry = _entry(store, provider)
    entry["amount"] = round(value, 4)
    entry["currency"] = _currency(currency)
    entry["source"] = "manual"
    entry["checked_at"] = ts
    entry["reference"] = max(_amount(entry.get("reference")) or 0.0, value) or value
    if value <= 0:
        entry["exhausted"] = True
        entry["exhausted_at"] = ts
        entry["exhausted_source"] = "manual"
    else:
        entry["exhausted"] = False
        entry.pop("exhausted_at", None)
        entry.pop("exhausted_source", None)
    store[provider] = entry
    _save(project_root, store)
    return balance_snapshot(project_root, provider, now=ts)


def clear_manual_balance(project_root: Path, provider: str) -> None:
    provider = _clean_provider(provider)
    try:
        store = _load(project_root)
        entry = _entry(store, provider)
        if entry.get("source") == "manual":
            for key in ("amount", "currency", "source", "checked_at"):
                entry.pop(key, None)
            if entry.get("exhausted_source") == "manual":
                entry["exhausted"] = False
                entry.pop("exhausted_at", None)
                entry.pop("exhausted_source", None)
            store[provider] = entry
            _save(project_root, store)
    except OSError:
        return


# ---------------------------------------------------------------------------
# live probes
# ---------------------------------------------------------------------------


def _probe_moonshot(api_key: str) -> tuple[float, str] | None:
    """Moonshot's real balance endpoint. Returns (available_balance, currency).

    api.moonshot.ai bills in USD. Returns ``None`` on any failure — a balance
    probe is an optimization and must never break Settings.
    """
    import urllib.request

    request = urllib.request.Request(
        "https://api.moonshot.ai/v1/users/me/balance",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=4.0) as response:  # noqa: S310  # nosec B310 - fixed HTTPS URL
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - any transport/parse failure means "no data"
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    available = _amount(data.get("available_balance"))
    if available is None:
        return None
    return available, "USD"


# provider → probe attribute name; resolved late so tests can patch the
# function. A probe takes the api key and returns (amount, currency) or None.
# Only providers with a real balance API belong here; everything else is
# manual/observed by design.
_LIVE_PROBES: dict[str, str] = {
    "kimi": "_probe_moonshot",
}


def _live_probe(provider: str) -> Callable[[str], tuple[float, str] | None] | None:
    name = _LIVE_PROBES.get(_clean_provider(provider))
    return globals().get(name) if name else None


def supports_live_balance(provider: str) -> bool:
    return _clean_provider(provider) in _LIVE_PROBES


def probe_balance(
    project_root: Path,
    provider: str,
    *,
    force: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Refresh ``provider``'s live balance (TTL-cached) and return its snapshot.

    Providers without a balance API return their snapshot unchanged. A failed
    probe changes nothing — stale truth beats fabricated truth.
    """
    provider = _clean_provider(provider)
    ts = time.time() if now is None else float(now)
    probe = _live_probe(provider)
    if probe is None:
        return balance_snapshot(project_root, provider, now=ts)
    store = _load(project_root)
    entry = _entry(store, provider)
    checked = _amount(entry.get("checked_at")) or 0.0
    if (
        not force
        and entry.get("source") == "provider"
        and (ts - checked) < PROBE_TTL_SECONDS
    ):
        return balance_snapshot(project_root, provider, now=ts)
    from .credentials import CredentialStore

    try:
        api_key = CredentialStore().get(provider) or ""
    except Exception:  # noqa: BLE001 - keychain trouble must not break Settings
        api_key = ""
    if not api_key:
        return balance_snapshot(project_root, provider, now=ts)
    result = probe(api_key)
    if result is None:
        return balance_snapshot(project_root, provider, now=ts)
    amount, currency = result
    entry["amount"] = round(amount, 4)
    entry["currency"] = _currency(currency)
    entry["source"] = "provider"
    entry["checked_at"] = ts
    entry["reference"] = max(_amount(entry.get("reference")) or 0.0, amount) or amount
    if amount <= 0:
        entry["exhausted"] = True
        entry["exhausted_at"] = ts
        entry["exhausted_source"] = "provider"
    else:
        entry["exhausted"] = False
        entry.pop("exhausted_at", None)
        entry.pop("exhausted_source", None)
    try:
        store[provider] = entry
        _save(project_root, store)
    except OSError:
        pass
    return balance_snapshot(project_root, provider, now=ts)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def is_exhausted(
    project_root: Path, provider: str, *, now: float | None = None
) -> bool:
    """True when ``provider`` is known out of credit and the verdict is fresh."""
    provider = _clean_provider(provider)
    if not provider:
        return False
    entry = _entry(_load(project_root), provider)
    ts = time.time() if now is None else float(now)
    return _is_exhausted_entry(entry, ts)


def _is_exhausted_entry(entry: dict[str, Any], now: float) -> bool:
    if not entry.get("exhausted"):
        return False
    at = _amount(entry.get("exhausted_at")) or 0.0
    return (now - at) <= EXHAUSTED_TTL_SECONDS


def _balance_snapshot_from_entry(
    provider: str, entry: dict[str, Any], now: float
) -> dict[str, Any]:
    """Build one balance snapshot from an already-loaded store entry."""
    amount = _amount(entry.get("amount"))
    currency = _currency(entry.get("currency")) if entry.get("currency") else "USD"
    source = entry.get("source") if entry.get("source") in _SOURCES else "none"
    reference = _amount(entry.get("reference"))
    out = _is_exhausted_entry(entry, now)
    percent: float | None = None
    if amount is not None and reference and reference > 0:
        percent = round(max(0.0, min(100.0, 100.0 * amount / reference)), 1)
    if out:
        status = "out"
    elif amount is None:
        status = "unknown"
    elif percent is not None and percent < LOW_FRACTION * 100:
        status = "low"
    elif amount > 0:
        status = "ok"
    else:
        status = "low"
    if source == "provider":
        detail = "Live balance reported by the provider."
    elif source == "manual":
        detail = "Entered by you in Settings."
    elif source == "observed":
        detail = "A recent call was refused for insufficient credit."
    else:
        detail = (
            "This provider does not report a balance — enter what your "
            "provider console shows."
        )
    return {
        "provider": provider,
        "displayName": provider_display_name(provider),
        "status": status,
        "amount": amount,
        "currency": currency,
        "percent": percent,
        "source": source,
        "supportsLiveBalance": supports_live_balance(provider),
        "checkedAt": _amount(entry.get("checked_at")),
        "detail": detail,
        "rechargeHint": RECHARGE_HINTS.get(provider, "Top up with your provider."),
    }


def balance_snapshot(
    project_root: Path, provider: str, *, now: float | None = None
) -> dict[str, Any]:
    """The UI-ready balance truth for one provider. Secret-free by shape."""
    provider = _clean_provider(provider)
    ts = time.time() if now is None else float(now)
    entry = _entry(_load(project_root), provider)
    return _balance_snapshot_from_entry(provider, entry, ts)


def balance_snapshots(
    project_root: Path,
    providers: Iterable[str],
    *,
    now: float | None = None,
) -> dict[str, dict[str, Any]]:
    """UI-ready snapshots for many providers with one store read."""
    ts = time.time() if now is None else float(now)
    store = _load(project_root)
    snapshots: dict[str, dict[str, Any]] = {}
    for raw_provider in providers:
        provider = _clean_provider(raw_provider)
        if not provider or provider in snapshots:
            continue
        snapshots[provider] = _balance_snapshot_from_entry(
            provider, _entry(store, provider), ts
        )
    return snapshots


def balance_overview(
    project_root: Path,
    providers: list[dict[str, Any]],
    *,
    probe: bool = False,
    force: bool = False,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Snapshots for a list of ``{"provider", "kind", "configured"}`` entries.

    ``probe=True`` refreshes live balances first (TTL-respecting; ``force=True``
    bypasses the TTL) for configured providers that support it — used by the
    Settings surface, which runs on a worker thread and can afford one short
    HTTP call.
    """
    ts = time.time() if now is None else float(now)
    cached = balance_snapshots(
        project_root,
        (str(item.get("provider") or "") for item in providers),
        now=ts,
    )
    seen: set[str] = set()
    overview: list[dict[str, Any]] = []
    for item in providers:
        provider = _clean_provider(str(item.get("provider") or ""))
        if not provider or provider in seen:
            continue
        seen.add(provider)
        configured = bool(item.get("configured", True))
        if probe and configured and supports_live_balance(provider):
            snapshot = probe_balance(project_root, provider, force=force, now=ts)
        else:
            snapshot = dict(cached[provider])
        snapshot["kind"] = str(item.get("kind") or "api")
        snapshot["configured"] = configured
        if not configured and snapshot["source"] == "none":
            snapshot["status"] = "not_configured"
            snapshot["detail"] = "Not connected yet."
        overview.append(snapshot)
    return overview
