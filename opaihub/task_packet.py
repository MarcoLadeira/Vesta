"""Compact provider-neutral context packet for a single coding task."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class TaskPacket:
    user_request: str
    mode: str
    repo: dict[str, Any]
    issue: dict[str, Any] = field(default_factory=dict)
    dirty: dict[str, Any] = field(default_factory=dict)
    relevant_files: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    done_criteria: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    verification_policy: dict[str, Any] = field(default_factory=dict)
    last_failure: dict[str, Any] = field(default_factory=dict)
    next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in (
            "relevant_files",
            "constraints",
            "allowed_actions",
            "forbidden_actions",
            "done_criteria",
            "tests",
        ):
            value[key] = list(value[key])
        return value


def _strings(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in items if str(item)))


def build_task_packet(
    *,
    user_request: str,
    mode: str,
    repo: Mapping[str, Any],
    issue: Mapping[str, Any] | None = None,
    dirty: Mapping[str, Any] | None = None,
    relevant_files: Iterable[str] = (),
    constraints: Iterable[str] = (),
    allowed_actions: Iterable[str] = (),
    forbidden_actions: Iterable[str] = (),
    done_criteria: Iterable[str] = (),
    tests: Iterable[str] = (),
    verification_policy: Mapping[str, Any] | None = None,
    last_failure: Mapping[str, Any] | None = None,
    next_action: str = "",
) -> TaskPacket:
    return TaskPacket(
        user_request=str(user_request),
        mode=str(mode),
        repo=dict(repo),
        issue=dict(issue or {}),
        dirty=dict(dirty or {}),
        relevant_files=_strings(relevant_files),
        constraints=_strings(constraints),
        allowed_actions=_strings(allowed_actions),
        forbidden_actions=_strings(forbidden_actions),
        done_criteria=_strings(done_criteria),
        tests=_strings(tests),
        verification_policy=dict(verification_policy or {}),
        last_failure=dict(last_failure or {}),
        next_action=str(next_action),
    )
