from __future__ import annotations

from pathlib import Path
from typing import Any

from .gitops import git_summary, resolve_ref, risky_diff_findings, secret_scan_diff
from .utils import run_command


def review_diff(root: Path, base: str | None = None) -> dict[str, Any]:
    summary = git_summary(root, base)
    risks = risky_diff_findings(root, base)
    secrets = secret_scan_diff(root, staged=False)
    diff_argv = ["git", "diff", "--no-ext-diff"]
    if base:
        diff_argv.append(f"{resolve_ref(root, base)}...HEAD")
    diff_argv += ["--", "."]
    diff = run_command(diff_argv, root, timeout=60)
    findings: list[dict[str, Any]] = []

    if secrets["hits"]:
        findings.append(
            {
                "severity": "P0",
                "title": "Secret-like value detected in diff",
                "body": "Redacted secret-like content appears in the current diff. Remove it before commit.",
            }
        )
    elif not secrets.get("scanner_ok", False):
        # Fail closed (#20): an unreadable scan is a blocker, never a pass.
        findings.append(
            {
                "severity": "P0",
                "title": "Secret scan could not run",
                "body": secrets.get("reason", "Secret scan failed")
                + ". Fix the scan before committing.",
            }
        )
    for risk in risks:
        findings.append(
            {
                "severity": "P2",
                "title": risk["reason"],
                "body": f"`{risk['file']}` changed and should receive focused review.",
            }
        )

    changed = "\n".join(summary.get("changed_files", []))
    code_changed = any(
        ext in changed
        for ext in [".py", ".js", ".ts", ".tsx", ".cs", ".go", ".rs", ".java"]
    )
    tests_changed = any(part in changed.lower() for part in ["test", "spec"])
    if code_changed and not tests_changed:
        findings.append(
            {
                "severity": "P2",
                "title": "Code changed without nearby test changes",
                "body": "Consider targeted tests or note why existing coverage is sufficient.",
            }
        )

    if diff.stdout.count("\n-") > 300:
        findings.append(
            {
                "severity": "P2",
                "title": "Large deletion in diff",
                "body": "Large deletions increase rollback risk. Verify behavior and release notes.",
            }
        )

    return {
        "summary": summary,
        "findings": findings,
        "local_only": True,
        "next_step": "Run targeted tests before asking an expensive model for review.",
    }
