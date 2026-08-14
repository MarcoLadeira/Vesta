"""Supervisor leases: is the process that owns this run still alive? (#295)

OPai persists a turn as ``state: "running"`` before the work starts, so a crash
leaves that record behind forever. The resume path already handled this
honestly — and said so in a comment that names the exact missing piece:

    Boot is deliberately read-only. A pending checkpoint can still belong to
    another live OPai window or CLI run; **without an owner lease, process
    death cannot be inferred safely**. Preserve it verbatim and let the user
    make the explicit resume/start-fresh choice.

That is the right call while the information is absent, but it means OPai cannot
distinguish "another window is working on this" from "this died three days ago",
and so cannot tell the user which one it is. #295 asks for exactly this:
invariant 4, *one active owner — each run and external process has exactly one
supervisor lease at a time*; and the transition rule that *a stale heartbeat
cannot remain "running" forever*.

**How liveness is decided, and why not by PID.** A bare process id is not
evidence: operating systems reuse them, so a dead owner's id can belong to some
unrelated process minutes later and would read as alive. This module uses a
*heartbeat* instead — the owning process restamps the lease while it works, and
a lease whose heartbeat has gone quiet for longer than ``STALE_AFTER_SECONDS``
is stale no matter what its pid says. A reused pid cannot refresh a lease it
does not know about, so the heartbeat is the only signal that can be trusted.

The pid and a per-process boot id are still recorded, for two narrower jobs they
*can* do honestly: recognising OPai's own lease (``owned_by_this_process``) so a
process never treats its own work as abandoned, and giving a human something to
identify in a diagnostic.

Nothing here terminates anything. A lease answers one question — is the owner
still alive? — and the decision about what to do belongs to the caller.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from . import shadow_journal
from .atomic_io import atomic_write_text, interprocess_transaction

# The owner restamps its lease at least this often while it is working.
HEARTBEAT_INTERVAL_SECONDS = 10.0
# A lease unrefreshed for longer than this is stale. Deliberately several
# heartbeats wide: a machine that briefly suspends, a busy GUI thread, or a slow
# disk must not be mistaken for a dead process. Declaring a live run abandoned
# is far worse than waiting a little longer to declare a dead one.
STALE_AFTER_SECONDS = 90.0

# Identifies *this* process for the lifetime of the interpreter. Regenerated on
# every start, so a recycled pid never looks like the same owner.
_BOOT_ID = uuid.uuid4().hex


def boot_id() -> str:
    """This process's identity for lease purposes."""
    return _BOOT_ID


def new_lease(*, now: float | None = None) -> dict[str, Any]:
    """A fresh lease owned by this process."""
    stamp = time.time() if now is None else float(now)
    return {
        "pid": int(os.getpid()),
        "boot": _BOOT_ID,
        "acquired_at": stamp,
        "heartbeat_at": stamp,
    }


def touch(lease: Any, *, now: float | None = None) -> dict[str, Any]:
    """Restamp a lease this process owns. Returns the updated lease.

    A lease belonging to a *different* process is returned unchanged: refreshing
    someone else's lease would keep a dead owner looking alive forever, which is
    the exact failure this module exists to prevent.
    """
    if not owned_by_this_process(lease):
        return dict(lease) if isinstance(lease, dict) else {}
    updated = dict(lease)
    updated["heartbeat_at"] = time.time() if now is None else float(now)
    return updated


def owned_by_this_process(lease: Any) -> bool:
    """True when this exact process (pid *and* boot id) holds the lease."""
    if not isinstance(lease, dict):
        return False
    return (
        str(lease.get("boot") or "") == _BOOT_ID
        and _as_int(lease.get("pid")) == os.getpid()
    )


def is_stale(lease: Any, *, now: float | None = None) -> bool:
    """True when the lease's owner has stopped proving it is alive.

    A missing or malformed lease counts as stale: a run recorded with no owner
    at all cannot be shown as actively running.
    """
    heartbeat = _heartbeat_at(lease)
    if heartbeat is None:
        return True
    stamp = time.time() if now is None else float(now)
    return (stamp - heartbeat) > STALE_AFTER_SECONDS


def describe(lease: Any, *, now: float | None = None) -> dict[str, Any]:
    """A secret-free account of the lease, for diagnostics and resume copy.

    ``reason`` is a closed vocabulary so a surface can branch on it, and
    ``ownerIsThisProcess`` lets a caller avoid offering to recover its own work.
    """
    stamp = time.time() if now is None else float(now)
    heartbeat = _heartbeat_at(lease)
    mine = owned_by_this_process(lease)
    # Staleness is decided before ownership, deliberately. Checking "is it mine?"
    # first reported a lease this process had stopped refreshing as healthy,
    # because the pid still matched — a run nobody was tending, described as
    # fine. The heartbeat is the evidence; ownership only refines the wording.
    if heartbeat is None:
        reason = "no_owner_recorded"
    elif (stamp - heartbeat) > STALE_AFTER_SECONDS:
        reason = "owner_gone"
    elif mine:
        reason = "owned_here"
    else:
        reason = "owner_alive_elsewhere"
    return {
        "reason": reason,
        "stale": reason in {"no_owner_recorded", "owner_gone"},
        "ownerIsThisProcess": mine,
        # How long the owner has been silent, so a diagnostic can say "three
        # days ago" rather than a bare boolean. None when never stamped.
        "silentForSeconds": None if heartbeat is None else max(0.0, stamp - heartbeat),
    }


def _heartbeat_at(lease: Any) -> float | None:
    if not isinstance(lease, dict):
        return None
    value = lease.get("heartbeat_at")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


# --- Durable, fenced leases (#517) -----------------------------------------
#
# Everything above is a pure, dict-based liveness check: the caller owns
# persistence, and a lease's only defence against a stale writer is its own
# heartbeat going quiet. That is enough for a single GUI thread file guarded
# by one cross-process write lock (see ``opai.gui_recents``), where at most
# one process is ever meant to hold the lease at a time and the write lock
# itself serializes acquisition.
#
# A run supervisor is a different shape of problem: #517 asks for leases that
# can prove, after the fact, that a write came from the *current* owner and
# not a process that has since been superseded — a stale supervisor that
# wakes from a suspend, or two processes racing to pick up the same abandoned
# run after a restart, must not both believe they are in charge. A heartbeat
# alone cannot prove that: a stale process's heartbeat still *looks* fresh to
# itself right up until it writes.
#
# A fencing token closes that gap. Each acquisition at a given ``path`` is
# assigned a token strictly greater than every token ever issued there
# before, including ones held by processes that are now dead. A writer that
# captured an older token can be shown, cheaply and without asking it
# anything, that someone else now holds the lease — the classic fencing-token
# pattern (Chubby/ZooKeeper-style), applied to a local file instead of a
# distributed lock service, since that is the durability substrate this
# module already trusts.


def lease_path(root: Any, resource_id: str) -> Path:
    """The durable lease file for one resource, under a project's state dir."""
    from .state import state_dir

    clean = "".join(ch for ch in str(resource_id) if ch.isalnum() or ch in "-_.")[:128]
    if not clean or clean != str(resource_id):
        raise ValueError(f"unsafe lease resource id: {resource_id!r}")
    return state_dir(Path(root)) / "health" / "leases" / f"{clean}.json"


def _read_lease_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_lease_file(path: Path, lease: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(lease, sort_keys=True) + "\n")


# --- #613 Stage 2: shadow journal ------------------------------------------
#
# Stage 1's inventory names this module JOURNAL_OWNED -- "leases: ownership and
# fencing". The legacy file below stays the single decision-maker and the only
# thing a caller's return value depends on; every accepted transition is *also*
# mirrored into a run_journal event from inside the same
# interprocess_transaction that made the decision, so the mirror can never
# observe a different order of acquisitions than the file did.
#
# The mechanics now live in opaihub.shadow_journal, which this module and
# worktree_leases each hand-rolled separately first. Two details it carries
# that were learned here: a shadow-write failure must never fail the lease it
# mirrors (a liveness primitive must not be refused over a logging concern),
# and the comparator must read both sides under the writer's own lock so the
# comparison can be exact rather than tolerating a fence-distance window.
#
# An earlier revision wrote this journal as a sibling of the lease file. The
# helper places it in a ``journal/`` subdirectory instead; any journal written
# under the old layout is simply orphaned, which is harmless because it was
# never authoritative -- the legacy file it shadowed is untouched.


def _valid_lease_record(record: Mapping[str, Any]) -> bool:
    """A mirrored lease must carry the identity and fence a reader needs."""

    return (
        _as_int(record.get("fence")) is not None
        and isinstance(record.get("heartbeat_at"), (int, float))
        and not isinstance(record.get("heartbeat_at"), bool)
        and isinstance(record.get("boot"), str)
        and bool(record.get("boot"))
    )


def _shadow_record(path: Path, lease: dict[str, Any]) -> None:
    """Mirror an already-written lease. Never raises."""

    shadow_journal.record_snapshot(path, lease, is_valid_record=_valid_lease_record)


def shadow_journal_projection(path: Path) -> dict[str, Any]:
    """The lease state the shadow journal alone would reconstruct."""

    return shadow_journal.projection(path, is_valid_record=_valid_lease_record)


def lease_contradiction_report(path: Path) -> dict[str, Any] | None:
    """``None`` when the file and its shadow agree; otherwise, what disagrees.

    This is the dual-read #613 asks for: "compare legacy and journal
    projections and record contradictions."
    """

    path = Path(path)
    return shadow_journal.contradiction_report(
        path,
        lambda: _read_lease_file(path),
        is_valid_record=_valid_lease_record,
    )


def acquire(path: Path, *, now: float | None = None) -> dict[str, Any]:
    """Durably acquire (or re-acquire) the fenced lease at ``path``.

    Assigns a fencing token one higher than the highest ever issued at this
    path — read-modify-write under the same cross-process lock every other
    durable state file in this codebase uses, so two processes racing to
    acquire can never be handed the same token. The caller becomes the
    current owner unconditionally: acquiring is how a fresh supervisor takes
    over an abandoned run, so it does not (and safely cannot) check whether a
    prior owner's heartbeat was still warm — that judgment belongs to
    whatever caller decided this lease was worth acquiring.
    """
    path = Path(path)
    with interprocess_transaction(path):
        existing = _read_lease_file(path)
        prior_fence = _as_int(existing.get("fence")) or 0
        acquired = {**new_lease(now=now), "fence": prior_fence + 1}
        _write_lease_file(path, acquired)
        # #613 Stage 2: mirrored while still holding the file's own lock, so
        # the journal can never observe acquisitions in a different order
        # than the file did.
        _shadow_record(path, acquired)
    return acquired


def current(path: Path) -> dict[str, Any]:
    """The durable lease at ``path`` without acquiring or changing it.

    An absent or unreadable file reads as ``{}`` — the same "no owner
    recorded" shape :func:`is_stale`/:func:`describe` already treat as stale,
    so a caller never needs a separate existence check.
    """
    return _read_lease_file(Path(path))


def renew(
    path: Path, lease: dict[str, Any], *, now: float | None = None
) -> dict[str, Any]:
    """Heartbeat a durably-acquired lease this process believes it owns.

    Refuses — and returns the file's actual current lease unchanged — unless
    both the process identity *and* the fencing token still match what is on
    disk. A token mismatch means someone else has since acquired the lease
    (this owner has been fenced out); overwriting their heartbeat with ours
    would be exactly the split-brain write this mechanism exists to prevent.
    The caller must compare the return value's ``fence`` to its own before
    trusting that the renewal actually took effect.
    """
    path = Path(path)
    with interprocess_transaction(path):
        on_disk = _read_lease_file(path)
        if not owned_by_this_process(on_disk) or _as_int(
            on_disk.get("fence")
        ) != _as_int(lease.get("fence")):
            return on_disk
        touched = {**touch(on_disk, now=now), "fence": on_disk["fence"]}
        _write_lease_file(path, touched)
        # Only a real, accepted renewal is mirrored -- the refusal above
        # returns early and writes nothing, on either side.
        _shadow_record(path, touched)
    return touched


def is_current(path: Path, fence: Any) -> bool:
    """True when ``fence`` is still the latest fencing token issued at ``path``."""
    on_disk = _read_lease_file(Path(path))
    disk_fence = _as_int(on_disk.get("fence"))
    caller_fence = _as_int(fence)
    return (
        disk_fence is not None
        and caller_fence is not None
        and disk_fence == caller_fence
    )
