"""Local launch-readiness checks for the Vesta Free Public Alpha.

Tells the founder what still blocks a public launch without making a network
call. Everything is read from local files and environment variables. No
telemetry, uploads, cloud calls, or paid-access assumptions are introduced.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# The exact, approved public claim. Must stay limited to the local benchmark.
LAUNCH_CLAIM = (
    "Local max benchmark proof: publish only results reproduced with "
    "`vesta benchmark run --suite max --mode both`."
)
LAUNCH_CAVEAT = (
    "Local Vesta benchmark suite result. Not an official SWE-bench, "
    "Terminal-Bench, Aider, or third-party leaderboard result."
)

# Legacy checkout/private-access placeholders must be removed, never replaced.
_LEGACY_ACCESS_PLACEHOLDERS = [
    "PRIVATE_FOUNDING_PRO_CHECKOUT_URL",
    "PRIVATE_TEAM_PILOT_APPLY_URL",
    "PRIVATE_BENCHMARK_PROOF_URL",
]
# Strings that would expose an unsafe raw-install route on the public site.
_LEAKAGE_MARKERS = [
    "raw.githubusercontent.com/MarcoLadeira/OPai",
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

    # Analytics is optional and must not become an access or release gate.
    wa_done = site_present and "REPLACE_WITH_CLOUDFLARE_WEB_ANALYTICS_TOKEN" not in site
    checks.append(
        {
            "name": "web_analytics_token",
            "ok": None,
            "detail": (
                "Cloudflare Web Analytics token configured (optional)."
                if wa_done
                else "Cloudflare Web Analytics token placeholder remains (optional)."
            ),
        }
    )

    # Legacy access placeholders are blockers because free alpha must not
    # accidentally route users into a stale checkout or private-access path.
    remaining = [
        token for token in _LEGACY_ACCESS_PLACEHOLDERS if site_present and token in site
    ]
    checks.append(
        _check(
            "legacy_access_placeholders",
            not remaining,
            "No legacy checkout or private-access placeholders remain."
            if not remaining
            else f"{len(remaining)} legacy access placeholder(s) remain: {', '.join(remaining)}.",
            "Remove legacy private-access placeholders; do not replace them with payment links.",
        )
    )

    # Site leakage checks (no raw install URL that bypasses the release path).
    leaks = [marker for marker in _LEAKAGE_MARKERS if marker in site]
    checks.append(
        _check(
            "site_leakage",
            not leaks,
            "No raw-install leakage on the site."
            if not leaks
            else f"Site leaks: {', '.join(leaks)}.",
            "Remove raw GitHub install URLs from site/index.html.",
        )
    )

    # Distribution is a manual decision, never an access gate.
    checks.append(
        {
            "name": "repo_privacy",
            "ok": None,
            "detail": "Manual distribution decision; Free Public Alpha has no access gate.",
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
                "Run `vesta benchmark run --suite max --mode both`.",
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
                "Improve routing/coverage until `vesta benchmark gate` passes.",
            )
        )

    blockers = [c for c in checks if c["ok"] is False]
    return {
        "report": "vesta-launch-readiness",
        "project": str(root),
        "ready": not blockers,
        "blocker_count": len(blockers),
        "checks": checks,
        "blockers": blockers,
        "launch_claim": LAUNCH_CLAIM,
        "launch_caveat": LAUNCH_CAVEAT,
        "privacy": "All checks are local; no network call, no telemetry, no secrets displayed.",
    }
