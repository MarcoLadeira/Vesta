from __future__ import annotations

from pathlib import Path
from typing import Any

from .scanner import scan_project
from .utils import (
    ensure_project_dirs,
    load_json,
    now_iso,
    project_op_dir,
    run_command,
    sha256_text,
    write_json,
    write_text,
)


DEFAULT_ENABLED_AGENTS = [
    "cost-controller",
    "context-manager",
    "planning",
    "implementation",
    "broken-code-fixer",
    "testing",
    "gitops",
    "code-review",
    "security",
    "dependency",
]


def profile_path(root: Path) -> Path:
    return project_op_dir(root) / "project.json"


def context_path(root: Path) -> Path:
    return project_op_dir(root) / "context.md"


def load_profile(root: Path) -> dict[str, Any]:
    return load_json(profile_path(root), {})


def _format_list(values: list[Any]) -> str:
    if not values:
        return "- none detected"
    return "\n".join(f"- {value}" for value in values)


def _format_commands(commands: dict[str, str]) -> str:
    if not commands:
        return "- none detected"
    return "\n".join(f"- {name}: `{cmd}`" for name, cmd in sorted(commands.items()))


def _format_top_level(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- no files detected"
    return "\n".join(
        f"- `{item['path']}`: {item['files']} files" for item in items[:20]
    )


def build_context(root: Path, profile: dict[str, Any] | None = None) -> str:
    profile = profile or scan_project(root)
    git_status = profile.get("git", {}).get("status", "")
    diff_stat = run_command("git diff --stat -- .", root, timeout=20)
    recent_log = run_command("git log --oneline -5", root, timeout=20)

    sections = [
        f"# OPcoding Project Context: {profile.get('name', root.name)}",
        "",
        f"Generated: {now_iso()}",
        "",
        "## Stack",
        f"- Root: `{profile.get('root', str(root))}`",
        f"- Languages: {', '.join(profile.get('languages', [])) or 'none detected'}",
        f"- Frameworks: {', '.join(profile.get('frameworks', [])) or 'none detected'}",
        f"- Package managers: {', '.join(profile.get('package_managers', [])) or 'none detected'}",
        "",
        "## Commands",
        _format_commands(profile.get("commands", {})),
        "",
        "## Important Files",
        _format_list(profile.get("manifests", [])),
        "",
        "## Docs",
        _format_list(profile.get("docs", [])[:30]),
        "",
        "## CI And Deployment",
        _format_list(profile.get("ci", [])),
        "",
        "## Top-Level Structure",
        _format_top_level(profile.get("top_level", [])),
        "",
        "## Git Snapshot",
        "```text",
        git_status or "not a git repository or no status available",
        "```",
        "",
        "## Current Diff Stat",
        "```text",
        diff_stat.stdout.strip()
        if diff_stat.returncode == 0 and diff_stat.stdout.strip()
        else "no unstaged diff detected",
        "```",
        "",
        "## Recent Commits",
        "```text",
        recent_log.stdout.strip()
        if recent_log.returncode == 0 and recent_log.stdout.strip()
        else "no recent commits detected",
        "```",
        "",
        "## Operating Rules For Agents",
        "- Prefer local commands and cached summaries before AI.",
        "- Load diffs, logs, and small relevant files before whole directories.",
        "- Do not include secrets in prompts or logs.",
        "- Run targeted tests before full suites.",
        "- Ask before destructive git, deployment, production, or expensive model actions.",
        "",
    ]
    return "\n".join(sections)


def onboard_project(root: Path, force: bool = False) -> dict[str, Any]:
    op_dir = ensure_project_dirs(root)
    existing = load_profile(root)
    if existing and not force:
        profile = existing
    else:
        profile = scan_project(root)
        write_json(profile_path(root), profile)

    context = build_context(root, profile)
    write_text(context_path(root), context)

    enabled_path = op_dir / "enabled-agents.json"
    if force or not enabled_path.exists():
        write_json(enabled_path, {"enabled": DEFAULT_ENABLED_AGENTS})

    budget_path = op_dir / "budget.json"
    if force or not budget_path.exists():
        write_json(
            budget_path,
            {
                "daily_usd_limit": 5.0,
                "monthly_usd_limit": 50.0,
                "per_task_soft_limit_usd": 0.75,
                "per_task_hard_limit_usd": 3.0,
                "require_confirmation_for_level": "L4",
            },
        )

    mcp_path = op_dir / "mcp.profile.json"
    if force or not mcp_path.exists():
        write_json(
            mcp_path,
            {
                "enabled": [
                    "filesystem-readonly",
                    "git-local",
                    "testing-local",
                    "memory-local",
                ],
                "disabled_by_default": ["github", "browser-web", "database"],
                "roots": [str(root)],
            },
        )

    digest = sha256_text(context)
    write_json(
        op_dir / "cache" / "context-meta.json",
        {"generated_at": now_iso(), "sha256": digest, "length": len(context)},
    )
    return profile
