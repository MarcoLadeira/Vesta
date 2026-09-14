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
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from . import owner_lease
from .call_reconciliation import pid_is_running, positive_pid

#: This process, provably. Pid *and* boot id match, so no reuse can fake it.
OWNED_HERE = "owned_here"
#: The owning process is gone. Conclusive: pid reuse cannot manufacture this.
OWNER_GONE = "owner_gone"
#: A process with the recorded id exists, but it may not be the same one.
#: Deliberately not "alive" -- that is the claim the evidence cannot support.
OWNER_UNVERIFIED = "owner_unverified"
#: The owner was tending this run and stopped. Distinct from OWNER_GONE: the
#: process may well still exist. What is known is that it has not restamped a
#: heartbeat it was previously restamping, which is positive evidence rather
#: than the absence of it.
OWNER_STALE = "owner_stale"
#: No process was recorded, or the platform declined to say. Pre-migration
#: leases land here, and so does a locked-down platform.
OWNER_UNKNOWN = "unknown"

#: Every verdict, so a surface can be checked for exhaustiveness rather than
#: discovering a new one in production.
VERDICTS: Sequence[str] = (
    OWNED_HERE,
    OWNER_GONE,
    OWNER_STALE,
    OWNER_UNVERIFIED,
    OWNER_UNKNOWN,
)

#: The verdicts a recovery pass may act on without asking anything further.
#: ``OWNER_UNVERIFIED`` is absent on purpose: acting on it would be acting on a
#: guess, and a wrong guess here cancels somebody's running work.
ACTIONABLE: Sequence[str] = (OWNED_HERE, OWNER_GONE)


def _moment(value: Any) -> datetime | None:
    """One ISO timestamp, or ``None`` for anything that is not one."""

    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stopped_responding(lease: Mapping[str, Any], now: datetime | None) -> bool:
    """True when a lease that *was* being tended has gone quiet.

    Two conditions, and the first is what keeps this honest. The heartbeat must
    have moved since the lease was acquired -- proof that somebody was
    restamping it. Without that, a quiet heartbeat means only that this
    surface never beats one: background runs and CLI runs do not, and judging
    them by a clock they never wound would report every one of them as dead.

    So absence of a heartbeat proves nothing here, and only its *stopping*
    does. The staleness window is `owner_lease`'s, deliberately: two
    definitions of "stale" in one codebase is the kind of second opinion #818
    exists to remove.
    """

    heartbeat = _moment(lease.get("lease_heartbeat_at"))
    acquired = _moment(lease.get("lease_acquired_at"))
    # `heartbeat <= acquired` is what "nobody has beaten this" looks like, and
    # it holds because `acquire_lease` stamps both columns from a single value
    # rather than reading the clock twice. That coupling is load-bearing: two
    # reads would differ by microseconds and make every never-beating lease
    # look tended, and then stale, and then reported as stopped responding.
    if heartbeat is None or acquired is None or heartbeat <= acquired:
        return False
    reference = now or datetime.now(timezone.utc)
    return (reference - heartbeat).total_seconds() > owner_lease.STALE_AFTER_SECONDS


def owner_liveness(
    lease: Mapping[str, Any],
    *,
    is_pid_running: Callable[[int], bool | None] = pid_is_running,
    this_pid: int | None = None,
    this_boot: str = "",
    now: datetime | None = None,
) -> str:
    """One of :data:`VERDICTS` for a lease row.

    ``is_pid_running`` is injected so the decision can be tested against a
    known answer instead of against whatever the machine's process table
    happens to contain -- the alternative is a test that passes because a pid
    was free, which is not a test.
    """

    pid = positive_pid(lease.get("owner_pid"))
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
        running = None
    if running is False:
        # Checked before staleness because it is the more precise answer: a
        # process that no longer exists is gone, not merely quiet.
        return OWNER_GONE
    if _stopped_responding(lease, now):
        return OWNER_STALE
    if running is True:
        # A pid that matches ours but carries a foreign boot id is a *reused*
        # pid -- our own interpreter would have written our boot id. Reporting
        # it unverified rather than owned keeps the one provable case provable.
        return OWNER_UNVERIFIED
    return OWNER_UNKNOWN


#: How long a lease nobody can confirm stays "maybe alive".
#:
#: Without a bound this module strands runs permanently, and that was a real
#: defect rather than a hypothetical one. ``OWNER_STALE`` and
#: ``OWNER_UNVERIFIED`` both count as possibly-alive, so a run whose owning pid
#: was reused by an unrelated process -- ordinary on Windows, where pids cycle
#: and restart low after a reboot -- reads unverified forever. Recovery skips
#: it every time, and it sits in ``running`` with no way out: exactly the ghost
#: state #613 opens by describing, reintroduced by the guard meant to prevent
#: it.
#:
#: Six hours is deliberately far beyond any plausible turn or automation. The
#: asymmetry the rest of this module states still holds -- writing a terminal
#: verdict onto live work is worse than leaving a dead run around -- but
#: "leave it around" has to mean *for a while*, not *for ever*. A run nobody
#: has heard from since this morning is not being worked on.
ABANDONED_AFTER_SECONDS = 6 * 60 * 60


def _last_sign_of_life(lease: Mapping[str, Any], now: datetime | None) -> float | None:
    """Seconds since anything was heard from this lease, or ``None``.

    The heartbeat when one was ever stamped, and the acquisition otherwise --
    background and CLI runs do not beat, and judging them by a clock they never
    wound is what :func:`_stopped_responding` exists to avoid. Acquisition is
    still a real signal: it is the moment somebody was demonstrably there.
    """

    heartbeat = _moment(lease.get("lease_heartbeat_at"))
    acquired = _moment(lease.get("lease_acquired_at"))
    latest = max((moment for moment in (heartbeat, acquired) if moment), default=None)
    if latest is None:
        return None
    reference = now or datetime.now(timezone.utc)
    return (reference - latest).total_seconds()


def _long_abandoned(lease: Mapping[str, Any], now: datetime | None) -> bool:
    """True when nothing has been heard for longer than anyone should wait.

    Absence of any timestamp is not evidence of abandonment -- a lease this
    cannot date is one it cannot judge, and it stays possibly-alive.
    """

    silence = _last_sign_of_life(lease, now)
    return silence is not None and silence > ABANDONED_AFTER_SECONDS


def may_be_alive(
    lease: Mapping[str, Any],
    *,
    is_pid_running: Callable[[int], bool | None] = pid_is_running,
    this_pid: int | None = None,
    this_boot: str = "",
    now: datetime | None = None,
) -> bool:
    """True when something might still be tending this run.

    The question a recovery pass actually has, and deliberately *not* the same
    as ``owner_liveness(...) != OWNER_GONE``.

    A lease with no process recorded is absence of evidence, not evidence of
    life. Treating it as "might be alive" would freeze recovery for every run
    admitted before the identity columns existed -- they would sit in
    ``running`` forever with no way out, which is the ghost state #613 opened
    by describing. Those fall through to whatever the caller did before.

    A lease that *does* name a process is different. Anything short of
    "that process is gone" leaves open the possibility that work is still
    running, including the case where the platform declined to answer. The
    asymmetry is deliberate: failing to recover a dead run is a nuisance, and
    writing a terminal verdict onto a live one is a lie.
    """

    if positive_pid(lease.get("owner_pid")) is None:
        return False
    verdict = owner_liveness(
        lease,
        is_pid_running=is_pid_running,
        this_pid=this_pid,
        this_boot=this_boot,
        now=now,
    )
    if verdict == OWNER_GONE:
        return False
    if verdict == OWNED_HERE:
        # This process holds it. No clock beats knowing.
        return True
    return not _long_abandoned(lease, now)


# `OWNER_STALE` is deliberately absent from `ACTIONABLE` and counts as
# possibly-alive above. A process that stopped restamping its heartbeat may be
# wedged, suspended, or in the middle of a long provider call that emits
# nothing -- and reconciling it would write a terminal verdict over work that
# is still running, which is the defect this module was built to stop. It is
# reported so a person can decide; it is not acted on automatically.


def describe(verdict: str) -> str:
    """A sentence for a person, matching the verdict exactly.

    Kept beside the vocabulary so a second surface cannot invent a third way of
    saying the same thing, which is the failure #818 exists to end.
    """

    return _SENTENCES.get(verdict, _SENTENCES[OWNER_UNKNOWN])


_SENTENCES = {
    OWNED_HERE: "This OPai is working on it now.",
    OWNER_GONE: "The OPai that started this is no longer running.",
    OWNER_STALE: "The OPai that started this stopped responding.",
    OWNER_UNVERIFIED: "Another OPai may still be working on it.",
    OWNER_UNKNOWN: "OPai cannot tell whether this is still running.",
}


__all__: Sequence[str] = (
    "ACTIONABLE",
    "OWNED_HERE",
    "OWNER_GONE",
    "OWNER_STALE",
    "OWNER_UNKNOWN",
    "OWNER_UNVERIFIED",
    "VERDICTS",
    "describe",
    "may_be_alive",
    "owner_liveness",
)
