from __future__ import annotations

from pathlib import Path
from typing import Any

from .context_manager import load_profile
from .scanner import scan_project


def deploy_plan(root: Path) -> dict[str, Any]:
    profile = load_profile(root) or scan_project(root)
    providers: list[str] = []
    if (root / "vercel.json").exists():
        providers.append("vercel")
    if (root / "wrangler.toml").exists():
        providers.append("cloudflare-workers")
    if (root / "netlify.toml").exists():
        providers.append("netlify")
    if (root / "Dockerfile").exists():
        providers.append("docker")
    commands = profile.get("commands", {})
    return {
        "providers_detected": providers,
        "pre_deploy_checks": [
            commands.get("test") or "no test command detected",
            commands.get("build") or "no build command detected",
            "op git <project> secrets --staged",
            "op review <project>",
        ],
        "requires_confirmation": ["deploy", "release publish", "rollback"],
        "rollback_template": [
            "identify last known good commit/release",
            "revert or redeploy previous artifact",
            "run smoke checks",
            "document incident notes in OPcoding memory",
        ],
    }
