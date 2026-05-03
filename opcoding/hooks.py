from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .gitops import secret_scan_diff
from .utils import run_command, write_text


def install_precommit(root: Path, opcoding_root: Path) -> dict[str, Any]:
    git_root = run_command("git rev-parse --show-toplevel", root, timeout=20)
    if git_root.returncode != 0:
        return {"installed": False, "reason": "not a git repository"}
    hooks_dir = Path(git_root.stdout.strip()) / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-commit"
    script = f"""#!/bin/sh
export PYTHONPATH="{opcoding_root.as_posix()}:$PYTHONPATH"
python -m opcoding hooks-check "$(pwd)"
"""
    write_text(hook, script)
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    return {"installed": True, "hook": str(hook)}


def hooks_check(root: Path) -> int:
    result = secret_scan_diff(root, staged=True)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get("safe_to_commit", False):
        print("OPcoding blocked commit: secret-like value detected in staged diff.")
        return 1
    return 0
