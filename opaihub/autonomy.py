from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .budget import budget_status
from .gui_preferences import DEFAULT_MODE, MODES, load_gui_preferences
from .sandbox import classify_command

RISK_READ = "read"
RISK_SAFE_LOCAL = "safe_local"
RISK_EDIT = "edit"
RISK_NETWORK = "network"
RISK_PAID = "paid"
RISK_DESTRUCTIVE = "destructive"
RISK_OUTSIDE_PROJECT = "outside_project"
RISK_DEPLOY = "deploy"

DECISION_ALLOW = "allow"
DECISION_CONFIRM = "confirm"
DECISION_DENY = "deny"

_DESTRUCTIVE_TERMS = [
    "rm -rf",
    "remove-item -recurse",
    "git reset --hard",
    "git clean",
    "format ",
    "del /s",
]
_DEPLOY_TERMS = [
    "git push",
    "npm publish",
    "twine upload",
    "wrangler deploy",
    "vercel --prod",
    "terraform apply",
    "kubectl delete",
]
_NETWORK_TERMS = [
    "curl ",
    "wget ",
    "invoke-webrequest",
    "iwr ",
    "irm ",
    "pip install",
    "npm install",
    "pnpm install",
    "yarn add",
]


def resolve_mode(
    project_root: Path, requested_mode: str | None = None
) -> dict[str, Any]:
    """Resolve requested GUI/runner mode to the safe effective mode.

    Durable Full Auto requires an explicit pinned preference. Passing
    ``requested_mode`` means the caller is acting on an immediate user action;
    that can run Full Auto for the current request, but stale saved preferences
    cannot quietly reopen the app in Full Auto.
    """
    root = project_root.expanduser().resolve()
    prefs = load_gui_preferences(root)
    requested = requested_mode or str(prefs.get("default_mode") or DEFAULT_MODE)
    if requested not in MODES:
        requested = DEFAULT_MODE
    pinned = bool(prefs.get("full_auto_pinned"))
    explicit = requested_mode is not None
    effective = requested
    warning = ""
    if requested == "full-auto" and not pinned and not explicit:
        effective = DEFAULT_MODE
        warning = (
            "Full Auto was reset to Safe Auto because it was not explicitly pinned."
        )
    elif requested == "full-auto" and not pinned and explicit:
        warning = "Full Auto is active for this explicit request only."
    return {
        "requested_mode": requested,
        "effective_mode": effective,
        "full_auto_pinned": pinned,
        "warning": warning,
        "modes": list(MODES),
        "risk_policy": {
            "default_mode": DEFAULT_MODE,
            "full_auto_requires_pin": True,
            "safe_auto_blocks": [RISK_DESTRUCTIVE],
            "safe_auto_confirms": [
                RISK_NETWORK,
                RISK_PAID,
                RISK_OUTSIDE_PROJECT,
                RISK_DEPLOY,
            ],
        },
        "next_action": (
            "Continue in Safe Auto."
            if effective != "full-auto"
            else "Review every changed file before committing."
        ),
    }


def _matched_any(text: str, terms: list[str]) -> str | None:
    lowered = " ".join(text.lower().split())
    for term in terms:
        if term in lowered:
            return term
    return None


def _absolute_paths(text: str) -> list[Path]:
    paths: list[Path] = []
    # Windows paths, including paths inside single/double quoted Python snippets.
    for match in re.finditer(r"[A-Za-z]:\\[^'\"\s]+(?:\\[^'\"\s]+)*", text):
        paths.append(Path(match.group(0)))
    # Conservative POSIX absolute paths.
    for match in re.finditer(r"(?<![:\w])/(?:[^'\"\s]+/)*[^'\"\s]+", text):
        paths.append(Path(match.group(0)))
    return paths


def _outside_project(command: str, root: Path) -> bool:
    if not command:
        return False
    resolved_root = root.expanduser().resolve()
    for candidate in _absolute_paths(command):
        try:
            resolved = candidate.expanduser().resolve(strict=False)
        except OSError:
            continue
        if resolved == resolved_root:
            continue
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            return True
    return False


def _risk_for(
    root: Path,
    *,
    mode: str,
    message: str | None,
    command: str | None,
    model_id: str | None,
    mutates: bool,
) -> tuple[str, str | None]:
    text = " ".join(part for part in [message or "", command or ""] if part)
    if command:
        policy = classify_command(command, root)
        matched = policy.get("matched_rule")
        if policy["decision"] == "deny":
            return RISK_DESTRUCTIVE, str(matched or "denied command policy")
        deploy = _matched_any(command, _DEPLOY_TERMS)
        destructive = _matched_any(command, _DESTRUCTIVE_TERMS)
        if deploy:
            return RISK_DEPLOY, str(matched or deploy)
        if destructive:
            return RISK_DESTRUCTIVE, str(matched or destructive)
        if mutates and _outside_project(command, root):
            return RISK_OUTSIDE_PROJECT, "command references a path outside the project"
        if policy["decision"] == "confirm":
            return RISK_SAFE_LOCAL, str(matched or "confirmation command policy")
    destructive = _matched_any(text, _DESTRUCTIVE_TERMS) if text else None
    deploy = _matched_any(text, _DEPLOY_TERMS) if text else None
    network = _matched_any(command, _NETWORK_TERMS) if command else None
    if destructive:
        return RISK_DESTRUCTIVE, destructive
    if deploy:
        return RISK_DEPLOY, deploy
    if network:
        return RISK_NETWORK, network
    if model_id and str(model_id).startswith("account:"):
        return RISK_PAID, "paid account model"
    if mutates:
        return RISK_EDIT, "workspace edit"
    if mode in {"safe-auto", "full-auto"} and command:
        return RISK_SAFE_LOCAL, "safe local command"
    return RISK_READ, None


def evaluate_action(
    project_root: Path,
    mode: str,
    *,
    message: str | None = None,
    command: str | None = None,
    model_id: str | None = None,
    mutates: bool = False,
) -> dict[str, Any]:
    """Return the single OPai autonomy decision for an action."""
    root = project_root.expanduser().resolve()
    selected_mode = mode if mode in MODES else DEFAULT_MODE
    risk, trigger = _risk_for(
        root,
        mode=selected_mode,
        message=message,
        command=command,
        model_id=model_id,
        mutates=mutates,
    )
    decision = DECISION_ALLOW
    reason = "Allowed by OPai autonomy policy."

    budget = budget_status(root)
    if budget.get("panic") and risk == RISK_PAID:
        decision = DECISION_DENY
        reason = "Panic mode is on, so paid/cloud model calls are blocked."
    elif selected_mode in {"ask", "plan"} and risk not in {RISK_READ, RISK_PAID}:
        decision = DECISION_DENY
        reason = f"{selected_mode} mode is read-only."
    elif selected_mode == "approve-edits" and risk in {
        RISK_EDIT,
        RISK_SAFE_LOCAL,
        RISK_NETWORK,
        RISK_OUTSIDE_PROJECT,
        RISK_DEPLOY,
    }:
        decision = DECISION_CONFIRM
        reason = "Approve Edits requires review before this action."
    elif selected_mode == "safe-auto":
        if risk == RISK_DESTRUCTIVE:
            decision = DECISION_DENY
            reason = "Safe Auto blocks destructive actions."
        elif risk in {RISK_NETWORK, RISK_PAID, RISK_OUTSIDE_PROJECT, RISK_DEPLOY}:
            decision = DECISION_CONFIRM
            reason = "Safe Auto requires confirmation for this risk class."
    elif selected_mode == "full-auto" and risk in {RISK_DESTRUCTIVE, RISK_DEPLOY}:
        decision = DECISION_CONFIRM
        reason = "Full Auto still requires review for destructive or deploy actions."

    return {
        "mode": selected_mode,
        "risk": risk,
        "decision": decision,
        "reason": reason,
        "trigger": trigger,
        "requires_confirmation": decision == DECISION_CONFIRM,
        "blocked": decision == DECISION_DENY,
    }
