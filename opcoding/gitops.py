from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .utils import find_secret_hits, run_command, slugify


def git_summary(root: Path, base: str | None = None) -> dict[str, Any]:
    status = run_command("git status --short --branch -- .", root, timeout=20)
    stat_cmd = f"git diff --stat {base}...HEAD -- ." if base else "git diff --stat -- ."
    name_cmd = (
        f"git diff --name-status {base}...HEAD -- ."
        if base
        else "git diff --name-status -- ."
    )
    diff_stat = run_command(stat_cmd, root, timeout=30)
    names = run_command(name_cmd, root, timeout=30)
    cached = run_command("git diff --cached --stat -- .", root, timeout=20)
    return {
        "is_repo": status.returncode == 0,
        "status": status.stdout.strip(),
        "diff_stat": diff_stat.stdout.strip(),
        "changed_files": names.stdout.strip().splitlines()
        if names.stdout.strip()
        else [],
        "staged_stat": cached.stdout.strip(),
    }


def secret_scan_diff(root: Path, staged: bool = False) -> dict[str, Any]:
    cmd = "git diff --cached -- ." if staged else "git diff -- ."
    diff = run_command(cmd, root, timeout=40)
    hits = find_secret_hits(diff.stdout)
    return {
        "command": cmd,
        "returncode": diff.returncode,
        "hits": hits,
        "safe_to_commit": len(hits) == 0,
    }


def changed_files(root: Path, base: str | None = None) -> list[str]:
    cmd = (
        f"git diff --name-only {base}...HEAD -- ."
        if base
        else "git diff --name-only -- ."
    )
    result = run_command(cmd, root, timeout=30)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def suggest_commit_message(root: Path, base: str | None = None) -> str:
    files = changed_files(root, base)
    if not files:
        staged = run_command("git diff --cached --name-only -- .", root, timeout=20)
        files = [line.strip() for line in staged.stdout.splitlines() if line.strip()]
    if not files:
        return "chore: update project"

    joined = " ".join(files).lower()
    kind = "chore"
    if any(part in joined for part in ["test", "spec"]):
        kind = "test"
    if any(part in joined for part in ["readme", "docs/", ".md"]):
        kind = "docs"
    if any(part in joined for part in ["fix", "bug", "error"]):
        kind = "fix"
    if any(part in joined for part in ["package", "requirements", "lock", "deps"]):
        kind = "chore"
    if any(
        part in joined
        for part in ["src/", "app/", "pages/", "components/", "opcoding/"]
    ):
        kind = "feat" if kind == "chore" else kind

    area = Path(files[0]).parts[0] if files else "project"
    if len(files) > 1:
        subject = f"{kind}: update {area} workflow"
    else:
        subject = f"{kind}: update {Path(files[0]).stem.replace('_', '-')}"
    return subject[:72]


def suggest_branch_name(task: str, prefix: str = "codex") -> str:
    lowered = task.lower()
    branch_type = "task"
    if any(word in lowered for word in ["fix", "bug", "error", "broken"]):
        branch_type = "fix"
    elif any(word in lowered for word in ["docs", "readme", "document"]):
        branch_type = "docs"
    elif any(word in lowered for word in ["refactor", "cleanup"]):
        branch_type = "refactor"
    elif any(
        word in lowered for word in ["feature", "add", "create", "implement", "build"]
    ):
        branch_type = "feature"
    return f"{prefix}/{branch_type}/{slugify(task)}"


def risky_diff_findings(root: Path, base: str | None = None) -> list[dict[str, Any]]:
    summary = git_summary(root, base)
    files = [line.split(maxsplit=1)[-1] for line in summary.get("changed_files", [])]
    findings: list[dict[str, Any]] = []
    risky_patterns = [
        (re.compile(r"(^|/)\.github/workflows/"), "CI workflow changed"),
        (
            re.compile(r"(^|/)Dockerfile$|docker-compose|compose\.ya?ml"),
            "Container/deployment config changed",
        ),
        (
            re.compile(
                r"package-lock\.json|pnpm-lock\.yaml|yarn\.lock|poetry\.lock|Cargo\.lock"
            ),
            "Lockfile changed",
        ),
        (
            re.compile(r"(^|/)(auth|security|permission|crypto|payment)", re.I),
            "Security-sensitive path changed",
        ),
        (
            re.compile(r"\.env|secret|credential|token", re.I),
            "Secret-looking file changed",
        ),
    ]
    for file in files:
        for pattern, reason in risky_patterns:
            if pattern.search(file):
                findings.append({"file": file, "reason": reason, "severity": "review"})
    return findings


def pr_description(root: Path, base: str | None = None) -> str:
    summary = git_summary(root, base)
    risks = risky_diff_findings(root, base)
    commit = suggest_commit_message(root, base)
    lines = [
        "## Summary",
        f"- {commit}",
        "",
        "## Changed Files",
    ]
    changed = summary.get("changed_files", [])
    lines.extend(f"- `{line}`" for line in changed[:30])
    if not changed:
        lines.append("- No unstaged diff detected")
    lines.extend(["", "## Risk Notes"])
    if risks:
        lines.extend(f"- `{item['file']}`: {item['reason']}" for item in risks)
    else:
        lines.append("- No obvious risky diff patterns detected")
    lines.extend(["", "## Tests", "- TODO: add command output before opening PR"])
    return "\n".join(lines)
