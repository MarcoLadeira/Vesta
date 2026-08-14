"""Shared cross-process transactions and crash-safe state-file writes."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


_LOCK_RETRY_SECONDS = 0.01
_REPLACE_ATTEMPTS = 20
_DIRECTORY_OPEN_FLAGS: int | None = (
    None if os.name == "nt" else os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
)
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS_PROCESS_ID = os.getpid()
_HELD_PATHS = threading.local()


class InterprocessLockTimeout(TimeoutError):
    """Raised when a state transaction cannot acquire its lock in time."""


def _path_key(target: Path) -> str:
    return os.path.normcase(str(target.resolve(strict=False)))


def _path_lock(key: str) -> threading.RLock:
    global _PATH_LOCKS, _PATH_LOCKS_GUARD, _PATH_LOCKS_PROCESS_ID

    process_id = os.getpid()
    if process_id != _PATH_LOCKS_PROCESS_ID:
        _PATH_LOCKS = {}
        _PATH_LOCKS_GUARD = threading.Lock()
        _PATH_LOCKS_PROCESS_ID = process_id
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _held_paths() -> set[str]:
    process_id = os.getpid()
    if getattr(_HELD_PATHS, "process_id", None) != process_id:
        held = set()
        _HELD_PATHS.process_id = process_id
        _HELD_PATHS.paths = held
        return held
    return _HELD_PATHS.paths


def _try_file_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _timeout(target: Path) -> InterprocessLockTimeout:
    return InterprocessLockTimeout(
        f"timed out waiting for interprocess transaction lock: {target}"
    )


def _replace_with_retry(temporary: Path, target: Path) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, target)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_LOCK_RETRY_SECONDS * (attempt + 1))


def _sync_parent_directory(directory: Path) -> None:
    if _DIRECTORY_OPEN_FLAGS is None:
        return
    descriptor = os.open(directory, _DIRECTORY_OPEN_FLAGS)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def interprocess_transaction(
    target: Path,
    *,
    timeout_seconds: float = 60.0,
) -> Iterator[None]:
    """Serialize a state-file transaction across threads and processes."""

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    key = _path_key(target)
    local_lock = _path_lock(key)
    timeout = max(0.0, float(timeout_seconds))
    deadline = time.monotonic() + timeout
    if not local_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise _timeout(target)

    held_paths = _held_paths()
    if key in held_paths:
        try:
            yield
        finally:
            local_lock.release()
        return

    lock_path = target.with_name(f"{target.name}.lock")
    try:
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()

            acquired = False
            try:
                while not (acquired := _try_file_lock(handle)):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise _timeout(target)
                    time.sleep(min(_LOCK_RETRY_SECONDS, remaining))
                held_paths.add(key)
                try:
                    yield
                finally:
                    held_paths.remove(key)
            finally:
                if acquired:
                    _unlock_file(handle)
    finally:
        local_lock.release()


def atomic_write_text(
    target: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Flush text to a unique sibling temporary file, then atomically replace.

    ``mode`` sets the permission bits on the temporary file before the replace,
    so the published file never carries the temporary file's private mode.
    """

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        _replace_with_retry(temporary, target)
        temporary = None
        _sync_parent_directory(target.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
