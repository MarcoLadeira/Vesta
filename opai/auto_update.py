"""Opt-in automatic updates (the Settings toggle).

Manual updating already worked: Settings shows a card, the user presses
"Update now", and :mod:`opai.updater` fast-forwards the checkout. This module
is the unattended version of that decision — and the whole design question is
what "unattended" is allowed to mean.

**It never touches uncommitted work.** ``apply_update(force=True)`` stashes
local changes, updates, and restores them. That is a reasonable thing to offer
behind a button the user just pressed, because they are present and can see
what happened. It is not a reasonable thing to do to someone who enabled a
toggle a month ago and is not watching: an automatic stash/pop can conflict, and
a conflict in *their* uncommitted work, triggered by a background task, is a bad
trade for saving them one click.

So auto-update applies only a clean fast-forward. A dirty tree is reported as
blocked, and the existing "Update anyway" affordance in Settings stays exactly
where it was — the user keeps that choice, they just make it deliberately.

The update is also never applied silently in the sense of pretending nothing
happened: the running process still has the old code in memory, so the result
always reports that a restart is required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

#: Outcomes, so callers branch on a value rather than parsing prose.
SKIPPED_DISABLED = "disabled"
SKIPPED_UNKNOWN = "check_unavailable"
UP_TO_DATE = "up_to_date"
APPLIED = "applied"
BLOCKED_DIRTY = "blocked_dirty"
FAILED = "failed"


def _result(
    outcome: str,
    *,
    message: str = "",
    restart_required: bool = False,
    check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "applied": outcome == APPLIED,
        "restart_required": restart_required,
        "message": message,
        "check": check or {},
    }


def run_auto_update(
    project_root: Path,
    *,
    enabled: bool,
    check: Callable[..., dict[str, Any]] | None = None,
    apply: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Check for an update and, if the tree is clean, apply it.

    ``check``/``apply`` are injected so this is testable without a network or a
    real checkout; they default to :mod:`opai.updater`.
    """

    if not enabled:
        return _result(SKIPPED_DISABLED)

    if check is None or apply is None:
        from opai.updater import apply_update, check_for_update

        check = check or check_for_update
        apply = apply or apply_update

    try:
        status = check(project_root)
    except Exception as exc:  # noqa: BLE001 - a background check must not crash boot
        return _result(SKIPPED_UNKNOWN, message=str(exc)[:200])

    if not isinstance(status, dict) or not status.get("checked"):
        reason = ""
        if isinstance(status, dict):
            reason = str(status.get("reason") or "")
        return _result(
            SKIPPED_UNKNOWN,
            message=reason or "Update status is unknown right now.",
            check=status if isinstance(status, dict) else {},
        )

    if status.get("up_to_date"):
        return _result(UP_TO_DATE, check=status)

    try:
        # force=False is the entire safety property of this module. It must not
        # become configurable: "automatic" and "rewrite the user's uncommitted
        # work" are separate decisions, and only one of them was opted into.
        applied = apply(project_root, force=False)
    except Exception as exc:  # noqa: BLE001
        return _result(FAILED, message=str(exc)[:200], check=status)

    if not isinstance(applied, dict):
        return _result(FAILED, message="The updater returned no result.", check=status)

    if applied.get("ok"):
        return _result(
            APPLIED,
            message=str(applied.get("message") or "OPai updated."),
            restart_required=True,
            check=status,
        )

    if applied.get("dirty"):
        return _result(
            BLOCKED_DIRTY,
            message=(
                "An update is available, but this checkout has uncommitted "
                "changes. Automatic updates never touch your local work — "
                "update from Settings when you are ready."
            ),
            check=status,
        )

    return _result(
        FAILED,
        message=str(applied.get("error") or "The update could not be applied."),
        check=status,
    )


def auto_update_enabled(project_root: Path) -> bool:
    """Whether this workspace opted in. Never raises — absent means off."""

    try:
        from opaihub.gui_preferences import load_gui_preferences

        return bool(load_gui_preferences(project_root).get("auto_update"))
    except Exception:  # noqa: BLE001 - an unreadable preference is not consent
        return False
