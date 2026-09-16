"""Deterministic focused/full test execution with bounded repair attempts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable


__test__ = False


@dataclass(frozen=True)
class TestFailure:
    failed: int
    summary: str
    output: str

    def to_dict(self) -> dict[str, object]:
        return {"failed": self.failed, "summary": self.summary, "output": self.output}


@dataclass(frozen=True)
class TestLoopResult:
    passed: bool
    stage: str
    repair_attempts: int
    commands: tuple[tuple[str, ...], ...]
    failure: TestFailure | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "stage": self.stage,
            "repair_attempts": self.repair_attempts,
            "commands": [list(command) for command in self.commands],
            "failure": self.failure.to_dict() if self.failure else None,
        }


def parse_test_failure(output: str) -> TestFailure:
    text = str(output or "")
    match = re.search(r"(?:^|\s)(\d+) failed(?:,|\s|$)", text, re.IGNORECASE)
    failed = int(match.group(1)) if match else int("FAILED" in text.upper())
    actionable = next(
        (
            line.strip()
            for line in text.splitlines()
            if re.search(r"(?:FAILED|ERROR).*?(?:tests?/|test_)", line, re.IGNORECASE)
        ),
        "Test command failed",
    )
    return TestFailure(
        failed=max(1, failed), summary=actionable[:500], output=text[-20_000:]
    )


class TestLoop:
    __test__ = False

    def __init__(
        self,
        *,
        run: Callable[[list[str]], tuple[int, str]],
        repair: Callable[[TestFailure, int], None],
    ) -> None:
        self.run = run
        self.repair = repair

    def execute(
        self,
        *,
        focused: Iterable[str],
        full: Iterable[str],
        max_repairs: int = 2,
    ) -> TestLoopResult:
        focused_command = [str(item) for item in focused]
        full_command = [str(item) for item in full]
        commands: list[tuple[str, ...]] = []
        attempts = 0
        while True:
            commands.append(tuple(focused_command))
            code, output = self.run(focused_command)
            if code == 0:
                break
            failure = parse_test_failure(output)
            if attempts >= max(0, int(max_repairs)):
                return TestLoopResult(
                    False, "focused", attempts, tuple(commands), failure
                )
            attempts += 1
            self.repair(failure, attempts)

        commands.append(tuple(full_command))
        code, output = self.run(full_command)
        if code != 0:
            return TestLoopResult(
                False, "full", attempts, tuple(commands), parse_test_failure(output)
            )
        return TestLoopResult(True, "full", attempts, tuple(commands))
