from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from .loader import hub_root, load_registry


def _rules(project_root: Path | None = None) -> dict[str, Any] | None:
    """Load the risky-command registry; ``None`` means the store is unavailable.

    Callers must treat ``None`` as fail-closed: an unloadable policy store must
    never silently downgrade unknown commands to ``allow``.
    """
    path = hub_root(project_root) / "security" / "risky_commands.yaml"
    try:
        data = load_registry(path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# Matches the prefix of a command that is just a shell wrapper, e.g.
# "bash -c", "cmd /c", "powershell -Command".  We strip these to expose the
# inner command so policy rules fire on the *actual* destructive operation.
_SHELL_WRAPPER_RE = re.compile(
    r"^(?:"
    r"bash\s+-c\s+|"
    r"sh\s+-c\s+|"
    r"zsh\s+-c\s+|"
    r"dash\s+-c\s+|"
    r"fish\s+-c\s+|"
    r"cmd(?:\.exe)?\s+/[cC]\s+|"
    r"powershell(?:\.exe)?\s+(?:-[Cc]ommand|-[Cc])\s+|"
    r"pwsh(?:\.exe)?\s+(?:-[Cc]ommand|-[Cc])\s+"
    r")[\"']?",
    re.IGNORECASE,
)


def _strip_shell_wrapper(command: str) -> str | None:
    """Peel one shell-wrapper layer; return inner command or None if not wrapped."""
    m = _SHELL_WRAPPER_RE.match(command.strip())
    if not m:
        return None
    inner = command[m.end() :].rstrip("'\"").strip()
    return inner if inner else None


def _normalize(command: str) -> str:
    """Lowercase, normalise pipe spacing, collapse whitespace.

    The key invariant: ``url|sh`` and ``url | sh`` must produce the same string
    so that policy rules written with spaces around ``|`` catch both forms.
    """
    cmd = command.lower()
    cmd = re.sub(r"\s*\|\s*", " | ", cmd)
    return " ".join(cmd.split())


def _matches_one(command: str, pattern: str) -> bool:
    normalized = _normalize(command)
    rule = " ".join(pattern.lower().split())
    return fnmatch.fnmatch(normalized, rule) or rule in normalized


def _candidates(command: str) -> list[str]:
    """Return the command plus up to two levels of shell-wrapper-unwrapped forms."""
    result = [command]
    inner = _strip_shell_wrapper(command)
    if inner:
        result.append(inner)
        inner2 = _strip_shell_wrapper(inner)
        if inner2:
            result.append(inner2)
    return result


def classify_command(command: str, project_root: Path | None = None) -> dict[str, Any]:
    rules = _rules(project_root)
    if rules is None:
        # Fail closed: without the policy store we cannot prove a command is
        # safe, so everything requires explicit confirmation (F23 hardening).
        return {
            "command": command,
            "decision": "confirm",
            "requires_confirmation": True,
            "denied": False,
            "matched_rule": None,
            "reason": "Command policy store unavailable; confirmation required.",
        }
    variants = _candidates(command)

    for pattern in rules.get("deny", []):
        for candidate in variants:
            if _matches_one(candidate, pattern):
                return {
                    "command": command,
                    "decision": "deny",
                    "requires_confirmation": False,
                    "denied": True,
                    "matched_rule": pattern,
                    "reason": "Blocked by OPai denied command policy.",
                }
    for pattern in rules.get("confirm", []):
        for candidate in variants:
            if _matches_one(candidate, pattern):
                return {
                    "command": command,
                    "decision": "confirm",
                    "requires_confirmation": True,
                    "denied": False,
                    "matched_rule": pattern,
                    "reason": "Requires explicit user confirmation before execution.",
                }
    return {
        "command": command,
        "decision": "allow",
        "requires_confirmation": False,
        "denied": False,
        "matched_rule": None,
        "reason": "No risky command rule matched.",
    }
