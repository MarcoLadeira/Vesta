"""Bounded, structured diff evidence and persisted human review decisions."""

from __future__ import annotations

import re
import subprocess  # nosec B404 - fixed git argv, no shell
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .command_runner import redact
from .proc import no_window_kwargs
from .workflow_state import load_workflow_state, save_workflow_state

_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_RISK_PATHS = (
    ("permission", "permissions"),
    (".github/workflows/", "CI/CD workflow"),
    ("migration", "database migration"),
    ("auth", "authentication or authorization"),
    ("credential", "credentials"),
    ("secret", "secrets"),
    (".env", "environment secrets"),
)


def _risk_reasons(path: str) -> list[str]:
    lowered = path.lower()
    return [label for needle, label in _RISK_PATHS if needle in lowered]


def _sensitive_path(path: str) -> bool:
    lowered = path.lower()
    name = Path(lowered).name
    return (
        name == ".env"
        or name.startswith(".env.")
        or any(term in lowered for term in ("credential", "secret", "id_rsa"))
    )


def _summary(files: list[dict[str, Any]], *, truncated: bool = False) -> dict[str, Any]:
    return {
        "files": len(files),
        "pending": sum(item.get("decision", "pending") == "pending" for item in files),
        "approved": sum(item.get("decision") == "approved" for item in files),
        "rejected": sum(item.get("decision") == "rejected" for item in files),
        "risky": sum(bool(item.get("risky")) for item in files),
        "additions": sum(int(item.get("additions") or 0) for item in files),
        "deletions": sum(int(item.get("deletions") or 0) for item in files),
        "truncated": bool(truncated),
    }


def parse_unified_diff(
    text: str,
    *,
    max_files: int = 50,
    max_hunks: int = 200,
    max_lines_per_hunk: int = 80,
    max_chars: int = 120_000,
) -> dict[str, Any]:
    """Parse git's unified diff into bounded file and hunk observations."""
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    hunk: dict[str, Any] | None = None
    hunk_count = 0
    truncated = False
    remaining_chars = max(0, int(max_chars))

    for raw_line in text.splitlines():
        header = _DIFF_HEADER.match(raw_line)
        if header:
            if len(files) >= max(1, int(max_files)):
                truncated = True
                current = None
                hunk = None
                continue
            path = header.group(2)
            reasons = _risk_reasons(path)
            current = {
                "path": path,
                "decision": "pending",
                "additions": 0,
                "deletions": 0,
                "risky": bool(reasons),
                "risk_reasons": reasons,
                "untracked": False,
                "hunks": [],
                "sensitive": _sensitive_path(path),
            }
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        match = _HUNK_HEADER.match(raw_line)
        if match:
            if hunk_count >= max(1, int(max_hunks)):
                truncated = True
                hunk = None
                continue
            hunk = {
                "old_start": int(match.group(1)),
                "old_count": int(match.group(2) or 1),
                "new_start": int(match.group(3)),
                "new_count": int(match.group(4) or 1),
                "heading": match.group(5).strip(),
                "lines": [],
                "truncated": False,
            }
            current["hunks"].append(hunk)
            hunk_count += 1
            continue
        if hunk is None or raw_line.startswith(("--- ", "+++ ")):
            continue
        if raw_line.startswith("+"):
            current["additions"] += 1
        elif raw_line.startswith("-"):
            current["deletions"] += 1
        if current.get("sensitive"):
            if not hunk["lines"]:
                value = "[sensitive diff hidden]"[:remaining_chars]
                if value:
                    hunk["lines"].append(value)
                    remaining_chars -= len(value)
        elif len(hunk["lines"]) < max(1, int(max_lines_per_hunk)):
            safe_line = redact(raw_line)
            value = safe_line[: min(1_000, remaining_chars)]
            if value:
                hunk["lines"].append(value)
                remaining_chars -= len(value)
            if len(value) < len(safe_line):
                hunk["truncated"] = True
                truncated = True
        else:
            hunk["truncated"] = True
            truncated = True

    return {"files": files, "summary": _summary(files, truncated=truncated)}


def _git(
    repo_root: Path,
    argv: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> subprocess.CompletedProcess[str]:
    return run(
        argv,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30.0,
        check=False,
        **no_window_kwargs(),
    )


def _safe_relative(repo_root: Path, value: str) -> str | None:
    normalized = value.replace("\\", "/").strip()
    if normalized.startswith(("?? ", "M  ", " M ", "A  ")):
        normalized = normalized[3:].strip()
    try:
        resolved = (repo_root / normalized).resolve()
        relative = resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return None
    return relative


def build_diff_review(
    repo_root: Path,
    *,
    include_paths: Iterable[str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Build review evidence for only the current task's attributed paths."""
    root = repo_root.expanduser().resolve()
    requested = [
        item
        for item in (
            _safe_relative(root, str(value)) for value in (include_paths or ())
        )
        if item
    ]
    requested = list(dict.fromkeys(requested))
    if include_paths is not None and not requested:
        return {"files": [], "summary": _summary([])}
    diff_argv = ["git", "diff", "--no-ext-diff", "--unified=3", "HEAD"]
    if requested:
        diff_argv.extend(["--", *requested])
    try:
        completed = _git(root, diff_argv, run=run)
    except (OSError, subprocess.TimeoutExpired):
        completed = subprocess.CompletedProcess(diff_argv, 1, "", "git diff failed")
    parsed = parse_unified_diff(completed.stdout if completed.returncode == 0 else "")
    by_path = {str(item["path"]): item for item in parsed["files"]}

    try:
        status = _git(root, ["git", "status", "--porcelain=v1", "-z"], run=run)
        entries = status.stdout.split("\0") if status.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        entries = []
    untracked = {
        entry[3:].replace("\\", "/")
        for entry in entries
        if entry.startswith("?? ") and len(entry) > 3
    }
    candidates = (
        requested if include_paths is not None else sorted(set(by_path) | untracked)
    )
    candidates_truncated = len(candidates) > 50
    candidates = candidates[:50]
    files: list[dict[str, Any]] = []
    remaining_chars = 120_000
    for path in candidates:
        if path in by_path:
            files.append(by_path[path])
            continue
        if path not in untracked:
            continue
        reasons = _risk_reasons(path)
        lines: list[str] = []
        truncated = False
        try:
            source = (root / path).read_text(encoding="utf-8", errors="replace")
            source_lines = source.splitlines()
            if _sensitive_path(path) and source_lines:
                if remaining_chars > 0:
                    lines = ["[sensitive diff hidden]"[:remaining_chars]]
                    remaining_chars -= len(lines[0])
            else:
                for line in source_lines[:80]:
                    if remaining_chars <= 0:
                        break
                    value = redact("+" + line)[: min(1_000, remaining_chars)]
                    lines.append(value)
                    remaining_chars -= len(value)
            truncated = (
                len(lines) < min(80, len(source_lines)) or len(source_lines) > 80
            )
        except OSError:
            source_lines = []
        files.append(
            {
                "path": path,
                "decision": "pending",
                "additions": len(source_lines),
                "deletions": 0,
                "risky": bool(reasons),
                "risk_reasons": reasons,
                "untracked": True,
                "sensitive": _sensitive_path(path),
                "hunks": [
                    {
                        "old_start": 0,
                        "old_count": 0,
                        "new_start": 1,
                        "new_count": len(source_lines),
                        "heading": "untracked file",
                        "lines": lines,
                        "truncated": truncated,
                    }
                ],
            }
        )
    return {
        "files": files,
        "summary": _summary(
            files,
            truncated=parsed["summary"]["truncated"] or candidates_truncated,
        ),
    }


def record_diff_decision(
    project_root: Path, path: str, decision: str
) -> dict[str, Any]:
    """Persist approve/reject as workflow metadata; never mutate source files."""
    normalized = decision.strip().lower()
    if normalized not in {"approved", "rejected", "pending"}:
        return {"ok": False, "error_code": "INVALID_DIFF_DECISION"}
    state = load_workflow_state(project_root)
    review = dict(state.diff_review or {})
    files = [dict(item) for item in review.get("files") or []]
    found = False
    for item in files:
        if str(item.get("path")) == path:
            item["decision"] = normalized
            found = True
    if not found:
        return {"ok": False, "error_code": "DIFF_PATH_UNKNOWN"}
    previous_summary = review.get("summary") or {}
    review = {
        **review,
        "files": files,
        "summary": _summary(
            files, truncated=bool(previous_summary.get("truncated", False))
        ),
    }
    rejected = [
        str(item["path"]) for item in files if item.get("decision") == "rejected"
    ]
    blocker = (
        f"Diff review rejected: {', '.join(rejected)}"
        if rejected
        else (
            "" if state.blocker.startswith("Diff review rejected:") else state.blocker
        )
    )
    blockers = tuple(
        item
        for item in state.blockers
        if not str(item).startswith("Diff review rejected:")
    )
    if blocker.startswith("Diff review rejected:"):
        blockers = (*blockers, blocker)
    updated = replace(
        state,
        diff_review=review,
        blocker=blocker,
        blockers=blockers,
        merge_status=(
            "blocked"
            if rejected
            else (
                "pending_checks"
                if state.mode == "ship" and state.merge_status == "blocked"
                else state.merge_status
            )
        ),
    )
    save_workflow_state(project_root, updated)
    return {"ok": True, "workflow": updated.to_dict()}
