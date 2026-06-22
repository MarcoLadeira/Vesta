"""Local launch-readiness checks for the OPai control center (GUI section 8).

Tells the founder what still blocks a controlled-alpha public launch without
ever leaking source or making a network call. Everything is read from local
files (the static site, docs) and environment variables. No telemetry, no
uploads, no cloud calls.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# The exact, approved public claim. Must stay limited to the local benchmark.
LAUNCH_CLAIM = (
    "OPai reduced context by 50x and avoided 16 paid calls on the "
    "16-task local benchmark suite."
)
LAUNCH_CAVEAT = (
    "Local OPai benchmark suite result. Not an official SWE-bench, "
    "Terminal-Bench, Aider, or third-party leaderboard result."
)

# Placeholders that must be replaced before a real public launch.
_PLACEHOLDERS = [
    "PRIVATE_FOUNDING_PRO_CHECKOUT_URL",
    "PRIVATE_TEAM_PILOT_APPLY_URL",
    "PRIVATE_BENCHMARK_PROOF_URL",
    "REPLACE_WITH_CLOUDFLARE_WEB_ANALYTICS_TOKEN",
]
# Strings that would leak private/raw access on the public controlled-alpha site.
_LEAKAGE_MARKERS = [
    "raw.githubusercontent.com/MarcoLadeira/OPai",
    "issues/new",
]


def _site_path(project_root: Path) -> Path:
    return project_root.expanduser().resolve() / "site" / "index.html"


def _read_site(project_root: Path) -> str:
    path = _site_path(project_root)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _check(name: str, ok: bool, detail: str, fix: str = "") -> dict[str, Any]:
    record: dict[str, Any] = {"name": name, "ok": ok, "detail": detail}
    if not ok and fix:
        record["fix"] = fix
    return record


def build_launch_readiness(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    site = _read_site(root)
    site_present = bool(site)

    checks: list[dict[str, Any]] = []

    # Cloudflare auth (token in env, never displayed).
    cf_auth = bool(os.environ.get("CLOUDFLARE_API_TOKEN"))
    checks.append(
        _check(
            "cloudflare_auth",
            cf_auth,
            "CLOUDFLARE_API_TOKEN present in environment."
            if cf_auth
            else "No CLOUDFLARE_API_TOKEN in environment.",
            "Set CLOUDFLARE_API_TOKEN before publishing (kept out of the GUI).",
        )
    )

    # Web Analytics token replaced.
    wa_done = site_present and "REPLACE_WITH_CLOUDFLARE_WEB_ANALYTICS_TOKEN" not in site
    checks.append(
        _check(
            "web_analytics_token",
            wa_done,
            "Cloudflare Web Analytics token configured."
            if wa_done
            else "Web Analytics token placeholder still present in site/index.html.",
            "Replace REPLACE_WITH_CLOUDFLARE_WEB_ANALYTICS_TOKEN in site/index.html.",
        )
    )

    # Lemon Squeezy + Tally checkout/apply links replaced.
    lemon_done = site_present and "PRIVATE_FOUNDING_PRO_CHECKOUT_URL" not in site
    checks.append(
        _check(
            "lemon_links",
            lemon_done,
            "Lemon Squeezy checkout link configured."
            if lemon_done
            else "Founding Pro checkout placeholder still present.",
            "Replace PRIVATE_FOUNDING_PRO_CHECKOUT_URL with a Lemon Squeezy link.",
        )
    )
    tally_done = site_present and not (
        "PRIVATE_TEAM_PILOT_APPLY_URL" in site or "PRIVATE_BENCHMARK_PROOF_URL" in site
    )
    checks.append(
        _check(
            "tally_links",
            tally_done,
            "Team Pilot / benchmark-proof apply links configured."
            if tally_done
            else "Tally apply/proof placeholders still present.",
            "Replace PRIVATE_TEAM_PILOT_APPLY_URL and PRIVATE_BENCHMARK_PROOF_URL with Tally links.",
        )
    )

    # All placeholders replaced.
    remaining = [token for token in _PLACEHOLDERS if site_present and token in site]
    checks.append(
        _check(
            "placeholder_replacement",
            not remaining,
            "All launch placeholders replaced."
            if not remaining
            else f"{len(remaining)} placeholder(s) remain: {', '.join(remaining)}.",
            "Replace the remaining placeholders in site/index.html.",
        )
    )

    # Site leakage checks (no raw install URLs / public issue intake during alpha).
    leaks = [marker for marker in _LEAKAGE_MARKERS if marker in site]
    checks.append(
        _check(
            "site_leakage",
            not leaks,
            "No raw-install or public-intake leakage on the site."
            if not leaks
            else f"Site leaks: {', '.join(leaks)}.",
            "Remove raw GitHub install URLs and public issue intake from site/index.html.",
        )
    )

    # Repo privacy is a manual decision during controlled alpha.
    checks.append(
        {
            "name": "repo_privacy",
            "ok": None,
            "detail": "Manual decision: keep source private during controlled alpha.",
        }
    )

    # Benchmark gate (proof must pass before public claims).
    from .benchmark import benchmark_gate, latest_benchmark_report

    report = latest_benchmark_report(root)
    if report is None:
        checks.append(
            _check(
                "benchmark_gate",
                False,
                "No benchmark run recorded.",
                "Run `opai benchmark run --suite max --mode both`.",
            )
        )
    else:
        gate = benchmark_gate(
            report, min_effectiveness_index=95.0, require_risk_blocks=True
        )
        checks.append(
            _check(
                "benchmark_gate",
                gate["ok"],
                "Benchmark gate passes (effectiveness >= 95, risk blocks present)."
                if gate["ok"]
                else "Benchmark gate fails the launch thresholds.",
                "Improve routing/coverage until `opai benchmark gate` passes.",
            )
        )

    blockers = [c for c in checks if c["ok"] is False]
    return {
        "report": "opai-launch-readiness",
        "project": str(root),
        "ready": not blockers,
        "blocker_count": len(blockers),
        "checks": checks,
        "blockers": blockers,
        "launch_claim": LAUNCH_CLAIM,
        "launch_caveat": LAUNCH_CAVEAT,
        "privacy": "All checks are local; no network call, no telemetry, no secrets displayed.",
    }
