"""Shutdown/cancel lifecycle shared by both desktop GUI hosts (#140, #141).

Closing the window (or pressing Stop) must never leave a paid CLI running in
the background, and Qt must never tear down a live worker thread (the
"QThread: Destroyed while thread is still running" crash). These helpers are
Qt-free — they operate duck-typed on anything exposing the ``QThread`` subset
(``isRunning()`` / ``wait(msecs) -> bool``) — so the policy is unit-tested
without a display, matching the rest of the ``gui_*`` helper layer.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable, Mapping

# One bounded budget for quitting: long enough for a cancelled CLI to die
# (runners poll their cancel Event ~5x/sec), short enough that closing the
# window never feels hung.
SHUTDOWN_BUDGET_MS = 3000


def start_tracked_worker(workers: list[Any], worker: Any) -> None:
    """Start a Qt-like worker and release its owner reference on completion."""

    def release() -> None:
        if worker in workers:
            workers.remove(worker)

    worker.finished.connect(release)
    workers.append(worker)
    worker.start()


def signal_cancels(cancels: Mapping[str, threading.Event]) -> int:
    """Set every pending cancel event; returns how many were newly signalled."""
    signalled = 0
    for event in list(cancels.values()):
        if event is not None and not event.is_set():
            event.set()
            signalled += 1
    return signalled


def drain_workers(
    workers: Iterable[Any], *, total_ms: int = SHUTDOWN_BUDGET_MS
) -> list[Any]:
    """Wait (bounded, shared budget) for worker threads to finish.

    Returns the workers still running when the budget ran out. Callers must
    keep references to those stragglers alive instead of letting Qt destroy a
    running thread — a leaked-but-alive thread is a bug; a destroyed one is a
    crash.
    """
    deadline = time.monotonic() + max(0, total_ms) / 1000.0
    stragglers: list[Any] = []
    for worker in list(workers):
        try:
            if not worker.isRunning():
                continue
            remaining_ms = int(max(0.0, deadline - time.monotonic()) * 1000)
            if not worker.wait(remaining_ms):
                stragglers.append(worker)
        except RuntimeError:
            # The underlying C++ thread object is already gone — nothing to wait for.
            continue
    return stragglers
