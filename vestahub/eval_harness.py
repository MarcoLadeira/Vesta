from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vesta import legacy

from .atomic_io import atomic_write_text, interprocess_transaction
from .ledger import task_fingerprint
from .model_intelligence import classify_task, recommend_model
from .policy import tier_value
from .state import state_dir


_READ_ATTEMPTS = 20
_READ_RETRY_SECONDS = 0.01


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


def read_scorecard(project_root: Path) -> dict[str, Any]:
    """Read the latest complete scorecard with an explicit integrity state."""

    path = eval_path(project_root.expanduser().resolve())
    for attempt in range(_READ_ATTEMPTS):
        try:
            raw = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return {
                "state": "missing",
                "reason": "scorecard_missing",
                "path": str(path),
            }
        except UnicodeError:
            return {
                "state": "degraded",
                "reason": "scorecard_invalid_json",
                "path": str(path),
            }
        except PermissionError as exc:
            if attempt < _READ_ATTEMPTS - 1:
                time.sleep(_READ_RETRY_SECONDS * (attempt + 1))
                continue
            return {
                "state": "degraded",
                "reason": "scorecard_unreadable",
                "error_type": type(exc).__name__,
                "path": str(path),
            }
        except OSError as exc:
            return {
                "state": "degraded",
                "reason": "scorecard_unreadable",
                "error_type": type(exc).__name__,
                "path": str(path),
            }

    try:
        scorecard = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "state": "degraded",
            "reason": "scorecard_invalid_json",
            "path": str(path),
        }

    sequence = (
        scorecard.get("publication_sequence") if isinstance(scorecard, dict) else None
    )
    if (
        not isinstance(scorecard, dict)
        or scorecard.get("report")
        not in {"vesta-model-eval", legacy.LEGACY_MODEL_EVAL_REPORT}
        or not isinstance(scorecard.get("evaluation_id"), str)
        or not scorecard["evaluation_id"]
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 1
        or not isinstance(scorecard.get("results"), list)
    ):
        return {
            "state": "degraded",
            "reason": "scorecard_schema_invalid",
            "path": str(path),
        }

    return {"state": "ready", "path": str(path), "scorecard": scorecard}


def _publish_scorecard(project_root: Path, scorecard: dict[str, Any]) -> Path:
    """Serialize and atomically publish one explicitly ordered scorecard."""

    path = eval_path(project_root)
    with interprocess_transaction(path):
        current = read_scorecard(project_root)
        previous_sequence = (
            int(current["scorecard"]["publication_sequence"])
            if current["state"] == "ready"
            else 0
        )
        scorecard["publication_sequence"] = previous_sequence + 1
        scorecard["published_at"] = _now_iso()
        atomic_write_text(
            path,
            json.dumps(scorecard, indent=2, sort_keys=True) + "\n",
        )
    return path


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
    evaluation_id = uuid.uuid4().hex
    evaluation_started_at = _now_iso()
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
        "report": "vesta-model-eval",
        "evaluation_id": evaluation_id,
        "evaluation_started_at": evaluation_started_at,
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
        path = _publish_scorecard(root, scorecard)
        scorecard["scorecard_path"] = str(path)

    return scorecard
