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
        "id": "prompts",
        "label": "Open prompt library",
        "hint": "Ctrl+P",
        "keywords": "templates saved prompts snippets examples",
    },
    {
        "id": "inspector",
        "label": "Toggle control panel",
        "hint": "Ctrl+I",
        "keywords": "inspector session right panel controls permissions",
    },
    {
        "id": "workspace",
        "label": "Open project folder",
        "hint": "Ctrl+O",
        "keywords": "workspace project directory switch open folder",
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
    ("Ctrl+P", "Open prompt library"),
    ("Ctrl+I", "Toggle control panel"),
    ("Ctrl+O", "Open project folder"),
    ("Ctrl+B", "Toggle sidebar"),
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
    "gpt": {"speed": "fast", "quality": "high", "cost": "$$"},
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


# --------------------------------------------------------------------------- #
# Privacy badges + session inspector (right control panel)
# --------------------------------------------------------------------------- #
def privacy_badges(*, model_kind: str | None, connected: bool) -> list[dict[str, str]]:
    """What the AI can and can't reach right now — never vague.

    ``tone`` is one of safe/info/warn so the panel can colour each badge.
    """
    badges: list[dict[str, str]] = [
        {"label": "No telemetry", "tone": "safe"},
        {"label": "Raw build prompts not logged", "tone": "safe"},
    ]
    if model_kind == "local":
        badges.insert(0, {"label": "Local only · private", "tone": "safe"})
    elif model_kind == "auto":
        badges.insert(0, {"label": "Local-first · cloud on confirm", "tone": "info"})
    else:
        badges.insert(0, {"label": "Cloud model · paid", "tone": "warn"})
    badges.append(
        {
            "label": "Account connected" if connected else "No account connected",
            "tone": "info" if connected else "warn",
        }
    )
    return badges


def _f2(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def live_agent_mode_row(run_mode: str | None, focus: str | None) -> dict[str, str]:
    """Inspector row with the LIVE agent-mode preview for the next run (F21).

    The persisted ``Agent mode`` row reports the *last completed* run and is
    stale until the next one finishes. This row is computed live from the
    current run mode + focus via ``gui_modes.describe_controls`` — the single
    source of truth (F20) — so web and classic always show the same preview.
    """
    from opai.gui_modes import describe_controls

    controls = describe_controls(run_mode, focus)
    return {"label": "Agent mode (next run)", "value": controls["agent_mode_label"]}


def session_inspector(
    *,
    model_label: str,
    model_kind: str | None,
    run_mode_label: str,
    task_summary: dict[str, Any] | None,
    inspector: dict[str, Any] | None,
    permission_summary: str,
    connected: bool,
) -> dict[str, Any]:
    """Compose the right-panel inspector payload from already-computed pieces.

    Pure: it just arranges display rows + a budget meter + privacy badges. The
    window renders this; the numbers come from ``app_state.inspector_state`` so
    they are real, not decorative.
    """
    ins = inspector or {}
    budget = ins.get("budget") or {}
    workspace = ins.get("workspace") or {}
    task = task_summary or {}
    spent = _f2(budget.get("spent_today"))
    limit = budget.get("daily_limit")
    pct = int(budget.get("pct") or 0)
    if isinstance(limit, (int, float)) and limit > 0:
        budget_text = f"${spent:.2f} / ${float(limit):.2f} today"
    else:
        budget_text = f"${spent:.2f} today · no cap"
    rows = [
        {"label": "Model", "value": str(model_label or "Auto").split(" · ")[0]},
        {"label": "Run mode", "value": str(run_mode_label or "Ask")},
        {"label": "Task focus", "value": str(task.get("focus") or "Build")},
        {"label": "Output", "value": str(task.get("format") or "Normal")},
        {"label": "Workspace", "value": str(workspace.get("text") or "—")},
        {"label": "Permissions", "value": permission_summary},
    ]
    return {
        "rows": rows,
        "budget": {
            "text": budget_text,
            "pct": max(0, min(100, pct)),
            "panic": bool(budget.get("panic")),
        },
        "privacy": privacy_badges(model_kind=model_kind, connected=connected),
        "read_only": bool(task.get("read_only")),
    }


def workflow_summary(result: dict[str, Any]) -> list[str]:
    """Compact, truthful footer fields for a completed coding-agent turn."""

    policy = result.get("agent_policy") or {}
    workflow = result.get("workflow") or {}
    pretty = lambda value: str(value or "").replace("_", " ")  # noqa: E731
    parts: list[str] = []
    mode = str(policy.get("label") or policy.get("mode") or workflow.get("mode") or "")
    if mode:
        parts.append(mode.title())
    phase = pretty(workflow.get("phase"))
    if phase:
        parts.append(phase.title())
    tests = pretty(workflow.get("tests_status"))
    if tests and tests != "not run":
        parts.append(f"Tests: {tests}")
    if workflow.get("pr_url"):
        parts.append(str(workflow["pr_url"]))
    merge = pretty(workflow.get("merge_status"))
    if merge and merge != "not requested":
        parts.append(f"Merge: {merge}")
    return parts
