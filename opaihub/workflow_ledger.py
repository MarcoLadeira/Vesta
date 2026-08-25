"""Privacy-safe local event ledger for coding-agent workflow truth."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_io import read_utf8_tail_lines
from .command_runner import redact
from .ledger import task_fingerprint
from .state import state_dir

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def redact_structure(value: Any) -> Any:
    """Recursively redact strings before workflow data reaches disk or UI."""

    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {redact(str(key)): redact_structure(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact_structure(item) for item in value]
    return value


class WorkflowLedger:
    """Append-only workflow evidence; raw task text is deliberately excluded."""

    def __init__(self, project_root: Path, *, task_id: str) -> None:
        self.project_root = project_root.expanduser().resolve()
        self.task_id = str(task_id)
        self.path = state_dir(self.project_root) / "agent" / "events.jsonl"

    def append(
        self,
        event_type: str,
        *,
        task: str = "",
        metadata: dict[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        event = {
            "created_at": _now(),
            "event_type": str(event_type),
            "task_id": self.task_id,
            "task_hash": task_fingerprint(task),
            "metadata": redact_structure(metadata or {}),
            **{
                redact(str(key)): redact_structure(value)
                for key, value in fields.items()
            },
        }
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def read(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        def matching(lines: list[str]) -> list[dict[str, Any]]:
            events = []
            for line in lines:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and value.get("task_id") == self.task_id:
                    events.append(value)
            return events

        if limit is None:
            return matching(
                self.path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
        target = max(0, int(limit))
        if target == 0:
            return []
        window = max(64, target * 2)
        previous_line_count = -1
        while True:
            lines = read_utf8_tail_lines(self.path, window)
            events = matching(lines)
            if len(events) >= target:
                return events[-target:]
            line_count = len(lines)
            if line_count < window or line_count == previous_line_count:
                return events
            previous_line_count = line_count
            window *= 2
