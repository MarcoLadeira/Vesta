"""Process-tree isolation and full-tree termination for cancellable runs (#108).

A visible Stop or window close must terminate the *entire* agent process tree,
not just the direct CLI child. Provider CLIs spawn grandchildren (language
servers, git, sub-agents) that can survive a plain ``terminate()`` and keep
spending or mutating the repo after OPai reports the run stopped.

Three pieces, all platform-aware and injectable for tests:

- :func:`isolated_group_kwargs` puts a spawned child in its own process group
  (Windows ``CREATE_NEW_PROCESS_GROUP``) or session (POSIX ``setsid`` via
  ``start_new_session``), so the whole tree can be signalled at once.
- :func:`adopt` records how to reach the tree **after the root has already
  died** — the case that leaves orphans. On Windows it binds the child to a job
  object, because ``taskkill /T`` walks the parent→child relationship and can
  no longer find survivors once the root is gone, while job membership outlives
  it. On POSIX it records the process group id, because ``getpgid`` fails on a
  dead leader. Call it only on a process spawned with
  :func:`isolated_group_kwargs`, whose guarantees it relies on.
- :func:`terminate_tree` kills that tree: the job object where one exists, the
  process group on POSIX, ``taskkill /T`` otherwise. It is idempotent and
  race-safe — safe to call before spawn completes, during streaming, and after
  the child already exited.

**A dead root does not mean a clean tree** (#295 gate 5). Termination therefore
reaps the tree *before* deciding the direct child needs nothing further; the
provider-crash case is precisely when orphans are left behind.

No paid or provider calls happen here; unit tests inject a fake process and a
fake tree killer, and ``test_orphan_processes.py`` proves the real behaviour
against real processes.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # nosec B404 - argv-only taskkill, never a shell
import sys
import weakref
from typing import Any, Callable

from .proc import no_window_kwargs

#: Attributes used to carry a tree's handle on the ``Popen`` that owns it.
#: Keeping them on the object (rather than in a module-level map) means the
#: handle's lifetime is the process's lifetime, with no registry to leak.
_JOB_ATTR = "_opai_job_handle"  # Windows: a job object
_PGID_ATTR = "_opai_pgid"  # POSIX: the process group the child leads

# Win32 constants (winnt.h / jobapi2.h).
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOBOBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


def isolated_group_kwargs(*, no_window: bool = True) -> dict[str, Any]:
    """Return ``subprocess`` kwargs that isolate the child's process tree.

    Windows: a new process group (plus ``CREATE_NO_WINDOW`` so the GUI never
    flashes a console). POSIX: a new session so the child leads its own
    process group and ``killpg`` reaches every descendant.
    """
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if no_window:
            flags |= subprocess.CREATE_NO_WINDOW
        return {"creationflags": flags}
    # POSIX: start_new_session=True calls setsid() in the child.
    return {"start_new_session": True}


# --------------------------------------------------------------------------
# Windows job objects
# --------------------------------------------------------------------------


def _kernel32() -> Any:
    """Return kernel32 with the prototypes we use declared, or ``None``.

    Declaring ``restype`` matters: handles are pointer-sized, and ctypes'
    default ``c_int`` return would truncate them on 64-bit Windows — producing a
    handle that looks valid and silently refers to nothing.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.TerminateJobObject.restype = wintypes.BOOL
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        return k
    except (ImportError, OSError, AttributeError, ValueError):
        return None


def _new_kill_on_close_job(k: Any) -> int | None:
    """Create a job whose members are killed when the last handle closes.

    ``KILL_ON_JOB_CLOSE`` is the belt to :func:`terminate_tree`'s braces: even
    if OPai is killed outright and never gets to terminate anything, Windows
    closes the handle on exit and reaps the tree.
    """
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job = k.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(
        job,
        _JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        k.CloseHandle(job)
        return None
    return int(job)


class _Job:
    """Owns a Windows job handle and releases it exactly once.

    Closing matters twice over. It is a kernel handle, so leaking one per run
    accumulates over a long session; and because the job is kill-on-close,
    releasing it is also what reaps any member still running when nothing
    holds the process any more.
    """

    __slots__ = ("handle",)

    def __init__(self, handle: int) -> None:
        self.handle = handle

    def close(self, *, terminate: bool = False) -> None:
        handle, self.handle = self.handle, 0
        if not handle:
            return  # already released; a retry must not double-close
        k = _kernel32()
        if k is None:
            return
        if terminate:
            with contextlib.suppress(Exception):  # noqa: BLE001 - best effort
                k.TerminateJobObject(handle, 1)
        with contextlib.suppress(Exception):  # noqa: BLE001
            k.CloseHandle(handle)


def adopt(proc: Any) -> Any:
    """Bind a freshly spawned child to a kill-on-close job (Windows).

    Returns ``proc`` so call sites can wrap ``Popen(...)`` directly. Best
    effort by design: if the job cannot be created or assigned — an old
    Windows without nested-job support, a locked-down policy — the process
    still runs and :func:`terminate_tree` still falls back to ``taskkill /T``.
    Refusing to launch would trade a cleanup weakness for an outage.
    """
    if proc is None:
        return proc
    pid = getattr(proc, "pid", None)
    # Only real processes are adopted. A test double's ``pid`` can coerce to an
    # int that belongs to something else entirely, and what we record here is a
    # live kill switch — it must never point at a process we did not spawn.
    if not isinstance(pid, int) or pid <= 0:
        return proc

    if sys.platform != "win32":
        # ``start_new_session`` made the child a session and group leader, so
        # its group id *is* its pid. Recording it now rather than looking it up
        # later is the point: ``getpgid`` fails once the leader dies, which is
        # exactly the crash case where survivors must still be reachable.
        with contextlib.suppress(Exception):  # noqa: BLE001
            setattr(proc, _PGID_ATTR, pid)
        return proc

    k = _kernel32()
    if k is None:
        return proc
    try:
        job = _new_kill_on_close_job(k)
    except Exception:  # noqa: BLE001 - never block a launch on cleanup setup
        return proc
    if job is None:
        return proc

    handle = None
    assigned = False
    try:
        handle = k.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid))
        if handle:
            assigned = bool(k.AssignProcessToJobObject(job, handle))
    except Exception:  # noqa: BLE001
        assigned = False
    finally:
        if handle:
            with contextlib.suppress(Exception):  # noqa: BLE001
                k.CloseHandle(handle)

    if not assigned:
        with contextlib.suppress(Exception):  # noqa: BLE001
            k.CloseHandle(job)
        return proc

    owned = _Job(job)
    try:
        setattr(proc, _JOB_ATTR, owned)
    except Exception:  # noqa: BLE001 - a proc we cannot annotate cannot be tracked
        owned.close()
        return proc
    # Backstop for the ordinary path, where a run ends without a Stop and
    # nothing calls terminate_tree: releasing the handle when the process
    # object is collected both frees it and reaps anything still in the job.
    with contextlib.suppress(TypeError):  # not every object is weak-referenceable
        weakref.finalize(proc, owned.close)
    return proc


def _terminate_job(proc: Any) -> bool:
    """Terminate and release the job owning ``proc``. True if one existed.

    Membership, not parentage, so this reaches survivors of a crashed root.
    """
    job = getattr(proc, _JOB_ATTR, None)
    if not isinstance(job, _Job):
        return False
    job.close(terminate=True)
    with contextlib.suppress(Exception):  # noqa: BLE001
        setattr(proc, _JOB_ATTR, None)
    return True


# --------------------------------------------------------------------------
# Termination
# --------------------------------------------------------------------------


def _default_tree_killer(pid: int) -> None:
    """Kill the whole tree rooted at ``pid`` (best effort, never raises)."""
    if sys.platform == "win32":
        # taskkill /T terminates the tree; /F forces it. Fixed argv, no shell.
        with contextlib.suppress(OSError, ValueError, subprocess.SubprocessError):
            subprocess.run(  # nosec B603 B607
                ["taskkill", "/F", "/T", "/PID", str(int(pid))],
                capture_output=True,
                check=False,
                timeout=10,
                **no_window_kwargs(),
            )
        return
    # POSIX: signal the whole process group the child leads. The child was
    # spawned with setsid, so its pid *is* the group id — looking the id up
    # would fail exactly when the leader has died, which is the case that
    # leaves orphans.
    _killpg(int(pid), signal.SIGTERM)


def _killpg(pgid: int, sig: int) -> None:
    """Signal a process group, refusing to signal our own.

    Killing our own group would take OPai down with the run it is cleaning up.
    Cheap to check and catastrophic to miss, so it is checked every time rather
    than reasoned about at each call site.
    """
    if pgid <= 0:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if pgid == os.getpgid(0):
            return
        os.killpg(pgid, sig)


def _default_group_sigkill(pid: int) -> None:
    """POSIX hard-kill of the process group; a no-op on Windows."""
    if sys.platform == "win32":
        return
    _killpg(int(pid), signal.SIGKILL)


def terminate_tree(
    proc: Any,
    *,
    timeout: float = 2.0,
    tree_killer: Callable[[int], None] | None = None,
    group_sigkill: Callable[[int], None] | None = None,
) -> None:
    """Terminate the whole process tree rooted at ``proc`` (idempotent, #108).

    Reaps the tree first — job object, then process group/tree — and only then
    deals with the direct child. That order is the point: a child that has
    already exited is *not* evidence of a clean tree, because a crashed provider
    CLI is exactly when its grandchildren are left running (#295 gate 5).

    Safe to call when ``proc`` is ``None``, has already exited, or has already
    been terminated once.
    """
    if proc is None:
        return

    pid = getattr(proc, "pid", None)
    killer = tree_killer or _default_tree_killer
    hard = group_sigkill or _default_group_sigkill
    group = getattr(proc, _PGID_ATTR, None)
    # ``returncode`` is read *before* our own poll(): once a process has been
    # reaped its pid is free for reuse, so signalling a bare recycled pid could
    # hit an unrelated process. A group recorded by :func:`adopt` has no such
    # problem — it names a group we created, not a pid we happen to remember —
    # which is why adoption is what makes the crash case safe to clean up.
    already_reaped = getattr(proc, "returncode", None) is not None

    if _terminate_job(proc):
        # Job membership covers the whole tree, including a dead root's
        # survivors, so no pid-based follow-up is needed for them.
        pass
    elif group is not None or (pid is not None and not already_reaped):
        target = group if group is not None else pid
        with contextlib.suppress(Exception):  # noqa: BLE001 - kill is best effort
            killer(int(target))

    # Then the direct child, so a fake/limited proc still stops.
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                target = group if group is not None else pid
                if target is not None:
                    with contextlib.suppress(Exception):  # noqa: BLE001
                        hard(int(target))
    except (OSError, ValueError):
        return
