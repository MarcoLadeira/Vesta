"""Reproducible local orchestration qualification; no provider/model calls."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess  # nosec B404 - deterministic local Git fixtures
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opaihub.agent_objectives import ObjectiveStore  # noqa: E402
from opaihub.objective_execution import ObjectiveExecutor  # noqa: E402

CASES = {
    "independent_bugs": {
        "parser.py": "def parse(s): return s.strip()\n",
        "totals.py": "def total(xs): return sum(xs)\n",
    },
    "feature_tests_docs": {
        "feature.py": "def enabled(): return True\n",
        "test_feature.py": "from feature import enabled\ndef test_enabled(): assert enabled()\n",
        "README.md": "The feature is enabled.\n",
    },
    "separable_migration": {
        "modules/one.py": "API_VERSION = 2\n",
        "modules/two.py": "API_VERSION = 2\n",
    },
    "independent_research": {
        "findings/api.md": "API contract: version 2.\n",
        "findings/storage.md": "Storage contract: journal.\n",
    },
    "review_and_fixes": {
        "review.md": "Two independent boundary defects require checks.\n",
        "bounds.py": "def valid(n): return 0 <= n <= 100\n",
    },
    "several_issues": {
        "issue_one.py": "VALUE = 'one'\n",
        "issue_two.py": "VALUE = 'two'\n",
        "issue_three.py": "VALUE = 'three'\n",
    },
}


def _git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
    subprocess.run(  # nosec B603 B607 - fixed Git argv, no shell
        ["git", *args], cwd=root, env=env, check=True, capture_output=True, timeout=30
    )


def run_case(name: str, *, parallel: int, worker_seconds: float = 1.0) -> dict:
    files = CASES[name]
    with tempfile.TemporaryDirectory(prefix="opai-agents-benchmark-") as temp:
        root = Path(temp) / "repo"
        root.mkdir()
        _git(root, "init")
        _git(root, "config", "user.email", "benchmark@localhost")
        _git(root, "config", "user.name", "Local qualification")
        (root / ".gitignore").write_text(".opaihub/\n__pycache__/\n", encoding="utf-8")
        (root / "pyproject.toml").write_text(
            "[project]\nname='orchestration-fixture'\nversion='0.0.0'\n",
            encoding="utf-8",
        )
        expected = json.dumps(files)
        verifier = (
            "import json; from pathlib import Path; expected=json.loads("
            + repr(expected)
            + "); assert all(Path(p).read_text(encoding='utf-8') == content for p,content in expected.items())"
        )
        policy = {
            "schema_version": 1,
            "checks": [
                {
                    "id": "static_analysis",
                    "kind": "static_analysis",
                    "requirement": "required",
                    "reason": "Validate all integrated fixture outputs",
                    "command": [sys.executable, "-c", verifier],
                }
            ],
        }
        policy["checks"][0]["command"] = [
            sys.executable,
            "-c",
            "import ast; from pathlib import Path; [ast.parse(p.read_text()) for p in Path('.').rglob('*.py') if '.opaihub' not in p.parts]",
        ]
        policy["checks"].extend(
            [
                {
                    "id": "lint",
                    "kind": "lint",
                    "requirement": "required",
                    "reason": "Fixture whitespace contract",
                    "command": [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; assert all(line == line.rstrip() for p in Path('.').rglob('*.py') if '.opaihub' not in p.parts for line in p.read_text().splitlines())",
                    ],
                },
                {
                    "id": "unit",
                    "kind": "unit",
                    "requirement": "required",
                    "reason": "Validate every integrated fixture output",
                    "command": [sys.executable, "-c", verifier],
                },
            ]
        )
        (root / "opai-verification-policy.yaml").write_text(
            json.dumps(policy), encoding="utf-8"
        )
        for path in files:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
        _git(root, "add", "--all")
        _git(root, "commit", "-m", "Fixture baseline")
        store = ObjectiveStore(root)
        objective = store.create(
            "Implement the agreed fixture changes",
            [
                {
                    "name": f"task-{i}",
                    "objective": f"Produce the agreed content for {path}",
                    "intended_paths": [path],
                    "estimated_cost_usd": "0",
                    "verification_targets": ["static_analysis"],
                }
                for i, path in enumerate(files)
            ],
            max_parallel=parallel,
            budget_usd="0",
        )

        def worker(packet, cancel, activity):
            path = packet["assignment"]["intended_paths"][0]
            if cancel.wait(worker_seconds):
                return {"status": "cancelled"}
            (Path(packet["worktree"]) / path).write_text(files[path], encoding="utf-8")
            return {
                "status": "completed",
                "cost_usd": "0",
                "measurement_kind": "actual",
            }

        started = time.perf_counter()
        result = ObjectiveExecutor(
            root, store=store, worker=worker, worktree_root=root.parent / "workers"
        ).run(objective["objective_id"])
        return {
            "case": name,
            "parallel_limit": parallel,
            "wall_seconds": round(time.perf_counter() - started, 4),
            "cost_usd": result["cost_usd"],
            "cost_complete": result["cost_complete"],
            "conflicts": len(result["integration"].get("conflicts", [])),
            "verification_success": result["status"] == "completed",
            "human_intervention": int(result["status"] != "completed"),
            "status": result["status"],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if not 0 <= args.worker_seconds <= 30:
        parser.error("worker-seconds must be between 0 and 30")
    rows = []
    for name in CASES:
        for limit in (1, 2):
            row = run_case(name, parallel=limit, worker_seconds=args.worker_seconds)
            rows.append(row)
            print(json.dumps(row), flush=True)
    report = {
        "schema_version": 1,
        "kind": "deterministic_orchestration_qualification",
        "provider_calls": 0,
        "worker_delay_seconds": args.worker_seconds,
        "limitation": "Synthetic local workers validate orchestration only. These results do not establish model quality, provider economics, or a competitive speed advantage.",
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if all(row["verification_success"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
