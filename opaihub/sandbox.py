from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from .loader import hub_root, load_registry


def _rules(project_root: Path | None = None) -> dict[str, Any]:
    path = hub_root(project_root) / "security" / "risky_commands.yaml"
    try:
        return load_registry(path)
    except Exception:
        return {"deny": [], "confirm": [], "safe_examples": []}


def _matches(command: str, pattern: str) -> bool:
    normalized = " ".join(command.lower().split())
    rule = " ".join(pattern.lower().split())
    return fnmatch.fnmatch(normalized, rule) or rule in normalized


def classify_command(command: str, project_root: Path | None = None) -> dict[str, Any]:
    rules = _rules(project_root)
    for pattern in rules.get("deny", []):
        if _matches(command, pattern):
            return {
                "command": command,
                "decision": "deny",
                "requires_confirmation": False,
                "denied": True,
                "matched_rule": pattern,
                "reason": "Blocked by OPai denied command policy.",
            }
    for pattern in rules.get("confirm", []):
        if _matches(command, pattern):
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
