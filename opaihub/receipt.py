"""Signed, screenshot-able savings receipts — OPai's shareable proof artifact.

A receipt is a compact, signed snapshot of the local savings ledger that a
developer can screenshot and share, and that an eng lead can independently
verify (see ``verify_receipt`` / the ``opai receipt verify`` command, #88). It
is the hero artifact behind Epic B (#84): one object that serves both the
solo-dev growth loop and the enterprise audit wedge.

Privacy (matches the rest of OPai): only the project *name* is included, never
an absolute path; every string field is redacted before signing; and nothing is
read but the local ledger aggregate (no raw prompts, no network).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        "report": "opai-savings-receipt",
        "schema_version": SCHEMA_VERSION,
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


def verify_receipt(project_root: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed verification: content hash + signature (used by #88).

    A receipt is VERIFIED only if its embedded ``receipt_hash`` matches a fresh
    hash of the body *and* the signature is valid. Any mutated byte breaks one or
    both. ``problems`` names each failing section.
    """
    problems: list[str] = []
    actual_hash = str(receipt.get("receipt_hash", ""))
    expected_hash = _content_hash(receipt)
    if not actual_hash:
        problems.append("missing receipt_hash")
    elif actual_hash != expected_hash:
        problems.append("content hash mismatch (tampered)")
    sig = verify_signature(project_root, receipt)
    if not sig.get("verified"):
        problems.append(f"signature: {sig.get('reason', 'invalid')}")
    return {
        "verified": not problems,
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
        sub = 'Run a task first:  opai route "<task>" --record'
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
  <text x="40" y="50" fill="{fg}" font-size="20" font-weight="800" font-family="Nunito,Segoe UI,sans-serif">OPai — Savings Receipt</text>
  {badge}
  <text x="40" y="68" fill="{muted}" font-size="13" font-family="Nunito,Segoe UI,sans-serif">{project}</text>
  <text x="40" y="135" fill="{accent}" font-size="52" font-weight="800" font-family="Nunito,Segoe UI,sans-serif">{hero}</text>
  <text x="42" y="158" fill="{muted}" font-size="14" font-family="Nunito,Segoe UI,sans-serif">{_esc(sub)}</text>
  {"".join(chip_svg)}
  <text x="40" y="285" fill="{muted}" font-size="12" font-family="Nunito,Segoe UI,sans-serif">hash {short_hash}  ·  verify:  opai receipt verify &lt;file&gt;</text>
  <text x="40" y="318" fill="{muted}" font-size="11" font-family="Nunito,Segoe UI,sans-serif">{_esc(receipt.get("privacy", ""))}</text>
</svg>"""
