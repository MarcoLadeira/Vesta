from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .gui_preferences import load_gui_preferences


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


def safety_warnings(
    project_root: Path, message: str, *, mode: str
) -> list[dict[str, Any]]:
    text = " ".join(message.lower().split())
    if mode not in {"safe-auto", "approve-edits", "ask", "plan"}:
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
        from .context_engine import profile_context

        profile = profile_context(root)
        actions.append(
            _safe_action(
                "context_profile",
                "Profiled context waste",
                {
                    "waste_share": profile.get("waste_share"),
                    "tokens_wasted": profile.get("estimated_tokens_wasted"),
                },
            )
        )

    if any(word in text for word in ["test", "failing", "failure", "bug", "fix"]):
        from .test_select import select_tests

        selected = select_tests(root)
        actions.append(
            _safe_action(
                "test_select",
                "Selected likely tests",
                {
                    "selected": selected.get("selected_tests", []),
                    "targeted_command": selected.get("targeted_command"),
                },
            )
        )

    if any(word in text for word in ["doctor", "status", "client", "claude", "codex"]):
        actions.append(
            _safe_action("doctor", "Checked OPai readiness", {"command": "opai doctor"})
        )

    return actions
