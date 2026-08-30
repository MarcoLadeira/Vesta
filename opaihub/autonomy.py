"""Central autonomy decision: one effective-mode rule for every surface (#137).

Full Auto grants broad edit/command authority, so it may only be the
effective mode when the user has *explicitly pinned* it with a durable
acknowledgement. Every surface - web GUI, classic GUI, headless boot, CLI,
proxy, account runners - resolves the effective mode through
:func:`effective_mode` here, so a stale persisted ``full-auto`` can never
silently reopen with more authority than the user expects. Full Auto stays
available, but it is opt-in, visibly dangerous, and one unpin from off.
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
    "approve-edits": "Approve Edits",
    "safe-auto": "Safe Auto",
    # Named after Claude Code's accept-edits, because it is the same thing:
    # local work proceeds, outward-facing work still asks. The autonomy level
    # it maps to has existed since command_policy was written; until now no
    # mode reached it, so the one level a user most wants was unselectable.
    "auto-edits": "Auto-Accept Edits",
    "full-auto": "Full Auto",
}

# Back-compat alias for the previously-private name.
_MODE_LABELS = MODE_LABELS


def mode_label(mode: str) -> str:
    """Human-readable label for a run mode, falling back to the raw id."""

    return MODE_LABELS.get(mode, mode)


def is_full_auto_pinned(prefs: Mapping[str, Any]) -> bool:
    """Full Auto is pinned only with the flag *and* a recorded acknowledgement."""

    acknowledgement = prefs.get("full_auto_acknowledged_at")
    return prefs.get("full_auto_pinned") is True and bool(
        acknowledgement.strip() if isinstance(acknowledgement, str) else ""
    )


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

    A request (or stored default) of ``full-auto`` is honored only when Full
    Auto is pinned with acknowledgement; otherwise it is downgraded to Safe
    Auto. Unknown modes also fall back to Safe Auto. This is the single
    authority; UI focus and stored defaults are inputs, never overrides.
    """

    pinned = is_full_auto_pinned(prefs)
    mode = str(requested or prefs.get("default_mode") or SAFE_MODE)
    if mode not in VALID_MODES:
        return AutonomyDecision(
            mode, SAFE_MODE, pinned, True, f"unknown mode {mode!r}; using Safe Auto"
        )
    if mode == FULL_AUTO and not pinned:
        return AutonomyDecision(
            mode,
            SAFE_MODE,
            pinned,
            True,
            "Full Auto is not pinned; using Safe Auto until you pin it",
        )
    return AutonomyDecision(mode, mode, pinned, False, "")


def resolve_startup_mode(prefs: Mapping[str, Any]) -> AutonomyDecision:
    """The effective mode a surface should boot into from stored preferences."""

    return effective_mode(None, prefs)
