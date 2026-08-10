from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context_manager import load_profile
from .scanner import scan_project
from .utils import write_text


CHECKOUT_ACTION = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
SETUP_PYTHON_ACTION = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"


def write_github_workflow(root: Path) -> dict[str, Any]:
    profile = load_profile(root) or scan_project(root)
    commands = profile.get("commands") if isinstance(profile, dict) else None
    commands = commands if isinstance(commands, dict) else {}
    test = commands.get("test")
    if not isinstance(test, str) or not test.strip():
        raise ValueError(
            "No test command detected; run 'op init' and verify the project profile "
            "before generating CI."
        )
    test = test.strip()
    build = commands.get("build")
    build = build.strip() if isinstance(build, str) and build.strip() else None
    lines = [
        "name: OPcoding Checks",
        "",
        "on:",
        "  pull_request:",
        "  push:",
        "    branches: [ main, master ]",
        "",
        "permissions:",
        "  contents: read",
        "",
        "concurrency:",
        "  group: opcoding-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
        "  cancel-in-progress: true",
        "",
        "jobs:",
        "  checks:",
        "    runs-on: ubuntu-latest",
        "    timeout-minutes: 30",
        "    steps:",
        f"      - uses: {CHECKOUT_ACTION}",
        "        with:",
        "          ref: ${{ github.sha }}",
        "          persist-credentials: false",
        f"      - uses: {SETUP_PYTHON_ACTION}",
        "        with:",
        "          python-version: '3.13'",
        "      - name: Test",
        "        shell: bash",
        f"        run: {json.dumps(test)}",
    ]
    if build:
        lines.extend(
            [
                "      - name: Build",
                "        shell: bash",
                f"        run: {json.dumps(build)}",
            ]
        )
    path = root / ".github" / "workflows" / "opcoding.yml"
    write_text(path, "\n".join(lines) + "\n")
    return {"written": str(path), "test": test, "build": build}
