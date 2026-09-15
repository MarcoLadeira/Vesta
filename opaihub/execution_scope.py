"""Request-local financial attribution for isolated objective workers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
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


def assignment_cost_events(root: Path, run_id: str, *, events=None) -> list[dict]:
    from .ledger import read_events

    calls: dict[str, dict] = {}
    for event in read_events(root) if events is None else events:
        if event.get("assignment_run_id") != run_id:
            continue
        kind = event.get("event_type")
        key = event.get("call_id") or event.get("operation_key")
        if not key or kind not in {
            "model_call_started",
            "model_call",
            "model_call_finalized",
            "model_call_not_dispatched",
        }:
            continue
        amount = (
            event.get("cost_usd")
            if kind in {"model_call", "model_call_finalized"}
            else None
        )
        provenance = str(event.get("cost_usd_provenance") or "unavailable")
        if kind == "model_call_not_dispatched":
            amount, provenance = 0, "actual"
        if provenance not in {"actual", "derived", "estimated"}:
            amount, provenance = None, "unavailable"
        calls[str(key)] = {
            "operation_key": str(key),
            "amount_usd": str(amount) if amount is not None else None,
            "measurement_kind": provenance if amount is not None else "unavailable",
        }
    return list(calls.values())


def managed_budget_gate(project_root: Path, *, next_cost_usd=None) -> dict:
    """Check exact objective caps; opaque provider calls have no bounded quote."""
    from .agent_objectives import ObjectiveStore, _money, _sum

    scope = _SCOPE.get()
    reasons = []

    def verdict():
        return {"allowed": not reasons, "denied": bool(reasons), "reasons": reasons}

    if scope is None:
        return verdict()
    store = ObjectiveStore(scope.authority_root)
    with store._db() as db:
        row = db.execute(
            "SELECT objective_id FROM objective_assignments WHERE run_id=?",
            (scope.run_id,),
        ).fetchone()
        if row is None:
            row = db.execute(
                "SELECT objective_id FROM agent_objectives WHERE run_id=?",
                (scope.run_id.removesuffix("-plan"),),
            ).fetchone()
        if row is None:
            reasons.append("Managed execution authority is unavailable")
            return verdict()
        obj = store._load(db, row[0])
        assignments = store._assignments(db, row[0])
        stored = [
            dict(event)
            for event in db.execute(
                "SELECT operation_key,assignment_id,amount_usd,measurement_kind FROM objective_cost_events WHERE objective_id=?",
                (row[0],),
            )
        ]
    current = next(
        (item for item in assignments if item["run_id"] == scope.run_id), None
    )
    cap = current.get("budget_usd") if current else None
    if (
        obj["status"] in {"stopping", "cancelled"}
        or current
        and current["status"] == "stopping"
    ):
        reasons.append("Objective cancellation requested")
        return verdict()
    if cap is None and obj["budget_usd"] is None:
        return verdict()
    if next_cost_usd is None:
        reasons.append(
            "Provider cannot enforce the configured dollar cap for this call"
        )
        return verdict()
    quote = Decimal(_money(str(next_cost_usd)))
    events = {event["operation_key"]: event for event in stored}
    runs = [(item["run_id"], item["assignment_id"]) for item in assignments]
    runs.append((obj["run_id"] + "-plan", None))
    for run_id, aid in runs:
        for event in assignment_cost_events(scope.authority_root, run_id):
            events[event["operation_key"]] = {**event, "assignment_id": aid}

    def spend(aid):
        rows = [event for event in events.values() if event["assignment_id"] == aid]
        complete = all(
            event["amount_usd"] is not None
            and event["measurement_kind"] in {"actual", "derived"}
            for event in rows
        )
        return _sum(event["amount_usd"] for event in rows), complete

    aid = current["assignment_id"] if current else None
    own, own_complete = spend(aid)
    own_next = _sum([own, quote])
    if cap is not None and (not own_complete or own_next > Decimal(cap)):
        reasons.append(
            "Assignment budget would be exceeded or prior cost is unavailable"
        )
    if obj["budget_usd"] is not None:
        total, complete = own_next, own_complete
        for other in assignments:
            if other["assignment_id"] == aid:
                continue
            used, known = spend(other["assignment_id"])
            if other["owner"]:
                reservation = other["budget_usd"] or other["estimated_cost_usd"]
                if reservation is None:
                    complete = False
                else:
                    total = _sum([total, max(used, Decimal(reservation))])
            else:
                total = _sum([total, used])
                complete = complete and known
        if aid is not None:
            planner_cost, known = spend(None)
            total = _sum([total, planner_cost])
            complete = complete and known
        if not complete or total > Decimal(obj["budget_usd"]):
            reasons.append(
                "Objective budget would be exceeded or prior cost is unavailable"
            )
    return verdict()
