"""User-owned team presentation and bounded, isolated follow-up assignments."""

from __future__ import annotations

import math
from uuid import uuid4

from .agent_objectives import _text, validate_plan


TEAM_ACTIONS = frozenset(
    {
        "add_agent",
        "agent_message",
        "agent_settings",
        "connect_agents",
        "team_layout",
        "start_agent",
        "group_agents",
    }
)


class TeamControlError(ValueError):
    pass


def actor(item):
    return item.get("agent_id") or item["assignment_id"]


def label(value, field, *, empty=False):
    value = _text(value, field, 40, empty=empty)
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} must be a single line")
    return value


def editable(obj):
    return (
        obj["status"] not in {"planning", "cancelled", "stopping"}
        and not obj["planning"]["owner"]
        and not obj["integration"]["owner"]
    )


def projection(obj):
    items = obj["assignments"]
    obj["team_revision"] = obj.get("team_revision", 0)
    obj["team_controls"] = {
        "editable": editable(obj),
        "can_add": editable(obj) and 0 < len(items) < 32,
        "remaining_tasks": 32 - len(items),
    }
    for item in items:
        item["agent_id"] = actor(item)
        latest = max(
            (a for a in items if actor(a) == actor(item)),
            key=lambda a: a.get("team_order", 0),
        )
        item["team_controls"] = {
            "can_start": editable(obj)
            and item["status"] == "pending"
            and bool(item.get("held")),
            "can_message": editable(obj)
            and len(items) < 32
            and latest["status"] in {"pending", "running", "completed"},
            "can_connect": editable(obj)
            and item["status"] in {"pending", "blocked"}
            and not item["owner"]
            and not item["fence"],
        }


def _reset_integration(obj):
    if obj["integration"]["status"] != "pending":
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
    if obj["status"] == "paused":
        obj["paused_from"] = "ready"
    else:
        obj["status"] = "ready"


def control_team(store, objective_id, action, assignment_id, value):
    if not isinstance(value, dict) or type(value.get("revision")) is not int:
        raise ValueError("Refresh the team before changing it")
    fields = {
        "add_agent": {
            "revision",
            "name",
            "objective",
            "model",
            "group",
            "start",
            "budget_usd",
        },
        "start_agent": {"revision"},
        "group_agents": {"revision", "assignment_ids", "group"},
        "agent_message": {"revision", "message"},
        "agent_settings": {"revision", "model", "group"},
        "connect_agents": {"revision", "source_id", "connected"},
        "team_layout": {"revision", "positions"},
    }
    if set(value) - fields[action]:
        raise ValueError("Unknown team setting")
    with store._db(True) as db:
        obj = store._load(db, objective_id)
        items = store._assignments(db, objective_id)
        if value["revision"] != obj.get("team_revision", 0):
            raise ValueError("Your team changed. Review the latest team and try again.")
        item = next((a for a in items if a["assignment_id"] == assignment_id), None)
        if action in {
            "agent_message",
            "agent_settings",
            "connect_agents",
            "start_agent",
        }:
            if item is None:
                raise ValueError("Choose an agent in this objective")
        elif assignment_id is not None:
            raise ValueError("This action applies to the whole team")
        event = {"action": action, "assignment_id": assignment_id}
        if action == "team_layout":
            positions = value.get("positions")
            known = {actor(a) for a in items}
            if not isinstance(positions, dict) or set(positions) - known:
                raise ValueError("Layout must contain agents from this objective")
            clean = {}
            for key, point in positions.items():
                if not isinstance(point, dict) or set(point) != {"x", "y"}:
                    raise ValueError("Each agent needs an x and y position")
                if any(
                    type(n) not in {int, float}
                    or not math.isfinite(n)
                    or not 0 <= n <= 10000
                    for n in point.values()
                ):
                    raise ValueError("Positions must stay on the team map")
                clean[key] = {k: round(n) for k, n in point.items()}
            obj["team_layout"] = clean
        elif action == "group_agents":
            selected = value.get("assignment_ids")
            if (
                not isinstance(selected, list)
                or not 1 <= len(selected) <= 32
                or any(not isinstance(key, str) for key in selected)
                or len(set(selected)) != len(selected)
            ):
                raise ValueError("Choose between one and 32 agents")
            by_id = {a["assignment_id"]: a for a in items}
            if set(selected) - set(by_id):
                raise ValueError("Choose agents from this objective")
            group = label(value.get("group"), "Group", empty=True)
            profiles = {actor(by_id[key]) for key in selected}
            for member in items:
                if actor(member) in profiles:
                    member["group"] = group
                    store._save_assignment(db, member)
        elif action == "start_agent":
            if (
                not editable(obj)
                or item["status"] != "pending"
                or not item.get("held")
                or item["owner"]
                or item["fence"]
            ):
                raise ValueError("This agent is no longer waiting to start")
            item["held"] = False
            store._save_assignment(db, item)
        elif action == "agent_settings":
            if not {"model", "group"} & set(value):
                raise ValueError("Choose a model or group")
            model = _text(value["model"], "model", 200) if "model" in value else None
            group = (
                label(value["group"], "Group", empty=True) if "group" in value else None
            )
            for member in items:
                if actor(member) != actor(item):
                    continue
                if group is not None:
                    member["group"] = group
                if model is not None:
                    member["preferred_model"] = model
                    if (
                        member["status"] == "pending"
                        and not member["owner"]
                        and not member["fence"]
                    ):
                        member.update(
                            model=model,
                            model_authorized=True,
                            route="auto",
                            provider="",
                        )
                store._save_assignment(db, member)
        elif action == "connect_agents":
            source = next(
                (a for a in items if a["assignment_id"] == value.get("source_id")), None
            )
            if (
                not editable(obj)
                or source is None
                or actor(source) == actor(item)
                or item["status"] not in {"pending", "blocked"}
                or item["owner"]
                or item["fence"]
                or type(value.get("connected")) is not bool
            ):
                raise ValueError(
                    "Connections can only change before the receiving task starts"
                )
            deps = list(item["depends_on"])
            if value["connected"] and source["name"] not in deps:
                deps.append(source["name"])
            elif not value["connected"] and source["name"] in deps:
                deps.remove(source["name"])
            candidate = [
                {
                    "name": a["name"],
                    "objective": a["objective"],
                    "depends_on": deps if a is item else a["depends_on"],
                }
                for a in items
            ]
            validate_plan(candidate)
            item["depends_on"] = deps
            if item["status"] == "blocked":
                item.update(status="pending", blocked_reason="")
                _reset_integration(obj)
            store._save_assignment(db, item)
            event.update(
                source_id=source["assignment_id"], connected=value["connected"]
            )
        else:
            if not editable(obj) or not items or len(items) >= 32:
                raise ValueError(
                    "This team cannot accept more work right now (32 tasks per objective)"
                )
            if action == "agent_message":
                chain = [a for a in items if actor(a) == actor(item)]
                latest = max(chain, key=lambda a: a.get("team_order", 0))
                if latest["status"] not in {"pending", "running", "completed"}:
                    raise ValueError(
                        "Resolve this agent's stopped or blocked task before sending more work"
                    )
                message = _text(value.get("message"), "Message", 8000)
                raw = {
                    "name": "followup-" + uuid4().hex[:12],
                    "objective": message,
                    "title": message[:300],
                    "role": latest["role"],
                    "depends_on": [latest["name"]],
                    "intended_paths": latest["intended_paths"],
                    "model": latest.get("preferred_model", latest["model"]),
                    "group": latest.get("group", ""),
                    "capabilities": latest["capabilities"],
                    "parallel_eligible": latest["parallel_eligible"],
                    "risk": latest["risk"],
                    "budget_usd": latest["budget_usd"],
                }
            else:
                if type(value.get("start", True)) is not bool:
                    raise ValueError("Start must be a boolean")
                message = _text(value.get("objective"), "Task", 8000)
                raw = {
                    "name": "custom-" + uuid4().hex[:12],
                    "objective": message,
                    "title": message[:300],
                    "intended_paths": ["."],
                    "model": _text(value.get("model", "auto"), "model", 200),
                    "group": label(value.get("group", ""), "Group", empty=True),
                    "budget_usd": value.get("budget_usd"),
                }
            candidate = [
                {k: a[k] for k in ("name", "objective", "depends_on")} for a in items
            ]
            new = validate_plan([*candidate, raw])[-1]
            new.update(
                agent_id=new["assignment_id"],
                team_order=len(items),
                model_authorized=True,
            )
            if action == "agent_message":
                new.update(
                    agent_id=actor(item),
                    display_name=latest["display_name"],
                    avatar_index=latest["avatar_index"],
                )
            else:
                new["display_name"] = label(
                    value.get("name", new["display_name"]), "Agent name"
                )
            new["held"] = action == "add_agent" and not value.get("start", True)
            new["preferred_model"] = new["model"]
            new["user_message"] = message
            store._insert_plan(db, obj, [new])
            db.execute(
                "UPDATE objective_assignments SET position=? WHERE assignment_id=?",
                (len(items), new["assignment_id"]),
            )
            _reset_integration(obj)
            event.update(assignment_id=new["assignment_id"], message=message)
        obj["team_revision"] = obj.get("team_revision", 0) + 1
        store._save_obj(db, obj)
        store._event(db, obj, "team-updated", event)
    return store.snapshot(objective_id)
