"""Process-tree isolation and full-tree termination for cancellable runs (#108).

A visible Stop or window close must terminate the *entire* agent process tree,
not just the direct CLI child. Provider CLIs spawn grandchildren (language
servers, git, sub-agents) that can survive a plain ``terminate()`` and keep
spending or mutating the repo after OPai reports the run stopped.

Two pieces, both platform-aware and injectable for tests:

- :func:`isolated_group_kwargs` puts a spawned child in its own process group
  (Windows ``CREATE_NEW_PROCESS_GROUP``) or session (POSIX ``setsid`` via
  ``start_new_session``), so the whole tree can be signalled at once.
- :func:`terminate_tree` kills that tree: POSIX signals the process group,
  Windows uses ``taskkill /T``. It is idempotent and race-safe — safe to call
  before spawn completes, during streaming, and after the child already exited.

No paid or provider calls happen here; tests inject a fake process and a fake
tree killer.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # nosec B404 - argv-only taskkill, never a shell
import sys
from typing import Any, Callable

from .proc import no_window_kwargs


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
    # POSIX: signal the whole process group the child leads (setsid).
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        pgid = os.getpgid(int(pid))
        os.killpg(pgid, signal.SIGTERM)


def _default_group_sigkill(pid: int) -> None:
    """POSIX hard-kill of the process group; a no-op on Windows."""
    if sys.platform == "win32":
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(int(pid)), signal.SIGKILL)


def terminate_tree(
    proc: Any,
    *,
    timeout: float = 2.0,
    tree_killer: Callable[[int], None] | None = None,
    group_sigkill: Callable[[int], None] | None = None,
) -> None:
    """Terminate the whole process tree rooted at ``proc`` (idempotent, #108).

    Signals the process group/tree first (grandchildren included), then hard-
    kills the direct child if it lingers. Safe to call when ``proc`` is
    ``None`` or has already exited, and safe to call repeatedly.
    """
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except (OSError, ValueError):
        return

    killer = tree_killer or _default_tree_killer
    hard = group_sigkill or _default_group_sigkill
    pid = getattr(proc, "pid", None)
    if pid is not None:
        with contextlib.suppress(Exception):  # noqa: BLE001 - kill is best effort
            killer(int(pid))

    # Always follow up on the direct child so a fake/limited proc still stops.
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                if pid is not None:
                    with contextlib.suppress(Exception):  # noqa: BLE001
                        hard(int(pid))
    except (OSError, ValueError):
        pass
