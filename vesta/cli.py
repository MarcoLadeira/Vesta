from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import uuid
from pathlib import Path
from typing import Any

from vesta import __brand__
from vesta.context_slim import (
    clean_generated_context,
    context_bloat_report,
    write_ai_ignore_files,
)
from vesta.integrations import (
    activate_project,
    install_global_integrations,
    load_global_status,
    project_status,
    render_statusline,
    shell_environment,
)
from vesta.installer import install_project
from vesta.publish import publish_status, write_publish_status
from vesta.project_discovery import discover_project_root
from vesta.release_identity import (
    current_release_identity,
    release_version_text,
    surface_identity_payload,
)
from vesta.terminal_ui import build_welcome, play_animation
from vestahub.cli import main as hub_main
from vestahub.model_intelligence import recommend_model
from vestahub.proc import AGENT_SESSION_ENV
from vestahub.router import compact_decision, route_task
from vestahub.skills import skill_items, skill_status
from vestahub.boundary_errors import safe_detail


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _project(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return discover_project_root(Path("."))


def cmd_version(args: argparse.Namespace) -> int:
    if args.json:
        print_json(surface_identity_payload(brand=__brand__))
    else:
        print(release_version_text(brand=__brand__))
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
    # or write project files. Use `vesta activate` for write side effects.
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
        from vesta.cockpit import build_cockpit, render_cockpit

        print(render_cockpit(build_cockpit(_project(args.project))), end="")
        return 0
    print_json(project_status(_project(args.project)))
    return 0


def cmd_repo(args: argparse.Namespace) -> int:
    """Inspect canonical repository safety without exposing destructive cleanup."""

    from vestahub.repo_context import repository_safety_surface

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
                from vestahub.repository_safety import build_repository_safety_receipt
                from vestahub.worktree_leases import WorktreeManager

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
                payload["recovery_error"] = safe_detail(exc)[:240]
        payload["leases"] = payload["worktree_leases"]
        payload["recovery"] = recovery
    if bool(getattr(args, "json", False)):
        print_json(payload)
    else:
        receipt = safety.get("receipt") or {}
        print(f"Repository: {context.path}")
        print(
            f"Safety: {safety.get('status')} ({receipt.get('repository_id', '')[:12]})"
        )
        assessment = safety.get("assessment") or {}
        print(f"Dirty assessment: {assessment.get('outcome') or 'unavailable'}")
        if command == "worktrees":
            print(f"Worktree leases: {len(payload['leases'])}")
            if recovery:
                print("Recovery only reconciled state; no worktree was removed.")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Resolve or execute the canonical verification policy."""

    from vestahub.verification_policy import (
        persist_effective_policy,
        resolve_verification_policy,
    )

    root = _project(args.project)
    policy = resolve_verification_policy(
        root,
        task=args.task,
        mode=args.mode,
        delivery=args.delivery,
    )
    payload = policy.to_dict()
    if args.verify_command == "run":
        from vestahub.repository_safety import (
            RepositoryProbeError,
            capture_repository_handle,
        )
        from vestahub.verification_execution import (
            VerificationExecutionContext,
            execute_policy,
            load_verification_manifest,
            persist_verification_manifest,
            verification_verdict,
        )

        task_id = "verify-" + sha256(args.task.encode("utf-8")).hexdigest()[:16]
        run_id = uuid.uuid4().hex[:16]
        try:
            handle = capture_repository_handle(root, task_id=task_id, run_id=run_id)
            persist_effective_policy(root, policy, task_id=task_id, run_id=run_id)
            manifest = execute_policy(
                policy, VerificationExecutionContext.from_repository_handle(handle)
            )
            reference = persist_verification_manifest(root, manifest)
            persisted = load_verification_manifest(reference.path)
            verdict = verification_verdict(persisted)
            output = {
                "policy": payload,
                "manifest": {**persisted.to_dict(), "artifact": reference.to_dict()},
                "verdict": verdict.value,
            }
        except (OSError, TypeError, ValueError, RepositoryProbeError) as exc:
            output = {
                "policy": payload,
                "manifest": {},
                "verdict": "blocked",
                "error": safe_detail(exc)[:400],
            }
        if args.json:
            print_json(output)
        else:
            print(f"Verification verdict: {output['verdict']}")
            if output["manifest"]:
                print("Evidence manifest: " + output["manifest"]["artifact"]["path"])
            elif output.get("error"):
                print("ERROR: " + output["error"])
        return 0 if output["verdict"] == "verified" else 2
    if args.json:
        print_json(payload)
    else:
        print(f"Verification policy: {payload['status']} ({payload['digest'][:12]})")
        print(
            "Required checks: "
            + ", ".join(
                item["id"]
                for item in payload["checks"]
                if item["requirement"] == "required"
            )
        )
        if payload["human_reviews"]:
            print(
                "Human reviews: "
                + ", ".join(item["requirement"] for item in payload["human_reviews"])
            )
        for finding in payload["findings"]:
            print(
                f"{finding['severity'].upper()}: {finding['code']} — {finding['message']}"
            )
    return 0 if policy.status == "ready" else 2


def cmd_cockpit(args: argparse.Namespace) -> int:
    from vesta.cockpit import build_cockpit, render_cockpit

    payload = build_cockpit(_project(args.project))
    if args.json:
        print_json(payload)
    else:
        print(render_cockpit(payload), end="")
    return 0


def cmd_autonomy(args: argparse.Namespace) -> int:
    """Show or change the Full Auto pin so the CLI matches every surface (#137)."""
    from vestahub.autonomy import resolve_startup_mode
    from vestahub.gui_preferences import (
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
    from vestahub.github_connector import (
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
        from vesta.gui_web import run_artifact_smoke

        try:
            result = run_artifact_smoke(
                root,
                Path(result_path),
                timeout_seconds=int(getattr(args, "smoke_timeout", 30)),
            )
        except Exception as exc:  # noqa: BLE001 - artifact smoke must surface a typed failure
            result = {
                "ok": False,
                "status": "artifact_smoke_failed",
                "error": safe_detail(exc),
            }
        print_json(result)
        return 0 if result.get("ok") else 1

    from vesta.gui_desktop import INSTALL_HINT, launch, render_screenshot, run_once

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
                    "error": safe_detail(exc),
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
            from vesta.gui_web import launch as launch_web
            from vesta.gui_web import web_available

            if web_available():
                return int(launch_web(root, task=task))
        except Exception as exc:  # noqa: BLE001 - fall back to the Qt window
            print_json({"status": "web_gui_fallback", "error": safe_detail(exc)})
    try:
        return int(launch(root, task=task))
    except Exception as exc:  # noqa: BLE001 - dependency/display failures degrade
        print_json(
            {
                "status": "gui_unavailable",
                "error": safe_detail(exc),
                "hint": INSTALL_HINT,
            }
        )
        return 1


def gui_main() -> int:
    """Windowed entry point for the ``vesta-gui`` launcher (#148).

    Installed via ``[project.gui_scripts]``, so on Windows pip generates a GUI
    executable (pythonw-backed) that opens the desktop app with **no attached
    console window** — suitable for a Start-menu/taskbar shortcut. Any arguments
    are forwarded to the ``gui`` subcommand, so ``vesta-gui --project X`` and
    ``vesta-gui "fix the bug"`` behave exactly like ``vesta gui ...``.
    """
    return main(["gui", *sys.argv[1:]])


def cmd_new(args: argparse.Namespace) -> int:
    """Scaffold a runnable app skeleton from a description — zero tokens (#276).

    The free-boilerplate entry point to Vesta Build: get a runnable app, then
    build features with cheap targeted `vesta ask` prompts.
    """
    from vestahub.app_scaffold import scaffold_app

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
            print_json({"ok": False, "error": safe_detail(exc)})
        else:
            print(f"✗ {safe_detail(exc)}")
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
    """One turn of the Vesta Build customization loop (#276).

    Sends only the relevant app files, receives complete updated files, and
    applies them deterministically with backups — the model never gets tool
    access. Runs through the normal pipeline, so routing, the cost firewall,
    and the savings receipt all apply.
    """
    refusal = _refuse_if_nested_agent_session()
    if refusal is not None:
        return refusal
    from vestahub.build_loop import run_build_request

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
    machine — this reads the same local events Vesta already recorded."""
    from vestahub.ux_metrics import render_ux_metrics_markdown, summarize_ux_metrics

    root = _project(args.project)
    metrics = summarize_ux_metrics(root)
    if getattr(args, "markdown", False):
        print(render_ux_metrics_markdown(metrics))
    else:
        print_json(metrics)
    return 0


def cmd_app_receipt(args: argparse.Namespace) -> int:
    """The aggregate cost story of one Vesta Build app (#276): what was spent,
    and — the number no one else shows — what was never spent."""
    from vestahub.build_loop import app_receipt

    app_root = Path(args.app).expanduser().resolve() if args.app else Path.cwd()
    receipt = app_receipt(app_root)
    if args.json:
        print_json(receipt)
        return 0 if receipt.get("ok") else 2
    if not receipt.get("ok"):
        print(f"✗ {receipt.get('error')}")
        return 2
    print(f"Vesta Build receipt — {receipt['app']} ({receipt['kind']})")
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
        from vestahub.gui_preferences import load_gui_preferences

        selected = str(load_gui_preferences(root).get("default_model") or "auto")
    except Exception:  # noqa: BLE001 - doctor must never crash on a bad prefs file
        return {"checked": False}
    parts = selected.split(":")
    if len(parts) >= 3 and parts[0] == "account":
        result = validate(parts[1], ":".join(parts[2:]))
        return {"checked": True, "model": selected, **result}
    # auto / free / local models are not account-registry ids — nothing to flag.
    return {"checked": True, "model": selected, "valid": True, "reason": ""}


def _iso_now_for_journal() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cmd_objectives(args: argparse.Namespace) -> int:
    from vesta.agents_bridge import control_objective_payload, objectives_payload
    from vestahub.agent_objectives import ObjectiveStore
    from vestahub.command_runner import redact

    root = _project(args.project)
    action = args.agents_command
    try:
        if action == "create":
            from vesta.agents_bridge import create_objective_payload

            objective = create_objective_payload(
                root,
                {
                    "text": args.objective,
                    "mode": args.mode,
                    "model": args.model,
                    "maxParallel": args.max_parallel,
                    "budgetUsd": args.budget,
                    "allowCloud": args.allow_cloud,
                    "requestId": args.request_id,
                },
            )
            if args.start_objective:
                from vestahub.objective_execution import ObjectiveExecutor

                objective = ObjectiveExecutor(root).run(objective["objective_id"])
            result = {"objective": objective}
        elif action == "list":
            result = objectives_payload(root)
        elif action == "show":
            result = {"objective": ObjectiveStore(root).snapshot(args.objective_id)}
        elif action == "receipt":
            objective = ObjectiveStore(root).snapshot(args.objective_id)
            receipt = objective["receipt"]
            if args.assignment:
                item = next(
                    (
                        row
                        for row in objective["assignments"]
                        if row["assignment_id"] == args.assignment
                    ),
                    None,
                )
                if item is None:
                    raise ValueError("Assignment does not belong to this objective")
                receipt = item["receipt"]
            if args.sign:
                from vestahub.signing import sign

                receipt = sign(root, receipt)
            print(json.dumps(receipt, indent=2, default=str))
            return 0
        elif action in {"run", "resume"}:
            from vestahub.objective_execution import ObjectiveExecutor

            if action == "resume":
                ObjectiveStore(root).control(args.objective_id, "resume")
            result = {"objective": ObjectiveExecutor(root).run(args.objective_id)}
        else:
            value = args.value
            if action == "approve":
                value = {"request_id": args.request_id}
            elif action == "retry":
                value = {"run_id": args.run_id}
            elif action == "request-review":
                value = {"revision": args.revision}
            result = control_objective_payload(
                root,
                {
                    "objective_id": args.objective_id,
                    "assignment_id": args.assignment,
                    "action": "request_review"
                    if action == "request-review"
                    else action,
                    "value": value,
                },
            )
        if args.json:
            print(json.dumps(result, default=str))
        else:
            objectives = result.get("objectives", [result.get("objective", {})])
            if not objectives:
                print("No engineering objectives yet.")
            for item in objectives:
                print(
                    f"{item.get('objective_id', '')}  {item.get('status', 'unknown')}  {item.get('objective', '')}"
                )
                cost = item.get("cost_usd", "0")
                coverage = "complete" if item.get("cost_complete") else "incomplete"
                budget = item.get("budget_usd")
                print(
                    f"  Cost: ${cost} ({coverage}); budget: {'unset' if budget is None else '$' + budget}"
                )
                for assignment in item.get("assignments", []):
                    print(
                        f"  {assignment['assignment_id']}  {assignment['status']}  {assignment.get('title', '')}"
                    )
                    route = (
                        assignment.get("observed_model")
                        or assignment.get("model")
                        or "auto"
                    )
                    print(
                        f"    Model: {route}; cost: ${assignment.get('cost_usd', '0')}"
                    )
                    if assignment.get("blocked_reason"):
                        print(f"    {assignment['blocked_reason']}")
                if action != "list":
                    integration = item.get("integration") or {}
                    evidence = integration.get("result") or {}
                    for label, value in (
                        (
                            "Integration",
                            evidence.get("summary") or evidence.get("error"),
                        ),
                        ("Commit", evidence.get("head_sha")),
                        ("Branch", integration.get("branch")),
                        ("Worktree", integration.get("worktree")),
                    ):
                        if value:
                            print(f"  {label}: {value}")
        executed = action in {"run", "resume", "reconcile", "verify"} or (
            action == "create" and args.start_objective
        )
        if executed and (result.get("objective") or {}).get("status") in {
            "failed",
            "needs-attention",
            "cancelled",
            "blocked",
        }:
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps({"ok": False, "error": redact(str(exc))})
            if args.json
            else redact(str(exc))
        )
        return 1


def cmd_journal(args: argparse.Namespace) -> int:
    """Inspect, back up and recover the #613 runtime journal.

    Requirement 12 asks for backup and recovery. Doctor answers "is there
    anything to recover from"; this is how a person actually does it. Building
    the recovery path and leaving it reachable only from Python would repeat
    the mistake this migration already made once, where a correct and fully
    tested reader was never wired into anything that runs.

    ``restore`` is the one destructive command here, so it names what it is
    about to replace and requires ``--yes``. It also takes its own backup of
    the journal it overwrites, which is the difference between a recovery and
    a second incident.
    """

    from vestahub import journal_backup, journal_store

    root = _project(getattr(args, "project", None))
    action = getattr(args, "journal_command", "status")
    as_json = bool(getattr(args, "json", False))

    if action == "status":
        payload = {
            "project": str(root),
            "journal": _journal_doctor(root),
        }
        if as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        journal = payload["journal"]
        if not journal.get("present"):
            print("runtime journal: not started (no journal for this project yet)")
            return 0
        integrity = journal.get("integrity", {})
        migration = journal.get("migration", {})
        backup = journal.get("backup", {})
        print(f"runtime journal: {integrity.get('state', 'unknown')}")
        if migration.get("runs_recorded_known", False):
            print(f"  runs recorded:  {migration.get('runs_recorded', 0)}")
        else:
            why = migration.get("runs_recorded_unknown_because") or "unreadable"
            print(f"  runs recorded:  unknown ({why})")
        print(f"  legacy runs:    {migration.get('legacy_runs', 0)}")
        print(f"  compared:       {migration.get('compared_runs', 0)}")
        print(f"  retirement:     {migration.get('retirement', 'unknown')}")
        for blocker in migration.get("blockers", []) or []:
            print(f"    - {blocker}")
        # A missing key is "not checked", never a reassuring default.
        if not migration.get("unterminated_runs_known", False):
            why = migration.get("unterminated_runs_unknown_because") or "unreadable"
            print(f"  unfinished:     unknown ({why})")
        else:
            unterminated = migration.get("unterminated_runs", 0)
            print(f"  unfinished:     {unterminated}", end="")
            held = migration.get("unterminated_runs_holding_a_lease", 0)
            print(f" ({held} still holding a lease)" if unterminated else "")
        # Narrower than "unfinished" on purpose: only runs whose owning process
        # is provably gone. Everything else is either being worked on or cannot
        # be judged, and neither is something to hand a user as a chore.
        abandoned = migration.get("unterminated_runs_abandoned", 0)
        if abandoned:
            print(f"  abandoned:      {abandoned} (owning process is gone)")
        # Named separately from "unfinished": these runs *ended*, and said they
        # succeeded. What they have not got is anything to show for it.
        if migration.get("completed_runs_known", False):
            bare = int(migration.get("completed_runs_without_evidence", 0))
            unverified = int(migration.get("completed_runs_without_verification", 0))
            total = int(migration.get("completed_runs", 0))
            if bare:
                print(
                    f"  unevidenced:    {bare} of {total} completed runs have no"
                    " verification, manifest or cost"
                )
            if unverified:
                print(
                    f"  unverified:     {unverified} of {total} completed runs have"
                    " no verification (AC6 asks for this one)"
                )
        if migration.get("cancelled_runs_known", False):
            unconfirmed = int(migration.get("cancelled_runs_unconfirmed", 0))
            cancelled = int(migration.get("cancelled_runs", 0))
            if unconfirmed:
                print(
                    f"  unconfirmed:    {unconfirmed} of {cancelled} cancelled runs"
                    " have no phase reaching 'terminated'"
                )
        # Two recordings of one history. Silence when they agree; a count when
        # they do not; and "could not check" said out loud rather than implied.
        if not migration.get("event_table_parity_known", False):
            why = migration.get("event_table_parity_unknown_because") or "unreadable"
            print(f"  event parity:   unknown ({why})")
        elif migration.get("event_table_disagreements", 0):
            count = int(migration["event_table_disagreements"])
            print(
                f"  event parity:   {count} run(s) where the events and the runs"
                " table disagree"
            )
        # Across surfaces: the journal versus the saved conversation.
        if not migration.get("turn_parity_known", False):
            why = migration.get("turn_parity_unknown_because") or "unreadable"
            print(f"  turn parity:    unknown ({why})")
        else:
            disagreed = int(migration.get("turn_parity_disagreements", 0))
            joined_runs = int(migration.get("turn_parity_joined", 0))
            unjoinable = int(migration.get("turn_parity_unjoinable", 0))
            if disagreed:
                print(
                    f"  turn parity:    {disagreed} of {joined_runs} turns disagree"
                    " with the journal about how they ended"
                )
            elif joined_runs:
                print(f"  turn parity:    {joined_runs} turns agree with the journal")
            if unjoinable:
                # Not a failure: these were saved before a turn recorded the
                # journal run that produced it, so there is no key to join on.
                print(f"  unjoinable:     {unjoinable} saved turn(s) predate run ids")
            journal_shape = migration.get("turn_outcomes_journal") or {}
            chat_shape = migration.get("turn_outcomes_conversations") or {}
            if journal_shape and chat_shape and journal_shape != chat_shape:
                print(f"  outcomes (lead): journal {journal_shape}")
                print(f"                   chats   {chat_shape}")
        print(f"  backups:        {backup.get('backups', 0)}", end="")
        print(f" (latest {backup['latest']})" if backup.get("latest") else "")
        return 0

    if action == "backup":
        if not journal_store.journal_path(root).exists():
            print("no runtime journal for this project yet; nothing to back up")
            return 1
        record = journal_backup.create_backup(root)
        if record is None:
            print("could not take a verified backup (see doctor for journal health)")
            return 1
        removed = journal_backup.prune_backups(
            root, keep=int(getattr(args, "keep", journal_backup.DEFAULT_KEEP))
        )
        if as_json:
            print(json.dumps({**record.to_dict(), "pruned": len(removed)}, indent=2))
            return 0
        print(f"backed up to {record.path}")
        print(
            f"  runs: {record.row_counts.get('runs', 0)}  digest: {record.digest[:12]}"
        )
        if removed:
            print(f"  pruned {len(removed)} older backup(s)")
        return 0

    if action == "pending":
        from vestahub import journal_liveness, journal_operations, journal_runtime

        runs = journal_runtime.unterminated_runs(root)
        operations = journal_operations.unreconciled_operations(root)
        if as_json:
            print(json.dumps({"runs": runs, "operations": operations}, indent=2))
            return 0
        if not runs and not operations:
            print("nothing unfinished: every run ended and every operation reconciled")
            return 0
        for entry in runs:
            held = "lease held" if entry["lease_held"] else "no lease"
            print(
                f"run {entry['run_id']}  attempt {entry['attempt']}  "
                f"{entry['observed_state']}  {held}  since {entry['created_at']}"
            )
            # #818: the lease now names a process, so this line can say who has
            # it rather than leaving the reader to go and find out.
            print(f"    {journal_liveness.describe(entry['owner_liveness'])}")
        for entry in operations:
            print(
                f"operation {entry['operation_key']}  {entry['kind']}  "
                f"since {entry['created_at']}"
            )

        # Reported rather than concluded. A pid that is gone is conclusive; the
        # rest are not, and they are not-conclusive for different reasons that
        # ask different things of the reader -- so each is counted and
        # explained on its own, never lumped under one "cannot verify" whose
        # explanation is only true of some of them (#818 review finding 14).
        def owners(verdict: str) -> list[dict[str, object]]:
            return [entry for entry in runs if entry["owner_liveness"] == verdict]

        stale = owners(journal_liveness.OWNER_STALE)
        unverified = owners(journal_liveness.OWNER_UNVERIFIED)
        unrecorded = owners(journal_liveness.OWNER_UNKNOWN)
        if stale:
            print(
                f"\n{len(stale)} run(s) had an owner that stopped responding. "
                "It may be stuck, or busy with something that reports nothing. "
                "Vesta will not end them for you, because a run that is merely "
                "quiet may still be working."
            )
        if unverified:
            print(
                f"\n{len(unverified)} run(s) have an owner Vesta cannot verify. "
                "Their process id is still in use, but ids get reused, so it may "
                "belong to something else entirely; Vesta will not call that work "
                "finished or abandoned."
            )
        if unrecorded:
            print(
                f"\n{len(unrecorded)} run(s) never recorded which process owned "
                "them (they predate that record, or this system would not say). "
                "With nothing to check, Vesta will not call them finished or "
                "abandoned."
            )
        return 0

    if action == "compact":
        from vestahub import journal_retention

        report = journal_retention.compact(
            root,
            now=_iso_now_for_journal(),
            presentation_days=int(
                getattr(args, "days", journal_retention.DEFAULT_PRESENTATION_DAYS)
            ),
            reclaim=not bool(getattr(args, "no_reclaim", False)),
        )
        if as_json:
            print(json.dumps(report.to_dict(), indent=2))
            return 0
        print(report.detail)
        if report.removed_events:
            print(
                f"  removed {report.removed_events} presentation event(s) "
                f"across {report.runs_touched} run(s)"
            )
            print(f"  reclaimed {report.reclaimed_bytes} byte(s)")
        print(f"  kept {report.kept_audit_critical} audit-critical event(s)")
        return 0

    if action == "backups":
        records = journal_backup.list_backups(root)
        if as_json:
            print(json.dumps([record.to_dict() for record in records], indent=2))
            return 0
        if not records:
            print("no backups for this project")
            return 0
        for record in records:
            runs = record.row_counts.get("runs", 0)
            print(f"{record.created_at}  runs={runs:<6} {record.path}")
        return 0

    if action == "restore":
        source = Path(str(getattr(args, "backup", "") or "")).expanduser()
        if not getattr(args, "yes", False):
            print(
                f"this replaces the runtime journal at {journal_store.journal_path(root)}"
            )
            print(f"with {source}")
            print(
                "re-run with --yes to proceed (the replaced journal is backed up first)"
            )
            return 2
        report = journal_backup.restore_backup(root, source)
        if as_json:
            print(json.dumps(report.to_dict(), indent=2))
            return 0 if report.ok else 1
        if not report.ok:
            print(f"refused: {report.reason} -- {report.detail}")
            return 1
        print(report.detail)
        if report.replaced_backup:
            print(f"  the replaced journal was kept at {report.replaced_backup}")
        return 0

    print(f"unknown journal command: {action}")
    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    from vesta.model_registry import catalog as model_catalog
    from vesta.model_registry import validate as validate_model
    from vestahub.local_models import discover_local_models
    from vestahub.loader import registry_items
    from vestahub.validator import validate_all

    root = _project(args.project)
    status = project_status(root)
    clients = status["client_integrations"]
    summary = clients["summary"]
    stale = status["stale_paths"]
    validation = validate_all(root)
    journal = _journal_doctor(root)
    launchers = _launcher_doctor()
    readiness = (
        "ready"
        if (
            not summary["broken"]
            and not summary["missing"]
            and stale["ok"]
            and not _journal_needs_attention(journal)
            # A dead desktop icon is not a footnote. Every other surface can be
            # perfectly healthy while the way the user actually opens Vesta does
            # nothing at all, so an unstartable launcher has to reach the
            # top-line verdict or doctor is reporting on a machine it did not
            # check.
            and not _launchers_need_attention(launchers)
        )
        else "attention"
    )
    payload = {
        **surface_identity_payload(brand=__brand__),
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
        "updater": _update_doctor(root),
        # #613 AC10: database health, migration status and degraded
        # integrity are visible here rather than only to the store.
        "runtime_journal": journal,
        # What the installed launchers will really spawn, read from the
        # launchers themselves rather than assumed from this process.
        "launchers": launchers,
        "next_steps": [
            "Run vesta activate --repair to fix broken or missing client integrations.",
            "Restart AI clients after global skill changes.",
            'Run vesta route "<task>" --record to populate the savings ledger.',
        ],
    }
    print_json(payload)
    return 0


def _launcher_doctor() -> dict[str, object]:
    """What the installed launchers will spawn, or why that is unknown.

    Doctor runs when something is already wrong, so this degrades rather than
    raises -- but it degrades to ``available: False``, never to a claim of
    health. "I could not check the launchers" and "the launchers are fine"
    are different answers and only one of them is reassuring.
    """

    try:
        from vestahub import launcher_health
    except Exception as exc:  # noqa: BLE001 - doctor never raises
        return {
            "available": False,
            "healthy": False,
            "reason": type(exc).__name__,
            "launchers": [],
        }
    try:
        reports = launcher_health.inspect_launchers()
        payload = dict(launcher_health.summary(reports))
    except Exception as exc:  # noqa: BLE001 - doctor never raises
        return {
            "available": False,
            "healthy": False,
            "reason": type(exc).__name__,
            "launchers": [],
        }
    payload["launchers"] = [
        {
            "name": report.name,
            "status": report.status,
            "interpreter": report.interpreter,
            "windowed": report.windowed,
            "detail": report.describe(),
        }
        for report in reports
    ]
    return payload


def _launchers_need_attention(launchers: dict[str, object]) -> bool:
    """True only when a launcher was read and found unstartable.

    An unreadable or uncheckable install is *not* treated as broken here:
    doctor already reports it as unavailable, and turning "I could not look"
    into a red verdict would be the same overconfidence in the other
    direction.
    """

    if not launchers.get("available"):
        return False
    return bool(launchers.get("broken"))


def _journal_migration(root: Path) -> dict[str, object]:
    """How far this installation has moved onto the journal.

    This used to stop at "needs_legacy_comparison", because a retirement
    verdict needs the legacy record to compare against and nothing assembled
    one. ``journal_background.legacy_runs`` now does, so the question can
    finally be asked properly and the answer is a real verdict with real
    blockers rather than a shrug.

    Everything is still best-effort. Doctor runs when things are already
    broken, so a corpus that cannot be read degrades to "no comparison
    possible" -- which ``journal_retirement`` treats as a blocker, not as
    permission.
    """

    # Every fact starts as "not checked". They used to share one suppress
    # block, so when the store refused to open -- a journal written by a newer
    # Vesta -- nothing after that point was ever set, `vesta journal status` fell
    # back to its defaults, and it printed "unfinished: 0" over a real
    # unfinished run (#818 review finding 5). Now each report stands alone and
    # a report that cannot look says so.
    facts: dict[str, object] = {
        "runs_recorded": 0,
        "runs_recorded_known": False,
        "runs_recorded_unknown_because": "not checked",
        "unreconciled_operations": 0,
        "retirement": "unknown",
        "unterminated_runs_known": False,
        "unterminated_runs_unknown_because": "not checked",
        "completed_runs_known": False,
        "cancelled_runs_known": False,
        "event_table_parity_known": False,
        "event_table_parity_unknown_because": "not checked",
        "turn_parity_known": False,
        "turn_parity_unknown_because": "not checked",
    }
    try:
        from vestahub import journal_store
    except Exception:  # noqa: BLE001 - doctor never raises
        return facts
    if not journal_store.journal_path(root).exists():
        facts["retirement"] = "not_started"
        return facts

    def count_runs() -> None:
        try:
            connection = journal_store.open_store(root)
        except Exception as exc:  # noqa: BLE001
            facts["runs_recorded_unknown_because"] = (
                "incompatible"
                if journal_store.written_by_a_newer_vesta(root)
                else journal_store.describe_open_failure(exc)
            )
            return
        try:
            facts["runs_recorded"] = int(
                connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            )
            facts["runs_recorded_known"] = True
            facts["runs_recorded_unknown_because"] = ""
        finally:
            connection.close()

    def operations() -> None:
        from vestahub import journal_operations

        summary = journal_operations.operation_summary(root)
        facts["unreconciled_operations"] = int(summary.get("unreconciled", 0))

    def unfinished() -> None:
        # The other half of "what did not finish". #613 opens by describing a
        # run that "may appear active with no worker"; operations had an answer
        # for that and runs did not.
        from vestahub import journal_runtime

        pending = journal_runtime.unterminated_summary(root)
        facts["unterminated_runs"] = int(pending.get("unterminated", 0))
        facts["unterminated_runs_holding_a_lease"] = int(pending.get("lease_held", 0))
        facts["unterminated_runs_abandoned"] = int(pending.get("abandoned", 0))
        # Zero unfinished runs and "could not read the journal" are different
        # answers, and only one of them is reassuring.
        facts["unterminated_runs_known"] = bool(pending.get("available"))
        facts["unterminated_runs_unknown_because"] = str(
            pending.get("unavailable_reason") or ""
        )

    def completions() -> None:
        # #818 AC6 asks that `completed` be impossible without the required
        # evidence. It is not yet -- the store records whatever verdict a
        # caller hands it -- so the honest intermediate step is to count the
        # completions that have nothing behind them.
        from vestahub import journal_runtime

        evidence = journal_runtime.unevidenced_completions(root)
        facts["completed_runs_known"] = bool(evidence.get("available"))
        facts["completed_runs"] = int(evidence.get("completed", 0))
        facts["completed_runs_without_evidence"] = int(evidence.get("unevidenced", 0))
        # The number AC6 actually asks about. Reported separately because the
        # one above flatters: every real turn records a cost, so counting cost
        # as evidence reads as a clean bill of health for a criterion that is
        # plainly unmet.
        facts["completed_runs_without_verification"] = int(
            evidence.get("without_verification", 0)
        )

    def cancellations() -> None:
        # AC5's counterpart to AC6: a `cancelled` verdict with no phase
        # reaching `terminated` is a claim that the work stopped, with nothing
        # showing that it did.
        from vestahub import journal_runtime

        stopped = journal_runtime.unconfirmed_cancellations(root)
        facts["cancelled_runs_known"] = bool(stopped.get("available"))
        facts["cancelled_runs"] = int(stopped.get("cancelled", 0))
        facts["cancelled_runs_unconfirmed"] = int(stopped.get("unconfirmed", 0))

    def event_parity() -> None:
        # Migration step 2's parity assertion: the journal against itself.
        # The `runs` table and the `events` table are written by the same
        # calls in the same transactions, so a disagreement is the store
        # contradicting itself.
        from vestahub import journal_projections

        parity = journal_projections.run_table_parity(root, now=_iso_now_for_journal())
        facts["event_table_parity_known"] = bool(parity.get("comparable"))
        facts["event_table_parity_unknown_because"] = str(parity.get("reason") or "")
        facts["event_table_disagreements"] = int(parity.get("disagreement_count", 0))

    def turn_parity() -> None:
        # Across surfaces: does the journal agree with the saved conversation
        # about how each turn ended? Comparing those two records is what found
        # the journal filing partial turns as completed.
        from vestahub import journal_conversations

        turns = journal_conversations.turn_parity(root)
        facts["turn_parity_known"] = bool(turns.get("available"))
        facts["turn_parity_unknown_because"] = str(turns.get("reason") or "")
        joined = turns.get("joined") or {}
        facts["turn_parity_joined"] = int(joined.get("runs", 0))
        facts["turn_parity_disagreements"] = int(joined.get("disagreement_count", 0))
        facts["turn_parity_unjoinable"] = int(turns.get("unjoinable_turns", 0))
        facts["turn_parity_in_progress"] = int(turns.get("in_progress", 0))
        # Reported separately and labelled as a lead: two populations can share
        # a shape without sharing members, so this is never a join.
        facts["turn_outcomes_journal"] = dict(
            (turns.get("aggregate") or {}).get("journal") or {}
        )
        facts["turn_outcomes_conversations"] = dict(
            (turns.get("aggregate") or {}).get("conversations") or {}
        )

    def retirement() -> None:
        from vestahub import journal_background, journal_retirement

        corpus = journal_background.legacy_runs(root)
        report = journal_retirement.assess(root, corpus)
        facts["retirement"] = report.status
        facts["blockers"] = list(report.blockers)
        facts["legacy_runs"] = len(corpus)
        facts["compared_runs"] = report.compared_runs
        facts["journal_reads"] = report.journal_reads
        facts["legacy_reads"] = report.legacy_reads
        facts["detail"] = report.detail

    for name, report in (
        ("runs", count_runs),
        ("operations", operations),
        ("unfinished", unfinished),
        ("completions", completions),
        ("cancellations", cancellations),
        ("event_parity", event_parity),
        ("turn_parity", turn_parity),
        ("retirement", retirement),
    ):
        try:
            report()
        except Exception as exc:  # noqa: BLE001 - doctor never raises
            errors = facts.setdefault("report_errors", {})
            if isinstance(errors, dict):
                errors[name] = type(exc).__name__
    return facts


def _journal_backup_health(root: Path) -> dict[str, object]:
    """Whether this project has anything to recover from (#613 requirement 12).

    Absence is reported, never escalated. A project that has never taken a
    backup is not broken, and a field that shouts on every fresh install is one
    nobody reads by the time it means something.
    """

    try:
        from vestahub import journal_backup

        return journal_backup.backup_health(root)
    except Exception:  # noqa: BLE001 - doctor never raises
        return {"available": False, "error_category": "journal_backup_unavailable"}


def _journal_permissions(root: Path) -> dict[str, object]:
    """Whether the journal file is readable only by its owner (#613 security)."""

    try:
        from vestahub import journal_store

        return journal_store.permissions_health(journal_store.journal_path(root))
    except Exception:  # noqa: BLE001 - doctor never raises
        return {"checked": False, "restricted": False, "detail": ""}


def _journal_retention(root: Path) -> dict[str, object]:
    """What retention would remove, without removing it (#613 requirement 5)."""

    try:
        from vestahub import journal_retention

        return journal_retention.retention_health(root)
    except Exception:  # noqa: BLE001 - doctor never raises
        return {"available": False}


def _journal_doctor(root: Path) -> dict[str, object]:
    """Runtime-journal health for doctor (#613 AC10, functional requirement 12).

    Absence is deliberately *not* a problem. Nothing reads from the journal
    yet, so a project that has never opened one is healthy rather than broken.
    Reporting "attention" for every project that simply has not started using
    it would train people to ignore this field long before it means anything --
    and the one time it does mean something is exactly when that habit costs.

    Only a store that exists *and* cannot be trusted is escalated. Like
    :func:`_update_doctor`, this never raises: doctor's job is to report a
    problem, not to become one.
    """

    try:
        from vestahub.journal_store import reading_only
    except Exception:  # noqa: BLE001 - doctor reports a stable safe category
        return {
            "schema_version": 1,
            "available": False,
            "error_category": "journal_unavailable",
        }
    # Doctor looks; it does not upgrade. Every report below reaches the store
    # through open_store, which inside this block refuses to migrate rather
    # than doing it as a side effect (#818 review finding 16).
    with reading_only():
        return _journal_doctor_payload(root)


def _journal_doctor_payload(root: Path) -> dict[str, object]:
    try:
        from vestahub.journal_store import (
            SCHEMA_VERSION,
            compatibility_version,
            store_health,
        )

        health = store_health(root)
        return {
            "schema_version": 1,
            "available": True,
            # What a healthy journal from this build is stamped with. Not the
            # newest migration: a migration older builds can ignore does not
            # raise the stamp (journal_store._OLDER_BUILDS_CAN_IGNORE).
            "expected_store_version": compatibility_version(),
            "newest_store_version": SCHEMA_VERSION,
            # #613 Stages 6-7: how far this installation has actually got.
            # Without it the migration is only observable by writing code, and
            # a migration nobody can see the state of is one nobody can finish.
            "migration": _journal_migration(root),
            # #613 functional requirement 12 asks for backup *and* health
            # checks in doctor. Health shipped first; this is the other half,
            # and it answers the only question that matters after a corrupt
            # store: is there anything to recover from.
            "backup": _journal_backup_health(root),
            # #613 security requirement: least-privilege database permissions.
            # Checked rather than re-applied, because the case that matters is
            # a database restored, copied or synced in from elsewhere carrying
            # whatever permissions it had there.
            "permissions": _journal_permissions(root),
            # #613 functional requirement 5. A dry run, never a prune: deciding
            # when to delete a user's history is not a diagnostic command's
            # business, but telling them it is accumulating is.
            "retention": _journal_retention(root),
            **health,
        }
    except Exception:  # noqa: BLE001 - doctor reports a stable safe category
        return {
            "schema_version": 1,
            "available": False,
            "error_category": "journal_unavailable",
        }


def _journal_needs_attention(journal: dict[str, object]) -> bool:
    """True only when a journal that *exists* is not trustworthy.

    Split from :func:`_journal_doctor` so the readiness rule is testable on
    its own and cannot drift from the payload it is derived from.
    """

    if not journal.get("available") or not journal.get("present"):
        return False
    # A journal Vesta cannot open is the loudest problem there is, and it is
    # invisible to an integrity check: the file can be structurally perfect
    # while every write is silently discarded. `openable` is absent on payloads
    # from older builds, so its default is the non-escalating one.
    if journal.get("openable") is False:
        return True
    integrity = journal.get("integrity")
    if not isinstance(integrity, dict):
        return True
    return integrity.get("state") not in {"complete", "degraded"}


def _update_doctor(root: Path) -> dict[str, object]:
    try:
        from vesta.update.factory import create_update_service

        return create_update_service(workspaces=[root]).doctor()
    except Exception:  # noqa: BLE001 - doctor reports a stable safe category
        return {
            "schema_version": 1,
            "available": False,
            "error_category": "updater_unavailable",
        }


def cmd_support_bundle(args: argparse.Namespace) -> int:
    """Preview or write a bounded, redacted diagnostic bundle (#551).

    Prints the bundle by default so the user can inspect exactly what would
    be shared before anything touches disk; ``--out`` writes it to a file
    instead. Every source this composes (audit events, ledger events) was
    already redacted at write time -- no raw prompts, command output, or
    credentials are ever included.
    """
    from vestahub.atomic_io import atomic_write_text
    from vestahub.support_bundle import build_support_bundle

    root = _project(args.project)
    bundle = build_support_bundle(root)
    bundle.update(surface_identity_payload(brand=__brand__))
    out = getattr(args, "out", None)
    if not out:
        print_json(bundle)
        return 0
    target = Path(out).expanduser().resolve()
    atomic_write_text(
        target, json.dumps(bundle, indent=2, sort_keys=True, default=str) + "\n"
    )
    print_json(
        {"status": "written", "path": str(target), "bytes": target.stat().st_size}
    )
    return 0


def _emit_update(payload: dict, args: argparse.Namespace) -> None:
    """Print the canonical payload, as JSON or as sentences.

    `--json` was declared on every update subcommand and read by none of them,
    so the flag promised a choice and the command always printed JSON. A wall
    of JSON cannot answer "why did it not notice the update?" for a person,
    which is the question these surfaces exist for.

    The human rendering is a formatter over the same payload -- it recomputes
    nothing, so the two outputs cannot disagree.
    """

    from vesta.update.report import render_status_lines

    if bool(getattr(args, "json", False)):
        print_json(payload)
        return
    verbose = str(getattr(args, "update_command", "") or "") == "doctor"
    for line in render_status_lines(payload, verbose=verbose):
        print(line)


def cmd_update(args: argparse.Namespace) -> int:
    """Operate the canonical updater used by the desktop and doctor."""
    from vesta.update.errors import UpdateError
    from vesta.update.adapters import DeveloperGitUpdateAdapter
    from vesta.update.factory import create_update_service
    from vesta.update.models import UpdateState

    root = _project(getattr(args, "project", None))
    service = create_update_service(workspaces=[root])
    command = str(getattr(args, "update_command", None) or "status")
    try:
        if command == "developer-git":
            if not isinstance(service.adapter, DeveloperGitUpdateAdapter):
                raise UpdateError("developer_update_requires_source_checkout")
            payload = (
                service.adapter.apply_source(force=bool(getattr(args, "force", False)))
                if bool(getattr(args, "apply", False))
                else service.adapter.check_source(force=True)
            )
            payload = {"schema_version": 1, "developer_source_update": True, **payload}
        elif command == "status":
            service.reconcile_native_result()
            payload = service.status()
        elif command == "doctor":
            service.reconcile_native_result()
            payload = service.doctor()
        elif command == "check":
            operation = service.check(force=not bool(getattr(args, "cached", False)))
            payload = service.status()
        elif command == "download":
            operation = service.store.load_operation()
            if operation.state in {
                UpdateState.IDLE,
                UpdateState.UP_TO_DATE,
                UpdateState.UNAVAILABLE,
            }:
                operation = service.check(force=True)
            if operation.state in {UpdateState.AVAILABLE, UpdateState.FAILED_RETRIABLE}:
                service.download(operation.operation_id)
            payload = service.status()
        elif command == "install":
            operation = service.store.load_operation()
            mode = (
                "when_idle"
                if bool(getattr(args, "when_idle", False))
                else "on_quit"
                if bool(getattr(args, "on_quit", False))
                else "now"
            )
            service.install(operation.operation_id, mode=mode)
            payload = service.status()
        elif command == "rollback":
            operation = service.store.load_operation()
            service.rollback(operation.operation_id)
            payload = service.status()
        else:
            raise UpdateError("update_command_invalid")
    except UpdateError as exc:
        payload = {
            "schema_version": 1,
            "ok": False,
            "error_category": exc.category,
            "retriable": exc.retriable,
            "status": service.status(),
        }
        _emit_update(payload, args)
        return 2
    _emit_update(payload, args)
    state = str(payload.get("operation", {}).get("state", ""))
    if command == "check" and state == UpdateState.AVAILABLE.value:
        return 3
    if state in {
        UpdateState.UNAVAILABLE.value,
        UpdateState.FAILED_RETRIABLE.value,
        UpdateState.FAILED_TERMINAL.value,
        UpdateState.NEEDS_ATTENTION.value,
    }:
        return 2
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    from vesta.integrations import uninstall_vesta

    result = uninstall_vesta(
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
                "vesta_status": "Using Vesta",
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
                "error": safe_detail(exc),
                "vesta_status": "Using Vesta",
            }
        )
        return 126


# --------------------------------------------------------------------------- #
# Recursion guard (F12): provider CLIs spawned by Vesta carry VESTA_AGENT_SESSION
# in their environment (see vestahub.proc). When an agent follows an
# instruction-file recipe like "run `vesta route ...`", the nested Vesta process
# must refuse instead of recursing into another agent run. Agentic subcommands
# check this guard; pure utility/hook subcommands must keep working inside an
# agent session.
# --------------------------------------------------------------------------- #
_NESTED_SESSION_REFUSAL = (
    "Vesta is already running inside a Vesta agent session; "
    "recursive self-invocation is disabled."
)


def _nested_agent_session_active() -> bool:
    return bool(os.environ.get(AGENT_SESSION_ENV))


def _refuse_if_nested_agent_session() -> int | None:
    """Exit code when invoked from inside a Vesta agent session, else None."""
    if _nested_agent_session_active():
        print(_NESTED_SESSION_REFUSAL, file=sys.stderr)
        return 2
    return None


# --------------------------------------------------------------------------- #
# Claude Code PreToolUse hook gate (F23). AccountRunner wires this subcommand
# into `claude` Full Auto runs via a generated --settings file (see
# vestahub.accounts.build_claude_hook_settings); the claude CLI then executes
# it for every Bash tool call. It must be fast, deterministic, and must never
# be blocked by the recursion guard above — it runs *inside* agent sessions.
# --------------------------------------------------------------------------- #
_HOOK_SHELL_TOOLS = frozenset({"bash", "shell", "sh", "powershell", "pwsh", "cmd"})

_HOOK_BLOCK_REASON = (
    "Vesta safety gate: this command is classified as destructive or "
    "confirmation-only ({detail}). It is blocked in autonomous runs and Vesta "
    "will not run it for you. To proceed, run it yourself in a terminal. Do not "
    "retry it or work around the block; continue with safe, read-only steps only."
)

# git push is a common, legitimate next step, so its block must point at the
# real control instead of implying a per-command approval dialog that does not
# exist (Bug 2): pushes are enabled once, in Settings, and then Vesta performs
# them through its own consent-aware GitHub tool — never as a raw shell push.
# Round 2: only say this when the control is actually still off. When consent is
# already granted the sentence was actively harmful — it sent users hunting for
# an "Enable pushes & PRs" button that, once enabled, reads "Disable pushes &
# PRs" — so this text is reserved for the not-yet-enabled case; a consented push
# goes to the per-push approval card instead (see ``vestahub.command_consent``).
_HOOK_BLOCK_REASON_PUSH = (
    "Vesta safety gate: pushing is not enabled yet, so Vesta will not run this "
    "`git push` ({detail}). Enable it once in Settings -> Providers & "
    'Connections, in the "GitHub · pushes & pull requests" card: connect a '
    'GitHub token, then click "Enable pushes & PRs". After that Vesta can push '
    "this branch itself. Or push yourself in a terminal. Do not retry this push "
    "until it is enabled; continue with safe, read-only steps only."
)

# A push Vesta still refuses even with consent: force/mirror/delete forms rewrite
# or destroy remote history, which consent to "push branches and open PRs" does
# not cover. Say exactly that instead of pointing at a toggle that is already on.
_HOOK_BLOCK_REASON_FORCE_PUSH = (
    "Vesta safety gate: pushes are enabled, but this is a force/delete/mirror "
    "push ({detail}), which rewrites or removes remote history. Vesta never runs "
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
    "Vesta safety gate: pushing is enabled, but each push needs the user's "
    "one-time approval ({detail}). Vesta has recorded this exact command and will "
    "ask them to approve it as soon as this turn ends. Stop here and report that "
    "the push is awaiting their approval. Do NOT retry the push, do not try "
    "another way to push, and do not claim the branch was pushed."
)

_PUSH_COMMAND = re.compile(r"\bgit\s+push\b", re.IGNORECASE)

# Outward-facing GitHub writes that a user can sensibly approve once, and that
# Vesta should therefore *ask* about rather than refuse outright.
#
# `gh pr create` was previously classified as destructive/confirmation-only with
# no consent channel, so a finished branch could be pushed and then hit a hard
# wall: the model was told to stop and not work around it, and the user was left
# to open the PR by hand. Nothing about opening a pull request is irreversible --
# it is a request for review on a branch that already exists -- so it belongs in
# the same one-shot approval channel as `gh pr comment`, not in the same class as
# a force push.
#
# Deliberately an allowlist of outward-facing verbs a user can sensibly approve
# one at a time. Anything that closes, deletes, or changes repository settings
# stays out: those are not "ask once", they are decisions the user makes
# themselves.
#
# `pr merge` is in the list. It was excluded on the reasoning that merging is a
# decision the user makes -- but excluding it did not put the decision in their
# hands, it removed the option: Vesta hit a hard refusal and told the user to go
# run it themselves. The user's decision is exactly what the approval card is,
# so a merge now asks instead of dead-ending. Every merge still shows its own
# card; approval is never inherited from an earlier one.
_APPROVABLE_GH_COMMAND = re.compile(
    r"^\s*gh(?:\.exe)?\s+(?:"
    r"pr\s+(?:create|comment|ready|merge)"
    r"|issue\s+(?:create|comment)"
    r")(?:\s|$)",
    re.IGNORECASE,
)
_SHELL_OPERATORS = re.compile(r"[|&;<>`]|\$\(|\$\{")

# Providers issue essentially every command as `cd "<repo>" && <real command>`,
# so anchoring the allowlist at the start of the string made the approval
# channel unreachable in practice: a chained `gh pr create` matched nothing,
# fell through to the destructive/confirmation-only refusal, and the user was
# told to open the PR by hand. `git push` never had this problem because its
# detection searches the string rather than anchoring to it -- the asymmetry,
# not the anchoring, was the defect.
#
# Exactly one leading `cd <dir> &&` is removed, and the directory itself may not
# contain a shell operator, so nothing new can be smuggled in: the remainder is
# still required to *start* with an allowlisted verb and to carry no operators
# of its own.
_LEADING_CD = re.compile(
    r"^\s*cd\s+(?:\"[^\"]*\"|'[^']*'|[^\s&|;<>`$]+)\s*&&\s*",
    re.IGNORECASE,
)


def _without_leading_cd(command: str) -> str:
    """Drop one leading ``cd <dir> &&`` prefix, if present."""

    return _LEADING_CD.sub("", str(command or ""), count=1)


#: Why each approvable command is outward-facing, in the user's terms. Keyed by
#: the `gh` subcommand so the approval card can say what will actually happen
#: instead of "a command needs approval".
_APPROVABLE_GH_REASONS = (
    (
        "pr create",
        "Opening a pull request asks your collaborators to review this branch.",
    ),
    ("pr comment", "Posting this comment changes the pull request conversation."),
    ("pr ready", "Marking this pull request ready requests review from collaborators."),
    ("pr merge", "This updates the default branch for everyone on the repository."),
    (
        "issue create",
        "Creating an issue is visible to everyone with repository access.",
    ),
    ("issue comment", "Posting this comment changes the issue conversation."),
)


def _approvable_gh_reason(command: str) -> str:
    """The user-facing sentence for an approvable GitHub command."""

    text = " ".join(str(command or "").split()).lower()
    for prefix, reason in _APPROVABLE_GH_REASONS:
        if f"gh {prefix}" in text or f"gh.exe {prefix}" in text:
            return reason
    return "This command performs an outward-facing GitHub action."


_HOOK_BLOCK_REASON_COMMAND_APPROVAL = (
    "Vesta safety gate: this outward-facing command needs the user's one-time "
    "approval ({detail}). Vesta has recorded this exact command and will ask "
    "them to approve it as soon as this turn ends. Stop here and report that "
    "the command is awaiting approval. Do NOT retry it, do not try another way "
    "to perform the action, and do not claim it completed."
)


def _is_plain_push(command: str) -> bool:
    """True for a lone, non-force ``git push`` to a named remote, nothing else."""

    from vestahub.command_consent import is_plain_push

    return is_plain_push(command)


def _is_approvable_gh_command(command: str) -> bool:
    """True for a direct, unchained GitHub command a user can approve once.

    The provider hook receives the entire shell string, including quoted PR and
    comment bodies. Matching anywhere in that string would treat ordinary prose
    as a command, so this stays deliberately narrow: the *executable* must be
    one of the allowlisted `gh` verbs and the string must carry no shell
    operators. A body containing `&&` or a backtick keeps the command out of the
    approval channel rather than letting it smuggle a second command through an
    approval.
    """

    text = _without_leading_cd(command)
    return bool(_APPROVABLE_GH_COMMAND.match(text)) and not bool(
        _SHELL_OPERATORS.search(text)
    )


def _push_consent_state() -> tuple[bool, str]:
    """Whether the user has already granted Vesta push consent, and why not.

    Consent is the same persisted pair the GUI toggle and Vesta's own
    ``git_push`` tool read: ``vesta github allow-push on`` plus a connected
    token. Fails closed — any lookup problem is treated as "not consented" so
    the gate can only ever become stricter on error.
    """
    try:
        from vestahub.github_connector import push_allowed, stored_github_token

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
        from vestahub.command_consent import is_history_rewriting_push

        # Judge the command, not the consent flag. This previously read
        # "consent is on" as "therefore it must be a force push", so with
        # pushing enabled an ordinary `git push origin my-branch` was told it
        # "rewrites or removes remote history" and that no consent could ever
        # unlock it -- false, and a dead end the user could not clear.
        if is_history_rewriting_push(text):
            return _HOOK_BLOCK_REASON_FORCE_PUSH.format(detail=detail)
        consented, _ = _push_consent_state()
        if consented:
            # Enabled, safe shape, but not approved yet: the one block with a
            # way forward, so point at the approval card rather than at a
            # toggle that is already on.
            return _HOOK_BLOCK_REASON_PUSH_APPROVAL.format(detail=detail)
        return _HOOK_BLOCK_REASON_PUSH.format(detail=detail)
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
    """Map a Claude Code PreToolUse payload to a Vesta gate decision.

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
    Neither is honest. ``vestahub.command_consent`` supplies the missing channel:
    an unapproved push is refused *and recorded*, so the pipeline can raise the
    GUI's approval card, and "Approve once" arms the one-shot grant consumed
    here. Force/delete/mirror pushes and anything chained onto a push stay
    denied outright — no approval unlocks those.
    """
    from vestahub import command_consent
    from vestahub.safety_gates import is_destructive_command
    from vestahub.sandbox import classify_command

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
    # The autonomy level this run was spawned with, published by
    # vestahub.proc.provider_child_env. This hook used to have no idea what mode
    # it was serving, so it demanded a one-shot approval for every push and
    # `gh pr create` even under Full Auto -- and since a PR can only follow a
    # push, an account run could never finish "push and open a pull request".
    # Bypass means the user asked for no prompts; honouring that here is what
    # makes the mode mean the same thing on the account path as in-process.
    from vestahub.command_policy import BLOCK, BYPASS, decide_command, normalize_autonomy

    autonomy = normalize_autonomy(os.environ.get("VESTA_AUTONOMY"))
    if autonomy == BYPASS:
        return _hook_allow()
    # Plan mode is read-only by construction, so a write is refused outright
    # rather than offered as a confirmation. Without this the hook fell through
    # to the risky-command rules, which have nothing to say about `git commit`
    # -- so plan mode happily committed, which is not what the mode promises
    # and not what the same word means in Claude Code.
    policy = decide_command(command, autonomy=autonomy)
    if policy.action == BLOCK:
        return _hook_deny(
            f"Vesta is in {autonomy} mode, which cannot change anything: "
            f"{policy.reason}. Switch to a mode that allows edits, or ask for "
            "a plan instead. Do not retry this command."
        )
    # These GitHub commands are outward-facing, but an explicit one-shot
    # approval is the right boundary — a terminal destructive block leaves the
    # requested action impossible to complete through the GUI, which is exactly
    # what happened to `gh pr create`: a finished, pushed branch with no way to
    # open its pull request. Check the actual invoked command before scanning
    # broader policy text so a quoted ``git push`` inside a PR body cannot be
    # mistaken for a push operation.
    if _is_approvable_gh_command(command):
        if command_consent.consume_grant(command):
            return _hook_allow()
        why = _approvable_gh_reason(command)
        command_consent.record_pending(command, why)
        return _hook_deny(
            _HOOK_BLOCK_REASON_COMMAND_APPROVAL.format(detail=why.rstrip("."))
        )
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
        from vestahub.ledger import record_route_decision
        from vestahub.router import route_context_sizes

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
        from vestahub.runs import record_run

        record_run(root, full_decision)
        recorded = {
            "tier": event["model_tier"],
            "estimated_savings_usd": event["estimated_savings_usd"],
            "cloud_call_avoided": event["cloud_call_avoided"],
            "context_chars_saved": event["context_chars_saved"],
            "cache_hit": event["cache_hit"],
            "ledger": ".vestahub/ledger/usage.jsonl",
        }
        if isinstance(output, dict):
            output = {**output, "recorded": recorded}
    print_json(output)
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    from vestahub.runs import explain_route, render_why_markdown

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
        from vesta.cli_stream import stream_ask

        return stream_ask(
            root,
            args.task,
            model=args.model,
            mode=getattr(args, "mode", None) or "ask",
            json_out=getattr(args, "json", False),
        )
    from vestahub.ask import render_ask, run_ask

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
    # canonical mapping, so `vesta ask` and the streaming path agree and a script
    # can tell a timeout from a refusal.
    from vestahub.run_state import exit_code_for

    answered = result["status"] in {"answered_locally", "cache_hit"}
    return exit_code_for("completed" if answered else "failed")


def cmd_context(args: argparse.Namespace) -> int:
    from vestahub.context_pack import build_context_pack

    root = _project(args.project)
    if args.context_command == "pack":
        pack = build_context_pack(root, changed_only=not args.all, write=args.write)
        print_json(pack)
        return 0
    if args.context_command == "profile":
        from vestahub.context_engine import profile_context, render_profile_markdown

        profile = profile_context(root)
        if getattr(args, "markdown", False):
            print(render_profile_markdown(profile))
        else:
            print_json(profile)
        return 0
    if args.context_command == "ignores":
        from vestahub.context_engine import generate_client_ignores

        clients = (
            [c.strip() for c in args.clients.split(",") if c.strip()]
            if getattr(args, "clients", None)
            else None
        )
        print_json(generate_client_ignores(root, clients))
        return 0
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    from vestahub.test_select import select_tests

    root = _project(args.project)
    selection = select_tests(root)
    if getattr(args, "run", False):
        if not selection["targeted_command"]:
            print_json({"status": "no_targeted_tests", **selection})
            return 0
        from vestahub.command_runner import run_policy_command

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
    from vestahub.share import build_savings_card, render_share_markdown

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
    from vestahub.metrics import build_local_metrics

    root = _project(args.project)
    metrics = build_local_metrics(root)
    # Merge in client readiness (kept in the vesta package to avoid a cycle).
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
    from vestahub.router import route_task
    from vestahub.savings import build_savings_report
    from vestahub.share import build_savings_card

    root = _project(args.project)
    steps: list[dict[str, Any]] = []

    activation = activate_project(root, install_global=not args.project_only)
    steps.append({"step": "activate", "status": activation.get("status")})

    sample_task = args.task or "show git status and summarize the diff"
    decision = route_task(root, sample_task, persist_cache=True)
    from vestahub.ledger import record_route_decision
    from vestahub.router import route_context_sizes

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
            "report": "vesta-quickstart",
            "project_root": str(root),
            "steps": steps,
            "savings_headline": report["headline"],
            "share_badge_markdown": card["badge"]["markdown"],
            "next_steps": [
                'Run more tasks with: vesta route "<task>" --record',
                "See the full report: vesta savings --markdown",
                "Share your savings: vesta share --markdown",
                "Check readiness anytime: vesta doctor",
            ],
        }
    )
    return 0


def cmd_savings(args: argparse.Namespace) -> int:
    from vestahub.savings import build_savings_report, render_savings_markdown

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
        from vestahub.ledger import rollup_ledger

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

    ``vesta release preflight`` assembles every release check into one
    deterministic readiness verdict from a clean checkout and exits non-zero
    when anything blocks. ``vesta release rollback`` prints (or, with --execute,
    performs) the steps to restore the previous tested artifact without touching
    user state.
    """
    from vestahub import release_preflight as rp

    root = _project(args.project)
    command = getattr(args, "release_command", None)

    if command == "preflight":
        ctx = rp.ReleaseContext(
            root=root,
            dry_run=not getattr(args, "execute", False),
            run_tests=getattr(args, "run_tests", False),
            qualification_required=getattr(args, "qualification_required", False),
            source_only=getattr(args, "source_only", False),
            candidate_sha=getattr(args, "candidate_sha", None),
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
            print_json({"status": "error", "message": safe_detail(exc)})
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
            print_json({"status": "error", "message": safe_detail(exc)})
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
    from vesta.gui_web import _resume_payload

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
    if thread.get("state") == "running":
        # #545: which surface owns this run, so a second terminal (or the GUI)
        # can tell "still active elsewhere" from "abandoned" before offering
        # to touch it. The JSON form already carried this; markdown did not.
        owner = payload.get("owner") or {}
        if owner.get("ownerIsThisProcess"):
            lines.append("- Owner: this process")
        elif owner.get("reason") == "owner_alive_elsewhere":
            lines.append("- Owner: another process, still active")
        else:
            silent = owner.get("silentForSeconds")
            silent_for = f", silent for {int(silent)}s" if silent is not None else ""
            lines.append(f"- Owner: none currently alive{silent_for}")
    if checkpoint:
        lines.append(
            f"- Checkpoint: {checkpoint.get('id', '')} "
            f"({checkpoint.get('completion_state', 'unknown')}, "
            f"{len(checkpoint.get('changed_files') or [])} changed file(s))"
        )
    lines.append("")
    lines.append("Open this workspace in the Vesta GUI to resume or start fresh.")
    print("\n".join(lines))
    return 0


def cmd_outcomes(args: argparse.Namespace) -> int:
    """Report task-outcome metrics (#288): cost per completed task and
    duplicate-call avoidance, reconciled to the authoritative ledger."""
    from vestahub.ledger import summarize_outcomes

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
        "# Vesta task outcomes",
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
    from vestahub.budget import budget_gate, budget_status, set_budget

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
            print_json({"status": "invalid", "error": safe_detail(exc)})
            return 2
        print_json(result)
        return 0
    if args.budget_command == "status":
        print_json(budget_status(root))
        return 0
    if args.budget_command == "panic":
        on = not args.off
        result = set_budget(root, panic=on)
        from vestahub.audit import POLICY_DENY, record_audit_event

        record_audit_event(root, POLICY_DENY if on else "panic_off", panic=on)
        print_json({"panic": on, **result})
        return 0
    if args.budget_command == "gate":
        from vestahub.cost_model import load_cost_model, tier_cost
        from vestahub.model_intelligence import recommend_model

        # Gate the escalation target (the model that *would* run this task if
        # escalated), since Vesta's local router itself never picks a paid tier.
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
    from vestahub.proxy import proxy_run

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
    from vestahub.agent_launch import PASSTHROUGH_EXIT, launch_agent

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
    from vestahub.receipt import build_receipt, render_receipt_svg, verify_receipt

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
                    "problems": [f"unreadable receipt: {safe_detail(exc)}"],
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
    from vestahub.proof import (
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
    from vestahub.benchmark import (
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
                    "message": "No benchmark history yet. Run 'vesta benchmark run --suite local --mode both'.",
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
                    "message": "No benchmark history yet. Run 'vesta benchmark run --suite local --mode both'.",
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
        from vestahub.vestabench import (
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
            print_json({"status": "error", "message": safe_detail(exc)})
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
    from vestahub.guarded import (
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
            from vestahub.audit import EVIDENCE_PACKET, record_audit_event

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
        from vestahub.guarded import verify_evidence_packet

        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        result = verify_evidence_packet(root, data)
        print_json(result)
        return 0 if result["verified"] else 1
    if args.guard_command == "action":
        result = guard_action(
            root, args.action, template_id=args.template, confirmed=args.confirm
        )
        if getattr(args, "audit", False):
            from vestahub.audit import GUARD_ALLOW, GUARD_DENY, record_audit_event

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
    from vestahub.editions import edition_summary, set_edition

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
    from vestahub.audit import export_audit, read_audit, summarize_audit, verify_chain

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
    from vestahub.team import team_report
    from vestahub.team_policy import apply_team_policy, init_team_policy

    root = _project(args.project)
    if args.team_command == "init":
        print_json(init_team_policy(root, profile=args.profile, team=args.team))
        return 0
    if args.team_command == "apply":
        result = apply_team_policy(root)
        if result.get("status") == "updated":
            from vestahub.audit import TEAM_POLICY_APPLIED, record_audit_event

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


def _models_overrides_command(args: argparse.Namespace) -> int:
    """Manage the user-owned model list (`vesta models add/hide/reset`).

    The built-in registry is a table compiled into the release, so a model a
    provider ships tomorrow is unreachable until Vesta itself is updated. These
    write `~/.vesta/models.json`, which is layered over it -- the picker, routing
    and validation all read the merged view.
    """
    from vesta.model_overrides import load_overrides, overrides_path, save_overrides

    report = load_overrides()
    if report.errors:
        # Refuse to write over a file we could not parse: the user's existing
        # entries are in there, and a blind overwrite would discard them.
        print_json(
            {
                "status": "overrides_unreadable",
                "path": str(report.path or overrides_path()),
                "errors": list(report.errors),
                "hint": "Fix or delete the file, then retry.",
            }
        )
        return 2

    providers: dict[str, list[dict[str, object]]] = {
        name: [
            {
                "id": spec.id,
                "display": spec.display,
                "full": spec.full,
                "capability": spec.capability,
                "aliases": list(spec.aliases),
            }
            for spec in specs
        ]
        for name, specs in report.models.items()
    }
    hide = {name: sorted(ids) for name, ids in report.hidden.items()}

    if args.models_command == "reset":
        target = overrides_path()
        existed = target.exists()
        target.unlink(missing_ok=True)
        print_json(
            {
                "status": "reset" if existed else "nothing_to_reset",
                "path": str(target),
            }
        )
        return 0

    provider = str(args.provider).strip().lower()
    model_id = str(args.model_id).strip()
    if not provider or not model_id:
        print_json({"status": "invalid", "error": "provider and model id are required"})
        return 2

    if args.models_command == "hide":
        hidden = set(hide.get(provider, []))
        hidden.add(model_id.lower())
        hide[provider] = sorted(hidden)
    else:
        entries = [
            e
            for e in providers.get(provider, [])
            if e["id"].lower() != model_id.lower()
        ]
        entries.append(
            {
                "id": model_id,
                "display": args.display or model_id,
                "full": args.full or args.display or model_id,
                "capability": args.capability,
                "aliases": list(args.alias or []),
            }
        )
        providers[provider] = entries
        # Adding a model back un-hides it; otherwise the add would look like a
        # no-op and the user would have no way to see why.
        if model_id.lower() in set(hide.get(provider, [])):
            hide[provider] = sorted(set(hide[provider]) - {model_id.lower()})

    try:
        path = save_overrides(providers, hide=hide)
    except (OSError, ValueError) as exc:
        print_json({"status": "write_failed", "error": safe_detail(exc)[:400]})
        return 2

    from vesta.model_registry import models_for

    print_json(
        {
            "status": "updated",
            "path": str(path),
            "provider": provider,
            "models": [
                {"id": m.id, "display": m.display, "capability": m.capability}
                for m in models_for(provider)
            ],
        }
    )
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    root = _project(args.project)
    if args.models_command == "list":
        from vesta import app_state as A
        from vestahub.gui_preferences import load_gui_preferences

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
                        "label": "Auto · Vesta routes the cheapest safe model",
                        "provider": "vesta",
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
        from vesta import app_state as A
        from vestahub.gui_preferences import save_gui_preferences

        known = {"auto"}
        known.update(option["id"] for option in A.available_models(root)["models"])
        if args.model_id not in known:
            print_json(
                {
                    "status": "unknown_model",
                    "model_id": args.model_id,
                    "hint": "Run `vesta models list` to see selectable model IDs.",
                }
            )
            return 2
        prefs = save_gui_preferences(root, {"default_model": args.model_id})
        print_json({"status": "updated", "preferences": prefs})
    elif args.models_command in {"add", "hide", "reset"}:
        return _models_overrides_command(args)
    elif args.models_command == "discover-local":
        from vestahub.local_models import discover_local_models

        print_json(discover_local_models(root))
    elif args.models_command == "onboard":
        # Guided local-model readiness (#3): detect state per runtime and,
        # optionally, prove one privacy-safe local route. Never downloads or
        # starts anything — commands are shown, not run.
        from vestahub.local_onboarding import (
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
        from vestahub.eval_harness import run_eval

        print_json(run_eval(root, write=not args.no_write))
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    from vestahub.policy import resolve_policy, set_profile

    root = _project(args.project)
    if args.policy_command == "show":
        print_json(resolve_policy(root))
        return 0
    if args.policy_command == "set":
        result = set_profile(root, args.profile)
        print_json(result)
        return 0 if result.get("status") == "updated" else 2
    if args.policy_command == "check":
        from vestahub.ci_check import run_policy_check

        result = run_policy_check(
            root, require_team_policy=getattr(args, "require_team_policy", False)
        )
        if getattr(args, "audit", False):
            from vestahub.audit import CI_CHECK, record_audit_event

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
        from vesta.visibility import write_visibility_status

        result["visibility"] = write_visibility_status(_project(args.project))
    if not args.quiet:
        print_json(result)
    return 0


def cmd_visibility(args: argparse.Namespace) -> int:
    from vesta.visibility import write_visibility_status

    if args.visibility_command == "install":
        print_json(write_visibility_status(_project(args.project)))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from vestahub.dashboard import build_dashboard
    from vestahub.dashboard_html import build_dashboard_html

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
        prog="vesta",
        description=(
            f"{current_release_identity().display_name}: "
            "local-first AI coding cost firewall."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=release_version_text(brand=__brand__),
    )
    parser.add_argument("--project", default=".", help="Project root")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("version")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser(
        "new",
        help="Scaffold a runnable app from a description (free boilerplate, then iterate with vesta build)",
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
        help="Only create Vesta local state",
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
        help="Skip automatic Superpowers install during Vesta install",
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
        help="Show Vesta activation, Superpowers, wrappers, and project state",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--human", action="store_true", help="Show the Vesta cockpit view")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "repo",
        help="Inspect canonical repository safety and isolated worktree leases",
    )
    repo_sub = p.add_subparsers(dest="repo_command", required=True)
    ri = repo_sub.add_parser(
        "inspect", help="Show redacted repository identity and safety assessment"
    )
    ri.add_argument("--project", default=None, help="Project root")
    ri.add_argument(
        "--json", action="store_true", help="Render machine-readable output"
    )
    ri.set_defaults(func=cmd_repo)
    rw = repo_sub.add_parser(
        "worktrees", help="List isolated worktree leases without cleanup"
    )
    rw.add_argument("--project", default=None, help="Project root")
    rw.add_argument(
        "--recover",
        action="store_true",
        help="Reconcile lease state and show recommended non-destructive actions",
    )
    rw.add_argument(
        "--json", action="store_true", help="Render machine-readable output"
    )
    rw.set_defaults(func=cmd_repo)

    p = sub.add_parser(
        "verify",
        help="Resolve a verification policy without running verification commands",
    )
    verify_sub = p.add_subparsers(dest="verify_command", required=True)
    vp = verify_sub.add_parser(
        "policy", help="Inspect the effective versioned verification policy"
    )
    vp.add_argument("--project", default=argparse.SUPPRESS, help="Project root")
    vp.add_argument(
        "--task", required=True, help="Task whose verification policy to resolve"
    )
    vp.add_argument("--mode", default="implement", help="Requested task mode")
    vp.add_argument(
        "--delivery",
        choices=("local", "ship"),
        default="local",
        help="Requested delivery outcome",
    )
    vp.add_argument(
        "--json", action="store_true", help="Render machine-readable policy JSON"
    )
    vp.set_defaults(func=cmd_verify)
    vr = verify_sub.add_parser(
        "run", help="Execute declared verification argv and persist evidence"
    )
    vr.add_argument("--project", default=argparse.SUPPRESS, help="Project root")
    vr.add_argument("--task", required=True, help="Task whose checks to execute")
    vr.add_argument("--mode", default="implement", help="Requested task mode")
    vr.add_argument(
        "--delivery",
        choices=("local", "ship"),
        default="local",
        help="Requested delivery outcome",
    )
    vr.add_argument("--json", action="store_true", help="Render manifest JSON")
    vr.set_defaults(func=cmd_verify)

    p = sub.add_parser(
        "journal",
        help="Inspect, back up and recover the transactional runtime journal",
    )
    journal_sub = p.add_subparsers(dest="journal_command", required=True)
    js = journal_sub.add_parser("status", help="Journal health and migration progress")
    js.add_argument("--project", default=None, help="Project root")
    js.add_argument("--json", action="store_true")
    js.set_defaults(func=cmd_journal)
    jb = journal_sub.add_parser("backup", help="Take a verified backup of the journal")
    jb.add_argument("--project", default=None, help="Project root")
    jb.add_argument(
        "--keep",
        type=int,
        default=5,
        help="How many backups to retain (older ones are pruned)",
    )
    jb.add_argument("--json", action="store_true")
    jb.set_defaults(func=cmd_journal)
    jp = journal_sub.add_parser(
        "pending",
        help="Runs that never ended and operations never reconciled",
    )
    jp.add_argument("--project", default=None, help="Project root")
    jp.add_argument("--json", action="store_true")
    jp.set_defaults(func=cmd_journal)
    jc = journal_sub.add_parser(
        "compact",
        help="Apply retention to high-volume presentation events and reclaim space",
    )
    jc.add_argument("--project", default=None, help="Project root")
    jc.add_argument(
        "--days",
        type=int,
        default=30,
        help="Keep presentation events younger than this many days",
    )
    jc.add_argument(
        "--no-reclaim",
        action="store_true",
        help="Skip VACUUM (it rewrites the database and wants a quiet moment)",
    )
    jc.add_argument("--json", action="store_true")
    jc.set_defaults(func=cmd_journal)
    jl = journal_sub.add_parser("backups", help="List verified backups, newest first")
    jl.add_argument("--project", default=None, help="Project root")
    jl.add_argument("--json", action="store_true")
    jl.set_defaults(func=cmd_journal)
    jr = journal_sub.add_parser(
        "restore", help="Replace the journal with a backup (requires --yes)"
    )
    jr.add_argument("backup", help="Path to the backup to restore")
    jr.add_argument("--project", default=None, help="Project root")
    jr.add_argument(
        "--yes",
        action="store_true",
        help="Confirm replacing the current journal; it is backed up first",
    )
    jr.add_argument("--json", action="store_true")
    jr.set_defaults(func=cmd_journal)

    p = sub.add_parser("cockpit", help="Obvious ON/OFF control panel for Vesta")
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
        help="Launch the Vesta desktop control center (native window, local-only)",
    )
    p.add_argument(
        "task",
        nargs="?",
        default=None,
        help='Optional task to pre-load the prompt with, e.g. vesta gui "fix the login bug"',
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
        help="Activate Vesta, Superpowers, and AI-client instructions for this project",
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
        help="Re-apply managed Vesta project/global integration files",
    )
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("visibility", help="Write GUI-visible Vesta status files")
    visibility_sub = p.add_subparsers(dest="visibility_command", required=True)
    vi = visibility_sub.add_parser(
        "install", help="Write VESTA_STATUS.md and .vestahub/vesta-status.json"
    )
    vi.add_argument("--project", default=None, help="Project root")
    vi.set_defaults(func=cmd_visibility)

    p = sub.add_parser("publish", help="Publish-readiness helpers")
    publish_sub = p.add_subparsers(dest="publish_command", required=True)
    pu = publish_sub.add_parser("status")
    pu.add_argument("--project", default=None, help="Project root")
    pu.add_argument(
        "--write", action="store_true", help="Write .vestahub/publish-status.json"
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
        help="Show the Vesta mascot welcome screen before launching",
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
        help="Also write Vesta project activation files before routing",
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
        help="Show the estimated AI spend Vesta saved on this project (cost firewall)",
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
        help="Include the full local qualification gate (scripts/ci_local.py --profile full)",
    )
    rp_pre.add_argument(
        "--source-only",
        action="store_true",
        help=(
            "Qualify source/RC checks only; final artifact qualification remains "
            "pending in the protected desktop workflow"
        ),
    )
    rp_pre.add_argument(
        "--artifacts", metavar="MANIFEST", help="Artifact manifest JSON to verify"
    )
    rp_pre.add_argument(
        "--qualification-required",
        action="store_true",
        help="Fail closed for every input required by the selected qualification scope",
    )
    rp_pre.add_argument(
        "--candidate-sha",
        help="Exact 40-character commit SHA that every qualification input must match",
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
        help="Route one agent call through Vesta (the inline-capture shim entrypoint)",
    )
    p.add_argument("agent", help="Agent to route through (claude, codex, or copilot)")
    p.add_argument("task", help="The task/prompt to run")
    p.add_argument(
        "--mode",
        default="ask",
        help="ask | plan | approve-edits | safe-auto | auto-edits | full-auto",
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
    br.add_argument("--mode", default="both", choices=["baseline", "vesta", "both"])
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
        "Vesta pipeline and score vs an imported baseline (#309/#314)",
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
        "why", help="Explain why Vesta chose its route for a task (read-only)"
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
        help=(
            "Run mode with --model: ask | plan | approve-edits | safe-auto"
            " | auto-edits | full-auto"
        ),
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
        "--write", action="store_true", help="Persist .vestahub/context/pack.json"
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
    ti = team_sub.add_parser("init", help="Create a committable vesta-team-policy.yaml")
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
        "set-default", help="Set the default model for Vesta GUI/account routing"
    )
    mo.add_argument("model_id")
    mo.add_argument("--project", default=None, help="Project root")
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser(
        "add",
        help="Add or override a provider model (survives Vesta updates)",
    )
    mo.add_argument("provider", help="claude, codex, copilot, or a free provider")
    mo.add_argument("model_id", help="Exactly what the provider CLI accepts")
    mo.add_argument("--display", default=None, help="Short picker label")
    mo.add_argument("--full", default=None, help="Longer diagnostic name")
    mo.add_argument(
        "--capability",
        default="balanced",
        choices=["fast", "balanced", "best", "preview"],
    )
    mo.add_argument(
        "--alias", action="append", default=None, help="Alternate id (repeatable)"
    )
    mo.add_argument("--project", default=None, help="Project root")
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser("hide", help="Hide a model a provider no longer offers")
    mo.add_argument("provider")
    mo.add_argument("model_id")
    mo.add_argument("--project", default=None, help="Project root")
    mo.set_defaults(func=cmd_models)
    mo = models_sub.add_parser("reset", help="Remove all local model overrides")
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
        help="Print the scorecard without writing .vestahub/eval/scorecard.json",
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
        help="Fail if vesta-team-policy.yaml is missing (strict team CI mode)",
    )
    po.add_argument("--project", default=None, help="Project root")
    po.set_defaults(func=cmd_policy)

    p = sub.add_parser("skills", help="Vesta skill registry helpers")
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
    p.add_argument(
        "--json",
        action="store_true",
        # cmd_doctor has always rendered a JSON payload when asked; the flag to
        # ask for it was simply never registered, so the whole machine-readable
        # report was unreachable from the command line.
        help="Render the full readiness report as JSON",
    )
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "support-bundle",
        help="Preview or write a bounded, redacted diagnostic bundle for support requests",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write the bundle to this file instead of printing it",
    )
    p.set_defaults(func=cmd_support_bundle)

    p = sub.add_parser(
        "update",
        help="Check, download, install, roll back, or diagnose signed Vesta updates",
    )
    update_sub = p.add_subparsers(dest="update_command")
    for update_name in ("status", "download", "rollback", "doctor"):
        update_parser = update_sub.add_parser(update_name)
        update_parser.add_argument("--json", action="store_true")
        update_parser.set_defaults(func=cmd_update)
    update_check = update_sub.add_parser("check")
    update_check.add_argument("--cached", action="store_true")
    update_check.add_argument("--json", action="store_true")
    update_check.set_defaults(func=cmd_update)
    update_install = update_sub.add_parser("install")
    install_mode = update_install.add_mutually_exclusive_group()
    install_mode.add_argument("--when-idle", action="store_true")
    install_mode.add_argument("--on-quit", action="store_true")
    update_install.add_argument("--json", action="store_true")
    update_install.set_defaults(func=cmd_update)
    update_developer = update_sub.add_parser(
        "developer-git",
        help="Explicitly check or update a detected developer source checkout",
    )
    update_developer.add_argument(
        "--apply",
        action="store_true",
        help="Apply the source update; without this flag the command only checks",
    )
    update_developer.add_argument(
        "--force",
        action="store_true",
        help="Allow the legacy source updater's explicit force mode",
    )
    update_developer.add_argument("--json", action="store_true")
    update_developer.set_defaults(func=cmd_update)
    p.set_defaults(func=cmd_update, update_command="status")

    p = sub.add_parser(
        "uninstall",
        help="Remove Vesta-managed blocks and wrappers (dry-run by default)",
    )
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument(
        "--confirm", action="store_true", help="Apply the removal (default is dry-run)"
    )
    p.add_argument(
        "--keep-project-files",
        action="store_true",
        help="Do not strip Vesta blocks from this project's instruction files",
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
        if name == "agents":
            agent_sub = p.add_subparsers(dest="agents_command")
            for action in (
                "list",
                "create",
                "show",
                "receipt",
                "run",
                "pause",
                "resume",
                "stop",
                "sequential",
                "budget",
                "prioritize",
                "reroute",
                "reconcile",
                "verify",
                "approve",
                "retry",
                "request-review",
            ):
                command = agent_sub.add_parser(action)
                command.add_argument(
                    "--project", default=argparse.SUPPRESS, help="Project root"
                )
                command.add_argument("--json", action="store_true")
                if action == "approve":
                    command.add_argument("--request-id", required=True)
                if action == "retry":
                    command.add_argument("--run-id", required=True)
                if action == "request-review":
                    command.add_argument("--revision", required=True, type=int)
                if action == "receipt":
                    command.add_argument(
                        "--sign",
                        action="store_true",
                        help="Sign the receipt with the local integrity key",
                    )
                if action == "create":
                    command.add_argument("objective")
                    command.add_argument("--mode", default="safe-auto")
                    command.add_argument("--model", default="auto")
                    command.add_argument("--max-parallel", type=int, default=2)
                    command.add_argument("--budget", default=None)
                    command.add_argument("--allow-cloud", action="store_true")
                    command.add_argument("--request-id", default=None)
                    command.add_argument(
                        "--run", dest="start_objective", action="store_true"
                    )
                elif action != "list":
                    command.add_argument("objective_id")
                command.add_argument("--assignment", default=None)
                command.add_argument(
                    "--value", default=None, help="Budget in USD, priority, or model ID"
                )
                command.set_defaults(func=cmd_objectives)

    p = sub.add_parser("dashboard", help="Write or serve the local Vesta dashboard")
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

    Vesta's output uses characters like ``·`` and ``✓``; on a legacy Windows
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
