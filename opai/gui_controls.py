"""Qt-free data + formatting for the OPai desktop GUI controls.

Kept PySide-free and dependency-light so the command palette, model badges,
header status strip, keyboard-shortcut help, and the empty/thinking/error
messages are unit-testable without a display. The widgets in ``gui_desktop.py``
consume these; the logic lives here so it can be tested headlessly (and so CI
without the desktop extra still covers it).

This is the "tasteful, stay simple" control layer: it adds clarity and a
command palette without turning the calm chat into a busy IDE.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Command palette (Ctrl+K)
# --------------------------------------------------------------------------- #
# Each command maps to an action the window already supports. ``hint`` is the
# shortcut shown on the right; ``keywords`` widen fuzzy matching.
COMMANDS: list[dict[str, str]] = [
    {
        "id": "new_chat",
        "label": "New chat",
        "hint": "Ctrl+N",
        "keywords": "reset clear conversation start",
    },
    {
        "id": "focus_input",
        "label": "Focus prompt",
        "hint": "Ctrl+L",
        "keywords": "compose write input message",
    },
    {
        "id": "stop",
        "label": "Stop generation",
        "hint": "Esc",
        "keywords": "cancel halt abort",
    },
    {
        "id": "change_model",
        "label": "Change model",
        "hint": "Ctrl+M",
        "keywords": "provider claude codex local auto",
    },
    {
        "id": "change_mode",
        "label": "Change mode",
        "hint": "",
        "keywords": "ask plan safe auto full",
    },
    {
        "id": "connect",
        "label": "Connect accounts",
        "hint": "",
        "keywords": "account claude codex sign in login",
    },
    {
        "id": "settings",
        "label": "Open settings",
        "hint": "",
        "keywords": "budget preferences config limit",
    },
    {
        "id": "savings",
        "label": "Show savings",
        "hint": "",
        "keywords": "cost ledger receipt spend saved",
    },
    {
        "id": "doctor",
        "label": "Run doctor",
        "hint": "",
        "keywords": "health check clients status",
    },
    {
        "id": "shortcuts",
        "label": "Keyboard shortcuts",
        "hint": "?",
        "keywords": "keys help bindings hotkeys",
    },
]


def filter_commands(
    query: str, commands: list[dict[str, str]] | None = None
) -> list[dict[str, str]]:
    """Return commands matching every whitespace token in ``query`` (AND match).

    Matches against label + keywords, case-insensitively. Empty query returns
    all commands in declared order.
    """
    pool = COMMANDS if commands is None else commands
    q = (query or "").strip().lower()
    if not q:
        return list(pool)
    tokens = q.split()
    out: list[dict[str, str]] = []
    for cmd in pool:
        hay = f"{cmd.get('label', '')} {cmd.get('keywords', '')}".lower()
        if all(tok in hay for tok in tokens):
            out.append(cmd)
    return out


# --------------------------------------------------------------------------- #
# Keyboard shortcuts (shown in the help panel; registered in the window)
# --------------------------------------------------------------------------- #
SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl+K", "Open command palette"),
    ("Ctrl+N", "New chat"),
    ("Ctrl+Enter", "Send prompt"),
    ("Esc", "Stop generation"),
    ("Ctrl+L", "Focus prompt input"),
    ("Ctrl+M", "Change model"),
    ("?", "Show keyboard shortcuts"),
]


# --------------------------------------------------------------------------- #
# Model capability badges
# --------------------------------------------------------------------------- #
# [ASSUMPTION] The engine does not expose per-model speed/quality/cost, so these
# are curated defaults by model family. Tune as providers change. They power a
# tooltip on each model in the picker — clarity without clutter.
_MODEL_TIERS: dict[str, dict[str, str]] = {
    "opus": {"speed": "slower", "quality": "highest", "cost": "$$$"},
    "sonnet": {"speed": "fast", "quality": "high", "cost": "$$"},
    "haiku": {"speed": "fastest", "quality": "good", "cost": "$"},
    "codex": {"speed": "fast", "quality": "high", "cost": "$$"},
}


def model_badge(option: dict[str, Any]) -> str:
    """A short capability badge for a model picker option.

    Auto and local get honest, distinctive badges; account models map to a
    family tier; anything unknown falls back to a safe 'cloud · paid'.
    """
    kind = option.get("kind")
    if kind == "auto":
        return "routes cheapest · $0 when local"
    if kind == "local":
        return "local · free · private"
    name = str(option.get("model") or option.get("id") or "").lower()
    for key, tier in _MODEL_TIERS.items():
        if key in name:
            return f"{tier['speed']} · {tier['quality']} · {tier['cost']}"
    return "cloud · paid"


# --------------------------------------------------------------------------- #
# Header status strip
# --------------------------------------------------------------------------- #
def header_status(
    model_label: str,
    mode_label: str,
    spent_today: float,
    saved: float | None = None,
) -> str:
    """One calm line: which model, which mode, what it has cost today.

    Gives the user constant visibility of the AI's state without a control pane.
    """
    short = str(model_label or "Auto").split(" · ")[0].split(" (")[0].strip()
    try:
        spent = f"${float(spent_today):.2f} today"
    except (TypeError, ValueError):
        spent = "$0.00 today"
    bits = [short, str(mode_label or "Ask"), spent]
    if saved is not None:
        try:
            bits.append(f"${float(saved):.2f} saved")
        except (TypeError, ValueError):
            pass
    return "  ·  ".join(bits)


# --------------------------------------------------------------------------- #
# Empty / thinking / error states
# --------------------------------------------------------------------------- #
def empty_state() -> dict[str, str]:
    return {
        "title": "What should we build?",
        "body": (
            "Pick a model and mode below, then describe a task. OPai runs it "
            "local-first and shows what it costs."
        ),
        "hint": "Press Ctrl+K for commands",
    }


def thinking_text(model_label: str | None = None) -> str:
    short = (
        str(model_label).split(" · ")[0].split(" (")[0].strip()
        if model_label
        else "OPai"
    )
    return f"{short or 'OPai'} is working…"


_FRIENDLY_ERRORS: dict[str, str] = {
    "account_not_connected": (
        "No account connected. Run `claude` or `codex` once to sign in, then "
        "pick it in the model menu."
    ),
    "account_timeout": (
        "{model} ran past the time limit and was stopped. Try a smaller "
        "request, a faster model, or run the long task in your terminal."
    ),
    "account_error": (
        "{model} hit an error and couldn't finish. Try again, or pick a "
        "different model."
    ),
    "needs_model": (
        "Auto has no free model for this. Pick Claude or Codex in the model "
        "menu, or add a local model under Advanced."
    ),
    "needs_confirmation": (
        "This needs a paid model. Pick Claude or Codex to run it — OPai won't "
        "spend on a paid call automatically."
    ),
    "blocked": (
        "OPai stopped this because it looks risky. Switch to Full Auto only if "
        "you intend that."
    ),
    "blocked_panic": (
        "Panic mode is on (local-only). Turn it off to use a paid account."
    ),
    "runner_error": (
        "The local model couldn't answer that. Pick Claude or Codex, or check "
        "your local model is running."
    ),
}


def friendly_error(status: str, model_label: str = "the model") -> str:
    """A calm, actionable message for a failure status (never a raw dump)."""
    short = (
        str(model_label).split(" · ")[0].split(" (")[0].strip() or "the model"
        if model_label
        else "the model"
    )
    template = _FRIENDLY_ERRORS.get(status)
    if not template:
        return "Something didn't go through. Try again, or pick a different model."
    return template.format(model=short)
