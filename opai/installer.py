from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from opai import __brand__
from opai.integrations import activate_project
from opai.release_identity import surface_identity_payload
from opaihub.command_runner import run_policy_command
from opaihub.dashboard import build_dashboard
from opaihub.dashboard_html import build_dashboard_html
from opaihub.state import attach_project, state_dir
from opaihub.validator import validate_all


def install_project(
    project_root: Path,
    install_tools: bool = False,
    install_superpowers: bool = True,
    timeout: int = 300,
    global_integrations: bool = False,
    install_shell_aliases: bool = False,
    home: Path | None = None,
) -> dict[str, Any]:
    """Attach, validate, and activate ``project_root``.

    ``home`` is forwarded to :func:`activate_project`; ``None`` means the
    user's real home. Tests must pass a throwaway directory -- activation
    resolves (and can write) home-level discovery files even when
    ``global_integrations`` is off.
    """
    root = project_root.expanduser().resolve()
    attach = attach_project(root)
    validation = validate_all(root)
    markdown_dashboard = build_dashboard(root)
    html_dashboard = build_dashboard_html(root)
    network_actions: list[dict[str, Any]] = []

    if install_tools:
        command = [
            sys.executable,
            "-m",
            "opcoding",
            "tools",
            str(root),
            "install",
            "--set",
            "core",
        ]
        completed = run_policy_command(command, root, timeout=timeout)
        network_actions.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "status": "ok" if completed.returncode == 0 else "failed",
                "output_tail": completed.combined_output[-1600:],
            }
        )

    activation = activate_project(
        root,
        home=home,
        install_global=global_integrations,
        install_shell_aliases=install_shell_aliases,
        install_superpowers=install_superpowers,
    )

    manifest = {
        **surface_identity_payload(brand=__brand__),
        "project_root": str(root),
        "state_path": attach["state_path"],
        "validation_ok": validation["ok"],
        "dashboards": {
            "markdown": str(markdown_dashboard),
            "html": str(html_dashboard),
        },
        "network_actions": network_actions,
        "activation": activation,
        "superpowers_auto_install": install_superpowers,
        "global_integrations": activation.get("global_integrations"),
        "next_steps": [
            "vesta doctor",
            "vesta scan",
            "vesta tools",
            "vesta dashboard --html",
            "vesta statusline",
        ],
    }
    path = state_dir(root) / "install.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
