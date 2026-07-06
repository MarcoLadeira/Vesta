from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .utils import find_secret_hits, run_command, slugify

# Refs cross a security boundary (#20): user input becomes a Git positional
# argument. Only plain branch/tag/commit names pass - the charset excludes
# option dashes up front, whitespace, shell metacharacters, control bytes,
# and every revision-expression operator (~ ^ : @{ } ? * [ \). Range syntax,
# empty path segments, and ".lock" endings are rejected explicitly.
_REF_GRAMMAR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")


def validate_ref(ref: str) -> str:
    """Accept only a plain ref name; anything ambiguous fails closed."""

    value = str(ref or "").strip()
    if (
        not value
        or not _REF_GRAMMAR.fullmatch(value)
        or ".." in value
        or "//" in value
        or value.endswith((".lock", "/", "."))
    ):
        raise ValueError(
            "Rejected unsafe Git ref: use a plain branch, tag, or commit name"
        )
    return value


def resolve_ref(root: Path, ref: str) -> str:
    """Grammar-check ``ref`` and confirm it names a real commit (fail closed)."""

    value = validate_ref(ref)
    result = run_command(
        ["git", "rev-parse", "--verify", "--quiet", f"{value}^{{commit}}"],
        root,
        timeout=15,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(f"Unknown Git ref: {value}")
    return value


def _diff_argv(*options: str, rev_range: str | None) -> list[str]:
    """Structured git-diff argv: resolved range, no external diff, terminator."""

    argv = ["git", "diff", "--no-ext-diff", *options]
    if rev_range:
        argv.append(rev_range)
    argv += ["--", "."]
    return argv


def _rev_range(root: Path, base: str | None) -> str | None:
    """Validate then resolve ``base`` before any other command is built."""

    return f"{resolve_ref(root, base)}...HEAD" if base else None


def git_summary(root: Path, base: str | None = None) -> dict[str, Any]:
    rev = _rev_range(root, base)
    status = run_command(
        ["git", "status", "--short", "--branch", "--", "."], root, timeout=20
    )
    diff_stat = run_command(_diff_argv("--stat", rev_range=rev), root, timeout=30)
    names = run_command(_diff_argv("--name-status", rev_range=rev), root, timeout=30)
    cached = run_command(
        ["git", "diff", "--no-ext-diff", "--cached", "--stat", "--", "."],
        root,
        timeout=20,
    )
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
    """Scan the diff for secrets; every failure mode blocks the action (#20)."""

    argv = ["git", "diff", "--no-ext-diff"]
    if staged:
        argv.append("--cached")
    argv += ["--", "."]
    diff = run_command(argv, root, timeout=40)
    scanner_ok = diff.returncode == 0 and not diff.timed_out
    hits: list[str] = []
    reason = "clean"
    if scanner_ok:
        try:
            hits = find_secret_hits(diff.stdout)
        except Exception:  # noqa: BLE001 - a broken scanner must block, not pass
            scanner_ok = False
            reason = "secret scanner failed; blocking until it can run"
        if hits:
            reason = "potential secrets detected in the diff"
    else:
        reason = "git diff failed or timed out; result is unreadable"
    return {
        "command": " ".join(argv),
        "returncode": diff.returncode,
        "hits": hits,
        "scanner_ok": scanner_ok,
        "safe_to_commit": scanner_ok and not hits,
        "reason": reason,
    }


def changed_files(root: Path, base: str | None = None) -> list[str]:
    rev = _rev_range(root, base)
    result = run_command(_diff_argv("--name-only", rev_range=rev), root, timeout=30)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def suggest_commit_message(root: Path, base: str | None = None) -> str:
    files = changed_files(root, base)
    if not files:
        staged = run_command(
            ["git", "diff", "--no-ext-diff", "--cached", "--name-only", "--", "."],
            root,
            timeout=20,
        )
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
