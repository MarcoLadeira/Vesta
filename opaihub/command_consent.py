"""One-shot approval for outward-facing commands, across process boundaries.

Round 5 QA finding 1: pinning Full Auto promises "Push, deploy, and destructive
actions still ask for confirmation", but a push ran with no confirmation UI at
all. The reason was structural. OPai has two execution channels and neither
could ask:

* Its own tool executor ran ``git_push``/``open_pr`` the moment consent existed
  in Settings, with no per-turn confirmation.
* A provider CLI (Claude Code) runs git in *its own* shell, gated only by the
  ``opai hooks claude-pre-tool`` PreToolUse hook. A hook can allow or deny — it
  has no interactive channel — so a "confirm" verdict was a dead end. Round 2/3
  fixed the dead end by auto-allowing a consented plain push, which is exactly
  what broke the promise.

This module is the interactive channel both channels were missing. It is a tiny
file-backed handshake between the GUI pipeline and any child process that gates
a command:

1. The hook (or the tool executor) refuses the command and calls
   :func:`record_pending` with the exact command string and the reason.
2. The pipeline reads it with :func:`take_pending` and returns
   ``needs_command_approval``, so the GUI renders its existing approval card
   instead of a silently-completed turn.
3. "Approve once" re-sends the same request with ``allowCommand``; the pipeline
   calls :func:`begin_turn` with that grant, and the gate consumes it with
   :func:`consume_grant` — once.

Settings consent (``opai github allow-push on`` + a connected token) still
decides whether pushing is possible *at all*; this decides whether *this* push
happens now. Both are required, which is what the dialog copy claims.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

# A grant/pending record older than this is ignored and removed: a crashed run
# must never leave an approval lying around that authorizes a later push.
CONSENT_TTL_SECONDS = 1800

_GRANT_NAME = "pending-grant.json"
_REQUEST_NAME = "pending-request.json"

# One whole command that is exactly a ``git push``, with no shell chaining,
# redirection, substitution, or a second command hidden behind an operator.
# Push-equivalence below applies only to this shape, so an approval can never be
# used to smuggle ``git push && rm -rf .`` past the gate.
_PLAIN_PUSH_COMMAND = re.compile(
    r"""^\s*(?:git(?:\.exe)?)\s+push(?P<rest>(?:\s+[^\s;&|<>`$()]+)*)\s*$""",
    re.IGNORECASE,
)

# Flags that make a push destructive to remote history. ``-f``/``--force``,
# ``--force-with-lease``, ``--mirror``, ``--delete``/``-d``, and a leading ``+``
# in a refspec (``git push origin +main``) all rewrite or drop remote refs.
# ``--receive-pack``/``--exec`` name a program to run on the remote end, which is
# code execution, not a push — an approval to push never covers that.
_FORCE_PUSH_FLAG = re.compile(
    r"(?:^|\s)(?:-f|-d|--force(?:-with-lease|-if-includes)?|--delete|--mirror|--prune"
    r"|--receive-pack|--exec|\+[\w./-]+:)",
    re.IGNORECASE,
)

# An explicit remote URL rather than a configured remote name. Consent means
# "push my branches to my repository", so an autonomous run may not use it to
# send the repository to an arbitrary host.
_PUSH_REMOTE_URL = re.compile(
    r"(?:^|\s)(?:[a-z][a-z0-9+.-]*://|[\w.-]+@[\w.-]+:)", re.IGNORECASE
)


def is_plain_push(command: str) -> bool:
    """True for a lone, non-force ``git push`` to a named remote, nothing else."""

    match = _PLAIN_PUSH_COMMAND.match(str(command or ""))
    if match is None:
        return False
    rest = match.group("rest") or ""
    return not _FORCE_PUSH_FLAG.search(rest) and not _PUSH_REMOTE_URL.search(rest)


def consent_dir() -> Path:
    """Where the handshake files live.

    A fixed per-user temp location, like the generated Claude hook settings file,
    so a hook subprocess started by a provider CLI can find it with no argument
    plumbing. ``OPAI_COMMAND_CONSENT_DIR`` overrides it for tests.
    """

    override = str(os.environ.get("OPAI_COMMAND_CONSENT_DIR", "") or "").strip()
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "opai" / "consent"


def _path(name: str) -> Path:
    return consent_dir() / name


def _read(name: str) -> dict[str, Any] | None:
    """Load a record, treating unreadable, malformed, and expired ones as absent."""

    target = _path(name)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        _discard(name)
        return None
    try:
        created = float(payload.get("created_at") or 0)
    except (TypeError, ValueError):
        created = 0.0
    if created <= 0 or (time.time() - created) > CONSENT_TTL_SECONDS:
        _discard(name)
        return None
    return payload


def _write(name: str, payload: dict[str, Any], *, exclusive: bool = False) -> bool:
    target = _path(name)
    body = json.dumps({**payload, "created_at": time.time()}, sort_keys=True)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
        handle = os.open(target, flags, 0o600)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
    except OSError:
        return False
    return True


def _discard(name: str) -> None:
    try:
        _path(name).unlink()
    except OSError:
        pass


def begin_turn(grant: str | None = None) -> None:
    """Reset the handshake for a new turn, optionally arming one approval.

    Called before any provider runs. Clearing first is the important half: a
    refusal recorded by a previous turn must never be re-surfaced, and a grant
    the user issued for an earlier turn must never authorize this one.
    """

    _discard(_REQUEST_NAME)
    _discard(_GRANT_NAME)
    command = str(grant or "").strip()[:2_000]
    if command:
        _write(_GRANT_NAME, {"command": command})


def end_turn() -> None:
    """Drop any unconsumed grant once the turn is over.

    An approval is for one command in one turn. If the model never made the call
    the user approved, the grant dies here rather than waiting for the TTL.
    """

    _discard(_GRANT_NAME)


def granted_command() -> str:
    """The command currently approved for this turn, if any (non-consuming)."""

    payload = _read(_GRANT_NAME)
    return str((payload or {}).get("command") or "")


def grant_permits(grant: str, command: str) -> bool:
    """Whether ``grant`` authorizes ``command``.

    Exact string match, plus one deliberate equivalence: two *plain* pushes
    authorize each other. The approval card shows the user the command the model
    tried first, and the model rarely re-emits that byte-for-byte on the retry
    (``git push`` vs ``git push -u origin feature``) — without the equivalence an
    approved push would be refused again, which is the dead end this whole module
    exists to remove. Force/delete/mirror/URL pushes are not "plain", so they can
    never be reached through a plain-push approval.
    """

    left = str(grant or "").strip()
    right = str(command or "").strip()
    if not left or not right:
        return False
    if left == right:
        return True
    return is_plain_push(left) and is_plain_push(right)


def consume_grant(command: str) -> bool:
    """Spend the one-shot grant on ``command``. False leaves it untouched."""

    payload = _read(_GRANT_NAME)
    if payload is None:
        return False
    if not grant_permits(str(payload.get("command") or ""), command):
        return False
    _discard(_GRANT_NAME)
    return True


def record_pending(command: str, reason: str) -> bool:
    """Record that ``command`` was refused and needs the user's approval.

    First refusal of a turn wins: a run that tries several gated commands should
    ask about the one it hit first, not have its request overwritten by later
    attempts the model made after being told no.
    """

    text = str(command or "").strip()
    if not text:
        return False
    if _read(_REQUEST_NAME) is not None:
        return False
    return _write(
        _REQUEST_NAME,
        {"command": text[:2_000], "reason": str(reason or "").strip()[:1_000]},
        exclusive=True,
    )


def take_pending() -> dict[str, str] | None:
    """Read and clear the pending approval request for this turn."""

    payload = _read(_REQUEST_NAME)
    _discard(_REQUEST_NAME)
    if payload is None:
        return None
    command = str(payload.get("command") or "").strip()
    if not command:
        return None
    return {"command": command, "reason": str(payload.get("reason") or "").strip()}
