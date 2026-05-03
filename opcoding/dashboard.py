from __future__ import annotations

from pathlib import Path

from .cost import budget_report
from .gitops import git_summary, risky_diff_findings
from .scanner import scan_project
from .utils import now_iso, project_op_dir, write_text


def build_dashboard(root: Path) -> Path:
    profile = scan_project(root)
    budget = budget_report(root)
    git = git_summary(root)
    risks = risky_diff_findings(root)
    lines = [
        f"# OPcoding Dashboard: {profile['name']}",
        "",
        f"Generated: {now_iso()}",
        "",
        "## Project",
        f"- Files: {profile['file_count']}",
        f"- Languages: {', '.join(profile['languages']) or 'none'}",
        f"- Frameworks: {', '.join(profile['frameworks']) or 'none'}",
        "",
        "## Commands",
    ]
    lines.extend(
        f"- {name}: `{cmd}`"
        for name, cmd in sorted(profile.get("commands", {}).items())
    )
    lines.extend(
        [
            "",
            "## Cost",
            f"- Estimated used: ${budget['estimated_total_usd']}",
            f"- Remaining monthly: ${budget['remaining_monthly_usd']}",
            f"- Policy: {budget['policy']}",
            "",
            "## Git",
            "```text",
            git.get("status", ""),
            "```",
            "",
            "## Risk Notes",
        ]
    )
    if risks:
        lines.extend(f"- `{item['file']}`: {item['reason']}" for item in risks)
    else:
        lines.append("- No obvious risky diff patterns detected")
    path = project_op_dir(root) / "dashboard.md"
    write_text(path, "\n".join(lines) + "\n")
    return path
