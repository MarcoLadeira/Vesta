from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .gui_preferences import load_gui_preferences

# Per-message overhead cache (#153): profiling the repo (a full walk) and
# selecting tests are gated behind the cheap repo fingerprint so a normal
# chat send never re-walks an unchanged repository. Keyed by
# (project root, fingerprint) → the last computed tool-trace row data.
_REPO_WORK_CACHE: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
_REPO_WORK_LOCK = threading.RLock()


def clear_repo_work_cache() -> None:
    with _REPO_WORK_LOCK:
        _REPO_WORK_CACHE.clear()


def _cached_repo_work(
    root: Path, kind: str, compute: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """Return cached tool-row data for ``kind``, recomputing only on repo change."""
    from .evidence_cache import assess_repo_fingerprint

    assessment = assess_repo_fingerprint(root)
    if not assessment.cacheable:
        return compute()
    key = (str(root), assessment.digest)
    with _REPO_WORK_LOCK:
        entry = _REPO_WORK_CACHE.get(key)
        if entry is not None and kind in entry:
            return entry[kind]
    data = compute()
    with _REPO_WORK_LOCK:
        # A new fingerprint supersedes stale entries so the cache stays small.
        stale = [
            item for item in _REPO_WORK_CACHE if item[0] == str(root) and item != key
        ]
        for item in stale:
            _REPO_WORK_CACHE.pop(item, None)
        _REPO_WORK_CACHE.setdefault(key, {})[kind] = data
    return data


@dataclass
class ToolActionSpec:
    id: str
    label: str
    risk: str = "read"
    mutates: bool = False
    auto_allowed: bool = True
    confirmation_required: bool = False
    source: str = "intent-router"
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["result"] = self.result or {}
        return data


_DESTRUCTIVE_TERMS = [
    "rm -rf",
    "remove-item -recurse",
    "delete the repository",
    "delete repo",
    "git reset --hard",
    "git clean",
    "format drive",
    "drop database",
]

_NETWORK_TERMS = ["curl ", "invoke-webrequest", "wget ", "npm publish", "twine upload"]

# Words that open a question or explanation, not an instruction to run something
# (#142). "explain what `git reset --hard` does" must not be blocked just
# because it mentions a risky command — the user is asking about it, not asking
# to run it. High precision on the imperative side: real destructive requests
# ("delete the repo with rm -rf", "rm -rf the project") do not start with these.
_INQUIRY_LEADERS = frozenset(
    {
        "what",
        "what's",
        "whats",
        "why",
        "how",
        "when",
        "which",
        "who",
        "whose",
        "where",
        "explain",
        "describe",
        "define",
        "is",
        "are",
        "does",
        "can",
        "could",
        "should",
        "would",
        "tell",
    }
)


def _is_inquiry(text: str) -> bool:
    """True when the message reads as a question/explanation, not a command."""
    stripped = text.strip()
    tokens = stripped.split()
    if not tokens:
        return False
    first = tokens[0].strip("`'\"*.,").lower()
    return first in _INQUIRY_LEADERS


def safety_warnings(
    project_root: Path, message: str, *, mode: str
) -> list[dict[str, Any]]:
    text = " ".join(message.lower().split())
    if mode not in {"safe-auto", "approve-edits", "auto-edits", "ask", "plan"}:
        return []
    # A question about a risky command is not a request to run it (#142). The
    # real backstop for anything that *does* execute is the command-level gate
    # at run time (sandbox.classify_command); this pre-flight scan only guards
    # against imperative destructive prompts.
    if _is_inquiry(text):
        return []
    prefs = load_gui_preferences(project_root)
    deny = [
        str(item).lower()
        for item in prefs.get("safe_auto", {}).get("deny_commands", [])
    ]
    warnings = []
    for term in [*_DESTRUCTIVE_TERMS, *_NETWORK_TERMS, *deny]:
        if term and term.lower() in text:
            warnings.append(
                {
                    "severity": "danger",
                    "reason": f"Blocked by Safe Auto policy: {term}",
                    "term": term,
                }
            )
            break
    return warnings


def _safe_action(
    action_id: str, label: str, result: dict[str, Any] | None = None
) -> dict[str, Any]:
    return ToolActionSpec(id=action_id, label=label, result=result or {}).to_dict()


def route_intents(
    project_root: Path, message: str, *, mode: str = "safe-auto"
) -> list[dict[str, Any]]:
    """Return the safe, read-only OPai tools that should run automatically."""
    root = project_root.expanduser().resolve()
    text = message.lower()
    actions: list[dict[str, Any]] = []

    from .model_intelligence import recommend_model

    rec = recommend_model(root, message)
    actions.append(
        _safe_action(
            "model_recommend",
            "Selected cheapest capable model",
            {
                "tier": rec.get("recommended_model_tier"),
                "model": rec.get("recommended_model_id"),
                "task_type": rec.get("task_type"),
                "requires_confirmation": rec.get("requires_confirmation"),
            },
        )
    )

    from .budget import budget_gate
    from .cost_model import load_cost_model, tier_cost

    cost_model = load_cost_model(root)
    tier = str(rec.get("recommended_model_tier") or "L1").upper()
    task_tokens = int(cost_model.get("default_task_tokens", 6000))
    provider_type = "local" if tier in {"L0", "L1"} else "cloud"
    gate = budget_gate(
        root,
        next_cost_usd=tier_cost(tier, task_tokens, cost_model),
        tier=tier,
        provider_type=provider_type,
        estimated_tokens=task_tokens,
    )
    actions.append(
        _safe_action(
            "budget_gate",
            "Checked budget firewall",
            {
                "allowed": gate.get("allowed"),
                "decision": gate.get("decision"),
                "reason": gate.get("reason"),
            },
        )
    )

    from .ledger import summarize_ledger

    ledger = summarize_ledger(root)
    actions.append(
        _safe_action(
            "savings_ledger",
            "Read savings ledger",
            {
                "routes": ledger.get("route_count"),
                "saved": ledger.get("estimated_savings_usd"),
                "spent": ledger.get("estimated_actual_spend_usd"),
            },
        )
    )

    if any(
        word in text
        for word in ["context", "cheap", "cost", "repo", "summarize", "fix"]
    ):
        # Full repo walk gated behind the cheap fingerprint (#153): an
        # unchanged repo reuses the last profile instead of re-walking on
        # every "fix this bug".
        def _profile() -> dict[str, Any]:
            from .context_engine import profile_context

            profile = profile_context(root)
            return {
                "waste_share": profile.get("waste_share"),
                "tokens_wasted": profile.get("estimated_tokens_wasted"),
            }

        actions.append(
            _safe_action(
                "context_profile",
                "Profiled context waste",
                _cached_repo_work(root, "context", _profile),
            )
        )

    if any(word in text for word in ["test", "failing", "failure", "bug", "fix"]):

        def _tests() -> dict[str, Any]:
            from .test_select import select_tests

            selected = select_tests(root)
            return {
                "selected": selected.get("selected_tests", []),
                "targeted_command": selected.get("targeted_command"),
            }

        actions.append(
            _safe_action(
                "test_select",
                "Selected likely tests",
                _cached_repo_work(root, "tests", _tests),
            )
        )

    if any(word in text for word in ["doctor", "status", "client", "claude", "codex"]):
        actions.append(
            _safe_action("doctor", "Checked OPai readiness", {"command": "opai doctor"})
        )

    return actions
