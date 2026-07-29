from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
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
from opaihub.proc import AGENT_SESSION_ENV
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
    if value:
        return Path(value).expanduser().resolve()
    return discover_project_root(Path("."))


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
    print(
        render_statusline(
            width=args.width,
            color=not args.no_color,
            project_root=_project(args.project),
        )
    )
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
    if getattr(args, "human", False):
        from opai.cockpit import build_cockpit, render_cockpit

        print(render_cockpit(build_cockpit(_project(args.project))), end="")
        return 0
    print_json(project_status(_project(args.project)))
    return 0


def cmd_repo(args: argparse.Namespace) -> int:
    """Inspect canonical repository safety without exposing destructive cleanup."""

    from opaihub.repo_context import repository_safety_surface

    root = _project(args.project)
    context, safety, leases = repository_safety_surface(root)
    command = str(getattr(args, "repo_command", "inspect") or "inspect")
    payload: dict[str, Any] = {
        "project": str(root),
        "repo_context": context.to_dict(),
        "repository_safety": safety,
        "worktree_leases": leases,
    }
    if command == "worktrees":
        recovery: list[dict[str, Any]] = []
        if bool(getattr(args, "recover", False)) and context.is_git:
            try:
                from opaihub.repository_safety import build_repository_safety_receipt
                from opaihub.worktree_leases import WorktreeManager

                recovered = WorktreeManager(context.path).recover()
                recovery = [item.to_dict() for item in recovered]
                leases = [item.lease.to_dict() for item in recovered]
                payload["worktree_leases"] = leases
                payload["repository_safety"]["receipt"] = (
                    build_repository_safety_receipt(
                        safety,
                        safety.get("assessment") if isinstance(safety, dict) else {},
                        leases,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - never turn recovery into cleanup
                payload["recovery_error"] = str(exc)[:240]
        payload["leases"] = payload["worktree_leases"]
        payload["recovery"] = recovery
    if bool(getattr(args, "json", False)):
        print_json(payload)
    else:
        receipt = safety.get("receipt") or {}
        print(f"Repository: {context.path}")
        print(f"Safety: {safety.get('status')} ({receipt.get('repository_id', '')[:12]})")
        assessment = safety.get("assessment") or {}
        print(f"Dirty assessment: {assessment.get('outcome') or 'unavailable'}")
        if command == "worktrees":
            print(f"Worktree leases: {len(payload['leases'])}")
            if recovery:
                print("Recovery only reconciled state; no worktree was removed.")
    return 0


def cmd_cockpit(args: argparse.Namespace) -> int:
    from opai.cockpit import build_cockpit, render_cockpit

    payload = build_cockpit(_project(args.project))
    if args.json:
        print_json(payload)
    else:
        print(render_cockpit(payload), end="")
    return 0


def cmd_autonomy(args: argparse.Namespace) -> int:
    """Show or change the Full Auto pin so the CLI matches every surface (#137)."""
    from opaihub.autonomy import resolve_startup_mode
    from opaihub.gui_preferences import (
        load_gui_preferences,
        pin_full_auto,
        unpin_full_auto,
    )

    root = _project(args.project)
    command = getattr(args, "autonomy_command", "status") or "status"
    if command == "pin":
        prefs = pin_full_auto(root)
    elif command == "unpin":
        prefs = unpin_full_auto(root)
    else:
        prefs = load_gui_preferences(root)
    print_json(resolve_startup_mode(prefs).to_dict())
    return 0


def cmd_github(args: argparse.Namespace) -> int:
    """Connect a GitHub account so runs can commit, push, and open PRs."""
    from opaihub.github_connector import (
        connect_github,
        disconnect_github,
        github_status,
        set_public_read_allowed,
        set_push_allowed,
    )

    command = getattr(args, "github_command", "status") or "status"
    if command == "connect":
        token = str(getattr(args, "token", "") or "").strip()
        if not token:
            import os

            token = str(
                os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
            ).strip()
        if not token:
            print_json(
                {
                    "connected": False,
                    "error": (
                        "No token given. Pass --token <PAT> or set GITHUB_TOKEN "
                        "before running connect."
                    ),
                }
            )
            return 1
        result = connect_github(token)
        print_json(result)
        return 0 if result.get("connected") else 1
    if command == "disconnect":
        print_json(disconnect_github())
        return 0
    if command == "allow-push":
        enabled = str(getattr(args, "state", "") or "").lower() == "on"
        result = set_push_allowed(enabled)
        if not enabled:
            result["note"] = "Pushes and PR creation are disabled again."
        elif result.get("ready"):
            result["note"] = (
                "Runs may now push branches and open PRs on your GitHub repos."
            )
        else:
            # Consent is on but the run still can't push — say exactly why so the
            # user isn't told to re-run the command they just ran.
            result["note"] = (
                "Consent enabled, but pushes/PRs are NOT yet possible: "
                + str(result.get("next_step") or "")
            )
        print_json(result)
        return 0
    if command == "allow-public-read":
        enabled = str(getattr(args, "state", "") or "").lower() == "on"
        print_json(set_public_read_allowed(enabled))
        return 0
    status = github_status()
    print_json(status)
    return 0 if status.get("connected") or status.get("allow_public_read") else 1


def cmd_gui(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if getattr(args, "artifact_smoke", False):
        result_path = str(getattr(args, "result", "") or "").strip()
        if not result_path:
            print_json(
                {
                    "ok": False,
                    "status": "artifact_smoke_invalid",
                    "error": "--artifact-smoke requires --result PATH",
                }
            )
            return 2
        from opai.gui_web import run_artifact_smoke

        try:
            result = run_artifact_smoke(
                root,
                Path(result_path),
                timeout_seconds=int(getattr(args, "smoke_timeout", 30)),
            )
        except Exception as exc:  # noqa: BLE001 - artifact smoke must surface a typed failure
            result = {"ok": False, "status": "artifact_smoke_failed", "error": str(exc)}
        print_json(result)
        return 0 if result.get("ok") else 1

    from opai.gui_desktop import INSTALL_HINT, launch, render_screenshot, run_once

    if args.once:
        summary = run_once(root)
        print_json(summary)
        return 0 if summary.get("ok") else 1
    if args.screenshot:
        try:
            print_json(
                render_screenshot(
                    root,
                    Path(args.screenshot),
                    width=int(args.width),
                    height=int(args.height),
                )
            )
            return 0
        except Exception as exc:  # noqa: BLE001 - dependency/display failures degrade
            print_json(
                {
                    "status": "gui_unavailable",
                    "error": str(exc),
                    "hint": INSTALL_HINT,
                }
            )
            return 1
    task = getattr(args, "task", None)
    # Default to the web-rendered UI (Chromium via QtWebEngine) for a modern,
    # crisp surface; fall back to the classic Qt window if it's unavailable or
    # the user asked for --classic.
    if not getattr(args, "classic", False):
        try:
            from opai.gui_web import launch as launch_web
            from opai.gui_web import web_available

            if web_available():
                return int(launch_web(root, task=task))
        except Exception as exc:  # noqa: BLE001 - fall back to the Qt window
            print_json({"status": "web_gui_fallback", "error": str(exc)})
    try:
        return int(launch(root, task=task))
    except Exception as exc:  # noqa: BLE001 - dependency/display failures degrade
        print_json(
            {
                "status": "gui_unavailable",
                "error": str(exc),
                "hint": INSTALL_HINT,
            }
        )
        return 1


def gui_main() -> int:
    """Windowed entry point for the ``opai-gui`` launcher (#148).

    Installed via ``[project.gui_scripts]``, so on Windows pip generates a GUI
    executable (pythonw-backed) that opens the desktop app with **no attached
    console window** — suitable for a Start-menu/taskbar shortcut. Any arguments
    are forwarded to the ``gui`` subcommand, so ``opai-gui --project X`` and
    ``opai-gui "fix the bug"`` behave exactly like ``opai gui ...``.
    """
    return main(["gui", *sys.argv[1:]])


def cmd_new(args: argparse.Namespace) -> int:
    """Scaffold a runnable app skeleton from a description — zero tokens (#276).

    The free-boilerplate entry point to OPai Build: get a runnable app, then
    build features with cheap targeted `opai ask` prompts.
    """
    from opaihub.app_scaffold import scaffold_app

    dest = Path(args.into).expanduser().resolve() if args.into else Path.cwd()
    try:
        result = scaffold_app(
            dest,
            args.description,
            name=args.name,
            kind=args.type,
            force=args.force,
        )
    except (ValueError, FileExistsError) as exc:
        if args.json:
            print_json({"ok": False, "error": str(exc)})
        else:
            print(f"✗ {exc}")
        return 2

    if args.json:
        print_json({"ok": True, **result.to_dict()})
        return 0
    avoided = result.to_dict()["boilerplate_tokens_avoided"]
    print(f"✓ Scaffolded {result.kind} app '{result.name}' at {result.root}")
    print(
        f"  {len(result.files)} files · ~{avoided} boilerplate tokens written for free"
    )
    print("  Next:")
    for step in result.next_steps:
        print(f"    {step}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """One turn of the OPai Build customization loop (#276).

    Sends only the relevant app files, receives complete updated files, and
    applies them deterministically with backups — the model never gets tool
    access. Runs through the normal pipeline, so routing, the cost firewall,
    and the savings receipt all apply.
    """
    refusal = _refuse_if_nested_agent_session()
    if refusal is not None:
        return refusal
    from opaihub.build_loop import run_build_request

    app_root = Path(args.app).expanduser().resolve() if args.app else Path.cwd()
    report = run_build_request(
        app_root,
        args.request,
        model=args.model,
        dry_run=args.dry_run,
        strict=args.strict,
    )
    if args.json:
        print_json(report)
        return 0 if report.get("ok") else 2

    status = str(report.get("status") or "error")
    if status == "dry_run":
        context = report["context"]
        print(
            f"→ would send {len(context['files'])} file(s) "
            f"({context['chars_selected']:,} of {context['chars_total']:,} chars"
            f" · {context['saved_pct']}% trimmed): {', '.join(context['files'])}"
        )
        return 0
    if status == "rolled_back":
        verify = report.get("verify") or {}
        failures = [c for c in verify.get("checks") or [] if not c.get("ok")]
        print("✗ verification failed — all files changed by this build were restored:")
        for check in failures[:6]:
            print(f"    {check['path']} · {check['check']}: {check['detail']}")
        print(f"  (the attempted files remain in {report.get('backup_dir')})")
        return 2
    if status in {"partial_rollback", "rollback_failed"}:
        print("✗ verification failed — automatic rollback was incomplete")
        for path in report.get("remaining_changed_files") or []:
            print(f"    still changed: {path}")
        print(f"  review the remaining changes and backup: {report.get('backup_dir')}")
        return 2
    if not report.get("ok"):
        detail = report.get("error") or report.get("answer") or status
        print(f"✗ {status}: {str(detail)[:400]}")
        return 2
    for item in report.get("applied") or []:
        print(
            f"✓ {item['action']} {item['path']} (+{item['added']} −{item['removed']})"
        )
    for item in report.get("rejected") or []:
        print(f"! rejected {item['path']}: {item['reason']}")
    verify = report.get("verify") or {}
    if verify:
        if verify.get("ok"):
            print(f"  verified: {verify['passed']} structural check(s) passed")
        else:
            print(f"  ⚠ verification: {verify['failed']} check(s) FAILED:")
            for check in verify.get("checks") or []:
                if not check.get("ok"):
                    print(f"    {check['path']} · {check['check']}: {check['detail']}")
            print("    (backups kept — rerun with --strict to auto-rollback)")
    context = report.get("context") or {}
    if context:
        print(
            f"  context: {len(context.get('files') or [])} file(s), "
            f"{context.get('saved_pct', 0)}% of the app left out of the prompt"
        )
    if report.get("backup_dir"):
        print(f"  backups: {report['backup_dir']}")
    receipt = report.get("receipt") or {}
    if receipt.get("estimated_actual_usd"):
        print(f"  cost: ${float(receipt['estimated_actual_usd']):.4f}")
    so_far = report.get("receipt_so_far") or {}
    if so_far.get("ok"):
        print(
            f"  app so far: {so_far['builds']} build(s) · "
            f"${so_far['spend_usd_actual']:.4f} measured spend · "
            f"~{so_far['tokens_never_sent']:,} tokens never sent"
        )
    print(f"  preview: {report.get('preview_cmd')}")
    return 0


def cmd_ux_metrics(args: argparse.Namespace) -> int:
    """Local-only product-health metrics (#395): verdict distribution and
    cancel/timeout rates from this project's ledger. No telemetry leaves the
    machine — this reads the same local events OPai already recorded."""
    from opaihub.ux_metrics import render_ux_metrics_markdown, summarize_ux_metrics

    root = _project(args.project)
    metrics = summarize_ux_metrics(root)
    if getattr(args, "markdown", False):
        print(render_ux_metrics_markdown(metrics))
    else:
        print_json(metrics)
    return 0


def cmd_app_receipt(args: argparse.Namespace) -> int:
    """The aggregate cost story of one OPai Build app (#276): what was spent,
    and — the number no one else shows — what was never spent."""
    from opaihub.build_loop import app_receipt

    app_root = Path(args.app).expanduser().resolve() if args.app else Path.cwd()
    receipt = app_receipt(app_root)
    if args.json:
        print_json(receipt)
        return 0 if receipt.get("ok") else 2
    if not receipt.get("ok"):
        print(f"✗ {receipt.get('error')}")
        return 2
    print(f"OPai Build receipt — {receipt['app']} ({receipt['kind']})")
    print(
        f"  files: {receipt['files']} · builds: {receipt['builds']} "
        f"({receipt['applied_builds']} applied, +{receipt['lines_added']} "
        f"−{receipt['lines_removed']} lines)"
    )
    print(
        f"  boilerplate written free: ~{receipt['boilerplate_tokens_avoided']:,} tokens"
    )
    print(
        f"  context never sent:       ~{receipt['context_tokens_avoided']:,} tokens (slicing)"
    )
    print(f"  tokens never spent:       ~{receipt['tokens_never_sent']:,}")
    print(f"  measured spend:  ${receipt['spend_usd_actual']:.4f}")
    if receipt["spend_usd_estimated"]:
        print(
            f"  estimated spend: ${receipt['spend_usd_estimated']:.4f} (model math, not billed)"
        )
    if receipt["savings_usd_estimated"]:
        print(f"  estimated saved: ${receipt['savings_usd_estimated']:.4f}")
    return 0


def _doctor_model_check(root: Path, validate: Any) -> dict[str, Any]:
    """Validate the project's default account model against the registry (#170).

    A stored default the provider no longer lists is flagged with its safe
    fallback here, instead of surfacing later as a misleading auth error.
    """
    try:
        from opaihub.gui_preferences import load_gui_preferences

        selected = str(load_gui_preferences(root).get("default_model") or "auto")
    except Exception:  # noqa: BLE001 - doctor must never crash on a bad prefs file
        return {"checked": False}
    parts = selected.split(":")
    if len(parts) >= 3 and parts[0] == "account":
        result = validate(parts[1], ":".join(parts[2:]))
        return {"checked": True, "model": selected, **result}
    # auto / free / local models are not account-registry ids — nothing to flag.
    return {"checked": True, "model": selected, "valid": True, "reason": ""}


def cmd_doctor(args: argparse.Namespace) -> int:
    from opai.model_registry import catalog as model_catalog
    from opai.model_registry import validate as validate_model
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
        # One true model catalog (#170) so the drift between accounts.py and
        # provider_contract can never resurface silently. If a project pins a
        # default model the provider no longer lists, flag it with the safe
        # fallback instead of failing later as a fake auth error.
        "model_registry": model_catalog(),
        "model_check": _doctor_model_check(root, validate_model),
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
    """Check for, or apply, an OPai update — the desktop Settings button's CLI twin.

    Operates on OPai's own running source checkout, never the project passed
    to other commands with ``--project``. Bare ``opai update`` checks and
    reports; ``opai update --apply`` fetches, fast-forwards, and reinstalls —
    refusing outright on any uncommitted local change.
    """
    from opai.updater import apply_update, check_for_update, install_root

    root = install_root()
    if getattr(args, "apply", False):
        result = apply_update(root)
        print_json(result)
        return 0 if result.get("ok") else 1

    result = check_for_update(root, force=not getattr(args, "cached", False))
    print_json(result)
    if not result.get("checked"):
        return 0
    return 0 if result.get("up_to_date") else 3


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


# --------------------------------------------------------------------------- #
# Recursion guard (F12): provider CLIs spawned by OPai carry OPAI_AGENT_SESSION
# in their environment (see opaihub.proc). When an agent follows an
# instruction-file recipe like "run `opai route ...`", the nested OPai process
# must refuse instead of recursing into another agent run. Agentic subcommands
# check this guard; pure utility/hook subcommands must keep working inside an
# agent session.
# --------------------------------------------------------------------------- #
_NESTED_SESSION_REFUSAL = (
    "OPai is already running inside an OPai agent session; "
    "recursive self-invocation is disabled."
)


def _nested_agent_session_active() -> bool:
    return bool(os.environ.get(AGENT_SESSION_ENV))


def _refuse_if_nested_agent_session() -> int | None:
    """Exit code when invoked from inside an OPai agent session, else None."""
    if _nested_agent_session_active():
        print(_NESTED_SESSION_REFUSAL, file=sys.stderr)
        return 2
    return None


# --------------------------------------------------------------------------- #
# Claude Code PreToolUse hook gate (F23). AccountRunner wires this subcommand
# into `claude` Full Auto runs via a generated --settings file (see
# opaihub.accounts.build_claude_hook_settings); the claude CLI then executes
# it for every Bash tool call. It must be fast, deterministic, and must never
# be blocked by the recursion guard above — it runs *inside* agent sessions.
# --------------------------------------------------------------------------- #
_HOOK_SHELL_TOOLS = frozenset({"bash", "shell", "sh", "powershell", "pwsh", "cmd"})

_HOOK_BLOCK_REASON = (
    "OPai safety gate: this command is classified as destructive or "
    "confirmation-only ({detail}). It is blocked in autonomous runs and OPai "
    "will not run it for you. To proceed, run it yourself in a terminal. Do not "
    "retry it or work around the block; continue with safe, read-only steps only."
)

# git push is a common, legitimate next step, so its block must point at the
# real control instead of implying a per-command approval dialog that does not
# exist (Bug 2): pushes are enabled once, in Settings, and then OPai performs
# them through its own consent-aware GitHub tool — never as a raw shell push.
# Round 2: only say this when the control is actually still off. When consent is
# already granted the sentence was actively harmful — it sent users hunting for
# an "Enable pushes & PRs" button that, once enabled, reads "Disable pushes &
# PRs" — so this text is reserved for the not-yet-enabled case; a consented push
# goes to the per-push approval card instead (see ``opaihub.command_consent``).
_HOOK_BLOCK_REASON_PUSH = (
    "OPai safety gate: pushing is not enabled yet, so OPai will not run this "
    "`git push` ({detail}). Enable it once in Settings -> Providers & "
    'Connections, in the "GitHub · pushes & pull requests" card: connect a '
    'GitHub token, then click "Enable pushes & PRs". After that OPai can push '
    "this branch itself. Or push yourself in a terminal. Do not retry this push "
    "until it is enabled; continue with safe, read-only steps only."
)

# A push OPai still refuses even with consent: force/mirror/delete forms rewrite
# or destroy remote history, which consent to "push branches and open PRs" does
# not cover. Say exactly that instead of pointing at a toggle that is already on.
_HOOK_BLOCK_REASON_FORCE_PUSH = (
    "OPai safety gate: pushes are enabled, but this is a force/delete/mirror "
    "push ({detail}), which rewrites or removes remote history. OPai never runs "
    "those autonomously regardless of consent. Run it yourself in a terminal if "
    "you intend it, or push without the force/delete flags."
)

# A push that IS enabled and IS a safe shape, refused only because the user has
# not approved this particular push yet. Unlike every other block reason this one
# is not a dead end: the hook records the request, the pipeline turns it into the
# GUI's approval card, and "Approve once" re-runs the turn with the grant armed
# (Round 5 finding 1). Telling the model to stop and report is what lets that
# card be the next thing the user sees.
_HOOK_BLOCK_REASON_PUSH_APPROVAL = (
    "OPai safety gate: pushing is enabled, but each push needs the user's "
    "one-time approval ({detail}). OPai has recorded this exact command and will "
    "ask them to approve it as soon as this turn ends. Stop here and report that "
    "the push is awaiting their approval. Do NOT retry the push, do not try "
    "another way to push, and do not claim the branch was pushed."
)

_PUSH_COMMAND = re.compile(r"\bgit\s+push\b", re.IGNORECASE)
_DIRECT_PR_COMMENT_COMMAND = re.compile(
    r"^\s*gh(?:\.exe)?\s+pr\s+comment(?:\s|$)", re.IGNORECASE
)
_SHELL_OPERATORS = re.compile(r"[|&;<>`]|\$\(|\$\{")

_HOOK_BLOCK_REASON_COMMAND_APPROVAL = (
    "OPai safety gate: this outward-facing command needs the user's one-time "
    "approval ({detail}). OPai has recorded this exact command and will ask "
    "them to approve it as soon as this turn ends. Stop here and report that "
    "the command is awaiting approval. Do NOT retry it, do not try another way "
    "to perform the action, and do not claim it completed."
)


def _is_plain_push(command: str) -> bool:
    """True for a lone, non-force ``git push`` to a named remote, nothing else."""

    from opaihub.command_consent import is_plain_push

    return is_plain_push(command)


def _is_direct_pr_comment(command: str) -> bool:
    """True for a direct, unchained ``gh pr comment`` shell invocation.

    The provider hook receives the entire shell string, including quoted comment
    bodies. Looking for ``git push`` anywhere in that string therefore treats
    ordinary prose as a push. Keep this deliberately narrow: only a command
    whose executable is ``gh pr comment`` and which carries no shell operators
    may enter the one-shot approval channel.
    """

    text = str(command or "")
    return bool(_DIRECT_PR_COMMENT_COMMAND.match(text)) and not bool(
        _SHELL_OPERATORS.search(text)
    )


def _push_consent_state() -> tuple[bool, str]:
    """Whether the user has already granted OPai push consent, and why not.

    Consent is the same persisted pair the GUI toggle and OPai's own
    ``git_push`` tool read: ``opai github allow-push on`` plus a connected
    token. Fails closed — any lookup problem is treated as "not consented" so
    the gate can only ever become stricter on error.
    """
    try:
        from opaihub.github_connector import push_allowed, stored_github_token

        if not push_allowed():
            return False, "push consent is off"
        if not stored_github_token()[0]:
            return False, "no GitHub token is connected"
        return True, ""
    except Exception:  # noqa: BLE001 - consent lookup must fail closed
        return False, "push consent could not be verified"


def _hook_block_reason(command: str, detail: str) -> str:
    """The honest block explanation for a denied autonomous command (Bug 2).

    For a push, the explanation depends on *why* it is still blocked: consent
    not granted yet points at the real Settings control; a force/delete push
    says plainly that no toggle unlocks it. Everything else says the user must
    run it themselves — never that a per-command UI dialog will appear.
    """
    text = str(command or "")
    if _PUSH_COMMAND.search(text):
        consented, _ = _push_consent_state()
        template = (
            _HOOK_BLOCK_REASON_FORCE_PUSH if consented else _HOOK_BLOCK_REASON_PUSH
        )
        return template.format(detail=detail)
    return _HOOK_BLOCK_REASON.format(detail=detail)


def _hook_allow() -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        },
        # Legacy shape for older Claude Code versions; ignored by current ones.
        "decision": "approve",
    }


def _hook_deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "decision": "block",
        "reason": reason,
    }


def claude_pre_tool_decision(
    payload: dict[str, Any], project_root: Path | None = None
) -> dict[str, Any]:
    """Map a Claude Code PreToolUse payload to an OPai gate decision.

    Shell-tool commands are re-classified through the same policy the GUI
    uses: ``sandbox.classify_command`` (deny/confirm rules) plus
    ``safety_gates.is_destructive_command``. Anything not provably safe is
    denied with an explanation; non-shell tools pass through untouched.

    One exception, and only one: a lone, non-force ``git push`` runs when the
    user has already granted push consent in Settings AND approved this specific
    push for this turn. ``git push`` sits in the *confirm* class, and this hook
    has no interactive channel of its own, so a confirm verdict used to be a hard
    deny — push could never complete through the GUI even with consent granted
    (Round 2 headline) — and then, once auto-allowed, it completed with no
    confirmation at all despite Full Auto promising one (Round 5 finding 1).
    Neither is honest. ``opaihub.command_consent`` supplies the missing channel:
    an unapproved push is refused *and recorded*, so the pipeline can raise the
    GUI's approval card, and "Approve once" arms the one-shot grant consumed
    here. Force/delete/mirror pushes and anything chained onto a push stay
    denied outright — no approval unlocks those.
    """
    from opaihub import command_consent
    from opaihub.safety_gates import is_destructive_command
    from opaihub.sandbox import classify_command

    tool_name = str(payload.get("tool_name") or "").strip().lower()
    if tool_name not in _HOOK_SHELL_TOOLS:
        return _hook_allow()
    tool_input = payload.get("tool_input")
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command") or "").strip()
    if not command:
        # Fail closed: a shell call we cannot inspect is not provably safe.
        return _hook_deny(
            _HOOK_BLOCK_REASON.format(detail="no inspectable command in payload")
        )
    # A PR comment is outward-facing, but an explicit one-shot approval is the
    # right boundary — a terminal destructive block leaves a requested comment
    # impossible to complete through the GUI. Check the actual invoked command
    # before scanning broader policy text so a quoted ``git push`` in the
    # comment body cannot be mistaken for a push operation.
    if _is_direct_pr_comment(command):
        if command_consent.consume_grant(command):
            return _hook_allow()
        reason = _HOOK_BLOCK_REASON_COMMAND_APPROVAL.format(
            detail="posting a comment changes the pull request conversation"
        )
        command_consent.record_pending(
            command, "Posting this comment changes the pull request conversation."
        )
        return _hook_deny(reason)
    if _is_plain_push(command) and _push_consent_state()[0]:
        if command_consent.consume_grant(command):
            return _hook_allow()
        reason = _HOOK_BLOCK_REASON_PUSH_APPROVAL.format(
            detail="no approval has been given for this push yet"
        )
        command_consent.record_pending(
            command, "Pushing sends this branch to the remote."
        )
        return _hook_deny(reason)
    verdict = classify_command(command, project_root)
    if (
        verdict.get("denied")
        or verdict.get("requires_confirmation")
        or is_destructive_command([command])
    ):
        detail = str(verdict.get("reason") or "destructive command policy")
        return _hook_deny(_hook_block_reason(command, detail))
    return _hook_allow()


def cmd_hooks(args: argparse.Namespace) -> int:
    """Provider CLI hook entrypoints; currently Claude Code PreToolUse only."""
    if getattr(args, "hooks_command", None) != "claude-pre-tool":
        print_json({"status": "unknown_hook", "hooks": ["claude-pre-tool"]})
        return 2
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
    except (json.JSONDecodeError, ValueError):
        # Fail closed: an unparseable payload cannot be proven safe.
        print(
            json.dumps(
                _hook_deny(_HOOK_BLOCK_REASON.format(detail="unparseable hook payload"))
            )
        )
        return 0
    # Single-line JSON on stdout; exit code stays 0 because the decision is
    # carried in the payload, not the process status.
    print(json.dumps(claude_pre_tool_decision(payload)))
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    refusal = _refuse_if_nested_agent_session()
    if refusal is not None:
        return refusal
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
        from opaihub.runs import record_run

        record_run(root, full_decision)
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


def cmd_why(args: argparse.Namespace) -> int:
    from opaihub.runs import explain_route, render_why_markdown

    root = _project(args.project)
    explanation = explain_route(root, args.task)
    if getattr(args, "markdown", False):
        print(render_why_markdown(explanation))
    else:
        print_json(explanation)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    refusal = _refuse_if_nested_agent_session()
    if refusal is not None:
        return refusal
    root = _project(args.project)
    # --model routes through the same pipeline as the GUI (accounts/auto/local)
    # with live activity, streaming, Ctrl+C cancel, and a cost/savings footer.
    # Without it, the classic free local-only path is unchanged.
    if getattr(args, "model", None):
        from opai.cli_stream import stream_ask

        return stream_ask(
            root,
            args.task,
            model=args.model,
            mode=getattr(args, "mode", None) or "ask",
            json_out=getattr(args, "json", False),
        )
    from opaihub.ask import render_ask, run_ask

    result = run_ask(
        root,
        args.task,
        allow_cloud=getattr(args, "allow_cloud", False),
        record=not getattr(args, "no_record", False),
    )
    if getattr(args, "json", False):
        print_json(result)
    else:
        print(render_ask(result))
    # #295 Workstream H: the exit code names which ending this was, from the one
    # canonical mapping, so `opai ask` and the streaming path agree and a script
    # can tell a timeout from a refusal.
    from opaihub.run_state import exit_code_for

    answered = result["status"] in {"answered_locally", "cache_hit"}
    return exit_code_for("completed" if answered else "failed")


def cmd_context(args: argparse.Namespace) -> int:
    from opaihub.context_pack import build_context_pack

    root = _project(args.project)
    if args.context_command == "pack":
        pack = build_context_pack(root, changed_only=not args.all, write=args.write)
        print_json(pack)
        return 0
    if args.context_command == "profile":
        from opaihub.context_engine import profile_context, render_profile_markdown

        profile = profile_context(root)
        if getattr(args, "markdown", False):
            print(render_profile_markdown(profile))
        else:
            print_json(profile)
        return 0
    if args.context_command == "ignores":
        from opaihub.context_engine import generate_client_ignores

        clients = (
            [c.strip() for c in args.clients.split(",") if c.strip()]
            if getattr(args, "clients", None)
            else None
        )
        print_json(generate_client_ignores(root, clients))
        return 0
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    from opaihub.test_select import select_tests

    root = _project(args.project)
    selection = select_tests(root)
    if getattr(args, "run", False):
        if not selection["targeted_command"]:
            print_json({"status": "no_targeted_tests", **selection})
            return 0
        from opaihub.command_runner import run_policy_command

        result = run_policy_command(
            selection["targeted_command"], root, timeout=args.timeout
        )
        print_json(
            {
                "status": "ran" if result.executed else "blocked",
                "command": selection["targeted_command"],
                "returncode": result.returncode,
                "policy": result.policy["decision"],
                "output_tail": result.combined_output[-1200:],
            }
        )
        return 0 if result.returncode == 0 else 1
    print_json(selection)
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    from opaihub.share import build_savings_card, render_share_markdown

    root = _project(args.project)
    card = build_savings_card(root)
    if getattr(args, "write_badge", None):
        target = Path(args.write_badge)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(card["badge"]["svg"], encoding="utf-8")
        print_json(
            {"status": "wrote_badge", "path": str(target), "headline": card["headline"]}
        )
        return 0
    if getattr(args, "markdown", False):
        print(render_share_markdown(card))
    else:
        print_json(card)
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    from opaihub.metrics import build_local_metrics

    root = _project(args.project)
    metrics = build_local_metrics(root)
    # Merge in client readiness (kept in the opai package to avoid a cycle).
    status = project_status(root)
    clients = status["client_integrations"]["summary"]
    metrics["ai_client_readiness"] = {
        "active": len(clients["active"]),
        "broken": len(clients["broken"]),
        "missing": len(clients["missing"]),
    }
    metrics["activated"] = status["project"]["activated"]
    print_json(metrics)
    return 0


def cmd_quickstart(args: argparse.Namespace) -> int:
    """Guided 60-second first run: activate, sample route, savings, share."""
    from opaihub.router import route_task
    from opaihub.savings import build_savings_report
    from opaihub.share import build_savings_card

    root = _project(args.project)
    steps: list[dict[str, Any]] = []

    activation = activate_project(root, install_global=not args.project_only)
    steps.append({"step": "activate", "status": activation.get("status")})

    sample_task = args.task or "show git status and summarize the diff"
    decision = route_task(root, sample_task, persist_cache=True)
    from opaihub.ledger import record_route_decision
    from opaihub.router import route_context_sizes

    sizes = route_context_sizes(route_task(root, sample_task, include_evidence=True))
    record_route_decision(
        root,
        sample_task,
        model_tier=decision["model_tier"],
        workflow=decision["workflow"],
        full_context_chars=sizes["full_chars"],
        compact_context_chars=sizes["compact_chars"],
        cache_hit=decision.get("evidence_cache_hit", False),
    )
    steps.append({"step": "route", "task": sample_task, "tier": decision["model_tier"]})

    report = build_savings_report(root)
    card = build_savings_card(root)
    steps.append({"step": "savings", "headline": report["headline"]})

    print_json(
        {
            "report": "opai-quickstart",
            "project_root": str(root),
            "steps": steps,
            "savings_headline": report["headline"],
            "share_badge_markdown": card["badge"]["markdown"],
            "next_steps": [
                'Run more tasks with: opai route "<task>" --record',
                "See the full report: opai savings --markdown",
                "Share your savings: opai share --markdown",
                "Check readiness anytime: opai doctor",
            ],
        }
    )
    return 0


def cmd_savings(args: argparse.Namespace) -> int:
    from opaihub.savings import build_savings_report, render_savings_markdown

    root = _project(args.project)
    report = build_savings_report(root)
    export_path = getattr(args, "export", None)
    if export_path:
        target = Path(export_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_savings_markdown(report), encoding="utf-8")
        print_json({"status": "exported", "path": str(target), "edition": "free"})
        return 0
    if getattr(args, "rollups", False):
        from opaihub.ledger import rollup_ledger

        rollups = rollup_ledger(root)
        report["rollups"] = {
            "by_day": rollups["by_day"],
            "by_week": rollups["by_week"],
            "by_month": rollups["by_month"],
            "by_agent": rollups["by_agent"],
            "by_repo": rollups["by_repo"],
        }
    if getattr(args, "markdown", False):
        print(render_savings_markdown(report))
    else:
        print_json(report)
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    """Reproducible release-candidate preflight, dry-run, and rollback (#32).

    ``opai release preflight`` assembles every release check into one
    deterministic readiness verdict from a clean checkout and exits non-zero
    when anything blocks. ``opai release rollback`` prints (or, with --execute,
    performs) the steps to restore the previous tested artifact without touching
    user state.
    """
    from opaihub import release_preflight as rp

    root = _project(args.project)
    command = getattr(args, "release_command", None)

    if command == "preflight":
        ctx = rp.ReleaseContext(
            root=root,
            dry_run=not getattr(args, "execute", False),
            run_tests=getattr(args, "run_tests", False),
            artifacts_manifest=(
                Path(args.artifacts) if getattr(args, "artifacts", None) else None
            ),
        )
        readiness = rp.run_preflight(ctx)
        if getattr(args, "out", None):
            Path(args.out).write_text(
                json.dumps(rp.sanitized_evidence(readiness), indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        if getattr(args, "format", "markdown") == "json":
            print(rp.render_report_json(readiness))
        else:
            print(rp.render_report_markdown(readiness), end="")
        return 0 if readiness.ready else 1

    if command == "dry-run-proof":
        ctx = rp.ReleaseContext(root=root, dry_run=True)
        proof = rp.prove_dry_run_isolation(ctx)
        print_json(proof)
        return 0 if proof["isolated"] else 1

    if command == "rollback":
        try:
            manifest = json.loads(
                Path(args.previous_manifest).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            print_json({"status": "error", "message": str(exc)})
            return 2
        version_now, _ = rp.resolve_version(rp.ReleaseContext(root=root))
        plan = rp.rollback_plan(
            from_version=version_now or "current",
            to_version=str(args.to or manifest.get("version") or "previous"),
            previous_manifest=manifest,
        )
        if not getattr(args, "execute", False):
            print_json({"dry_run": True, "plan": plan})
            return 0
        if not args.release_root or not args.pointer:
            print_json(
                {
                    "status": "error",
                    "message": "--execute requires --release-root and --pointer",
                }
            )
            return 2
        try:
            result = rp.perform_rollback(
                release_root=Path(args.release_root),
                previous_manifest=Path(args.previous_manifest),
                pointer_file=Path(args.pointer),
                user_state_dirs=[Path(p) for p in (args.protect or [])],
            )
        except rp.ReleaseError as exc:
            print_json({"status": "error", "message": str(exc)})
            return 2
        print_json({"dry_run": False, "plan": plan, "result": result})
        return 0

    print_json({"status": "error", "message": "unknown release command"})
    return 2


def cmd_resume(args: argparse.Namespace) -> int:
    """Show the workspace's resumable session — CLI parity with the GUI (#313).

    Reads the same crash-safe resume offer the GUI boot builds (thread store +
    workflow state + checkpoint linkage), read-only: inspecting a pending
    session never mutates it.
    """
    from opai.gui_web import _resume_payload

    root = _project(args.project)
    payload = _resume_payload(root)
    if getattr(args, "json", False) or not getattr(args, "markdown", False):
        print_json(payload)
        return 0
    if not payload.get("available"):
        print("No resumable session in this workspace.")
        return 0
    thread = payload.get("thread") or {}
    workflow = payload.get("workflow") or {}
    checkpoint = payload.get("checkpoint") or {}
    messages = thread.get("messages") or []
    last_user = next(
        (m.get("text", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    lines = [
        "# Resumable session",
        "",
        f"- Messages: **{len(messages)}** · thread state: "
        f"{thread.get('state', 'unknown')}",
        f"- Last task: {last_user[:120] or '(none)'}",
        f"- Workflow: {workflow.get('phase', 'idle')} — {workflow.get('message', '')}",
    ]
    if checkpoint:
        lines.append(
            f"- Checkpoint: {checkpoint.get('id', '')} "
            f"({checkpoint.get('completion_state', 'unknown')}, "
            f"{len(checkpoint.get('changed_files') or [])} changed file(s))"
        )
    lines.append("")
    lines.append("Open this workspace in the OPai GUI to resume or start fresh.")
    print("\n".join(lines))
    return 0


def cmd_outcomes(args: argparse.Namespace) -> int:
    """Report task-outcome metrics (#288): cost per completed task and
    duplicate-call avoidance, reconciled to the authoritative ledger."""
    from opaihub.ledger import summarize_outcomes

    root = _project(args.project)
    summary = summarize_outcomes(root)
    if getattr(args, "json", False) or not getattr(args, "markdown", False):
        print_json(summary)
        return 0
    spend = summary["spend"]
    cpct = spend["cost_per_completed_task_usd"]
    cpct_label = f"${cpct:.6f}" if isinstance(cpct, (int, float)) else "unknown"
    avoided = summary["duplicate_calls_avoided"]
    lines = [
        "# OPai task outcomes",
        "",
        f"- Outcomes recorded: **{summary['outcome_count']}**",
        f"- Completed: **{summary['by_category']['completed']}** · "
        f"failed: {summary['by_category']['failed']} · "
        f"blocked: {summary['by_category']['blocked']} · "
        f"cancelled: {summary['by_category']['cancelled']}",
        f"- Authoritative spend: **${spend['authoritative_estimated_usd']:.6f}**",
        f"- Cost per completed task: **{cpct_label}**",
        f"- Duplicate model calls avoided: **{avoided['from_cache_lookups']}** "
        "(from cache evidence)",
        f"- Reconciles to ledger spend: "
        f"**{'yes' if summary['reconciles_to_ledger'] else 'NO'}**",
        "",
        f"_{summary['privacy']}_",
    ]
    print("\n".join(lines))
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    from opaihub.budget import budget_gate, budget_status, set_budget

    root = _project(args.project)
    if args.budget_command == "set":
        try:
            result = set_budget(
                root,
                daily_usd=args.daily,
                monthly_usd=args.monthly,
                per_task_usd=args.per_task,
            )
        except ValueError as exc:
            print_json({"status": "invalid", "error": str(exc)})
            return 2
        print_json(result)
        return 0
    if args.budget_command == "status":
        print_json(budget_status(root))
        return 0
    if args.budget_command == "panic":
        on = not args.off
        result = set_budget(root, panic=on)
        from opaihub.audit import POLICY_DENY, record_audit_event

        record_audit_event(root, POLICY_DENY if on else "panic_off", panic=on)
        print_json({"panic": on, **result})
        return 0
    if args.budget_command == "gate":
        from opaihub.cost_model import load_cost_model, tier_cost
        from opaihub.model_intelligence import recommend_model

        # Gate the escalation target (the model that *would* run this task if
        # escalated), since OPai's local router itself never picks a paid tier.
        recommendation = recommend_model(root, args.task)
        tier = str(recommendation.get("recommended_model_tier") or "L1").upper()
        provider = str(
            (recommendation.get("recommended_model") or {}).get("provider_type")
            or "local"
        )
        cost_model = load_cost_model(root)
        tokens = int(cost_model.get("default_task_tokens", 6000))
        cost = tier_cost(tier, tokens, cost_model)
        gate = budget_gate(
            root,
            next_cost_usd=cost,
            tier=tier,
            provider_type=provider,
            estimated_tokens=tokens,
        )
        gate["escalation_target"] = {
            "tier": tier,
            "provider_type": provider,
            "model_id": recommendation.get("recommended_model_id"),
        }
        print_json(gate)
        return gate["exit_code"]
    return 0


def cmd_proxy(args: argparse.Namespace) -> int:
    refusal = _refuse_if_nested_agent_session()
    if refusal is not None:
        return refusal
    from opaihub.proxy import proxy_run

    root = _project(args.project)
    result = proxy_run(
        root,
        args.task,
        agent=args.agent,
        model=getattr(args, "model", None),
        mode=getattr(args, "mode", None),
    )
    if getattr(args, "json", False):
        print_json(result)
    else:
        print(result.get("answer", "") or result.get("reason", ""))
    status = result.get("status", "")
    if status in {"answered_by_account", "fail_open"}:
        return 0
    if status == "blocked":
        return 2
    return 1


def cmd_agent_launch(args: argparse.Namespace) -> int:
    """Wrapper entrypoint: proxy canonical one-shot calls, otherwise stay silent."""
    from opaihub.agent_launch import PASSTHROUGH_EXIT, launch_agent

    raw_args = list(getattr(args, "agent_args", []) or [])
    if raw_args[:1] == ["--"]:
        raw_args = raw_args[1:]
    try:
        result = launch_agent(
            _project(args.project),
            args.agent,
            raw_args,
        )
    except Exception:  # noqa: BLE001 - shell wrapper must always fail open
        return PASSTHROUGH_EXIT
    if result.get("status") == "passthrough":
        return PASSTHROUGH_EXIT
    answer = result.get("answer") or result.get("reason") or ""
    if answer:
        print(answer)
    if result.get("status") in {"answered_by_account", "fail_open"}:
        return 0
    if result.get("status") == "blocked":
        return 2
    return 1


def cmd_receipt(args: argparse.Namespace) -> int:
    from opaihub.receipt import build_receipt, render_receipt_svg, verify_receipt

    root = _project(args.project)
    if getattr(args, "receipt_command", None) == "verify":
        try:
            receipt = json.loads(Path(args.file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("TAMPERED — do not trust this receipt.")
            print_json(
                {
                    "status": "TAMPERED",
                    "verified": False,
                    "problems": [f"unreadable receipt: {exc}"],
                }
            )
            return 1
        result = verify_receipt(root, receipt)
        status = result.get("status", "TAMPERED")
        # Human verdict first, then the machine-readable detail (#88).
        print(
            {
                "VERIFIED": "VERIFIED — signature and content hash both check out.",
                "CONTENT_VERIFIED": (
                    "VERIFIED (content) — the numbers match the embedded hash. The "
                    "signature was not checked here (no shared key on this machine)."
                ),
                "TAMPERED": "TAMPERED — do not trust this receipt.",
            }.get(status, "TAMPERED — do not trust this receipt.")
        )
        if status == "TAMPERED" and result.get("failing_section"):
            print(f"Failing section: {result['failing_section']}")
        print_json(result)
        # A portable content-verified receipt (no shared key here) is not
        # tampering, so only TAMPERED exits non-zero.
        return 1 if status == "TAMPERED" else 0

    receipt = build_receipt(root, sign=not getattr(args, "no_sign", False))
    wrote_any = False
    if getattr(args, "svg", None):
        Path(args.svg).parent.mkdir(parents=True, exist_ok=True)
        Path(args.svg).write_text(render_receipt_svg(receipt), encoding="utf-8")
        wrote_any = True
    if getattr(args, "out", None):
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        wrote_any = True
    if wrote_any:
        print_json(
            {
                "status": "written",
                "svg": getattr(args, "svg", None),
                "json": getattr(args, "out", None),
                "signed": isinstance(receipt.get("signature"), dict),
                "has_data": receipt.get("has_data"),
            }
        )
        return 0
    print_json(receipt)
    return 0


def cmd_proof(args: argparse.Namespace) -> int:
    from opaihub.proof import (
        build_proof_bundle,
        render_proof_markdown,
        verify_proof_bundle,
    )

    root = _project(args.project)
    if args.proof_command == "bundle":
        bundle = build_proof_bundle(root, sign=not args.no_sign)
        if getattr(args, "markdown", False):
            print(render_proof_markdown(bundle))
            return 0
        if getattr(args, "out", None):
            Path(args.out).write_text(
                json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print_json({"status": "written", "path": args.out})
            return 0
        print_json(bundle)
        return 0
    if args.proof_command == "verify":
        bundle = json.loads(Path(args.file).read_text(encoding="utf-8"))
        result = verify_proof_bundle(root, bundle)
        print_json(result)
        return 0 if result["verified"] else 1
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    from opaihub.benchmark import (
        benchmark_gate,
        compare_benchmark_reports,
        export_promptfoo_config,
        latest_benchmark_report,
        list_benchmark_suites,
        read_benchmark_history,
        render_benchmark_comparison_markdown,
        render_benchmark_html,
        render_benchmark_markdown,
        run_benchmark,
    )

    root = _project(args.project)
    if args.benchmark_command == "list":
        print_json(list_benchmark_suites())
        return 0
    if args.benchmark_command == "run":
        report = run_benchmark(
            root,
            suite=args.suite,
            mode=args.mode,
            audit=args.audit,
        )
        print_json(report)
        return 0
    if args.benchmark_command == "report":
        report = latest_benchmark_report(root)
        if report is None:
            print_json(
                {
                    "status": "missing",
                    "message": "No benchmark history yet. Run 'opai benchmark run --suite local --mode both'.",
                }
            )
            return 1
        if args.format == "json":
            print_json(report)
        elif args.format == "html":
            print(render_benchmark_html(report), end="")
        else:
            print(render_benchmark_markdown(report), end="")
        return 0
    if args.benchmark_command == "gate":
        report = latest_benchmark_report(root)
        if report is None:
            print_json(
                {
                    "status": "missing",
                    "message": "No benchmark history yet. Run 'opai benchmark run --suite local --mode both'.",
                }
            )
            return 1
        result = benchmark_gate(
            report,
            min_context_reduction=args.min_context_reduction,
            min_paid_call_avoidance=args.min_paid_call_avoidance,
            min_cost_reduction=args.min_cost_reduction,
            min_success_rate=args.min_success_rate,
            min_effectiveness_index=args.min_effectiveness_index,
            max_human_interventions=args.max_human_interventions,
            require_risk_blocks=args.require_risk_blocks,
        )
        print_json(result)
        return 0 if result["ok"] else 1
    if args.benchmark_command == "compare":
        history = read_benchmark_history(root, limit=2)
        if len(history) < 2:
            print_json(
                {
                    "status": "missing",
                    "message": "Need at least two benchmark runs to compare.",
                }
            )
            return 1
        comparison = compare_benchmark_reports(history[0], history[1])
        if args.format == "markdown":
            print(render_benchmark_comparison_markdown(comparison), end="")
        else:
            print_json(comparison)
        return 0 if comparison["ok"] else 1
    if args.benchmark_command == "export":
        if args.harness != "promptfoo":
            print_json({"status": "unsupported", "harness": args.harness})
            return 2
        target = Path(args.out) if args.out else None
        print_json(export_promptfoo_config(root, out=target, suite=args.suite))
        return 0
    if args.benchmark_command == "parity":
        # Daily-driver parity proof (#309/#314): the same offline fixture run the
        # op-hub tool exposes, surfaced on the primary CLI so users can actually
        # run it. Delegates to the one implementation — no duplicated harness.
        from opaihub.opaibench import (
            render_parity_html,
            render_parity_markdown,
            run_parity_benchmark,
        )

        try:
            report = run_parity_benchmark(
                root,
                baseline_path=Path(args.baseline) if args.baseline else None,
                write=not args.no_write,
                task_ids=tuple(args.task or ()) or None,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print_json({"status": "error", "message": str(exc)})
            return 2
        if args.format == "markdown":
            print(render_parity_markdown(report), end="")
        elif args.format == "html":
            print(render_parity_html(report), end="")
        else:
            print_json(report)
        return 0 if report["totals"]["passed"] == report["totals"]["total"] else 1
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
        packet = build_evidence_packet(
            root, args.workflow, write=not args.no_write, sign=args.sign
        )
        if not args.no_write:
            from opaihub.audit import EVIDENCE_PACKET, record_audit_event

            record_audit_event(
                root,
                EVIDENCE_PACKET,
                workflow_id=args.workflow,
                signed=args.sign,
                packet_sha256=packet.get("packet_sha256"),
            )
        print_json(packet)
        return 0
    if args.guard_command == "verify":
        from opaihub.guarded import verify_evidence_packet

        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        result = verify_evidence_packet(root, data)
        print_json(result)
        return 0 if result["verified"] else 1
    if args.guard_command == "action":
        result = guard_action(
            root, args.action, template_id=args.template, confirmed=args.confirm
        )
        if getattr(args, "audit", False):
            from opaihub.audit import GUARD_ALLOW, GUARD_DENY, record_audit_event

            record_audit_event(
                root,
                GUARD_DENY if result["decision"] == "deny" else GUARD_ALLOW,
                action=args.action,
                decision=result["decision"],
                confirmed=result["confirmed"],
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
        return 0 if result.get("status") == "free_alpha" else 2
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    from opaihub.audit import export_audit, read_audit, summarize_audit, verify_chain

    root = _project(args.project)
    if args.audit_command == "log":
        print_json(read_audit(root, limit=args.limit))
        return 0
    if args.audit_command == "status":
        print_json(summarize_audit(root))
        return 0
    if args.audit_command == "verify":
        result = verify_chain(root)
        print_json(result)
        return 0 if result["ok"] else 1
    if args.audit_command == "export":
        bundle = export_audit(root, sign=not args.no_sign)
        if args.out:
            Path(args.out).write_text(
                json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print_json(
                {
                    "status": "exported",
                    "path": args.out,
                    "events": bundle["event_count"],
                }
            )
        else:
            print_json(bundle)
        return 0
    return 0


def cmd_team(args: argparse.Namespace) -> int:
    from opaihub.team import team_report
    from opaihub.team_policy import apply_team_policy, init_team_policy

    root = _project(args.project)
    if args.team_command == "init":
        print_json(init_team_policy(root, profile=args.profile, team=args.team))
        return 0
    if args.team_command == "apply":
        result = apply_team_policy(root)
        if result.get("status") == "updated":
            from opaihub.audit import TEAM_POLICY_APPLIED, record_audit_event

            record_audit_event(
                root, TEAM_POLICY_APPLIED, profile=result.get("applied_profile")
            )
        print_json(result)
        return 0 if result.get("status") == "updated" else 2
    if args.team_command == "report":
        report = team_report(root)
        status = project_status(root)
        report["client_readiness"] = status["client_integrations"]["summary"]
        print_json(report)
        return 0
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.models_command == "list":
        from opai import app_state as A
        from opaihub.gui_preferences import load_gui_preferences

        data = A.available_models(root)
        # Reuse the GUI's capability-filtered source of truth.  Calling
        # account_models() directly here would bypass the local Codex
        # account-type check and advertise subscription-incompatible IDs.
        account_options = [
            option for option in data["models"] if option.get("kind") == "account"
        ]
        groups = [
            {
                "id": "auto",
                "label": "Auto",
                "models": [
                    {
                        "id": "auto",
                        "label": "Auto · OPai routes the cheapest safe model",
                        "provider": "opai",
                        "paid": False,
                        "available": True,
                    }
                ],
            },
            {
                "id": "codex",
                "label": "Codex",
                "models": [
                    option
                    for option in account_options
                    if option.get("provider") == "codex"
                ],
            },
            {
                "id": "claude",
                "label": "Claude",
                "models": [
                    option
                    for option in account_options
                    if option.get("provider") == "claude"
                ],
            },
            {
                "id": "copilot",
                "label": "Copilot",
                "models": [
                    option
                    for option in account_options
                    if option.get("provider") == "copilot"
                ],
            },
            {
                "id": "local",
                "label": "Local",
                "models": [
                    option for option in data["models"] if option.get("kind") == "local"
                ],
            },
        ]
        print_json(
            {
                "groups": groups,
                "default_model": load_gui_preferences(root).get("default_model"),
                "connected_accounts": data["accounts"],
                "hint": data.get("hint"),
                **{
                    key: data[key]
                    for key in (
                        "providerCatalogVersion",
                        "providerProtocolVersion",
                        "providerContracts",
                    )
                    if key in data
                },
            }
        )
    elif args.models_command == "set-default":
        from opai import app_state as A
        from opaihub.gui_preferences import save_gui_preferences

        known = {"auto"}
        known.update(option["id"] for option in A.available_models(root)["models"])
        if args.model_id not in known:
            print_json(
                {
                    "status": "unknown_model",
                    "model_id": args.model_id,
                    "hint": "Run `opai models list` to see selectable model IDs.",
                }
            )
            return 2
        prefs = save_gui_preferences(root, {"default_model": args.model_id})
        print_json({"status": "updated", "preferences": prefs})
    elif args.models_command == "discover-local":
        from opaihub.local_models import discover_local_models

        print_json(discover_local_models(root))
    elif args.models_command == "onboard":
        # Guided local-model readiness (#3): detect state per runtime and,
        # optionally, prove one privacy-safe local route. Never downloads or
        # starts anything — commands are shown, not run.
        from opaihub.local_onboarding import (
            local_onboarding_status,
            local_route_smoke_test,
        )

        status = local_onboarding_status(root)
        if getattr(args, "smoke", False) and status["ready"]:
            status["smoke_test"] = local_route_smoke_test(root)
        print_json(status)
        return 0 if status["ready"] else 1
    elif args.models_command == "recommend":
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
    if args.policy_command == "check":
        from opaihub.ci_check import run_policy_check

        result = run_policy_check(
            root, require_team_policy=getattr(args, "require_team_policy", False)
        )
        if getattr(args, "audit", False):
            from opaihub.audit import CI_CHECK, record_audit_event

            record_audit_event(root, CI_CHECK, ok=result["ok"], failed=result["failed"])
        print_json(result)
        return 0 if result["ok"] else 1
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
    if args.repair and not args.dry_run:
        from opai.visibility import write_visibility_status

        result["visibility"] = write_visibility_status(_project(args.project))
    if not args.quiet:
        print_json(result)
    return 0


def cmd_visibility(args: argparse.Namespace) -> int:
    from opai.visibility import write_visibility_status

    if args.visibility_command == "install":
        print_json(write_visibility_status(_project(args.project)))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from opaihub.dashboard import build_dashboard
    from opaihub.dashboard_html import build_dashboard_html

    root = _project(args.project)
    path = (
        build_dashboard_html(root) if args.html or args.serve else build_dashboard(root)
    )
    if args.serve:
        import http.server
        import socketserver
        import urllib.parse
        import webbrowser

        port = int(args.port)
        directory = str(path.parent)

        class DashboardRequestHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *handler_args: Any, **handler_kwargs: Any) -> None:
                super().__init__(*handler_args, directory=directory, **handler_kwargs)

            def send_head(self) -> Any:
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path in {"", "/"}:
                    self.path = "/" + path.name
                    if parsed.query:
                        self.path += "?" + parsed.query
                return super().send_head()

        class DashboardServer(socketserver.TCPServer):
            allow_reuse_address = True

        with DashboardServer(("127.0.0.1", port), DashboardRequestHandler) as httpd:
            actual_port = httpd.server_address[1]
            url = f"http://127.0.0.1:{actual_port}/"
            print_json({"status": "serving", "url": url, "path": str(path)})
            if args.open:
                webbrowser.open(url)
            httpd.serve_forever()
        return 0
    print_json({"status": "written", "path": str(path)})
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
        description="OPai 0.2.0 alpha.2: local-first AI coding cost firewall.",
    )
    parser.add_argument("--project", default=".", help="Project root")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("version")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser(
        "new",
        help="Scaffold a runnable app from a description (free boilerplate, then iterate with opai build)",
    )
    p.add_argument(
        "description", help='What to build, e.g. "a todo app with dark mode"'
    )
    p.add_argument(
        "--name",
        default=None,
        help="App/folder name (default: derived from the description)",
    )
    p.add_argument(
        "--type",
        default="auto",
        help="App kind: auto (infer) | web | static | data",
    )
    p.add_argument(
        "--into",
        default=None,
        help="Parent directory to create the app in (default: current dir)",
    )
    p.add_argument(
        "--force", action="store_true", help="Overwrite a non-empty target directory"
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser(
        "build",
        help="Edit a scaffolded app with one cheap targeted prompt (sends only the relevant files)",
    )
    p.add_argument("request", help='What to change, e.g. "make the heading purple"')
    p.add_argument("--app", default=None, help="App directory (default: current dir)")
    p.add_argument(
        "--model", default=None, help="Model id/alias (default: auto routing)"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what context would be sent; no AI call, no writes",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Roll the edit back automatically if structural verification fails",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser(
        "app-receipt",
        help="The app's aggregate cost story: spend, and the tokens never spent",
    )
    p.add_argument("--app", default=None, help="App directory (default: current dir)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_app_receipt)

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
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--width", type=int)
    p.add_argument("--no-color", action="store_true")
    p.set_defaults(func=cmd_statusline)

    p = sub.add_parser(
        "status",
        help="Show OPai activation, Superpowers, wrappers, and project state",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--human", action="store_true", help="Show the OPai cockpit view")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "repo",
        help="Inspect canonical repository safety and isolated worktree leases",
    )
    repo_sub = p.add_subparsers(dest="repo_command", required=True)
    ri = repo_sub.add_parser("inspect", help="Show redacted repository identity and safety assessment")
    ri.add_argument("--project", default=None, help="Project root")
    ri.add_argument("--json", action="store_true", help="Render machine-readable output")
    ri.set_defaults(func=cmd_repo)
    rw = repo_sub.add_parser("worktrees", help="List isolated worktree leases without cleanup")
    rw.add_argument("--project", default=None, help="Project root")
    rw.add_argument(
        "--recover",
        action="store_true",
        help="Reconcile lease state and show recommended non-destructive actions",
    )
    rw.add_argument("--json", action="store_true", help="Render machine-readable output")
    rw.set_defaults(func=cmd_repo)

    p = sub.add_parser("cockpit", help="Obvious ON/OFF control panel for OPai")
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_cockpit)

    p = sub.add_parser("autonomy", help="Show or change the Full Auto pin (#137)")
    p.add_argument("--project", default=None, help="Project root")
    autonomy_sub = p.add_subparsers(dest="autonomy_command")
    for name, helptext in (
        ("status", "Show the effective mode and Full Auto pin state"),
        ("pin", "Explicitly pin Full Auto with acknowledgement"),
        ("unpin", "Clear the Full Auto pin and fall back to Safe Auto"),
    ):
        a = autonomy_sub.add_parser(name, help=helptext)
        a.add_argument("--project", default=None, help="Project root")
        a.set_defaults(func=cmd_autonomy)
    p.set_defaults(func=cmd_autonomy)

    p = sub.add_parser(
        "github",
        help="Connect a GitHub account so runs can commit, push, and open PRs",
    )
    github_sub = p.add_subparsers(dest="github_command")
    g = github_sub.add_parser(
        "connect", help="Validate and store a GitHub personal access token"
    )
    g.add_argument(
        "--token",
        default=None,
        help="Personal access token (defaults to GITHUB_TOKEN/GH_TOKEN)",
    )
    g.set_defaults(func=cmd_github)
    g = github_sub.add_parser("status", help="Show connection and push consent")
    g.set_defaults(func=cmd_github)
    g = github_sub.add_parser(
        "disconnect", help="Remove the stored token and revoke push consent"
    )
    g.set_defaults(func=cmd_github)
    g = github_sub.add_parser(
        "allow-push",
        help="Enable or disable pushes and PR creation from coding runs",
    )
    g.add_argument("state", choices=["on", "off"], help="on enables push/PR tools")
    g.set_defaults(func=cmd_github)
    g = github_sub.add_parser(
        "allow-public-read",
        help="Enable or disable anonymous issue search on the active public origin",
    )
    g.add_argument(
        "state",
        choices=["on", "off"],
        help="on consents to anonymous public GitHub issue reads",
    )
    g.set_defaults(func=cmd_github)
    p.set_defaults(func=cmd_github)

    p = sub.add_parser(
        "gui",
        help="Launch the OPai desktop control center (native window, local-only)",
    )
    p.add_argument(
        "task",
        nargs="?",
        default=None,
        help='Optional task to pre-load the prompt with, e.g. opai gui "fix the login bug"',
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--once",
        action="store_true",
        help="Headless smoke: print the control-center state as JSON and exit",
    )
    p.add_argument(
        "--screenshot",
        metavar="PATH",
        help="Render a desktop GUI screenshot for visual QA and exit",
    )
    p.add_argument("--artifact-smoke", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--result", help=argparse.SUPPRESS)
    p.add_argument("--smoke-timeout", type=int, default=30, help=argparse.SUPPRESS)
    p.add_argument("--width", type=int, default=1040, help=argparse.SUPPRESS)
    p.add_argument("--height", type=int, default=720, help=argparse.SUPPRESS)
    p.add_argument(
        "--classic",
        action="store_true",
        help="Use the classic Qt window instead of the web-rendered UI",
    )
    p.set_defaults(func=cmd_gui)

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
        "--targets",
        default="all",
        help="Comma list: codex,claude,copilot,gemini,shell or all",
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

    p = sub.add_parser("visibility", help="Write GUI-visible OPai status files")
    visibility_sub = p.add_subparsers(dest="visibility_command", required=True)
    vi = visibility_sub.add_parser(
        "install", help="Write OPAI_STATUS.md and .opaihub/opai-status.json"
    )
    vi.add_argument("--project", default=None, help="Project root")
    vi.set_defaults(func=cmd_visibility)

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
    p.add_argument(
        "--rollups",
        action="store_true",
        help="Include day/week/month/agent/repo savings rollups",
    )
    p.set_defaults(func=cmd_savings)

    p = sub.add_parser(
        "ux-metrics",
        help="Show local-only product-health metrics (verdict distribution, cancel/timeout rates)",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--markdown", action="store_true", help="Render the report as markdown"
    )
    p.set_defaults(func=cmd_ux_metrics)

    p = sub.add_parser(
        "outcomes",
        help="Task-outcome metrics: cost per completed task and duplicate calls avoided (#288)",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--markdown", action="store_true", help="Render a short human summary"
    )
    p.add_argument(
        "--json", action="store_true", help="Print the full summary as JSON (default)"
    )
    p.set_defaults(func=cmd_outcomes)

    p = sub.add_parser(
        "release",
        help="Release-candidate preflight, dry-run isolation proof, and rollback (#32)",
    )
    release_sub = p.add_subparsers(dest="release_command", required=True)
    rp_pre = release_sub.add_parser(
        "preflight", help="Assemble every release check into one readiness verdict"
    )
    rp_pre.add_argument("--project", default=None, help="Project root")
    rp_pre.add_argument(
        "--run-tests",
        action="store_true",
        help="Include the local test gate (scripts/ci_local.py --fast)",
    )
    rp_pre.add_argument(
        "--artifacts", metavar="MANIFEST", help="Artifact manifest JSON to verify"
    )
    rp_pre.add_argument(
        "--execute",
        action="store_true",
        help="Treat this as a real release run (default is dry-run: publish disabled)",
    )
    rp_pre.add_argument("--format", default="markdown", choices=["markdown", "json"])
    rp_pre.add_argument("--out", metavar="PATH", help="Write sanitized evidence JSON")
    rp_pre.set_defaults(func=cmd_release)
    rp_proof = release_sub.add_parser(
        "dry-run-proof",
        help="Prove a dry-run has no side effects: publish disabled + network blocked",
    )
    rp_proof.add_argument("--project", default=None, help="Project root")
    rp_proof.set_defaults(func=cmd_release)
    rp_rb = release_sub.add_parser(
        "rollback", help="Plan (or --execute) a rollback to the previous tested release"
    )
    rp_rb.add_argument("--project", default=None, help="Project root")
    rp_rb.add_argument(
        "--previous-manifest",
        required=True,
        metavar="MANIFEST",
        help="Manifest of the previous tested release",
    )
    rp_rb.add_argument("--to", metavar="VERSION", help="Version to roll back to")
    rp_rb.add_argument(
        "--execute",
        action="store_true",
        help="Perform the rollback (default: print the plan only)",
    )
    rp_rb.add_argument("--release-root", metavar="DIR", help="Installed release root")
    rp_rb.add_argument("--pointer", metavar="FILE", help="Active-release pointer file")
    rp_rb.add_argument(
        "--protect",
        action="append",
        metavar="DIR",
        help="User-state dir rollback must never touch (repeatable)",
    )
    rp_rb.set_defaults(func=cmd_release)

    p = sub.add_parser(
        "resume",
        help="Show this workspace's resumable session (read-only; GUI parity, #313)",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--markdown", action="store_true", help="Render a short human summary"
    )
    p.add_argument(
        "--json", action="store_true", help="Print the full payload as JSON (default)"
    )
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser(
        "budget",
        help="Hard cost firewall: set/status/gate budgets and panic mode",
    )
    budget_sub = p.add_subparsers(dest="budget_command", required=True)
    bs = budget_sub.add_parser("status", help="Budget ceilings, spend, and remaining")
    bs.add_argument("--project", default=None, help="Project root")
    bs.set_defaults(func=cmd_budget)
    bset = budget_sub.add_parser("set", help="Set per-project budget ceilings")
    bset.add_argument("--daily", type=float, default=None, help="Daily USD limit")
    bset.add_argument("--monthly", type=float, default=None, help="Monthly USD limit")
    bset.add_argument(
        "--per-task", type=float, default=None, help="Hard per-task USD limit"
    )
    bset.add_argument("--project", default=None, help="Project root")
    bset.set_defaults(func=cmd_budget)
    bg = budget_sub.add_parser(
        "gate",
        help="Fail-closed gate: exits non-zero if the next route exceeds policy/budget",
    )
    bg.add_argument("task")
    bg.add_argument("--project", default=None, help="Project root")
    bg.set_defaults(func=cmd_budget)
    bp = budget_sub.add_parser(
        "panic", help="Force deterministic/local-only routing until disabled"
    )
    bp.add_argument("--off", action="store_true", help="Disable panic mode")
    bp.add_argument("--project", default=None, help="Project root")
    bp.set_defaults(func=cmd_budget)

    p = sub.add_parser(
        "proxy",
        help="Route one agent call through OPai (the inline-capture shim entrypoint)",
    )
    p.add_argument("agent", help="Agent to route through (claude, codex, or copilot)")
    p.add_argument("task", help="The task/prompt to run")
    p.add_argument(
        "--mode",
        default="ask",
        help="ask | plan | safe-auto | approve-edits | full-auto",
    )
    p.add_argument(
        "--model", default=None, help="Model for the account (claude/codex/copilot)"
    )
    p.add_argument("--json", action="store_true", help="Print the full result as JSON")
    p.add_argument("--project", default=None, help="Project root")
    p.set_defaults(func=cmd_proxy)

    p = sub.add_parser(
        "agent-launch",
        help="Internal shell-wrapper entrypoint for capture-aware agent launches",
    )
    p.add_argument("agent", help="claude | codex | copilot | gemini")
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("agent_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_agent_launch)

    p = sub.add_parser(
        "receipt",
        help="Signed, screenshot-able savings receipt you can share and verify",
    )
    receipt_sub = p.add_subparsers(dest="receipt_command", required=False)
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--svg", metavar="PATH", help="Write a shareable SVG card")
    p.add_argument("--out", metavar="PATH", help="Write the signed receipt JSON")
    p.add_argument(
        "--no-sign",
        action="store_true",
        help="Skip signing (card is watermarked UNVERIFIED)",
    )
    p.set_defaults(func=cmd_receipt)
    rv = receipt_sub.add_parser(
        "verify", help="Verify a receipt's content hash + signature"
    )
    rv.add_argument("file")
    rv.add_argument("--project", default=None, help="Project root")
    rv.set_defaults(func=cmd_receipt)

    p = sub.add_parser(
        "proof",
        help="Local, signed proof bundles for alpha users and teams",
    )
    proof_sub = p.add_subparsers(dest="proof_command", required=True)
    pb = proof_sub.add_parser(
        "bundle", help="Assemble a signed proof bundle (benchmark+savings+policy+audit)"
    )
    pb.add_argument("--out", metavar="PATH", help="Write the bundle to a file")
    pb.add_argument("--markdown", action="store_true")
    pb.add_argument("--no-sign", action="store_true")
    pb.add_argument("--project", default=None, help="Project root")
    pb.set_defaults(func=cmd_proof)
    pv = proof_sub.add_parser(
        "verify", help="Verify a proof bundle's signature + artifacts"
    )
    pv.add_argument("file")
    pv.add_argument("--project", default=None, help="Project root")
    pv.set_defaults(func=cmd_proof)

    p = sub.add_parser(
        "benchmark",
        help="Run local effectiveness benchmarks against normal AI usage",
    )
    benchmark_sub = p.add_subparsers(dest="benchmark_command", required=True)
    br = benchmark_sub.add_parser("list", help="List benchmark suites")
    br.add_argument("--project", default=None, help="Project root")
    br.set_defaults(func=cmd_benchmark)
    br = benchmark_sub.add_parser(
        "run", help="Run a local benchmark suite with no cloud calls by default"
    )
    br.add_argument("--project", default=None, help="Project root")
    br.add_argument("--suite", default="local", choices=["local", "max"])
    br.add_argument("--mode", default="both", choices=["baseline", "opai", "both"])
    br.add_argument(
        "--audit", action="store_true", help="Record a redacted benchmark audit event"
    )
    br.set_defaults(func=cmd_benchmark)
    br = benchmark_sub.add_parser("report", help="Render the latest benchmark report")
    br.add_argument("--project", default=None, help="Project root")
    br.add_argument(
        "--format", default="markdown", choices=["markdown", "json", "html"]
    )
    br.set_defaults(func=cmd_benchmark)
    br = benchmark_sub.add_parser(
        "gate", help="Fail CI if the latest benchmark misses proof thresholds"
    )
    br.add_argument("--project", default=None, help="Project root")
    br.add_argument("--min-context-reduction", type=float, default=10.0)
    br.add_argument("--min-paid-call-avoidance", type=float, default=1.0)
    br.add_argument("--min-cost-reduction", type=float, default=1.0)
    br.add_argument("--min-success-rate", type=float, default=1.0)
    br.add_argument("--min-effectiveness-index", type=float, default=0.0)
    br.add_argument("--max-human-interventions", type=int)
    br.add_argument("--require-risk-blocks", action="store_true")
    br.set_defaults(func=cmd_benchmark)
    br = benchmark_sub.add_parser("compare", help="Compare the latest two runs")
    br.add_argument("--project", default=None, help="Project root")
    br.add_argument("--format", default="json", choices=["json", "markdown"])
    br.set_defaults(func=cmd_benchmark)
    br = benchmark_sub.add_parser(
        "export", help="Export an optional external benchmark harness config"
    )
    br.add_argument("--project", default=None, help="Project root")
    br.add_argument("--harness", default="promptfoo", choices=["promptfoo"])
    br.add_argument("--suite", default="local", choices=["local", "max"])
    br.add_argument("--out", metavar="PATH")
    br.set_defaults(func=cmd_benchmark)
    br = benchmark_sub.add_parser(
        "parity",
        help="Daily-driver parity: run real coding fixtures through the offline "
        "OPai pipeline and score vs an imported baseline (#309/#314)",
    )
    br.add_argument("--project", default=None, help="Project root")
    br.add_argument(
        "--format", default="markdown", choices=["markdown", "json", "html"]
    )
    br.add_argument(
        "--baseline", help="Versioned offline baseline JSON to score against"
    )
    br.add_argument(
        "--task", action="append", help="Run only this fixture task id (repeatable)"
    )
    br.add_argument(
        "--no-write", action="store_true", help="Do not persist the report to the hub"
    )
    br.set_defaults(func=cmd_benchmark)

    p = sub.add_parser(
        "why", help="Explain why OPai chose its route for a task (read-only)"
    )
    p.add_argument("task")
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(func=cmd_why)

    p = sub.add_parser(
        "ask",
        help=(
            "Ask a task: free local-first by default; --model streams your "
            "Claude/Codex/Copilot account with live activity (same core as the GUI)"
        ),
    )
    p.add_argument("task")
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--model",
        default=None,
        help=(
            "Model to run: auto, claude[:sonnet|opus|haiku], codex[:model], "
            "copilot[:model], or a full/local model id. Streams live activity."
        ),
    )
    p.add_argument(
        "--mode",
        default=None,
        help="Run mode with --model: ask | plan | safe-auto | approve-edits | full-auto",
    )
    p.add_argument(
        "--allow-cloud",
        action="store_true",
        help="Permit a cloud-tier recommendation to be surfaced (still never auto-calls cloud)",
    )
    p.add_argument(
        "--no-record", action="store_true", help="Do not record a ledger savings event"
    )
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser(
        "hooks",
        help="Provider CLI hook entrypoints (fast; safe inside agent sessions)",
    )
    hooks_sub = p.add_subparsers(dest="hooks_command")
    hp = hooks_sub.add_parser(
        "claude-pre-tool",
        help="Claude Code PreToolUse gate: classify a Bash command from stdin",
    )
    hp.set_defaults(func=cmd_hooks)
    p.set_defaults(func=cmd_hooks)

    p = sub.add_parser(
        "context", help="Build a tiny, targeted context pack instead of whole files"
    )
    context_sub = p.add_subparsers(dest="context_command", required=True)
    cp = context_sub.add_parser("pack", help="Changed files + adjacent tests + markers")
    cp.add_argument("--project", default=None, help="Project root")
    cp.add_argument(
        "--all", action="store_true", help="Project scope instead of changed-only"
    )
    cp.add_argument(
        "--write", action="store_true", help="Persist .opaihub/context/pack.json"
    )
    cp.set_defaults(func=cmd_context)
    cpr = context_sub.add_parser(
        "profile", help="Rank context-waste sources with before/after token/cost"
    )
    cpr.add_argument("--project", default=None, help="Project root")
    cpr.add_argument("--markdown", action="store_true")
    cpr.set_defaults(func=cmd_context)
    cig = context_sub.add_parser(
        "ignores", help="Generate per-client ignore files (cursor/claude/copilot/cline)"
    )
    cig.add_argument(
        "--clients", default=None, help="Comma list, e.g. cursor,claude,copilot,cline"
    )
    cig.add_argument("--project", default=None, help="Project root")
    cig.set_defaults(func=cmd_context)

    p = sub.add_parser(
        "test", help="Select the tests most likely to cover changed files"
    )
    p.add_argument(
        "--changed", action="store_true", help="Select from changed files (default)"
    )
    p.add_argument(
        "--run", action="store_true", help="Run the targeted tests via policy"
    )
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--project", default=None, help="Project root")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser(
        "share",
        help="Generate a shareable savings card and badge from the local ledger",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--markdown", action="store_true")
    p.add_argument("--write-badge", metavar="PATH", help="Write an SVG savings badge")
    p.set_defaults(func=cmd_share)

    p = sub.add_parser(
        "metrics",
        help="Local product metrics (savings, escalations avoided, cache rate)",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser(
        "quickstart", help="Guided 60-second first run: activate, route, savings, share"
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--project-only", action="store_true", help="Skip global integrations"
    )
    p.add_argument("--task", help="Sample task to route (defaults to a git summary)")
    p.set_defaults(func=cmd_quickstart)

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
    ge.add_argument("--sign", action="store_true", help="HMAC-sign the evidence packet")
    ge.set_defaults(func=cmd_guard)
    gv = guard_sub.add_parser(
        "verify", help="Verify an evidence packet hash + signature"
    )
    gv.add_argument("file")
    gv.add_argument("--project", default=None, help="Project root")
    gv.set_defaults(func=cmd_guard)
    ga = guard_sub.add_parser("action", help="Fail-closed gate for a risky action")
    ga.add_argument("action")
    ga.add_argument("--template", default=None)
    ga.add_argument("--confirm", action="store_true")
    ga.add_argument(
        "--audit", action="store_true", help="Record the decision to the audit trail"
    )
    ga.add_argument("--project", default=None, help="Project root")
    ga.set_defaults(func=cmd_guard)

    p = sub.add_parser(
        "edition",
        help="Show free public-alpha availability; legacy selection is a no-op",
    )
    edition_sub = p.add_subparsers(dest="edition_command", required=True)
    ed = edition_sub.add_parser("show")
    ed.add_argument("--project", default=None, help="Project root")
    ed.set_defaults(func=cmd_edition)
    ed = edition_sub.add_parser("set")
    ed.add_argument(
        "edition_name",
        help="Legacy value to ignore; every implemented alpha capability is free",
    )
    ed.add_argument("--project", default=None, help="Project root")
    ed.set_defaults(func=cmd_edition)

    p = sub.add_parser(
        "audit", help="Tamper-evident governance audit trail (log, verify, export)"
    )
    audit_sub = p.add_subparsers(dest="audit_command", required=True)
    au = audit_sub.add_parser("log", help="Show recent audit events")
    au.add_argument("--limit", type=int, default=20)
    au.add_argument("--project", default=None, help="Project root")
    au.set_defaults(func=cmd_audit)
    au = audit_sub.add_parser("status", help="Audit summary and chain validity")
    au.add_argument("--project", default=None, help="Project root")
    au.set_defaults(func=cmd_audit)
    au = audit_sub.add_parser("verify", help="Verify the audit hash chain")
    au.add_argument("--project", default=None, help="Project root")
    au.set_defaults(func=cmd_audit)
    au = audit_sub.add_parser("export", help="Export a signed audit bundle")
    au.add_argument("--out", metavar="PATH", help="Write the bundle to a file")
    au.add_argument("--no-sign", action="store_true")
    au.add_argument("--project", default=None, help="Project root")
    au.set_defaults(func=cmd_audit)

    p = sub.add_parser(
        "team", help="Team governance: committed policy, apply, and report"
    )
    team_sub = p.add_subparsers(dest="team_command", required=True)
    ti = team_sub.add_parser("init", help="Create a committable opai-team-policy.yaml")
    ti.add_argument("--profile", default="team-safe")
    ti.add_argument("--team", default="my-team")
    ti.add_argument("--project", default=None, help="Project root")
    ti.set_defaults(func=cmd_team)
    ta = team_sub.add_parser("apply", help="Apply the committed team policy locally")
    ta.add_argument("--project", default=None, help="Project root")
    ta.set_defaults(func=cmd_team)
    tr = team_sub.add_parser("report", help="Team governance rollup")
    tr.add_argument("--project", default=None, help="Project root")
    tr.set_defaults(func=cmd_team)

    p = sub.add_parser("models", help="Model recommendation and routing helpers")
    models_sub = p.add_subparsers(dest="models_command", required=True)
    mo = models_sub.add_parser(
        "list", help="List Auto, Codex, Claude, Copilot, and local model choices"
    )
    mo.add_argument("--project", default=None, help="Project root")
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser(
        "set-default", help="Set the default model for OPai GUI/account routing"
    )
    mo.add_argument("model_id")
    mo.add_argument("--project", default=None, help="Project root")
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser(
        "discover-local", help="Detect Ollama, LM Studio, or local model endpoints"
    )
    mo.add_argument("--project", default=None, help="Project root")
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser(
        "onboard",
        help="Guided local-model readiness with consent-gated next steps (#3)",
    )
    mo.add_argument("--project", default=None, help="Project root")
    mo.add_argument(
        "--smoke",
        action="store_true",
        help="If a local model is ready, run one privacy-safe route smoke test",
    )
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser("recommend")
    mo.add_argument("task")
    mo.add_argument("--project", default=None, help="Project root")
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser(
        "eval", help="Score routing on offline fixtures (local redacted scorecard)"
    )
    mo.add_argument(
        "--no-write",
        action="store_true",
        help="Print the scorecard without writing .opaihub/eval/scorecard.json",
    )
    mo.add_argument("--project", default=None, help="Project root")
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
    po = policy_sub.add_parser(
        "check", help="Fail-closed CI governance gate (exits non-zero on violation)"
    )
    po.add_argument(
        "--audit", action="store_true", help="Record the result to the audit trail"
    )
    po.add_argument(
        "--require-team-policy",
        action="store_true",
        help="Fail if opai-team-policy.yaml is missing (strict team CI mode)",
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
        "update",
        help="Check whether a newer OPai is available, or apply it with --apply",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Fetch, fast-forward, and reinstall (refuses on uncommitted local changes)",
    )
    p.add_argument(
        "--cached",
        action="store_true",
        help="Reuse the last check (within an hour) instead of hitting the network again",
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
        "cost": ["cost", "status"],
    }.items():
        p = sub.add_parser(name)
        p.add_argument("--project", default=None, help="Project root")
        p.set_defaults(func=cmd_delegate, hub_args=hub_args)

    p = sub.add_parser("dashboard", help="Write or serve the local OPai dashboard")
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--html", action="store_true")
    p.add_argument("--serve", action="store_true", help="Serve dashboard on localhost")
    p.add_argument("--port", type=int, default=0, help="Localhost port; 0 chooses one")
    p.add_argument("--open", action="store_true", help="Open the served dashboard")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("hub", help="Pass through to op-hub")
    p.add_argument("hub_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_delegate)
    return parser


def _force_utf8_stdout() -> None:
    """Avoid UnicodeEncodeError when printing rich text on a cp1252 console (Windows).

    OPai's output uses characters like ``·`` and ``✓``; on a legacy Windows
    console these crash plain ``print``. Reconfigure to UTF-8 with a safe
    fallback so output degrades to ``?`` instead of raising.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
