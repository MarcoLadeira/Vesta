from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


def discover_local_models(project_root: Path) -> dict[str, Any]:
    candidates = {
        "ollama": shutil.which("ollama"),
        "lmstudio": shutil.which("lmstudio"),
        "llama-server": shutil.which("llama-server"),
        "llama-cli": shutil.which("llama-cli"),
    }
    env = {
        "LOCAL_MODEL_URL": bool(os.environ.get("LOCAL_MODEL_URL")),
        "OLLAMA_HOST": bool(os.environ.get("OLLAMA_HOST")),
    }
    return {
        "project": str(project_root.resolve()),
        "commands": {name: path for name, path in candidates.items() if path},
        "env": env,
        "available": any(candidates.values()) or any(env.values()),
        "notes": "No model is downloaded or started by discovery.",
    }
