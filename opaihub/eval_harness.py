from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ledger import task_fingerprint
from .model_intelligence import classify_task, recommend_model
from .policy import tier_value
from .state import state_dir


# Offline fixtures for common coding tasks. These describe the cheapest tier
# that should be capable, so the harness can score whether routing over-spends.
# No external prompts or network calls are used.
DEFAULT_FIXTURES: list[dict[str, Any]] = [
    {"task": "show git status and summarize the diff", "expected_max_tier": "L1"},
    {"task": "write a commit message for staged changes", "expected_max_tier": "L1"},
    {"task": "fix the failing unit test in the auth module", "expected_max_tier": "L2"},
    {"task": "debug this traceback from the test run", "expected_max_tier": "L2"},
    {"task": "add a small helper function and a docstring", "expected_max_tier": "L2"},
    {
        "task": "refactor this module without changing behavior",
        "expected_max_tier": "L3",
    },
    {
        "task": "design the architecture for a new billing service",
        "expected_max_tier": "L3",
    },
    {
        "task": "run a security audit for secrets and vulnerabilities",
        "expected_max_tier": "L3",
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def eval_path(project_root: Path) -> Path:
    return state_dir(project_root) / "eval" / "scorecard.json"


def run_eval(
    project_root: Path,
    fixtures: list[dict[str, Any]] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Score routing decisions against offline fixtures.

    For each fixture the harness compares the recommended tier to the cheapest
    capable tier. Lower-or-equal tiers score 1.0; over-spending is penalized.
    Only task hashes and scores are stored - never the prompt text (#26).
    """
    root = project_root.expanduser().resolve()
    fixtures = fixtures or DEFAULT_FIXTURES
    results: list[dict[str, Any]] = []
    total = 0.0

    for fixture in fixtures:
        task = fixture["task"]
        expected = str(fixture.get("expected_max_tier", "L3")).upper()
        classification = classify_task(root, task)
        recommendation = recommend_model(root, task)
        chosen = str(
            recommendation.get("recommended_model_tier")
            or classification["default_tier"]
        ).upper()

        within = tier_value(chosen) <= tier_value(expected)
        # 1.0 if at/below the cheapest capable tier; partial credit per tier over.
        over = max(0, tier_value(chosen) - tier_value(expected))
        score = 1.0 if within else round(max(0.0, 1.0 - 0.34 * over), 3)
        total += score

        results.append(
            {
                "task_hash": task_fingerprint(task),
                "task_type": classification["task_type"],
                "expected_max_tier": expected,
                "chosen_tier": chosen,
                "within_budget_tier": within,
                "policy_decision": recommendation.get("policy_decision"),
                "score": score,
            }
        )

    count = len(results) or 1
    scorecard = {
        "report": "opai-model-eval",
        "created_at": _now_iso(),
        "project": str(root),
        "fixtures": len(results),
        "average_score": round(total / count, 3),
        "cheapest_tier_rate": round(
            sum(1 for item in results if item["within_budget_tier"]) / count, 3
        ),
        "results": results,
        "privacy": "Only task hashes and scores are stored; no prompt text or global config is changed.",
    }

    if write:
        path = eval_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(scorecard, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        scorecard["scorecard_path"] = str(path)

    return scorecard
