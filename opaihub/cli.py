from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .analytics import build_analytics_summary
from .dashboard import build_dashboard
from .dashboard_html import build_dashboard_html
from .discovery import discover_tools
from .health import health_all, health_history, run_health_check
from .loader import hub_root, load_named_registry, registry_items
from .local_models import discover_local_models
from .mcp import render_mcp_config, write_mcp_config
from .model_intelligence import recommend_model
from .registry_writer import add_tool_entry, build_tool_entry
from .sandbox import classify_command
from .scheduler import create_schedule, list_schedules
from .skills import skill_items, skill_status
from .state import (
    attach_project,
    effective_mcp_servers,
    effective_tools,
    load_state,
    set_mcp,
    set_tool,
)
from .team import cloud_status, init_team
from .validator import validate_all, validate_registry
from .workflow_runner import run_workflow


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _project(path: str | None) -> Path:
    return Path(path or ".").expanduser().resolve()


def _load_text(root: Path, relative: str) -> str:
    path = hub_root(root) / relative
    return path.read_text(encoding="utf-8") if path.exists() else ""


def cmd_init(args: argparse.Namespace) -> int:
    root = _project(args.project)
    hub = hub_root(root)
    print_json(
        {
            "hub": str(hub),
            "project": str(root),
            "status": "ready" if hub.exists() else "missing",
            "next_steps": ["op-hub scan", "op-hub list-tools", "op-hub doctor"],
        }
    )
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    root = _project(args.project)
    registries = {
        name: len(registry_items(name, root))
        for name in ["tools", "agents", "workflows", "mcp_servers", "models"]
    }
    print_json(
        {"hub": str(hub_root(root)), "project": str(root), "registries": registries}
    )
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = _project(args.project)
    result = (
        validate_registry(args.registry, root) if args.registry else validate_all(root)
    )
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_list(args: argparse.Namespace) -> int:
    root = _project(args.project)
    items = (
        effective_tools(root)
        if args.registry == "tools"
        else registry_items(args.registry, root)
    )
    if args.category:
        items = [item for item in items if item.get("category") == args.category]
    if args.json:
        print_json(items)
    else:
        for item in items:
            status = item.get("status", "registered")
            enabled = item.get("effective_enabled")
            enabled_note = "" if enabled is None else f" enabled={enabled}"
            cost = (
                item.get("cost_level")
                or item.get("default_model_tier")
                or item.get("cost_policy", "")
            )
            label = item.get("name") or item.get("trigger") or item.get("id")
            print(f"{item.get('id')}: {label} [{status}] {cost}{enabled_note}")
    return 0


def cmd_tool(args: argparse.Namespace) -> int:
    root = _project(args.project)
    tools = effective_tools(root)
    if args.tool_command == "health":
        if args.id:
            matches = [tool for tool in tools if tool.get("id") == args.id]
            if not matches:
                print_json({"status": "missing", "id": args.id})
                return 2
            print_json(run_health_check(matches[0], root, timeout=args.timeout))
        else:
            print_json(health_all(root, category=args.category))
    elif args.tool_command in {"enable", "disable"}:
        if not args.id:
            print_json({"status": "error", "message": "tool id required"})
            return 2
        state = set_tool(root, args.id, enabled=args.tool_command == "enable")
        print_json(
            {
                "status": "updated",
                "id": args.id,
                "action": args.tool_command,
                "enabled_tools": state.get("enabled_tools", []),
                "disabled_tools": state.get("disabled_tools", []),
            }
        )
    elif args.tool_command == "add":
        entry = build_tool_entry(args)
        print_json(add_tool_entry(root, entry))
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    agents = registry_items("agents", _project(args.project))
    matches = [agent for agent in agents if agent.get("id") == args.id]
    print_json(
        {
            "status": "planned",
            "agent": matches[0] if matches else None,
            "note": "Execution wiring remains policy-gated; use workflow plans first.",
        }
    )
    return 0


def cmd_workflow(args: argparse.Namespace) -> int:
    root = _project(args.project)
    print_json(run_workflow(root, args.id, execute=args.execute, timeout=args.timeout))
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    root = _project(args.project)
    items = effective_mcp_servers(root)
    if args.mcp_command == "list":
        print_json(
            items
            if args.json
            else [
                {"id": item["id"], "enabled": item["effective_enabled"]}
                for item in items
            ]
        )
    elif args.mcp_command in {"enable", "disable"}:
        state = set_mcp(root, args.id, enabled=args.mcp_command == "enable")
        print_json(
            {
                "status": "updated",
                "id": args.id,
                "action": args.mcp_command,
                "enabled_mcp_servers": state.get("enabled_mcp_servers", []),
                "disabled_mcp_servers": state.get("disabled_mcp_servers", []),
            }
        )
    elif args.mcp_command == "render":
        if args.write:
            print_json({"status": "written", "path": str(write_mcp_config(root))})
        else:
            print_json(render_mcp_config(root))
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.cost_command == "status":
        print_json(
            load_named_registry("models", root)
            | {"budget": _load_text(root, "cost/budget.yaml")}
        )
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.context_command == "build":
        print_json(
            {
                "status": "planned",
                "project": str(root),
                "policy": "Use hub/context/context_policy.yaml and OPcoding op context for Phase 2.",
            }
        )
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.project_command == "attach":
        print_json(attach_project(root, force=args.force))
    elif args.project_command == "status":
        print_json(
            {
                "state": load_state(root),
                "effective_tools": [
                    {"id": tool["id"], "enabled": tool["effective_enabled"]}
                    for tool in effective_tools(root)
                ],
                "effective_mcp_servers": [
                    {"id": server["id"], "enabled": server["effective_enabled"]}
                    for server in effective_mcp_servers(root)
                ],
            }
        )
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    root = _project(args.project)
    path = build_dashboard_html(root) if args.html else build_dashboard(root)
    print_json({"path": str(path), "format": "html" if args.html else "markdown"})
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.models_command == "discover-local":
        print_json(discover_local_models(root))
    elif args.models_command == "recommend":
        print_json(recommend_model(root, args.task))
    elif args.models_command == "eval":
        from .eval_harness import run_eval

        print_json(run_eval(root, write=not args.no_write))
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    from .policy import resolve_policy, set_profile

    root = _project(args.project)
    if args.policy_command == "show":
        print_json(resolve_policy(root))
        return 0
    if args.policy_command == "set":
        result = set_profile(root, args.profile)
        print_json(result)
        return 0 if result.get("status") == "updated" else 2
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.skills_command == "list":
        items = skill_items(root)
        if args.json:
            print_json(items)
        else:
            for item in items:
                default = " default" if item.get("enabled_by_default") else ""
                print(f"{item['id']}: {item['name']} [{item['cost_policy']}]{default}")
    elif args.skills_command == "doctor":
        print_json(skill_status(root))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.health_command == "history":
        print_json(health_history(root, limit=args.limit))
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.discover_command == "tools":
        print_json(discover_tools(root))
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.schedule_command == "create":
        print_json(create_schedule(root, args.workflow_id, args.cadence))
    elif args.schedule_command == "list":
        print_json(list_schedules(root))
    return 0


def cmd_automation(args: argparse.Namespace) -> int:
    from .background_runs import (
        BackgroundRunner,
        enqueue_automation,
        list_automation_schedules,
        list_runs,
        pipeline_executor,
        read_notifications,
        recover_interrupted_runs,
        request_cancel,
        schedule_automation,
        tick_automations,
    )

    root = _project(args.project)
    try:
        if args.automation_command == "enqueue":
            run = enqueue_automation(
                root,
                args.workflow_id,
                args.task,
                allow_cloud=args.allow_cloud,
                cloud_confirmed=args.confirm_cloud,
            )
            print_json(run.to_dict())
        elif args.automation_command == "list":
            print_json([run.to_dict() for run in list_runs(root, status=args.status)])
        elif args.automation_command == "cancel":
            print_json(request_cancel(root, args.run_id).to_dict())
        elif args.automation_command == "run":
            runner = BackgroundRunner(
                root, executor=pipeline_executor(model_id=args.model, mode=args.mode)
            )
            print_json(runner.run_now(args.run_id).to_dict())
        elif args.automation_command == "tick":
            print_json([run.to_dict() for run in tick_automations(root)])
        elif args.automation_command == "notifications":
            print_json(read_notifications(root, limit=args.limit))
        elif args.automation_command == "schedule":
            print_json(
                schedule_automation(
                    root,
                    args.workflow_id,
                    args.task,
                    cadence=args.cadence,
                    allow_cloud=args.allow_cloud,
                    cloud_confirmed=args.confirm_cloud,
                )
            )
        elif args.automation_command == "schedules":
            print_json(list_automation_schedules(root))
        elif args.automation_command == "recover":
            print_json([run.to_dict() for run in recover_interrupted_runs(root)])
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print_json({"status": "error", "message": str(exc)})
        return 2
    return 0


def cmd_analytics(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.analytics_command == "status":
        print_json(build_analytics_summary(root))
    return 0


def cmd_savings(args: argparse.Namespace) -> int:
    from .savings import build_savings_report, render_savings_markdown

    root = _project(args.project)
    report = build_savings_report(root)
    if getattr(args, "markdown", False):
        print(render_savings_markdown(report))
    else:
        print_json(report)
    return 0


def cmd_sandbox(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.sandbox_command == "check":
        print_json(classify_command(args.command_text, root))
    return 0


def cmd_team(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.team_command == "init":
        print_json(init_team(root, args.mode))
    elif args.team_command == "cloud-status":
        print_json(cloud_status(root))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    root = _project(args.project)
    print_json(
        {
            "scan": {"project": str(root), "hub": str(hub_root(root))},
            "registries": {
                name: len(registry_items(name, root))
                for name in ["tools", "agents", "workflows", "mcp_servers", "models"]
            },
            "validation": validate_all(root),
            "tool_health": health_all(root),
            "local_models": discover_local_models(root),
            "safe_next_steps": [
                "Run opai doctor for the branded readiness check.",
                "Review hub/registry/tools.yaml before enabling cloud tools.",
                "Use opai hub tool health --id <tool> for targeted checks.",
                "Use opai hub project attach to create a project overlay.",
            ],
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="op-hub", description="OPai / OP AI Hub registry CLI"
    )
    parser.add_argument("--project", default=".", help="Project root")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("scan")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("validate")
    p.add_argument(
        "--registry", choices=["tools", "agents", "workflows", "mcp_servers", "models"]
    )
    p.set_defaults(func=cmd_validate)

    for command, registry in [
        ("list-tools", "tools"),
        ("list-agents", "agents"),
        ("list-workflows", "workflows"),
    ]:
        p = sub.add_parser(command)
        p.set_defaults(func=cmd_list, registry=registry)
        p.add_argument("--category")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("tool")
    tool_sub = p.add_subparsers(dest="tool_command", required=True)
    t = tool_sub.add_parser("health")
    t.add_argument("--id")
    t.add_argument("--category")
    t.add_argument("--timeout", type=int, default=30)
    t.set_defaults(func=cmd_tool)
    t = tool_sub.add_parser("add")
    t.add_argument("--id", required=True)
    t.add_argument("--name")
    t.add_argument("--category", default="miscellaneous")
    t.add_argument("--description", required=True)
    t.add_argument("--status", default="optional")
    t.add_argument("--type", default="local")
    t.add_argument("--cost-level", default="free")
    t.add_argument("--permission-level", default="low")
    t.add_argument("--local-first", action=argparse.BooleanOptionalAction, default=True)
    t.add_argument("--open-source", action=argparse.BooleanOptionalAction, default=True)
    t.add_argument(
        "--requires-api-key", action=argparse.BooleanOptionalAction, default=None
    )
    t.add_argument("--env-vars", default="")
    t.add_argument("--install-command")
    t.add_argument("--run-command")
    t.add_argument("--health-command")
    t.add_argument("--inputs")
    t.add_argument("--outputs")
    t.add_argument("--tags")
    t.add_argument("--docs-url")
    t.add_argument("--notes")
    t.add_argument(
        "--enabled-by-default", action=argparse.BooleanOptionalAction, default=False
    )
    t.set_defaults(func=cmd_tool)
    for name in ["enable", "disable"]:
        t = tool_sub.add_parser(name)
        t.add_argument("id", nargs="?")
        t.set_defaults(func=cmd_tool)

    p = sub.add_parser("agent")
    agent_sub = p.add_subparsers(dest="agent_command", required=True)
    a = agent_sub.add_parser("run")
    a.add_argument("id")
    a.set_defaults(func=cmd_agent)

    p = sub.add_parser("workflow")
    workflow_sub = p.add_subparsers(dest="workflow_command", required=True)
    w = workflow_sub.add_parser("run")
    w.add_argument("id")
    w.add_argument("--execute", action="store_true")
    w.add_argument("--timeout", type=int, default=120)
    w.set_defaults(func=cmd_workflow)

    p = sub.add_parser("mcp")
    mcp_sub = p.add_subparsers(dest="mcp_command", required=True)
    m = mcp_sub.add_parser("list")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_mcp)
    for name in ["enable", "disable"]:
        m = mcp_sub.add_parser(name)
        m.add_argument("id")
        m.set_defaults(func=cmd_mcp)
    m = mcp_sub.add_parser("render")
    m.add_argument("--write", action="store_true")
    m.set_defaults(func=cmd_mcp)

    p = sub.add_parser("cost")
    cost_sub = p.add_subparsers(dest="cost_command", required=True)
    c = cost_sub.add_parser("status")
    c.set_defaults(func=cmd_cost)

    p = sub.add_parser("context")
    context_sub = p.add_subparsers(dest="context_command", required=True)
    c = context_sub.add_parser("build")
    c.set_defaults(func=cmd_context)

    p = sub.add_parser("project")
    project_sub = p.add_subparsers(dest="project_command", required=True)
    pr = project_sub.add_parser("attach")
    pr.add_argument("--force", action="store_true")
    pr.set_defaults(func=cmd_project)
    pr = project_sub.add_parser("status")
    pr.set_defaults(func=cmd_project)

    p = sub.add_parser("dashboard")
    p.add_argument("--html", action="store_true")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("models")
    models_sub = p.add_subparsers(dest="models_command", required=True)
    mo = models_sub.add_parser("discover-local")
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser("recommend")
    mo.add_argument("task")
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser("eval")
    mo.add_argument("--no-write", action="store_true")
    mo.set_defaults(func=cmd_models)

    p = sub.add_parser("policy")
    policy_sub = p.add_subparsers(dest="policy_command", required=True)
    po = policy_sub.add_parser("show")
    po.set_defaults(func=cmd_policy)
    po = policy_sub.add_parser("set")
    po.add_argument(
        "profile",
        choices=["solo-cheap", "solo-balanced", "team-safe", "enterprise-strict"],
    )
    po.set_defaults(func=cmd_policy)

    p = sub.add_parser("skills")
    skills_sub = p.add_subparsers(dest="skills_command", required=True)
    sk = skills_sub.add_parser("list")
    sk.add_argument("--json", action="store_true")
    sk.set_defaults(func=cmd_skills)
    sk = skills_sub.add_parser("doctor")
    sk.set_defaults(func=cmd_skills)

    p = sub.add_parser("health")
    health_sub = p.add_subparsers(dest="health_command", required=True)
    he = health_sub.add_parser("history")
    he.add_argument("--limit", type=int, default=10)
    he.set_defaults(func=cmd_health)

    p = sub.add_parser("discover")
    discover_sub = p.add_subparsers(dest="discover_command", required=True)
    di = discover_sub.add_parser("tools")
    di.set_defaults(func=cmd_discover)

    p = sub.add_parser("schedule")
    schedule_sub = p.add_subparsers(dest="schedule_command", required=True)
    sc = schedule_sub.add_parser("create")
    sc.add_argument("workflow_id")
    sc.add_argument(
        "--cadence", default="daily", choices=["manual", "daily", "weekly", "hourly"]
    )
    sc.set_defaults(func=cmd_schedule)
    sc = schedule_sub.add_parser("list")
    sc.set_defaults(func=cmd_schedule)

    p = sub.add_parser("automation")
    automation_sub = p.add_subparsers(dest="automation_command", required=True)
    au = automation_sub.add_parser("enqueue")
    au.add_argument("workflow_id")
    au.add_argument("--task", required=True)
    au.add_argument("--allow-cloud", action="store_true")
    au.add_argument("--confirm-cloud", action="store_true")
    au.set_defaults(func=cmd_automation)
    au = automation_sub.add_parser("list")
    au.add_argument("--status")
    au.set_defaults(func=cmd_automation)
    au = automation_sub.add_parser("cancel")
    au.add_argument("run_id")
    au.set_defaults(func=cmd_automation)
    au = automation_sub.add_parser("run")
    au.add_argument("run_id")
    au.add_argument("--model")
    au.add_argument("--mode")
    au.set_defaults(func=cmd_automation)
    au = automation_sub.add_parser("tick")
    au.set_defaults(func=cmd_automation)
    au = automation_sub.add_parser("notifications")
    au.add_argument("--limit", type=int, default=20)
    au.set_defaults(func=cmd_automation)
    au = automation_sub.add_parser("schedule")
    au.add_argument("workflow_id")
    au.add_argument("--task", required=True)
    au.add_argument(
        "--cadence", default="daily", choices=["manual", "hourly", "daily", "weekly"]
    )
    au.add_argument("--allow-cloud", action="store_true")
    au.add_argument("--confirm-cloud", action="store_true")
    au.set_defaults(func=cmd_automation)
    au = automation_sub.add_parser("schedules")
    au.set_defaults(func=cmd_automation)
    au = automation_sub.add_parser("recover")
    au.set_defaults(func=cmd_automation)

    p = sub.add_parser("analytics")
    analytics_sub = p.add_subparsers(dest="analytics_command", required=True)
    an = analytics_sub.add_parser("status")
    an.set_defaults(func=cmd_analytics)

    p = sub.add_parser("savings", help="Estimated AI spend saved (cost firewall)")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(func=cmd_savings)

    p = sub.add_parser("sandbox")
    sandbox_sub = p.add_subparsers(dest="sandbox_command", required=True)
    sa = sandbox_sub.add_parser("check")
    sa.add_argument("--command", dest="command_text", required=True)
    sa.set_defaults(func=cmd_sandbox)

    p = sub.add_parser("team")
    team_sub = p.add_subparsers(dest="team_command", required=True)
    te = team_sub.add_parser("init")
    te.add_argument("--mode", default="solo", choices=["solo", "team"])
    te.set_defaults(func=cmd_team)
    te = team_sub.add_parser("cloud-status")
    te.set_defaults(func=cmd_team)

    p = sub.add_parser("doctor")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
