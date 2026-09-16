"""Batch buffer for the bridge's ``activityBatch`` signal (#226).

Qt-free on purpose so the buffering contract is unit-tested hermetically;
``vesta.gui_web`` wires it to a ~33ms QTimer on the GUI thread. Worker threads
append events; each timer tick drains the buffer into ONE wire payload, so a
burst of activity costs one cross-thread signal instead of one per event.
A force-flush at request end guarantees no event is lost or delayed at the
tail (honesty invariant: everything emitted is delivered, in order).
"""

from __future__ import annotations

import json
import threading
from typing import Any

FLUSH_INTERVAL_MS = 33


class ActivityBatcher:
    """Thread-safe per-request event buffer flushed as one JSON payload."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)

    def pending(self) -> int:
        with self._lock:
            return len(self._events)

    def flush(self) -> str | None:
        """Drain the buffer; returns the wire payload, or None when empty.

        Wire shape (mirrored by the e2e mock bridge):
        ``{"requestId": str, "events": [event, ...]}`` — order preserved.
        """
        with self._lock:
            if not self._events:
                return None
            events, self._events = self._events, []
        return json.dumps({"requestId": self.request_id, "events": events})
