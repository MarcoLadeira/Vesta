"""Transactional objective planning, ownership, controls and exact cost evidence."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .journal_store import (
    SCHEMA_VERSION,
    StaleWriterError,
    _payload_hash,
    _transaction,
    minimise,
    open_store,
)

_TERMINAL = {"completed", "failed", "cancelled", "blocked", "needs-attention"}
_RESERVED_PATHS = {".git", ".opaihub", ".opcoding", "node_modules"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id(prefix):
    return f"{prefix}-{uuid4().hex}"


def _text(value, name, limit=8000, empty=False):
    if (
        not isinstance(value, str)
        or (not empty and not value.strip())
        or len(value) > limit
    ):
        raise ValueError(f"{name} must be bounded text")
    return value.strip()


def _money(value):
    if value is None:
        return None
    if isinstance(value, (bool, float)):
        raise ValueError("Costs must be exact decimal strings")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid decimal cost") from exc
    if (
        not amount.is_finite()
        or amount < 0
        or len(str(value)) > 100
        or abs(amount.as_tuple().exponent) > 100
        or amount.adjusted() > 30
    ):
        raise ValueError("Cost must be finite and nonnegative")
    return str(amount)


def _sum(values):
    with localcontext() as ctx:
        ctx.prec = 256
        return sum(
            (Decimal(value) for value in values if value is not None), Decimal(0)
        )


def _strings(value, name, limit=100):
    if not isinstance(value, (list, tuple)) or len(value) > limit:
        raise ValueError(f"{name} must be a bounded list")
    return [_text(item, name, 2000) for item in value]


def _paths(value, *, limit=100, protected=True):
    paths = _strings(value, "paths", limit)
    result = []
    for path in paths:
        path = path.replace("\\", "/")
        parts = PurePosixPath(path).parts
        if (
            path.startswith("/")
            or ":" in path
            or ".." in parts
            or (not parts and path != ".")
            or protected and any(
                part.casefold() in _RESERVED_PATHS
                or part.casefold().startswith(".opcoding")
                for part in parts
            )
        ):
            raise ValueError("Paths must stay within the repository source scope")
        result.append(str(PurePosixPath(path)))
    return result


def _overlap(left, right):
    if not left or not right:
        return True
    for a in left:
        for b in right:
            a, b = a.casefold().rstrip("/"), b.casefold().rstrip("/")
            if (
                any(ch in a + b for ch in "*?[")
                or a == "."
                or b == "."
                or a == b
                or a.startswith(b + "/")
                or b.startswith(a + "/")
            ):
                return True
    return False


def validate_plan(assignments):
    if not isinstance(assignments, (list, tuple)) or not 1 <= len(assignments) <= 32:
        raise ValueError("A plan requires between one and 32 assignments")
    result, names = [], set()
    for index, raw in enumerate(assignments):
        if not isinstance(raw, dict):
            raise ValueError("Each assignment must be an object")
        name = _text(
            raw.get("name", raw.get("assignment_id", f"assignment-{index + 1}")),
            "name",
            100,
        )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in names:
            raise ValueError("Assignment names must be unique identifiers")
        names.add(name)
        risk = raw.get("risk", "low")
        if risk not in {"low", "medium", "high"}:
            raise ValueError("Unknown assignment risk")
        if (
            "depends_on" in raw
            and "dependencies" in raw
            and raw["depends_on"] != raw["dependencies"]
        ):
            raise ValueError("Conflicting dependencies")
        item = dict(
            assignment_id=_id("assignment"),
            task_id=_id("task"),
            run_id=_id("run"),
            name=name,
            title=_text(raw.get("title", name), "title", 300),
            objective=_text(raw.get("objective", raw.get("title", "")), "objective"),
            role=_text(raw.get("role", "implementer"), "role", 100),
            intended_paths=_paths(raw.get("intended_paths", [])),
            depends_on=_strings(
                raw.get("depends_on", raw.get("dependencies", [])), "depends_on", 32
            ),
            verification_targets=_strings(
                raw.get("verification_targets", []), "verification_targets"
            ),
            capabilities=_strings(raw.get("capabilities", []), "capabilities", 32),
            route=_text(raw.get("route", "auto"), "route", 100),
            provider=_text(raw.get("provider", ""), "provider", 100, empty=True),
            model=_text(raw.get("model", "auto"), "model", 200),
            risk=risk,
            estimated_cost_usd=_money(raw.get("estimated_cost_usd")),
            budget_usd=_money(raw.get("budget_usd")),
            status="pending",
            owner="",
            last_owner="",
            fence=0,
            expires_at=None,
            worktree="",
            branch="",
            lease_id="",
            base_sha="",
            activity="",
            changed_files=[],
            cost_usd="0",
            cost_complete=True,
            verification={},
            result={},
            blocked_reason="",
            allowed_actions=[],
        )
        if (
            item["budget_usd"] is not None
            and item["estimated_cost_usd"] is not None
            and Decimal(item["estimated_cost_usd"]) > Decimal(item["budget_usd"])
        ):
            raise ValueError("Assignment estimate exceeds its budget")
        result.append(item)
    graph = {item["name"]: item["depends_on"] for item in result}
    visiting, visited = set(), set()

    def visit(name):
        if name not in graph:
            raise ValueError("Unknown dependency")
        if name in visiting:
            raise ValueError("Dependency cycle")
        if name in visited:
            return
        visiting.add(name)
        for dependency in graph[name]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in graph:
        visit(name)
    return result


class ObjectiveStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    @contextmanager
    def _db(self, write=False):
        db = open_store(self.root)
        try:
            if write:
                with _transaction(db):
                    yield db
            else:
                db.execute("BEGIN")
                try:
                    yield db
                finally:
                    db.execute("ROLLBACK")
        finally:
            db.close()

    def _event(self, db, obj, event, payload):
        now = _now()
        safe = minimise(dict(objective_id=obj["objective_id"], **payload))
        db.execute(
            "INSERT INTO events(run_id,event_type,event_schema_version,occurred_at,recorded_at,producer,payload,payload_hash,privacy_class) VALUES (?,?,1,?,?,?,?,?,?)",
            (
                obj["run_id"],
                "agents." + event,
                now,
                now,
                "agent_objectives",
                json.dumps(safe),
                _payload_hash(safe),
                "internal",
            ),
        )

    def _identity(self, db, task_id, run_id, objective, model="auto"):
        now = _now()
        task = db.execute(
            "SELECT task_id FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if task is None:
            db.execute(
                "INSERT INTO tasks(task_id,origin_surface,created_at,repository_ref,requested_outcome,schema_version,updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    task_id,
                    "agents",
                    now,
                    str(self.root),
                    objective,
                    SCHEMA_VERSION,
                    now,
                ),
            )
        existing = db.execute(
            "SELECT task_id FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if existing is not None:
            if existing["task_id"] != task_id:
                raise ValueError("Run belongs to a different task")
            return
        attempt = db.execute(
            "SELECT COALESCE(MAX(attempt),0)+1 FROM runs WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO runs(run_id,task_id,attempt,desired_state,observed_state,model,created_at,updated_at) VALUES (?,?,?,'running','pending',?,?,?)",
            (run_id, task_id, attempt, model, now, now),
        )

    def _load(self, db, oid):
        row = db.execute(
            "SELECT payload FROM agent_objectives WHERE objective_id=?", (oid,)
        ).fetchone()
        if row is None:
            raise KeyError(oid)
        return json.loads(row["payload"])

    def _assignments(self, db, oid):
        return [
            json.loads(row["payload"])
            for row in db.execute(
                "SELECT payload FROM objective_assignments WHERE objective_id=? ORDER BY position,assignment_id",
                (oid,),
            )
        ]

    def _save_obj(self, db, obj):
        obj["updated_at"] = _now()
        db.execute(
            "UPDATE agent_objectives SET status=?,payload=?,updated_at=? WHERE objective_id=?",
            (obj["status"], json.dumps(obj), obj["updated_at"], obj["objective_id"]),
        )
        db.execute(
            "UPDATE runs SET observed_state=?,updated_at=? WHERE run_id=?",
            (obj["status"], obj["updated_at"], obj["run_id"]),
        )
        self._terminal_run(db, obj["run_id"], obj["status"])

    def _terminal_run(self, db, run_id, status, reason=""):
        terminal = status in {"completed", "failed", "cancelled", "blocked"}
        db.execute(
            "UPDATE runs SET terminal_verdict=?,terminal_reason=? WHERE run_id=?",
            (status if terminal else None, reason if terminal else None, run_id),
        )

    def _save_assignment(self, db, item):
        db.execute(
            "UPDATE objective_assignments SET status=?,owner=?,fence=?,expires_at=?,payload=? WHERE assignment_id=?",
            (
                item["status"],
                item["owner"],
                item["fence"],
                item["expires_at"],
                json.dumps(item),
                item["assignment_id"],
            ),
        )
        db.execute(
            "UPDATE runs SET observed_state=?,lease_fence=?,updated_at=? WHERE run_id=?",
            (item["status"], item["fence"], _now(), item["run_id"]),
        )
        self._terminal_run(
            db,
            item["run_id"],
            item["status"] if not item["owner"] else "running",
            item.get("blocked_reason", ""),
        )
        if item["owner"]:
            db.execute(
                "INSERT INTO leases(run_id,owner,fence,acquired_at,heartbeat_at,expires_at) VALUES (?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET owner=excluded.owner,fence=excluded.fence,heartbeat_at=excluded.heartbeat_at,expires_at=excluded.expires_at,released_at=NULL",
                (
                    item["run_id"],
                    item["owner"],
                    item["fence"],
                    _now(),
                    _now(),
                    item["expires_at"],
                ),
            )
        else:
            db.execute(
                "UPDATE leases SET released_at=? WHERE run_id=? AND released_at IS NULL",
                (_now(), item["run_id"]),
            )

    def _insert_plan(self, db, obj, assignments):
        for position, item in enumerate(assignments):
            self._identity(
                db, item["task_id"], item["run_id"], item["objective"], item["model"]
            )
            db.execute(
                "INSERT INTO objective_assignments(assignment_id,objective_id,task_id,run_id,status,position,payload) VALUES (?,?,?,?,?,?,?)",
                (
                    item["assignment_id"],
                    obj["objective_id"],
                    item["task_id"],
                    item["run_id"],
                    item["status"],
                    position,
                    json.dumps(item),
                ),
            )

    def create(
        self,
        objective,
        assignments=(),
        *,
        task_id=None,
        run_id=None,
        max_parallel=2,
        project_limit=4,
        budget_usd=None,
        mode="safe-auto",
        model="auto",
        shared_context="",
        allow_cloud=False,
    ):
        objective = _text(objective, "objective", 16000)
        if (
            type(max_parallel) is not int
            or not 1 <= max_parallel <= 8
            or type(project_limit) is not int
            or not 1 <= project_limit <= 32
        ):
            raise ValueError("Invalid concurrency limit")
        if type(allow_cloud) is not bool:
            raise ValueError("allow_cloud must be boolean")
        validated = validate_plan(assignments) if assignments else []
        if assignments is None or not isinstance(assignments, (list, tuple)):
            raise ValueError("assignments must be a list")
        now = _now()
        obj = dict(
            objective_id=_id("objective"),
            task_id=task_id or _id("task"),
            run_id=run_id or _id("run"),
            objective=objective,
            status="ready" if validated else "planning",
            mode=_text(mode, "mode", 100),
            model=_text(model, "model", 200),
            allow_cloud=allow_cloud,
            shared_context=_text(shared_context, "shared_context", 16000, empty=True),
            budget_usd=_money(budget_usd),
            cost_usd="0",
            cost_complete=True,
            max_parallel=max_parallel,
            project_limit=project_limit,
            integration={
                "status": "pending",
                "owner": "",
                "fence": 0,
                "expires_at": None,
                "worktree": "",
                "branch": "",
                "verification": {},
                "result": {},
                "conflicts": [],
            },
            planning={
                "status": "pending",
                "owner": "",
                "fence": 0,
                "expires_at": None,
                "result": {},
            },
            allowed_actions=[],
            created_at=now,
            updated_at=now,
        )
        contract = {
            key: obj[key]
            for key in (
                "task_id",
                "run_id",
                "objective",
                "mode",
                "model",
                "allow_cloud",
                "shared_context",
                "budget_usd",
                "max_parallel",
                "project_limit",
            )
        }
        contract["assignments"] = [
            {
                key: val
                for key, val in item.items()
                if key not in {"assignment_id", "task_id", "run_id"}
            }
            for item in validated
        ]
        obj["request_digest"] = _payload_hash(contract)
        with self._db(True) as db:
            existing = db.execute(
                "SELECT payload FROM agent_objectives WHERE run_id=?", (obj["run_id"],)
            ).fetchone()
            if existing is not None:
                previous = json.loads(existing[0])
                if previous["request_digest"] != obj["request_digest"]:
                    raise ValueError(
                        "Objective request identity already has a different contract"
                    )
                return self.snapshot(previous["objective_id"])
            self._identity(db, obj["task_id"], obj["run_id"], objective, model)
            db.execute(
                "INSERT INTO agent_objectives(objective_id,task_id,run_id,status,payload,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    obj["objective_id"],
                    obj["task_id"],
                    obj["run_id"],
                    obj["status"],
                    json.dumps(obj),
                    now,
                    now,
                ),
            )
            self._insert_plan(db, obj, validated)
            self._event(db, obj, "created", {"assignment_count": len(validated)})
        return self.snapshot(obj["objective_id"])

    def set_plan(self, objective_id, assignments):
        validated = validate_plan(assignments)
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            if (
                obj["status"] != "planning"
                or obj["planning"]["owner"]
                or self._assignments(db, objective_id)
            ):
                raise ValueError("Only an unplanned objective accepts a plan")
            self._insert_plan(db, obj, validated)
            obj["status"] = "ready"
            self._save_obj(db, obj)
            self._event(db, obj, "planned", {"assignment_count": len(validated)})
        return self.snapshot(objective_id)

    def begin_plan(self, objective_id, owner, lease_seconds=120):
        owner = _text(owner, "owner", 200)
        if not isinstance(lease_seconds, (int, float)) or not 0 < lease_seconds <= 3600:
            raise ValueError("Invalid lease duration")
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            planning = obj["planning"]
            if (
                obj["status"] != "planning"
                or planning["owner"]
                or self._assignments(db, objective_id)
            ):
                return None
            planning.update(
                status="running",
                owner=owner,
                fence=planning["fence"] + 1,
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
                ).isoformat(),
            )
            self._save_obj(db, obj)
            self._event(
                db,
                obj,
                "planning-started",
                {"owner": owner, "fence": planning["fence"]},
            )
            return planning

    def finish_plan(
        self,
        objective_id,
        owner,
        fence,
        *,
        assignments=None,
        status="completed",
        result=None,
    ):
        if status not in {"completed", "failed", "cancelled", "needs-attention"}:
            raise ValueError("Invalid planning status")
        validated = validate_plan(assignments) if status == "completed" else []
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            planning = obj["planning"]
            if (
                not planning["owner"]
                or planning["owner"] != owner
                or planning["fence"] != fence
            ):
                raise StaleWriterError("Planning ownership or fence no longer matches")
            if obj["status"] == "stopping":
                status, validated = "cancelled", []
            if validated:
                self._insert_plan(db, obj, validated)
            planning.update(
                status=status,
                last_owner=owner,
                owner="",
                expires_at=None,
                result=result or {},
            )
            next_status = (
                "ready"
                if status == "completed"
                else ("cancelled" if status == "cancelled" else "needs-attention")
            )
            if obj["status"] == "paused":
                obj["paused_from"] = next_status
            else:
                obj["status"] = next_status
            self._save_obj(db, obj)
            self._event(
                db,
                obj,
                "planning-finished",
                {"status": status, "assignment_count": len(validated)},
            )
        return self.snapshot(objective_id)

    def heartbeat_phase(self, objective_id, phase, owner, fence, lease_seconds=120):
        if phase not in {"planning", "integration"}:
            raise ValueError("Unknown objective phase")
        if type(lease_seconds) not in (int, float) or not 0 < lease_seconds <= 3600:
            raise ValueError("Invalid lease duration")
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            owned = obj[phase]
            if (
                not owned["owner"]
                or owned["owner"] != owner
                or owned["fence"] != fence
                or owned["status"] not in {"running", "stopping"}
            ):
                raise StaleWriterError("Phase ownership or fence no longer matches")
            owned["expires_at"] = (
                datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
            ).isoformat()
            self._save_obj(db, obj)
            return owned

    def _costs(self, db, oid, aid=None):
        query = "SELECT amount_usd,measurement_kind FROM objective_cost_events WHERE objective_id=?"
        args = [oid]
        if aid is not None:
            query += " AND assignment_id=?"
            args.append(aid)
        rows = db.execute(query, args).fetchall()
        missing_query = "SELECT COUNT(*) FROM objective_assignments a WHERE a.objective_id=? AND a.fence>0 AND a.owner='' AND NOT EXISTS (SELECT 1 FROM objective_cost_events c WHERE c.assignment_id=a.assignment_id)"
        missing_args = [oid]
        if aid is not None:
            missing_query += " AND a.assignment_id=?"
            missing_args.append(aid)
        missing = db.execute(missing_query, missing_args).fetchone()[0]
        return str(_sum(row["amount_usd"] for row in rows)), not missing and all(
            row["amount_usd"] is not None
            and row["measurement_kind"] in {"actual", "derived"}
            for row in rows
        )

    def snapshot(self, objective_id):
        with self._db() as db:
            obj = self._load(db, objective_id)
            obj["assignments"] = self._assignments(db, objective_id)
            obj["revision"] = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events WHERE run_id=?", (obj["run_id"],)).fetchone()[0]
            obj["cost_usd"], obj["cost_complete"] = self._costs(db, objective_id)
            obj["cost_complete"] = (
                obj["cost_complete"]
                and not any(item["owner"] for item in obj["assignments"])
                and not obj["planning"]["owner"]
                and not obj["integration"]["owner"]
            )
            obj["allowed_actions"] = (
                []
                if obj["status"] in {"completed", "cancelled"}
                else ["stop", "budget", "sequential"]
            )
            if obj["status"] == "paused":
                obj["allowed_actions"].append("resume")
            elif obj["status"] in {"ready", "running", "planning"}:
                obj["allowed_actions"].append("pause")
            if (
                obj["status"] in {"ready-to-integrate", "needs-attention"}
                and obj["assignments"]
                and all(
                    item["status"] == "completed" and not item["owner"]
                    for item in obj["assignments"]
                )
                and not obj["integration"]["owner"]
            ):
                obj["allowed_actions"].append("reconcile")
            for item in obj["assignments"]:
                item["cost_usd"], item["cost_complete"] = self._costs(
                    db, objective_id, item["assignment_id"]
                )
                item["cost_complete"] = item["cost_complete"] and not bool(
                    item["owner"]
                )
                item["allowed_actions"] = (
                    ["stop"]
                    if item["owner"]
                    else (
                        ["stop", "prioritize", "reroute", "budget"]
                        if item["status"] == "pending"
                        else []
                    )
                )
            return obj

    def list_objectives(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Invalid objective limit")
        with self._db() as db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT objective_id FROM agent_objectives ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            ]
        return [self.snapshot(oid) for oid in ids]

    def claim_next(self, objective_id, owner, lease_seconds=120):
        owner = _text(owner, "owner", 200)
        if not isinstance(lease_seconds, (int, float)) or not 0 < lease_seconds <= 3600:
            raise ValueError("Invalid lease duration")
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            if obj["status"] not in {"ready", "running"}:
                return None
            items = self._assignments(db, objective_id)
            active = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM objective_assignments WHERE owner<>''"
                )
            ]
            objective_limits = [
                json.loads(row[0])["project_limit"]
                for row in db.execute(
                    "SELECT payload FROM agent_objectives WHERE status NOT IN ('completed','cancelled')"
                )
            ]
            if (
                len(active) >= min(objective_limits)
                or sum(bool(item["owner"]) for item in items) >= obj["max_parallel"]
            ):
                return None
            total, complete = self._costs(db, objective_id)
            states = {item["name"]: item["status"] for item in items}
            for item in items:
                if item["status"] != "pending":
                    continue
                if any(
                    states[dep] in _TERMINAL - {"completed"}
                    for dep in item["depends_on"]
                ):
                    item.update(
                        status="blocked", blocked_reason="Dependency did not complete"
                    )
                    self._save_assignment(db, item)
                    continue
                if any(states[dep] != "completed" for dep in item["depends_on"]):
                    continue
                if any(
                    _overlap(item["intended_paths"], other["intended_paths"])
                    for other in active
                ):
                    continue
                reserve = item["budget_usd"] or item["estimated_cost_usd"]
                reservations = [
                    other["budget_usd"] or other["estimated_cost_usd"]
                    for other in items
                    if other["owner"]
                ]
                if obj["budget_usd"] is not None and (
                    not complete
                    or reserve is None
                    or any(value is None for value in reservations)
                    or _sum([total, reserve, *reservations])
                    > Decimal(obj["budget_usd"])
                ):
                    item["blocked_reason"] = (
                        "Budget evidence or reservation unavailable"
                    )
                    self._save_assignment(db, item)
                    continue
                item.update(
                    status="running",
                    owner=owner,
                    fence=item["fence"] + 1,
                    expires_at=(
                        datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
                    ).isoformat(),
                    blocked_reason="",
                    activity="Starting isolated worker",
                )
                self._save_assignment(db, item)
                obj["status"] = "running"
                self._save_obj(db, obj)
                self._event(
                    db,
                    obj,
                    "claimed",
                    {
                        "assignment_id": item["assignment_id"],
                        "owner": owner,
                        "fence": item["fence"],
                    },
                )
                return item
            return None

    def _owned(self, db, oid, aid, owner, fence):
        row = db.execute(
            "SELECT payload FROM objective_assignments WHERE objective_id=? AND assignment_id=?",
            (oid, aid),
        ).fetchone()
        if row is None:
            raise KeyError(aid)
        item = json.loads(row[0])
        if not item["owner"] or item["owner"] != owner or item["fence"] != fence:
            raise StaleWriterError("Assignment ownership or fence no longer matches")
        return item

    def heartbeat(
        self,
        objective_id,
        assignment_id,
        owner,
        fence,
        *,
        lease_seconds=120,
        activity=None,
    ):
        if not isinstance(lease_seconds, (int, float)) or not 0 < lease_seconds <= 3600:
            raise ValueError("Invalid lease duration")
        with self._db(True) as db:
            item = self._owned(db, objective_id, assignment_id, owner, fence)
            if item["status"] not in {"running", "stopping"}:
                raise StaleWriterError("Worker requires recovery")
            item["expires_at"] = (
                datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
            ).isoformat()
            if activity is not None:
                item["activity"] = _text(activity, "activity", 2000, empty=True)
                self._event(
                    db, self._load(db, objective_id), "activity",
                    {"assignment_id": assignment_id, "activity": item["activity"]},
                )
            self._save_assignment(db, item)
            return item

    def attach_worktree(
        self,
        objective_id,
        assignment_id,
        owner,
        fence,
        *,
        worktree,
        branch,
        lease_id,
        base_sha,
    ):
        with self._db(True) as db:
            item = self._owned(db, objective_id, assignment_id, owner, fence)
            if item["status"] != "running":
                raise StaleWriterError("Worker no longer accepts work")
            item.update(
                worktree=_text(worktree, "worktree", 4000),
                branch=_text(branch, "branch", 300),
                lease_id=_text(lease_id, "lease_id", 200),
                base_sha=_text(base_sha, "base_sha", 100),
            )
            self._save_assignment(db, item)
            self._event(
                db,
                self._load(db, objective_id),
                "worktree-attached",
                {"assignment_id": assignment_id, "lease_id": lease_id},
            )
            return item

    def update_activity(self, objective_id, assignment_id, owner, fence, *, activity):
        return self.heartbeat(
            objective_id, assignment_id, owner, fence, activity=activity
        )

    def observe_route(self, objective_id, assignment_id, owner, fence, *, model=None, provider=None):
        with self._db(True) as db:
            item = self._owned(db, objective_id, assignment_id, owner, fence)
            if model:
                item["observed_model"] = _text(model, "model", 200)
            if provider:
                item["observed_provider"] = _text(provider, "provider", 200)
            self._save_assignment(db, item)
            db.execute("UPDATE runs SET model=?,provider=? WHERE run_id=?", (item.get("observed_model", item["model"]), item.get("observed_provider", item["provider"]), item["run_id"]))
            self._event(db, self._load(db, objective_id), "route-observed", {"assignment_id": assignment_id, "model": item.get("observed_model"), "provider": item.get("observed_provider")})

    def _refresh(self, db, obj):
        items = self._assignments(db, obj["objective_id"])
        states = {item["name"]: item["status"] for item in items}
        for item in items:
            if item["status"] == "pending" and any(
                states[dep] in _TERMINAL - {"completed"} for dep in item["depends_on"]
            ):
                item.update(
                    status="blocked", blocked_reason="Dependency did not complete"
                )
                self._save_assignment(db, item)
        phases_owned = bool(obj["planning"]["owner"] or obj["integration"]["owner"])
        if (
            obj["status"] == "stopping"
            and not any(item["owner"] for item in items)
            and not phases_owned
        ):
            obj["status"] = "cancelled"
        elif (
            not phases_owned
            and items
            and all(item["status"] in _TERMINAL and not item["owner"] for item in items)
        ):
            next_status = (
                "ready-to-integrate"
                if all(item["status"] == "completed" for item in items)
                else "needs-attention"
            )
            if obj["status"] == "paused":
                obj["paused_from"] = next_status
            else:
                obj["status"] = next_status
        self._save_obj(db, obj)

    def finish_assignment(
        self,
        objective_id,
        assignment_id,
        owner,
        fence,
        *,
        status,
        changed_files=(),
        verification=None,
        result=None,
    ):
        if status not in {"completed", "failed", "cancelled", "needs-attention"}:
            raise ValueError("Invalid assignment terminal status")
        changed_files = _paths(changed_files, limit=10000, protected=False)
        if (
            verification is not None
            and not isinstance(verification, dict)
            or result is not None
            and not isinstance(result, dict)
        ):
            raise ValueError("Worker evidence must be an object")
        with self._db(True) as db:
            item = self._owned(db, objective_id, assignment_id, owner, fence)
            if item["status"] == "stopping" and status == "completed":
                status = "cancelled"
            item.update(
                status=status,
                last_owner=owner,
                owner="",
                expires_at=None,
                changed_files=changed_files,
                verification=verification or {},
                result=result or {},
                activity="Worker terminated",
            )
            self._save_assignment(db, item)
            obj = self._load(db, objective_id)
            self._event(
                db,
                obj,
                "assignment-finished",
                {"assignment_id": assignment_id, "status": status, "fence": fence},
            )
            self._refresh(db, obj)
        return self.snapshot(objective_id)

    def record_cost(
        self,
        objective_id,
        assignment_id,
        operation_key,
        amount_usd,
        measurement_kind="actual",
    ):
        operation_key = _text(operation_key, "operation_key", 300)
        amount = _money(amount_usd)
        if measurement_kind not in {"actual", "derived", "estimated", "unavailable"}:
            raise ValueError("Unknown cost measurement kind")
        if amount is None:
            measurement_kind = "unavailable"
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            run_id = obj["run_id"]
            if assignment_id is not None:
                row = db.execute(
                    "SELECT run_id FROM objective_assignments WHERE objective_id=? AND assignment_id=?",
                    (objective_id, assignment_id),
                ).fetchone()
                if row is None:
                    raise KeyError(assignment_id)
                run_id = row[0]
            existing = db.execute(
                "SELECT objective_id,assignment_id,amount_usd,measurement_kind FROM objective_cost_events WHERE operation_key=?",
                (operation_key,),
            ).fetchone()
            evidence = (objective_id, assignment_id, amount, measurement_kind)
            if existing is not None:
                if tuple(existing) == evidence:
                    return self.snapshot(objective_id)
                if (
                    tuple(existing)[:2] == evidence[:2]
                    and existing["amount_usd"] is not None
                    and existing["measurement_kind"] in {"actual", "derived"}
                    and measurement_kind in {"unavailable", "estimated"}
                ):
                    return {
                        "cost_usd": self._costs(db, objective_id)[0],
                        "cost_complete": self._costs(db, objective_id)[1],
                    }
                if (
                    tuple(existing)[:2] == evidence[:2]
                    and existing["measurement_kind"] in {"unavailable", "estimated"}
                    and amount is not None
                    and measurement_kind in {"actual", "derived"}
                ):
                    db.execute(
                        "UPDATE objective_cost_events SET amount_usd=?,measurement_kind=?,recorded_at=? WHERE operation_key=?",
                        (amount, measurement_kind, _now(), operation_key),
                    )
                    self._event(
                        db,
                        obj,
                        "cost-reconciled",
                        {
                            "assignment_id": assignment_id,
                            "operation_key": operation_key,
                            "amount_usd": amount,
                            "measurement_kind": measurement_kind,
                        },
                    )
                    return {
                        "cost_usd": self._costs(db, objective_id)[0],
                        "cost_complete": self._costs(db, objective_id)[1],
                    }
                raise ValueError("Operation already has different cost evidence")
            now = _now()
            operation = db.execute(
                "SELECT run_id FROM operations WHERE operation_key=?", (operation_key,)
            ).fetchone()
            if operation is not None and operation[0] != run_id:
                raise ValueError("Cost operation belongs to another run")
            if operation is None:
                db.execute(
                    "INSERT INTO operations(operation_key,kind,target_digest,state,run_id,created_at,updated_at) VALUES (?,'agent-work',?,'observed',?,?,?)",
                    (operation_key, objective_id, run_id, now, now),
                )
            db.execute(
                "INSERT INTO objective_cost_events(operation_key,objective_id,assignment_id,amount_usd,measurement_kind,recorded_at) VALUES (?,?,?,?,?,?)",
                (operation_key, *evidence, now),
            )
            self._event(
                db,
                obj,
                "cost-recorded",
                {
                    "assignment_id": assignment_id,
                    "operation_key": operation_key,
                    "amount_usd": amount,
                    "measurement_kind": measurement_kind,
                },
            )
        return self.snapshot(objective_id)

    def control(self, objective_id, action, assignment_id=None, value=None):
        if action not in {
            "pause",
            "resume",
            "stop",
            "prioritize",
            "budget",
            "reroute",
            "sequential",
        }:
            raise ValueError("Unknown objective control")
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            items = self._assignments(db, objective_id)
            item = next(
                (item for item in items if item["assignment_id"] == assignment_id), None
            )
            if assignment_id is not None and item is None:
                raise KeyError(assignment_id)
            if obj["status"] in {"completed", "cancelled"}:
                raise ValueError("Objective is terminal")
            if action == "pause":
                if assignment_id or obj["status"] not in {
                    "ready",
                    "running",
                    "planning",
                }:
                    raise ValueError("Objective cannot be paused")
                obj["paused_from"] = obj["status"]
                obj["status"] = "paused"
            elif action == "resume":
                if obj["status"] != "paused":
                    raise ValueError("Only paused objectives can resume")
                obj["status"] = obj.pop("paused_from", "ready")
            elif action == "stop":
                for target in [item] if item else items:
                    if target["owner"]:
                        target["status"] = "stopping"
                    elif target["status"] == "pending":
                        target["status"] = "cancelled"
                    self._save_assignment(db, target)
                if item is None:
                    obj["status"] = (
                        "stopping"
                        if any(target["owner"] for target in items)
                        or obj["integration"]["owner"]
                        or obj["planning"]["owner"]
                        else "cancelled"
                    )
            elif action == "prioritize":
                if item is None or item["status"] != "pending":
                    raise ValueError("Only pending assignments can be prioritized")
                position = db.execute(
                    "SELECT COALESCE(MIN(position),0)-1 FROM objective_assignments WHERE objective_id=?",
                    (objective_id,),
                ).fetchone()[0]
                db.execute(
                    "UPDATE objective_assignments SET position=? WHERE assignment_id=?",
                    (position, assignment_id),
                )
            elif action == "budget":
                if item:
                    item["budget_usd"] = _money(value)
                    self._save_assignment(db, item)
                else:
                    obj["budget_usd"] = _money(value)
            elif action == "reroute":
                if (
                    item is None
                    or item["status"] != "pending"
                    or not isinstance(value, dict)
                    or not value
                    or set(value) - {"route", "provider", "model"}
                ):
                    raise ValueError("Only pending assignments can be rerouted")
                for key, route in value.items():
                    item[key] = _text(route, key, 200)
                item["model_authorized"] = True
                self._save_assignment(db, item)
            elif action == "sequential":
                obj["max_parallel"] = 1
            self._save_obj(db, obj)
            self._event(
                db,
                obj,
                "controlled",
                {"action": action, "assignment_id": assignment_id},
            )
        return self.snapshot(objective_id)

    def recover_expired(self, objective_id=None, *, now=None):
        now = now or _now()
        recovered = []
        with self._db(True) as db:
            rows = db.execute(
                "SELECT objective_id,payload FROM objective_assignments WHERE owner<>'' AND expires_at IS NOT NULL AND expires_at<?",
                (now,),
            ).fetchall()
            for row in rows:
                if objective_id is not None and row["objective_id"] != objective_id:
                    continue
                item = json.loads(row["payload"])
                item.update(
                    status="needs-attention",
                    fence=item["fence"] + 1,
                    expires_at=None,
                    blocked_reason="Worker ownership interrupted; confirm termination before releasing capacity",
                )
                self._save_assignment(db, item)
                obj = self._load(db, row["objective_id"])
                obj["status"] = "needs-attention"
                self._save_obj(db, obj)
                self._event(
                    db,
                    obj,
                    "ownership-interrupted",
                    {"assignment_id": item["assignment_id"], "fence": item["fence"]},
                )
                recovered.append(item["assignment_id"])
            for row in db.execute("SELECT payload FROM agent_objectives").fetchall():
                obj = json.loads(row[0])
                planning = obj["planning"]
                if (
                    (objective_id is None or obj["objective_id"] == objective_id)
                    and planning["owner"]
                    and planning["expires_at"]
                    and planning["expires_at"] < now
                ):
                    planning.update(
                        status="needs-attention",
                        fence=planning["fence"] + 1,
                        expires_at=None,
                    )
                    obj["status"] = "needs-attention"
                    self._save_obj(db, obj)
                    self._event(db, obj, "planning-interrupted", {})
                    recovered.append(obj["objective_id"])
                integration = obj["integration"]
                if (
                    (objective_id is None or obj["objective_id"] == objective_id)
                    and integration["owner"]
                    and integration["expires_at"]
                    and integration["expires_at"] < now
                ):
                    integration.update(
                        status="needs-attention",
                        fence=integration["fence"] + 1,
                        expires_at=None,
                    )
                    obj["status"] = "needs-attention"
                    self._save_obj(db, obj)
                    self._event(db, obj, "integration-interrupted", {})
                    recovered.append(obj["objective_id"])
        return recovered

    def begin_integration(self, objective_id, owner, lease_seconds=120):
        owner = _text(owner, "owner", 200)
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            items = self._assignments(db, objective_id)
            if (
                obj["status"] not in {"ready-to-integrate", "needs-attention"}
                or obj["integration"]["owner"]
                or not items
                or any(item["owner"] or item["status"] != "completed" for item in items)
            ):
                return None
            integration = obj["integration"]
            integration.update(
                status="running",
                owner=owner,
                fence=integration["fence"] + 1,
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
                ).isoformat(),
            )
            obj["status"] = "integrating"
            self._save_obj(db, obj)
            self._event(
                db,
                obj,
                "integration-started",
                {"owner": owner, "fence": integration["fence"]},
            )
            return integration

    def finish_integration(
        self,
        objective_id,
        owner,
        fence,
        *,
        status,
        worktree="",
        branch="",
        verification=None,
        result=None,
        conflicts=(),
    ):
        if status not in {"completed", "failed", "cancelled", "needs-attention"}:
            raise ValueError("Invalid integration status")

        with self._db(True) as db:
            obj = self._load(db, objective_id)
            integration = obj["integration"]
            if (
                not integration["owner"]
                or integration["owner"] != owner
                or integration["fence"] != fence
            ):
                raise StaleWriterError(
                    "Integration ownership or fence no longer matches"
                )
            if obj["status"] == "stopping":
                status = "cancelled"
            if status == "completed":
                self._validate_integration(
                    obj, worktree, verification, result, conflicts
                )
            integration.update(
                status=status,
                last_owner=owner,
                owner="",
                expires_at=None,
                worktree=worktree,
                branch=branch,
                verification=verification or {},
                result=result or {},
                conflicts=list(conflicts),
            )
            obj["status"] = (
                status if status in {"completed", "cancelled"} else "needs-attention"
            )
            self._save_obj(db, obj)
            self._event(db, obj, "integration-finished", {"status": status})
        return self.snapshot(objective_id)

    def _validate_integration(self, obj, worktree, verification, result, conflicts):
        from .repository_safety import capture_repository_handle
        from .verification_execution import (
            load_verification_manifest,
            verification_verdict,
        )

        if (
            not isinstance(verification, dict)
            or verification.get("passed") is not True
            or not isinstance(result, dict)
            or conflicts
        ):
            raise ValueError(
                "Completion requires integrated verification without conflicts"
            )
        reference = verification.get("manifest")
        if (
            not isinstance(reference, dict)
            or not reference.get("path")
            or not reference.get("digest")
            or not worktree
        ):
            raise ValueError("Completion requires a persisted verification manifest")
        target = Path(worktree).resolve()
        if target == self.root:
            raise ValueError("Integration must use an isolated worktree")
        manifest = load_verification_manifest(Path(reference["path"]))
        if (
            manifest.digest != reference["digest"]
            or verification_verdict(manifest).value != "verified"
        ):
            raise ValueError("Verification manifest is not verified")
        required = [
            check
            for check in manifest.policy.get("checks", [])
            if check.get("requirement") == "required"
        ]
        if not required or manifest.policy.get("human_reviews"):
            raise ValueError("Required integrated verification is missing")
        if (
            manifest.context.task_id != obj["task_id"]
            or manifest.context.run_id != obj["run_id"] + "-integration"
            or manifest.context.worktree != target
            or manifest.context.head_sha != result.get("head_sha")
        ):
            raise ValueError("Verification manifest does not match this integration")
        handle = capture_repository_handle(
            target, task_id=obj["task_id"], run_id=obj["run_id"] + "-integration"
        )
        if handle.identity.head_sha != manifest.context.head_sha:
            raise ValueError("Integration HEAD changed after verification")
