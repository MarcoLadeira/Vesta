"""Vesta brand — the one canonical source of product identity and voice.

Every surface (web GUI via the boot payload, Qt fallback, CLI, docs) reads its
product copy from here so Vesta speaks with one voice everywhere. Change copy in
this file, and both the app and the terminal change together.

Positioning: **the cost-aware AI coding cockpit** — one command center over
Claude, Codex, Copilot, and local models. Promise: *every step visible, every
dollar accounted.* Voice: a senior engineer who respects your time — calm,
honest, technical, protective; never hype, never fake precision.
"""

from __future__ import annotations

import importlib.resources as _resources
import random
from pathlib import Path as _Path

NAME = "Vesta"
CATEGORY = "cost-aware AI coding cockpit"
TAGLINE = "Every step visible. Every dollar accounted."
POSITIONING = (
    "Vesta is the cost-aware AI coding cockpit: one command center over Claude, "
    "Codex, Copilot, and local models — every step visible, every dollar "
    "accounted."
)
PROMISE = (
    "Vesta plans, routes to the cheapest capable model, shows every step while "
    "it works, and hands you an honest receipt."
)

# The empty-state moment — the first thing a user reads.
#
# It used to be four blocks of text stacked above the actions: an eyebrow
# repeating the product name, a headline, and a twenty-five word paragraph
# explaining the mechanism. Nobody reads a mechanism before they have a
# reason to care.
#
# Then it was a claim -- "Better. Faster. Cheaper." -- which is what any tool
# says about itself. The headline is now Vesta's motto: flame, light, and
# beginnings, drawn from these lines. Edit the list here and nowhere else.
VESTA_MOTTOS: tuple[str, ...] = (
    "The Living Flame.",
    "By a Light That Never Fails.",
    "Keep the Intelligence Burning.",
    "Intelligence, Always Burning.",
    "The Fire Behind Your Work.",
    "An Undying Intelligence.",
    "Where Intelligence Comes Alive.",
    "Intelligence Comes First.",
    "First, Vesta.",
    "Where Every Task Begins.",
    "Conceive of Vesta as naught but the living flame.",
    "An undying fire is hidden in that temple.",
    "Vesta guards it, because she sees all things by her light that never fails.",
    "Guardian of Fire.",
    "She Occupies the First Place.",
)


def _draw_motto() -> str:
    """One motto, every line equally likely."""
    return random.choice(VESTA_MOTTOS)  # nosec B311 - display copy, not a secret


# Drawn once, when this process first imports the brand -- the import lock
# makes that exactly once. Every window, reload, and re-render in this run
# reads the same line; closing Vesta and opening it again draws afresh.
_SESSION_MOTTO = _draw_motto()


def empty_title() -> str:
    """This launch's motto for the empty state, stable for the whole run."""
    return _SESSION_MOTTO


# There is no body and no hint any more, and their absence is the point.
#
# The body was a second sentence describing the product to someone who is
# looking at it. The hint taught a keyboard shortcut to someone who has not
# yet done the thing the shortcut is for. Neither survives the question this
# screen should be asked: does this line change what the reader does next?
#
# What is left is a mark, a motto, and three things you can click. The one
# sentence that *does* change what you do next -- that no provider is
# connected, so nothing will run -- is still shown, by the front end, only
# when it is true.

COMPOSER_PLACEHOLDER = "Tell Vesta what to build, fix, or explain…"

# Voice rules the copy in this file (and new copy elsewhere) must follow.
VOICE = {
    "is": ("calm", "honest", "technical", "protective", "builder-first"),
    "is_not": ("hype", "corporate fluff", "fake magic", "fake precision"),
    "cost_rule": "Estimates are labeled estimated; paid calls are never shown as savings.",
    "error_rule": "Say what happened and what to do next. Never a bare 'something went wrong'.",
}

# Provider display names — Vesta-first language, no raw internal ids in copy.
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
    parts = ["vesta", "ask"]
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
        "emptyTitle": empty_title(),
        "composerPlaceholder": COMPOSER_PLACEHOLDER,
    }


APP_ICON_NAME = "opai-icon.png"


def app_icon_path():
    """Absolute path to the packaged square app icon, or ``None`` if absent.

    Resolved through ``importlib.resources`` so it works from an installed wheel
    or a closed-source artifact, not just a source checkout (#148). Vesta ships as
    a normal (unzipped) wheel, so the resource is a real filesystem path that
    stays valid after this returns. Never raises: a missing icon simply degrades
    to the default window icon.
    """
    try:
        resource = _resources.files("opai") / "assets" / APP_ICON_NAME
        if resource.is_file():
            return _Path(str(resource))
    except (FileNotFoundError, ModuleNotFoundError, TypeError, OSError):
        return None
    return None
