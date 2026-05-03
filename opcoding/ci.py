from __future__ import annotations

from pathlib import Path
from typing import Any

from .context_manager import load_profile
from .scanner import scan_project
from .utils import write_text


def write_github_workflow(root: Path) -> dict[str, Any]:
    profile = load_profile(root) or scan_project(root)
    test = profile.get("commands", {}).get("test", 'echo "No test command detected"')
    build = profile.get("commands", {}).get("build")
    lines = [
        "name: OPcoding Checks",
        "",
        "on:",
        "  pull_request:",
        "  push:",
        "    branches: [ main, master ]",
        "",
        "jobs:",
        "  checks:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - uses: actions/setup-python@v5",
        "        with:",
        "          python-version: '3.x'",
        "      - name: Test",
        f"        run: {test}",
    ]
    if build:
        lines.extend(["      - name: Build", f"        run: {build}"])
    path = root / ".github" / "workflows" / "opcoding.yml"
    write_text(path, "\n".join(lines) + "\n")
    return {"written": str(path), "test": test, "build": build}
