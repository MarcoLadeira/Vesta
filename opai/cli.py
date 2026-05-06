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
    activate_project(root, install_global=False)
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
    decision = route_task(root, args.task, include_evidence=include_evidence)
    print_json(decision if include_evidence else compact_decision(decision))
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.models_command == "recommend":
        print_json(recommend_model(root, args.task))
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
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("models", help="Model recommendation and routing helpers")
    models_sub = p.add_subparsers(dest="models_command", required=True)
    mo = models_sub.add_parser("recommend")
    mo.add_argument("task")
    mo.set_defaults(func=cmd_models)

    p = sub.add_parser("skills", help="OPai skill registry helpers")
    skills_sub = p.add_subparsers(dest="skills_command", required=True)
    sk = skills_sub.add_parser("list")
    sk.add_argument("--json", action="store_true")
    sk.set_defaults(func=cmd_skills)
    sk = skills_sub.add_parser("doctor")
    sk.set_defaults(func=cmd_skills)

    for name, hub_args in {
        "scan": ["scan"],
        "doctor": ["doctor"],
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
