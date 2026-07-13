"""Deterministic pre-ship gates for coding-agent changes."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
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


# --- run_command safety classifier (#310) -------------------------------------
#
# The agent's general command tool runs argv WITHOUT a shell, so pipes,
# redirects, chaining, and substitution never execute. On top of that no-shell
# guarantee and the cwd confinement the executor enforces, this classifier is a
# defence-in-depth denylist: it refuses commands that reach the network, install
# packages, escalate privilege, read secrets, or damage the machine — the model
# is told to ask the user to run those. Everything else (build, lint, format,
# test, generate, inspect) runs bounded and redacted.

# Shell control characters. Present ⇒ the model tried to chain/redirect/pipe;
# we refuse rather than silently running a neutered single command.
_SHELL_OPERATORS = re.compile(r"[|&;<>`\n\r]|\$\(|\$\{|>>|<<")

# First-token commands that are never run from the tool (basename, lower-cased).
_DENIED_COMMANDS = frozenset(
    {
        # privilege escalation
        "sudo",
        "su",
        "doas",
        "runas",
        # network fetch / remote code
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "telnet",
        "ssh",
        "scp",
        "sftp",
        "rsync",
        "ftp",
        "npx",
        "bunx",
        "pnpx",
        # disk / device / system
        "dd",
        "mkfs",
        "fdisk",
        "parted",
        "mount",
        "umount",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "init",
        "systemctl",
        "service",
        "crontab",
        "at",
        "chown",
        "chgrp",
        # process nukes
        "kill",
        "pkill",
        "killall",
    }
)

# Package managers whose install/add subcommand hits the network and can run
# arbitrary install scripts. (name, subcommand) pairs.
_NETWORK_INSTALL = {
    ("npm", "install"),
    ("npm", "i"),
    ("npm", "add"),
    ("npm", "ci"),
    ("pnpm", "install"),
    ("pnpm", "add"),
    ("yarn", "install"),
    ("yarn", "add"),
    ("bun", "install"),
    ("bun", "add"),
    ("pip", "install"),
    ("pip3", "install"),
    ("gem", "install"),
    ("cargo", "install"),
    ("go", "install"),
    ("go", "get"),
    ("apt", "install"),
    ("apt-get", "install"),
    ("brew", "install"),
    ("choco", "install"),
    ("dnf", "install"),
    ("yum", "install"),
    ("poetry", "add"),
    ("uv", "add"),
    ("uv", "pip"),
}

# Commands that read file contents — refused when the target looks like a secret.
_READ_COMMANDS = frozenset(
    {"cat", "type", "less", "more", "head", "tail", "strings", "xxd", "od", "nl", "bat"}
)
_SECRET_ARG = re.compile(
    r"(?:^|[\\/])(?:\.env(?:\.[\w.-]+)?|.*\.pem|.*\.key|id_[rd]sa[\w.]*|"
    r"credentials(?:\.\w+)?|secrets?\.\w+|\.npmrc|\.netrc|\.pgpass)$",
    re.IGNORECASE,
)
# Command wrappers that launch another command — they would sidestep the
# argv[0] checks below (`env FOO=x curl ...`, `xargs rm`, `timeout 5 ssh ...`),
# so they are refused outright rather than reasoned about.
_COMMAND_WRAPPERS = frozenset(
    {
        "env",
        "xargs",
        "nice",
        "ionice",
        "nohup",
        "setsid",
        "timeout",
        "stdbuf",
        "watch",
        "chroot",
        "unbuffer",
        "script",
        "time",
        "eval",
        "exec",
    }
)
# Bare environment dumps can exfiltrate secrets.
_ENV_DUMP = frozenset({"printenv", "set"})


def _basename(token: str) -> str:
    return PurePosixPath(str(token).replace("\\", "/")).name.lower()


def classify_run_command(raw: str, argv: Sequence[str]) -> tuple[bool, str]:
    """Decide whether the agent's ``run_command`` may run ``argv``.

    Returns ``(allowed, reason)``. ``allowed`` is False for any command that is
    empty, uses shell operators, is destructive, or falls in the denylist above;
    ``reason`` is a short, model-readable explanation. Fails closed: anything the
    classifier cannot parse is refused.
    """
    if not argv:
        return False, "No command was given."
    if _SHELL_OPERATORS.search(str(raw or "")):
        return False, (
            "Shell operators (| & ; > < `` $()) are not supported. Run one "
            "command per call; chain steps as separate run_command calls."
        )
    if is_destructive_command(argv):
        return False, (
            "This is a destructive command. Ask the user to run it themselves."
        )
    first = _basename(argv[0])
    if first in _COMMAND_WRAPPERS:
        return False, (
            f"`{first}` wraps another command and is not allowed. Run the target "
            "command directly."
        )
    if first in _DENIED_COMMANDS:
        return False, (
            f"`{first}` (network/privilege/system access) is not allowed from "
            "run_command. Ask the user to run it themselves."
        )
    second = _basename(argv[1]) if len(argv) > 1 else ""
    if (first, second) in _NETWORK_INSTALL:
        return False, (
            f"`{first} {second}` reaches the network and runs install scripts. "
            "Ask the user to install dependencies themselves."
        )
    if first == "chmod" and any(
        token in {"777", "-r", "-rf", "a+w", "o+w", "+w"}
        for token in (str(a).lower() for a in argv[1:])
    ):
        return False, "World-writable or recursive chmod is not allowed."
    if first in _READ_COMMANDS and any(_SECRET_ARG.search(str(a)) for a in argv[1:]):
        return False, "Reading credential/secret files is not allowed."
    if first in _ENV_DUMP and len(argv) == 1:
        return False, "Dumping the environment can leak secrets and is not allowed."
    return True, ""


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
