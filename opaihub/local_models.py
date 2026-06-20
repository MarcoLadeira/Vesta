from __future__ import annotations

import ipaddress
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def classify_endpoint(url: str) -> dict[str, Any]:
    """Classify a model endpoint as loopback, private, or public remote.

    Only loopback and private (RFC1918 / .local) endpoints count as free local
    models. Public HTTPS endpoints are remote and must be treated as cloud
    (issue #19): they require confirmation and are never reported as free.
    """
    raw = (url or "").strip()
    if not raw:
        return {"url": raw, "classification": "empty", "is_local": False}

    candidate = raw if "://" in raw else f"//{raw}"
    parsed = urlparse(candidate, scheme="")
    host = parsed.hostname or ""
    scheme = parsed.scheme or ""

    classification = "public"
    if host in LOOPBACK_HOSTS:
        classification = "loopback"
    elif host.endswith(".local") or host.endswith(".internal"):
        classification = "private"
    else:
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_loopback:
                classification = "loopback"
            elif ip.is_private or ip.is_link_local:
                classification = "private"
            else:
                classification = "public"
        except ValueError:
            classification = "public"

    is_local = classification in {"loopback", "private"}
    result = {
        "url": raw,
        "scheme": scheme,
        "host": host,
        "classification": classification,
        "is_local": is_local,
    }
    if not is_local:
        result["requires_cloud_confirmation"] = True
        result["reason"] = (
            "Public/remote endpoint is treated as cloud and requires confirmation; "
            "it is not a free local model."
        )
    return result


def discover_local_models(project_root: Path) -> dict[str, Any]:
    candidates = {
        "ollama": shutil.which("ollama"),
        "lmstudio": shutil.which("lmstudio"),
        "llama-server": shutil.which("llama-server"),
        "llama-cli": shutil.which("llama-cli"),
    }
    endpoints: dict[str, dict[str, Any]] = {}
    for env_name in ["LOCAL_MODEL_URL", "OLLAMA_HOST"]:
        value = os.environ.get(env_name)
        if value:
            endpoints[env_name] = classify_endpoint(value)

    local_endpoint = any(item.get("is_local") for item in endpoints.values())
    remote_endpoints = [
        name for name, item in endpoints.items() if not item.get("is_local")
    ]
    has_command = any(candidates.values())

    return {
        "project": str(project_root.resolve()),
        "commands": {name: path for name, path in candidates.items() if path},
        "endpoints": endpoints,
        "env": {
            "LOCAL_MODEL_URL": bool(os.environ.get("LOCAL_MODEL_URL")),
            "OLLAMA_HOST": bool(os.environ.get("OLLAMA_HOST")),
        },
        # Only real local commands or loopback/private endpoints make a free
        # local model available. A bare public URL no longer counts (#19).
        "available": has_command or local_endpoint,
        "remote_endpoints_require_confirmation": remote_endpoints,
        "notes": "No model is downloaded or started by discovery. Public endpoints are treated as cloud.",
    }
