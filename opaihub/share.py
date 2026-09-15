"""Shareable savings card and badge (business-strategy growth loop).

Turns the private local savings ledger into something a developer wants to
share: a README badge, a shields.io URL, and a short social card - all from
aggregate numbers only. No prompts, no telemetry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from .ledger import summarize_ledger


SITE_URL = "https://github.com/MarcoLadeira/OPai"


def _badge_svg(label: str, value: str, color: str = "#0a7cff") -> str:
    # Self-contained flat badge; widths are approximate but render cleanly.
    label_w = 8 + len(label) * 7
    value_w = 8 + len(value) * 7
    total = label_w + value_w
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" '
        f'role="img" aria-label="{label}: {value}">'
        f'<rect width="{total}" height="20" rx="3" fill="#555"/>'
        f'<rect x="{label_w}" width="{value_w}" height="20" rx="3" fill="{color}"/>'
        f'<g fill="#fff" font-family="Verdana,Geneva,sans-serif" font-size="11">'
        f'<text x="{label_w / 2}" y="14" text-anchor="middle">{label}</text>'
        f'<text x="{label_w + value_w / 2}" y="14" text-anchor="middle">{value}</text>'
        f"</g></svg>"
    )


def build_savings_card(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    summary = summarize_ledger(root)
    saved = summary["estimated_savings_usd"]
    avoided = summary["cloud_calls_avoided"]
    tokens = summary["context_tokens_saved"]
    routes = summary["route_count"]

    value = f"${saved:.2f}"
    shields_url = (
        f"https://img.shields.io/badge/{quote('Vesta saved')}-{quote(value)}-0a7cff"
    )
    headline = (
        f"Vesta saved an estimated {value} across {routes} routed tasks "
        f"and avoided {avoided} cloud calls on this project."
        if routes
        else 'No routed tasks recorded yet - run: vesta route "<task>" --record'
    )

    return {
        "report": "opai-share-card",
        "project": str(root),
        "has_data": routes > 0,
        "headline": headline,
        "stats": {
            "estimated_savings_usd": saved,
            "cloud_calls_avoided": avoided,
            "context_tokens_saved": tokens,
            "routed_tasks": routes,
        },
        "badge": {
            "shields_url": shields_url,
            "svg": _badge_svg("Vesta saved", value),
            "markdown": f"![Vesta saved {value}]({shields_url})",
        },
        "social": (
            f"I'm using Vesta as my AI coding cost firewall — it saved an estimated "
            f"{value} and avoided {avoided} cloud calls on one project, all local "
            f"and private. {SITE_URL}"
        ),
        "privacy": summary["privacy"],
    }


def render_share_markdown(card: dict[str, Any]) -> str:
    badge = card["badge"]
    stats = card["stats"]
    return "\n".join(
        [
            "## My Vesta savings",
            "",
            badge["markdown"],
            "",
            f"> {card['headline']}",
            "",
            f"- Estimated savings: ${stats['estimated_savings_usd']:.2f}",
            f"- Cloud calls avoided: {stats['cloud_calls_avoided']}",
            f"- Context tokens saved: {stats['context_tokens_saved']}",
            "",
            f"_{card['privacy']}_",
            "",
            f"Try Vesta: {SITE_URL}",
        ]
    )
