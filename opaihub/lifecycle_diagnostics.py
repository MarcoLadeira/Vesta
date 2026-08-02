"""Durable, privacy-safe evidence for rejected lifecycle transitions."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import run_journal
from .state import state_dir

_EVENT_TYPE = "illegal_lifecycle_transition"
_MAX_RECENT = 64
_SOURCE_LABELS = frozenset(
    {
        "background_runs",
        "pipeline",
        "test",
        "workflow_runner",
        "workflow_runner.step",
    }
)


def _path(project_root: Path) -> Path:
    return state_dir(project_root.expanduser().resolve()) / "agent" / "lifecycle-diagnostics.jsonl"


def _empty() -> dict[str, Any]:
    return {"count": 0, "recent": []}


def _reduce(projection: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": int(projection.get("count", 0)) + 1,
        "recent": [*projection.get("recent", []), dict(event)][-_MAX_RECENT:],
    }


def _valid(event: dict[str, Any]) -> bool:
    return (
        event.get("event_type") == _EVENT_TYPE
        and isinstance(event.get("from"), str)
        and bool(event["from"])
        and isinstance(event.get("to"), str)
        and bool(event["to"])
        and isinstance(event.get("source"), str)
        and bool(event["source"])
        and isinstance(event.get("created_at"), str)
        and bool(event["created_at"])
    )


def source_label(source: object) -> str:
    """Project caller input onto the closed, non-sensitive source vocabulary."""

    candidate = str(source or "").strip().lower()
    return candidate if candidate in _SOURCE_LABELS else "unknown"


def record_illegal_transition(
    project_root: Path,
    from_state: str,
    to_state: str,
    source: object = "",
) -> dict[str, Any]:
    """Append one redacted refusal to the local crash-safe journal (#517)."""

    event = {
        "event_type": _EVENT_TYPE,
        "from": str(from_state),
        "to": str(to_state),
        "source": source_label(source),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    record, _projection = run_journal.append(
        _path(Path(project_root)),
        event,
        reduce=_reduce,
        empty=_empty,
        validate=_valid,
    )
    return record


def read_diagnostics(project_root: Path) -> list[dict[str, Any]]:
    """Return the bounded durable tail of rejected lifecycle transitions."""

    recovery = run_journal.load(
        _path(Path(project_root)),
        reduce=_reduce,
        empty=_empty,
        validate=_valid,
    )
    return [dict(event) for event in recovery.projection["recent"]]
