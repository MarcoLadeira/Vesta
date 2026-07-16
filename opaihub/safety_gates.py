"""Deterministic pre-ship gates for coding-agent changes."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Iterable, Sequence

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


# --- Autonomous command capability gate --------------------------------------
#
# ``run_command`` is intentionally much narrower than a shell. Builds and tests
# have dedicated, fixed-command tools; GitHub/network work has consent-aware
# connector tools. This gate therefore accepts only a small set of local Git
# reads and returns canonical argv for execution. A denylist is insufficient:
# executable aliases, wrappers, Git aliases and new subcommands otherwise create
# bypasses whenever one spelling is missed.

_SHELL_OPERATORS = re.compile(r"[|&;<>`\n\r]|\$\(|\$\{|>>|<<")
_LOCAL_GIT_READS = frozenset({"status", "diff", "log", "show", "rev-parse"})
_EXECUTING_GIT_OPTIONS = frozenset(
    {"--ext-diff", "--textconv", "--output", "--no-index"}
)


@dataclass(frozen=True)
class NormalizedCommand:
    """A canonical local-read command that is safe to hand to the ACI."""

    executable: str
    subcommand: str
    argv: tuple[str, ...]


def _is_executable_path(token: str) -> bool:
    return (
        "/" in token
        or "\\" in token
        or PurePosixPath(token).is_absolute()
        or PureWindowsPath(token).is_absolute()
    )


def _has_executing_git_option(arguments: Sequence[str]) -> bool:
    for argument in arguments:
        option = str(argument).lower()
        if option in _EXECUTING_GIT_OPTIONS:
            return True
        if any(option.startswith(prefix + "=") for prefix in _EXECUTING_GIT_OPTIONS):
            return True
    return False


def normalize_autonomous_command(
    raw: str,
    argv: Sequence[str],
) -> NormalizedCommand | None:
    """Return canonical argv for the explicit local Git-read allowlist.

    The executable itself must be a bare ``git``/``git.exe`` basename. Only
    ``--no-pager`` may precede the first effective subcommand. Git options that
    can execute helpers or write output files are rejected as an extra guard
    against repository-controlled configuration.
    """

    if not argv or _SHELL_OPERATORS.search(str(raw or "")):
        return None
    executable_token = str(argv[0]).strip()
    if not executable_token or _is_executable_path(executable_token):
        return None
    executable = executable_token.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable != "git":
        return None

    arguments = [str(item) for item in argv[1:]]
    canonical: list[str] = ["git"]
    if arguments and arguments[0].lower() == "--no-pager":
        canonical.append("--no-pager")
        arguments.pop(0)
    if not arguments:
        return None

    subcommand = arguments.pop(0).lower()
    canonical.append(subcommand)
    if subcommand == "branch":
        if tuple(argument.lower() for argument in arguments) != ("--show-current",):
            return None
        canonical.append("--show-current")
    elif subcommand in _LOCAL_GIT_READS:
        if _has_executing_git_option(arguments):
            return None
        if subcommand in {"diff", "log", "show"}:
            canonical.extend(("--no-ext-diff", "--no-textconv"))
        canonical.extend(arguments)
    else:
        return None
    return NormalizedCommand(executable, subcommand, tuple(canonical))


def classify_run_command(raw: str, argv: Sequence[str]) -> tuple[bool, str]:
    """Backwards-compatible classifier for the canonical capability gate."""

    if not argv:
        return False, "No command was given."
    if _SHELL_OPERATORS.search(str(raw or "")):
        return False, (
            "Shell operators (| & ; > < `` $()) are not supported. Run one "
            "command per call; chain steps as separate run_command calls."
        )
    if normalize_autonomous_command(raw, argv) is not None:
        return True, ""
    return False, (
        "Only bounded local Git reads are allowed: status, diff, log, show, "
        "rev-parse, or exactly branch --show-current (optional --no-pager). "
        "Use dedicated test/build and consent-aware GitHub tools for other work."
    )


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
        str(path)
        for path in changed
        if not any(_overlap(path, target) for target in intended)
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
        if any(
            part in {"production", "prod", "auth", "credentials"} for part in path.parts
        )
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
