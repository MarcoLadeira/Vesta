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

import os
import time
import uuid
from typing import Any

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
