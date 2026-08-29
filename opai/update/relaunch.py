"""Restart OPai into a build that is already on disk.

Claude Code and Codex both stop here: they install the new version and tell
you to run the command again yourself. Finishing the job is the difference
between "the update is downloaded" and "you are running it".

The relaunch is deliberately indirect. A new instance started while this one
is still alive would race it for the QtWebEngine profile lock and the
updater's own operation lease, so a small detached supervisor waits for this
process to exit first and only then starts the replacement. That supervisor
is the only thing that outlives us, and it gives up on its own if we never
quit -- a stuck window must never leave a process spinning forever.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The supervisor is passed as source rather than written to a file: nothing
# to clean up, nothing left behind to be tampered with between write and run.
_SUPERVISOR = """
import os, subprocess, sys, time

pid = int(sys.argv[1])
deadline = time.monotonic() + float(sys.argv[2])
command = sys.argv[3:]


def alive(target):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, target)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(target, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


while alive(pid):
    if time.monotonic() >= deadline:
        raise SystemExit(0)  # never outlive a window that refused to close
    time.sleep(0.25)

subprocess.Popen(command, close_fds=True)
"""


def relaunch_command(
    *,
    executable: str | None = None,
    argv: list[str] | None = None,
    frozen: bool | None = None,
) -> list[str] | None:
    """Reconstruct the command that started this OPai, or ``None``.

    Three shapes reach here, and they do not share a reconstruction:

    * a frozen bundle, where ``sys.executable`` *is* the application;
    * a generated launcher (``opai-gui.exe`` from ``[project.gui_scripts]``),
      which carries its subcommand inside the entry point rather than in
      ``argv`` -- so it can only be re-run as itself;
    * a module launch (``pythonw -m opai gui``), where ``argv[1:]`` already
      holds the subcommand and the *same* interpreter must be reused, which on
      Windows keeps a windowed process windowed and anywhere keeps a
      virtualenv's OPai from being replaced by some other Python's.

    Anything else returns ``None`` and the caller leaves the restart to the
    user. That is deliberate: an app that closes itself and then fails to come
    back is far worse than one that asks you to start it again, so this
    guesses at nothing it cannot read off the process it is running in.
    """

    executable = executable if executable is not None else sys.executable
    argv = list(argv if argv is not None else sys.argv)
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        return [executable, *argv[1:]] if executable else None
    if not argv:
        return None
    launcher = Path(argv[0])
    suffix = launcher.suffix.casefold()
    if suffix in {".exe", ".bat", ".cmd"}:
        # The subcommand lives in the entry point, not in argv, so this is
        # re-runnable only as itself -- never as a module with argv[1:].
        return [str(launcher), *argv[1:]] if launcher.is_file() else None
    if suffix in {".py", ".pyw"} and executable:
        return [executable, "-m", "opai", *argv[1:]]
    return None


def schedule_relaunch(
    command: list[str],
    *,
    pid: int | None = None,
    give_up_after_seconds: float = 120.0,
    spawn=subprocess.Popen,
) -> bool:
    """Arm a detached supervisor that restarts OPai once this process exits.

    Returns whether the supervisor started. It is armed *before* the quit so a
    failure to arm can still be reported honestly -- an app that closed itself
    and then failed to come back is the one outcome worth preventing.
    """

    if not command:
        return False
    if getattr(sys, "frozen", False):
        # The supervisor is hosted by ``sys.executable -c``, and in a frozen
        # bundle that executable is the application itself: it has no ``-c``.
        # Rather than spawn a second copy of OPai to babysit the first, say
        # the restart is unavailable and let the user do it.
        return False
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console is inherited
        # and Ctrl-C in whatever launched us never reaches the supervisor.
        creationflags = 0x00000008 | 0x00000200
    try:
        spawn(
            [
                sys.executable,
                "-c",
                _SUPERVISOR,
                str(pid if pid is not None else os.getpid()),
                str(float(give_up_after_seconds)),
                *command,
            ],
            close_fds=True,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except (OSError, ValueError):
        return False
    return True
