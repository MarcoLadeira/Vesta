"""Small, prompt-free persisted status for the current coding workflow."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .state import state_dir


@dataclass(frozen=True)
class WorkflowState:
    task_id: str = ""
    mode: str = "explain"
    phase: str = "idle"
    message: str = "Ready"
    tests_status: str = "not_run"
    pr_url: str = ""
    merge_status: str = "not_requested"
    issue_number: int | None = None
    blockers: tuple[str, ...] = ()
    blocker: str = ""
    next_actions: tuple[str, ...] = ()
    history: tuple[dict[str, object], ...] = ()
    changed_files: tuple[str, ...] = ()
    last_test: dict[str, object] = field(default_factory=dict)
    provider: dict[str, object] = field(default_factory=dict)
    cost: dict[str, object] = field(default_factory=dict)
    safety_gates: dict[str, object] = field(default_factory=dict)
    diff_review: dict[str, object] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["blockers"] = list(self.blockers)
        data["next_actions"] = list(self.next_actions)
        data["history"] = list(self.history)
        data["changed_files"] = list(self.changed_files)
        return data


def workflow_state_path(project_root: Path) -> Path:
    return state_dir(project_root.expanduser().resolve()) / "gui" / "workflow.json"


def save_workflow_state(project_root: Path, state: WorkflowState) -> Path:
    target = workflow_state_path(project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = state.to_dict()
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(target)
    return target


def load_workflow_state(project_root: Path) -> WorkflowState:
    try:
        data = json.loads(workflow_state_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WorkflowState()
    return WorkflowState(
        task_id=str(data.get("task_id") or ""),
        mode=str(data.get("mode") or "explain"),
        phase=str(data.get("phase") or "idle"),
        message=str(data.get("message") or "Ready"),
        tests_status=str(data.get("tests_status") or "not_run"),
        pr_url=str(data.get("pr_url") or ""),
        merge_status=str(data.get("merge_status") or "not_requested"),
        issue_number=(
            int(data["issue_number"]) if data.get("issue_number") is not None else None
        ),
        blockers=tuple(str(item) for item in data.get("blockers") or []),
        blocker=str(data.get("blocker") or ""),
        next_actions=tuple(str(item) for item in data.get("next_actions") or []),
        history=tuple(
            dict(item) for item in data.get("history") or [] if isinstance(item, dict)
        ),
        changed_files=tuple(str(item) for item in data.get("changed_files") or []),
        last_test=dict(data.get("last_test") or {}),
        provider=dict(data.get("provider") or {}),
        cost=dict(data.get("cost") or {}),
        safety_gates=dict(data.get("safety_gates") or {}),
        diff_review=dict(data.get("diff_review") or {}),
        updated_at=str(data.get("updated_at") or ""),
    )
