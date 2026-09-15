"""One-shot approval for outward-facing commands, across process boundaries.

Round 5 QA finding 1: pinning Full Auto promises "Push, deploy, and destructive
actions still ask for confirmation", but a push ran with no confirmation UI at
all. The reason was structural. Vesta has two execution channels and neither
could ask:

* Its own tool executor ran ``git_push``/``open_pr`` the moment consent existed
  in Settings, with no per-turn confirmation.
* A provider CLI (Claude Code) runs git in *its own* shell, gated only by the
  ``vesta hooks claude-pre-tool`` PreToolUse hook. A hook can allow or deny — it
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

Settings consent (``vesta github allow-push on`` + a connected token) still
decides whether pushing is possible *at all*; this decides whether *this* push
happens now. Both are required, which is what the dialog copy claims.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

# A grant/pending record older than this is ignored and removed: a crashed run
# must never leave an approval lying around that authorizes a later push.
CONSENT_TTL_SECONDS = 1800

_GRANT_NAME = "pending-grant.json"
_REQUEST_NAME = "pending-request.json"

# Which run a grant belongs to, carried into every provider child so the hook
# subprocess that spends it can prove it is the run the user actually answered
# for (#818 AC8). Defined here rather than in ``opaihub.proc`` because proc
# imports this module and not the other way round -- this one stays
# dependency-free so a provider CLI's hook can import it on every tool call.
RUN_ENV = "OPAI_RUN_ID"

# The run this turn has armed a grant for. Set by :func:`begin_turn` and read
# by ``opaihub.proc.provider_child_env`` when it builds a child environment, so
# the run identity reaches the hook without being threaded through
# AccountRunner and every provider adapter in between.
#
# A ContextVar, not a module global. A global is shared by every turn in the
# process, so two turns running at once in one GUI overwrote -- and on ending,
# cleared -- each other's run id; and a cleared id makes the ownership check
# below fall back to "allow". Same reason, same shape as gui_pipeline's
# _JOURNAL_RUN. A turn sets and reads it on its own thread, which is where the
# provider child is launched.
_CURRENT_RUN: ContextVar[str] = ContextVar("opai_command_consent_run", default="")

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


# An agent never sends a bare `git push`: it needs a working directory, so the
# real command is `cd "<repo>" && git push ...`, often with a trailing `2>&1`.
# Requiring the whole string to be a lone push therefore matched nothing that is
# actually issued, which made the push-approval card unreachable in practice and
# sent every safe push down the force/delete/mirror branch instead -- telling the
# user their ordinary branch push "rewrites or removes remote history", and that
# no consent could ever unlock it.
#
# Deliberately narrow rather than a general shell parser: exactly one leading
# `cd <single-path> &&`, and at most the exact `2>&1` redirect. Anything else --
# a pipe, a second command, a substitution, a chained `&&` -- still fails, so an
# approval can never smuggle `git push && rm -rf .` past the gate.
_CD_PREFIX = re.compile(
    r"""^\s*cd\s+(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s;&|<>`$()]+)\s*&&\s*""",
    re.IGNORECASE,
)
_TRAILING_REDIRECT = re.compile(r"\s*2>&1\s*$")


def operative_push_command(command: str) -> str | None:
    """The `git push ...` a caller actually runs, or ``None`` if it is not one.

    Strips the one shell wrapper Vesta's own tooling adds. Returns the bare push
    so callers judge the push itself rather than the wrapper around it.
    """

    text = str(command or "")
    stripped = _TRAILING_REDIRECT.sub("", text)
    stripped = _CD_PREFIX.sub("", stripped, count=1)
    return stripped if _PLAIN_PUSH_COMMAND.match(stripped) else None


def is_plain_push(command: str) -> bool:
    """True for a non-force ``git push`` to a named remote, nothing else."""

    operative = operative_push_command(command)
    if operative is None:
        return False
    match = _PLAIN_PUSH_COMMAND.match(operative)
    if match is None:  # pragma: no cover - operative_push_command already matched
        return False
    rest = match.group("rest") or ""
    return not _FORCE_PUSH_FLAG.search(rest) and not _PUSH_REMOTE_URL.search(rest)


def is_history_rewriting_push(command: str) -> bool:
    """True only when a push genuinely rewrites or removes remote history.

    The gate previously inferred this from *consent being on* rather than from
    the command, so with pushing enabled every blocked push was reported as a
    force/delete/mirror. Saying that about `git push origin my-branch` is simply
    false, and it is the difference between "approve this once" and "Vesta will
    never do this" -- a dead end the user cannot clear.
    """

    text = str(command or "")
    if not _PUSH_COMMAND_ANYWHERE.search(text):
        return False
    stripped = _TRAILING_REDIRECT.sub("", text)
    stripped = _CD_PREFIX.sub("", stripped, count=1)
    match = _PLAIN_PUSH_COMMAND.match(stripped)
    # Not a shape we can parse: do not claim it rewrites history, and do not
    # claim it is safe either. The caller falls back to the generic refusal.
    if match is None:
        return False
    rest = match.group("rest") or ""
    return bool(_FORCE_PUSH_FLAG.search(rest))


_PUSH_COMMAND_ANYWHERE = re.compile(r"\bgit\s+push\b", re.IGNORECASE)


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

    return _read_path(_path(name))


def _read_path(target: Path) -> dict[str, Any] | None:
    """The same, for a record already claimed under a temporary name.

    Split out so :func:`consume_grant` can validate the grant it has already
    won without a second lookup by name -- by then the name no longer refers
    to it, which is the point.
    """

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        _discard_path(target)
        return None
    try:
        created = float(payload.get("created_at") or 0)
    except (TypeError, ValueError):
        created = 0.0
    if created <= 0 or (time.time() - created) > CONSENT_TTL_SECONDS:
        _discard_path(target)
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
    _discard_path(_path(name))


def _discard_path(target: Path) -> None:
    try:
        target.unlink()
    except OSError:
        pass


def _normalize_run(value: Any) -> str:
    return str(value or "").strip()[:200]


def resolve_run(explicit: str | None = None) -> str:
    """Which run the caller is acting for: argument, then env, then this turn.

    The hook that spends a grant is a *different process* from the one that
    armed it, so it has no module state to read -- it learns its run from
    ``OPAI_RUN_ID``, exported by ``provider_child_env``. In-process callers
    fall back to whatever :func:`begin_turn` recorded.
    """

    if explicit is not None:
        return _normalize_run(explicit)
    from_env = _normalize_run(os.environ.get(RUN_ENV, ""))
    if from_env:
        return from_env
    return _CURRENT_RUN.get()


def current_run() -> str:
    """The run this turn armed a grant for, for building a child env."""

    return _CURRENT_RUN.get()


def _sweep_orphaned_claims() -> None:
    """Remove claims a gate took and then died holding.

    A claim is a renamed grant, so a process killed between claiming and
    discarding leaves one behind. It cannot authorize anything -- nothing ever
    looks for a grant under that name -- but it is litter in a shared per-user
    directory, and nothing else would ever remove it.

    Only claims past the grant TTL go. Renaming keeps the grant's mtime, so an
    old claim holds an expired grant that :func:`consume_grant` would refuse
    anyway, and a gate in the middle of a claim right now is never disturbed.
    """

    cutoff = time.time() - CONSENT_TTL_SECONDS
    try:
        leftovers = list(consent_dir().glob(f"{_GRANT_NAME}.*.claim"))
    except OSError:
        return
    for leftover in leftovers:
        try:
            if leftover.stat().st_mtime < cutoff:
                leftover.unlink()
        except OSError:
            continue


def begin_turn(grant: str | None = None, *, run: str | None = None) -> None:
    """Reset the handshake for a new turn, optionally arming one approval.

    Called before any provider runs. Clearing first is the important half: a
    refusal recorded by a previous turn must never be re-surfaced, and a grant
    the user issued for an earlier turn must never authorize this one.

    ``run`` binds the grant to the run the user was asked about. Without it the
    handshake directory is a fixed per-user path shared by every Vesta process
    on the machine, so a second window -- different repository, different run,
    a question its user was never asked -- could spend the first window's
    approval. Measured, not theorised: two processes, one grant, both told yes.
    """

    _discard(_REQUEST_NAME)
    _discard(_GRANT_NAME)
    _sweep_orphaned_claims()
    armed = _normalize_run(run)
    _CURRENT_RUN.set(armed)
    command = str(grant or "").strip()[:2_000]
    if command:
        _write(_GRANT_NAME, {"command": command, "run": armed})


def end_turn() -> None:
    """Drop any unconsumed grant once the turn is over.

    An approval is for one command in one turn. If the model never made the call
    the user approved, the grant dies here rather than waiting for the TTL.
    """

    _discard(_GRANT_NAME)
    _CURRENT_RUN.set("")


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


def grant_belongs_to(grant_run: Any, caller_run: Any) -> bool:
    """Whether a grant issued for ``grant_run`` may be spent by ``caller_run``.

    **Refused only on a positive mismatch**: both sides name a run, and the
    names differ. Everything else is allowed.

    An earlier version used strict equality, so a caller that could not say
    which run it was got refused. That is the wrong failure for this gate, and
    the reason is the shape of the call chain rather than a preference.

    The process that spends a grant is the PreToolUse hook, and Vesta does not
    launch it. Vesta launches the *provider's* CLI, and that CLI launches the
    hook. Whether ``OPAI_RUN_ID`` survives the middle hop is a third party's
    decision. Under strict equality, a provider that sanitises the environment
    it hands its hooks would silently refuse **every** approved push -- the
    user presses Approve and nothing happens. That is a far worse failure than
    the one this check exists to prevent. Vesta must never be the reason a
    person cannot do the thing they just explicitly asked for.

    So the refusal needs evidence, exactly like everything else in this epic.
    "This grant belongs to run B and I am run A" is evidence. "I do not know
    which run I am" is not, and answering that with a refusal would be the same
    confident-guess mistake pointing the other way.

    The leak stays closed where it actually happens: two Vesta windows on one
    machine either both carry a run id or neither does, so a real cross-window
    attempt is a positive mismatch. Where identity does not propagate at all,
    the behaviour degrades to what it was before this check existed -- no worse
    than the status quo, and never a block.
    """

    left = _normalize_run(grant_run)
    right = _normalize_run(caller_run)
    if not left or not right:
        return True
    return left == right


def _spendable(payload: dict[str, Any] | None, command: str, caller: str) -> bool:
    return (
        payload is not None
        and grant_belongs_to(payload.get("run"), caller)
        and grant_permits(str(payload.get("command") or ""), command)
    )


def consume_grant(command: str, *, run: str | None = None) -> bool:
    """Spend the one-shot grant on ``command``. False leaves it untouched.

    **Exactly once, across processes.** This used to read the file, check it,
    and then unlink it, with nothing holding those three steps together. Eight
    gates racing for one grant were all told yes -- measured, not theorised --
    which makes "Approve once" a promise Vesta could not keep. A model that
    emits the same gated command several times in a turn is the ordinary way
    to reach that, not an exotic one.

    The claim is a rename. Whoever renames the grant out of the way owns it;
    everybody else gets ``FileNotFoundError`` and is told no.

    **A grant this caller cannot spend is never moved.** A grant for another
    command is still the user's approval for a command the model has not tried
    yet, and a grant for another run is still that run's; both must survive the
    attempt. The first version got there by claiming first and putting the
    grant back afterwards -- and the put-back was a hole. It could land after
    ``end_turn`` had run, or on top of the next turn's grant, restoring an
    approval whose turn was over; and a hook that cannot name its run is allowed
    to spend a grant (see :func:`grant_belongs_to`), so the revived approval was
    spendable. Reproduced by forcing the interleaving.

    So the check comes first and the claim second, and nothing is ever put back.
    The file can still change between the two -- a new turn arming a different
    approval -- so what was claimed is checked again, and if it is not what was
    looked at, it is dropped rather than restored. That costs the user one more
    click in a race between two turns; restoring it could cost a push nobody
    approved.
    """

    caller = resolve_run(run)
    grant = _path(_GRANT_NAME)
    if not _spendable(_read_path(grant), command, caller):
        return False
    claim = _path(f"{_GRANT_NAME}.{os.getpid()}.{time.time_ns():x}.claim")
    try:
        os.rename(grant, claim)
    except OSError:
        # Another gate claimed it first, or the turn ended. Both are "no".
        return False
    claimed = _read_path(claim)
    _discard_path(claim)
    return _spendable(claimed, command, caller)


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
