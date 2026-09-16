import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

from scripts import benchmark_agents_provider as benchmark
from scripts.agents_benchmark_cases import CASES, oracle_source


@pytest.fixture
def workspace_root(tmp_path):
    """An explicit, never-created workspace root that fits the Windows gate.

    ``main()`` refuses a workspace root longer than 48 characters on Windows
    before any consent or dispatch gate runs. Its default derives from
    ``Path.home()``, so CLI tests that omit ``--workspace-root`` only reach the
    gate under test when the ambient home is short; an isolated or long Windows
    profile exits 2 first. pytest's ``tmp_path`` is itself too long on Windows,
    so use a unique short directory on the same drive. Nothing in these tests
    may create it; teardown removes it only if a regression did.
    """
    root = Path(tmp_path.anchor) / f"vesta-bench-{uuid.uuid4().hex[:8]}"
    yield root
    shutil.rmtree(root, ignore_errors=True)


REFERENCE = {
    "independent_bugs": {
        "cache.py": "def fresh(created, now, ttl): return ttl > 0 and created <= now < created + ttl\n",
        "invoice.py": """import argparse
from decimal import Decimal
def total(lines):
    result = Decimal(0)
    for price, count in lines:
        value = Decimal(price)
        if not value.is_finite() or value < 0 or type(count) is not int or count < 0:
            raise ValueError()
        result += value * count
    return result
""",
    },
    "feature_tests_docs": {
        "retry.py": """import math
def delays(attempts, base=1.0, cap=60.0):
    if type(attempts) is not int or attempts < 0:
        raise ValueError()
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (base, cap)):
        raise ValueError()
    result, value = [], min(base, cap)
    for _ in range(attempts):
        result.append(value)
        value = min(value * 2, cap)
    return result
""",
        "tests/test_retry.py": """import unittest
from retry import delays
class RetryTests(unittest.TestCase):
    def test_empty(self): self.assertEqual(delays(0), [])
    def test_cap(self): self.assertEqual(delays(3, 1, 2), [1, 2, 2])
    def test_invalid(self):
        with self.assertRaises(ValueError): delays(-1)
if __name__ == '__main__': unittest.main()
""",
        "README.md": "Use delays(attempts, base=1.0, cap=60.0). Invalid input raises ValueError.\n```python\nfrom retry import delays\nprint(delays(3))\n```\n",
    },
    "separable_migration": {
        "writer.py": """import argparse
from decimal import Decimal
def encode(name, timeout):
    if not isinstance(name, str) or not name or type(timeout) not in (int, float): raise ValueError()
    value = Decimal(str(timeout)) * 1000
    if not value.is_finite() or value < 0 or value != value.to_integral_value(): raise ValueError()
    return {'version': 2, 'service': {'name': name, 'timeout_ms': int(value)}}
""",
        "reader.py": """from writer import encode
def normalize(record):
    try:
        if 'version' not in record: return encode(record['name'], record['timeout'])
        service = record['service']
        name, value = service['name'], service['timeout_ms']
        if record['version'] != 2 or not isinstance(name, str) or not name or type(value) is not int or value < 0: raise ValueError()
        return {'version': 2, 'service': {'name': name, 'timeout_ms': value}}
    except (KeyError, TypeError): raise ValueError() from None
""",
    },
    "independent_research": {
        "findings/api.json": json.dumps(
            {
                "source": "specs/api.md",
                "idempotency_header": "Idempotency-Key",
                "retention_hours": 24,
                "payload_conflict_status": 409,
                "unknown_job_status": 404,
                "retryable_statuses": [429],
                "retry_header": "Retry-After",
            }
        ),
        "findings/storage.json": json.dumps(
            {
                "source": "specs/storage.md",
                "journal_mode": "WAL",
                "foreign_keys_per_connection": True,
                "write_transaction": "BEGIN IMMEDIATE",
                "money_encoding": "decimal text",
                "replay_unconfirmed": False,
            }
        ),
    },
    "review_and_fixes": {
        "bounds.py": "def valid_percent(value): return type(value) in (int, float) and 0 <= value <= 100\n",
        "access.py": "def can_read(owner, actor, public=False): return public is True or (isinstance(owner, str) and isinstance(actor, str) and bool(owner) and owner == actor)\n",
        "review.json": json.dumps(
            [
                {"path": path, "code": code, "explanation": explanation}
                for path, code, explanation in [
                    (
                        "bounds.py",
                        "inclusive-range",
                        "Zero and one hundred are incorrectly excluded.",
                    ),
                    (
                        "bounds.py",
                        "numeric-domain",
                        "Booleans and unsupported numeric inputs are not handled.",
                    ),
                    (
                        "access.py",
                        "missing-identity",
                        "Two missing identities incorrectly grant private access.",
                    ),
                ]
            ]
        ),
    },
    "several_issues": {
        "pages.py": """from itertools import islice
def paginate(items, size):
    if type(size) is not int or size <= 0: raise ValueError()
    iterator = iter(items)
    return list(iter(lambda: list(islice(iterator, size)), []))
""",
        "secrets.py": """def redact(record):
    if isinstance(record, dict): return {k: '[REDACTED]' if k.lower() in {'password', 'token', 'api_key'} else redact(v) for k, v in record.items()}
    if isinstance(record, list): return [redact(v) for v in record]
    return record
""",
        "export.py": """import csv, io
def csv_row(values):
    out = io.StringIO(newline='')
    csv.writer(out).writerow(values)
    return out.getvalue()
""",
    },
}


@pytest.mark.parametrize("case", CASES)
def test_behavioral_oracle_rejects_baseline_accepts_independent_solution(
    tmp_path, case
):
    compile(oracle_source(case), "acceptance.py", "exec")
    root, digest = benchmark.prepare_run(tmp_path / case, case)
    assert (
        digest
        == hashlib.sha256((root.parent / "acceptance.py").read_bytes()).hexdigest()
    )
    for path, content in REFERENCE[case].items():
        (root / path).write_text(content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(root.parent / "acceptance.py")],
        cwd=root,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_prepare_only_never_constructs_executor(tmp_path, monkeypatch, workspace_root):
    monkeypatch.setattr(
        benchmark,
        "execute_run",
        lambda *_: pytest.fail("Provider dispatch during preparation"),
    )
    output = tmp_path / "plan"
    assert (
        benchmark.main(
            [
                "--output",
                str(output),
                "--model",
                "codex:test",
                "--workspace-root",
                str(workspace_root),
                "--budget-per-run-usd",
                "1",
            ]
        )
        == 0
    )
    report = json.loads((output / "report.json").read_text())
    assert report["executed"] is False and report["results"] == []
    assert not workspace_root.exists()
    assert report["aggregate_admission_budget_usd"] == "12"
    assert len(report["runs"]) == 12
    for name in CASES:
        pair = [row for row in report["runs"] if row["case"] == name]
        assert {row["parallel_limit"] for row in pair} == {1, 2}
        assert pair[0]["fixture_sha256"] == pair[1]["fixture_sha256"]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1", "bad"])
def test_invalid_budget(value):
    with pytest.raises(argparse.ArgumentTypeError):
        benchmark.money(value)


def test_unknown_cost_stops_remaining_provider_dispatch(
    tmp_path, monkeypatch, workspace_root
):
    calls = []

    def execute(args, entry, directory):
        calls.append(entry["id"])
        return {
            "cost_complete": False,
            "cost_usd": "0",
            "oracle_unchanged": True,
            "authority_unchanged": True,
            "timed_out": False,
        }

    monkeypatch.setattr(benchmark, "execute_run", execute)
    output = tmp_path / "run"
    assert (
        benchmark.main(
            [
                "--output",
                str(output),
                "--model",
                "codex:test",
                "--workspace-root",
                str(workspace_root),
                "--budget-per-run-usd",
                "1",
                "--execute",
            ]
        )
        == 1
    )
    assert len(calls) == 1
    assert (
        "Unknown" in json.loads((output / "report.json").read_text())["stopped_reason"]
    )


def test_positive_budget():
    assert benchmark.money("1.25") == Decimal("1.25")


def test_account_quota_plan_requires_explicit_cloud_consent(
    tmp_path, capsys, monkeypatch, workspace_root
):
    # Planning an account-quota run must never reach a real account provider.
    monkeypatch.setattr(
        benchmark,
        "execute_run",
        lambda *_: pytest.fail("Provider dispatch during account-quota planning"),
    )
    args = [
        "--output",
        str(tmp_path / "account"),
        "--model",
        "account:claude:sonnet",
        "--workspace-root",
        str(workspace_root),
        "--account-quota",
    ]
    with pytest.raises(SystemExit) as refused:
        benchmark.main(args)
    # The refusal must come from the consent gate itself, not an unrelated
    # usage error, and must not write a plan.
    assert refused.value.code == 2
    assert "explicit allow-cloud" in capsys.readouterr().err
    assert not (tmp_path / "account").exists()
    assert benchmark.main([*args, "--allow-cloud"]) == 0
    report = json.loads((tmp_path / "account" / "report.json").read_text())
    assert report["budget_per_run_usd"] is None
    assert report["aggregate_admission_budget_usd"] is None
    assert report["maximum_assignment_attempts"] == 30
    assert report["executed"] is False
    assert not workspace_root.exists()


def test_retained_run_requires_real_integrated_verification(tmp_path, monkeypatch):
    from vestahub import objective_execution
    from pathlib import Path

    real_executor = objective_execution.ObjectiveExecutor

    def worker(packet, cancel, activity):
        for path in packet["assignment"]["intended_paths"]:
            (Path(packet["worktree"]) / path).write_text(
                REFERENCE["independent_bugs"][path], encoding="utf-8"
            )
        return {"status": "completed", "cost_usd": "0", "measurement_kind": "actual"}

    monkeypatch.setattr(
        objective_execution,
        "ObjectiveExecutor",
        lambda *a, **kw: real_executor(*a, worker=worker, **kw),
    )
    args = argparse.Namespace(
        model="test",
        budget_per_run_usd=Decimal(1),
        allow_cloud=False,
        timeout_seconds=60,
        workspace_root=tmp_path.parent / "b",
    )
    result = benchmark.execute_run(
        args,
        {
            "id": "test",
            "case": "independent_bugs",
            "parallel_limit": 2,
            "fixture_sha256": "fixture",
            "assignments": CASES["independent_bugs"]["assignments"],
        },
        tmp_path / "run",
    )
    assert result["verification_success"], json.loads(
        Path(result["evidence"]).read_text()
    )["integration"]
    assert result["cost_complete"] and result["oracle_unchanged"]
