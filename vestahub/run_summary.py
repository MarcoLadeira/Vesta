"""One verdict-first, evidence-linked, shareable run summary (#389).

The single source of truth for the receipt's four questions — *did it work,
what changed, what did it cost, what did I save* — rendered purely from the run
record so every surface that has the record shows identical content (no
display-side recomputation).  Savings obey the #381 rule automatically: they are
read from the already verdict-gated receipt, so a partial/blocked/timeout run
shows its actual spend with no savings claim.  Secrets are redacted (#87) before
anything can leave the app.
"""

from __future__ import annotations

from typing import Any, Mapping

from .completion import verdict_label

# Cost-measurement badge (mirrors app.js receiptBadge / #235 honesty badge).
_BADGE_LABEL = {
    "actual": "measured",
    "unknown": "subscription",
    "estimated": "estimated",
    "blocked": "estimated",
}


def _redact(text: Any) -> str:
    """Scrub secret-shaped text before it can appear in a shareable summary."""

    from vesta.provider_contract import redact_secrets

    return redact_secrets(str(text or "")).strip()


def _money(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return f"${float(value):.4f}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def build_run_summary(record: Mapping[str, Any] | None) -> str:
    """Render a run record into a verdict-first, shareable Markdown receipt.

    Order is deliberate and fixed: verdict + reason, then evidence, then the
    model actually executed with its routing rationale, then honest cost and the
    counterfactual savings.  Every free-text field is redacted.  Returns a
    plain, professional block safe to copy into an issue, PR, or chat.
    """

    record = _mapping(record)
    verdict = _mapping(record.get("completion_verdict"))
    receipt = _mapping(record.get("receipt"))

    lines: list[str] = ["# Vesta run receipt", ""]

    # 1) Verdict + typed reason — first, always. This is the answer to "did it
    #    work?" and no other line may precede it.
    vkey = str(verdict.get("verdict") or "").strip().lower()
    vlabel = verdict_label(vkey)
    reason = _redact(verdict.get("reason"))
    lines.append(f"**Verdict: {vlabel}**" + (f" — {reason}" if reason else ""))

    objective = _mapping(verdict.get("objective"))
    obj_text = _redact(objective.get("objective_text"))
    obj_mode = str(objective.get("mode") or "").strip()
    if obj_text:
        suffix = f" ({obj_mode})" if obj_mode else ""
        lines += ["", f"Objective: {obj_text}{suffix}"]

    # A non-completed run leads with its recovery action, never a savings claim.
    next_action = _redact(verdict.get("next_action"))
    if next_action and vkey and vkey != "completed":
        lines += ["", f"Next: {next_action}"]

    # 2) Evidence — what actually changed. Files first, then any typed evidence
    #    ref (tests, answer) the verdict was built from.
    evidence_lines: list[str] = []
    changed = [
        str(path).strip()
        for path in (record.get("changed_files") or ())
        if str(path).strip()
    ]
    if changed:
        shown = ", ".join(changed[:20])
        more = f" (+{len(changed) - 20} more)" if len(changed) > 20 else ""
        evidence_lines.append(f"- Files changed ({len(changed)}): {shown}{more}")
    for ref in verdict.get("evidence") or ():
        ref = _mapping(ref)
        kind = str(ref.get("kind") or "").strip().lower()
        summary = _redact(ref.get("summary"))
        # The diff/changed-file evidence is already the files line above.
        if summary and kind != "diff":
            evidence_lines.append(f"- {summary}")
    manifest = _mapping(verdict.get("verification_manifest"))
    manifest_digest = str(manifest.get("digest") or "").strip().lower()
    if manifest_digest:
        evidence_lines.append(f"- Verification manifest: {manifest_digest[:12]}")
    if evidence_lines:
        lines += ["", "## Evidence", *evidence_lines]

    # 3) Model actually executed + routing rationale (which tier, vs baseline).
    model = str(
        record.get("selected_model") or receipt.get("selected_model") or ""
    ).strip()
    run_mode = str(
        record.get("effective_run_mode") or receipt.get("selected_mode") or ""
    ).strip()
    chosen_tier = str(receipt.get("chosen_tier") or "").strip().upper()
    baseline_tier = str(receipt.get("baseline_tier") or "").strip().upper()
    if model:
        model_line = f"- Model: {model}" + (f" (run in {run_mode})" if run_mode else "")
        rationale = ""
        if chosen_tier and baseline_tier and chosen_tier != baseline_tier:
            rationale = f" — routed to {chosen_tier} from the {baseline_tier} baseline"
        elif chosen_tier:
            rationale = f" — ran on {chosen_tier}"
        lines += ["", "## Model", model_line + rationale]

    # 4) Cost (badged) + savings with a named counterfactual. Read straight from
    #    the verdict-gated receipt, so a non-completed run shows spend and no
    #    savings by construction (#381).
    cost_lines: list[str] = []
    spend = _money(receipt.get("estimated_actual_usd"))
    if spend is not None:
        badge = _BADGE_LABEL.get(
            str(receipt.get("confidence") or "").strip().lower(), "estimated"
        )
        paid = " paid" if receipt.get("paid_call") else ""
        cost_lines.append(f"- Spend: {spend} ({badge}){paid}")
    savings = receipt.get("estimated_savings_usd")
    if (
        isinstance(savings, (int, float))
        and not isinstance(savings, bool)
        and savings > 0
    ):
        against = baseline_tier or "the cloud"
        cost_lines.append(f"- Saved: {_money(savings)} vs the {against} baseline")
    if cost_lines:
        lines += ["", "## Cost", *cost_lines]

    lines += ["", "See the full ledger with: vesta savings"]
    return "\n".join(lines).strip() + "\n"
