"""Multi-agent parallel issue solving with isolated worktrees (#177).

Independent issues run in parallel only when their intended paths do not
overlap: each assignment gets its own ``codex/`` branch and a dedicated
worktree outside the source checkout, so agents never write to a shared
file. Ownership metadata is durable and claim-based - one owner per
assignment, bounded active concurrency - and reconciliation refuses to
auto-merge assignments whose changed files collide, surfacing them for
sequential human-reviewed merges instead. Nothing here resets, cleans, or
overwrites the user's checkout; worktree creation is delegated to durable,
reconciled OPai worktree leases.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - argv-only git helpers, never a shell string
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from collections.abc import Mapping
from typing import Any, Callable, Iterable

from . import shadow_journal
from .atomic_io import atomic_write_text, interprocess_transaction
from .command_runner import redact
from .repo_context import DirtyConflictError, classify_dirty_paths
from .repository_safety import capture_repository_handle
from .state import state_dir
from .worktree_leases import WorktreeLease, WorktreeManager
from .workflow_ledger import WorkflowLedger

ASSIGNMENT_STATUSES = {"planned", "active", "completed", "abandoned"}


class OwnershipError(RuntimeError):
    """Raised when an agent touches an assignment another agent owns."""


class SharedFileConflictError(RuntimeError):
    """Raised when a merge would overwrite files another assignment changed."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")
    return text[:40].rstrip("-") or "issue"


def _valid_assignment_id(value: str) -> str:
    assignment_id = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", assignment_id):
        raise ValueError("Invalid assignment id")
    return assignment_id


def _normal(path: str) -> PurePosixPath:
    return PurePosixPath(str(path).replace("\\", "/").strip("/"))


def _overlaps(left: PurePosixPath, right: PurePosixPath) -> bool:
    return left == right or left in right.parents or right in left.parents


def _paths_collide(first: Iterable[str], second: Iterable[str]) -> bool:
    lefts = [_normal(item) for item in first if str(item)]
    rights = [_normal(item) for item in second if str(item)]
    if not lefts or not rights:
        # Unknown scope must be treated as potentially everything: refusing
        # parallelism is safer than two agents editing the same file.
        return True
    return any(_overlaps(left, right) for left in lefts for right in rights)


@dataclass(frozen=True)
class AgentAssignment:
    assignment_id: str
    issue_number: int
    title: str
    owner: str
    branch: str
    worktree: str
    intended_paths: tuple[str, ...]
    status: str = "planned"
    changed_files: tuple[str, ...] = ()
    lease_id: str = ""
    lease_state: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["intended_paths"] = list(self.intended_paths)
        value["changed_files"] = list(self.changed_files)
        return value


@dataclass(frozen=True)
class ParallelPlan:
    assignments: tuple[AgentAssignment, ...]
    deferred: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignments": [item.to_dict() for item in self.assignments],
            "deferred": list(self.deferred),
        }


def plan_parallel_assignments(
    repo_root: Path,
    issues: Iterable[dict[str, Any]],
    *,
    max_parallel: int = 2,
    owner_prefix: str = "agent",
) -> ParallelPlan:
    """Assign independent issues to parallel agents; defer anything unsafe.

    ``issues`` are already in priority order; each needs ``number``,
    ``title``, and ``intended_paths``. Overlapping intended paths or an
    unknown scope defer the later issue - parallelism never wins over the
    no-shared-file guarantee.
    """

    root = repo_root.expanduser().resolve()
    limit = max(1, int(max_parallel))
    assignments: list[AgentAssignment] = []
    deferred: list[dict[str, Any]] = []
    for issue in issues:
        number = int(issue.get("number") or 0)
        title = str(issue.get("title") or "")
        intended = tuple(str(item) for item in issue.get("intended_paths") or ())
        if len(assignments) >= limit:
            deferred.append(
                {
                    "number": number,
                    "reason": f"concurrency limit reached ({limit})",
                }
            )
            continue
        collision = next(
            (
                item
                for item in assignments
                if _paths_collide(intended, item.intended_paths)
            ),
            None,
        )
        if collision is not None:
            deferred.append(
                {
                    "number": number,
                    "reason": (
                        f"intended paths overlap issue #{collision.issue_number}; "
                        "run sequentially to avoid shared-file overwrites"
                    ),
                }
            )
            continue
        owner = f"{owner_prefix}-{len(assignments) + 1}"
        branch = f"codex/issue-{number}-{_slug(title)}"
        worktree = root.parent / f"{root.name}-agents" / f"issue-{number}"
        assignments.append(
            AgentAssignment(
                assignment_id=uuid.uuid4().hex[:16],
                issue_number=number,
                title=redact(title),
                owner=owner,
                branch=branch,
                worktree=str(worktree),
                intended_paths=intended,
                status="planned",
                created_at=_now_iso(),
                updated_at=_now_iso(),
            )
        )
    return ParallelPlan(tuple(assignments), tuple(deferred))


def _assignment_path(project_root: Path, assignment_id: str) -> Path:
    return (
        state_dir(project_root.expanduser().resolve())
        / "agent"
        / "parallel"
        / f"{_valid_assignment_id(assignment_id)}.json"
    )


def _valid_assignment_record(record: Mapping[str, Any]) -> bool:
    """A mirrored assignment must carry the identity and status a reader needs."""

    return (
        isinstance(record.get("assignment_id"), str)
        and bool(record.get("assignment_id"))
        and isinstance(record.get("status"), str)
        and bool(record.get("status"))
    )


def save_assignment(project_root: Path, assignment: AgentAssignment) -> Path:
    path = _assignment_path(project_root, assignment.assignment_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # This module wrote with no interprocess lock at all -- a hand-rolled
    # temp+replace. The replace itself is atomic, so a reader never saw a torn
    # file, but two processes writing the same assignment could interleave,
    # and #613 Stage 2 needs the journal to observe writes in the same order
    # the file did. Taking the same lock every other migrated module uses
    # gives both, and lets atomic_write_text replace the hand-rolled pair.
    #
    # Scope, stated plainly: this closes the window *inside* save. It does not
    # close the wider check-then-claim race in claim_assignment, which reads,
    # counts active assignments, and only then writes -- two claimants can
    # still both pass the max_active check. That needs its own fix with
    # multiprocess tests.
    with interprocess_transaction(path):
        atomic_write_text(
            path, json.dumps(assignment.to_dict(), indent=2, sort_keys=True) + "\n"
        )
        shadow_journal.record_snapshot(
            path, assignment.to_dict(), is_valid_record=_valid_assignment_record
        )
    return path


def shadow_journal_projection(project_root: Path, assignment_id: str) -> dict[str, Any]:
    """The assignment the shadow journal alone would reconstruct."""

    return shadow_journal.projection(
        _assignment_path(project_root, assignment_id),
        is_valid_record=_valid_assignment_record,
    )


def assignment_contradiction_report(
    project_root: Path, assignment_id: str
) -> dict[str, Any] | None:
    """``None`` when the file and its shadow agree; otherwise what differs.

    The dual-read #613 asks for. ``load_assignment`` is deliberately not
    reused: it raises on anything malformed, which is right for its callers
    but would raise past the very divergence this exists to report.
    """

    path = _assignment_path(project_root, assignment_id)

    def read_legacy() -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    return shadow_journal.contradiction_report(
        path,
        read_legacy,
        is_valid_record=_valid_assignment_record,
        identity={"assignment_id": assignment_id},
    )


def load_assignment(project_root: Path, assignment_id: str) -> AgentAssignment:
    data = json.loads(
        _assignment_path(project_root, assignment_id).read_text(encoding="utf-8")
    )
    return AgentAssignment(
        assignment_id=str(data["assignment_id"]),
        issue_number=int(data.get("issue_number") or 0),
        title=str(data.get("title") or ""),
        owner=str(data.get("owner") or ""),
        branch=str(data.get("branch") or ""),
        worktree=str(data.get("worktree") or ""),
        intended_paths=tuple(str(item) for item in data.get("intended_paths") or ()),
        status=str(data.get("status") or "planned"),
        changed_files=tuple(str(item) for item in data.get("changed_files") or ()),
        lease_id=str(data.get("lease_id") or ""),
        lease_state=str(data.get("lease_state") or ""),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
    )


def list_assignments(
    project_root: Path, *, status: str | None = None
) -> list[AgentAssignment]:
    directory = state_dir(project_root.expanduser().resolve()) / "agent" / "parallel"
    if not directory.is_dir():
        return []
    assignments = []
    for path in directory.glob("*.json"):
        try:
            assignment = load_assignment(project_root, path.stem)
        except (OSError, ValueError, KeyError):
            continue
        if status is None or assignment.status == status:
            assignments.append(assignment)
    return sorted(assignments, key=lambda item: (item.issue_number, item.assignment_id))


def _record(project_root: Path, assignment: AgentAssignment, event: str) -> None:
    WorkflowLedger(project_root, task_id=assignment.assignment_id).append(
        "parallel_assignment",
        task=assignment.title,
        metadata={
            "event": event,
            "issue": assignment.issue_number,
            "owner": assignment.owner,
            "branch": assignment.branch,
            "status": assignment.status,
        },
    )


def claim_assignment(
    project_root: Path,
    assignment_id: str,
    owner: str,
    *,
    max_active: int = 2,
) -> AgentAssignment:
    """Claim exclusive ownership; bounded by the active-concurrency limit."""

    claimant = str(owner or "").strip()
    if not claimant:
        raise ValueError("An owner is required to claim an assignment")
    assignment = load_assignment(project_root, assignment_id)
    if assignment.status == "active" and assignment.owner != claimant:
        raise OwnershipError(
            f"Assignment {assignment_id} is owned by {assignment.owner}"
        )
    if assignment.status in {"completed", "abandoned"}:
        raise ValueError(f"Assignment {assignment_id} is already {assignment.status}")
    active = [
        item
        for item in list_assignments(project_root, status="active")
        if item.assignment_id != assignment.assignment_id
    ]
    if len(active) >= max(1, int(max_active)):
        raise RuntimeError(f"Active assignment limit reached ({int(max_active)})")
    claimed = replace(
        assignment, owner=claimant, status="active", updated_at=_now_iso()
    )
    save_assignment(project_root, claimed)
    _record(project_root, claimed, "claimed")
    return claimed


def release_assignment(
    project_root: Path,
    assignment_id: str,
    owner: str,
    *,
    status: str = "completed",
    changed_files: Iterable[str] = (),
) -> AgentAssignment:
    """Release an owned assignment, recording what it actually changed."""

    if status not in {"completed", "abandoned"}:
        raise ValueError("Release status must be completed or abandoned")
    assignment = load_assignment(project_root, assignment_id)
    if assignment.status != "active":
        raise ValueError(
            f"Only active assignments can be released ({assignment.status})"
        )
    if assignment.owner != str(owner or "").strip():
        raise OwnershipError(
            f"Assignment {assignment_id} is owned by {assignment.owner}"
        )
    released = replace(
        assignment,
        status=status,
        changed_files=tuple(str(item) for item in changed_files),
        updated_at=_now_iso(),
    )
    save_assignment(project_root, released)
    _record(project_root, released, "released")
    return released


def create_assignment_worktree(
    repo_root: Path,
    assignment: AgentAssignment,
    *,
    base: str = "origin/main",
    dirty_paths: Iterable[str] = (),
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    """Create a leased assignment worktree, retaining legacy command evidence."""

    lease = create_assignment_lease(
        repo_root,
        assignment,
        base=base,
        dirty_paths=dirty_paths,
        intended_paths=assignment.intended_paths,
        run=run,
    )
    command = lease.evidence.get("command")
    return list(command) if isinstance(command, list) else []


def create_assignment_lease(
    repo_root: Path,
    assignment: AgentAssignment,
    *,
    base: str = "origin/main",
    dirty_paths: Iterable[str] = (),
    intended_paths: Iterable[str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> WorktreeLease:
    """Create one assignment-owned durable worktree lease."""

    scope = (
        tuple(intended_paths)
        if intended_paths is not None
        else assignment.intended_paths
    )
    assessment = classify_dirty_paths(dirty_paths, scope)
    if not assessment.can_proceed:
        joined = ", ".join(assessment.conflicting_paths)
        raise DirtyConflictError(f"User changes overlap requested files: {joined}")
    root = repo_root.expanduser().resolve()
    handle = capture_repository_handle(
        root,
        task_id=assignment.assignment_id,
        run_id=assignment.assignment_id,
        git_run=run,
    )
    manager = WorktreeManager(root, git_run=run, min_free_bytes=0)
    lease = manager.create(
        handle,
        task_id=assignment.assignment_id,
        run_id=assignment.assignment_id,
        owner=assignment.owner,
        branch=assignment.branch,
        target=Path(assignment.worktree),
        base=base,
        planned_paths=scope,
    )
    # Assignment persistence predates durable leases. Preserve that public
    # contract while making an already-persisted assignment point at the
    # manager-owned record that is now authoritative for worktree lifecycle.
    if _assignment_path(root, assignment.assignment_id).is_file():
        save_assignment(
            root,
            replace(
                assignment,
                lease_id=lease.lease_id,
                lease_state=lease.state,
                updated_at=_now_iso(),
            ),
        )
    return lease


def detect_shared_file_overwrites(
    assignments: Iterable[AgentAssignment],
) -> dict[str, tuple[str, ...]]:
    """Map each file changed by more than one assignment to those assignments."""

    touched: dict[str, list[str]] = {}
    for assignment in assignments:
        for changed in assignment.changed_files:
            key = str(_normal(changed))
            ids = touched.setdefault(key, [])
            if assignment.assignment_id not in ids:
                ids.append(assignment.assignment_id)
    return {path: tuple(ids) for path, ids in touched.items() if len(ids) > 1}


def ensure_no_overwrite(
    merged_files: Iterable[str], incoming_files: Iterable[str]
) -> None:
    """Fail closed before a merge would overwrite already-merged files."""

    merged = {str(_normal(item)) for item in merged_files if str(item)}
    colliding = sorted(
        str(_normal(item)) for item in incoming_files if str(_normal(item)) in merged
    )
    if colliding:
        raise SharedFileConflictError(
            "Merging would overwrite files another assignment already changed: "
            + ", ".join(colliding)
        )


def plan_reconciliation(
    assignments: Iterable[AgentAssignment],
) -> dict[str, Any]:
    """Order completed assignments for merging; conflicts go to review.

    Assignments whose changed files never overlap can auto-merge in issue
    order. Any assignment that shares a changed file with another one is
    pulled out of the auto-merge order and listed for sequential,
    human-reviewed reconciliation - shared files are never overwritten
    silently.
    """

    completed = sorted(
        (item for item in assignments if item.status == "completed"),
        key=lambda item: item.issue_number,
    )
    conflicts = detect_shared_file_overwrites(completed)
    conflicted_ids = {value for values in conflicts.values() for value in values}
    merge_order = [
        item.assignment_id
        for item in completed
        if item.assignment_id not in conflicted_ids
    ]
    needs_review = [
        item.assignment_id for item in completed if item.assignment_id in conflicted_ids
    ]
    return {
        "merge_order": merge_order,
        "needs_sequential_review": needs_review,
        "conflicts": {path: list(ids) for path, ids in conflicts.items()},
        "can_auto_reconcile": not conflicts,
    }
