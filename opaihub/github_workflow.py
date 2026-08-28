"""Issue selection and safe, mockable GitHub PR/merge orchestration."""

from __future__ import annotations

import base64
import json
import re
import subprocess  # nosec B404 - argv-only GitHub CLI adapter
import sys
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable, Iterable

from .command_runner import redact
from .boundary_errors import safe_detail


_ACTIONS_RUN_ID = re.compile(r"/actions/runs/(\d+)(?:/|$)")
_GIT_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40,64}$")


@dataclass(frozen=True)
class _RequiredCheckSpec:
    """Trusted source contract for one required pull-request check."""

    name: str
    workflow_path: str | None
    workflow_name: str | None
    trusted_app: str
    events: tuple[str, ...]


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

    if any(
        label in labels for label in ("release-blocker", "release blocker", "blocker")
    ):
        score += 45
        reasons.append("release blocker")
    if "bug" in labels or "regression" in labels:
        score += 25
        reasons.append("user-facing defect")
    if any(label in labels for label in ("high-impact", "priority:high", "p1")):
        score += 25
        reasons.append("high impact")
    if any(
        label in labels
        for label in ("size:s", "size:small", "small", "good first issue")
    ):
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
    if any(
        term in text
        for term in ("entire architecture", "architectural migration", "multi-quarter")
    ):
        score -= 35
        reasons.append("architectural scope")
    if any(
        label in labels
        for label in ("risk:high", "security-critical", "breaking-change")
    ):
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

    #: ``gh`` noun/verb pairs whose success is legitimately silent on stdout
    #: (mutations that report only through the exit code or stderr). Every
    #: other command — reads and creates/comments that echo a URL — must
    #: produce stdout; exit 0 with empty stdout is a silent failure (F19).
    _SILENT_STDOUT_VERBS = frozenset(
        {
            ("issue", "close"),
            ("issue", "delete"),
            ("issue", "reopen"),
            ("pr", "close"),
            ("pr", "merge"),
            ("pr", "ready"),
            ("pr", "review"),
            ("release", "delete"),
            ("repo", "archive"),
        }
    )

    def __init__(
        self,
        repo_root: Path,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        on_event: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self._run_impl = run
        self._on_event = on_event

    def _activity(
        self, event_type: str, status: str, title: str, **kwargs: Any
    ) -> dict[str, Any]:
        from opai.activity import emit_event

        return emit_event(self._on_event, event_type, status, title, **kwargs)

    def _expects_stdout(self, args: list[str]) -> bool:
        pair = tuple(str(item).lower() for item in args[:2])
        return pair not in self._SILENT_STDOUT_VERBS

    def _run(
        self,
        args: list[str],
        *,
        allowed_returncodes: tuple[int, ...] = (0,),
        expect_output: bool | None = None,
    ) -> str:
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
        stdout = str(result.stdout or "").strip()
        if result.returncode not in allowed_returncodes or (
            result.returncode != 0 and not stdout
        ):
            detail = redact(
                str(result.stderr or result.stdout or "GitHub command failed")
            )
            raise RuntimeError(detail)
        if expect_output is None:
            expect_output = self._expects_stdout(args)
        if expect_output and not stdout:
            # `gh` can exit 0 with an empty body in broken environments (F19);
            # surface that as an error instead of a green "ran" with no data.
            detail = redact(
                f"gh {' '.join(str(item) for item in args)} produced no output "
                "on stdout (exit 0); treating it as failed instead of "
                "reporting an empty success"
            )
            raise RuntimeError(detail)
        return stdout

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

    def issue_view(self, number: int, *, repo: str | None = None) -> dict[str, Any]:
        """Read one issue with body, labels, and comments via ``--json`` (F19).

        Plain ``gh issue view`` can exit 0 with empty stdout in some
        environments, so this always uses the reliable ``--json`` form and
        refuses to hand back empty or malformed payloads.
        """
        args = [
            "issue",
            "view",
            str(int(number)),
            "--json",
            "number,title,body,state,labels,comments,url",
        ]
        if repo:
            args += ["--repo", str(repo)]
        output = self._run(args)
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                redact(f"gh issue view returned malformed JSON: {exc}")
            ) from exc
        if not isinstance(data, dict) or not data:
            raise RuntimeError("gh issue view returned no issue data")
        return data

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
        safe_head = redact(str(head))[:200]
        safe_base = redact(str(base))[:200]
        started_at = time.monotonic()
        event = self._activity(
            "tool_call",
            "running",
            "Opening pull request",
            metadata={
                "operation": "open_pr",
                "head": safe_head,
                "base": safe_base,
                "issue": issue_number,
            },
        )
        linked_body = body.rstrip()
        if issue_number:
            linked_body += f"\n\nCloses #{issue_number}"
        try:
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
            if not output:
                raise RuntimeError("GitHub did not return a pull request URL")
        except (OSError, RuntimeError, ValueError):
            self._activity(
                "tool_call",
                "error",
                "Pull request could not be opened",
                event_id=event["id"],
                duration_ms=int((time.monotonic() - started_at) * 1000),
                metadata={
                    "operation": "open_pr",
                    "head": safe_head,
                    "base": safe_base,
                    "issue": issue_number,
                },
            )
            raise
        url = redact(output.splitlines()[-1])[:500]
        self._activity(
            "tool_call",
            "success",
            "Pull request opened",
            detail=url,
            event_id=event["id"],
            duration_ms=int((time.monotonic() - started_at) * 1000),
            metadata={
                "operation": "open_pr",
                "head": safe_head,
                "base": safe_base,
                "issue": issue_number,
            },
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
        # #616: outward set-state. Repeating an identical edit is naturally
        # idempotent, but a lost response still needs an operation record so
        # the turn can prove what happened instead of guessing.
        from .idempotency import DONE, FRESH, IN_FLIGHT, abandon, begin, complete
        from .idempotency import operation_key

        key = operation_key(
            "update_pr",
            root=str(self.repo_root),
            pr=int(number),
            title=title,
            body=body,
        )
        prior = begin(self.repo_root, key)
        if prior["state"] == DONE:
            return str(prior["result"].get("output") or "")
        if prior["state"] == IN_FLIGHT:
            reconciled, decided = self._reconcile_update_pr(key, number, title, body)
            if reconciled is not None:
                return reconciled
            if not decided:
                raise RuntimeError(
                    "An earlier attempt to edit this pull request did not "
                    "confirm, and GitHub could not be checked. The edit may "
                    "already be applied — check before retrying."
                )
            prior = begin(self.repo_root, key)
            if prior["state"] != FRESH:
                raise RuntimeError(
                    "Edit operation state changed during reconciliation; "
                    "check the pull request before retrying."
                )
        try:
            output = self._run(
                ["pr", "edit", str(number), "--title", title, "--body", body]
            )
        except OSError:
            abandon(self.repo_root, key)
            raise
        except (RuntimeError, ValueError):
            reconciled, decided = self._reconcile_update_pr(key, number, title, body)
            if reconciled is not None:
                return reconciled
            if not decided:
                raise RuntimeError(
                    "The edit did not confirm and GitHub could not be "
                    "checked. It may already be applied — check before "
                    "retrying."
                )
            raise
        complete(self.repo_root, key, {"output": output})
        return output

    def _reconcile_update_pr(
        self, key: str, number: int, title: str, body: str
    ) -> tuple[str | None, bool]:
        """Settle an uncertain PR edit by observing its title and body.

        Set-state is idempotent: the PR already carrying exactly this title
        and body proves the edit landed; anything else proves nothing usable
        except through a fresh edit, so the key is released and the dispatch
        proceeds — applying the same state again is safe.
        """
        from .idempotency import OperationPersistenceError, abandon, complete

        try:
            raw = self._run(["pr", "view", str(number), "--json", "title,body"])
            observed = json.loads(raw)
        except (OSError, RuntimeError, ValueError, AttributeError):
            return None, False
        if (
            str(observed.get("title") or "") == title
            and str(observed.get("body") or "") == body
        ):
            try:
                complete(self.repo_root, key, {"output": "confirmed on GitHub"})
            except OperationPersistenceError:
                return None, False
            return "confirmed on GitHub", True
        try:
            abandon(self.repo_root, key)
        except OperationPersistenceError:
            return None, False
        return None, True

    def _trusted_required_check_manifest(self) -> dict[str, Any] | None:
        """Load policy from an immutable snapshot of the default branch.

        A pull request must never be able to change the policy used to judge
        that same pull request.  Resolve the repository's default branch to an
        exact commit, then its tree and the manifest blob.  Repositories whose
        trusted default-branch tree genuinely has no manifest retain the
        legacy ``gh pr checks`` behaviour.  API failures and malformed remote
        data fail closed.
        """

        repo = self._json_object(
            self._run(
                [
                    "api",
                    "repos/{owner}/{repo}",
                    "-H",
                    "Accept: application/vnd.github+json",
                ]
            ),
            source="GitHub repository API",
        )
        default_branch = str(repo.get("default_branch") or "").strip()
        if not default_branch:
            raise RuntimeError("GitHub did not return a default branch")

        ref = self._json_object(
            self._run(
                [
                    "api",
                    f"repos/{{owner}}/{{repo}}/git/ref/heads/{default_branch}",
                    "-H",
                    "Accept: application/vnd.github+json",
                ]
            ),
            source="GitHub default-branch ref API",
        )
        ref_object = ref.get("object")
        trusted_commit = (
            str(ref_object.get("sha") or "").strip().lower()
            if isinstance(ref_object, dict)
            else ""
        )
        if (
            not isinstance(ref_object, dict)
            or ref_object.get("type") not in (None, "commit")
            or _GIT_OBJECT_ID.fullmatch(trusted_commit) is None
        ):
            raise RuntimeError("GitHub did not return a valid default-branch SHA")

        commit = self._json_object(
            self._run(
                [
                    "api",
                    f"repos/{{owner}}/{{repo}}/git/commits/{trusted_commit}",
                    "-H",
                    "Accept: application/vnd.github+json",
                ]
            ),
            source="GitHub commit API",
        )
        tree_object = commit.get("tree")
        tree_sha = (
            str(tree_object.get("sha") or "").strip().lower()
            if isinstance(tree_object, dict)
            else ""
        )
        if _GIT_OBJECT_ID.fullmatch(tree_sha) is None:
            raise RuntimeError("GitHub did not return a valid default-branch tree SHA")

        tree = self._json_object(
            self._run(
                [
                    "api",
                    f"repos/{{owner}}/{{repo}}/git/trees/{tree_sha}?recursive=1",
                    "-H",
                    "Accept: application/vnd.github+json",
                ]
            ),
            source="GitHub tree API",
        )
        if tree.get("truncated") is True:
            raise RuntimeError(
                "GitHub default-branch tree was truncated; refusing partial policy"
            )
        entries = tree.get("tree")
        if not isinstance(entries, list):
            raise RuntimeError("GitHub tree API returned no tree entries")
        matches = [
            item
            for item in entries
            if isinstance(item, dict)
            and item.get("path") == ".github/required-checks.json"
            and item.get("type") == "blob"
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise RuntimeError("Trusted required-check manifest is ambiguous")
        blob_sha = str(matches[0].get("sha") or "").strip().lower()
        if _GIT_OBJECT_ID.fullmatch(blob_sha) is None:
            raise RuntimeError("GitHub did not return a valid policy blob SHA")

        blob = self._json_object(
            self._run(
                [
                    "api",
                    f"repos/{{owner}}/{{repo}}/git/blobs/{blob_sha}",
                    "-H",
                    "Accept: application/vnd.github+json",
                ]
            ),
            source="GitHub blob API",
        )
        if blob.get("encoding") != "base64":
            raise RuntimeError("Trusted required-check manifest has unknown encoding")
        raw_content = "".join(str(blob.get("content") or "").split())
        if not raw_content or len(raw_content) > 350_000:
            raise RuntimeError("Trusted required-check manifest has an invalid size")
        try:
            decoded = base64.b64decode(raw_content, validate=True)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "Trusted required-check manifest is not valid base64"
            ) from exc
        if not decoded or len(decoded) > 262_144:
            raise RuntimeError("Trusted required-check manifest has an invalid size")
        try:
            manifest = json.loads(decoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Trusted required-check manifest is malformed: {redact(str(exc))}"
            ) from exc
        if not isinstance(manifest, dict):
            raise RuntimeError("Required-check manifest must be a JSON object")
        return manifest

    def _required_check_specs(self) -> tuple[_RequiredCheckSpec, ...] | None:
        """Load the trusted default-branch required-check contract.

        Repositories without the manifest retain the legacy ``gh pr checks``
        behaviour. Once the manifest exists, malformed or ambiguous input is a
        hard error: silently falling back would turn a broken gate into a pass.

        Schema v1 stores check names as strings and the workflow path at the
        top level. Schema v2 stores objects whose ``workflow`` is the exact
        Actions workflow display name and whose ``trusted_app`` identifies the
        check-run producer. Both shapes remain supported.
        """

        local_manifest = self.repo_root / ".github" / "required-checks.json"
        git_marker = self.repo_root / ".git"
        if not local_manifest.is_file() and not git_marker.exists():
            # Preserve the adapter's legacy behaviour for non-checkout clients
            # and lightweight embeddings.  A real checkout still queries the
            # trusted branch even if a candidate deletes its local manifest.
            return None
        manifest = self._trusted_required_check_manifest()
        if manifest is None:
            return None

        raw_checks = manifest.get("required_checks")
        if not isinstance(raw_checks, list) or not raw_checks:
            raise RuntimeError("Required-check manifest has no required checks")

        top_workflow = str(manifest.get("workflow") or "").strip() or None
        default_app = str(manifest.get("trusted_app") or "github-actions").strip()
        raw_events = manifest.get("events") or manifest.get("trusted_events")
        default_events = self._string_tuple(raw_events) or ("pull_request",)
        specs: list[_RequiredCheckSpec] = []
        seen: set[str] = set()
        for raw in raw_checks:
            if isinstance(raw, str):
                name = raw.strip()
                workflow_path = top_workflow
                workflow_name = None
                trusted_app = default_app
                events = default_events
            elif isinstance(raw, dict):
                name = str(raw.get("name") or "").strip()
                item_workflow = str(raw.get("workflow") or "").strip() or None
                explicit_path = str(raw.get("workflow_path") or "").strip() or None
                # In v2 ``workflow`` is the display name. Accept a path-shaped
                # value as an explicit path for compatibility with early drafts.
                if item_workflow and (
                    item_workflow.startswith(".github/")
                    or item_workflow.endswith((".yml", ".yaml"))
                ):
                    workflow_path = explicit_path or item_workflow
                    workflow_name = str(raw.get("workflow_name") or "").strip() or None
                else:
                    workflow_path = explicit_path or top_workflow
                    workflow_name = (
                        str(raw.get("workflow_name") or item_workflow or "").strip()
                        or None
                    )
                trusted_app = str(raw.get("trusted_app") or default_app or "").strip()
                events = (
                    self._string_tuple(raw.get("events") or raw.get("trusted_events"))
                    or default_events
                )
            else:
                raise RuntimeError(
                    "Each required check must be a name or a contract object"
                )
            if not name:
                raise RuntimeError("Required-check manifest contains an empty name")
            if name in seen:
                raise RuntimeError(
                    f"Required-check manifest contains duplicate name: {name}"
                )
            if not trusted_app:
                raise RuntimeError(f"Required check {name!r} has no trusted app")
            if not workflow_path and not workflow_name:
                raise RuntimeError(f"Required check {name!r} has no trusted workflow")
            seen.add(name)
            specs.append(
                _RequiredCheckSpec(
                    name=name,
                    workflow_path=workflow_path,
                    workflow_name=workflow_name,
                    trusted_app=trusted_app,
                    events=events,
                )
            )
        return tuple(specs)

    @staticmethod
    def _string_tuple(raw: Any) -> tuple[str, ...]:
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            return ()
        return tuple(value for item in raw if (value := str(item or "").strip()))

    @staticmethod
    def _json_object(output: str, *, source: str) -> dict[str, Any]:
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{source} returned malformed JSON: {safe_detail(exc)}"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{source} returned an unexpected JSON shape")
        return data

    def _pr_head_sha(self, number: int) -> str:
        output = self._run(["pr", "view", str(number), "--json", "headRefOid"])
        data = self._json_object(output, source="gh pr view")
        head_sha = str(data.get("headRefOid") or "").strip()
        if not _GIT_OBJECT_ID.fullmatch(head_sha):
            raise RuntimeError("GitHub did not return a valid pull-request head SHA")
        return head_sha.lower()

    def _head_check_runs(self, head_sha: str) -> list[dict[str, Any]]:
        endpoint = (
            "repos/{owner}/{repo}/commits/"
            f"{head_sha}/check-runs?filter=latest&per_page=100"
        )
        output = self._run(
            ["api", endpoint, "-H", "Accept: application/vnd.github+json"]
        )
        data = self._json_object(output, source="GitHub check-runs API")
        raw_runs = data.get("check_runs")
        if not isinstance(raw_runs, list):
            raise RuntimeError("GitHub check-runs API returned no check-run list")
        total_count = data.get("total_count")
        if isinstance(total_count, int) and total_count > len(raw_runs):
            raise RuntimeError(
                "GitHub check-run snapshot was truncated; refusing partial evidence"
            )
        return [item for item in raw_runs if isinstance(item, dict)]

    def _actions_run(self, run_id: str) -> dict[str, Any]:
        output = self._run(
            [
                "api",
                f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}",
                "-H",
                "Accept: application/vnd.github+json",
            ]
        )
        return self._json_object(output, source="GitHub Actions run API")

    @staticmethod
    def _required_placeholder(
        spec: _RequiredCheckSpec,
        bucket: str,
        *,
        head_sha: str,
        current_head_sha: str | None = None,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "name": spec.name,
            "state": bucket.upper(),
            "bucket": bucket,
            "required": True,
            "head_sha": head_sha,
            "workflow": spec.workflow_name or "",
            "workflow_path": spec.workflow_path or "",
            "app": spec.trusted_app,
        }
        if current_head_sha:
            item["current_head_sha"] = current_head_sha
        return item

    @staticmethod
    def _run_id_from_url(url: str) -> str | None:
        match = _ACTIONS_RUN_ID.search(url)
        return match.group(1) if match else None

    @staticmethod
    def _check_bucket(check: dict[str, Any]) -> str:
        status = str(check.get("status") or "").strip().lower()
        conclusion = str(check.get("conclusion") or "").strip().lower()
        value = conclusion if status == "completed" and conclusion else status
        if value == "success":
            return "pass"
        if value in {"queued", "in_progress", "pending", "requested", "waiting"}:
            return "pending"
        return value or "pending"

    def _load_required_pr_checks(
        self, number: int, specs: tuple[_RequiredCheckSpec, ...]
    ) -> list[dict[str, Any]]:
        head_sha = self._pr_head_sha(number)
        raw_checks = self._head_check_runs(head_sha)
        by_name: dict[str, list[dict[str, Any]]] = {}
        required_names = {spec.name for spec in specs}
        for item in raw_checks:
            name = str(item.get("name") or "")
            if name in required_names:
                by_name.setdefault(name, []).append(item)

        actions_runs: dict[str, dict[str, Any]] = {}
        checked: list[dict[str, Any]] = []
        for spec in specs:
            candidates = by_name.get(spec.name, [])
            if not candidates:
                checked.append(
                    self._required_placeholder(spec, "missing", head_sha=head_sha)
                )
                continue
            if len(candidates) != 1:
                checked.append(
                    self._required_placeholder(spec, "duplicate", head_sha=head_sha)
                )
                continue

            candidate = candidates[0]
            candidate_sha = str(candidate.get("head_sha") or "").strip().lower()
            app = candidate.get("app")
            app_slug = (
                str(app.get("slug") or "").strip()
                if isinstance(app, dict)
                else str(app or "").strip()
            )
            details_url = str(candidate.get("details_url") or "").strip()
            bucket: str | None = None
            workflow_name = ""
            workflow_path = ""
            event = ""
            if candidate_sha != head_sha:
                bucket = "stale"
            elif app_slug != spec.trusted_app:
                bucket = "untrusted"

            run_id = self._run_id_from_url(details_url)
            if bucket is None and not run_id:
                bucket = "untrusted"
            if bucket is None and run_id:
                if run_id not in actions_runs:
                    actions_runs[run_id] = self._actions_run(run_id)
                action_run = actions_runs[run_id]
                run_sha = str(action_run.get("head_sha") or "").strip().lower()
                workflow_path = str(action_run.get("path") or "").strip()
                workflow_name = str(action_run.get("name") or "").strip()
                event = str(action_run.get("event") or "").strip()
                if run_sha != head_sha:
                    bucket = "stale"
                elif spec.workflow_path and workflow_path != spec.workflow_path:
                    bucket = "wrong_workflow"
                elif spec.workflow_name and workflow_name != spec.workflow_name:
                    bucket = "wrong_workflow"
                elif event not in spec.events:
                    bucket = "untrusted_event"
            if bucket is None:
                bucket = self._check_bucket(candidate)

            checked.append(
                {
                    "name": spec.name,
                    "state": str(
                        candidate.get("conclusion") or candidate.get("status") or bucket
                    ).upper(),
                    "bucket": bucket,
                    "link": details_url,
                    "workflow": workflow_name or spec.workflow_name or "",
                    "workflow_path": workflow_path or spec.workflow_path or "",
                    "event": event,
                    "required": True,
                    "head_sha": candidate_sha,
                    "app": app_slug,
                }
            )

        # Close the race in which the PR advances after the first head lookup
        # but before all check-run provenance has been validated. The old
        # snapshot is no longer evidence for the PR and must not briefly pass.
        current_head_sha = self._pr_head_sha(number)
        if current_head_sha != head_sha:
            return [
                self._required_placeholder(
                    spec,
                    "head_moved",
                    head_sha=head_sha,
                    current_head_sha=current_head_sha,
                )
                for spec in specs
            ]
        return checked

    def _load_pr_checks(self, number: int) -> list[dict[str, Any]]:
        specs = self._required_check_specs()
        if specs is not None:
            return self._load_required_pr_checks(number, specs)
        output = self._run(
            ["pr", "checks", str(number), "--json", "name,state,bucket,link,workflow"],
            # GitHub CLI uses 1 for failed checks and 8 for pending checks while
            # still returning the complete JSON snapshot on stdout.
            allowed_returncodes=(0, 1, 8),
        )
        data = json.loads(output or "[]")
        return data if isinstance(data, list) else []

    @staticmethod
    def _check_state(checks: list[dict[str, Any]]) -> str:
        values = {
            str(item.get("bucket") or item.get("state") or "").strip().lower()
            for item in checks
        }
        failures = {
            "fail",
            "failure",
            "error",
            "cancel",
            "cancelled",
            "action_required",
            "startup_failure",
            "timed_out",
            "stale",
            "duplicate",
            "untrusted",
            "untrusted_event",
            "wrong_workflow",
            "skipping",
            "skipped",
            "neutral",
        }
        successes = {"pass", "success"}
        if values & failures:
            return "failed"
        if checks and values and values <= successes:
            return "passed"
        return "pending"

    @staticmethod
    def _check_detail(checks: list[dict[str, Any]], state: str) -> str:
        count = len(checks)
        noun = "check" if count == 1 else "checks"
        if state == "passed":
            return f"{count} {noun} passed"
        if state == "failed":
            failed = sum(
                GitHubAdapter._check_state([item]) == "failed" for item in checks
            )
            return f"{failed} of {count} {noun} failed"
        return f"{count} {noun} pending" if count else "Waiting for CI checks"

    def pr_checks(self, number: int) -> list[dict[str, Any]]:
        started_at = time.monotonic()
        event = self._activity(
            "ci_watch",
            "running",
            "Checking CI status",
            metadata={"operation": "check_ci", "pr": int(number)},
        )
        try:
            checks = self._load_pr_checks(number)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            self._activity(
                "ci_watch",
                "error",
                "CI status could not be checked",
                event_id=event["id"],
                duration_ms=int((time.monotonic() - started_at) * 1000),
                metadata={"operation": "check_ci", "pr": int(number)},
            )
            raise
        state = self._check_state(checks)
        self._activity(
            "ci_watch",
            {"passed": "success", "failed": "error"}.get(state, "running"),
            f"CI checks {state}",
            detail=self._check_detail(checks, state),
            event_id=event["id"],
            duration_ms=int((time.monotonic() - started_at) * 1000),
            metadata={
                "operation": "check_ci",
                "pr": int(number),
                "checks": len(checks),
            },
        )
        return checks

    def watch_pr_checks(
        self,
        number: int,
        *,
        poll_interval: float = 5.0,
        timeout: float = 900.0,
        cancel: Any = None,
        sleep: Callable[[float], Any] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> dict[str, Any]:
        """Poll CI to a terminal state while updating one truthful timeline row."""

        started_at = monotonic()
        event = self._activity(
            "ci_watch",
            "running",
            "Watching CI checks",
            metadata={"operation": "watch_ci", "pr": int(number)},
        )
        checks: list[dict[str, Any]] = []
        while True:
            elapsed_ms = int((monotonic() - started_at) * 1000)
            if cancel is not None and cancel.is_set():
                self._activity(
                    "ci_watch",
                    "cancelled",
                    "CI watch cancelled",
                    event_id=event["id"],
                    duration_ms=elapsed_ms,
                    metadata={"operation": "watch_ci", "pr": int(number)},
                )
                return {"status": "cancelled", "checks": checks}
            if elapsed_ms >= max(0.0, timeout) * 1000:
                self._activity(
                    "ci_watch",
                    "warning",
                    "CI checks still pending",
                    detail=self._check_detail(checks, "pending"),
                    event_id=event["id"],
                    duration_ms=elapsed_ms,
                    metadata={"operation": "watch_ci", "pr": int(number)},
                )
                return {"status": "timed_out", "checks": checks}
            try:
                checks = self._load_pr_checks(number)
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
                elapsed_ms = int((monotonic() - started_at) * 1000)
                self._activity(
                    "ci_watch",
                    "error",
                    "CI watch failed",
                    event_id=event["id"],
                    duration_ms=elapsed_ms,
                    metadata={"operation": "watch_ci", "pr": int(number)},
                )
                raise
            state = self._check_state(checks)
            elapsed_ms = int((monotonic() - started_at) * 1000)
            status = {"passed": "success", "failed": "error"}.get(state, "running")
            self._activity(
                "ci_watch",
                status,
                f"CI checks {state}",
                detail=self._check_detail(checks, state),
                event_id=event["id"],
                duration_ms=elapsed_ms,
                metadata={
                    "operation": "watch_ci",
                    "pr": int(number),
                    "checks": len(checks),
                },
            )
            if state != "pending":
                return {"status": state, "checks": checks}
            sleep(max(0.0, poll_interval))

    def comment_pr(self, number: int, body: str) -> str:
        """Post a comment once, however many times the turn is retried (#295 gate 4).

        A duplicate comment is outward, visible to everyone on the thread, and
        not undoable by OPai — so a retried, resumed or reconnected turn must not
        post the same thing twice. The key is the comment's identity (repo, PR
        number, body), never an attempt counter.
        """

        from .idempotency import DONE, FRESH, IN_FLIGHT, abandon, begin, complete
        from .idempotency import operation_key

        key = operation_key(
            "comment_pr", root=str(self.repo_root), pr=int(number), body=body
        )
        prior = begin(self.repo_root, key)
        if prior["state"] == DONE:
            return str(prior["result"].get("output") or "")
        if prior["state"] == IN_FLIGHT:
            # Started and never confirmed. Reconcile against the thread —
            # the exact body on this PR is observable — before deciding
            # whether posting again is safe (#616).
            reconciled, decided = self._reconcile_comment_pr(key, number, body)
            if reconciled is not None:
                return reconciled
            if not decided:
                raise RuntimeError(
                    "An earlier attempt to post this comment did not confirm, "
                    "and GitHub could not be checked. It may already be on "
                    "the pull request — check before retrying."
                )
            # GitHub answered and the comment is absent: the earlier attempt
            # provably never landed. Claim fresh and continue below.
            prior = begin(self.repo_root, key)
            if prior["state"] != FRESH:
                raise RuntimeError(
                    "Comment operation state changed during reconciliation; "
                    "check the pull request before retrying."
                )
        try:
            output = self._run(["pr", "comment", str(number), "--body", body])
        except OSError:
            # gh itself never ran: provably nothing was posted.
            abandon(self.repo_root, key)
            raise
        except (RuntimeError, ValueError) as exc:
            # gh ran and failed. A refused post (4xx) provably landed nothing,
            # but a network drop after GitHub accepted the comment exits
            # nonzero too — reconcile before releasing the key (#616).
            reconciled, decided = self._reconcile_comment_pr(key, number, body)
            if reconciled is not None:
                return reconciled
            if not decided:
                raise RuntimeError(
                    "The comment did not confirm and GitHub could not be "
                    "checked. It may already be on the pull request — check "
                    "before retrying."
                ) from exc
            raise
        complete(self.repo_root, key, {"output": output})
        return output

    def _reconcile_comment_pr(
        self, key: str, number: int, body: str
    ) -> tuple[str | None, bool]:
        """Settle an uncertain comment by searching the thread for its body.

        ``(output, True)`` records and confirms a found comment;
        ``(None, True)`` proves it absent and releases the key;
        ``(None, False)`` keeps the operation uncertain.
        """
        from .idempotency import OperationPersistenceError, abandon, complete

        try:
            raw = self._run(["pr", "view", str(number), "--json", "comments"])
            comments = json.loads(raw).get("comments") or []
        except (OSError, RuntimeError, ValueError, AttributeError):
            return None, False
        for comment in comments:
            if (
                isinstance(comment, dict)
                and str(comment.get("body") or "").strip() == body.strip()
            ):
                try:
                    complete(self.repo_root, key, {"output": "confirmed on GitHub"})
                except OperationPersistenceError:
                    return None, False
                return "confirmed on GitHub", True
        try:
            abandon(self.repo_root, key)
        except OperationPersistenceError:
            return None, False
        return None, True

    def merge_pr(self, number: int, *, method: str = "squash") -> str:
        if method not in {"merge", "squash", "rebase"}:
            raise ValueError("Unsupported merge method")
        # #616 / #295 gate 4: a merge is the single most irreversible outward
        # action OPai can take — it lands on the repository's default branch
        # history and cannot be undone by OPai. A retried, resumed or
        # reconnected turn must not merge twice, and a lost response after
        # GitHub accepted the merge must fail closed as uncertain rather than
        # silently dispatching a second merge. The key is the merge's identity
        # (repo, PR number, method), never an attempt counter.
        from .idempotency import DONE, FRESH, IN_FLIGHT, abandon, begin, complete
        from .idempotency import operation_key

        key = operation_key(
            "merge_pr", root=str(self.repo_root), pr=int(number), method=method
        )
        prior = begin(self.repo_root, key)
        if prior["state"] == DONE:
            return str(prior["result"].get("output") or "")
        if prior["state"] == IN_FLIGHT:
            # Started and never confirmed. Reconcile against the PR's observed
            # state — a merge is atomic server-side, so MERGED proves it
            # landed and OPEN proves it did not (#616).
            reconciled, decided = self._reconcile_merge_pr(key, number)
            if reconciled is not None:
                return reconciled
            if not decided:
                raise RuntimeError(
                    "An earlier attempt to merge this pull request did not "
                    "confirm, and GitHub could not be checked. It may already "
                    "be merged — check the repository before retrying."
                )
            # The PR is observably not merged: the earlier attempt provably
            # never landed. Claim fresh and continue below.
            prior = begin(self.repo_root, key)
            if prior["state"] != FRESH:
                raise RuntimeError(
                    "Merge operation state changed during reconciliation; "
                    "check the repository before retrying."
                )
        started_at = time.monotonic()
        event = self._activity(
            "command_run",
            "running",
            "Merging pull request",
            metadata={"operation": "merge_pr", "pr": int(number), "method": method},
        )
        try:
            output = self._run(["pr", "merge", str(number), f"--{method}"])
        except OSError:
            # gh itself never ran: provably nothing was merged.
            abandon(self.repo_root, key)
            self._activity(
                "command_complete",
                "error",
                "Pull request could not be merged",
                event_id=event["id"],
                duration_ms=int((time.monotonic() - started_at) * 1000),
                metadata={"operation": "merge_pr", "pr": int(number), "method": method},
            )
            raise
        except (RuntimeError, ValueError) as exc:
            # gh ran and failed. A refusal (checks pending, not mergeable)
            # provably merged nothing, but a network drop after GitHub
            # accepted the merge exits nonzero too — reconcile before
            # releasing the key (#616).
            reconciled, decided = self._reconcile_merge_pr(key, number)
            if reconciled is not None:
                self._activity(
                    "command_complete",
                    "success",
                    "Pull request merge confirmed on GitHub",
                    event_id=event["id"],
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    metadata={
                        "operation": "merge_pr",
                        "pr": int(number),
                        "method": method,
                    },
                )
                return reconciled
            if not decided:
                self._activity(
                    "command_complete",
                    "error",
                    "Pull request merge state is uncertain",
                    event_id=event["id"],
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    metadata={
                        "operation": "merge_pr",
                        "pr": int(number),
                        "method": method,
                    },
                )
                raise RuntimeError(
                    "The merge did not confirm and GitHub could not be "
                    "checked. It may already be merged — check the repository "
                    "before retrying."
                ) from exc
            abandon(self.repo_root, key)
            self._activity(
                "command_complete",
                "error",
                "Pull request could not be merged",
                event_id=event["id"],
                duration_ms=int((time.monotonic() - started_at) * 1000),
                metadata={"operation": "merge_pr", "pr": int(number), "method": method},
            )
            raise
        self._activity(
            "command_complete",
            "success",
            "Pull request merged",
            event_id=event["id"],
            duration_ms=int((time.monotonic() - started_at) * 1000),
            metadata={"operation": "merge_pr", "pr": int(number), "method": method},
        )
        complete(self.repo_root, key, {"output": output})
        return output

    def _reconcile_merge_pr(self, key: str, number: int) -> tuple[str | None, bool]:
        """Settle an uncertain merge by observing the PR's state on GitHub.

        A merge is atomic server-side: ``MERGED`` proves it landed (recorded
        as done), ``OPEN``/``CLOSED`` prove this merge never did (key
        released, retry free). Anything unobservable keeps it uncertain.
        """
        from .idempotency import OperationPersistenceError, abandon, complete

        try:
            raw = self._run(["pr", "view", str(number), "--json", "state"])
            state = str(json.loads(raw).get("state") or "").upper()
        except (OSError, RuntimeError, ValueError, AttributeError):
            return None, False
        if state == "MERGED":
            try:
                complete(self.repo_root, key, {"output": "confirmed merged"})
            except OperationPersistenceError:
                return None, False
            return "confirmed merged", True
        if state in {"OPEN", "CLOSED"}:
            try:
                abandon(self.repo_root, key)
            except OperationPersistenceError:
                return None, False
            return None, True
        return None, False


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
