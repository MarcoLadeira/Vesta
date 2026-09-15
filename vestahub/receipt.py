"""Signed, screenshot-able savings receipts — Vesta's shareable proof artifact.

A receipt is a compact, signed snapshot of the local savings ledger that a
developer can screenshot and share, and that an eng lead can independently
verify (see ``verify_receipt`` / the ``vesta receipt verify`` command, #88). It
is the hero artifact behind Epic B (#84): one object that serves both the
solo-dev growth loop and the enterprise audit wedge.

Privacy (matches the rest of Vesta): only the project *name* is included, never
an absolute path; every string field is redacted before signing; and nothing is
read but the local ledger aggregate (no raw prompts, no network).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vesta.release_identity import surface_identity_payload

from .command_runner import redact
from .savings import build_savings_report
from .signing import sign as sign_payload
from .signing import verify as verify_signature


SCHEMA_VERSION = 1
HASH_FIELDS_EXCLUDED = ("signature", "receipt_hash")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _content_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 of the receipt body, excluding hash + signature fields."""
    body = {k: v for k, v in payload.items() if k not in HASH_FIELDS_EXCLUDED}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_receipt(project_root: Path, *, sign: bool = True) -> dict[str, Any]:
    """Build a compact savings receipt, signed by default (#87).

    Returns a JSON-serializable dict. When ``sign`` is False the receipt carries
    no signature block and renders with an ``UNVERIFIED`` watermark.
    """
    root = project_root.expanduser().resolve()
    report = build_savings_report(root)
    totals = report["totals"]
    body: dict[str, Any] = {
        "report": "vesta-savings-receipt",
        "schema_version": SCHEMA_VERSION,
        **surface_identity_payload(),
        "generated_at": _now_iso(),
        "project_name": redact(root.name),
        "has_data": bool(report["has_data"]),
        "baseline_tier": report["baseline_tier"],
        "totals": {
            "routed_tasks": totals["routed_tasks"],
            "cloud_calls_avoided": totals["cloud_calls_avoided"],
            "estimated_baseline_usd": totals["estimated_baseline_usd"],
            "estimated_actual_spend_usd": totals["estimated_actual_spend_usd"],
            "estimated_savings_usd": totals["estimated_savings_usd"],
            "estimated_savings_percent": totals["estimated_savings_percent"],
            "context_tokens_saved": totals["context_tokens_saved"],
        },
        "privacy": "No raw prompts or file paths; numbers are local ledger estimates.",
    }
    body["receipt_hash"] = _content_hash(body)
    if sign:
        return sign_payload(root, body)
    return body


def build_objective_receipts(snapshot: dict[str, Any]) -> tuple[dict, dict[str, dict]]:
    from decimal import Decimal, localcontext

    children = {}
    for item in snapshot.get("assignments", []):
        body = {
            "report": "vesta-agent-receipt",
            "schema_version": 1,
            "objective_id": snapshot["objective_id"],
            "assignment_id": item["assignment_id"],
            "task_id": item["task_id"],
            "run_id": item["run_id"],
            "status": item["status"],
            "provisional": bool(item["owner"]) or item["status"] == "pending",
            "owner": item["owner"] or item.get("last_owner", ""),
            "role": item["role"],
            "model": item.get("observed_model") or item["model"],
            "provider": item.get("observed_provider") or item["provider"],
            "cost_usd": item["cost_usd"],
            "cost_complete": item["cost_complete"],
            "budget_usd": item["budget_usd"],
            "changed_files": item["changed_files"],
            "verification": item["verification"],
            "branch": item["branch"],
            "base_sha": item["base_sha"],
            "head_sha": (item.get("result", {}).get("git_evidence") or {}).get(
                "head_sha"
            ),
            "evidence_hash": _content_hash({"evidence": item.get("result", {})}),
            "cost_evidence_hash": item.get("cost_evidence_hash"),
        }
        body["receipt_hash"] = _content_hash(body)
        children[item["assignment_id"]] = body
    integration = snapshot.get("integration") or {}
    with localcontext() as context:
        context.prec = 256
        child_cost = sum(
            (Decimal(item["cost_usd"]) for item in children.values()), Decimal(0)
        )
        coordinator_cost = Decimal(snapshot["cost_usd"]) - child_cost
    body = {
        "report": "vesta-objective-receipt",
        "schema_version": 1,
        "objective_id": snapshot["objective_id"],
        "task_id": snapshot["task_id"],
        "run_id": snapshot["run_id"],
        "revision": snapshot["revision"],
        "status": snapshot["status"],
        "provisional": snapshot["status"]
        not in {"completed", "cancelled", "failed", "needs-attention"}
        or bool(integration.get("owner"))
        or bool(snapshot.get("planning", {}).get("owner"))
        or any(item["owner"] for item in snapshot.get("assignments", [])),
        "cost_usd": snapshot["cost_usd"],
        "cost_complete": snapshot["cost_complete"],
        "cost_evidence_hash": snapshot.get("cost_evidence_hash"),
        "cost_components": {
            "coordinator_usd": str(coordinator_cost),
            "agents_usd": str(child_cost),
        },
        "budget_usd": snapshot["budget_usd"],
        "verification": integration.get("verification", {}),
        "integration": {
            "branch": integration.get("branch", ""),
            "head_sha": (integration.get("result") or {}).get("head_sha"),
            "changed_files": (integration.get("result") or {}).get("changed_files", []),
            "conflicts": integration.get("conflicts", []),
        },
        "agents": list(children.values()),
        "privacy": "Local execution evidence; no raw prompts or provider responses.",
    }
    body["receipt_hash"] = _content_hash(body)
    return body, children


def verify_receipt(project_root: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed verification: content hash + signature (#88).

    Two independent checks:

    - **content hash** — a fresh SHA-256 of the body must match the embedded
      ``receipt_hash``. This is machine-independent: anyone can recompute it
      without any key, so it is portable tamper-evidence of the numbers.
    - **signature** — the HMAC-SHA256 signature is checked when the shared key
      is available on this machine. A *mismatch* is tampering; a *missing key*
      is not — the content stays portably verified (``status`` distinguishes
      ``VERIFIED`` from ``CONTENT_VERIFIED`` from ``TAMPERED``).

    ``verified`` stays True only for a fully checked, valid receipt (content +
    signature), so existing callers keep their strict contract; ``status`` and
    ``failing_section`` carry the finer verdict for the CLI.
    """
    problems: list[str] = []
    failing_section: str | None = None
    actual_hash = str(receipt.get("receipt_hash", ""))
    expected_hash = _content_hash(receipt)
    content_ok = bool(actual_hash) and actual_hash == expected_hash
    if not actual_hash:
        problems.append("content hash: missing receipt_hash")
        failing_section = "content_hash"
    elif not content_ok:
        problems.append("content hash: mismatch (payload was altered)")
        failing_section = "content_hash"

    sig = verify_signature(project_root, receipt)
    reason = str(sig.get("reason", ""))
    if sig.get("verified"):
        signature_status = "valid"
    elif "no signature present" in reason:
        signature_status = "unsigned"
        problems.append("signature: receipt is unsigned")
    elif "no signing key" in reason:
        # Different/absent key on this machine — the HMAC cannot be checked, but
        # the content hash above is portable. Not tampering.
        signature_status = "no_key"
    else:
        signature_status = "mismatch"
        problems.append("signature: mismatch (tampered or wrong key)")
        if failing_section is None:
            failing_section = "signature"

    if not content_ok or signature_status == "mismatch":
        status = "TAMPERED"
    elif signature_status == "valid":
        status = "VERIFIED"
    else:  # content_ok, signature unsigned or unverifiable here
        status = "CONTENT_VERIFIED"

    return {
        "verified": status == "VERIFIED",
        "status": status,
        "content_verified": content_ok,
        "signature_status": signature_status,
        "failing_section": failing_section,
        "problems": problems,
        "receipt_hash": actual_hash,
        "signature": sig,
    }


# --------------------------------------------------------------------------- #
# SVG card rendering (self-contained, dark, screenshot-friendly)
# --------------------------------------------------------------------------- #
def _esc(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _money(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "$0.00"
    return f"${amount:,.2f}" if amount >= 1 else f"${amount:.4f}"


def render_receipt_svg(receipt: dict[str, Any]) -> str:
    """Render a receipt as a self-contained dark SVG card (string)."""
    totals = receipt.get("totals", {})
    signed = isinstance(receipt.get("signature"), dict)
    short_hash = _esc(str(receipt.get("receipt_hash", ""))[:12] or "unhashed")
    project = _esc(receipt.get("project_name", "project"))
    has_data = bool(receipt.get("has_data"))

    bg, card = "#0d1117", "#161b22"
    fg, muted, accent = "#e6edf3", "#8b949e", "#3fb950"

    if has_data:
        hero = _esc(_money(totals.get("estimated_savings_usd", 0)))
        pct = totals.get("estimated_savings_percent", 0)
        sub = f"{_esc(pct)}% vs un-routed {_esc(receipt.get('baseline_tier', 'L3'))} baseline"
        chips = [
            (f"{_esc(totals.get('routed_tasks', 0))}", "routed tasks"),
            (f"{_esc(totals.get('cloud_calls_avoided', 0))}", "paid calls avoided"),
            (
                _esc(_money(totals.get("estimated_actual_spend_usd", 0))),
                "actually spent",
            ),
        ]
    else:
        hero = "No data yet"
        sub = 'Run a task first:  vesta route "<task>" --record'
        chips = [("0", "routed tasks"), ("0", "paid calls avoided"), ("$0.00", "spent")]

    chip_svg = []
    for i, (value, label) in enumerate(chips):
        x = 40 + i * 200
        chip_svg.append(
            f'<g transform="translate({x},170)">'
            f'<rect width="180" height="70" rx="10" fill="{bg}" stroke="#30363d"/>'
            f'<text x="16" y="34" fill="{fg}" font-size="26" font-weight="700" '
            f'font-family="Nunito,Segoe UI,sans-serif">{value}</text>'
            f'<text x="16" y="55" fill="{muted}" font-size="13" '
            f'font-family="Nunito,Segoe UI,sans-serif">{_esc(label)}</text>'
            f"</g>"
        )

    badge = (
        f'<text x="600" y="50" text-anchor="end" fill="{accent}" font-size="14" '
        f'font-weight="700" font-family="Nunito,Segoe UI,sans-serif">✓ SIGNED</text>'
        if signed
        else '<text x="600" y="50" text-anchor="end" fill="#d29922" font-size="14" '
        'font-weight="700" font-family="Nunito,Segoe UI,sans-serif">⚠ UNVERIFIED</text>'
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360" width="640" height="360">
  <rect width="640" height="360" fill="{bg}"/>
  <rect x="20" y="20" width="600" height="320" rx="16" fill="{card}" stroke="#30363d"/>
  <text x="40" y="50" fill="{fg}" font-size="20" font-weight="800" font-family="Nunito,Segoe UI,sans-serif">Vesta — Savings Receipt</text>
  {badge}
  <text x="40" y="68" fill="{muted}" font-size="13" font-family="Nunito,Segoe UI,sans-serif">{project}</text>
  <text x="40" y="135" fill="{accent}" font-size="52" font-weight="800" font-family="Nunito,Segoe UI,sans-serif">{hero}</text>
  <text x="42" y="158" fill="{muted}" font-size="14" font-family="Nunito,Segoe UI,sans-serif">{_esc(sub)}</text>
  {"".join(chip_svg)}
  <text x="40" y="285" fill="{muted}" font-size="12" font-family="Nunito,Segoe UI,sans-serif">hash {short_hash}  ·  verify:  vesta receipt verify &lt;file&gt;</text>
  <text x="40" y="318" fill="{muted}" font-size="11" font-family="Nunito,Segoe UI,sans-serif">{_esc(receipt.get("privacy", ""))}</text>
</svg>"""
