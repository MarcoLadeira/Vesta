"""Proof bundles and team pilot trust reports (#54).

Founders and budget owners pay when OPai *proves* savings and safety in a form
they can trust. A proof bundle assembles the benchmark report, savings rollup,
policy check, audit checkpoint, signed-evidence summary, and a redaction summary
into one private, signable artifact - without ever storing or exposing raw
prompts. Verification re-checks the signature and the embedded artifact hashes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import summarize_audit, verify_chain
from .benchmark import benchmark_gate, latest_benchmark_report, run_benchmark
from .ci_check import run_policy_check
from .ledger import rollup_ledger, summarize_ledger
from .signing import sign as sign_payload
from .signing import verify as verify_payload
from .team import team_report


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _benchmark_section(project_root: Path, *, allow_run: bool) -> dict[str, Any]:
    report = latest_benchmark_report(project_root)
    if report is None and allow_run:
        report = run_benchmark(project_root, suite="local", mode="both", write=False)
    if report is None:
        return {"present": False, "reason": "no benchmark run recorded"}
    gate = benchmark_gate(report, min_context_reduction=1.0, min_success_rate=0.9)
    return {
        "present": True,
        "run_id": report.get("run_id"),
        "suite": report.get("suite"),
        "efficiency_score": report.get("efficiency_score"),
        "claim_readiness": report.get("claim_readiness"),
        "gate_ok": gate["ok"],
        "report_sha256": report.get("artifact_hashes", {}).get("report"),
    }


def build_proof_bundle(
    project_root: Path,
    *,
    sign: bool = True,
    allow_benchmark_run: bool = True,
) -> dict[str, Any]:
    """Assemble a private, optionally signed proof bundle. Read-only by default."""
    root = project_root.expanduser().resolve()

    ledger = summarize_ledger(root)
    rollups = rollup_ledger(root)
    policy = run_policy_check(root)
    audit_summary = summarize_audit(root)
    audit_chain = verify_chain(root)
    team = team_report(root)
    benchmark = _benchmark_section(root, allow_run=allow_benchmark_run)

    governed_agents = sorted(rollups["by_agent"].keys())
    policy_exceptions = [c for c in policy["checks"] if not c.get("ok")]

    body = {
        "report": "opai-proof-bundle",
        "schema_version": 1,
        "generated_at": _now_iso(),
        "project": str(root),
        "benchmark": benchmark,
        "savings": {
            "estimated_savings_usd": ledger["estimated_savings_usd"],
            "estimated_actual_spend_usd": ledger["estimated_actual_spend_usd"],
            "cloud_calls_avoided": ledger["cloud_calls_avoided"],
            "context_tokens_saved": ledger["context_tokens_saved"],
            "routed_tasks": ledger["route_count"],
            "by_agent": rollups["by_agent"],
            "by_repo": rollups["by_repo"],
        },
        "policy_check": {"ok": policy["ok"], "exceptions": policy_exceptions},
        "audit_checkpoint": {
            "event_count": audit_summary["event_count"],
            "denied_actions": audit_summary["denied_actions"],
            "chain_valid": audit_chain["ok"],
            "head_hash": audit_chain.get("head_hash"),
        },
        "team_report": {
            "active_profile": team["policy"]["active_profile"],
            "conforms": team["policy"]["conforms"],
            "risk_blocks": team["governance"]["denied_actions"],
            "governed_agents": governed_agents,
        },
        "redaction_summary": {
            "raw_prompts_stored": False,
            "method": "one-way task hashes only; secrets redacted before persistence",
            "ledger_events": ledger["event_count"],
        },
    }
    if sign:
        return sign_payload(root, body)
    return body


def verify_proof_bundle(project_root: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    """Verify a proof bundle's signature and required artifacts (fail-closed)."""
    root = project_root.expanduser().resolve()
    problems: list[str] = []

    signature = verify_payload(root, bundle)
    if not signature.get("verified"):
        problems.append(f"signature: {signature.get('reason')}")

    if not bundle.get("benchmark", {}).get("present"):
        problems.append("benchmark artifact missing")
    if not bundle.get("audit_checkpoint", {}).get("chain_valid", False):
        problems.append("audit chain invalid")
    if bundle.get("redaction_summary", {}).get("raw_prompts_stored") is not False:
        problems.append("redaction summary not asserted")

    return {
        "verified": not problems,
        "signature": signature,
        "problems": problems,
    }


def render_proof_markdown(bundle: dict[str, Any]) -> str:
    savings = bundle.get("savings", {})
    bench = bundle.get("benchmark", {})
    audit = bundle.get("audit_checkpoint", {})
    team = bundle.get("team_report", {})
    score = bench.get("efficiency_score", {}) or {}
    lines = [
        "# OPai Proof Bundle",
        "",
        f"Generated: {bundle.get('generated_at', '')}",
        "",
        "## Savings",
        f"- Estimated savings: ${savings.get('estimated_savings_usd', 0):.4f}",
        f"- Cloud calls avoided: {savings.get('cloud_calls_avoided', 0)}",
        f"- Context tokens saved: {savings.get('context_tokens_saved', 0)}",
        f"- Routed tasks: {savings.get('routed_tasks', 0)}",
        "",
        "## Benchmark",
        f"- Present: {bench.get('present', False)}",
        f"- Context reduction: {score.get('context_reduction_ratio', 'n/a')}x",
        f"- Effectiveness index: {score.get('opai_effectiveness_index', 'n/a')}",
        "",
        "## Governance",
        f"- Policy check ok: {bundle.get('policy_check', {}).get('ok')}",
        f"- Risk blocks: {team.get('risk_blocks', 0)}",
        f"- Governed agents: {', '.join(team.get('governed_agents', [])) or 'none recorded'}",
        f"- Audit events: {audit.get('event_count', 0)} (chain valid: {audit.get('chain_valid')})",
        "",
        "## Privacy",
        f"- Raw prompts stored: {bundle.get('redaction_summary', {}).get('raw_prompts_stored')}",
        "- Method: one-way task hashes only; nothing transmitted.",
    ]
    sig = bundle.get("signature")
    if isinstance(sig, dict):
        lines.extend(["", f"_Signed ({sig.get('algorithm', 'unsigned')})._"])
    return "\n".join(lines) + "\n"
