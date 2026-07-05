"""Durable, validated coding-agent runtime owned by OPai."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from .state import state_dir
from .workflow_ledger import WorkflowLedger, redact_structure


class RuntimePhase(str, Enum):
    IDLE = "idle"
    INTENT_RESOLVED = "intent_resolved"
    REPO_RESOLVED = "repo_resolved"
    ISSUE_SELECTED = "issue_selected"
    CONTEXT_GATHERING = "context_gathering"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    REPAIRING = "repairing"
    REVIEWING_DIFF = "reviewing_diff"
    PREPARING_PR = "preparing_pr"
    PR_CREATED = "pr_created"
    MERGE_CHECK_RUNNING = "merge_check_running"
    MERGED = "merged"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


_FORWARD: dict[RuntimePhase, set[RuntimePhase]] = {
    RuntimePhase.IDLE: {RuntimePhase.INTENT_RESOLVED},
    RuntimePhase.INTENT_RESOLVED: {RuntimePhase.REPO_RESOLVED},
    RuntimePhase.REPO_RESOLVED: {
        RuntimePhase.ISSUE_SELECTED,
        RuntimePhase.CONTEXT_GATHERING,
        RuntimePhase.PLANNING,
    },
    RuntimePhase.ISSUE_SELECTED: {
        RuntimePhase.CONTEXT_GATHERING,
        RuntimePhase.PLANNING,
    },
    RuntimePhase.CONTEXT_GATHERING: {
        RuntimePhase.PLANNING,
        RuntimePhase.AWAITING_APPROVAL,
        RuntimePhase.IMPLEMENTING,
        RuntimePhase.REVIEWING_DIFF,
    },
    RuntimePhase.PLANNING: {
        RuntimePhase.AWAITING_APPROVAL,
        RuntimePhase.IMPLEMENTING,
        RuntimePhase.COMPLETED,
    },
    RuntimePhase.AWAITING_APPROVAL: {RuntimePhase.IMPLEMENTING, RuntimePhase.COMPLETED},
    RuntimePhase.IMPLEMENTING: {RuntimePhase.TESTING, RuntimePhase.REVIEWING_DIFF},
    RuntimePhase.TESTING: {RuntimePhase.REPAIRING, RuntimePhase.REVIEWING_DIFF},
    RuntimePhase.REPAIRING: {RuntimePhase.TESTING},
    RuntimePhase.REVIEWING_DIFF: {
        RuntimePhase.IMPLEMENTING,
        RuntimePhase.TESTING,
        RuntimePhase.PREPARING_PR,
        RuntimePhase.COMPLETED,
    },
    RuntimePhase.PREPARING_PR: {RuntimePhase.PR_CREATED},
    RuntimePhase.PR_CREATED: {RuntimePhase.MERGE_CHECK_RUNNING, RuntimePhase.COMPLETED},
    RuntimePhase.MERGE_CHECK_RUNNING: {RuntimePhase.MERGED},
    RuntimePhase.MERGED: {RuntimePhase.COMPLETED},
    RuntimePhase.BLOCKED: set(),
    RuntimePhase.FAILED: set(),
    RuntimePhase.COMPLETED: set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _valid_task_id(value: str) -> str:
    task_id = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id):
        raise ValueError("Invalid workflow task id")
    return task_id


@dataclass(frozen=True)
class RuntimeEvent:
    sequence: int
    from_phase: str
    phase: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    blocker: str = ""
    next_actions: tuple[str, ...] = ()
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["next_actions"] = list(self.next_actions)
        return value


@dataclass(frozen=True)
class AgentRuntimeState:
    task_id: str
    phase: RuntimePhase = RuntimePhase.IDLE
    message: str = "Ready"
    metadata: dict[str, Any] = field(default_factory=dict)
    blocker: str = ""
    next_actions: tuple[str, ...] = ()
    history: tuple[RuntimeEvent, ...] = ()
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "phase": self.phase.value,
            "message": self.message,
            "metadata": self.metadata,
            "blocker": self.blocker,
            "next_actions": list(self.next_actions),
            "history": [event.to_dict() for event in self.history],
            "updated_at": self.updated_at,
        }


class AgentRuntime:
    def __init__(
        self, project_root: Path, *, task: str, task_id: str | None = None
    ) -> None:
        self.project_root = project_root.expanduser().resolve()
        self.task = task
        self.task_id = _valid_task_id(task_id or uuid.uuid4().hex[:16])
        self.state = AgentRuntimeState(task_id=self.task_id)
        self.ledger = WorkflowLedger(self.project_root, task_id=self.task_id)
        self._persist()

    @property
    def path(self) -> Path:
        return state_dir(self.project_root) / "agent" / "tasks" / f"{self.task_id}.json"

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.state.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _move(
        self,
        phase: RuntimePhase,
        *,
        message: str,
        metadata: dict[str, Any] | None = None,
        blocker: str = "",
        next_actions: Iterable[str] = (),
        validate: bool = True,
    ) -> AgentRuntimeState:
        previous = self.state.phase
        allowed = _FORWARD.get(previous, set()) | {
            RuntimePhase.BLOCKED,
            RuntimePhase.FAILED,
        }
        if validate and phase not in allowed:
            raise ValueError(
                f"Invalid runtime transition: {previous.value} -> {phase.value}"
            )
        created = _now()
        safe_metadata = redact_structure(dict(metadata or {}))
        event = RuntimeEvent(
            sequence=len(self.state.history) + 1,
            from_phase=previous.value,
            phase=phase.value,
            message=str(redact_structure(str(message))),
            metadata=dict(safe_metadata),
            blocker=str(redact_structure(str(blocker))),
            next_actions=tuple(
                str(redact_structure(str(item))) for item in next_actions
            ),
            created_at=created,
        )
        self.state = AgentRuntimeState(
            task_id=self.task_id,
            phase=phase,
            message=event.message,
            metadata=event.metadata,
            blocker=event.blocker,
            next_actions=event.next_actions,
            history=(*self.state.history, event),
            updated_at=created,
        )
        self._persist()
        self.ledger.append(
            "state_transition",
            task=self.task,
            phase=phase.value,
            from_phase=previous.value,
            metadata=event.metadata,
            blocker=event.blocker,
        )
        return self.state

    def transition(
        self,
        phase: RuntimePhase,
        *,
        message: str,
        metadata: dict[str, Any] | None = None,
        next_actions: Iterable[str] = (),
    ) -> AgentRuntimeState:
        return self._move(
            RuntimePhase(phase),
            message=message,
            metadata=metadata,
            next_actions=next_actions,
        )

    def block(
        self, reason: str, *, next_actions: Iterable[str] = ()
    ) -> AgentRuntimeState:
        return self._move(
            RuntimePhase.BLOCKED,
            message="OPai needs a safe resolution before continuing",
            blocker=reason,
            next_actions=next_actions,
        )

    def fail(
        self, reason: str, *, next_actions: Iterable[str] = ()
    ) -> AgentRuntimeState:
        return self._move(
            RuntimePhase.FAILED,
            message="The workflow failed",
            blocker=reason,
            next_actions=next_actions,
        )

    def resume(
        self,
        phase: RuntimePhase,
        *,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRuntimeState:
        if self.state.phase not in {RuntimePhase.BLOCKED, RuntimePhase.FAILED}:
            raise ValueError("Only blocked or failed workflows can be resumed")
        if phase in {RuntimePhase.IDLE, RuntimePhase.BLOCKED, RuntimePhase.FAILED}:
            raise ValueError("Resume target must be an active workflow phase")
        return self._move(phase, message=message, metadata=metadata, validate=False)

    @classmethod
    def load(cls, project_root: Path, task_id: str) -> AgentRuntime:
        root = project_root.expanduser().resolve()
        task_id = _valid_task_id(task_id)
        path = state_dir(root) / "agent" / "tasks" / f"{task_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        instance = cls.__new__(cls)
        instance.project_root = root
        instance.task = ""
        instance.task_id = str(task_id)
        instance.ledger = WorkflowLedger(root, task_id=str(task_id))
        history = tuple(
            RuntimeEvent(
                sequence=int(item["sequence"]),
                from_phase=str(item["from_phase"]),
                phase=str(item["phase"]),
                message=str(item["message"]),
                metadata=dict(item.get("metadata") or {}),
                blocker=str(item.get("blocker") or ""),
                next_actions=tuple(item.get("next_actions") or ()),
                created_at=str(item.get("created_at") or ""),
            )
            for item in data.get("history") or ()
        )
        instance.state = AgentRuntimeState(
            task_id=str(data["task_id"]),
            phase=RuntimePhase(data.get("phase", "idle")),
            message=str(data.get("message") or ""),
            metadata=dict(data.get("metadata") or {}),
            blocker=str(data.get("blocker") or ""),
            next_actions=tuple(data.get("next_actions") or ()),
            history=history,
            updated_at=str(data.get("updated_at") or ""),
        )
        return instance


@dataclass(frozen=True)
class AgentDecision:
    action: str
    reason: str
    phase: RuntimePhase

    def to_dict(self) -> dict[str, str]:
        return {"action": self.action, "reason": self.reason, "phase": self.phase.value}


class AgentWorkbench:
    """Small ReAct controller: observe evidence, transition, choose one next action."""

    def __init__(self, runtime: AgentRuntime, *, max_repairs: int = 2) -> None:
        self.runtime = runtime
        self.max_repairs = max(0, int(max_repairs))
        self.repair_attempts = 0

    def _decision(self, action: str, reason: str) -> AgentDecision:
        decision = AgentDecision(action, reason, self.runtime.state.phase)
        self.runtime.ledger.append(
            "agent_decision",
            task=self.runtime.task,
            metadata=decision.to_dict(),
        )
        return decision

    def observe(self, observation: Any) -> AgentDecision:
        kind = str(getattr(observation, "kind", "observation"))
        ok = bool(getattr(observation, "ok", False))
        data = dict(getattr(observation, "data", {}) or {})
        self.runtime.ledger.append(
            "observation",
            task=self.runtime.task,
            metadata={"kind": kind, "ok": ok, "data": data},
        )
        phase = self.runtime.state.phase
        if phase is RuntimePhase.IMPLEMENTING and kind in {"patch_apply", "command"}:
            if not ok:
                self.runtime.fail(
                    "Implementation action failed",
                    next_actions=("inspect the structured error",),
                )
                return self._decision("inspect_error", "implementation failed")
            self.runtime.transition(
                RuntimePhase.TESTING,
                message="Implementation changed; focused tests are next",
                next_actions=("run focused tests",),
            )
            return self._decision(
                "run_focused_tests", "implementation produced a change"
            )
        if phase is RuntimePhase.TESTING and kind == "test_run":
            if ok:
                self.runtime.transition(
                    RuntimePhase.REVIEWING_DIFF,
                    message="Tests passed; review the resulting diff",
                    metadata={"test": data},
                    next_actions=("review diff", "run safety gates"),
                )
                return self._decision("review_diff", "test evidence passed")
            if self.repair_attempts >= self.max_repairs:
                self.runtime.block(
                    "Test repair limit reached",
                    next_actions=(
                        "review the last failure",
                        "choose a product direction",
                    ),
                )
                return self._decision("request_direction", "repair budget exhausted")
            self.repair_attempts += 1
            self.runtime.transition(
                RuntimePhase.REPAIRING,
                message=f"Repairing test failure ({self.repair_attempts}/{self.max_repairs})",
                metadata={"test_failure": data},
                next_actions=("apply the smallest repair",),
            )
            return self._decision("repair_failure", "test evidence failed")
        if phase is RuntimePhase.REPAIRING and kind in {"patch_apply", "command"}:
            if not ok:
                self.runtime.block(
                    "Repair action failed",
                    next_actions=("inspect the repair error",),
                )
                return self._decision("inspect_error", "repair failed")
            self.runtime.transition(
                RuntimePhase.TESTING,
                message="Repair applied; rerun the focused tests",
                next_actions=("rerun focused tests",),
            )
            return self._decision("run_focused_tests", "repair needs verification")
        return self._decision(
            "inspect_observation", f"no automatic transition for {phase.value}/{kind}"
        )
