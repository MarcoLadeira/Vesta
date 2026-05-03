from __future__ import annotations

import http.client
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

from .utils import find_secret_hits, now_iso, project_op_dir, write_json


MORPH_ENDPOINT = "https://api.morphllm.com/v1/chat/completions"
DEFAULT_MODEL = "morph-v3-fast"


def morph_doctor() -> dict[str, Any]:
    return {
        "endpoint": MORPH_ENDPOINT,
        "default_model": DEFAULT_MODEL,
        "api_key_env": "MORPH_API_KEY",
        "api_key_present": bool(os.environ.get("MORPH_API_KEY")),
        "policy": "never stored in repo; execution requires --execute and --confirm-spend",
    }


def build_morph_content(instruction: str, code: str, update: str = "") -> str:
    parts = [f"<instruction>{instruction}</instruction>", f"<code>{code}</code>"]
    if update:
        parts.append(f"<update>{update}</update>")
    return "\n".join(parts)


def build_morph_payload(
    instruction: str, code: str, update: str = "", model: str = DEFAULT_MODEL
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "user", "content": build_morph_content(instruction, code, update)}
        ],
    }


def call_morph(payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    api_key = os.environ.get("MORPH_API_KEY")
    if not api_key:
        return {"ok": False, "error": "MORPH_API_KEY is not set"}
    body = json.dumps(payload).encode("utf-8")
    parsed = urllib.parse.urlparse(MORPH_ENDPOINT)
    if parsed.scheme != "https" or parsed.hostname != "api.morphllm.com":
        return {"ok": False, "error": "Invalid Morph endpoint policy"}
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port, timeout=timeout
    )
    try:
        connection.request(
            "POST",
            path,
            body=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        if 200 <= response.status < 300:
            return {"ok": True, "status": response.status, "response": json.loads(raw)}
        return {"ok": False, "status": response.status, "error": raw[-4000:]}
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        connection.close()


def morph_apply_snippet(
    root: Path,
    instruction: str,
    code: str,
    update: str = "",
    execute: bool = False,
    confirm_spend: bool = False,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    combined = "\n".join([instruction, code, update])
    secret_hits = find_secret_hits(combined)
    payload = build_morph_payload(instruction, code, update, model=model)
    result: dict[str, Any] = {
        "created_at": now_iso(),
        "model": model,
        "payload": payload,
        "secret_hits": secret_hits,
        "executed": False,
        "policy": "prepare-only unless --execute and --confirm-spend are provided",
    }
    if secret_hits:
        result["blocked"] = "secret-like content detected"
    elif execute and not confirm_spend:
        result["blocked"] = "confirm_spend_required"
    elif execute:
        result["executed"] = True
        result["morph"] = call_morph(payload)
    write_json(project_op_dir(root) / "cache" / "last-morph.json", result)
    return result
