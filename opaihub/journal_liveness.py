"""Is the process that owns this run still tending it? (#818)

The canonical journal records a lease for every admitted run, and a lease is
released by ``record_terminal`` -- not by a process exiting. So a run that
crashed keeps its lease forever, and looks exactly like a run being worked on
right now. ``unterminated_runs`` said as much, and told its caller to go and
check whether the owning process still existed. It then handed over a lease
whose owner was the string ``"gui"``.

This module is the other half of that sentence. Given the process identity a
lease now records, it answers one question -- is the owner still there? -- in a
closed vocabulary, and refuses to answer when the evidence does not support an
answer.

**Why a pid alone is not proof of life.** Operating systems reuse process ids,
so a dead owner's id can belong to something unrelated minutes later and would
probe as alive. That asymmetry is the whole design:

* a pid that is **not** running is conclusive. Reuse can only ever make a dead
  process look alive; it cannot make a live one look dead. So ``OWNER_GONE`` is
  safe to state.
* a pid that **is** running is not conclusive, unless it is this very process.
  So it reports ``OWNER_UNVERIFIED`` -- something with that id exists, and this
  module will not upgrade that into a claim that the work is being tended.

The one case that is fully decidable is our own: a lease carrying this
process's pid *and* this interpreter's boot id can only have been written by
us, because the boot id is minted per interpreter and never reused. That is
what lets OPai avoid offering to recover work it is doing right now.

Nothing here terminates, resumes or reconciles anything. It answers a question
and the caller decides, which is the same division ``owner_lease`` draws for
the legacy record it is destined to replace.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Sequence

from . import owner_lease
from .call_reconciliation import pid_is_running

#: This process, provably. Pid *and* boot id match, so no reuse can fake it.
OWNED_HERE = "owned_here"
#: The owning process is gone. Conclusive: pid reuse cannot manufacture this.
OWNER_GONE = "owner_gone"
#: A process with the recorded id exists, but it may not be the same one.
#: Deliberately not "alive" -- that is the claim the evidence cannot support.
OWNER_UNVERIFIED = "owner_unverified"
#: No process was recorded, or the platform declined to say. Pre-migration
#: leases land here, and so does a locked-down platform.
OWNER_UNKNOWN = "unknown"

#: Every verdict, so a surface can be checked for exhaustiveness rather than
#: discovering a new one in production.
VERDICTS: Sequence[str] = (OWNED_HERE, OWNER_GONE, OWNER_UNVERIFIED, OWNER_UNKNOWN)

#: The verdicts a recovery pass may act on without asking anything further.
#: ``OWNER_UNVERIFIED`` is absent on purpose: acting on it would be acting on a
#: guess, and a wrong guess here cancels somebody's running work.
ACTIONABLE: Sequence[str] = (OWNED_HERE, OWNER_GONE)


def owner_liveness(
    lease: Mapping[str, Any],
    *,
    is_pid_running: Callable[[int], bool | None] = pid_is_running,
    this_pid: int | None = None,
    this_boot: str = "",
) -> str:
    """One of :data:`VERDICTS` for a lease row.

    ``is_pid_running`` is injected so the decision can be tested against a
    known answer instead of against whatever the machine's process table
    happens to contain -- the alternative is a test that passes because a pid
    was free, which is not a test.
    """

    pid = _as_pid(lease.get("owner_pid"))
    boot = str(lease.get("owner_boot") or "")
    if pid is None:
        # Includes every lease written before the identity columns existed.
        return OWNER_UNKNOWN

    current_pid = os.getpid() if this_pid is None else int(this_pid)
    current_boot = this_boot or owner_lease.boot_id()
    if pid == current_pid and boot and boot == current_boot:
        return OWNED_HERE

    try:
        running = is_pid_running(pid)
    except Exception:  # noqa: BLE001 - a probe that fails answers "unknown"
        return OWNER_UNKNOWN
    if running is False:
        return OWNER_GONE
    if running is True:
        # A pid that matches ours but carries a foreign boot id is a *reused*
        # pid -- our own interpreter would have written our boot id. Reporting
        # it unverified rather than owned keeps the one provable case provable.
        return OWNER_UNVERIFIED
    return OWNER_UNKNOWN


def describe(verdict: str) -> str:
    """A sentence for a person, matching the verdict exactly.

    Kept beside the vocabulary so a second surface cannot invent a third way of
    saying the same thing, which is the failure #818 exists to end.
    """

    return _SENTENCES.get(verdict, _SENTENCES[OWNER_UNKNOWN])


_SENTENCES = {
    OWNED_HERE: "This OPai is working on it now.",
    OWNER_GONE: "The OPai that started this is no longer running.",
    OWNER_UNVERIFIED: "Another OPai may still be working on it.",
    OWNER_UNKNOWN: "OPai cannot tell whether this is still running.",
}


def _as_pid(value: Any) -> int | None:
    """A usable process id, or ``None`` for anything that is not one.

    ``OverflowError`` is caught explicitly because it is an ``ArithmeticError``
    rather than a ``ValueError``: ``int(float("inf"))`` raises it and would
    otherwise escape a function documented never to raise. ``call_reconciliation``
    was bitten by the same gap on the same kind of value.
    """

    if isinstance(value, bool):
        return None
    try:
        pid = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return pid if pid > 0 else None


__all__: Sequence[str] = (
    "ACTIONABLE",
    "OWNED_HERE",
    "OWNER_GONE",
    "OWNER_UNKNOWN",
    "OWNER_UNVERIFIED",
    "VERDICTS",
    "describe",
    "owner_liveness",
)
