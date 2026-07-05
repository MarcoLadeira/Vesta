"""Deterministic pre-ship gates for coding-agent changes."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Iterable

_SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|(?:api[_-]?key|token|secret)\s*[=:]\s*[^\s]{8,})",
    re.IGNORECASE,
)
_RISKY_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "permissions.yaml",
}


def is_destructive_command(command: Iterable[str]) -> bool:
    """Catch destructive Git/filesystem operations, including shell wrappers."""

    text = " ".join(str(item) for item in command).lower()
    patterns = (
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\b",
        r"\bgit\s+push\b[^\r\n]*(?:--force(?:-with-lease)?|\s-f(?:\s|$))",
        r"\bgit\s+branch\s+(?:-d|-D|--delete)\b",
        r"\brm\s+-rf\b",
        r"\brmdir\s+/s\b",
        r"\bremove-item\b[^\r\n]*-recurse\b",
        r"\bdel\s+/[sq]\b",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)
def _normal(value: str) -> PurePosixPath:
    return PurePosixPath(str(value).replace("\\", "/").strip("/"))


def _overlap(left: PurePosixPath, right: PurePosixPath) -> bool:
    return left == right or left in right.parents or right in left.parents


@dataclass(frozen=True)
class SafetyGateReport:
    gates: dict[str, bool]
    failed: tuple[str, ...]
    details: dict[str, tuple[str, ...]]

    @property
    def can_ship(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["can_ship"] = self.can_ship
        return value


def evaluate_safety_gates(
    *,
    changed_files: Iterable[str],
    intended_files: Iterable[str],
    command: Iterable[str] = (),
    diff_text: str = "",
    tests_pass: bool = False,
    correct_branch: bool = False,
    no_conflicts: bool = False,
    pr_checks_pass: bool = False,
    production_authorized: bool = False,
    risky_files_reviewed: bool = False,
) -> SafetyGateReport:
    changed = tuple(_normal(item) for item in changed_files)
    intended = tuple(_normal(item) for item in intended_files)
    unrelated = tuple(
        str(path) for path in changed if not any(_overlap(path, target) for target in intended)
    )
    risky = tuple(
        str(path)
        for path in changed
        if path.name.lower() in _RISKY_NAMES
        or str(path).startswith((".github/workflows/", "migrations/"))
    )
    destructive = is_destructive_command(command)
    production_paths = tuple(
        str(path)
        for path in changed
        if any(part in {"production", "prod", "auth", "credentials"} for part in path.parts)
    )
    gates = {
        "secrets": not bool(_SECRET.search(diff_text)),
        "risky_files": not risky or risky_files_reviewed,
        "destructive_command": not destructive,
        "production_auth": not production_paths or production_authorized,
        "unrelated_diff": not unrelated,
        "tests": bool(tests_pass),
        "correct_branch": bool(correct_branch),
        "conflicts": bool(no_conflicts),
        "pr_checks": bool(pr_checks_pass),
    }
    failed = tuple(name for name, passed in gates.items() if not passed)
    return SafetyGateReport(
        gates,
        failed,
        {
            "risky_files": risky,
            "unrelated_files": unrelated,
            "production_auth_files": production_paths,
        },
    )
