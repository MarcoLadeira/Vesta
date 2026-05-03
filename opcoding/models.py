from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .cache import get_cached_bundle, prompt_key, put_prompt_bundle
from .context_manager import build_context, load_profile
from .cost import route_task
from .utils import read_limited, run_command, sha256_text


MODEL_ENV = {
    "L1": "OPCODING_MODEL_L1_COMMAND",
    "L2": "OPCODING_MODEL_L2_COMMAND",
    "L3": "OPCODING_MODEL_L3_COMMAND",
    "L4": "OPCODING_MODEL_L4_COMMAND",
}


def _context(root: Path, max_chars: int = 18000) -> str:
    context_file = root / ".opcoding" / "context.md"
    if context_file.exists():
        return read_limited(context_file, max_chars)
    return build_context(root, load_profile(root))[:max_chars]


def build_model_bundle(
    root: Path, task: str, execute: bool = False, confirm_expensive: bool = False
) -> dict[str, Any]:
    context = _context(root)
    context_hash = sha256_text(context)[:16]
    route = route_task(task, root=root, context_chars=len(context))
    key = prompt_key(task, context_hash, route["route"])
    cached = get_cached_bundle(root, key)
    if cached:
        return {**cached, "cache_hit": True}

    prompt = "\n".join(
        [
            "You are an OPcoding coding agent.",
            f"Route: {route['route']} - {route['route_description']}",
            "Rules: use local evidence, minimize edits, avoid secrets, provide verification command.",
            "",
            "Project context:",
            context,
            "",
            "Task:",
            task,
            "",
            "Return: plan, target files, patch strategy, tests, risks.",
        ]
    )
    bundle: dict[str, Any] = {
        "task": task,
        "route": route,
        "context_hash": context_hash,
        "prompt": prompt,
        "cache_hit": False,
        "model_executed": False,
        "model_output": "",
        "status": "prepared",
    }

    if route["route"] == "L4" and not confirm_expensive:
        bundle["status"] = "blocked_expensive_confirmation_required"
    elif execute:
        command = os.environ.get(MODEL_ENV.get(route["route"], ""))
        if command:
            result = run_command(command, root, timeout=600)
            bundle.update(
                {
                    "model_executed": True,
                    "model_command": command,
                    "model_returncode": result.returncode,
                    "model_output": result.combined_output[-12000:],
                    "status": "executed"
                    if result.returncode == 0
                    else "model_command_failed",
                }
            )
        else:
            bundle["status"] = "no_model_command_configured"

    put_prompt_bundle(root, key, bundle)
    return bundle
