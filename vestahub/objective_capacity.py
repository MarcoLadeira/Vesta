"""Host-wide worker capacity using OS locks, without another task state store."""

from contextlib import contextmanager
from pathlib import Path
import threading

from .atomic_io import InterprocessLockTimeout, interprocess_transaction

HOST_WORKER_LIMIT = 4


@contextmanager
def host_slot(cancel: threading.Event, *, directory=None, limit=HOST_WORKER_LIMIT):
    if type(limit) is not int or not 1 <= limit <= HOST_WORKER_LIMIT:
        raise ValueError("Host worker limit must be between 1 and 4")
    directory = (
        Path(directory)
        if directory is not None
        else Path.home() / ".vestahub" / "runtime" / "agent-slots"
    )
    while not cancel.is_set():
        for index in range(limit):
            lease = interprocess_transaction(
                directory / f"worker-{index}", timeout_seconds=0
            )
            try:
                lease.__enter__()
            except InterprocessLockTimeout:
                continue
            try:
                if cancel.is_set():
                    raise InterruptedError(
                        "Cancelled while waiting for worker capacity"
                    )
                yield
            finally:
                lease.__exit__(None, None, None)
            return
        cancel.wait(0.1)
    raise InterruptedError("Cancelled while waiting for worker capacity")
