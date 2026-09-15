from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opai.cockpit import build_cockpit, compact_statusline
from opaihub.state import state_dir


STATUS_FILENAME = "OPAI_STATUS.md"
STATUS_JSON = "opai-status.json"
LOCAL_EXCLUDE_PATTERNS = [
    STATUS_FILENAME,
    ".opaihub/",
    ".opaihub/opai-status.json",
    ".opaihub/dashboard.html",
    ".opaihub/benchmarks/",
]


def _ensure_local_git_ignore(root: Path) -> str:
    git_dir = root / ".git"
    if not git_dir.exists():
        return "not_git_repo"
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    existing_lines = {line.strip() for line in existing.splitlines()}
    missing = [
        pattern for pattern in LOCAL_EXCLUDE_PATTERNS if pattern not in existing_lines
    ]
    if not missing:
        return "already_ignored"
    suffix = "" if existing.endswith("\n") or not existing else "\n"
    exclude.write_text(
        existing
        + suffix
        + "# Vesta local status and proof files\n"
        + "\n".join(missing)
        + "\n",
        encoding="utf-8",
    )
    return "ignored"


def render_visibility_markdown(payload: dict[str, Any]) -> str:
    clients = payload["clients"]
    savings = payload["savings"]
    budget = payload["budget"]
    benchmark = payload["benchmark"]
    status_text = "active" if payload["status"] == "on" else "needs attention"
    lines = [
        "# Vesta Status",
        "",
        f"Vesta is {status_text} for this project.",
        "",
        f"`{compact_statusline(payload)}`",
        "",
        "## Readiness",
        "",
        f"- Clients active: {clients['active']}/{clients['total']}",
        f"- Broken clients: {', '.join(clients['summary']['broken']) or 'none'}",
        f"- Missing clients: {', '.join(clients['summary']['missing']) or 'none'}",
        f"- Budget: {'panic mode ON' if budget['panic'] else 'budget ok'}",
        "",
        "## Savings",
        "",
        f"- Routed tasks: {savings['routed_tasks']}",
        f"- Estimated savings: ${savings['estimated_savings_usd']:.2f}",
        f"- Cloud calls avoided: {savings['cloud_calls_avoided']}",
        "",
        "## Proof",
        "",
        f"- Benchmark: {benchmark['claim']}",
        "",
        "## Commands",
        "",
        "- `vesta cockpit` - obvious ON/OFF control panel",
        "- `vesta doctor` - detailed client readiness",
        '- `vesta route "<task>" --record` - record real savings',
        "- `vesta dashboard --html` - write the local dashboard",
        "",
        "_Local only: no raw prompts, secrets, or telemetry are stored here._",
        "",
    ]
    return "\n".join(lines)


def write_visibility_status(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    payload = build_cockpit(root)
    markdown_path = root / STATUS_FILENAME
    json_path = state_dir(root) / STATUS_JSON
    markdown_path.write_text(render_visibility_markdown(payload), encoding="utf-8")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    ignore_status = _ensure_local_git_ignore(root)
    return {
        "status": "installed",
        "project": str(root),
        "markdown": str(markdown_path),
        "json": str(json_path),
        "git_ignore": ignore_status,
    }
