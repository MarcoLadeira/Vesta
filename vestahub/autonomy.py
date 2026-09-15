"""Central autonomy decision: one effective-mode rule for every surface (#137).

Every surface - web GUI, classic GUI, headless boot, CLI, proxy, account
runners - resolves the effective mode through :func:`effective_mode`, so they
cannot disagree about which mode is in force.

A chosen mode is the mode, and it survives a restart. Full Auto used to be
conditional: honoured only while a separate "pinned" flag and a recorded
acknowledgement were both present, and silently downgraded to Safe Auto
otherwise. The intent was that a stale default could never reopen with more
authority than expected. What it produced was a modal on every launch and a
setting that did not persist -- the durable default was overruled on the way
back in, so the app forgot a deliberate choice every single time. A setting
that does not stick is not a safety feature; it is a bug that trains people
to click through warnings.

Authority is still a deliberate act: it takes an explicit selection, from a
menu that says plainly what each mode does, and Full Auto sits apart from the
graded modes precisely so it cannot be reached by drifting one step at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

SAFE_MODE = "safe-auto"
FULL_AUTO = "full-auto"
# Ordered strictly-to-permissively, and every surface renders them in this
# order. "approve-edits" sits below "safe-auto" because it is *stricter*:
# it asks before even the curated safe commands that safe-auto runs.
VALID_MODES = (
    "ask",
    "plan",
    "approve-edits",
    "safe-auto",
    "auto-edits",
    "full-auto",
)

# The single source of truth for human-readable run-mode labels (#400). Every
# surface — web GUI, classic GUI, pipeline, CLI — must consume these rather than
# hardcoding its own copy, so a renamed mode can never say one thing in the
# composer and another in the receipt. The web front-end receives them via the
# boot payload's ``modes`` list (built in ``gui_web.boot_payload``); the JS
# mirror in ``settings.js`` is kept in sync by a CI contract test.
MODE_LABELS = {
    "ask": "Ask",
    "plan": "Plan",
    # Claude Code's names, because these are Claude Code's levels and calling
    # the same thing by a different word is how the two drifted apart before.
    "approve-edits": "Manual",
    "safe-auto": "Auto",
    # Named after Claude Code's accept-edits, because it is the same thing:
    # local work proceeds, outward-facing work still asks. The autonomy level
    # it maps to has existed since command_policy was written; until now no
    # mode reached it, so the one level a user most wants was unselectable.
    "auto-edits": "Accept Edits",
    "full-auto": "Bypass Permissions",
}

# Back-compat alias for the previously-private name.
_MODE_LABELS = MODE_LABELS


def mode_label(mode: str) -> str:
    """Human-readable label for a run mode, falling back to the raw id."""

    return MODE_LABELS.get(mode, mode)


def is_full_auto_pinned(prefs: Mapping[str, Any]) -> bool:
    """Whether Full Auto is the stored default.

    Kept because several surfaces still ask the question, but it no longer
    gates anything: "pinned" now means only "this is the mode you chose", not
    "you have earned the right to keep it". The separate acknowledgement flag
    is still read so an existing preferences file keeps answering the same way.
    """

    if prefs.get("full_auto_pinned") is True:
        return True
    return str(prefs.get("default_mode") or "") == FULL_AUTO


@dataclass(frozen=True)
class AutonomyDecision:
    requested_mode: str
    effective_mode: str
    full_auto_pinned: bool
    downgraded: bool
    reason: str

    @property
    def effective_label(self) -> str:
        return _MODE_LABELS.get(self.effective_mode, self.effective_mode)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "effective_label": self.effective_label,
            "full_auto_pinned": self.full_auto_pinned,
            "downgraded": self.downgraded,
            "reason": self.reason,
        }


def effective_mode(requested: str | None, prefs: Mapping[str, Any]) -> AutonomyDecision:
    """Resolve the effective run mode from a requested mode and preferences.

    A valid mode is returned unchanged, every time, however it was reached --
    a request for this run, or the durable default from a previous one. That
    is the whole point: the mode a user picked is the mode they get back after
    a restart, a reboot, or a week away.

    Only an unrecognised id falls back, and that is not a policy judgement:
    there is nothing else to do with a mode no surface can render. This is the
    single authority; UI focus and stored defaults are inputs, never overrides.
    """

    mode = str(requested or prefs.get("default_mode") or SAFE_MODE)
    if mode not in VALID_MODES:
        return AutonomyDecision(
            mode,
            SAFE_MODE,
            False,
            True,
            f"unknown mode {mode!r}; using Safe Auto",
        )
    return AutonomyDecision(mode, mode, mode == FULL_AUTO, False, "")


def resolve_startup_mode(prefs: Mapping[str, Any]) -> AutonomyDecision:
    """The effective mode a surface should boot into from stored preferences."""

    return effective_mode(None, prefs)
