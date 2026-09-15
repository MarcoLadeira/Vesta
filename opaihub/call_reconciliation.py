"""When a dispatched model call stops counting as *outstanding* (#685).

A ``model_call_started`` with no matching finalize means the request left Vesta
and its cost was never learned. That is a permanent fact and reports must keep
saying so. But before this module the same record was also permanently *open*:
``active_calls`` had no expiry, no sweep, and no way to tell "started 200ms ago
by this process" from "orphaned by a crash last week". Nothing could safely
gate on the set, because one crashed run would have required confirmation on
every paid route forever, unclearable short of hand-editing the ledger.

The policy here draws that missing line. Two independent signals decide it, and
the ordering between them is the safety property:

* **Ownership.** A call this very process started and has not finalized is in
  flight, full stop. It is never aged out, whatever the clock says.
* **Age.** Every other call is bounded by :data:`ABANDON_AFTER_SECONDS`.

Liveness (is the owning PID still running?) is deliberately only an
*accelerator*: it can retire a call sooner, never keep one open longer. PIDs are
recycled, so a reused PID must not be able to hold a call open forever — that
would rebuild the exact bug this fixes. Because the age bound applies
unconditionally, a wrong or unavailable liveness answer can cost accuracy but
can never cost boundedness.

Ageing never invents a cost. It records ``cost_unknown`` and preserves #619
AC5's "unavailable, never zero"; and a late outcome that arrives after ageing
still supersedes it, so the truth wins whenever it turns up.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping


#: Identifies *this* interpreter. Written onto every call this process starts,
#: so ownership survives into the ledger and a restart is never mistaken for
#: the process that made the call. A fresh value per process is the point: PIDs
#: are recycled across reboots, this is not.
RUNTIME_ID = uuid.uuid4().hex

#: How long a call from another runtime may stay open before it is treated as
#: abandoned.
#:
#: Rationale, not a magic number: this bounds a *single provider turn* — one
#: request/response, not an agent session, which is many calls each finalized
#: on its own. Provider turns run in seconds and time out in minutes; six hours
#: is roughly two orders of magnitude of headroom over the slowest realistic
#: one, so a call still open at this age has overwhelmingly not survived — the
#: process died, the machine slept, or the finalize write failed. Long enough
#: that a genuine turn is never retired under it; short enough that a crash
#: clears within one working day rather than never.
ABANDON_AFTER_SECONDS = 6 * 60 * 60

#: Grace before a dead-owner verdict is acted on. Covers the window where a
#: record has been committed but the OS has not yet published the process, and
#: absorbs small clock skew between the writer and the sweeper. Only delays the
#: fast path; :data:`ABANDON_AFTER_SECONDS` still applies regardless.
OWNER_DEAD_GRACE_SECONDS = 60


class CallLiveness(Enum):
    """Why a dispatched call is, or is no longer, an open item."""

    #: Started by this process, not yet finalized. Never aged out.
    IN_FLIGHT_SELF = "in_flight_self"
    #: Another runtime's call, young enough that it may still be running.
    IN_FLIGHT = "in_flight"
    #: The owning process is gone; nothing can still be running there.
    ABANDONED_OWNER_GONE = "abandoned_owner_gone"
    #: Older than the bound. The reason no gate can hang forever.
    ABANDONED_EXPIRED = "abandoned_expired"

    @property
    def abandoned(self) -> bool:
        return self in _ABANDONED


_ABANDONED = frozenset(
    {CallLiveness.ABANDONED_OWNER_GONE, CallLiveness.ABANDONED_EXPIRED}
)

#: Recorded on an aged-out call so a report can say *why* without guessing.
ABANDON_REASONS = {
    CallLiveness.ABANDONED_OWNER_GONE: (
        "the process that dispatched it is no longer running"
    ),
    CallLiveness.ABANDONED_EXPIRED: (
        f"no outcome was recorded within {ABANDON_AFTER_SECONDS // 3600}h"
    ),
}


def _windows_pid_is_running(pid: int) -> bool | None:
    """Ask Windows whether ``pid`` exists, without ever signalling it.

    ``os.kill`` is not usable here: on Windows CPython implements it with
    ``TerminateProcess``, so the POSIX idiom ``os.kill(pid, 0)`` would *kill*
    the process it was meant to merely probe.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, OSError):  # pragma: no cover - ctypes ships with CPython
        return None

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            # ERROR_INVALID_PARAMETER (87) is the definitive "no such process".
            # Anything else (notably ERROR_ACCESS_DENIED) means a process does
            # exist and we simply may not look at it -- report alive, never
            # dead, so a permissions problem cannot retire a live call.
            return False if ctypes.get_last_error() == 87 else True
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        return None


def positive_pid(value: object) -> int | None:
    """A usable process id, or ``None``. The one place Vesta decides this.

    There were three -- here, in ``journal_store`` and in ``journal_liveness``
    -- and they disagreed: one truncated ``2.9`` to pid 2, and this one read
    ``True`` as pid 1, which exists on every system and so would be probed as
    a live owner (#818 review). Zero and negatives are not process ids on any
    platform Vesta runs on, and a probe of one asks a meaningless question and
    gets a meaningful-looking answer.

    The type is narrowed before converting rather than converted and caught: a
    bool and a float are refused outright, because each converts to an id
    nobody recorded. ``OverflowError`` is caught explicitly -- it is an
    ``ArithmeticError``, not a ``ValueError``, and ``int(float("inf"))``
    raises it.
    """

    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        pid = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return pid if pid > 0 else None


def pid_is_running(pid: int) -> bool | None:
    """``True``/``False`` if known, ``None`` if the platform will not say.

    ``None`` is a first-class answer, not an error: an unknown liveness falls
    back to the age bound rather than guessing. Guessing "dead" would retire a
    running call; guessing "alive" would hold an orphan open.
    """
    checked = positive_pid(pid)
    if checked is None:
        return None
    pid = checked
    if sys.platform == "win32":
        return _windows_pid_is_running(pid)
    try:
        os.kill(pid, 0)  # POSIX-only: signal 0 probes without delivering.
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Exists, owned by someone else.
    except OverflowError:
        # POSIX pid_t is a signed 32-bit int, so a pid past 2**31-1 cannot name
        # a process: os.kill raises OverflowError before it ever asks the
        # kernel. That is an ArithmeticError, not an OSError, so it escaped the
        # chain below and propagated out of a function documented never to
        # raise. Windows takes the same value through its own probe and never
        # reaches os.kill, which is why this only ever surfaced on Linux. Out
        # of range is a definite answer -- no such process can exist -- so
        # report it dead rather than unknown.
        return False
    except OSError:
        return None
    return True


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a ledger ISO timestamp, tolerating trailing ``Z`` and naive text."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def call_age_seconds(record: Mapping[str, Any], *, now: datetime) -> float | None:
    """Seconds since dispatch, or ``None`` when the record has no usable time."""
    started = parse_timestamp(record.get("created_at"))
    if started is None:
        return None
    return (now - started).total_seconds()


def classify_call(
    record: Mapping[str, Any],
    *,
    now: datetime | None = None,
    runtime_id: str | None = None,
    current_pid: int | None = None,
    pid_probe: Callable[[int], bool | None] | None = None,
) -> CallLiveness:
    """Decide whether a dispatched call is still an open item.

    Records written before ownership was tracked carry no owner, which means
    "not recorded" and never "abandoned" -- they fall through to the age bound,
    which retires them on exactly the same schedule as everything else.

    ``pid_probe`` resolves to :func:`pid_is_running` at call time rather than
    being bound as a default: a default argument is captured at definition, so
    a module-level replacement (a platform shim, or a test) would be silently
    ignored -- an injection point that looks real and is not.
    """
    pid_probe = pid_is_running if pid_probe is None else pid_probe
    now = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    runtime_id = RUNTIME_ID if runtime_id is None else runtime_id
    current_pid = os.getpid() if current_pid is None else current_pid

    owner_runtime = str(record.get("owner_runtime") or "")
    owner_pid_raw = record.get("owner_pid")
    try:
        owner_pid = int(owner_pid_raw) if owner_pid_raw is not None else None
    except (TypeError, ValueError):
        owner_pid = None

    # Ours and unfinished: in flight by construction. Checked before anything
    # time-based so a suspended or slow turn of our own is never retired.
    if owner_runtime and owner_runtime == runtime_id and owner_pid == current_pid:
        return CallLiveness.IN_FLIGHT_SELF

    age = call_age_seconds(record, now=now)

    # A record with no readable timestamp cannot be aged. Retire it only on a
    # definitive dead-owner verdict; otherwise it stays open rather than being
    # retired on a guess.
    if age is None:
        if owner_pid is not None and pid_probe(owner_pid) is False:
            return CallLiveness.ABANDONED_OWNER_GONE
        return CallLiveness.IN_FLIGHT

    if age >= ABANDON_AFTER_SECONDS:
        return CallLiveness.ABANDONED_EXPIRED

    if (
        owner_pid is not None
        and age >= OWNER_DEAD_GRACE_SECONDS
        and pid_probe(owner_pid) is False
    ):
        return CallLiveness.ABANDONED_OWNER_GONE

    return CallLiveness.IN_FLIGHT


def owner_fields() -> dict[str, Any]:
    """Ownership stamp for a call this process is about to dispatch."""
    return {"owner_pid": os.getpid(), "owner_runtime": RUNTIME_ID}
