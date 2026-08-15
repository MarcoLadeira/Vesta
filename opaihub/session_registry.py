"""A single-flight registry of active provider/tool sessions (#169).

OPai already has good per-call process hygiene (CREATE_NO_WINDOW, cancel Events
that kill CLIs, a bounded shutdown drain). What was missing is a *global* view:
which sessions are running right now, a guarantee that a retry never runs two
processes for the same request, and a sweep for child PIDs orphaned by a crash.

This module is that view. It is deliberately dependency-free and thread-safe so
the runner layer can own one registry and every surface (GUI "active sessions"
indicator, CLI, cancellation) reads the same truth. Process termination and
liveness are injected, so the whole thing is hermetically testable.
"""

from __future__ import annotations

import threading
import time
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .atomic_io import atomic_write_text


# Session states. Only ``running`` sessions are "active"; the rest are terminal.
RUNNING = "running"
SUPERSEDED = "superseded"  # a newer request with the same id replaced this one
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
_TERMINAL = {SUPERSEDED, DONE, FAILED, CANCELLED}


@dataclass
class Session:
    """One live (or just-finished) provider/tool run, keyed by request id."""

    request_id: str
    provider: str
    started_at: float
    state: str = RUNNING
    pid: int | None = None
    cancel: Any = None  # a threading.Event, or anything with .set()
    finished_at: float | None = None
    #: Identifies the *logical* request (#295 gate 3). Two submissions of the
    #: same task carry the same key even though their request ids differ, which
    #: is what lets a double-click or a reconnect replay be recognised as one.
    admission_key: str | None = None

    @property
    def active(self) -> bool:
        return self.state == RUNNING

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        clock = time.monotonic() if now is None else now
        end = self.finished_at if self.finished_at is not None else clock
        return {
            "request_id": self.request_id,
            "provider": self.provider,
            "state": self.state,
            "pid": self.pid,
            "elapsed_ms": max(0, int((end - self.started_at) * 1000)),
            "admission_key": self.admission_key,
        }


class SessionRegistry:
    """Thread-safe, single-flight registry of active sessions."""

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
        durable_root: Path | None = None,
        process_id: int | None = None,
    ) -> None:
        self._now = now
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._durable_root = Path(durable_root) if durable_root is not None else None
        self._process_id = os.getpid() if process_id is None else int(process_id)

    def _durable_path(self, request_id: str) -> Path | None:
        if self._durable_root is None:
            return None
        token = hashlib.sha256(
            f"{self._process_id}:{request_id}".encode("utf-8")
        ).hexdigest()[:32]
        return self._durable_root / f"{token}.json"

    def _persist_running(self, session: Session) -> None:
        path = self._durable_path(session.request_id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                path,
                json.dumps(
                    {
                        "schema_version": 1,
                        "pid": self._process_id,
                        "provider": session.provider,
                        "started_at": time.time(),
                    },
                    sort_keys=True,
                )
                + "\n",
                mode=0o600,
            )
        except OSError:
            pass

    def _clear_durable(self, request_id: str) -> None:
        path = self._durable_path(request_id)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def start(
        self,
        request_id: str,
        provider: str,
        *,
        cancel: Any = None,
        pid: int | None = None,
        admission_key: str | None = None,
    ) -> Session:
        """Register a new running session for ``request_id``.

        Single-flight (#169): if a session for the same id is already running —
        a retry — its cancel Event is fired and it is marked ``superseded`` so
        two processes never run for one request. Returns the new session.

        This keys on the request *id*, which only dedups callers that already
        know two submissions are the same. Use :meth:`claim` to dedup by
        admission key, which catches the callers that do not (#295 gate 3).
        """
        rid = str(request_id)
        with self._lock:
            return self._start_locked(
                rid, provider, cancel=cancel, pid=pid, admission_key=admission_key
            )

    def _start_locked(
        self,
        rid: str,
        provider: str,
        *,
        cancel: Any = None,
        pid: int | None = None,
        admission_key: str | None = None,
    ) -> Session:
        """Body of :meth:`start`. Caller must hold ``self._lock``."""
        previous = self._sessions.get(rid)
        if previous is not None and previous.active:
            self._signal_cancel(previous)
            previous.state = SUPERSEDED
            previous.finished_at = self._now()
        session = Session(
            request_id=rid,
            provider=str(provider or "unknown"),
            started_at=self._now(),
            cancel=cancel,
            pid=pid,
            admission_key=(str(admission_key) if admission_key else None),
        )
        self._sessions[rid] = session
        self._persist_running(session)
        return session

    def claim(
        self,
        admission_key: str,
        request_id: str,
        provider: str = "pipeline",
        *,
        cancel: Any = None,
        pid: int | None = None,
    ) -> str | None:
        """Atomically start a run for ``admission_key``, or report the live one.

        Returns ``None`` when the run was started (the key was free), or the
        request id of the **already active** equivalent run when it was not.

        Test-and-set under one lock acquisition is the entire point (#295 gate
        3). A double-click is two near-simultaneous submissions, so a caller
        doing ``active_by_key()`` and then ``start()`` would leave a window in
        which both observe an idle key and both launch a paid run.
        """
        key = str(admission_key or "")
        rid = str(request_id)
        with self._lock:
            if key:
                existing = self._active_by_key_locked(key)
                if existing is not None and existing.request_id != rid:
                    return existing.request_id
            self._start_locked(
                rid, provider, cancel=cancel, pid=pid, admission_key=key or None
            )
            return None

    def _active_by_key_locked(self, admission_key: str) -> Session | None:
        """Caller must hold ``self._lock``."""
        for session in self._sessions.values():
            if session.active and session.admission_key == admission_key:
                return session
        return None

    def active_by_key(self, admission_key: str) -> Session | None:
        """The running session for ``admission_key``, if any.

        Read-only: to *act* on the answer, use :meth:`claim`, which does the
        check and the start without releasing the lock in between.
        """
        key = str(admission_key or "")
        if not key:
            return None
        with self._lock:
            return self._active_by_key_locked(key)

    def finish(self, request_id: str, *, state: str = DONE) -> None:
        """Mark a session terminal. A superseded session stays superseded (a late
        finish from the cancelled predecessor must not overwrite the truth)."""
        rid = str(request_id)
        end_state = state if state in _TERMINAL else DONE
        with self._lock:
            session = self._sessions.get(rid)
            if session is None or not session.active:
                return
            session.state = end_state
            session.finished_at = self._now()
            self._clear_durable(rid)

    def cancel(self, request_id: str) -> bool:
        """Fire a running session's cancel Event and mark it cancelled. Returns
        True if a running session was found."""
        rid = str(request_id)
        with self._lock:
            session = self._sessions.get(rid)
            if session is None or not session.active:
                return False
            self._signal_cancel(session)
            session.state = CANCELLED
            session.finished_at = self._now()
            self._clear_durable(rid)
            return True

    def get(self, request_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(str(request_id))

    def active(self) -> list[Session]:
        with self._lock:
            return [s for s in self._sessions.values() if s.active]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.active)

    def active_pids(self) -> list[int]:
        with self._lock:
            return [s.pid for s in self._sessions.values() if s.active and s.pid]

    def snapshot(self) -> list[dict[str, Any]]:
        """Serializable list of active sessions, newest first — for the GUI."""
        now = self._now()
        with self._lock:
            running = [s for s in self._sessions.values() if s.active]
        running.sort(key=lambda s: s.started_at, reverse=True)
        return [s.to_dict(now=now) for s in running]

    def prune(self, *, max_terminal: int = 200) -> None:
        """Drop old terminal sessions so the map can't grow unbounded."""
        with self._lock:
            terminal = [
                (s.finished_at or 0.0, rid)
                for rid, s in self._sessions.items()
                if not s.active
            ]
            if len(terminal) <= max_terminal:
                return
            terminal.sort()
            for _, rid in terminal[: len(terminal) - max_terminal]:
                self._sessions.pop(rid, None)

    @staticmethod
    def _signal_cancel(session: Session) -> None:
        setter = getattr(session.cancel, "set", None)
        if callable(setter):
            try:
                setter()
            except Exception:  # noqa: BLE001  # nosec B110 - a cancel signal must never raise
                pass


def sweep_orphans(
    pids: list[int],
    *,
    is_alive: Callable[[int], bool],
    kill: Callable[[int], None],
) -> list[int]:
    """Terminate leftover child PIDs from a previous crash (#169).

    Pure and injectable: given PIDs recorded before OPai exited, kill the ones
    still alive (a clean shutdown would have cleared them) and return the list of
    PIDs actually terminated. A kill that fails is skipped, never raised.
    """
    terminated: list[int] = []
    for pid in pids:
        try:
            if int(pid) <= 0 or not is_alive(int(pid)):
                continue
            kill(int(pid))
            terminated.append(int(pid))
        except Exception:  # noqa: BLE001  # nosec B112 - orphan cleanup is best-effort
            continue
    return terminated


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def durable_active_count(
    root: Path,
    *,
    is_pid_alive: Callable[[int], bool] = _pid_is_alive,
) -> int:
    """Read the canonical session registry across OPai processes."""

    path = Path(root)
    if (
        not path.is_dir()
        or path.is_symlink()
        or (hasattr(path, "is_junction") and path.is_junction())
    ):
        return 0
    active = 0
    for record in path.glob("*.json"):
        try:
            value = json.loads(record.read_text(encoding="utf-8"))
            pid = int(value.get("pid") or 0) if isinstance(value, dict) else 0
            if (
                not isinstance(value, dict)
                or value.get("schema_version") != 1
                or pid <= 0
                or not is_pid_alive(pid)
            ):
                record.unlink(missing_ok=True)
                continue
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        active += 1
    return active


# One process-wide registry the runner boundary and GUI read (#169).
def durable_session_root(home: Path | None = None) -> Path:
    override = os.environ.get("OPAI_ACTIVE_SESSION_ROOT")
    if override:
        return Path(override).expanduser().resolve(strict=False)
    return (
        (home or Path.home()).expanduser().resolve(strict=False)
        / ".opai"
        / "active-sessions"
    )


_SHARED = SessionRegistry(durable_root=durable_session_root())


def registry() -> SessionRegistry:
    """The shared active-session registry."""
    return _SHARED
