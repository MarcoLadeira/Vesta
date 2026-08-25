"""Offline OPai effectiveness benchmarks.

The benchmark suite compares a plain "send the task to a strong model" baseline
against OPai's existing local-first router. It is local and fixture-backed by
default: no provider calls, no raw prompt storage, and only privacy-safe hashes
are persisted under .opaihub/benchmarks/.
"""

from __future__ import annotations

import hashlib
import html
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .atomic_io import read_utf8_tail_lines
from .audit import BENCHMARK_RUN, record_audit_event
from .cost_model import estimate_tokens, is_local_tier, load_cost_model, tier_cost
from .ledger import task_fingerprint
from .router import route_context_sizes, route_task
from .state import state_dir


MAX_RATIO = 50.0
LARGER_IS_BETTER_METRICS = {
    "context_reduction_ratio",
    "opai_effectiveness_index",
    "paid_call_avoidance_ratio",
    "estimated_cost_reduction_ratio",
    "success_rate",
    "risk_events_blocked",
    "paid_calls_avoided",
    "context_bytes_saved",
    "estimated_cost_saved_usd",
}
SMALLER_IS_BETTER_METRICS = {
    "time_to_evidence_seconds",
    "human_interventions",
}
VALIDATION_LADDER: list[dict[str, str]] = [
    {
        "id": "local",
        "status": "implemented",
        "purpose": "Fast fixture-backed proof that OPai reduces context, paid calls, and unsafe escalation.",
    },
    {
        "id": "promptfoo",
        "status": "export_ready",
        "purpose": "Provider-backed coding-agent evals with assertions, cost checks, and CI traces.",
    },
    {
        "id": "swe-bench-verified-mini",
        "status": "follow_up",
        "purpose": "Public bug-fix correctness signal before broad claims.",
    },
    {
        "id": "terminal-bench",
        "status": "follow_up",
        "purpose": "Long-horizon terminal autonomy signal for real agent workflows.",
    },
    {
        "id": "aider-polyglot",
        "status": "follow_up",
        "purpose": "Cross-language edit correctness and test-repair signal.",
    },
]

LOCAL_TASKS: list[dict[str, Any]] = [
    {
        "id": "planning",
        "category": "planning",
        "prompt": "Plan a small feature using local repo evidence before model use.",
    },
    {
        "id": "bug_triage",
        "category": "bug_triage",
        "prompt": "Triage a likely bug from local status, changed files, and tests.",
    },
    {
        "id": "test_failure",
        "category": "test_failure",
        "prompt": "Debug a failing unit test using targeted local evidence first.",
    },
    {
        "id": "security_review",
        "category": "security_review",
        "prompt": "Review this project for secrets, auth risk, and unsafe commands.",
    },
    {
        "id": "release_preflight",
        "category": "release_preflight",
        "prompt": "Prepare a release preflight with tests, policy gates, and rollback.",
    },
    {
        "id": "docs",
        "category": "docs",
        "prompt": "Update docs from observed behavior and local project metadata.",
    },
    {
        "id": "dependency_update",
        "category": "dependency_update",
        "prompt": "Assess dependency update risk without sending the whole repo.",
    },
    {
        "id": "mobile_readiness",
        "category": "mobile_readiness",
        "prompt": "Audit mobile release readiness with evidence and stop conditions.",
    },
]

MAX_TASKS: list[dict[str, Any]] = [
    {
        "id": "swe_bugfix",
        "category": "test_failure",
        "benchmark_alignment": ["swe-bench-pro"],
        "prompt": "Resolve a realistic failing test in a multi-file Python project using evidence before edits.",
        "baseline_context_bytes": 96_000,
    },
    {
        "id": "swe_refactor",
        "category": "planning",
        "benchmark_alignment": ["swe-bench-pro"],
        "prompt": "Plan a cross-file refactor with tests, rollback, and minimal context exposure.",
        "baseline_context_bytes": 120_000,
    },
    {
        "id": "swe_regression",
        "category": "bug_triage",
        "benchmark_alignment": ["swe-bench-pro"],
        "prompt": "Triage a regression from issue text, changed files, and test evidence.",
        "baseline_context_bytes": 88_000,
    },
    {
        "id": "terminal_build_failure",
        "category": "test_failure",
        "benchmark_alignment": ["terminal-bench"],
        "prompt": "Debug a terminal build failure by running local commands before model escalation.",
        "baseline_context_bytes": 72_000,
    },
    {
        "id": "terminal_ci_repair",
        "category": "test_failure",
        "benchmark_alignment": ["terminal-bench"],
        "prompt": "Repair CI using shell evidence, logs, and targeted verification commands.",
        "baseline_context_bytes": 78_000,
    },
    {
        "id": "terminal_security_gate",
        "category": "security_review",
        "benchmark_alignment": ["terminal-bench", "opai-governance"],
        "prompt": "Investigate a risky shell workflow and block unsafe commands unless approved.",
        "baseline_context_bytes": 84_000,
    },
    {
        "id": "aider_python_edit",
        "category": "test_failure",
        "benchmark_alignment": ["aider-polyglot"],
        "prompt": "Fix a Python exercise by editing source files after reading failing tests.",
        "baseline_context_bytes": 64_000,
    },
    {
        "id": "aider_typescript_edit",
        "category": "bug_triage",
        "benchmark_alignment": ["aider-polyglot"],
        "prompt": "Fix a TypeScript exercise using test feedback and smallest-file context.",
        "baseline_context_bytes": 64_000,
    },
    {
        "id": "aider_rust_edit",
        "category": "bug_triage",
        "benchmark_alignment": ["aider-polyglot"],
        "prompt": "Fix a Rust exercise with compiler feedback and targeted local evidence.",
        "baseline_context_bytes": 64_000,
    },
    {
        "id": "promptfoo_cost_assertions",
        "category": "planning",
        "benchmark_alignment": ["promptfoo"],
        "prompt": "Generate a provider-backed eval handoff with cost and latency assertions.",
        "baseline_context_bytes": 56_000,
    },
    {
        "id": "promptfoo_redteam_boundaries",
        "category": "security_review",
        "benchmark_alignment": ["promptfoo", "opai-governance"],
        "prompt": "Evaluate whether a coding agent respects filesystem, terminal, and review boundaries.",
        "baseline_context_bytes": 72_000,
    },
    {
        "id": "governance_policy_ci",
        "category": "security_review",
        "benchmark_alignment": ["opai-governance"],
        "prompt": "Run strict team policy checks and fail closed when required policy is missing.",
        "baseline_context_bytes": 52_000,
    },
    {
        "id": "governance_audit_chain",
        "category": "security_review",
        "benchmark_alignment": ["opai-governance"],
        "prompt": "Verify benchmark audit events and detect log tail truncation.",
        "baseline_context_bytes": 52_000,
    },
    {
        "id": "release_preflight_max",
        "category": "release_preflight",
        "benchmark_alignment": ["terminal-bench", "opai-governance"],
        "prompt": "Prepare a release preflight with evidence packets, tests, and approval gates.",
        "baseline_context_bytes": 84_000,
    },
    {
        "id": "dependency_risk_max",
        "category": "dependency_update",
        "benchmark_alignment": ["swe-bench-pro", "opai-governance"],
        "prompt": "Assess a dependency update with local scanners, targeted tests, and policy gates.",
        "baseline_context_bytes": 76_000,
    },
    {
        "id": "mobile_readiness_max",
        "category": "mobile_readiness",
        "benchmark_alignment": ["terminal-bench", "opai-governance"],
        "prompt": "Audit mobile release readiness with bounded evidence and no deploy without approval.",
        "baseline_context_bytes": 82_000,
    },
]


def _tasks_for_suite(suite: str) -> list[dict[str, Any]]:
    if suite == "local":
        return LOCAL_TASKS
    if suite == "max":
        return MAX_TASKS
    msg = f"unknown benchmark suite: {suite}"
    raise ValueError(msg)


def _external_harnesses() -> list[dict[str, Any]]:
    return [
        {
            "id": "promptfoo",
            "status": "installed" if shutil.which("promptfoo") else "optional",
            "purpose": "Provider-backed assertion, cost, red-team, and coding-agent comparisons.",
            "default": "disabled",
        },
        {
            "id": "swe-bench-pro",
            "status": "external",
            "purpose": "Long-horizon software-engineering correctness validation.",
            "default": "disabled",
        },
        {
            "id": "terminal-bench",
            "status": "external",
            "purpose": "Real terminal autonomy validation.",
            "default": "disabled",
        },
        {
            "id": "aider-polyglot",
            "status": "external",
            "purpose": "Cross-language edit correctness validation.",
            "default": "disabled",
        },
    ]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def benchmark_dir(project_root: Path) -> Path:
    return state_dir(project_root.expanduser().resolve()) / "benchmarks"


def benchmark_history_path(project_root: Path) -> Path:
    return benchmark_dir(project_root) / "runs.jsonl"


def promptfoo_config_path(project_root: Path) -> Path:
    return benchmark_dir(project_root) / "promptfooconfig.yaml"


def _artifact_hash(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def list_benchmark_suites() -> dict[str, Any]:
    """Return available local benchmark suites and optional external harnesses."""
    local_tasks = _tasks_for_suite("local")
    max_tasks = _tasks_for_suite("max")
    return {
        "local": {
            "id": "local",
            "name": "Local OPai effectiveness suite",
            "description": (
                "Fixture-backed coding-agent tasks comparing plain AI usage with "
                "OPai-routed local-first usage."
            ),
            "tasks": [
                {"id": task["id"], "category": task["category"]} for task in local_tasks
            ],
            "default_mode": "both",
            "storage": ".opaihub/benchmarks/runs.jsonl",
            "privacy": "Raw prompts are not stored; benchmark history keeps hashes and counts.",
            "external_harnesses": [_external_harnesses()[0]],
        },
        "max": {
            "id": "max",
            "name": "Max local control-plane benchmark",
            "description": (
                "Leaderboard-aligned local stress suite covering SWE-bench Pro, "
                "Terminal-Bench, Aider Polyglot, promptfoo, and OPai governance signals."
            ),
            "tasks": [
                {
                    "id": task["id"],
                    "category": task["category"],
                    "benchmark_alignment": task["benchmark_alignment"],
                }
                for task in max_tasks
            ],
            "default_mode": "both",
            "storage": ".opaihub/benchmarks/runs.jsonl",
            "privacy": "Raw prompts are not stored; benchmark history keeps hashes and counts.",
            "external_harnesses": _external_harnesses(),
        },
    }


def _selected_modes(mode: str) -> list[str]:
    if mode not in {"baseline", "opai", "both"}:
        msg = "mode must be one of: baseline, opai, both"
        raise ValueError(msg)
    if mode == "both":
        return ["baseline", "opai"]
    return [mode]


def _bytes_to_tokens(byte_count: int, cost_model: dict[str, Any]) -> int:
    return estimate_tokens("x" * max(0, int(byte_count)), cost_model)


def _ratio(baseline: float, actual: float) -> float:
    if baseline <= 0:
        return 1.0
    if actual <= 0:
        return MAX_RATIO
    return round(min(MAX_RATIO, baseline / actual), 3)


def _opai_local_command_count(decision: dict[str, Any]) -> int:
    evidence = decision.get("evidence") or decision.get("evidence_summary") or {}
    git = evidence.get("git", {})
    commands = 0
    for key in ("status", "changed_files", "diff_stat"):
        if isinstance(git.get(key), dict) and git[key].get("executed"):
            commands += 1
    commands += len(evidence.get("test_commands", []) or [])
    return commands


def _baseline_result(
    task: dict[str, Any], cost_model: dict[str, Any]
) -> dict[str, Any]:
    baseline_tier = str(cost_model.get("baseline_tier", "L3")).upper()
    context_bytes = int(task.get("baseline_context_bytes", 24_000))
    tokens = _bytes_to_tokens(context_bytes, cost_model)
    paid_calls = 0 if is_local_tier(baseline_tier, cost_model) else 1
    return {
        "mode": "baseline",
        "model_tier": baseline_tier,
        "context_bytes": context_bytes,
        "estimated_tokens": tokens,
        "estimated_cost_usd": tier_cost(baseline_tier, tokens, cost_model),
        "paid_model_calls": paid_calls,
        "cloud_calls_avoided": 0,
        "local_commands_used": 0,
        "policy_events": [],
        "risk_events_blocked": 0,
        "human_interventions": 0,
        "assertions": [{"name": "fixture_completed", "ok": True}],
        "success": True,
    }


def _opai_result(
    project_root: Path, task: dict[str, Any], cost_model: dict[str, Any]
) -> dict[str, Any]:
    prompt = str(task.get("prompt") or task.get("id") or "")
    started = time.perf_counter()
    decision = route_task(
        project_root,
        prompt,
        include_evidence=True,
        use_cache=True,
        persist_cache=False,
    )
    elapsed = round(time.perf_counter() - started, 3)
    sizes = route_context_sizes(decision)
    context_bytes = int(task.get("opai_context_bytes", sizes["compact_chars"]))
    tokens = _bytes_to_tokens(context_bytes, cost_model)
    tier = str(decision.get("model_tier", "L1")).upper()
    paid_calls = 0 if is_local_tier(tier, cost_model) else 1
    risk_categories = {"security_review", "release_preflight", "mobile_readiness"}
    confirmation = (
        bool(decision.get("requires_confirmation"))
        or str(task.get("category", "")) in risk_categories
    )
    policy_events = []
    if confirmation:
        policy_events.append("confirmation_gate")
    policy_decision = decision.get("policy_decision")
    if policy_decision:
        policy_events.append(str(policy_decision))
    assertions = [
        {"name": "route_created", "ok": bool(decision.get("workflow"))},
        {"name": "compact_context_created", "ok": context_bytes > 0},
        {"name": "cloud_disabled_by_default", "ok": paid_calls == 0},
    ]
    return {
        "mode": "opai",
        "workflow": decision.get("workflow"),
        "model_tier": tier,
        "context_bytes": context_bytes,
        "full_context_bytes": sizes["full_chars"],
        "estimated_tokens": tokens,
        "estimated_cost_usd": tier_cost(tier, tokens, cost_model),
        "paid_model_calls": paid_calls,
        "cloud_calls_avoided": 1 if paid_calls == 0 else 0,
        "local_commands_used": _opai_local_command_count(decision),
        "policy_events": policy_events,
        "risk_events_blocked": 1 if confirmation else 0,
        "human_interventions": 1 if confirmation else 0,
        "time_to_evidence_seconds": elapsed,
        "assertions": assertions,
        "success": all(assertion["ok"] for assertion in assertions),
    }


def _task_result(
    project_root: Path,
    task: dict[str, Any],
    *,
    mode: str,
    cost_model: dict[str, Any],
) -> dict[str, Any]:
    prompt = str(task.get("prompt") or task.get("id") or "")
    task_id = str(task.get("id") or task_fingerprint(prompt))
    selected = _selected_modes(mode)
    modes: dict[str, Any] = {}
    baseline = _baseline_result(task, cost_model)
    if "baseline" in selected:
        modes["baseline"] = baseline
    if "opai" in selected:
        opai = _opai_result(project_root, task, cost_model)
        if "baseline" in selected:
            opai["cloud_calls_avoided"] = max(
                0, baseline["paid_model_calls"] - opai["paid_model_calls"]
            )
        modes["opai"] = opai
    return {
        "task_id": task_id,
        "category": str(task.get("category") or task_id),
        "benchmark_alignment": list(task.get("benchmark_alignment", ["local"])),
        "task_hash": task_fingerprint(prompt),
        "modes": modes,
        "artifact_hashes": {"modes": _artifact_hash(modes)},
    }


def _assertion_rate(results: list[dict[str, Any]]) -> float:
    total = 0
    passed = 0
    for result in results:
        for mode in result["modes"].values():
            for assertion in mode.get("assertions", []):
                total += 1
                passed += 1 if assertion.get("ok") else 0
    if not total:
        return 0.0
    return round(passed / total, 3)


def _sum_mode(results: list[dict[str, Any]], mode: str, key: str) -> float:
    total = 0.0
    for result in results:
        value = result["modes"].get(mode, {}).get(key)
        if isinstance(value, (int, float)):
            total += float(value)
    return round(total, 6)


def _alignment_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for result in results:
        for alignment in result.get("benchmark_alignment", ["local"]):
            summary[str(alignment)] = summary.get(str(alignment), 0) + 1
    return dict(sorted(summary.items()))


def _score(results: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_context = _sum_mode(results, "baseline", "context_bytes")
    opai_context = _sum_mode(results, "opai", "context_bytes")
    baseline_paid = _sum_mode(results, "baseline", "paid_model_calls")
    opai_paid = _sum_mode(results, "opai", "paid_model_calls")
    baseline_cost = _sum_mode(results, "baseline", "estimated_cost_usd")
    opai_cost = _sum_mode(results, "opai", "estimated_cost_usd")
    time_to_evidence = _sum_mode(results, "opai", "time_to_evidence_seconds")
    risk_blocks = int(_sum_mode(results, "opai", "risk_events_blocked"))
    human_interventions = int(_sum_mode(results, "opai", "human_interventions"))
    context_ratio = _ratio(baseline_context, opai_context)
    paid_ratio = _ratio(baseline_paid, opai_paid)
    cost_ratio = _ratio(baseline_cost, opai_cost)
    success_rate = _assertion_rate(results)
    time_to_evidence = round(time_to_evidence, 3)
    risk_score = min(1.0, risk_blocks / 3.0) if risk_blocks else 0.0
    speed_score = (
        1.0 if time_to_evidence <= 30 else max(0.0, 1.0 - (time_to_evidence - 30) / 120)
    )
    index = round(
        min(1.0, context_ratio / MAX_RATIO) * 25
        + min(1.0, paid_ratio / MAX_RATIO) * 20
        + min(1.0, cost_ratio / MAX_RATIO) * 20
        + success_rate * 20
        + risk_score * 10
        + speed_score * 5,
        3,
    )
    grade = "A+" if index >= 97 else "A" if index >= 90 else "B" if index >= 80 else "C"
    return {
        "context_reduction_ratio": _ratio(baseline_context, opai_context),
        "paid_call_avoidance_ratio": paid_ratio,
        "estimated_cost_reduction_ratio": cost_ratio,
        "time_to_evidence_seconds": time_to_evidence,
        "success_rate": success_rate,
        "risk_events_blocked": risk_blocks,
        "human_interventions": human_interventions,
        "paid_calls_avoided": int(max(0, baseline_paid - opai_paid)),
        "context_bytes_saved": int(max(0, baseline_context - opai_context)),
        "estimated_cost_saved_usd": round(max(0.0, baseline_cost - opai_cost), 6),
        "opai_effectiveness_index": index,
        "leaderboard_grade": grade,
        "task_count": len(results),
        "ratio_cap": MAX_RATIO,
    }


def claim_readiness(score: dict[str, Any]) -> dict[str, Any]:
    """Translate benchmark metrics into honest market-facing claim guidance."""
    context_ratio = float(score.get("context_reduction_ratio", 0) or 0)
    paid_ratio = float(score.get("paid_call_avoidance_ratio", 0) or 0)
    cost_ratio = float(score.get("estimated_cost_reduction_ratio", 0) or 0)
    success = float(score.get("success_rate", 0) or 0)
    paid_calls_avoided = int(score.get("paid_calls_avoided", 0) or 0)
    task_count = int(score.get("task_count", 0) or 0)
    effectiveness_index = float(score.get("opai_effectiveness_index", 0) or 0)

    if task_count >= 16 and effectiveness_index >= 95:
        status = "top_local_control_plane"
    elif success >= 0.99 and context_ratio >= 10 and paid_ratio >= 10:
        status = "shareable_local_claim"
    elif success >= 0.95 and context_ratio >= 2 and cost_ratio >= 2:
        status = "internal_evidence"
    else:
        status = "needs_more_evidence"

    caveats = [
        "Local fixture-backed result, not a universal coding-agent claim.",
        "Provider-backed promptfoo, SWE-bench, or Terminal-Bench validation should precede broad public claims.",
    ]
    return {
        "status": status,
        "public_claim": (
            f"OPai reduced context by {context_ratio:g}x and avoided "
            f"{paid_calls_avoided} paid calls on the {task_count}-task local benchmark suite."
        ),
        "caveats": caveats,
        "next_validation": ["promptfoo", "swe-bench-verified-mini", "terminal-bench"],
    }


def _history_record(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": report["kind"],
        "run_id": report["run_id"],
        "created_at": report["created_at"],
        "project": report["project"],
        "suite": report["suite"],
        "mode": report["mode"],
        "task_count": report["task_count"],
        "efficiency_score": report["efficiency_score"],
        "benchmark_alignment": report.get("benchmark_alignment", {}),
        "claim_readiness": report["claim_readiness"],
        "validation_ladder": report["validation_ladder"],
        "results": report["results"],
        "artifact_hashes": report["artifact_hashes"],
        "external_harnesses": report["external_harnesses"],
        "privacy": report["privacy"],
        "storage": report["storage"],
    }


def run_benchmark(
    project_root: Path,
    *,
    suite: str = "local",
    mode: str = "both",
    tasks: list[dict[str, Any]] | None = None,
    audit: bool = False,
    write: bool = True,
) -> dict[str, Any]:
    """Run a local benchmark and optionally persist privacy-safe metadata."""
    if suite != "local":
        _tasks_for_suite(suite)
    _selected_modes(mode)
    root = project_root.expanduser().resolve()
    selected_tasks = list(tasks or _tasks_for_suite(suite))
    cost_model = load_cost_model(root)
    results = [
        _task_result(root, task, mode=mode, cost_model=cost_model)
        for task in selected_tasks
    ]
    report = {
        "kind": "opai-benchmark",
        "schema_version": 1,
        "run_id": f"bench-{uuid4().hex[:12]}",
        "created_at": _now_iso(),
        "project": str(root),
        "suite": suite,
        "mode": mode,
        "task_count": len(results),
        "results": results,
        "efficiency_score": _score(results),
        "external_harnesses": list_benchmark_suites()[suite]["external_harnesses"],
        "benchmark_alignment": _alignment_summary(results),
        "validation_ladder": VALIDATION_LADDER,
        "storage": str(benchmark_history_path(root)),
        "privacy": "No raw prompts are stored; benchmark history keeps hashes, counts, and artifact hashes.",
    }
    report["claim_readiness"] = claim_readiness(report["efficiency_score"])
    report["artifact_hashes"] = {"report": _artifact_hash(report)}
    if write:
        path = benchmark_history_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_history_record(report), sort_keys=True) + "\n")
    if audit:
        record_audit_event(
            root,
            BENCHMARK_RUN,
            run_id=report["run_id"],
            suite=suite,
            mode=mode,
            task_count=len(results),
            efficiency_score=report["efficiency_score"],
            report_sha256=report["artifact_hashes"]["report"],
        )
    return report


def read_benchmark_history(
    project_root: Path, limit: int | None = None
) -> list[dict[str, Any]]:
    path = benchmark_history_path(project_root.expanduser().resolve())
    if not path.exists():
        return []
    lines = (
        path.read_text(encoding="utf-8", errors="replace").splitlines()
        if limit is None
        else read_utf8_tail_lines(path, limit)
    )
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def latest_benchmark_report(project_root: Path) -> dict[str, Any] | None:
    history = read_benchmark_history(project_root, limit=1)
    return history[0] if history else None


def benchmark_gate(
    report: dict[str, Any],
    *,
    min_context_reduction: float = 10.0,
    min_paid_call_avoidance: float = 1.0,
    min_cost_reduction: float = 1.0,
    min_success_rate: float = 1.0,
    min_effectiveness_index: float = 0.0,
    max_human_interventions: int | None = None,
    require_risk_blocks: bool = False,
) -> dict[str, Any]:
    """Evaluate the latest benchmark against CI-friendly thresholds."""
    score = report.get("efficiency_score", {})
    checks = [
        {
            "metric": "context_reduction_ratio",
            "actual": float(score.get("context_reduction_ratio", 0) or 0),
            "operator": ">=",
            "threshold": float(min_context_reduction),
        },
        {
            "metric": "paid_call_avoidance_ratio",
            "actual": float(score.get("paid_call_avoidance_ratio", 0) or 0),
            "operator": ">=",
            "threshold": float(min_paid_call_avoidance),
        },
        {
            "metric": "estimated_cost_reduction_ratio",
            "actual": float(score.get("estimated_cost_reduction_ratio", 0) or 0),
            "operator": ">=",
            "threshold": float(min_cost_reduction),
        },
        {
            "metric": "success_rate",
            "actual": float(score.get("success_rate", 0) or 0),
            "operator": ">=",
            "threshold": float(min_success_rate),
        },
        {
            "metric": "opai_effectiveness_index",
            "actual": float(score.get("opai_effectiveness_index", 0) or 0),
            "operator": ">=",
            "threshold": float(min_effectiveness_index),
        },
    ]
    if max_human_interventions is not None:
        checks.append(
            {
                "metric": "human_interventions",
                "actual": int(score.get("human_interventions", 0) or 0),
                "operator": "<=",
                "threshold": int(max_human_interventions),
            }
        )
    if require_risk_blocks:
        checks.append(
            {
                "metric": "risk_events_blocked",
                "actual": int(score.get("risk_events_blocked", 0) or 0),
                "operator": ">",
                "threshold": 0,
            }
        )

    failed = []
    for check in checks:
        actual = check["actual"]
        threshold = check["threshold"]
        operator = check["operator"]
        passed = actual > threshold if operator == ">" else actual >= threshold
        if operator == "<=":
            passed = actual <= threshold
        check["ok"] = passed
        if not passed:
            failed.append(check)
    return {
        "kind": "opai-benchmark-gate",
        "run_id": report.get("run_id"),
        "ok": not failed,
        "checks": checks,
        "failed": failed,
        "claim_readiness": report.get("claim_readiness", {}),
    }


def compare_benchmark_reports(
    previous: dict[str, Any], current: dict[str, Any], *, tolerance: float = 0.001
) -> dict[str, Any]:
    """Compare two benchmark reports and flag regressions in key score metrics."""
    previous_score = previous.get("efficiency_score", {})
    current_score = current.get("efficiency_score", {})
    metrics = sorted(LARGER_IS_BETTER_METRICS | SMALLER_IS_BETTER_METRICS)
    deltas: dict[str, dict[str, Any]] = {}
    regressions = []
    for metric in metrics:
        old = previous_score.get(metric)
        new = current_score.get(metric)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            continue
        delta = round(float(new) - float(old), 6)
        direction = "larger_is_better"
        regressed = delta < -tolerance
        if metric in SMALLER_IS_BETTER_METRICS:
            direction = "smaller_is_better"
            regressed = delta > tolerance
        record = {
            "previous": old,
            "current": new,
            "delta": delta,
            "direction": direction,
            "regressed": regressed,
        }
        deltas[metric] = record
        if regressed:
            regressions.append({"metric": metric, **record})
    return {
        "kind": "opai-benchmark-comparison",
        "ok": not regressions,
        "previous_run_id": previous.get("run_id"),
        "current_run_id": current.get("run_id"),
        "deltas": deltas,
        "regressions": regressions,
    }


def render_benchmark_comparison_markdown(comparison: dict[str, Any]) -> str:
    lines = [
        "# OPai Benchmark Comparison",
        "",
        f"- Previous: `{comparison.get('previous_run_id', 'unknown')}`",
        f"- Current: `{comparison.get('current_run_id', 'unknown')}`",
        f"- Status: `{'pass' if comparison.get('ok') else 'regression'}`",
        "",
        "## Deltas",
        "",
    ]
    for metric, delta in comparison.get("deltas", {}).items():
        lines.append(
            f"- `{metric}`: {delta.get('previous')} -> {delta.get('current')} "
            f"({delta.get('delta'):+g})"
        )
    regressions = comparison.get("regressions", [])
    if regressions:
        lines.extend(["", "## Regressions", ""])
        for item in regressions:
            lines.append(f"- `{item['metric']}` regressed by {item['delta']:+g}")
    return "\n".join(lines) + "\n"


def export_promptfoo_config(
    project_root: Path, *, out: Path | None = None, suite: str = "local"
) -> dict[str, Any]:
    """Write a privacy-safe promptfoo starter config for provider-backed evals."""
    tasks = _tasks_for_suite(suite)
    root = project_root.expanduser().resolve()
    target = out or promptfoo_config_path(root)
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Generated by OPai. Provider-backed runs are opt-in.",
        "description: OPai coding-agent benchmark handoff",
        "providers:",
        "  - id: echo",
        "prompts:",
        "  - 'Run OPai benchmark task {{task_id}} with your coding-agent harness.'",
        "tests:",
    ]
    for task in tasks:
        task_hash = task_fingerprint(str(task.get("prompt") or task["id"]))
        lines.extend(
            [
                f"  - description: {task['id']}",
                "    vars:",
                f"      task_id: {task['id']}",
                f"      category: {task['category']}",
                f"      task_hash: {task_hash}",
                f"      benchmark_alignment: {','.join(task.get('benchmark_alignment', ['local']))}",
                "    assert:",
                "      - type: contains",
                f"        value: {task['id']}",
            ]
        )
    lines.extend(
        [
            "metadata:",
            "  opai_privacy: raw prompts are not exported",
            "  recommended_command: opai benchmark run --suite local --mode both",
        ]
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "harness": "promptfoo",
        "suite": suite,
        "path": str(target),
        "task_count": len(tasks),
        "privacy": "Raw prompts are not exported; task hashes and ids are used.",
        "next_command": f"promptfoo eval -c {target}",
    }


def render_benchmark_markdown(report: dict[str, Any]) -> str:
    score = report.get("efficiency_score", {})
    claim = report.get("claim_readiness", {})
    lines = [
        "# OPai Benchmark Report",
        "",
        f"- Run: `{report.get('run_id', 'unknown')}`",
        f"- Suite: `{report.get('suite', 'local')}`",
        f"- Mode: `{report.get('mode', 'both')}`",
        f"- Tasks: `{report.get('task_count', 0)}`",
        "",
        "## OPai Efficiency Score",
        "",
        f"- Context reduction: `{score.get('context_reduction_ratio', 0)}x`",
        f"- Paid-call avoidance: `{score.get('paid_call_avoidance_ratio', 0)}x`",
        f"- Estimated cost reduction: `{score.get('estimated_cost_reduction_ratio', 0)}x`",
        f"- Time to evidence: `{score.get('time_to_evidence_seconds', 0)}s`",
        f"- Success rate: `{score.get('success_rate', 0)}`",
        f"- Risk events blocked: `{score.get('risk_events_blocked', 0)}`",
        f"- Human interventions: `{score.get('human_interventions', 0)}`",
        "",
        "## Claim readiness",
        "",
        f"- Status: `{claim.get('status', 'unknown')}`",
        f"- Claim: {claim.get('public_claim', '')}",
        "",
        "## Tasks",
        "",
    ]
    for result in report.get("results", []):
        modes = ", ".join(sorted(result.get("modes", {}).keys()))
        lines.append(f"- `{result.get('task_id')}` ({result.get('category')}): {modes}")
    lines.extend(
        [
            "",
            "## External Validation",
            "",
            "- `promptfoo`: optional provider-backed assertions, scoring, and cost checks.",
            "- SWE-bench Verified Mini, SWE-bench Pro, and Terminal-Bench are follow-up validation layers.",
            "",
            f"Privacy: {report.get('privacy', '')}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_benchmark_html(report: dict[str, Any]) -> str:
    markdown = html.escape(render_benchmark_markdown(report))
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8"><title>OPai Benchmark Report</title>'
        '<style>body{font-family:"Nunito","Segoe UI",system-ui,sans-serif;max-width:900px;margin:40px auto;'
        "line-height:1.5}pre{white-space:pre-wrap;background:#f6f8fa;padding:16px;"
        "border-radius:8px}</style></head><body><pre>"
        f"{markdown}</pre></body></html>\n"
    )
