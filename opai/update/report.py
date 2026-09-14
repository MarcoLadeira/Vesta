"""One rendering of updater state, for every surface that shows it.

Deliberately a *formatter*, not a second opinion. Everything here reads the
canonical payload produced by `UpdateService.status()` and turns it into
sentences; nothing recomputes a fact, reaches for a clock, or asks the update
source anything. Two places that both decide what "last checked" means is how
the first one stops being trusted, so this one cannot decide it at all.

The vocabulary is shared so the desktop, `opai update status` and `opai doctor`
describe the same installation the same way. In particular a cached answer says
so, always, and never borrows the words a fresh one uses.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _parse(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def humanise_age(value: object, *, now: datetime | None = None) -> str:
    """ "2 min ago" for a timestamp, or "" when there isn't one.

    Empty rather than "never" or "unknown": the caller knows which of those it
    means, and a formatter guessing is how "never checked" becomes "checked a
    long time ago".
    """

    moment = _parse(value)
    if moment is None:
        return ""
    reference = now or datetime.now(timezone.utc)
    seconds = int((reference - moment).total_seconds())
    if seconds < 0:
        # A clock that moved backwards. Saying "in 3 minutes" about a check
        # that already happened is worse than declining to date it.
        return "just now"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} hr ago"
    return f"{seconds // 86400} d ago"


_STATE_WORDS = {
    "up_to_date": "Up to date",
    "available": "Update available",
    "downloading": "Downloading update",
    "verifying": "Verifying update",
    "ready_to_install": "Ready to restart",
    "install_on_quit": "Installs on quit",
    "waiting_for_idle": "Waiting for active work to finish",
    "deferred": "Update deferred",
    "completed": "Updated — restart to use it",
    "unavailable": "Couldn't reach the update source",
    "policy_blocked": "Updates managed elsewhere",
    "unsupported_install": "Manual update required",
    "failed_retriable": "Update paused",
    "failed_terminal": "Update blocked",
    "rollback_pending": "Recovery required",
    "needs_attention": "Update needs attention",
    "rolled_back": "Update rolled back",
    "checking": "Checking for updates",
    "idle": "Idle",
}


def freshness_phrase(discovery: dict, *, now: datetime | None = None) -> str:
    """How the state on screen was arrived at, in the user's terms.

    This is the sentence the whole epic is about: a cached answer must never
    borrow the words a fresh one uses.
    """

    # `remote_checked_at` is newer than some persisted operations, so fall back
    # to the successful-check clock, which only ever moves on genuine contact
    # with the update source. Without this an installation that checked twenty
    # minutes ago reads "not yet checked remotely" in the headline while the
    # detail below it says otherwise -- a renderer contradicting itself is
    # exactly the failure this epic is about.
    remote_age = humanise_age(
        discovery.get("last_remote_check_at")
        or discovery.get("last_successful_remote_check_at"),
        now=now,
    )
    if discovery.get("showing_cached_result"):
        if remote_age:
            return f"cached result · remote checked {remote_age}"
        return "cached result · never checked remotely"
    if remote_age:
        return f"checked remotely {remote_age}"
    return "not yet checked remotely"


# What a person is told. Deliberately short, and deliberately not the same
# vocabulary as the diagnostics above.
#
# The update surface had grown a developer's vocabulary: "cannot update
# transactionally", "cached result - remote checked 47 min ago", "4 commits
# behind origin/main", a shell command in backticks. All of that is true and
# none of it is the user's problem. They need to know whether there is an
# update, and what to press.
#
# Timings, cache provenance, install types, sources and commands stay in
# `opai update doctor`, which is where someone debugging the updater looks.
_USER_MESSAGES = {
    "available": "A new version of Vesta is ready.",
    "downloading": "Downloading the update.",
    "verifying": "Checking the update.",
    "ready_to_install": "Restart Vesta to finish updating.",
    "completed": "Restart Vesta to finish updating.",
    "install_on_quit": "Vesta will finish updating when you close it.",
    "waiting_for_idle": "Vesta will update when the current work finishes.",
    "deferred": "The update is ready when you are.",
    "unavailable": "Vesta couldn't check for updates. It will try again.",
    "failed_retriable": "The update didn't finish. Vesta will try again.",
    "failed_terminal": "The update couldn't be verified, so it wasn't installed.",
    "policy_blocked": "Updates are managed outside Vesta.",
    "rollback_pending": "Vesta is restoring the previous version.",
    "rolled_back": "Vesta went back to the previous version.",
    "needs_attention": "The update needs your attention.",
    "up_to_date": "",
    "checking": "",
    "idle": "",
}

_USER_TITLES = {
    "available": "Update available",
    "downloading": "Updating Vesta",
    "verifying": "Updating Vesta",
    "ready_to_install": "Update ready",
    "completed": "Update ready",
    "install_on_quit": "Update ready",
    "waiting_for_idle": "Update ready",
    "deferred": "Update available",
    "unavailable": "Couldn't check for updates",
    "failed_retriable": "Update didn't finish",
    "failed_terminal": "Update blocked",
    "policy_blocked": "Managed elsewhere",
    "rollback_pending": "Restoring previous version",
    "rolled_back": "Restored previous version",
    "needs_attention": "Update needs attention",
    "up_to_date": "You're on the latest version",
    "checking": "Checking for updates",
    "idle": "",
}


def user_facing(payload: dict) -> dict:
    """Title and one sentence, in the user's language. No detail.

    `unsupported_install` is the state a source checkout reaches when it is
    behind its remote, and calling that "Manual update required" told someone
    with a working Update button that they had to do something by hand. What it
    means to them is simply that an update is available -- unless Vesta genuinely
    cannot install it, which is a different sentence.
    """

    operation = dict(payload.get("operation") or {})
    discovery = dict(payload.get("discovery") or {})
    ownership = dict(discovery.get("ownership") or {})
    state = str(operation.get("state") or "idle")

    category = str(operation.get("error_category") or "")
    rollout_messages = {
        "candidate_quarantined": "This version failed its health check. Vesta is waiting for a fixed update.",
        "rollout_quarantined": "This update was stopped after a health failure.",
        "rollout_paused": "This update is paused while its health is reviewed.",
        "rollout_yanked": "This update was withdrawn by its publisher.",
        "rollout_excluded": "This update is being released gradually and is not available to this installation yet.",
    }
    if category in rollout_messages:
        return {"title": "Update held back", "message": rollout_messages[category]}

    if state == "unsupported_install":
        if ownership.get("self_updatable") is False:
            return {
                "title": "Managed elsewhere",
                "message": "Updates for this installation are handled outside Vesta.",
            }
        return {"title": "Update available", "message": _USER_MESSAGES["available"]}

    return {
        "title": _USER_TITLES.get(state, ""),
        "message": _USER_MESSAGES.get(state, ""),
    }


def render_status_lines(
    payload: dict, *, verbose: bool = False, now: datetime | None = None
) -> list[str]:
    """The canonical facts as lines a person can read."""

    operation = dict(payload.get("operation") or {})
    discovery = dict(payload.get("discovery") or {})
    ownership = dict(discovery.get("ownership") or {})
    state = str(operation.get("state") or "idle")

    headline = _STATE_WORDS.get(state, state.replace("_", " ").capitalize())
    lines = [f"{headline} · {freshness_phrase(discovery, now=now)}"]

    diagnostic = str(operation.get("safe_diagnostic") or "")
    if diagnostic:
        lines.append(f"  {diagnostic}")

    # The one thing worth saying unprompted when someone else owns updates:
    # what they should actually run.
    if ownership and not ownership.get("self_updatable"):
        remediation = str(ownership.get("remediation") or "")
        if remediation:
            lines.append(f"  {remediation}")

    # Distinct from the asset-fingerprint staleness that produces the
    # COMPLETED state: this one is a version disagreement between the running
    # process and what release identity on disk claims.
    if discovery.get("version_differs_from_disk"):
        lines.append(
            "  Running "
            f"{discovery.get('running_version')} while "
            f"{discovery.get('disk_version')} is on disk — restart to use it."
        )

    if not verbose:
        return lines

    retry_age = humanise_age(discovery.get("next_retry_at"), now=now)
    detail = [
        ("Install type", discovery.get("install_type")),
        ("Update owner", f"{ownership.get('owner')} ({ownership.get('mechanism')})"),
        ("Update source", discovery.get("update_source")),
        ("Channel", discovery.get("channel")),
        ("Running version", discovery.get("running_version")),
        ("Version on disk", discovery.get("disk_version")),
        ("Launcher", discovery.get("launcher")),
        ("Cadence", _seconds(discovery.get("cadence_seconds"))),
        ("Cadence reason", discovery.get("cadence_reason")),
        (
            "Last scheduler wake",
            humanise_age(discovery.get("last_scheduler_tick_at"), now=now),
        ),
        (
            "Last check attempt",
            humanise_age(discovery.get("last_check_attempt_at"), now=now),
        ),
        (
            "Last remote check",
            humanise_age(discovery.get("last_remote_check_at"), now=now),
        ),
        (
            "Last successful remote",
            humanise_age(discovery.get("last_successful_remote_check_at"), now=now),
        ),
        ("Next remote check", discovery.get("next_remote_check_eligible_at")),
        ("Last trigger", discovery.get("last_trigger")),
        ("Retry count", discovery.get("retry_count")),
        ("Next retry", retry_age),
        ("Automatic downloads", discovery.get("automatic_downloads")),
        ("Install on quit", discovery.get("automatic_install_on_quit")),
        ("Restart required", discovery.get("restart_required")),
        ("Restart available", discovery.get("restart_available")),
    ]
    width = max(len(label) for label, _ in detail)
    for label, value in detail:
        if value in (None, "", []):
            continue
        lines.append(f"  {label.ljust(width)}  {value}")
    return lines


def _seconds(value: object) -> str:
    try:
        total = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if total % 3600 == 0:
        return f"{total // 3600} h"
    if total % 60 == 0:
        return f"{total // 60} min"
    return f"{total} s"
