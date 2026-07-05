"""Issue selection and safe, mockable GitHub PR/merge orchestration."""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - argv-only GitHub CLI adapter
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable, Iterable

from .command_runner import redact


@dataclass(frozen=True)
class IssueCandidate:
    number: int
    title: str
    body: str = ""
    labels: tuple[str, ...] = ()
    url: str = ""
    score: int = 0
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _label_names(raw: Any) -> tuple[str, ...]:
    labels = []
    for item in raw or []:
        name = item.get("name") if isinstance(item, dict) else item
        value = str(name or "").strip().lower()
        if value and value not in labels:
            labels.append(value)
    return tuple(labels)


def _score_issue(issue: dict[str, Any]) -> IssueCandidate:
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    text = f"{title} {body}".lower()
    labels = _label_names(issue.get("labels"))
    score = 0
    reasons: list[str] = []

    if any(label in labels for label in ("release-blocker", "release blocker", "blocker")):
        score += 45
        reasons.append("release blocker")
    if "bug" in labels or "regression" in labels:
        score += 25
        reasons.append("user-facing defect")
    if any(label in labels for label in ("high-impact", "priority:high", "p1")):
        score += 25
        reasons.append("high impact")
    if any(label in labels for label in ("size:s", "size:small", "small", "good first issue")):
        score += 20
        reasons.append("bounded")
    if "test" in text or "reproduc" in text:
        score += 15
        reasons.append("testable")
    if "no user impact" in text or "internal comment" in text:
        score -= 15
        reasons.append("low impact")
    if any(label in labels for label in ("size:xl", "size:large", "epic")):
        score -= 40
        reasons.append("too large")
    if any(term in text for term in ("entire architecture", "architectural migration", "multi-quarter")):
        score -= 35
        reasons.append("architectural scope")
    if any(label in labels for label in ("risk:high", "security-critical", "breaking-change")):
        score -= 20
        reasons.append("high risk")

    return IssueCandidate(
        number=int(issue.get("number") or 0),
        title=title,
        body=body,
        labels=labels,
        url=str(issue.get("url") or ""),
        score=score,
        reasons=tuple(reasons),
    )


def rank_issues(issues: Iterable[dict[str, Any]]) -> list[IssueCandidate]:
    """Rank high-value, bounded, testable work ahead of architectural epics."""

    ranked = [_score_issue(issue) for issue in issues]
    return sorted(ranked, key=lambda item: (-item.score, item.number))


def select_small_important_issue(
    issues: Iterable[dict[str, Any]],
) -> IssueCandidate | None:
    ranked = rank_issues(issues)
    return ranked[0] if ranked and ranked[0].score > 0 else None


@dataclass(frozen=True)
class ShipChecks:
    tests_pass: bool = False
    correct_branch: bool = False
    no_secrets: bool = False
    no_risky_files: bool = False
    production_auth_safe: bool = False
    no_unrelated_files: bool = False
    no_conflicts: bool = False
    checks_acceptable: bool = False

    @property
    def blockers(self) -> tuple[str, ...]:
        messages = {
            "tests_pass": "tests have not passed",  # nosec B105 - status text
            "correct_branch": "branch is not the expected PR branch",
            "no_secrets": "secrets were detected",
            "no_risky_files": "risky files have not been reviewed",
            "production_auth_safe": "production or authentication changes are not authorized",
            "no_unrelated_files": "unrelated files are included",
            "no_conflicts": "conflicts remain unresolved",
            "checks_acceptable": "PR checks are not acceptable",
        }
        return tuple(
            messages[field.name]
            for field in fields(self)
            if not bool(getattr(self, field.name))
        )

    @property
    def can_merge(self) -> bool:
        return not self.blockers


class GitHubAdapter:
    """Small `gh` adapter; all process execution is injectable for tests."""

    def __init__(
        self,
        repo_root: Path,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self._run_impl = run

    def _run(self, args: list[str]) -> str:
        command = ["gh", *args]
        kwargs: dict[str, Any] = {
            "cwd": str(self.repo_root),
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 30.0,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = self._run_impl(command, **kwargs)
        if result.returncode != 0:
            detail = redact(str(result.stderr or result.stdout or "GitHub command failed"))
            raise RuntimeError(detail)
        return str(result.stdout or "").strip()

    def list_open_issues(self, *, limit: int = 100) -> list[dict[str, Any]]:
        output = self._run(
            [
                "issue",
                "list",
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title,body,labels,url",
            ]
        )
        data = json.loads(output or "[]")
        return data if isinstance(data, list) else []

    def create_issue(self, *, title: str, body: str, labels: Iterable[str] = ()) -> str:
        args = ["issue", "create", "--title", title, "--body", body]
        label_text = ",".join(str(item) for item in labels if str(item))
        if label_text:
            args += ["--label", label_text]
        return self._run(args).splitlines()[-1]

    def create_pr(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        issue_number: int | None = None,
    ) -> str:
        linked_body = body.rstrip()
        if issue_number:
            linked_body += f"\n\nCloses #{issue_number}"
        output = self._run(
            [
                "pr",
                "create",
                "--title",
                title,
                "--body",
                linked_body,
                "--head",
                head,
                "--base",
                base,
            ]
        )
        return output.splitlines()[-1]

    def list_open_prs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        output = self._run(
            [
                "pr",
                "list",
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title,body,url,headRefName,baseRefName,isDraft,mergeStateStatus,closingIssuesReferences",
            ]
        )
        data = json.loads(output or "[]")
        return data if isinstance(data, list) else []

    def find_linked_pr(self, issue_number: int) -> dict[str, Any] | None:
        number = int(issue_number)
        marker = re.compile(rf"(?<!\d)#{number}(?!\d)")
        for pr in self.list_open_prs():
            references = pr.get("closingIssuesReferences") or []
            if any(
                isinstance(item, dict) and int(item.get("number") or 0) == number
                for item in references
            ):
                return pr
            if marker.search(str(pr.get("body") or "")):
                return pr
        return None

    def update_pr(self, number: int, *, title: str, body: str) -> str:
        return self._run(
            ["pr", "edit", str(number), "--title", title, "--body", body]
        )

    def pr_checks(self, number: int) -> list[dict[str, Any]]:
        output = self._run(
            ["pr", "checks", str(number), "--json", "name,state,bucket,link,workflow"]
        )
        data = json.loads(output or "[]")
        return data if isinstance(data, list) else []

    def comment_pr(self, number: int, body: str) -> str:
        return self._run(["pr", "comment", str(number), "--body", body])

    def merge_pr(self, number: int, *, method: str = "squash") -> str:
        if method not in {"merge", "squash", "rebase"}:
            raise ValueError("Unsupported merge method")
        return self._run(["pr", "merge", str(number), f"--{method}"])


class CodingWorkflow:
    def __init__(self, github: GitHubAdapter) -> None:
        self.github = github

    def select_issue(self) -> IssueCandidate | None:
        return select_small_important_issue(self.github.list_open_issues())

    def merge_if_safe(self, pr_number: int, checks: ShipChecks) -> dict[str, object]:
        if not checks.can_merge:
            return {"merged": False, "blockers": list(checks.blockers)}
        output = self.github.merge_pr(pr_number)
        return {"merged": True, "blockers": [], "output": output}
