from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from opai import __brand__, __release_stage__, __version__
from opai.context_slim import (
    clean_generated_context,
    context_bloat_report,
    write_ai_ignore_files,
)
from opai.integrations import (
    activate_project,
    install_global_integrations,
    load_global_status,
    project_status,
    render_statusline,
    shell_environment,
)
from opai.installer import install_project
from opai.publish import publish_status, write_publish_status
from opai.terminal_ui import build_welcome, play_animation
from opaihub.cli import main as hub_main
from opaihub.model_intelligence import recommend_model
from opaihub.router import compact_decision, route_task
from opaihub.skills import skill_items, skill_status

PROJECT_ROOT_MARKERS = [
    ".opaihub",
    ".git",
    "pyproject.toml",
    "package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "Cargo.toml",
    "go.mod",
    "composer.json",
    "Gemfile",
    "mix.exs",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "Dockerfile",
]


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def discover_project_root(start: Path) -> Path:
    path = start.expanduser().resolve()
    current = path.parent if path.is_file() else path
    for candidate in [current, *current.parents]:
        if any((candidate / marker).exists() for marker in PROJECT_ROOT_MARKERS):
            return candidate
    return current


def _project(value: str | None) -> Path:
    return discover_project_root(Path(value or "."))


def cmd_version(args: argparse.Namespace) -> int:
    if args.json:
        print_json(
            {
                "brand": __brand__,
                "version": __version__,
                "release_stage": __release_stage__,
            }
        )
    else:
        print(f"{__brand__} {__version__} {__release_stage__}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    root = _project(args.project)
    print_json(
        install_project(
            root,
            install_tools=args.install_tools,
            install_superpowers=not args.no_superpowers,
            timeout=args.timeout,
            global_integrations=args.global_integrations,
            install_shell_aliases=args.shell_aliases,
        )
    )
    return 0


def cmd_delegate(args: argparse.Namespace) -> int:
    root = _project(args.project)
    # Read-only by default (issue #12): delegating to the hub must not activate
    # or write project files. Use `opai activate` for write side effects.
    project_args = ["--project", str(root)]
    return hub_main(project_args + list(args.hub_args))


def cmd_statusline(args: argparse.Namespace) -> int:
    print(render_statusline(width=args.width, color=not args.no_color))
    return 0


def cmd_welcome(args: argparse.Namespace) -> int:
    if args.animate:
        play_animation(
            width=args.width,
            compact=args.compact,
            image_mode=args.image,
            color=not args.no_color,
            frames=args.frames,
            delay=args.delay,
        )
        return 0
    print(
        build_welcome(
            width=args.width,
            compact=args.compact,
            image_mode=args.image,
            color=not args.no_color,
        )
    )
    return 0


def _split_targets(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def cmd_integrate(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser().resolve() if args.home else None
    if args.integrate_command == "install":
        print_json(
            install_global_integrations(
                _project(args.project),
                home=home,
                targets=_split_targets(args.targets),
                install_shell_aliases=args.shell_aliases,
            )
        )
    elif args.integrate_command == "status":
        print_json(load_global_status(home))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print_json(project_status(_project(args.project)))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from opaihub.local_models import discover_local_models
    from opaihub.loader import registry_items
    from opaihub.validator import validate_all

    root = _project(args.project)
    status = project_status(root)
    clients = status["client_integrations"]
    summary = clients["summary"]
    stale = status["stale_paths"]
    validation = validate_all(root)
    readiness = (
        "ready"
        if not summary["broken"] and not summary["missing"] and stale["ok"]
        else "attention"
    )
    payload = {
        "brand": __brand__,
        "version": __version__,
        "release_stage": __release_stage__,
        "project_root": str(root),
        "readiness": readiness,
        "client_integrations": clients,
        "stale_paths": stale,
        "superpowers": status["superpowers"],
        "registries": {
            name: len(registry_items(name, root))
            for name in ["tools", "agents", "workflows", "mcp_servers", "models"]
        },
        "validation": {"ok": validation.get("ok")},
        "local_models": discover_local_models(root),
        "next_steps": [
            "Run opai activate --repair to fix broken or missing client integrations.",
            "Restart AI clients after global skill changes.",
            'Run opai route "<task>" --record to populate the savings ledger.',
        ],
    }
    print_json(payload)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    from opai.integrations import update_opai_source

    print_json(update_opai_source())
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    from opai.integrations import uninstall_opai

    result = uninstall_opai(
        _project(args.project),
        dry_run=not args.confirm,
        remove_project_files=not args.keep_project_files,
    )
    print_json(result)
    return 0


def cmd_slim(args: argparse.Namespace) -> int:
    root = _project(args.project)
    ignore_files = write_ai_ignore_files(root)
    report = context_bloat_report(root)
    payload: dict[str, Any] = {
        "status": "cleaned" if args.clean else "reported",
        "project_root": str(root),
        "ai_ignore_files": ignore_files,
        "report": report,
    }
    if args.clean:
        payload["clean"] = clean_generated_context(root, dry_run=False)
        payload["after"] = context_bloat_report(root)
    print_json(payload)
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    activate_project(_project(args.project), install_global=True)
    command = shutil.which(args.tool)
    launch_args = list(args.tool_args)
    if launch_args and launch_args[0] == "--":
        launch_args = launch_args[1:]
    if not command and args.tool == "copilot":
        gh = shutil.which("gh")
        if gh:
            command = gh
            launch_args = ["copilot", *launch_args]
    if not command:
        print_json(
            {
                "status": "missing",
                "tool": args.tool,
                "message": f"{args.tool} command not found on PATH.",
                "opai_status": "Using OPai",
            }
        )
        return 127
    if not args.welcome:
        print(render_statusline())
    elif args.no_animate:
        print(build_welcome(compact=True, image_mode=args.image))
    else:
        play_animation(
            compact=True, image_mode=args.image, frames=args.frames, delay=args.delay
        )
    try:
        if os.name == "nt" and command.lower().endswith((".bat", ".cmd")):
            cmd_exe = shutil.which("cmd.exe") or "C:\\Windows\\System32\\cmd.exe"
            return subprocess.call(  # nosec
                [cmd_exe, "/c", command, *launch_args], env=shell_environment()
            )
        return subprocess.call([command, *launch_args], env=shell_environment())  # nosec
    except OSError as exc:
        print_json(
            {
                "status": "failed",
                "tool": args.tool,
                "command": command,
                "error": str(exc),
                "opai_status": "Using OPai",
            }
        )
        return 126


def cmd_route(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.activate:
        activate_project(root, install_global=False)
    include_evidence = args.full_evidence or args.verbose
    record = getattr(args, "record", False)
    # Routing stays read-only by default (issue #12). Recording is opt-in and is
    # the only path that writes a ledger event or persists an evidence cache.
    full_decision = route_task(
        root, args.task, include_evidence=True, persist_cache=record
    )
    output = full_decision if include_evidence else compact_decision(full_decision)
    if record:
        from opaihub.ledger import record_route_decision
        from opaihub.router import route_context_sizes

        sizes = route_context_sizes(full_decision)
        event = record_route_decision(
            root,
            args.task,
            model_tier=full_decision["model_tier"],
            workflow=full_decision["workflow"],
            full_context_chars=sizes["full_chars"],
            compact_context_chars=sizes["compact_chars"],
            cache_hit=full_decision.get("evidence_cache_hit", False),
            store_summary=getattr(args, "store_summary", False),
        )
        recorded = {
            "tier": event["model_tier"],
            "estimated_savings_usd": event["estimated_savings_usd"],
            "cloud_call_avoided": event["cloud_call_avoided"],
            "context_chars_saved": event["context_chars_saved"],
            "cache_hit": event["cache_hit"],
            "ledger": ".opaihub/ledger/usage.jsonl",
        }
        if isinstance(output, dict):
            output = {**output, "recorded": recorded}
    print_json(output)
    return 0


def cmd_savings(args: argparse.Namespace) -> int:
    from opaihub.savings import build_savings_report, render_savings_markdown

    root = _project(args.project)
    report = build_savings_report(root)
    export_path = getattr(args, "export", None)
    if export_path:
        from opaihub.editions import require_feature

        gate = require_feature(root, "savings_export")
        if not gate["available"]:
            print_json({"status": "upgrade_required", **gate})
            return 3
        target = Path(export_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_savings_markdown(report), encoding="utf-8")
        print_json(
            {"status": "exported", "path": str(target), "edition": gate["edition"]}
        )
        return 0
    if getattr(args, "markdown", False):
        print(render_savings_markdown(report))
    else:
        print_json(report)
    return 0


def cmd_guard(args: argparse.Namespace) -> int:
    from opaihub.guarded import (
        build_evidence_packet,
        guard_action,
        load_templates,
        validate_all_templates,
    )

    root = _project(args.project)
    if args.guard_command == "list":
        data = load_templates(root)
        print_json(
            {
                "reference_implementation": data.get("reference_implementation"),
                "templates": [
                    {"id": t["id"], "title": t.get("title")}
                    for t in data.get("templates", [])
                ],
            }
        )
        return 0
    if args.guard_command == "check":
        result = validate_all_templates(root)
        print_json(result)
        return 0 if result["ok"] else 1
    if args.guard_command == "evidence":
        print_json(build_evidence_packet(root, args.workflow, write=not args.no_write))
        return 0
    if args.guard_command == "action":
        result = guard_action(
            root, args.action, template_id=args.template, confirmed=args.confirm
        )
        print_json(result)
        return 0 if result["decision"] != "deny" else 1
    return 0


def cmd_edition(args: argparse.Namespace) -> int:
    from opaihub.editions import edition_summary, set_edition

    root = _project(args.project)
    if args.edition_command == "show":
        print_json(edition_summary(root))
        return 0
    if args.edition_command == "set":
        result = set_edition(root, args.edition_name)
        print_json(result)
        return 0 if result.get("status") == "updated" else 2
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.models_command == "recommend":
        print_json(recommend_model(root, args.task))
    elif args.models_command == "eval":
        from opaihub.eval_harness import run_eval

        print_json(run_eval(root, write=not args.no_write))
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    from opaihub.policy import resolve_policy, set_profile

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
        if args.json:
            print_json(skill_items(root))
        else:
            for item in skill_items(root):
                default = " default" if item.get("enabled_by_default") else ""
                print(f"{item['id']}: {item['name']} [{item['cost_policy']}]{default}")
    elif args.skills_command == "doctor":
        print_json(skill_status(root))
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    result = activate_project(
        _project(args.project),
        install_global=not args.project_only,
        install_shell_aliases=args.shell_aliases,
        install_superpowers=args.install_superpowers,
        dry_run=args.dry_run,
        repair=args.repair,
    )
    if not args.quiet:
        print_json(result)
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.publish_command == "status":
        status = publish_status(root)
        if args.write:
            status["written"] = str(write_publish_status(root))
        print_json(status)
        return 0 if status["ready"] else 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opai",
        description="OPai 0.1.1 pre-alpha: local-first AI tools hub.",
    )
    parser.add_argument("--project", default=".", help="Project root")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("version")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("install")
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--with-tools",
        dest="install_tools",
        action="store_true",
        help="Install free local tool set",
    )
    p.add_argument(
        "--no-tools",
        dest="install_tools",
        action="store_false",
        help="Only create OPai local state",
    )
    p.add_argument(
        "--global-integrations",
        dest="global_integrations",
        action="store_true",
        default=True,
        help="Install global AI-client discovery files",
    )
    p.add_argument(
        "--project-only",
        dest="global_integrations",
        action="store_false",
        help="Do not write global AI-client discovery files",
    )
    p.add_argument(
        "--shell-aliases",
        action="store_true",
        help="Install PowerShell aliases for codex/claude/copilot",
    )
    p.add_argument(
        "--no-superpowers",
        action="store_true",
        help="Skip automatic Superpowers install during OPai install",
    )
    p.add_argument("--timeout", type=int, default=300)
    p.set_defaults(func=cmd_install, install_tools=False)

    p = sub.add_parser("statusline")
    p.add_argument("--width", type=int)
    p.add_argument("--no-color", action="store_true")
    p.set_defaults(func=cmd_statusline)

    p = sub.add_parser(
        "status",
        help="Show OPai activation, Superpowers, wrappers, and project state",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "slim",
        help="Write AI ignore files and report or clean generated context bloat",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--clean",
        action="store_true",
        help="Remove generated project caches after writing AI ignore files",
    )
    p.set_defaults(func=cmd_slim)

    p = sub.add_parser("welcome")
    p.add_argument("--compact", action="store_true")
    p.add_argument(
        "--image",
        default="auto",
        choices=["auto", "ansi", "ascii", "kitty", "iterm", "none"],
    )
    p.add_argument("--width", type=int)
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--animate", action="store_true")
    p.add_argument("--frames", type=int, default=8)
    p.add_argument("--delay", type=float, default=0.06)
    p.set_defaults(func=cmd_welcome)

    p = sub.add_parser("integrate")
    integrate_sub = p.add_subparsers(dest="integrate_command", required=True)
    i = integrate_sub.add_parser("install")
    i.add_argument("--project", default=None, help="Project root")
    i.add_argument(
        "--targets", default="all", help="Comma list: codex,claude,copilot,shell or all"
    )
    i.add_argument("--shell-aliases", action="store_true")
    i.add_argument("--home", help=argparse.SUPPRESS)
    i.set_defaults(func=cmd_integrate)
    i = integrate_sub.add_parser("status")
    i.add_argument("--home", help=argparse.SUPPRESS)
    i.set_defaults(func=cmd_integrate)

    p = sub.add_parser(
        "activate",
        help="Activate OPai, Superpowers, and AI-client instructions for this project",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--project-only",
        action="store_true",
        help="Do not update global AI-client integrations",
    )
    p.add_argument(
        "--shell-aliases",
        action="store_true",
        help="Install PowerShell aliases for AI CLIs",
    )
    p.add_argument(
        "--install-superpowers",
        action="store_true",
        help="Clone or update Superpowers before linking skills",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show activation writes without changing files",
    )
    p.add_argument(
        "--repair",
        action="store_true",
        help="Re-apply managed OPai project/global integration files",
    )
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("publish", help="Publish-readiness helpers")
    publish_sub = p.add_subparsers(dest="publish_command", required=True)
    pu = publish_sub.add_parser("status")
    pu.add_argument("--project", default=None, help="Project root")
    pu.add_argument(
        "--write", action="store_true", help="Write .opaihub/publish-status.json"
    )
    pu.set_defaults(func=cmd_publish)

    p = sub.add_parser("launch")
    p.add_argument("tool", choices=["codex", "claude", "copilot"])
    p.add_argument(
        "--image",
        default="auto",
        choices=["auto", "ansi", "ascii", "kitty", "iterm", "none"],
    )
    p.add_argument(
        "--welcome",
        action="store_true",
        help="Show the OPai mascot welcome screen before launching",
    )
    p.add_argument("--no-animate", action="store_true")
    p.add_argument("--frames", type=int, default=8)
    p.add_argument("--delay", type=float, default=0.06)
    p.add_argument("tool_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser(
        "route", help="Collect local evidence and choose the cheapest useful path"
    )
    p.add_argument("task")
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--activate",
        action="store_true",
        help="Also write OPai project activation files before routing",
    )
    p.add_argument(
        "--full-evidence",
        action="store_true",
        help="Include full local evidence instead of compact summaries",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Alias for --full-evidence",
    )
    p.add_argument(
        "--record",
        action="store_true",
        help="Record this routing decision to the local usage ledger (opt-in write)",
    )
    p.add_argument(
        "--store-summary",
        action="store_true",
        help="Store a redacted task summary in the ledger (off by default for privacy)",
    )
    p.set_defaults(func=cmd_route)

    p = sub.add_parser(
        "savings",
        help="Show the estimated AI spend OPai saved on this project (cost firewall)",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--markdown", action="store_true", help="Render the report as markdown"
    )
    p.add_argument(
        "--export",
        metavar="PATH",
        help="Write a shareable savings report (Pro edition feature)",
    )
    p.set_defaults(func=cmd_savings)

    p = sub.add_parser(
        "guard",
        help="Guarded-workflow contract: templates, validation, evidence, gates",
    )
    guard_sub = p.add_subparsers(dest="guard_command", required=True)
    gl = guard_sub.add_parser("list", help="List guarded-workflow templates")
    gl.add_argument("--project", default=None, help="Project root")
    gl.set_defaults(func=cmd_guard)
    gc = guard_sub.add_parser("check", help="Validate templates against the contract")
    gc.add_argument("--project", default=None, help="Project root")
    gc.set_defaults(func=cmd_guard)
    ge = guard_sub.add_parser("evidence", help="Generate a guarded evidence packet")
    ge.add_argument("workflow")
    ge.add_argument("--project", default=None, help="Project root")
    ge.add_argument("--no-write", action="store_true")
    ge.set_defaults(func=cmd_guard)
    ga = guard_sub.add_parser("action", help="Fail-closed gate for a risky action")
    ga.add_argument("action")
    ga.add_argument("--template", default=None)
    ga.add_argument("--confirm", action="store_true")
    ga.add_argument("--project", default=None, help="Project root")
    ga.set_defaults(func=cmd_guard)

    p = sub.add_parser(
        "edition",
        help="Show or set the OPai open-core edition (Free/Pro/Team/Enterprise)",
    )
    edition_sub = p.add_subparsers(dest="edition_command", required=True)
    ed = edition_sub.add_parser("show")
    ed.add_argument("--project", default=None, help="Project root")
    ed.set_defaults(func=cmd_edition)
    ed = edition_sub.add_parser("set")
    ed.add_argument("edition_name", choices=["free", "pro", "team", "enterprise"])
    ed.add_argument("--project", default=None, help="Project root")
    ed.set_defaults(func=cmd_edition)

    p = sub.add_parser("models", help="Model recommendation and routing helpers")
    models_sub = p.add_subparsers(dest="models_command", required=True)
    mo = models_sub.add_parser("recommend")
    mo.add_argument("task")
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser(
        "eval", help="Score routing on offline fixtures (local redacted scorecard)"
    )
    mo.add_argument(
        "--no-write",
        action="store_true",
        help="Print the scorecard without writing .opaihub/eval/scorecard.json",
    )
    mo.set_defaults(func=cmd_models)

    p = sub.add_parser(
        "policy", help="Show or set the cost/safety policy profile for this project"
    )
    policy_sub = p.add_subparsers(dest="policy_command", required=True)
    po = policy_sub.add_parser("show")
    po.add_argument("--project", default=None, help="Project root")
    po.set_defaults(func=cmd_policy)
    po = policy_sub.add_parser("set")
    po.add_argument(
        "profile",
        choices=["solo-cheap", "solo-balanced", "team-safe", "enterprise-strict"],
    )
    po.add_argument("--project", default=None, help="Project root")
    po.set_defaults(func=cmd_policy)

    p = sub.add_parser("skills", help="OPai skill registry helpers")
    skills_sub = p.add_subparsers(dest="skills_command", required=True)
    sk = skills_sub.add_parser("list")
    sk.add_argument("--json", action="store_true")
    sk.set_defaults(func=cmd_skills)
    sk = skills_sub.add_parser("doctor")
    sk.set_defaults(func=cmd_skills)

    p = sub.add_parser(
        "doctor",
        help="Branded readiness check: client integrations, stale paths, registries",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "update", help="Update the installed OPai source (~/.opai/source)"
    )
    p.set_defaults(func=cmd_update)

    p = sub.add_parser(
        "uninstall", help="Remove OPai-managed blocks and wrappers (dry-run by default)"
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--confirm", action="store_true", help="Apply the removal (default is dry-run)"
    )
    p.add_argument(
        "--keep-project-files",
        action="store_true",
        help="Do not strip OPai blocks from this project's instruction files",
    )
    p.set_defaults(func=cmd_uninstall)

    for name, hub_args in {
        "scan": ["scan"],
        "tools": ["list-tools"],
        "agents": ["list-agents"],
        "workflows": ["list-workflows"],
        "dashboard": ["dashboard"],
        "cost": ["cost", "status"],
    }.items():
        p = sub.add_parser(name)
        p.add_argument("--project", default=None, help="Project root")
        if name == "dashboard":
            p.add_argument("--html", action="store_true")
        p.set_defaults(func=cmd_delegate, hub_args=hub_args)

    p = sub.add_parser("hub", help="Pass through to op-hub")
    p.add_argument("hub_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_delegate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "dashboard" and getattr(args, "html", False):
        args.hub_args = ["dashboard", "--html"]
    return int(args.func(args))
