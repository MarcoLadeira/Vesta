"""Process-tree isolation and full-tree termination for cancellable runs (#108).

A visible Stop or window close must terminate the *entire* agent process tree,
not just the direct CLI child. Provider CLIs spawn grandchildren (language
servers, git, sub-agents) that can survive a plain ``terminate()`` and keep
spending or mutating the repo after Vesta reports the run stopped.

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
from pathlib import Path
import signal
import subprocess  # nosec B404 - argv-only taskkill, never a shell
import sys
import time
import weakref
from typing import Any, Callable

from .proc import no_window_kwargs

#: Attributes used to carry a tree's handle on the ``Popen`` that owns it.
#: Keeping them on the object (rather than in a module-level map) means the
#: handle's lifetime is the process's lifetime, with no registry to leak.
_JOB_ATTR = "_vesta_job_handle"  # Windows: a job object
_PGID_ATTR = "_vesta_pgid"  # POSIX: the process group the child leads
_SUBREAPER_ATTR = "_vesta_subreaper"  # Linux: exclusive guardian child custody

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
    if os.environ.get("VESTA_OBJECTIVE_TREE_CUSTODY") == "posix-group":
        # The guardian owns the outer group. Nested provider/check processes
        # must stay in it so supervisor loss cannot orphan a new session.
        return {}
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
        k.QueryInformationJobObject.restype = wintypes.BOOL
        k.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        k.CloseHandle.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        return k
    except (ImportError, OSError, AttributeError, ValueError):
        return None


def _new_kill_on_close_job(k: Any) -> int | None:
    """Create a job whose members are killed when the last handle closes.

    ``KILL_ON_JOB_CLOSE`` is the belt to :func:`terminate_tree`'s braces: even
    if Vesta is killed outright and never gets to terminate anything, Windows
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
            group = (
                os.getpgid(pid)
                if os.environ.get("VESTA_OBJECTIVE_TREE_CUSTODY") == "posix-group"
                else pid
            )
            setattr(proc, _PGID_ATTR, group)
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


def _linux_children() -> set[int]:
    """Read our kernel child lists; an unreadable list is never emptiness."""
    children = set()
    tasks = list(Path("/proc/self/task").iterdir())
    if not tasks:
        raise OSError("Guardian task list is unavailable")
    for task in tasks:
        children.update(int(pid) for pid in (task / "children").read_text().split())
    return children


def _child_subreaper(*, enable: bool = False) -> bool:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if enable and libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "Unable to establish child subreaper")
    value = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(value), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Unable to verify child subreaper")
    return value.value == 1


class _LinuxSubreaper:
    """Custody for one dedicated guardian, including setsid/double-fork children.

    The guardian must spawn only its gated worker. Linux reparents every orphan
    descendant to this subreaper, even when it leaves the original process group.
    No child is reaped between enumerating and signalling it, so its PID cannot
    be reused during that interval. This is not a sandbox against hostile code
    with permission to kill the guardian or change the guardian's credentials.
    """

    def __init__(self):
        if _linux_children():
            raise RuntimeError("Guardian already has unrelated children")
        if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
            raise RuntimeError("Guardian requires exclusive child reaping")
        if not _child_subreaper(enable=True):
            raise RuntimeError("Child subreaper could not be established")
        self.owner_pid = os.getpid()
        self.root_pid = None
        self.drained = False

    def terminate(self, proc: Any, *, timeout: float) -> bool:
        if self.drained:
            return True
        deadline = time.monotonic() + timeout
        while True:
            try:
                # Reap the direct Popen child through Popen before waitpid(-1).
                # Killing each generation causes even escaped descendants to
                # become direct children, which the next iteration can reach.
                proc.poll()
                children = _linux_children()
                for pid in children:
                    os.kill(pid, signal.SIGKILL)
                if proc.poll() is not None:
                    while True:
                        try:
                            pid, _ = os.waitpid(-1, os.WNOHANG)
                        except ChildProcessError:
                            # ECHILD accounts for live children and unreaped
                            # zombies. Confirm /proc access still works too.
                            if _linux_children():
                                return False
                            self.drained = True
                            return True
                        if pid == 0:
                            break
            except (OSError, ValueError):
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


def objective_runtime_support() -> dict[str, Any]:
    supported = sys.platform in {"win32", "linux"}
    return {
        "supported": supported,
        "platform": sys.platform,
        "reason": ""
        if supported
        else (
            "Multi-agent execution is unavailable on this host. Use Windows or "
            "Linux; existing objectives remain available for inspection."
        ),
    }


def prepare_guardian_custody() -> _LinuxSubreaper | None:
    """Establish full descendant custody before spawning a gated worker.

    Process groups alone do not contain setsid() children. Other POSIX hosts
    therefore fail closed until an equivalent descendant custody API exists.
    """
    if sys.platform == "win32":
        return None
    if sys.platform != "linux":
        raise RuntimeError("Full worker tree custody is unavailable on this platform")
    return _LinuxSubreaper()


def adopt_guardian(proc: Any, custody: _LinuxSubreaper | None) -> Any:
    adopt(proc)
    if custody is not None:
        if custody.owner_pid != os.getpid() or custody.root_pid is not None:
            raise RuntimeError("Guardian custody cannot be shared or reused")
        custody.root_pid = proc.pid
        setattr(proc, _SUBREAPER_ATTR, custody)
    return proc


def custody_kind(proc: Any) -> str:
    """Require a retained tree identity before opening a worker's start gate."""
    if sys.platform == "win32":
        job = getattr(proc, _JOB_ATTR, None)
        if isinstance(job, _Job) and job.handle:
            return "windows-job"
    elif sys.platform == "linux":
        custody = getattr(proc, _SUBREAPER_ATTR, None)
        if (
            isinstance(custody, _LinuxSubreaper)
            and custody.owner_pid == os.getpid()
            and custody.root_pid == proc.pid
            and _child_subreaper()
        ):
            return "linux-subreaper"
    raise RuntimeError("Worker tree custody could not be established")


def _job_active_processes(job: _Job) -> int:
    import ctypes
    from ctypes import wintypes

    class Accounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    info = Accounting()
    kernel = _kernel32()
    if kernel is None or not kernel.QueryInformationJobObject(
        job.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None
    ):
        raise OSError("Unable to query retained worker job")
    return int(info.ActiveProcesses)


def terminate_tree_confirmed(proc: Any, *, timeout: float = 10.0) -> bool:
    """Kill custody members and prove emptiness before releasing its identity.

    Unlike the legacy best-effort cleanup, root exit and a successful kill
    request are not proof. On failure the job handle stays open for a retry.
    """
    try:
        kind = custody_kind(proc)
    except (OSError, RuntimeError, ValueError):
        return False
    if kind == "linux-subreaper":
        return getattr(proc, _SUBREAPER_ATTR).terminate(proc, timeout=timeout)
    deadline = time.monotonic() + timeout
    job = getattr(proc, _JOB_ATTR, None)
    kernel = _kernel32()
    if kernel is None or not kernel.TerminateJobObject(job.handle, 1):
        return False
    while True:
        proc.poll()
        try:
            empty = _job_active_processes(job) == 0
            if empty:
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
                job.close()
                return True
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


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

    Killing our own group would take Vesta down with the run it is cleaning up.
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
