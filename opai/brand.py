"""OPai brand — the one canonical source of product identity and voice.

Every surface (web GUI via the boot payload, Qt fallback, CLI, docs) reads its
product copy from here so OPai speaks with one voice everywhere. Change copy in
this file, and both the app and the terminal change together.

Positioning: **the cost-aware AI coding cockpit** — one command center over
Claude, Codex, Copilot, and local models. Promise: *every step visible, every
dollar accounted.* Voice: a senior engineer who respects your time — calm,
honest, technical, protective; never hype, never fake precision.
"""

from __future__ import annotations

NAME = "OPai"
CATEGORY = "cost-aware AI coding cockpit"
TAGLINE = "Every step visible. Every dollar accounted."
POSITIONING = (
    "OPai is the cost-aware AI coding cockpit: one command center over Claude, "
    "Codex, Copilot, and local models — every step visible, every dollar "
    "accounted."
)
PROMISE = (
    "OPai plans, routes to the cheapest capable model, shows every step while "
    "it works, and hands you an honest receipt."
)

# The empty-state moment — the first thing a user reads. Ownable, cost-aware,
# builder-first; not the generic "What do you want to build?" every tool asks.
EMPTY_TITLE = "Build more. Burn less."
EMPTY_BODY = (
    "Tell OPai the goal. It plans, routes to the cheapest capable model, "
    "shows every step, and hands you the receipt."
)
EMPTY_HINT = "Press Ctrl+K for commands"

COMPOSER_PLACEHOLDER = "Tell OPai what to build, fix, or explain…"

# Voice rules the copy in this file (and new copy elsewhere) must follow.
VOICE = {
    "is": ("calm", "honest", "technical", "protective", "builder-first"),
    "is_not": ("hype", "corporate fluff", "fake magic", "fake precision"),
    "cost_rule": "Estimates are labeled estimated; paid calls are never shown as savings.",
    "error_rule": "Say what happened and what to do next. Never a bare 'something went wrong'.",
}

# Provider display names — OPai-first language, no raw internal ids in copy.
_PROVIDER_NAMES = {
    "claude": "Claude",
    "codex": "Codex",
    "copilot": "Copilot",
    "auto": "Auto",
    "local": "Local",
}


def provider_display(provider_id: str | None) -> str:
    """Human display name for a provider id ('claude' -> 'Claude')."""
    key = str(provider_id or "").strip().lower()
    return _PROVIDER_NAMES.get(key, key.capitalize() or "Auto")


def cli_header(mode: str, model_id: str) -> str:
    """The one-line branded header for a streaming CLI run."""
    return f"{NAME} · {mode} · {model_id}"


def cli_mirror(model: str | None, mode: str | None, task: str = "") -> str:
    """The terminal equivalent of a GUI selection — the CLI Mirror.

    GUI/CLI parity is a brand promise: anything you do in the app has a
    terminal twin. The mirror shows it, ready to copy.
    """
    shorthand = _shorthand(model)
    parts = ["opai", "ask"]
    if shorthand and shorthand != "auto":
        parts += ["--model", shorthand]
    elif shorthand == "auto":
        parts += ["--model", "auto"]
    if mode and mode != "ask":
        parts += ["--mode", str(mode)]
    prompt = (task or "").strip().replace('"', "'")
    if len(prompt) > 60:
        prompt = prompt[:57] + "..."
    parts.append(f'"{prompt or "<your task>"}"')
    return " ".join(parts)


def _shorthand(model: str | None) -> str:
    """Reverse of the CLI's normalize_model_choice: full id -> friendly short."""
    value = str(model or "auto").strip()
    if value.startswith("account:"):
        return value[len("account:") :]
    return value or "auto"


def boot_brand() -> dict[str, str]:
    """The brand block the GUI boot payload carries — one voice, every surface."""
    return {
        "name": NAME,
        "tagline": TAGLINE,
        "emptyTitle": EMPTY_TITLE,
        "emptyBody": EMPTY_BODY,
        "emptyHint": EMPTY_HINT,
        "composerPlaceholder": COMPOSER_PLACEHOLDER,
    }
