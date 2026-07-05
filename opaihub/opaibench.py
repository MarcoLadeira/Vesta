"""OPaiBench: deterministic agent-quality benchmark runner and dashboard (#180).

Expands the ``tests/agent_evals`` scenarios into a first-class local
benchmark. Every scenario is deterministic and offline - policy, runtime,
and gate code run against scripted inputs in throwaway directories, so a
run makes zero cloud calls and costs $0.00 (reported as such, honestly).

Dimensions: intent resolution, context quality, repair success, safety
gates. Each run also measures per-scenario latency, and runs append to a
local history so regressions (a scenario that used to pass, or a dimension
score that dropped) are named explicitly instead of averaged away.
"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable

from .state import state_dir

DIMENSIONS = ("intent", "context_quality", "repair_success", "safety_gates")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Deterministic scenarios (offline; throwaway state only)
# --------------------------------------------------------------------------- #
def _intent_latest_instruction_wins() -> bool:
    from .agent_policy import AgentMode, resolve_agent_policy

    policy = resolve_agent_policy(
        "Generic guidance: do not edit files. Latest request: fix the bug and run tests."
    )
    return policy.mode is AgentMode.IMPLEMENT


def _intent_explain_stays_read_only() -> bool:
    from .agent_policy import AgentMode, resolve_agent_policy

    policy = resolve_agent_policy("Explain how the router works. Do not edit files.")
    return policy.mode is AgentMode.EXPLAIN and not policy.allows("edit_files")


def _intent_dangerous_requires_confirmation() -> bool:
    from .agent_policy import resolve_agent_policy

    policy = resolve_agent_policy("Force-push main and hard reset my branch now.")
    return bool(policy.requires_confirmation)


def _intent_negative_constraints_are_not_danger() -> bool:
    from .agent_policy import AgentMode, resolve_agent_policy

    policy = resolve_agent_policy(
        "Implement the feature. Never force-push; ask before production credential changes."
    )
    return policy.mode is AgentMode.IMPLEMENT and not policy.requires_confirmation


def _context_clean_tree_can_proceed() -> bool:
    from .repo_context import classify_dirty_paths

    return classify_dirty_paths((), ("src/app.py",)).status == "clean"


def _context_unrelated_dirt_is_protected_not_blocking() -> bool:
    from .repo_context import classify_dirty_paths

    assessment = classify_dirty_paths(("notes/todo.md",), ("src/app.py",))
    return assessment.status == "unrelated" and assessment.can_proceed


def _context_conflicting_dirt_blocks() -> bool:
    from .repo_context import classify_dirty_paths

    assessment = classify_dirty_paths(("src/app.py",), ("src/",))
    return assessment.status == "conflicting" and not assessment.can_proceed


def _context_unknown_scope_needs_inspection() -> bool:
    from .repo_context import classify_dirty_paths

    return classify_dirty_paths(("src/app.py",), None).status == "needs_inspection"


def _repair_recovers_after_one_fix() -> bool:
    from .test_loop import TestLoop

    outputs = iter([(1, "1 failed FAILED tests/test_x.py"), (0, "ok"), (0, "ok")])
    loop = TestLoop(run=lambda cmd: next(outputs), repair=lambda failure, n: None)
    result = loop.execute(focused=["pytest", "-k", "x"], full=["pytest"])
    return result.passed and result.repair_attempts == 1


def _repair_budget_is_bounded() -> bool:
    from .test_loop import TestLoop

    loop = TestLoop(
        run=lambda cmd: (1, "1 failed FAILED tests/test_x.py"),
        repair=lambda failure, n: None,
    )
    result = loop.execute(focused=["pytest"], full=["pytest"], max_repairs=2)
    return not result.passed and result.repair_attempts == 2


def _repair_workbench_blocks_when_budget_exhausted() -> bool:
    from .aci import Observation
    from .agent_runtime import AgentRuntime, AgentWorkbench, RuntimePhase

    with tempfile.TemporaryDirectory() as tmp:
        runtime = AgentRuntime(Path(tmp), task="opaibench repair scenario")
        runtime.transition(RuntimePhase.INTENT_RESOLVED, message="ready")
        runtime.transition(RuntimePhase.REPO_RESOLVED, message="ready")
        runtime.transition(RuntimePhase.CONTEXT_GATHERING, message="ready")
        runtime.transition(RuntimePhase.IMPLEMENTING, message="ready")
        workbench = AgentWorkbench(runtime, max_repairs=1)
        workbench.observe(Observation("patch_apply", True, {}))
        workbench.observe(Observation("test_run", False, {"stderr": "1 failed"}))
        workbench.observe(Observation("patch_apply", True, {}))
        decision = workbench.observe(
            Observation("test_run", False, {"stderr": "1 failed"})
        )
        return (
            runtime.state.phase is RuntimePhase.BLOCKED
            and decision.action == "request_direction"
        )


def _gates_fail_closed_when_unknown() -> bool:
    from .safety_gates import evaluate_safety_gates

    report = evaluate_safety_gates(changed_files=(), intended_files=())
    return not report.can_ship and "tests" in report.failed


def _gates_catch_secrets() -> bool:
    from .safety_gates import evaluate_safety_gates

    report = evaluate_safety_gates(
        changed_files=("src/app.py",),
        intended_files=("src/app.py",),
        diff_text="api_key = 'sk-abcdefghijklmnopqrstuv'",
    )
    return "secrets" in report.failed


def _gates_catch_destructive_commands() -> bool:
    from .safety_gates import is_destructive_command

    return is_destructive_command(["git", "push", "--force"]) and not (
        is_destructive_command(["git", "push"])
    )


def _gates_flag_unrelated_diffs() -> bool:
    from .safety_gates import evaluate_safety_gates

    report = evaluate_safety_gates(
        changed_files=("src/app.py", "docs/other.md"),
        intended_files=("src/app.py",),
    )
    return "unrelated_diff" in report.failed


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "intent.latest_instruction_wins",
        "dimension": "intent",
        "run": _intent_latest_instruction_wins,
    },
    {
        "id": "intent.explain_stays_read_only",
        "dimension": "intent",
        "run": _intent_explain_stays_read_only,
    },
    {
        "id": "intent.dangerous_requires_confirmation",
        "dimension": "intent",
        "run": _intent_dangerous_requires_confirmation,
    },
    {
        "id": "intent.negative_constraints_are_not_danger",
        "dimension": "intent",
        "run": _intent_negative_constraints_are_not_danger,
    },
    {
        "id": "context.clean_tree_can_proceed",
        "dimension": "context_quality",
        "run": _context_clean_tree_can_proceed,
    },
    {
        "id": "context.unrelated_dirt_protected",
        "dimension": "context_quality",
        "run": _context_unrelated_dirt_is_protected_not_blocking,
    },
    {
        "id": "context.conflicting_dirt_blocks",
        "dimension": "context_quality",
        "run": _context_conflicting_dirt_blocks,
    },
    {
        "id": "context.unknown_scope_needs_inspection",
        "dimension": "context_quality",
        "run": _context_unknown_scope_needs_inspection,
    },
    {
        "id": "repair.recovers_after_one_fix",
        "dimension": "repair_success",
        "run": _repair_recovers_after_one_fix,
    },
    {
        "id": "repair.budget_is_bounded",
        "dimension": "repair_success",
        "run": _repair_budget_is_bounded,
    },
    {
        "id": "repair.workbench_blocks_on_exhaustion",
        "dimension": "repair_success",
        "run": _repair_workbench_blocks_when_budget_exhausted,
    },
    {
        "id": "gates.fail_closed_when_unknown",
        "dimension": "safety_gates",
        "run": _gates_fail_closed_when_unknown,
    },
    {
        "id": "gates.catch_secrets",
        "dimension": "safety_gates",
        "run": _gates_catch_secrets,
    },
    {
        "id": "gates.catch_destructive_commands",
        "dimension": "safety_gates",
        "run": _gates_catch_destructive_commands,
    },
    {
        "id": "gates.flag_unrelated_diffs",
        "dimension": "safety_gates",
        "run": _gates_flag_unrelated_diffs,
    },
)


# --------------------------------------------------------------------------- #
# Runner, history, regressions
# --------------------------------------------------------------------------- #
def opaibench_dir(project_root: Path) -> Path:
    return state_dir(project_root.expanduser().resolve()) / "benchmarks" / "opaibench"


def opaibench_history_path(project_root: Path) -> Path:
    return opaibench_dir(project_root) / "runs.jsonl"


def read_opaibench_history(
    project_root: Path, *, limit: int | None = None
) -> list[dict[str, Any]]:
    path = opaibench_history_path(project_root)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if limit is not None:
        lines = lines[-limit:]
    runs = []
    for line in lines:
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs


def detect_regressions(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> dict[str, Any]:
    """Name what got worse since the previous run; never average it away."""

    if not previous:
        return {"baseline_run_id": None, "scenarios": [], "dimensions": []}
    previous_passed = {
        item["id"]: bool(item["passed"]) for item in previous.get("scenarios") or []
    }
    scenario_regressions = [
        item["id"]
        for item in current.get("scenarios") or []
        if not item["passed"] and previous_passed.get(item["id"], False)
    ]
    dimension_regressions = []
    previous_dimensions = previous.get("dimensions") or {}
    for name, data in (current.get("dimensions") or {}).items():
        before = previous_dimensions.get(name) or {}
        if before and float(data["score"]) < float(before.get("score") or 0.0):
            dimension_regressions.append(
                {
                    "dimension": name,
                    "from": float(before["score"]),
                    "to": float(data["score"]),
                }
            )
    return {
        "baseline_run_id": previous.get("run_id"),
        "scenarios": scenario_regressions,
        "dimensions": dimension_regressions,
    }


def run_opaibench(
    project_root: Path,
    *,
    write: bool = True,
    scenarios: tuple[dict[str, Any], ...] = SCENARIOS,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Run every deterministic scenario locally and report per dimension."""

    root = project_root.expanduser().resolve()
    results = []
    for scenario in scenarios:
        started = clock()
        try:
            passed = bool(scenario["run"]())
            detail = ""
        except Exception as exc:  # noqa: BLE001 - a crash is a failed scenario
            passed = False
            detail = f"{type(exc).__name__}: {exc}"[:200]
        latency_ms = round(max(0.0, (clock() - started)) * 1000.0, 3)
        results.append(
            {
                "id": str(scenario["id"]),
                "dimension": str(scenario["dimension"]),
                "passed": passed,
                "latency_ms": latency_ms,
                "detail": detail,
            }
        )
    dimensions: dict[str, dict[str, Any]] = {}
    for name in DIMENSIONS:
        subset = [item for item in results if item["dimension"] == name]
        passed_count = sum(1 for item in subset if item["passed"])
        dimensions[name] = {
            "passed": passed_count,
            "total": len(subset),
            "score": round(passed_count / len(subset), 4) if subset else 0.0,
            "latency_ms": round(sum(item["latency_ms"] for item in subset), 3),
        }
    total = len(results)
    total_passed = sum(1 for item in results if item["passed"])
    history = read_opaibench_history(root)
    report = {
        "kind": "opaibench",
        "run_id": uuid.uuid4().hex[:12],
        "created_at": _now_iso(),
        "project": str(root),
        "dimensions": dimensions,
        "scenarios": results,
        "totals": {
            "passed": total_passed,
            "total": total,
            "score": round(total_passed / total, 4) if total else 0.0,
            "latency_ms": round(sum(item["latency_ms"] for item in results), 3),
        },
        "cost": {
            "cloud_calls": 0,
            "cost_usd": 0.0,
            "note": "All scenarios run locally against scripted inputs.",
        },
        "privacy": "Local only. No prompts, secrets, or telemetry leave this machine.",
    }
    report["regressions"] = detect_regressions(report, history[-1] if history else None)
    if write:
        path = opaibench_history_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, sort_keys=True) + "\n")
    return report


def latest_opaibench_report(project_root: Path) -> dict[str, Any] | None:
    history = read_opaibench_history(project_root, limit=1)
    return history[-1] if history else None


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
_DIMENSION_LABELS = {
    "intent": "Intent resolution",
    "context_quality": "Context quality",
    "repair_success": "Repair success",
    "safety_gates": "Safety gates",
}


def _score_class(score: float) -> str:
    return "ok" if score >= 1.0 else ("warn" if score >= 0.75 else "bad")


def build_opaibench_dashboard(project_root: Path, *, history_limit: int = 12) -> Path:
    """Render the OPaiBench dashboard HTML from recorded history."""

    root = project_root.expanduser().resolve()
    history = read_opaibench_history(root, limit=history_limit)
    latest = history[-1] if history else None
    if latest is None:
        body = (
            '<p class="empty">No OPaiBench run recorded yet. '
            "Run <code>opai hub opaibench run</code>.</p>"
        )
    else:
        dimension_cards = "\n".join(
            f"<li><strong>{escape(_DIMENSION_LABELS.get(name, name))}</strong>"
            f'<span class="{_score_class(float(data["score"]))}">'
            f"{data['passed']}/{data['total']}</span>"
            f"<em>{data['latency_ms']:.1f} ms</em></li>"
            for name, data in (latest.get("dimensions") or {}).items()
        )
        regressions = latest.get("regressions") or {}
        regression_items = [
            f"<li>Scenario <code>{escape(str(item))}</code> regressed</li>"
            for item in regressions.get("scenarios") or []
        ] + [
            f"<li>Dimension <code>{escape(str(item['dimension']))}</code> dropped "
            f"{item['from']:.2f} → {item['to']:.2f}</li>"
            for item in regressions.get("dimensions") or []
        ]
        regression_html = (
            "\n".join(regression_items)
            if regression_items
            else "<li>No regressions against the previous run.</li>"
        )
        scenario_rows = "\n".join(
            f"<tr><td><code>{escape(item['id'])}</code></td>"
            f"<td>{escape(_DIMENSION_LABELS.get(item['dimension'], item['dimension']))}</td>"
            f'<td class="{"ok" if item["passed"] else "bad"}">'
            f"{'pass' if item['passed'] else 'FAIL'}</td>"
            f"<td>{item['latency_ms']:.1f}</td>"
            f"<td>{escape(item.get('detail') or '')}</td></tr>"
            for item in latest.get("scenarios") or []
        )
        history_rows = "\n".join(
            f"<tr><td><code>{escape(str(run.get('run_id')))}</code></td>"
            f"<td>{escape(str(run.get('created_at')))}</td>"
            f"<td>{run['totals']['passed']}/{run['totals']['total']}</td>"
            f"<td>{run['totals']['latency_ms']:.1f}</td>"
            f"<td>{len((run.get('regressions') or {}).get('scenarios') or [])}</td></tr>"
            for run in reversed(history)
        )
        totals = latest["totals"]
        cost = latest["cost"]
        body = f"""
    <section class="grid">
      <div class="panel">
        <h2>Latest run</h2>
        <p><code>{escape(str(latest["run_id"]))}</code> at {escape(str(latest["created_at"]))}</p>
        <p class="{_score_class(float(totals["score"]))}">{totals["passed"]}/{totals["total"]} scenarios passed</p>
        <p>Total latency: {totals["latency_ms"]:.1f} ms</p>
      </div>
      <div class="panel">
        <h2>Cost</h2>
        <p><strong>${cost["cost_usd"]:.2f}</strong> · {cost["cloud_calls"]} cloud calls</p>
        <p class="muted">{escape(str(cost["note"]))}</p>
      </div>
      <div class="panel">
        <h2>Regressions</h2>
        <ul>{regression_html}</ul>
      </div>
    </section>
    <section class="panel wide">
      <h2>Dimensions</h2>
      <ul class="cards">{dimension_cards}</ul>
    </section>
    <section class="panel wide">
      <h2>Scenarios</h2>
      <table>
        <thead><tr><th>Scenario</th><th>Dimension</th><th>Result</th><th>Latency (ms)</th><th>Detail</th></tr></thead>
        <tbody>{scenario_rows}</tbody>
      </table>
    </section>
    <section class="panel wide">
      <h2>Run history (latest first)</h2>
      <table>
        <thead><tr><th>Run</th><th>When</th><th>Passed</th><th>Latency (ms)</th><th>Regressions</th></tr></thead>
        <tbody>{history_rows}</tbody>
      </table>
    </section>"""
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OPaiBench Dashboard</title>
  <style>
    body {{ font-family: "Nunito", "Segoe UI", system-ui, sans-serif; margin: 0; color: #1b2430; background: #f7f8f5; }}
    header {{ background: #173b35; color: white; padding: 28px 32px; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    section {{ margin: 0 0 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; }}
    .wide {{ grid-column: 1 / -1; }}
    .panel {{ background: white; border: 1px solid #d8ded6; border-radius: 8px; padding: 16px; }}
    ul.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; padding: 0; }}
    .cards li {{ list-style: none; background: #fbfcfa; border: 1px solid #d8ded6; border-radius: 8px; padding: 12px; }}
    .cards span {{ display: block; font-size: 26px; margin-top: 8px; }}
    .cards em {{ color: #647066; font-style: normal; font-size: 12px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #e4e8e2; }}
    code {{ background: #eef2eb; padding: 2px 6px; border-radius: 5px; }}
    .ok {{ color: #0f6b45; font-weight: 700; }}
    .warn {{ color: #8a4d00; font-weight: 700; }}
    .bad {{ color: #9c1f1f; font-weight: 700; }}
    .empty {{ color: #6b5d2e; background: #fff7d6; border: 1px solid #ead68a; border-radius: 8px; padding: 10px; }}
    .muted {{ color: #647066; }}
  </style>
</head>
<body>
  <header>
    <h1>OPaiBench</h1>
    <p>Deterministic agent-quality benchmark. Local only; $0.00 per run.</p>
  </header>
  <main>{body}
  </main>
</body>
</html>
"""
    path = opaibench_dir(root) / "dashboard.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
