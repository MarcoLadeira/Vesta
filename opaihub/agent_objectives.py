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
            or protected
            and any(
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
        if type(raw.get("parallel_eligible", True)) is not bool:
            raise ValueError("Parallel eligibility must be a boolean")
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
            display_name=(
                "Alex",
                "Sam",
                "Taylor",
                "Riley",
                "Jordan",
                "Morgan",
                "Casey",
                "Robin",
            )[index % 8]
            + (f" {index // 8 + 1}" if index >= 8 else ""),
            avatar_index=index,
            title=_text(raw.get("title", name), "title", 300),
            objective=_text(raw.get("objective", raw.get("title", "")), "objective"),
            role=_text(raw.get("role", "implementer"), "role", 100),
            rationale=_text(raw.get("rationale", ""), "rationale", 2000, empty=True),
            parallel_eligible=raw.get("parallel_eligible", True),
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
        bypass_permissions=False,
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
        if type(bypass_permissions) is not bool:
            raise ValueError("bypass_permissions must be boolean")
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
            bypass_permissions=bypass_permissions,
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
        if bypass_permissions:
            contract["bypass_permissions"] = True
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
            self._require_terminated(planning)
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
            admission = self._queue_projection(db, obj, obj["assignments"])
            obj["revision"] = db.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM events WHERE run_id=?",
                (obj["run_id"],),
            ).fetchone()[0]
            obj["timeline"], obj["timeline_truncated"] = self._timeline(db, obj)
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
                if not obj["planning"]["owner"] and not any(
                    item["owner"] for item in obj["assignments"]
                ):
                    obj["allowed_actions"].append("run")
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
                if (
                    item.get("pending_approval")
                    and not item["owner"]
                    and item["status"] == "needs-attention"
                    and obj["status"] not in {"completed", "cancelled", "stopping"}
                ):
                    item["allowed_actions"].append("approve")
                if obj["status"] not in {
                    "completed",
                    "cancelled",
                    "stopping",
                } and self._retryable(db, item):
                    item["allowed_actions"].extend(["retry", "reroute", "budget"])
            if (
                0 < len(obj["assignments"]) < 32
                and all(
                    a["status"] == "completed" and not a["owner"]
                    for a in obj["assignments"]
                )
                and not obj["planning"]["owner"]
                and not obj["integration"]["owner"]
                and obj["status"]
                in {"completed", "ready-to-integrate", "needs-attention"}
            ):
                obj["allowed_actions"].append("request_review")
            from .receipt import _content_hash, build_objective_receipts

            costs = [
                dict(row)
                for row in db.execute(
                    "SELECT operation_key,assignment_id,amount_usd,measurement_kind FROM objective_cost_events WHERE objective_id=? ORDER BY operation_key",
                    (objective_id,),
                )
            ]
            obj["cost_evidence_hash"] = _content_hash({"cost_events": costs})
            for item in obj["assignments"]:
                if item["assignment_id"] in admission:
                    item["admission"] = admission[item["assignment_id"]]
                item["cost_evidence_hash"] = _content_hash(
                    {
                        "cost_events": [
                            row
                            for row in costs
                            if row["assignment_id"] == item["assignment_id"]
                        ],
                    }
                )
            obj["receipt"], receipts = build_objective_receipts(obj)
            for item in obj["assignments"]:
                item["receipt"] = receipts[item["assignment_id"]]
            return obj

    def _timeline(self, db, obj):
        kinds = ("agents.claimed", "agents.activity", "agents.assignment-finished")
        recorded = db.execute(
            "SELECT sequence,occurred_at,event_type,payload FROM events "
            "WHERE run_id=? AND event_type IN (?,?,?) "
            "AND CASE WHEN json_valid(json_extract(payload,'$.activity')) "
            "THEN COALESCE(json_extract(json_extract(payload,'$.activity'),'$.channel'),'feed') "
            "ELSE 'feed' END <> 'status' ORDER BY sequence DESC LIMIT 81",
            (obj["run_id"], *kinds),
        ).fetchall()
        entries = []
        known = {a["assignment_id"] for a in obj["assignments"]}
        for row in reversed(recorded[:80]):
            payload = json.loads(row["payload"])
            if payload.get("assignment_id") not in known:
                continue
            entry = {
                "sequence": row["sequence"],
                "occurred_at": row["occurred_at"],
                "kind": row["event_type"].removeprefix("agents."),
                "assignment_id": payload["assignment_id"],
            }
            for field in ("activity", "status", "summary", "verification_summary"):
                if isinstance(payload.get(field), str):
                    entry[field] = payload[field][:2000]
            entries.append(entry)
        return entries, len(recorded) > 80

    def _queue_projection(self, db, obj, items):
        active = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM objective_assignments WHERE owner<>''"
            )
        ]
        limits = [
            json.loads(row[0])["project_limit"]
            for row in db.execute(
                "SELECT payload FROM agent_objectives WHERE status NOT IN ('completed','cancelled')"
            )
        ]
        states = {item["name"]: item["status"] for item in items}
        own_active = [item for item in items if item["owner"]]
        result = {}
        for item in items:
            if item["status"] != "pending":
                continue
            waiting, blockers, reason = False, [], "Ready for an execution slot"
            dependencies = [
                name for name in item["depends_on"] if states[name] != "completed"
            ]
            conflicts = [
                row
                for row in active
                if _overlap(item["intended_paths"], row["intended_paths"])
                or not item.get("parallel_eligible", True)
                or not row.get("parallel_eligible", True)
            ]
            if obj["status"] not in {"ready", "running"}:
                reason = "Objective " + obj["status"].replace("-", " ")
            elif dependencies:
                reason = "Waiting for dependencies: " + ", ".join(dependencies)
                blockers = [
                    row for row in items if row["name"] in dependencies and row["owner"]
                ]
            elif conflicts:
                reason = "Waiting for overlapping or sequential work"
                blockers = conflicts
            elif limits and len(active) >= min(limits):
                reason, blockers = "Project concurrency limit reached", active
            elif len(own_active) >= obj["max_parallel"]:
                reason, blockers = "Objective concurrency limit reached", own_active
            elif item.get("blocked_reason"):
                reason = item["blocked_reason"]
            waiting = bool(blockers) and all(
                row["status"] in {"running", "stopping"} for row in blockers
            )
            result[item["assignment_id"]] = {
                "reason": reason,
                "waiting_for_owners": waiting,
                "blocking_assignment_ids": [row["assignment_id"] for row in blockers],
            }
        return result

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
                    or not item.get("parallel_eligible", True)
                    or not other.get("parallel_eligible", True)
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
                    db,
                    self._load(db, objective_id),
                    "activity",
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

    def observe_route(
        self, objective_id, assignment_id, owner, fence, *, model=None, provider=None
    ):
        with self._db(True) as db:
            item = self._owned(db, objective_id, assignment_id, owner, fence)
            if model:
                item["observed_model"] = _text(model, "model", 200)
            if provider:
                item["observed_provider"] = _text(provider, "provider", 200)
            self._save_assignment(db, item)
            db.execute(
                "UPDATE runs SET model=?,provider=? WHERE run_id=?",
                (
                    item.get("observed_model", item["model"]),
                    item.get("observed_provider", item["provider"]),
                    item["run_id"],
                ),
            )
            self._event(
                db,
                self._load(db, objective_id),
                "route-observed",
                {
                    "assignment_id": assignment_id,
                    "model": item.get("observed_model"),
                    "provider": item.get("observed_provider"),
                },
            )

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
                and obj["integration"]["status"]
                not in {"needs-attention", "failed", "cancelled"}
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
            self._require_terminated(item)
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
            item.pop("pending_approval", None)
            item.pop("approval_grant", None)
            pending = self._pending_approval(result or {})
            if pending and status != "cancelled":
                item["status"] = status = "needs-attention"
                item["pending_approval"] = {**pending, "request_id": _id("approval")}
                item["blocked_reason"] = "Waiting for approval of this operation"
            self._save_assignment(db, item)
            obj = self._load(db, objective_id)
            self._event(
                db,
                obj,
                "assignment-finished",
                {
                    "assignment_id": assignment_id,
                    "status": status,
                    "fence": fence,
                    "summary": str(
                        ((result or {}).get("handoff") or {}).get("summary", "")
                    )[:2000]
                    if isinstance((result or {}).get("handoff"), dict)
                    else "",
                    "verification_summary": str(
                        (verification or {}).get("summary", "")
                    )[:2000],
                },
            )
            self._refresh(db, obj)
        return self.snapshot(objective_id)

    @staticmethod
    def _pending_approval(result):
        if result.get("scope_violations"):
            return None
        status = result.get("status")
        if status == "needs_command_approval":
            raw = result.get("command_approval")
            if isinstance(raw, dict) and isinstance(raw.get("command"), str):
                command = raw["command"].strip()
                if command and len(command) <= 8000:
                    return {
                        "kind": "command",
                        "command": command,
                        "reason": str(raw.get("reason", ""))[:2000],
                    }
        if status == "needs_edit_approval":
            raw = result.get("edit_approval")
            if isinstance(raw, dict):
                try:
                    files = _paths(raw.get("files", []))
                except ValueError:
                    return None
                if files:
                    return {
                        "kind": "edits",
                        "files": files,
                        "reason": "Enable file editing in this assignment's isolated worktree for one continuation. Changes outside the intended scope require review.",
                    }
        return None

    def approve(self, objective_id, assignment_id, value):
        if not isinstance(value, dict) or set(value) != {"request_id"}:
            raise ValueError("Approval requires the current request ID")
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            items = self._assignments(db, objective_id)
            item = next((a for a in items if a["assignment_id"] == assignment_id), None)
            pending = (item or {}).get("pending_approval")
            if (
                not pending
                or pending["request_id"] != value["request_id"]
                or item["owner"]
                or item["status"] != "needs-attention"
                or obj["status"] in {"completed", "cancelled", "stopping"}
                or obj["planning"]["owner"]
                or obj["integration"]["owner"]
            ):
                raise ValueError("Approval is stale or the operation is still active")
            if len(item.get("attempts", [])) >= 16:
                raise ValueError("Assignment continuation limit reached")
            previous = {
                k: v
                for k, v in item.items()
                if k not in {"attempts", "resume_from", "approval_grant"}
            }
            item.setdefault("attempts", []).append(previous)
            item["resume_from"] = previous
            item["run_id"] = _id("run")
            self._identity(
                db, item["task_id"], item["run_id"], item["objective"], item["model"]
            )
            db.execute(
                "UPDATE objective_assignments SET run_id=? WHERE assignment_id=?",
                (item["run_id"], assignment_id),
            )
            item["approval_grant"] = {**pending, "run_id": item["run_id"]}
            for key in (
                "pending_approval",
                "execution",
                "observed_model",
                "observed_provider",
            ):
                item.pop(key, None)
            item.update(
                status="pending",
                owner="",
                expires_at=None,
                worktree="",
                branch="",
                lease_id="",
                base_sha="",
                result={},
                changed_files=[],
                verification={},
                activity="Approval recorded; continuation queued",
                blocked_reason="",
            )
            self._save_assignment(db, item)
            for target in items:
                if (
                    target["status"] == "blocked"
                    and target.get("blocked_reason") == "Dependency did not complete"
                ):
                    target.update(status="pending", blocked_reason="")
                    self._save_assignment(db, target)
            if obj["status"] == "paused":
                obj["paused_from"] = "ready"
            else:
                obj["status"] = "ready"
            self._save_obj(db, obj)
            self._event(
                db,
                obj,
                "operation-approved",
                {
                    "assignment_id": assignment_id,
                    "request_id": pending["request_id"],
                    "run_id": item["run_id"],
                },
            )
            self._refresh(db, obj)
        return self.snapshot(objective_id)

    def _retryable(self, db, item):
        if (
            not item
            or item["owner"]
            or item["status"] != "needs-attention"
            or item.get("pending_approval")
            or item.get("changed_files")
            or item.get("result", {}).get("scope_violations")
            or item.get("result", {}).get("status") != "blocked"
            or item.get("result", {}).get("dispatch_state") != "not-dispatched"
            or item.get("execution")
            and not self._proven_terminated(item["execution"])
        ):
            return False
        costs = db.execute(
            "SELECT c.amount_usd,c.measurement_kind FROM objective_cost_events c JOIN operations o ON o.operation_key=c.operation_key WHERE o.run_id=? AND c.assignment_id=?",
            (item["run_id"], item["assignment_id"]),
        ).fetchall()
        return bool(costs) and all(
            row["amount_usd"] is not None
            and Decimal(row["amount_usd"]) == 0
            and row["measurement_kind"] in {"actual", "derived"}
            for row in costs
        )

    def retry(self, objective_id, assignment_id, value):
        if not isinstance(value, dict) or set(value) != {"run_id"}:
            raise ValueError("Retry requires the current run ID")
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            items = self._assignments(db, objective_id)
            item = next((a for a in items if a["assignment_id"] == assignment_id), None)
            if (
                not self._retryable(db, item)
                or item["run_id"] != value["run_id"]
                or obj["status"] in {"completed", "cancelled", "stopping"}
                or obj["planning"]["owner"]
                or obj["integration"]["owner"]
            ):
                raise ValueError(
                    "Retry is stale or pre-dispatch termination is not proven"
                )
            if len(item.get("attempts", [])) >= 16:
                raise ValueError("Assignment continuation limit reached")
            previous = {
                k: v
                for k, v in item.items()
                if k not in {"attempts", "resume_from", "approval_grant"}
            }
            item.setdefault("attempts", []).append(previous)
            item["run_id"] = _id("run")
            self._identity(
                db, item["task_id"], item["run_id"], item["objective"], item["model"]
            )
            db.execute(
                "UPDATE objective_assignments SET run_id=? WHERE assignment_id=?",
                (item["run_id"], assignment_id),
            )
            for key in (
                "resume_from",
                "approval_grant",
                "execution",
                "observed_model",
                "observed_provider",
            ):
                item.pop(key, None)
            item.update(
                status="pending",
                owner="",
                expires_at=None,
                worktree="",
                branch="",
                lease_id="",
                base_sha="",
                result={},
                changed_files=[],
                verification={},
                activity="Pre-dispatch retry queued",
                blocked_reason="",
            )
            self._save_assignment(db, item)
            for target in items:
                if (
                    target["status"] == "blocked"
                    and target.get("blocked_reason") == "Dependency did not complete"
                ):
                    target.update(status="pending", blocked_reason="")
                    self._save_assignment(db, target)
            if obj["status"] == "paused":
                obj["paused_from"] = "ready"
            else:
                obj["status"] = "ready"
            self._save_obj(db, obj)
            self._event(
                db,
                obj,
                "pre-dispatch-retry",
                {
                    "assignment_id": assignment_id,
                    "previous_run_id": previous["run_id"],
                    "run_id": item["run_id"],
                },
            )
            self._refresh(db, obj)
        return self.snapshot(objective_id)

    def request_review(self, objective_id, value):
        if (
            not isinstance(value, dict)
            or set(value) != {"revision"}
            or type(value["revision"]) is not int
        ):
            raise ValueError("Review requires the current objective revision")
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            items = self._assignments(db, objective_id)
            revision = db.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM events WHERE run_id=?",
                (obj["run_id"],),
            ).fetchone()[0]
            if (
                revision != value["revision"]
                or not items
                or len(items) >= 32
                or any(a["owner"] or a["status"] != "completed" for a in items)
                or obj["planning"]["owner"]
                or obj["integration"]["owner"]
                or obj["status"]
                not in {"completed", "ready-to-integrate", "needs-attention"}
            ):
                raise ValueError("Review is stale or assignments are not ready")
            raw = [
                {k: a[k] for k in ("name", "objective", "depends_on")} for a in items
            ]
            name = "additional-review-" + uuid4().hex[:12]
            raw.append(
                {
                    "name": name,
                    "title": "Additional review",
                    "role": "reviewer",
                    "objective": "Review the combined assignment changes for correctness, regressions, and missing requirements. Report findings with evidence. Do not modify files.",
                    "depends_on": [a["name"] for a in items],
                    "intended_paths": ["."],
                    "model": obj["model"],
                    "parallel_eligible": False,
                }
            )
            review = validate_plan(raw)[-1]
            self._insert_plan(db, obj, [review])
            db.execute(
                "UPDATE objective_assignments SET position=? WHERE assignment_id=?",
                (len(items), review["assignment_id"]),
            )
            obj.setdefault("integration_history", []).append(obj["integration"])
            obj["integration"] = {
                "status": "pending",
                "owner": "",
                "fence": obj["integration"]["fence"],
                "expires_at": None,
                "worktree": "",
                "branch": "",
                "verification": {},
                "result": {},
                "conflicts": [],
            }
            obj["status"] = "ready"
            self._save_obj(db, obj)
            self._event(
                db,
                obj,
                "additional-review-requested",
                {"assignment_id": review["assignment_id"]},
            )
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
        if action == "rename":
            display_name = _text(value, "Agent name", 40)
            if any(ord(char) < 32 for char in display_name):
                raise ValueError("Agent name must be a single line")
            with self._db(True) as db:
                obj = self._load(db, objective_id)
                item = next(
                    (
                        a
                        for a in self._assignments(db, objective_id)
                        if a["assignment_id"] == assignment_id
                    ),
                    None,
                )
                if item is None:
                    raise ValueError("Choose an assignment in this objective")
                item["display_name"] = display_name
                self._save_assignment(db, item)
                self._event(
                    db,
                    obj,
                    "agent-renamed",
                    {"assignment_id": assignment_id, "display_name": display_name},
                )
            return self.snapshot(objective_id)
        if action == "retry":
            return self.retry(objective_id, assignment_id, value)
        if action == "approve":
            return self.approve(objective_id, assignment_id, value)
        if action == "request_review":
            if assignment_id is not None:
                raise ValueError("Additional review operates on the whole objective")
            return self.request_review(objective_id, value)
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
                    and not self._retryable(db, item)
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

    @staticmethod
    def _require_terminated(item):
        custody = item.get("execution")
        if custody and not ObjectiveStore._proven_terminated(custody):
            raise StaleWriterError("Worker process-tree termination is not confirmed")

    @staticmethod
    def _proven_terminated(custody):
        proof = custody.get("termination_proof") or {}
        # Historical process-group proofs cannot account for setsid descendants.
        # Preserve that evidence, but never use it to release ownership/retry.
        return (
            custody.get("tree_kind") in {"windows-job", "linux-subreaper"}
            and proof.get("tree_terminated") is True
            and all(
                proof.get(key) == custody.get(key)
                for key in ("execution_id", "owner", "dispatch_fence", "tree_kind")
            )
        )

    def _execution_target(
        self, db, objective_id, owner, fence, assignment_id, phase, *, expired=False
    ):
        if (assignment_id is None) == (phase is None) or (
            phase and phase not in {"planning", "integration"}
        ):
            raise ValueError("Specify one execution assignment or phase")
        obj = self._load(db, objective_id)
        item = (
            obj[phase]
            if phase
            else next(
                (
                    a
                    for a in self._assignments(db, objective_id)
                    if a["assignment_id"] == assignment_id
                ),
                None,
            )
        )
        valid_fences = {fence, fence + 1} if expired else {fence}
        if (
            not item
            or not owner
            or item["owner"] != owner
            or type(fence) is not int
            or item["fence"] not in valid_fences
            or (item["fence"] != fence and item["expires_at"] is not None)
        ):
            raise StaleWriterError("Execution ownership no longer matches")
        return obj, item

    def record_execution_custody(
        self,
        objective_id,
        owner,
        dispatch_fence,
        execution_id,
        *,
        assignment_id=None,
        phase=None,
        guardian_pid,
        worker_pid,
        tree_kind,
    ):
        execution_id = _text(execution_id, "execution_id", 200)
        if (
            type(guardian_pid) is not int
            or guardian_pid <= 0
            or type(worker_pid) is not int
            or worker_pid <= 0
            or tree_kind not in {"windows-job", "linux-subreaper"}
        ):
            raise ValueError("Invalid process custody")
        phase_lease = None
        if phase:
            from .worktree_leases import WorktreeManager

            current = self.snapshot(objective_id)
            phase_run = current["run_id"] + (
                "-plan" if phase == "planning" else "-integration"
            )
            leases = [
                lease
                for lease in WorktreeManager(self.root).list()
                if lease.run_id == phase_run
                and lease.task_id == current["task_id"]
                and lease.owner == owner
                and lease.state == "active"
            ]
            if len(leases) == 1:
                phase_lease = leases[0]
        with self._db(True) as db:
            obj, item = self._execution_target(
                db, objective_id, owner, dispatch_fence, assignment_id, phase
            )
            if item["status"] != "running" or item.get("execution"):
                raise StaleWriterError(
                    "Execution is already bound or no longer running"
                )
            item["execution"] = dict(
                execution_id=execution_id,
                owner=owner,
                dispatch_fence=dispatch_fence,
                guardian_pid=guardian_pid,
                worker_pid=worker_pid,
                tree_kind=tree_kind,
                termination_proof=None,
            )
            if phase_lease:
                item.update(
                    worktree=phase_lease.path,
                    branch=phase_lease.branch,
                    lease_id=phase_lease.lease_id,
                    base_sha=phase_lease.base_sha,
                )
            if not phase:
                self._save_assignment(db, item)
            self._save_obj(db, obj)
            self._event(
                db,
                obj,
                "execution-custody-recorded",
                {
                    "execution_id": execution_id,
                    "assignment_id": assignment_id,
                    "phase": phase,
                },
            )

    def record_execution_termination(
        self,
        objective_id,
        owner,
        dispatch_fence,
        execution_id,
        *,
        assignment_id=None,
        phase=None,
        proof,
    ):
        with self._db(True) as db:
            obj, item = self._execution_target(
                db,
                objective_id,
                owner,
                dispatch_fence,
                assignment_id,
                phase,
                expired=True,
            )
            custody = item.get("execution") or {}
            if (
                custody.get("execution_id") != execution_id
                or custody.get("owner") != owner
                or custody.get("dispatch_fence") != dispatch_fence
                or not isinstance(proof, dict)
                or not self._proven_terminated({**custody, "termination_proof": proof})
            ):
                raise StaleWriterError("Termination proof does not match custody")
            if (
                custody.get("termination_proof")
                and custody["termination_proof"] != proof
            ):
                raise ValueError("Execution already has different termination evidence")
            custody["termination_proof"] = dict(proof)
            if not phase:
                self._save_assignment(db, item)
            self._save_obj(db, obj)
            self._event(
                db,
                obj,
                "execution-termination-confirmed",
                {
                    "execution_id": execution_id,
                    "assignment_id": assignment_id,
                    "phase": phase,
                },
            )
        self._release_proven_executions(objective_id)

    def interrupt_execution(
        self, objective_id, owner, dispatch_fence, *, assignment_id=None, phase=None
    ):
        with self._db(True) as db:
            obj, item = self._execution_target(
                db, objective_id, owner, dispatch_fence, assignment_id, phase
            )
            item.update(
                status="needs-attention",
                fence=dispatch_fence + 1,
                expires_at=None,
                blocked_reason="Worker termination is unconfirmed; capacity is retained",
            )
            if not phase:
                self._save_assignment(db, item)
            obj["status"] = "needs-attention"
            self._save_obj(db, obj)
            self._event(
                db,
                obj,
                "execution-interrupted",
                {"assignment_id": assignment_id, "phase": phase},
            )
        self._release_proven_executions(objective_id)

    def _release_proven_executions(self, objective_id=None):
        with self._db() as db:
            objs = [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM agent_objectives")
            ]
            targets = []
            for obj in objs:
                if objective_id and obj["objective_id"] != objective_id:
                    continue
                for phase, item in [
                    (p, obj[p]) for p in ("planning", "integration")
                ] + [(None, a) for a in self._assignments(db, obj["objective_id"])]:
                    custody = item.get("execution") or {}
                    if (
                        item["owner"]
                        and item["expires_at"] is None
                        and item["fence"] == custody.get("dispatch_fence", -2) + 1
                        and self._proven_terminated(custody)
                    ):
                        targets.append((obj, phase, item, custody))
        for obj, phase, item, custody in targets:
            aid = item.get("assignment_id") if not phase else None
            result = {
                **item.get("result", {}),
                "termination_proof": custody["termination_proof"],
            }
            paths = item.get("changed_files", [])
            if not phase and item.get("worktree") and item.get("base_sha"):
                try:
                    from .objective_execution import observe_changes

                    observed = observe_changes(Path(item["worktree"]), item["base_sha"])
                    result["git_evidence"] = observed
                    paths = observed["changed_files"]
                except Exception:  # noqa: BLE001 - termination remains proven even if Git inspection fails
                    result["recovery_error"] = "Retained worktree requires inspection"
            try:
                from .execution_scope import assignment_cost_events

                run_id = (
                    item.get("run_id")
                    if not phase
                    else obj["run_id"]
                    + ("-plan" if phase == "planning" else "-integration")
                )
                events = assignment_cost_events(self.root, run_id)
                for event in events:
                    self.record_cost(
                        obj["objective_id"],
                        aid,
                        event["operation_key"],
                        event.get("amount_usd"),
                        event.get("measurement_kind", "unavailable"),
                    )
                if not events and phase != "integration":
                    self.record_cost(
                        obj["objective_id"],
                        aid,
                        run_id + "-provider",
                        None,
                        "unavailable",
                    )
                self.acknowledge_interrupted(
                    obj["objective_id"],
                    custody["owner"],
                    custody["dispatch_fence"],
                    assignment_id=aid,
                    phase=phase,
                    result=result,
                    changed_files=paths,
                )
            except StaleWriterError:
                continue
            from .worktree_leases import WorktreeManager

            manager = WorktreeManager(self.root)
            run_id = (
                item.get("run_id")
                if not phase
                else obj["run_id"]
                + ("-plan" if phase == "planning" else "-integration")
            )
            for lease in manager.list():
                if (
                    lease.run_id == run_id
                    and lease.owner == custody["owner"]
                    and lease.state == "active"
                ):
                    manager.release(lease.lease_id, owner=custody["owner"])

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
        self._release_proven_executions(objective_id)
        return recovered

    def acknowledge_interrupted(
        self,
        objective_id,
        owner,
        dispatch_fence,
        *,
        assignment_id=None,
        phase=None,
        result=None,
        changed_files=(),
    ):
        if (assignment_id is None) == (phase is None):
            raise ValueError("Specify one interrupted assignment or phase")
        if phase is not None and phase not in {"planning", "integration"}:
            raise ValueError("Unknown objective phase")
        paths = _paths(changed_files, limit=10000, protected=False)
        with self._db(True) as db:
            obj = self._load(db, objective_id)
            item = (
                obj[phase]
                if phase
                else self._owned(
                    db,
                    objective_id,
                    assignment_id,
                    owner,
                    dispatch_fence + 1,
                )
            )
            if (
                item["owner"] != owner
                or item["fence"] != dispatch_fence + 1
                or item["expires_at"] is not None
                or item["status"] not in {"needs-attention", "stopping"}
            ):
                raise StaleWriterError("Interrupted ownership no longer matches")
            self._require_terminated(item)
            item.update(
                status="cancelled"
                if item["status"] == "stopping"
                else "needs-attention",
                last_owner=owner,
                owner="",
                result=result or {},
            )
            if phase is None:
                item.update(
                    changed_files=paths,
                    activity="Interrupted worker terminated",
                    blocked_reason="Execution was interrupted; retained changes require review",
                )
                self._save_assignment(db, item)
            else:
                self._save_obj(db, obj)
            self._refresh(db, obj)
            self._event(
                db,
                obj,
                "interrupted-owner-terminated",
                {
                    "assignment_id": assignment_id,
                    "phase": phase,
                    "owner": owner,
                    "dispatch_fence": dispatch_fence,
                },
            )
        return self.snapshot(objective_id)

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
            if integration.get("execution"):
                self._require_terminated(integration)
                history = integration.setdefault("execution_history", [])
                if len(history) >= 32:
                    raise ValueError("Integration attempt limit reached")
                history.append(integration.pop("execution"))
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
            self._require_terminated(integration)
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
