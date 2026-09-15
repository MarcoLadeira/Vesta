"""Per-provider *account usage* truth for the Settings → Model Usage page.

This answers a different question from the Cost Firewall's per-model token
budgets (``opaihub.usage``) and from Credits & Balance
(``opaihub.provider_balance``): *how much of each provider's own rate/usage
allowance have I used, in the window that provider actually enforces?*

Every provider has a different allowance model, so this module is
window-aware rather than forcing a single shape:

* **Claude** subscription — a rolling ~5-hour session window.
* **Codex** (ChatGPT plan) — weekly plan limits.
* **Copilot** — monthly premium-request allowance.
* **Gemini / Groq / Mistral** free tiers — per-day request/token limits.
* **Kimi** (Moonshot) free tier — prepaid credit (delegated to balance).

Data provenance is always explicit and never fabricated:

* ``provider`` — a real limit/remaining/reset the provider itself reported,
  either observed from the rate-limit headers of your actual calls (recorded
  in the local ledger's ``quota_snapshot``) or refreshed live from a safe,
  prompt-free endpoint (``GET /models``) or the balance API (Kimi).
* ``opai-tracked`` — Vesta's own local count of calls/tokens in the applicable
  window, from the privacy-safe ledger. Always labelled as Vesta-tracked, and
  never presented as the provider's official percentage.
* ``unavailable`` — no safe machine-readable usage endpoint exists (the
  account CLIs). The verifiable window definition and where to check official
  usage are shown; a percentage is never invented.

One unavailable provider never breaks the rest of the page: every read is
best-effort and degrades to ``unknown``/``unavailable`` instead of raising.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ledger import EVENT_MODEL_CALL, read_events
from .provider_balance import balance_snapshot, provider_display_name
from .state import state_dir

# Live probes (GET /models, balance API) are cached this long so opening the
# page or pressing Refresh twice does not re-hit the network needlessly.
PROBE_TTL_SECONDS = 300.0
# Observed rate-limit data older than its own reset window is stale: the window
# has rolled over and the numbers no longer describe the current window.
DEFAULT_OBSERVED_TTL_SECONDS = 24 * 3600.0

# ---------------------------------------------------------------------------
# provider window models (verifiable, published knowledge — no fabrication)
# ---------------------------------------------------------------------------
# ``liveSource``: how (if at all) official usage can be refreshed safely.
#   "headers"  — GET /models returns rate-limit headers (free OpenAI-compat APIs)
#   "balance"  — a prepaid-credit balance API (Kimi/Moonshot)
#   None       — no safe machine-readable usage endpoint (account CLIs)
USAGE_MODELS: dict[str, dict[str, Any]] = {
    "claude": {
        "kind": "account",
        "windowType": "rolling",
        "windowLabel": "5-hour session window",
        "windowSeconds": 5 * 3600,
        "metric": "session",
        "liveSource": None,
        "checkUrl": "https://claude.ai/settings/usage",
        "note": (
            "Claude subscriptions meter a rolling ~5-hour session window. Vesta "
            "signs in through the Claude CLI, which does not expose a "
            "machine-readable usage figure, so the exact percentage is only "
            "visible in Claude directly. Vesta's own count below only includes "
            "messages sent through Vesta's chat — not the claude CLI used directly."
        ),
    },
    "codex": {
        "kind": "account",
        "windowType": "weekly",
        "windowLabel": "Weekly plan limit",
        "windowSeconds": 7 * 24 * 3600,
        "metric": "messages",
        "liveSource": None,
        "checkUrl": "https://chatgpt.com/#settings",
        "note": (
            "Codex runs on your ChatGPT plan, which enforces weekly message "
            "limits. The plan does not publish a usage endpoint Vesta can read, "
            "so official usage is shown in ChatGPT. Vesta's own count below only "
            "includes messages sent through Vesta's chat — not the codex CLI "
            "used directly."
        ),
    },
    "copilot": {
        "kind": "account",
        "windowType": "monthly",
        "windowLabel": "Monthly premium requests",
        "windowSeconds": 30 * 24 * 3600,
        "metric": "requests",
        "liveSource": None,
        "checkUrl": "https://github.com/settings/copilot",
        "note": (
            "Copilot meters premium requests monthly. GitHub reports usage in "
            "your account billing page rather than through an API Vesta can call. "
            "Vesta's own count below only includes messages sent through Vesta's "
            "chat — not GitHub Copilot used directly in your editor."
        ),
    },
    "gemini": {
        "kind": "free",
        "windowType": "daily",
        "windowLabel": "Daily requests (free tier)",
        "windowSeconds": 24 * 3600,
        "metric": "requests",
        "liveSource": "headers",
        "checkUrl": "https://aistudio.google.com",
    },
    "groq": {
        "kind": "free",
        "windowType": "daily",
        "windowLabel": "Daily requests (free tier)",
        "windowSeconds": 24 * 3600,
        "metric": "requests",
        "liveSource": "headers",
        "checkUrl": "https://console.groq.com/settings/limits",
    },
    "mistral": {
        "kind": "free",
        "windowType": "monthly",
        "windowLabel": "Monthly tokens (free tier)",
        "windowSeconds": 30 * 24 * 3600,
        "metric": "tokens",
        "liveSource": "headers",
        "checkUrl": "https://console.mistral.ai",
    },
    "kimi": {
        "kind": "free",
        "windowType": "balance",
        "windowLabel": "Prepaid credit",
        "windowSeconds": None,
        "metric": "credit",
        "liveSource": "balance",
        "checkUrl": "https://platform.moonshot.ai",
    },
}

# API bases for the safe header probe, kept in step with free_models specs.
_MODELS_ENDPOINTS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
}


def _clean_provider(provider: str) -> str:
    return str(provider or "").strip().lower()


def usage_model(provider: str) -> dict[str, Any] | None:
    return USAGE_MODELS.get(_clean_provider(provider))


def supports_live_usage(provider: str) -> bool:
    model = usage_model(provider)
    return bool(model and model.get("liveSource"))


# ---------------------------------------------------------------------------
# rate-limit header parsing (real provider data → normalized quota)
# ---------------------------------------------------------------------------
_DURATION_RE = re.compile(
    r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?"
)


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_reset(value: Any, *, observed_at: float) -> float | None:
    """Resolve a reset header (duration like ``2m59s``, seconds, or a timestamp)
    to an absolute epoch second. Returns ``None`` when it cannot be parsed."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Bare number: seconds-from-now for small values, epoch for large ones.
    number = _to_float(text)
    if number is not None:
        if number > 1_000_000_000:  # already an epoch timestamp
            return number
        return observed_at + max(0.0, number)
    # ISO-8601 timestamp.
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        pass
    # Duration string, e.g. "1h30m", "2m59.56s", "45s".
    match = _DURATION_RE.fullmatch(text)
    if match and any(match.groups()):
        hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
        return observed_at + hours * 3600 + minutes * 60 + seconds
    return None


def parse_quota_headers(
    headers: dict[str, Any] | None, *, observed_at: float | None = None
) -> dict[str, Any] | None:
    """Normalize OpenAI-style ``x-ratelimit-*`` headers into a quota snapshot.

    Prefers the request-count window; falls back to the token window. Returns
    ``None`` when no usable limit header is present (never fabricates)."""

    if not headers:
        return None
    ts = time.time() if observed_at is None else float(observed_at)
    lower = {str(k).lower(): v for k, v in headers.items()}

    for metric, limit_key, remaining_key, reset_key, window in (
        (
            "requests",
            "x-ratelimit-limit-requests",
            "x-ratelimit-remaining-requests",
            "x-ratelimit-reset-requests",
            "day",
        ),
        (
            "tokens",
            "x-ratelimit-limit-tokens",
            "x-ratelimit-remaining-tokens",
            "x-ratelimit-reset-tokens",
            "minute",
        ),
    ):
        limit = _to_float(lower.get(limit_key))
        if not limit or limit <= 0:
            continue
        remaining = _to_float(lower.get(remaining_key))
        remaining = max(0.0, remaining) if remaining is not None else None
        used = max(0.0, limit - remaining) if remaining is not None else None
        return {
            "metric": metric,
            "limit": limit,
            "remaining": remaining,
            "used": used,
            "window": window,
            "resetsAt": parse_reset(lower.get(reset_key), observed_at=ts),
            "observedAt": ts,
        }
    return None


# ---------------------------------------------------------------------------
# probe store (latest live-probed quota per provider; TTL-cached)
# ---------------------------------------------------------------------------
def _path(project_root: Path) -> Path:
    return state_dir(project_root) / "health" / "provider_usage.json"


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


def _headers_of(response: Any) -> dict[str, str]:
    raw = getattr(response, "headers", None)
    if raw is None:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in raw.items()}
    except (AttributeError, TypeError):
        return {}


def _probe_headers(provider: str, api_key: str) -> dict[str, Any] | None:
    """Read rate-limit headers from a prompt-free ``GET /models`` call.

    Official and safe: no prompt is submitted, only public model metadata is
    requested. Returns a normalized quota or ``None`` on any failure."""

    import urllib.request

    base = _MODELS_ENDPOINTS.get(provider)
    if not base:
        return None
    request = urllib.request.Request(
        base.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:  # noqa: S310  # nosec B310 - fixed HTTPS hosts
            response.read(1)
            return parse_quota_headers(_headers_of(response))
    except Exception:  # noqa: BLE001 - a usage probe must never break Settings
        return None


def probe_usage(
    project_root: Path, provider: str, *, force: bool = False, now: float | None = None
) -> dict[str, Any] | None:
    """Refresh ``provider``'s live official usage (TTL-cached) and persist it.

    Returns the freshly probed quota, or the stored one within the TTL, or
    ``None`` when the provider has no safe live source or the probe failed."""

    provider = _clean_provider(provider)
    model = usage_model(provider)
    if not model:
        return None
    ts = time.time() if now is None else float(now)
    source = model.get("liveSource")
    store = _load(project_root)
    entry = store.get(provider) if isinstance(store.get(provider), dict) else {}
    checked = _to_float(entry.get("checkedAt")) or 0.0
    if not force and entry.get("quota") and (ts - checked) < PROBE_TTL_SECONDS:
        return entry["quota"]

    quota: dict[str, Any] | None = None
    if source == "headers":
        from .credentials import CredentialStore

        try:
            api_key = CredentialStore().get(provider) or ""
        except Exception:  # noqa: BLE001 - keychain trouble must not break Settings
            api_key = ""
        if api_key:
            quota = _probe_headers(provider, api_key)
    # "balance" is handled in the snapshot via provider_balance; nothing to probe here.

    if quota is not None:
        entry = {"quota": quota, "checkedAt": ts}
        try:
            store[provider] = entry
            _save(project_root, store)
        except OSError:
            pass
        return quota
    return entry.get("quota") if isinstance(entry, dict) else None


# ---------------------------------------------------------------------------
# ledger reads (observed provider quota + Vesta-tracked window counts)
# ---------------------------------------------------------------------------
def _event_time(event: dict[str, Any]) -> float | None:
    text = str(event.get("created_at") or "").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _model_call_events(project_root: Path) -> list[dict[str, Any]]:
    return [
        event
        for event in read_events(project_root)
        if event.get("event_type") == EVENT_MODEL_CALL
    ]


def _provider_of(event: dict[str, Any]) -> str:
    return _clean_provider(
        str(event.get("provider_id") or event.get("provider_type") or "")
    )


def _observed_quota(
    events: list[dict[str, Any]], provider: str, *, now: float
) -> dict[str, Any] | None:
    """The most recent provider-reported quota for ``provider`` from real calls."""

    for event in reversed(events):
        if _provider_of(event) != provider:
            continue
        snapshot = event.get("quota_snapshot")
        if isinstance(snapshot, dict) and _to_float(snapshot.get("limit")):
            observed_at = _event_time(event) or now
            reset_raw = snapshot.get("resetsAt")
            limit = _to_float(snapshot.get("limit"))
            remaining = _to_float(snapshot.get("remaining"))
            remaining = max(0.0, remaining) if remaining is not None else None
            used = max(0.0, limit - remaining) if remaining is not None else None
            return {
                "metric": str(snapshot.get("metric") or "requests"),
                "limit": limit,
                "remaining": remaining,
                "used": used,
                "window": str(snapshot.get("window") or "day"),
                "resetsAt": parse_reset(reset_raw, observed_at=observed_at),
                "observedAt": observed_at,
            }
    return None


def _window_start(
    window_type: str, window_seconds: int | None, now: float
) -> float | None:
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    if window_type == "rolling" and window_seconds:
        return now - window_seconds
    if window_type == "daily":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if window_type == "weekly":
        midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.timestamp() - dt.weekday() * 24 * 3600
    if window_type == "monthly":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return None


def _opai_tracked(
    events: list[dict[str, Any]], provider: str, model: dict[str, Any], *, now: float
) -> dict[str, Any]:
    """Vesta's own local activity count for ``provider``.

    Account CLIs (Claude/Codex/Copilot) have no official window Vesta can
    verify the real boundaries of — mimicking a tight rolling window (e.g.
    Claude's actual ~5 hours) against events timestamped by *Vesta's* clock
    would almost always read as empty, since most usage of these tools
    happens through the CLI directly and never touches Vesta's ledger at all.
    For these, count all-time activity instead, with a "last used" freshness
    readout — an honest, always-useful signal rather than a technically
    precise but practically always-zero one. Free-tier APIs with a real
    calendar window (daily/weekly/monthly) keep matching that window, since
    "today"/"this week" are independently verifiable regardless of what the
    provider's own reset clock reads.
    """

    verified_window = str(model.get("windowType")) in {"daily", "weekly", "monthly"}
    start = (
        _window_start(str(model.get("windowType")), model.get("windowSeconds"), now)
        if verified_window
        else None
    )
    calls = 0
    tokens = 0
    tasks = 0
    last_used: float | None = None
    for event in events:
        if _provider_of(event) != provider:
            continue
        when = _event_time(event)
        if last_used is None or (when is not None and when > last_used):
            last_used = when
        if start is not None and (when is None or when < start):
            continue
        tasks += 1
        raw_calls = event.get("model_calls")
        model_calls = _to_float(raw_calls)
        calls += int(model_calls) if model_calls and model_calls > 0 else 1
        raw_tokens = event.get("tokens")
        token_count = _to_float(raw_tokens)
        tokens += int(token_count) if token_count is not None and token_count > 0 else 0
    labels = {
        "rolling": "All time via Vesta",
        "daily": "Today",
        "weekly": "This week",
        "monthly": "This month",
        "balance": "All time",
    }
    return {
        "calls": calls,
        "tokens": tokens,
        "lastUsedAt": last_used,
        "tasks": tasks,
        "windowLabel": labels.get(str(model.get("windowType")), "Recent"),
    }


# ---------------------------------------------------------------------------
# snapshot assembly
# ---------------------------------------------------------------------------
def _percent(used: float | None, limit: float | None) -> float | None:
    if used is None or not limit or limit <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * used / limit)), 1)


def usage_snapshot(
    project_root: Path,
    provider: str,
    *,
    configured: bool = True,
    events: list[dict[str, Any]] | None = None,
    probe: bool = False,
    force: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """The UI-ready official-usage truth for one provider. Secret-free by shape."""

    provider = _clean_provider(provider)
    ts = time.time() if now is None else float(now)
    model = usage_model(provider)
    if model is None:
        return {
            "provider": provider,
            "displayName": provider_display_name(provider),
            "kind": "unknown",
            "configured": configured,
            "status": "unsupported",
            "window": None,
            "official": {"available": False},
            "opaiTracked": None,
            "detail": "Vesta does not track usage windows for this provider yet.",
            "checkUrl": None,
            "supportsRefresh": False,
        }

    events = _model_call_events(project_root) if events is None else events
    window = {
        "type": model["windowType"],
        "label": model["windowLabel"],
        "seconds": model.get("windowSeconds"),
        "metric": model.get("metric"),
    }
    opai_tracked = _opai_tracked(events, provider, model, now=ts)

    official: dict[str, Any] = {"available": False}
    source = model.get("liveSource")

    if not configured:
        status = "not_configured"
        detail = f"Not connected. {model.get('note') or 'Connect this provider to see usage.'}"
    elif source == "balance":
        balance = balance_snapshot(project_root, provider, now=ts)
        amount = balance.get("amount")
        if amount is not None:
            official = {
                "available": True,
                "source": "provider"
                if balance.get("source") == "provider"
                else balance.get("source"),
                "metric": "credit",
                "limit": None,
                "remaining": amount,
                "used": None,
                "percent": balance.get("percent"),
                "currency": balance.get("currency"),
                "resetsAt": None,
                "resetsInSeconds": None,
                "observedAt": balance.get("checkedAt"),
                "stale": False,
            }
            status = "live"
            detail = "Prepaid credit remaining, reported by the provider."
        else:
            status = "unavailable"
            detail = (
                "No credit figure yet — it appears after your first call, or "
                "enter it under Credits & Balance."
            )
    else:
        quota = None
        if probe and source == "headers":
            quota = probe_usage(project_root, provider, force=force, now=ts)
        if quota is None:
            quota = _observed_quota(events, provider, now=ts)
        if quota is not None:
            observed_at = _to_float(quota.get("observedAt")) or ts
            resets_at = _to_float(quota.get("resetsAt"))
            resets_in = max(0.0, resets_at - ts) if resets_at else None
            # Stale once the observed window has demonstrably rolled over.
            stale = bool(resets_at and resets_at <= ts) or (
                ts - observed_at > DEFAULT_OBSERVED_TTL_SECONDS
            )
            used = quota.get("used")
            limit = quota.get("limit")
            official = {
                "available": True,
                "source": "provider",
                "metric": quota.get("metric"),
                "limit": limit,
                "remaining": quota.get("remaining"),
                "used": used,
                "percent": _percent(used, limit),
                "resetsAt": resets_at,
                "resetsInSeconds": None if resets_in is None else int(resets_in),
                "observedAt": observed_at,
                "stale": stale,
            }
            status = "stale" if stale else "live"
            detail = (
                "Reported by the provider on your recent calls."
                if not stale
                else "From an earlier window; make a call or refresh to update."
            )
        else:
            status = "unavailable"
            detail = (
                "Live usage appears after your first call to this provider "
                "(Vesta reads the rate-limit headers your call returns)."
            )

    if source is None and configured:
        # Account CLIs: honest unavailable, with the verifiable window + link.
        status = "unavailable"
        detail = model.get("note") or detail

    return {
        "provider": provider,
        "displayName": provider_display_name(provider),
        "kind": model["kind"],
        "configured": configured,
        "status": status,
        "window": window,
        "official": official,
        "opaiTracked": opai_tracked,
        "detail": detail,
        "checkUrl": model.get("checkUrl"),
        "supportsRefresh": bool(source),
    }


def usage_overview(
    project_root: Path,
    providers: list[dict[str, Any]],
    *,
    events: list[dict[str, Any]] | None = None,
    probe: bool = False,
    force: bool = False,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Usage snapshots for a list of ``{"provider", "configured"}`` entries.

    Reads the ledger once and shares it across providers. ``probe=True``
    refreshes live sources (header probes) for configured providers that
    support one — used by the Settings surface on its worker thread."""

    events = _model_call_events(project_root) if events is None else events
    events_by_provider: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        event_provider = _provider_of(event)
        if event_provider:
            events_by_provider.setdefault(event_provider, []).append(event)
    ts = time.time() if now is None else float(now)
    seen: set[str] = set()
    overview: list[dict[str, Any]] = []
    for item in providers:
        provider = _clean_provider(str(item.get("provider") or ""))
        if not provider or provider in seen or provider not in USAGE_MODELS:
            continue
        seen.add(provider)
        overview.append(
            usage_snapshot(
                project_root,
                provider,
                configured=bool(item.get("configured", True)),
                events=events_by_provider.get(provider, []),
                probe=probe,
                force=force,
                now=ts,
            )
        )
    return overview
