"""Local-only GUI startup instrumentation (#246).

A cockpit that takes seconds to become interactive undermines the "instant
local-first" positioning, and without instrumentation a regression lands
silently. This records the major init stages — process start to the chat view
being interactive — as a small trace.

It is **off by default** and **never leaves the machine**: enable it with the
``VESTA_STARTUP_TRACE`` environment flag, and the trace is appended to a JSONL
file under the workspace's local state directory. Disabled, every method is a
no-op and nothing is written — no timing cost, no file, consistent with the
privacy posture (no telemetry, ever).

The recorder is pure and injectable (clock + enabled flag are parameters), so
it is hermetically testable without a running GUI.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

TRACE_ENV = "VESTA_STARTUP_TRACE"
_TRUTHY = {"1", "true", "yes", "on"}


def trace_enabled(env: dict[str, str] | None = None) -> bool:
    """True only when the local ``VESTA_STARTUP_TRACE`` flag is explicitly set."""
    source = os.environ if env is None else env
    return str(source.get(TRACE_ENV, "")).strip().lower() in _TRUTHY


def trace_path(project_root: Path) -> Path:
    """Where the trace is appended — under the workspace's local state dir."""
    from vestahub.state import state_dir

    return state_dir(project_root) / "gui" / "startup-trace.jsonl"


class StartupTrace:
    """Records ``(stage, ms-since-start)`` marks for one GUI startup.

    When ``enabled`` is False the recorder is inert: ``mark`` does nothing,
    ``write`` writes nothing, and ``to_dict`` reports an empty trace — so
    instrumentation carries no cost and produces no file unless explicitly
    turned on.
    """

    def __init__(
        self, *, enabled: bool, clock: Callable[[], float] = time.perf_counter
    ) -> None:
        self._enabled = bool(enabled)
        self._clock = clock
        self._t0 = clock()
        self._marks: list[tuple[str, float]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def mark(self, stage: str) -> None:
        if not self._enabled:
            return
        self._marks.append((str(stage), round((self._clock() - self._t0) * 1000, 3)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "vesta_startup_trace",
            "enabled": self._enabled,
            "stages": [{"stage": stage, "ms": ms} for stage, ms in self._marks],
            "total_ms": self._marks[-1][1] if self._marks else 0.0,
        }

    def write(self, path: Path) -> Path | None:
        """Append the trace to ``path`` (JSONL). No-op when disabled or empty."""
        if not self._enabled or not self._marks:
            return None
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.to_dict(), sort_keys=True) + "\n")
        return target
