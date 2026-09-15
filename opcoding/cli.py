from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent_runner import run_agent_batch
from .ci import write_github_workflow
from .context_manager import (
    build_context,
    context_path,
    load_profile,
    onboard_project,
    profile_path,
)
from .cost import budget_report, log_route, route_task
from .dashboard import build_dashboard
from .deploy import deploy_plan
from .doctor import doctor
from .free_tools import install_tools, run_tool, tools_doctor
from .gitops import (
    pr_description,
    secret_scan_diff,
    suggest_branch_name,
    suggest_commit_message,
    git_summary,
)
from .hooks import hooks_check, install_precommit
from .mcp_manager import mcp_doctor, render_codex_mcp_config
from .memory import add_decision, add_memory, list_memory
from .models import build_model_bundle
from .integrate import integrate_status, launch_tool, statusline
from .morph import morph_apply_snippet, morph_doctor
from .project_index import build_index, search_index
from .reviewer import review_diff
from .scanner import scan_project
from .shipper import ship_plan
from .testing import analyze_failure, detect_test_command, run_tests
from .utils import print_json, resolve_project_path, run_command, write_json, write_text


def project_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Project path, defaults to current directory",
    )


def cmd_init(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    profile = onboard_project(root, force=args.force)
    print(f"Initialized OPcoding profile for {root}")
    print(f"- profile: {profile_path(root)}")
    print(f"- context: {context_path(root)}")
    print(f"- languages: {', '.join(profile.get('languages', [])) or 'none detected'}")
    print(
        f"- commands: {', '.join(sorted(profile.get('commands', {}).keys())) or 'none detected'}"
    )
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    profile = scan_project(root)
    if args.save:
        write_json(profile_path(root), profile)
    if args.json:
        print_json(profile)
    else:
        print(f"Project: {profile['name']}")
        print(f"Root: {profile['root']}")
        print(f"Files: {profile['file_count']}")
        print(f"Languages: {', '.join(profile['languages']) or 'none'}")
        print(f"Frameworks: {', '.join(profile['frameworks']) or 'none'}")
        print("Commands:")
        for name, command in sorted(profile["commands"].items()):
            print(f"  {name}: {command}")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    profile = load_profile(root) or scan_project(root)
    context = build_context(root, profile)
    if args.refresh:
        write_text(context_path(root), context)
    if args.print:
        print(context)
    else:
        print(f"Context length: {len(context)} chars")
        print(f"Context file: {context_path(root)}")
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    route = route_task(args.task, root=root, context_chars=args.context_chars)
    log_route(root, route)
    print_json(route)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    route = route_task(args.task, root=root, context_chars=args.context_chars)
    log_route(root, route)
    print("Plan route")
    print(f"- route: {route['route']} ({route['route_description']})")
    print(f"- agents: {', '.join(route['agents'])}")
    print("- local-first checklist:")
    for step in route["pre_ai_steps"]:
        print(f"  - {step}")
    print("- expected output: target files, steps, tests, risks")
    return 0


def cmd_refactor(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    task = f"refactor {args.scope}: {args.goal}"
    route = route_task(task, root=root, context_chars=args.context_chars)
    log_route(root, route)
    print("Refactor route")
    print(f"- scope: {args.scope}")
    print(f"- goal: {args.goal}")
    print(f"- route: {route['route']} ({route['route_description']})")
    print(
        "- required before edits: current tests, targeted scope, behavior-preserving plan"
    )
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    if args.cost_command == "report":
        print_json(budget_report(root))
    elif args.cost_command == "route":
        route = route_task(args.task, root=root, context_chars=args.context_chars)
        log_route(root, route)
        print_json(route)
    return 0


def cmd_git(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    try:
        if args.git_command == "summary":
            print_json(git_summary(root, args.base))
        elif args.git_command == "secrets":
            print_json(secret_scan_diff(root, staged=args.staged))
        elif args.git_command == "commit-message":
            print(suggest_commit_message(root, args.base))
        elif args.git_command == "branch-name":
            print(suggest_branch_name(args.task, prefix=args.prefix))
        elif args.git_command == "pr":
            print(pr_description(root, args.base))
    except ValueError as exc:
        # Unsafe or unknown ref (#20): refuse loudly, never run the command.
        print_json({"status": "error", "message": str(exc)})
        return 2
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    command = args.cmd or detect_test_command(root, full=args.full)
    if not command:
        print(
            "No test command detected. Run op init first or pass --cmd.",
            file=sys.stderr,
        )
        return 2
    if args.dry_run:
        print(command)
        return 0
    result = run_tests(root, command, timeout=args.timeout)
    print_json(result)
    return 0 if result["returncode"] == 0 else result["returncode"]


def cmd_review(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    try:
        result = review_diff(root, args.base)
    except ValueError as exc:
        # Unsafe or unknown ref (#20): refuse loudly, never run the command.
        print_json({"status": "error", "message": str(exc)})
        return 2
    if args.json:
        print_json(result)
    else:
        findings = result["findings"]
        print(f"Findings: {len(findings)}")
        for item in findings:
            print(f"- [{item['severity']}] {item['title']}: {item['body']}")
        print(result["next_step"])
    return 0


def cmd_fix(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    result = run_command(args.cmd, root, timeout=args.timeout)
    analysis = analyze_failure(result.combined_output)
    payload = {
        "command": args.cmd,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "duration_seconds": round(result.duration_seconds, 3),
        "failure_clues": analysis,
        "stdout_tail": result.stdout[-3000:],
        "stderr_tail": result.stderr[-3000:],
        "next_step": "Use the clues and recent diff for a minimal patch before escalating to AI.",
    }
    print_json(payload)
    return 0 if result.returncode == 0 else result.returncode


def cmd_doctor(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    print_json(doctor(root))
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    profile = load_profile(root)
    if not profile:
        profile = onboard_project(root, force=False)
    route = route_task(args.task, root=root)
    log_route(root, route)
    print("OPcoding auto route")
    print(f"- route: {route['route']} ({route['route_description']})")
    print(f"- agents: {', '.join(route['agents'])}")
    print("- recommended local-first steps:")
    for step in route["pre_ai_steps"]:
        print(f"  - {step}")
    if route["requires_confirmation"]:
        print("- confirmation required before expensive model use")
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    result = run_agent_batch(
        root,
        args.task,
        execute_model=args.execute_model,
        confirm_expensive=args.confirm_expensive,
    )
    print_json(result)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    bundle = build_model_bundle(
        root,
        args.task,
        execute=args.execute_model,
        confirm_expensive=args.confirm_expensive,
    )
    if args.prompt:
        print(bundle.get("prompt", ""))
    else:
        printable = {key: value for key, value in bundle.items() if key != "prompt"}
        print_json(printable)
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    if args.index_command == "build":
        print_json(build_index(root))
    elif args.index_command == "search":
        print_json(search_index(root, args.query))
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    if args.memory_command == "add":
        print_json(add_memory(root, args.kind, args.title, args.body))
    elif args.memory_command == "decision":
        print_json(add_decision(root, args.title, args.body, status=args.status))
    elif args.memory_command == "list":
        print_json(list_memory(root, limit=args.limit))
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    if args.mcp_command == "doctor":
        print_json(mcp_doctor(root))
    elif args.mcp_command == "render":
        print_json(render_codex_mcp_config(root))
    return 0


def cmd_ship(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    print_json(ship_plan(root, args.task))
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    print_json(deploy_plan(root))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    path = build_dashboard(root)
    print(str(path))
    return 0


def cmd_hooks(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    if args.hooks_command == "install":
        opcoding_root = Path(__file__).resolve().parents[1]
        print_json(install_precommit(root, opcoding_root))
        return 0
    if args.hooks_command == "check":
        return hooks_check(root)
    return 0


def cmd_ci(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    if args.ci_command == "github":
        try:
            print_json(write_github_workflow(root))
        except ValueError as exc:
            print_json({"status": "blocked", "message": str(exc)})
            return 2
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    if args.tools_command == "doctor":
        print_json(tools_doctor(root))
    elif args.tools_command == "install":
        print_json(install_tools(root, tool_set=args.set))
    elif args.tools_command == "run":
        print_json(run_tool(root, args.name, timeout=args.timeout))
    return 0


def cmd_morph(args: argparse.Namespace) -> int:
    root = resolve_project_path(args.project)
    if args.morph_command == "doctor":
        print_json(morph_doctor())
    elif args.morph_command == "apply-snippet":
        result = morph_apply_snippet(
            root,
            instruction=args.instruction,
            code=args.code,
            update=args.update or "",
            execute=args.execute,
            confirm_spend=args.confirm_spend,
            model=args.model,
        )
        if not args.show_payload and "payload" in result:
            result = {key: value for key, value in result.items() if key != "payload"}
        print_json(result)
    return 0


def cmd_statusline(args: argparse.Namespace) -> int:
    print(statusline(getattr(args, "project", None)))
    return 0


def cmd_integrate(args: argparse.Namespace) -> int:
    if args.integrate_command == "status":
        from .utils import print_json

        print_json(integrate_status())
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    return launch_tool(args.tool, getattr(args, "tool_args", []) or [])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="op", description="OPcoding local-first coding workspace CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Create or refresh .opcoding project profile")
    project_arg(p)
    p.add_argument(
        "--force", action="store_true", help="Regenerate existing profile files"
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("scan", help="Scan project stack, commands, docs, and git")
    project_arg(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("context", help="Build or print compressed project context")
    project_arg(p)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--print", action="store_true")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser(
        "route", help="Route a task through local/cheap/strong/Max levels"
    )
    p.add_argument("task")
    p.add_argument("--project", default=".")
    p.add_argument("--context-chars", type=int, default=0)
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("plan", help="Create a local-first plan route for a task")
    p.add_argument("task")
    p.add_argument("--project", default=".")
    p.add_argument("--context-chars", type=int, default=0)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("refactor", help="Prepare a safe refactor route")
    p.add_argument("scope")
    p.add_argument("goal")
    p.add_argument("--project", default=".")
    p.add_argument("--context-chars", type=int, default=0)
    p.set_defaults(func=cmd_refactor)

    p = sub.add_parser("cost", help="Budget and model routing helpers")
    project_arg(p)
    cost_sub = p.add_subparsers(dest="cost_command", required=True)
    c = cost_sub.add_parser("report")
    c.set_defaults(func=cmd_cost)
    c = cost_sub.add_parser("route")
    c.add_argument("task")
    c.add_argument("--context-chars", type=int, default=0)
    c.set_defaults(func=cmd_cost)

    p = sub.add_parser("git", help="Safe GitOps helpers")
    project_arg(p)
    git_sub = p.add_subparsers(dest="git_command", required=True)
    for name in ["summary", "commit-message", "pr"]:
        g = git_sub.add_parser(name)
        g.add_argument("--base")
        g.set_defaults(func=cmd_git)
    g = git_sub.add_parser("secrets")
    g.add_argument("--staged", action="store_true")
    g.set_defaults(func=cmd_git)
    g = git_sub.add_parser("branch-name")
    g.add_argument("task")
    g.add_argument("--prefix", default="codex")
    g.set_defaults(func=cmd_git)

    p = sub.add_parser("test", help="Detect and run project tests")
    project_arg(p)
    p.add_argument("--cmd", help="Explicit test command")
    p.add_argument("--full", action="store_true", help="Prefer full test command")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--timeout", type=int, default=240)
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("review", help="Local-first diff review")
    project_arg(p)
    p.add_argument("--base")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser(
        "fix", help="Run failing command and extract local failure clues"
    )
    project_arg(p)
    p.add_argument("--cmd", required=True)
    p.add_argument("--timeout", type=int, default=240)
    p.set_defaults(func=cmd_fix)

    p = sub.add_parser("doctor", help="Check local tool readiness")
    project_arg(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("auto", help="Auto-route a task without spending AI credits")
    p.add_argument("task")
    p.add_argument("--project", default=".")
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("agents", help="Run a batched local-first agent plan")
    p.add_argument("task")
    p.add_argument("--project", default=".")
    p.add_argument("--execute-model", action="store_true")
    p.add_argument("--confirm-expensive", action="store_true")
    p.set_defaults(func=cmd_agents)

    p = sub.add_parser("ask", help="Prepare or execute a routed model prompt")
    p.add_argument("task")
    p.add_argument("--project", default=".")
    p.add_argument("--execute-model", action="store_true")
    p.add_argument("--confirm-expensive", action="store_true")
    p.add_argument("--prompt", action="store_true", help="Print the generated prompt")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("index", help="Build and search a local project index")
    project_arg(p)
    index_sub = p.add_subparsers(dest="index_command", required=True)
    i = index_sub.add_parser("build")
    i.set_defaults(func=cmd_index)
    i = index_sub.add_parser("search")
    i.add_argument("query")
    i.set_defaults(func=cmd_index)

    p = sub.add_parser("memory", help="Local project memory and decisions")
    project_arg(p)
    mem_sub = p.add_subparsers(dest="memory_command", required=True)
    m = mem_sub.add_parser("add")
    m.add_argument("kind")
    m.add_argument("title")
    m.add_argument("body")
    m.set_defaults(func=cmd_memory)
    m = mem_sub.add_parser("decision")
    m.add_argument("title")
    m.add_argument("body")
    m.add_argument("--status", default="accepted")
    m.set_defaults(func=cmd_memory)
    m = mem_sub.add_parser("list")
    m.add_argument("--limit", type=int, default=20)
    m.set_defaults(func=cmd_memory)

    p = sub.add_parser("mcp", help="MCP profile validation and config rendering")
    project_arg(p)
    mcp_sub = p.add_subparsers(dest="mcp_command", required=True)
    mp = mcp_sub.add_parser("doctor")
    mp.set_defaults(func=cmd_mcp)
    mp = mcp_sub.add_parser("render")
    mp.set_defaults(func=cmd_mcp)

    p = sub.add_parser("ship", help="Create a one-prompt-to-two-prompt shipping plan")
    p.add_argument("task")
    p.add_argument("--project", default=".")
    p.set_defaults(func=cmd_ship)

    p = sub.add_parser("deploy", help="Prepare local-first deploy and rollback plan")
    project_arg(p)
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("dashboard", help="Write .opcoding/dashboard.md")
    project_arg(p)
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("hooks", help="Install or run OPcoding git hooks")
    project_arg(p)
    hooks_sub = p.add_subparsers(dest="hooks_command", required=True)
    h = hooks_sub.add_parser("install")
    h.set_defaults(func=cmd_hooks)
    h = hooks_sub.add_parser("check")
    h.set_defaults(func=cmd_hooks)

    p = sub.add_parser("ci", help="Generate CI workflow files")
    project_arg(p)
    ci_sub = p.add_subparsers(dest="ci_command", required=True)
    c = ci_sub.add_parser("github")
    c.set_defaults(func=cmd_ci)

    p = sub.add_parser("tools", help="Install and run free local coding tools")
    project_arg(p)
    tools_sub = p.add_subparsers(dest="tools_command", required=True)
    t = tools_sub.add_parser("doctor")
    t.set_defaults(func=cmd_tools)
    t = tools_sub.add_parser("install")
    t.add_argument("--set", choices=["core", "security", "all"], default="core")
    t.set_defaults(func=cmd_tools)
    t = tools_sub.add_parser("run")
    t.add_argument("name")
    t.add_argument("--timeout", type=int, default=300)
    t.set_defaults(func=cmd_tools)

    p = sub.add_parser(
        "morph", help="Morph Fast Apply adapter, disabled unless explicitly executed"
    )
    project_arg(p)
    morph_sub = p.add_subparsers(dest="morph_command", required=True)
    mo = morph_sub.add_parser("doctor")
    mo.set_defaults(func=cmd_morph)
    mo = morph_sub.add_parser("apply-snippet")
    mo.add_argument("--instruction", required=True)
    mo.add_argument("--code", required=True)
    mo.add_argument("--update")
    mo.add_argument("--model", default="morph-v3-fast")
    mo.add_argument("--execute", action="store_true")
    mo.add_argument("--confirm-spend", action="store_true")
    mo.add_argument("--show-payload", action="store_true")
    mo.set_defaults(func=cmd_morph)

    p = sub.add_parser(
        "statusline", help="Print a one-line Vesta status for shell prompts"
    )
    p.add_argument("project", nargs="?", default=".")
    p.set_defaults(func=cmd_statusline)

    p = sub.add_parser("integrate", help="AI coding tool integration helpers")
    int_sub = p.add_subparsers(dest="integrate_command", required=True)
    int_sub.add_parser("status").set_defaults(func=cmd_integrate)

    p = sub.add_parser(
        "launch", help="Launch an AI coding tool (codex, claude, copilot)"
    )
    p.add_argument("tool", choices=["codex", "claude", "copilot"])
    p.add_argument(
        "tool_args", nargs=argparse.REMAINDER, help="Extra args forwarded to the tool"
    )
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("hooks-check", help=argparse.SUPPRESS)
    project_arg(p)
    p.set_defaults(func=lambda args: hooks_check(resolve_project_path(args.project)))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
