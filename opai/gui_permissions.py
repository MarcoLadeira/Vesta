"""Tool-permission view derived from the *real* run mode — not a mock panel.

The control panel shows the user exactly what the AI may do in the current run
mode, with three states: ``allow`` (happens without asking), ``ask`` (you
confirm first), ``block`` (refused in this mode). The mapping mirrors what OPai
actually enforces:

* Ask / Plan are read-only — the runner is built with ``allow_edits=False``.
* Safe Auto allows the curated ``safe_auto.allow_commands`` and asks before
  edits; it also *asks* before an arbitrary command instead of hard-refusing
  it (the in-context ``needs_command_approval`` confirmation is the escalation
  path, F17); destructive commands and network sit in ``deny_commands``.
* Approve Edits allows reads, asks before every edit.
* Full Auto allows edits and commands but still flags destructive actions.

Because it is computed from the same mode + preferences the engine uses, the
panel can't drift into a comforting lie. Qt-free and unit-tested.
"""

from __future__ import annotations

from typing import Any

# Capability ids the panel reports on, in display order.
CAPABILITIES: list[tuple[str, str]] = [
    ("read", "Read files"),
    ("search", "Search code"),
    ("run_safe", "Run safe commands"),
    ("edit", "Edit files"),
    ("create", "Create files"),
    ("run_any", "Run any command"),
    ("delete", "Delete files"),
    ("network", "Network / web"),
]

READ_ONLY_MODES = {"ask", "plan"}

# Per-mode capability state. Anything not listed for a mode defaults to "block".
_MODE_RULES: dict[str, dict[str, str]] = {
    "ask": {"read": "allow", "search": "allow"},
    "plan": {"read": "allow", "search": "allow"},
    "safe-auto": {
        "read": "allow",
        "search": "allow",
        "run_safe": "allow",
        "edit": "ask",
        "create": "ask",
        # F17: an arbitrary command is confirmable in-context (the same
        # approval flow as edits), not a hard block with no way forward.
        "run_any": "ask",
        "delete": "block",
        "network": "block",
    },
    "approve-edits": {
        "read": "allow",
        "search": "allow",
        "run_safe": "ask",
        "edit": "ask",
        "create": "ask",
        "run_any": "ask",
        "delete": "block",
        "network": "block",
    },
    "full-auto": {
        "read": "allow",
        "search": "allow",
        "run_safe": "allow",
        "edit": "allow",
        "create": "allow",
        "run_any": "allow",
        "delete": "ask",
        "network": "ask",
    },
}

_STATE_NOTE = {
    "allow": "Runs without asking",
    "ask": "Asks you first",
    "block": "Blocked in this mode",
}


def permissions_for(
    run_mode: str, *, safe_auto: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    """Return permission rows for ``run_mode`` as ``{id,label,state,note}``.

    ``safe_auto`` (from GUI preferences) refines the note for ``run_safe`` by
    naming a couple of the allowed commands, so the user sees the concrete
    allow-list rather than a vague label.
    """
    rules = _MODE_RULES.get(str(run_mode), _MODE_RULES["ask"])
    allow_cmds = []
    if isinstance(safe_auto, dict):
        allow_cmds = [str(c) for c in (safe_auto.get("allow_commands") or [])][:3]
    rows: list[dict[str, str]] = []
    for cap_id, label in CAPABILITIES:
        state = rules.get(cap_id, "block")
        note = _STATE_NOTE[state]
        if cap_id == "run_safe" and state == "allow" and allow_cmds:
            note = "e.g. " + ", ".join(allow_cmds)
        rows.append({"id": cap_id, "label": label, "state": state, "note": note})
    return rows


def is_read_only(run_mode: str) -> bool:
    return str(run_mode) in READ_ONLY_MODES


def permission_summary(run_mode: str) -> str:
    """A one-line summary for the header/inspector, e.g. '2 allowed · 3 ask · 3 blocked'."""
    rows = permissions_for(run_mode)
    counts = {"allow": 0, "ask": 0, "block": 0}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return (
        f"{counts['allow']} allowed · {counts['ask']} ask · {counts['block']} blocked"
    )
