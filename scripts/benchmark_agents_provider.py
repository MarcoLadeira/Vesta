"""Prepare or explicitly execute paired real-provider objective qualification."""

from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
import json
import os
from pathlib import Path
import subprocess  # nosec B404 - fixed Git and verification commands
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.agents_benchmark_cases import CASES, oracle_source  # noqa: E402
from opaihub.atomic_io import atomic_write_text  # noqa: E402


def money(value):
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(
            "Budget must be a finite positive decimal"
        ) from exc
    if not amount.is_finite() or amount <= 0:
        raise argparse.ArgumentTypeError("Budget must be a finite positive decimal")
    return amount


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(  # nosec B603 B607 - fixed Git argv, no shell
        ["git", "-c", "core.hooksPath=", *args],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        timeout=60,
    )
    return result.stdout.decode("utf-8").strip()


def prepare_run(directory, name):
    root = directory / "repo"
    root.mkdir(parents=True)
    case = CASES[name]
    oracle = directory / "acceptance.py"
    source = oracle_source(name)
    oracle.write_bytes(source.encode("utf-8"))
    for relative, content in case["files"].items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (root / ".gitignore").write_text(".opaihub/\n__pycache__/\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\nname='agents-provider-qualification'\nversion='0.0.0'\n",
        encoding="utf-8",
    )
    policy = {
        "schema_version": 1,
        "checks": [
            {
                "id": "static_analysis",
                "kind": "static_analysis",
                "requirement": "required",
                "reason": "Parse integrated Python sources without importing them",
                "command": [
                    sys.executable,
                    "-c",
                    "import ast; from pathlib import Path; [ast.parse(p.read_text(encoding='utf-8')) for p in Path('.').rglob('*.py') if '.opaihub' not in p.parts]",
                ],
            },
            {
                "id": "lint",
                "kind": "lint",
                "requirement": "required",
                "reason": "Reject whitespace errors in the integrated changes",
                "command": ["git", "diff", "--check", "HEAD^", "HEAD"],
            },
            {
                "id": "unit",
                "kind": "unit",
                "requirement": "required",
                "reason": "Independent behavioral contract outside assignment worktrees",
                "command": [sys.executable, str(oracle)],
            },
        ],
    }
    (root / "opai-verification-policy.yaml").write_text(
        json.dumps(policy), encoding="utf-8"
    )
    git(root, "init")
    git(root, "config", "user.email", "benchmark@localhost")
    git(root, "config", "user.name", "Provider qualification")
    git(root, "add", "--all")
    git(root, "commit", "-m", "Immutable qualification baseline")
    check = subprocess.run(  # nosec B603 - fixed trusted acceptance fixture
        [sys.executable, str(oracle)],
        cwd=root,
        capture_output=True,
        timeout=60,
    )
    if check.returncode == 0:
        raise ValueError(f"{name}: baseline unexpectedly satisfies acceptance")
    return root, hashlib.sha256(source.encode()).hexdigest()


def plan(args):
    names = args.case or list(CASES)
    runs = []
    for repeat in range(args.repeats):
        for index, name in enumerate(names):
            for parallel in (1, 2) if (repeat + index) % 2 == 0 else (2, 1):
                case = CASES[name]
                runs.append(
                    {
                        "id": f"{repeat + 1}-{name}-{parallel}",
                        "case": name,
                        "parallel_limit": parallel,
                        "fixture_sha256": hashlib.sha256(
                            json.dumps(case, sort_keys=True).encode()
                        ).hexdigest(),
                        "assignments": deepcopy(case["assignments"]),
                    }
                )
    return {
        "schema_version": 1,
        "kind": "provider_objective_qualification",
        "model": args.model,
        "allow_cloud": args.allow_cloud,
        "account_quota": args.account_quota,
        "budget_per_run_usd": str(args.budget_per_run_usd)
        if args.budget_per_run_usd
        else None,
        "aggregate_admission_budget_usd": str(args.budget_per_run_usd * len(runs))
        if args.budget_per_run_usd
        else None,
        "maximum_assignment_attempts": sum(len(run["assignments"]) for run in runs),
        "workspace_root": str(args.workspace_root.resolve()),
        "timeout_seconds_per_run": args.timeout_seconds,
        "runs": runs,
        "limitations": [
            "Fixed decomposition isolates execution orchestration; it does not benchmark model planning.",
            "One repeat is a smoke qualification, not a statistical or competitive performance claim.",
            "Automated research/review/docs checks validate specified facts, not full human-assessed quality.",
            "Dollar caps govern admission; provider billing can arrive late. Unknown costs stop capped runs. Explicit account-quota runs preserve unknown cost without substituting zero and cannot establish dollar savings.",
            "Sequential means the same bounded assignments with concurrency one, not a separate agent product.",
        ],
    }


def execute_run(args, entry, directory):
    from opaihub.agent_objectives import ObjectiveStore
    from opaihub.objective_execution import ObjectiveExecutor

    directory.mkdir(parents=True, exist_ok=False)
    workspace = args.workspace_root.resolve() / uuid.uuid4().hex[:12]
    root, oracle_digest = prepare_run(workspace, entry["case"])
    baseline = git(root, "rev-parse", "HEAD")
    assignments = deepcopy(entry["assignments"])
    reserve = None
    if args.budget_per_run_usd is not None:
        reserve = args.budget_per_run_usd / Decimal(len(assignments))
        reserve = str(reserve.quantize(Decimal("0.000001"), rounding=ROUND_DOWN))
    for row in assignments:
        row.update(model=args.model, budget_usd=reserve, estimated_cost_usd=reserve)
    store = ObjectiveStore(root)
    objective = store.create(
        f"Complete the {entry['case']} qualification contracts and preserve unrelated files.",
        assignments,
        model=args.model,
        allow_cloud=args.allow_cloud,
        max_parallel=entry["parallel_limit"],
        budget_usd=str(args.budget_per_run_usd) if args.budget_per_run_usd else None,
        shared_context="Use only this fixture repository. No network research, publishing, credential changes or other agents. Acceptance checks are independently maintained.",
    )
    cancel = threading.Event()
    timer = threading.Timer(args.timeout_seconds, cancel.set)
    timer.daemon = True
    started = time.perf_counter()
    timer.start()
    try:
        result = ObjectiveExecutor(
            root, store=store, worktree_root=workspace / "workers"
        ).run(objective["objective_id"], cancel)
    finally:
        timer.cancel()
        timer.join()
    oracle_unchanged = (
        hashlib.sha256((workspace / "acceptance.py").read_bytes()).hexdigest()
        == oracle_digest
    )
    authority_unchanged = git(root, "rev-parse", "HEAD") == baseline and not git(
        root, "status", "--porcelain"
    )
    atomic_write_text(directory / "objective.json", json.dumps(result, indent=2) + "\n")
    return {
        "id": entry["id"],
        "case": entry["case"],
        "parallel_limit": entry["parallel_limit"],
        "fixture_sha256": entry["fixture_sha256"],
        "objective_id": objective["objective_id"],
        "wall_seconds": round(time.perf_counter() - started, 4),
        "cost_usd": result["cost_usd"],
        "cost_complete": result["cost_complete"],
        "verification_success": result["status"] == "completed"
        and oracle_unchanged
        and authority_unchanged,
        "oracle_unchanged": oracle_unchanged,
        "authority_unchanged": authority_unchanged,
        "timed_out": cancel.is_set(),
        "status": result["status"],
        "conflicts": len(result["integration"].get("conflicts", [])),
        "requires_human_intervention": result["status"] != "completed",
        "human_interventions_performed": 0,
        "observed_routes": [
            {
                "model": row.get("observed_model"),
                "provider": row.get("observed_provider"),
                "routing": row.get("result", {}).get("routing"),
            }
            for row in result["assignments"]
        ],
        "evidence": str(directory / "objective.json"),
        "retained_workspace": str(workspace),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New retained evidence directory"
    )
    parser.add_argument(
        "--model", required=True, help="Explicit concrete installed provider/model ID"
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path.home() / ".opaihub" / "benchmark",
        help="Short retained fixture path, separate from the report directory",
    )
    funding = parser.add_mutually_exclusive_group(required=True)
    funding.add_argument("--budget-per-run-usd", type=money)
    funding.add_argument(
        "--account-quota",
        action="store_true",
        help="Use the explicitly authorized account allowance without a dollar cap; retain unknown costs",
    )
    parser.add_argument("--case", action="append", choices=sorted(CASES))
    parser.add_argument("--repeats", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--allow-cloud",
        action="store_true",
        help="Explicitly authorize the selected cloud/account provider",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Dispatch model workers; omission writes a reviewable plan only",
    )
    args = parser.parse_args(argv)
    if args.model.lower() == "auto" or not args.model.strip():
        parser.error("A concrete model is required for reproducible comparisons")
    if not 30 <= args.timeout_seconds <= 3600:
        parser.error("timeout-seconds must be between 30 and 3600")
    if os.name == "nt" and len(str(args.workspace_root.resolve())) > 48:
        parser.error(
            "Choose a shorter workspace-root (at most 48 characters) for Windows evidence paths"
        )
    if args.account_quota and (
        not args.model.startswith("account:") or not args.allow_cloud
    ):
        parser.error(
            "account-quota requires a concrete account model and explicit allow-cloud"
        )
    if args.budget_per_run_usd is not None and args.budget_per_run_usd < Decimal(
        "0.000003"
    ):
        parser.error("budget-per-run-usd must cover at least 0.000001 per assignment")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    report = plan(args)
    report.update(executed=False, results=[])
    output = args.output / "report.json"
    atomic_write_text(output, json.dumps(report, indent=2) + "\n")
    if not args.execute:
        print(str(output))
        return 0
    report["executed"] = True
    for entry in report["runs"]:
        try:
            row = execute_run(args, entry, args.output / entry["id"])
        except Exception as exc:
            report["stopped_reason"] = (
                f"Run {entry['id']} raised {type(exc).__name__}; inspect retained evidence before any retry."
            )
            atomic_write_text(output, json.dumps(report, indent=2) + "\n")
            return 1
        report["results"].append(row)
        if args.budget_per_run_usd is not None and (
            not row["cost_complete"]
            or Decimal(row["cost_usd"]) > args.budget_per_run_usd
        ):
            report["stopped_reason"] = (
                "Unknown or over-budget cost; no further provider dispatch."
            )
        if (
            not row["oracle_unchanged"]
            or not row["authority_unchanged"]
            or row["timed_out"]
        ):
            report["stopped_reason"] = (
                "Acceptance integrity or timeout gate failed; inspect retained processes and evidence."
            )
        atomic_write_text(output, json.dumps(report, indent=2) + "\n")
        print(json.dumps(row), flush=True)
        if report.get("stopped_reason"):
            return 1
    return 0 if all(row["verification_success"] for row in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
