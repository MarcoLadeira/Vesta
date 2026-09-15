"""Capability-aware local model fallback for coding workflows (#179).

Planning, context classification, and repair steps should run on a free
local model whenever one is demonstrably capable enough. Capability is
scored deterministically from what the model advertises (parameter size,
coding focus) and adjusted by locally observed outcomes; each task kind
has a quality threshold. When no local model clears the bar the decision
is *requires_confirmation* - Vesta proposes cloud escalation and stops.
Nothing in this module ever contacts a cloud provider or spends money.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .state import state_dir

TASK_KINDS = ("planning", "context_classification", "repair")

# Deterministic capability bar per task kind: the smallest advertised
# parameter count that plausibly handles the work, and the quality score a
# candidate must reach. Repair edits real code, so it needs the most.
_REQUIREMENTS: dict[str, dict[str, float]] = {
    "context_classification": {"min_params_b": 1.0, "threshold": 0.35},
    "planning": {"min_params_b": 3.0, "threshold": 0.50},
    "repair": {"min_params_b": 6.5, "threshold": 0.60},
}

# Named sizes some local runtimes use instead of a parameter count.
_SIZE_ALIASES = {"mini": 3.8, "small": 7.0, "medium": 14.0, "large": 34.0}

_PARAMS = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)

# Observed outcomes only start adjusting the score once there is a real
# sample, and they never lift an incapable model over the bar on their own.
_MIN_OBSERVED_SAMPLES = 3


def parse_param_billions(model_name: str) -> float | None:
    """Extract the advertised parameter count in billions, if any."""

    name = str(model_name or "").lower()
    match = _PARAMS.search(name)
    if match:
        return float(match.group(1))
    for alias, size in _SIZE_ALIASES.items():
        if re.search(rf"\b{alias}\b", name):
            return size
    return None


def capability_score(model: Mapping[str, Any], task_kind: str) -> dict[str, Any]:
    """Deterministically score one local model for one task kind.

    Unknown size fails closed: a model whose capability cannot be verified
    locally is not silently trusted with coding-workflow steps.
    """

    if task_kind not in TASK_KINDS:
        raise ValueError(f"Unknown fallback task kind: {task_kind!r}")
    requirement = _REQUIREMENTS[task_kind]
    name = str(model.get("model") or model.get("id") or "")
    params = parse_param_billions(name)
    reasons: list[str] = []
    if params is None:
        return {
            "model_id": str(model.get("id") or name),
            "score": 0.0,
            "capable": False,
            "reasons": [f"{name or 'model'}: advertised size unknown; fails closed"],
        }
    ratio = params / requirement["min_params_b"]
    score = min(1.0, 0.5 * ratio)
    reasons.append(
        f"{params:g}B vs {requirement['min_params_b']:g}B minimum for {task_kind}"
    )
    if re.search(r"coder|code", name):
        score = min(1.0, score + 0.15)
        reasons.append("coding-focused model")
    if re.search(r"instruct", name):
        score = min(1.0, score + 0.05)
        reasons.append("instruction-tuned")
    capable = ratio >= 1.0
    if not capable:
        reasons.append("below the minimum size for this task kind")
    return {
        "model_id": str(model.get("id") or name),
        "score": round(score, 4),
        "capable": capable,
        "reasons": reasons,
    }


def _stats_path(project_root: Path) -> Path:
    return (
        state_dir(project_root.expanduser().resolve()) / "agent" / "fallback_stats.json"
    )


def _read_stats(project_root: Path) -> dict[str, Any]:
    path = _stats_path(project_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def record_fallback_outcome(
    project_root: Path, model_id: str, task_kind: str, *, success: bool
) -> dict[str, Any]:
    """Record one observed local-fallback outcome (evidence, not opinion)."""

    if task_kind not in TASK_KINDS:
        raise ValueError(f"Unknown fallback task kind: {task_kind!r}")
    stats = _read_stats(project_root)
    key = f"{str(model_id)}::{task_kind}"
    entry = dict(stats.get(key) or {"successes": 0, "failures": 0})
    entry["successes" if success else "failures"] += 1
    stats[key] = entry
    path = _stats_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return entry


def observed_quality(
    project_root: Path, model_id: str, task_kind: str
) -> dict[str, Any]:
    entry = _read_stats(project_root).get(f"{str(model_id)}::{task_kind}") or {}
    successes = int(entry.get("successes") or 0)
    failures = int(entry.get("failures") or 0)
    samples = successes + failures
    return {
        "successes": successes,
        "failures": failures,
        "samples": samples,
        "rate": round(successes / samples, 4) if samples else None,
    }


def _effective_score(base: dict[str, Any], observed: dict[str, Any]) -> float:
    if observed["samples"] < _MIN_OBSERVED_SAMPLES or observed["rate"] is None:
        return float(base["score"])
    return round(0.5 * float(base["score"]) + 0.5 * float(observed["rate"]), 4)


@dataclass(frozen=True)
class FallbackDecision:
    task_kind: str
    model_id: str = ""
    score: float = 0.0
    threshold: float = 0.0
    use_local: bool = False
    cloud_escalation: str = "not_needed"
    reasons: tuple[str, ...] = ()
    candidates: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        value["candidates"] = list(self.candidates)
        return value


def choose_local_fallback(
    project_root: Path,
    task_kind: str,
    models: Iterable[Mapping[str, Any]],
    *,
    threshold: float | None = None,
) -> FallbackDecision:
    """Pick the best capable local model, or ask before going to cloud.

    The returned decision never triggers a cloud call by itself: when no
    local model clears the quality threshold, ``cloud_escalation`` is
    ``requires_confirmation`` and the caller must obtain the user's explicit
    yes through the existing confirmation flow.
    """

    if task_kind not in TASK_KINDS:
        raise ValueError(f"Unknown fallback task kind: {task_kind!r}")
    bar = (
        float(threshold)
        if threshold is not None
        else _REQUIREMENTS[task_kind]["threshold"]
    )
    scored = []
    for model in models:
        base = capability_score(model, task_kind)
        observed = observed_quality(project_root, base["model_id"], task_kind)
        scored.append(
            {
                **base,
                "observed": observed,
                "effective_score": _effective_score(base, observed),
            }
        )
    scored.sort(key=lambda item: (-item["effective_score"], item["model_id"]))
    best = next(
        (item for item in scored if item["capable"] and item["effective_score"] >= bar),
        None,
    )
    if best is not None:
        return FallbackDecision(
            task_kind=task_kind,
            model_id=best["model_id"],
            score=best["effective_score"],
            threshold=bar,
            use_local=True,
            cloud_escalation="not_needed",
            reasons=tuple(best["reasons"]),
            candidates=tuple(scored[:5]),
        )
    reasons = ["no local model met the quality threshold for this task kind"]
    reasons.extend(
        f"{item['model_id']}: score {item['effective_score']:g} < {bar:g}"
        if item["capable"]
        else f"{item['model_id']}: {item['reasons'][-1]}"
        for item in scored[:3]
    )
    if not scored:
        reasons.append("no local models are connected")
    return FallbackDecision(
        task_kind=task_kind,
        threshold=bar,
        use_local=False,
        cloud_escalation="requires_confirmation",
        reasons=tuple(reasons),
        candidates=tuple(scored[:5]),
    )


def plan_workflow_fallback(project_root: Path, task_kind: str) -> FallbackDecision:
    """Decide the fallback for a workflow step from live local discovery."""

    from .local_runner import list_local_models

    return choose_local_fallback(
        project_root, task_kind, list_local_models(project_root)
    )
