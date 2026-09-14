"""Deterministic provider blocks — the "this cannot work yet" memory.

``provider_reliability`` remembers *flaky* providers and only deprioritizes
them. ``provider_balance`` remembers providers that are *out of credit*. This
module covers the third category Vesta kept re-discovering the hard way: a
provider that is connected, funded, and reachable, but **cannot serve this
class of request at all until the user changes something**.

Two real examples from the QA campaign, both of which made Vesta look random:

* Codex answers every single request with ``The 'gpt-5.6-terra' model requires
  a newer version of Codex``. Retrying cannot help — only upgrading the CLI can.
* GitHub Copilot's CLI cannot expose a bounded edit-tool set, so Vesta refuses
  to launch it with repository write access. Ask/Plan work fine; every editing
  task is refused.

Without a memory of that, Auto burned a fallback step on the same guaranteed
refusal on every single turn, and the model picker happily offered a model that
could never complete the user's task. That is exactly the "sometimes it works,
sometimes it doesn't" experience: it was never random, it was four deterministic
refusals stacked behind one another.

Design rules, deliberately narrow:

* **Closed vocabulary.** Only the reason slugs in ``BLOCK_REASONS`` are stored,
  so no free-form provider text (which could embed a token) reaches disk.
* **Scoped.** A block is either ``"all"`` (the provider cannot answer anything)
  or ``"edit"`` (it can still explain and plan, it just cannot write files).
  A write-incapable provider must stay usable for Ask and Plan.
* **Self-healing.** Every block expires. A user who upgrades their CLI outside
  Vesta gets the provider back automatically, and any successful call clears the
  block immediately.
* **Never a hard refusal of the user's own choice.** These blocks steer *Auto*
  and annotate the picker. If the user explicitly picks a blocked model, Vesta
  still runs it — honesty over cleverness, same as the reliability memory.

The store is local JSON under ``.opaihub/health`` and records no prompts and no
secrets — only provider ids, reason slugs, and timestamps.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .state import state_dir

# reason slug -> {scope, ttl_seconds, title, remedy}
#
# ``scope`` is "all" when nothing can run, "edit" when only repository writes
# are impossible. ``ttl_seconds`` is how long Vesta trusts the observation before
# re-proving it, so an out-of-band fix is always rediscovered.
BLOCK_REASONS: dict[str, dict[str, Any]] = {
    "cli_outdated": {
        "scope": "all",
        "ttl_seconds": 24 * 3600.0,
        "title": "The installed CLI is too old for this model.",
        "remedy": "Update the provider CLI, then pick this model again.",
    },
    "config_invalid": {
        "scope": "all",
        "ttl_seconds": 6 * 3600.0,
        "title": "This provider's config file is invalid.",
        "remedy": "Run the one-click repair in Settings, then retry.",
    },
    "no_scoped_edits": {
        "scope": "edit",
        "ttl_seconds": 6 * 3600.0,
        "title": "This provider cannot be given safe repository write access.",
        "remedy": "Use it for Ask or Plan, or update its CLI for scoped tools.",
    },
}

# Provider-specific upgrade commands, so the remedy is a command to run and not
# a vague instruction. Kept next to the reasons because it is the same fix.
_CLI_UPDATE_HINTS = {
    "codex": "npm install -g @openai/codex",
    "claude": "npm install -g @anthropic-ai/claude-code",
    "copilot": "npm install -g @github/copilot",
    "gemini": "npm install -g @google/gemini-cli",
}


def _path(project_root: Path) -> Path:
    return state_dir(project_root) / "health" / "provider_blocks.json"


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


def _clean_provider(provider: str) -> str:
    return str(provider or "").strip().lower()


def reason_for(status: str = "", error: Any = None) -> str:
    """Map a pipeline status / structured error onto a block reason slug.

    Returns ``""`` when the failure is transient or user-fixable by retrying —
    those belong to the reliability memory, not here.
    """
    code = ""
    if isinstance(error, dict):
        code = str(error.get("code") or "").upper()
    if code == "PROVIDER_CLI_OUTDATED":
        return "cli_outdated"
    if code == "CONFIG_INVALID":
        return "config_invalid"
    if str(status or "").strip() == "capability_mismatch":
        return "no_scoped_edits"
    return ""


def record_block(
    project_root: Path,
    provider: str,
    reason: str,
    *,
    now: float | None = None,
) -> None:
    """Remember that ``provider`` deterministically cannot serve requests.

    Unknown reasons are ignored rather than stored, so a caller mistake can
    never persist free-form text. Best-effort; never raises.
    """
    provider = _clean_provider(provider)
    reason = str(reason or "").strip().lower()
    if not provider or provider in {"auto", "local"} or reason not in BLOCK_REASONS:
        return
    ts = time.time() if now is None else float(now)
    try:
        store = _load(project_root)
        entry = store.get(provider)
        if not isinstance(entry, dict):
            entry = {}
        entry["reason"] = reason
        entry["at"] = ts
        store[provider] = entry
        _save(project_root, store)
    except OSError:
        return


def clear_block(project_root: Path, provider: str) -> None:
    """A real call succeeded (or the user fixed it) — drop any block."""
    provider = _clean_provider(provider)
    if not provider:
        return
    try:
        store = _load(project_root)
        if provider not in store:
            return
        store.pop(provider, None)
        _save(project_root, store)
    except OSError:
        return


def active_block(
    project_root: Path, provider: str, *, now: float | None = None
) -> dict[str, Any] | None:
    """The live block for ``provider``, or ``None`` when it can be used.

    Expired blocks return ``None`` so a fix made outside Vesta is rediscovered
    without the user having to clear anything.
    """
    provider = _clean_provider(provider)
    if not provider:
        return None
    entry = _load(project_root).get(provider)
    if not isinstance(entry, dict):
        return None
    ts = time.time() if now is None else float(now)
    return _active_block_from_entry(provider, entry, ts)


def _active_block_from_entry(
    provider: str, entry: dict[str, Any], now: float
) -> dict[str, Any] | None:
    reason = str(entry.get("reason") or "")
    spec = BLOCK_REASONS.get(reason)
    if not spec:
        return None
    try:
        at = float(entry.get("at") or 0.0)
    except (TypeError, ValueError):
        at = 0.0
    if (now - at) > float(spec["ttl_seconds"]):
        return None
    remedy = str(spec["remedy"])
    update_hint = _CLI_UPDATE_HINTS.get(provider)
    if reason == "cli_outdated" and update_hint:
        remedy = f"Update the CLI (`{update_hint}`), then pick this model again."
    return {
        "provider": provider,
        "reason": reason,
        "scope": str(spec["scope"]),
        "title": str(spec["title"]),
        "remedy": remedy,
        "at": at,
        "expiresAt": at + float(spec["ttl_seconds"]),
    }


def is_blocked(
    project_root: Path,
    provider: str,
    *,
    needs_edit: bool = False,
    now: float | None = None,
) -> bool:
    """True when ``provider`` cannot serve a request of this kind.

    An ``"edit"``-scoped block only applies when ``needs_edit`` is True, so a
    write-incapable provider stays a perfectly good Ask/Plan option.
    """
    block = active_block(project_root, provider, now=now)
    if not block:
        return False
    if block["scope"] == "edit":
        return bool(needs_edit)
    return True


def block_message(
    project_root: Path,
    provider: str,
    *,
    needs_edit: bool = False,
    now: float | None = None,
) -> str:
    """One user-facing sentence explaining the block, or ``""`` when usable."""
    block = active_block(project_root, provider, now=now)
    if not block or (block["scope"] == "edit" and not needs_edit):
        return ""
    return f"{block['title']} {block['remedy']}".strip()


def blocked_providers(
    project_root: Path, *, needs_edit: bool = False, now: float | None = None
) -> dict[str, dict[str, Any]]:
    """Every live block, keyed by provider — for routing and diagnostics."""
    ts = time.time() if now is None else float(now)
    store = _load(project_root)
    out: dict[str, dict[str, Any]] = {}
    for provider, entry in store.items():
        if not isinstance(entry, dict):
            continue
        provider = _clean_provider(str(provider))
        block = _active_block_from_entry(provider, entry, ts)
        if not block:
            continue
        if block["scope"] == "edit" and not needs_edit:
            continue
        out[str(provider)] = block
    return out
