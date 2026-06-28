from __future__ import annotations

import argparse
import json
import os
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


def cmd_cockpit(args: argparse.Namespace) -> int:
    from opai.cockpit import build_cockpit, render_cockpit

    payload = build_cockpit(_project(args.project))
    if args.json:
        print_json(payload)
    else:
        print(render_cockpit(payload), end="")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from opai.gui_desktop import INSTALL_HINT, launch, render_screenshot, run_once

    root = _project(args.project)
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
    try:
        return int(launch(root, task=getattr(args, "task", None)))
    except Exception as exc:  # noqa: BLE001 - dependency/display failures degrade
        print_json(
            {
                "status": "gui_unavailable",
                "error": str(exc),
                "hint": INSTALL_HINT,
            }
        )
        return 1


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
    from opaihub.ask import render_ask, run_ask

    root = _project(args.project)
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
    # Exit non-zero when nothing was answered, so scripts can branch on it.
    return 0 if result["status"] in {"answered_locally", "cache_hit"} else 2


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


def cmd_budget(args: argparse.Namespace) -> int:
    from opaihub.budget import budget_gate, budget_status, set_budget

    root = _project(args.project)
    if args.budget_command == "set":
        print_json(
            set_budget(
                root,
                daily_usd=args.daily,
                monthly_usd=args.monthly,
                per_task_usd=args.per_task,
            )
        )
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


def cmd_receipt(args: argparse.Namespace) -> int:
    from opaihub.receipt import build_receipt, render_receipt_svg, verify_receipt

    root = _project(args.project)
    if getattr(args, "receipt_command", None) == "verify":
        try:
            receipt = json.loads(Path(args.file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print_json({"verified": False, "problems": [f"unreadable receipt: {exc}"]})
            return 1
        result = verify_receipt(root, receipt)
        print_json(result)
        return 0 if result["verified"] else 1

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
        if result.get("status") == "updated":
            from opaihub.audit import EDITION_CHANGE, record_audit_event

            record_audit_event(root, EDITION_CHANGE, edition=args.edition_name)
        print_json(result)
        return 0 if result.get("status") == "updated" else 2
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
        from opaihub.accounts import account_models
        from opaihub.gui_preferences import load_gui_preferences

        data = A.available_models(root)
        account_options = account_models(include_unavailable=True)
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
            }
        )
    elif args.models_command == "set-default":
        from opai import app_state as A
        from opaihub.accounts import account_models
        from opaihub.gui_preferences import save_gui_preferences

        known = {"auto"}
        known.update(
            option["id"] for option in account_models(include_unavailable=True)
        )
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
        description="OPai 0.2.0 alpha.1: local-first AI coding cost firewall.",
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

    p = sub.add_parser("cockpit", help="Obvious ON/OFF control panel for OPai")
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_cockpit)

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
    p.add_argument("--width", type=int, default=1040, help=argparse.SUPPRESS)
    p.add_argument("--height", type=int, default=720, help=argparse.SUPPRESS)
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
        help="Private, signed proof bundles for customers and team pilots",
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

    p = sub.add_parser(
        "why", help="Explain why OPai chose its route for a task (read-only)"
    )
    p.add_argument("task")
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(func=cmd_why)

    p = sub.add_parser(
        "ask",
        help="Answer a cheap task locally (local model + result cache, $0, no cloud)",
    )
    p.add_argument("task")
    p.add_argument("--project", default=None, help="Project root")
    p.add_argument("--json", action="store_true")
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
        help="Show or set the OPai open-core edition (Free/Pro/Team/Team-Governance/Enterprise)",
    )
    edition_sub = p.add_subparsers(dest="edition_command", required=True)
    ed = edition_sub.add_parser("show")
    ed.add_argument("--project", default=None, help="Project root")
    ed.set_defaults(func=cmd_edition)
    ed = edition_sub.add_parser("set")
    ed.add_argument(
        "edition_name",
        choices=["free", "pro", "team", "team-governance", "enterprise"],
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
        "list", help="List Auto, Codex, Claude, and local model choices"
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
