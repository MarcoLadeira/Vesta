"""Request-local financial attribution for isolated objective workers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class AssignmentScope:
    execution_root: Path
    authority_root: Path
    task_id: str
    run_id: str


_SCOPE: ContextVar[AssignmentScope | None] = ContextVar(
    "assignment_scope", default=None
)


@contextmanager
def assignment_scope(
    execution_root: Path, authority_root: Path, *, task_id: str, run_id: str
) -> Iterator[AssignmentScope]:
    if not task_id or not run_id:
        raise ValueError(
            "Managed execution requires canonical task and run identifiers"
        )
    scope = AssignmentScope(
        Path(execution_root).expanduser().resolve(),
        Path(authority_root).expanduser().resolve(),
        task_id,
        run_id,
    )
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def financial_root(project_root: Path) -> Path:
    root = Path(project_root).expanduser().resolve()
    scope = _SCOPE.get()
    return scope.authority_root if scope and root == scope.execution_root else root


def attribution_fields() -> dict[str, str]:
    scope = _SCOPE.get()
    return (
        {"assignment_task_id": scope.task_id, "assignment_run_id": scope.run_id}
        if scope
        else {}
    )


def assignment_cost_events(root: Path, run_id: str) -> list[dict]:
    from .ledger import read_events

    calls: dict[str, dict] = {}
    for event in read_events(root):
        if event.get("assignment_run_id") != run_id:
            continue
        kind = event.get("event_type")
        key = event.get("call_id")
        if not key or kind not in {
            "model_call_started",
            "model_call",
            "model_call_not_dispatched",
        }:
            continue
        amount = event.get("cost_usd") if kind == "model_call" else None
        provenance = str(event.get("cost_usd_provenance") or "unavailable")
        if kind == "model_call_not_dispatched":
            amount, provenance = 0, "actual"
        calls[str(key)] = {
            "operation_key": str(key),
            "amount_usd": str(amount) if amount is not None else None,
            "measurement_kind": provenance if amount is not None else "unavailable",
        }
    return list(calls.values())
