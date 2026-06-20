"""Local tamper-evidence signing for OPai governance artifacts.

Honest scope: this is HMAC-SHA256 integrity signing with a shared secret, not
public-key identity. It proves an evidence packet or audit bundle was not altered
since it was signed by a holder of the key. Teams share one key out-of-band
(e.g. a CI secret in ``OPAI_SIGNING_KEY``); solo users get an auto-generated
local key. No key or signed content ever leaves the machine on its own.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from .state import state_dir


ENV_KEY = "OPAI_SIGNING_KEY"
SIGNATURE_FIELD = "signature"


def key_path(project_root: Path) -> Path:
    return state_dir(project_root) / "keys" / "team.key"


def resolve_key(project_root: Path, *, create: bool = False) -> tuple[str | None, str]:
    """Resolve the signing key: env var first, then a local key file.

    Returns (key, source). ``source`` is one of env, file, generated, missing.
    """
    env = os.environ.get(ENV_KEY)
    if env:
        return env, "env"
    path = key_path(project_root.expanduser().resolve())
    if path.exists():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            return value, "file"
    if create:
        value = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value, "generated"
    return None, "missing"


def _canonical(payload: dict[str, Any]) -> bytes:
    body = {k: v for k, v in payload.items() if k != SIGNATURE_FIELD}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def sign_payload(payload: dict[str, Any], key: str) -> str:
    return hmac.new(
        key.encode("utf-8"), _canonical(payload), hashlib.sha256
    ).hexdigest()


def verify_payload(payload: dict[str, Any], signature: str, key: str) -> bool:
    expected = sign_payload(payload, key)
    return hmac.compare_digest(expected, str(signature))


def sign(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Return payload with a ``signature`` block, creating a key if needed."""
    key, source = resolve_key(project_root, create=True)
    signature = sign_payload(payload, key or "")
    return {
        **payload,
        SIGNATURE_FIELD: {
            "algorithm": "HMAC-SHA256",
            "value": signature,
            "key_source": source,
            "note": "Integrity signature with a shared secret; not public-key identity.",
        },
    }


def verify(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    block = payload.get(SIGNATURE_FIELD)
    if not isinstance(block, dict) or "value" not in block:
        return {"verified": False, "reason": "no signature present"}
    key, source = resolve_key(project_root, create=False)
    if not key:
        return {"verified": False, "reason": f"no signing key available ({source})"}
    ok = verify_payload(payload, block["value"], key)
    return {
        "verified": ok,
        "reason": "signature valid"
        if ok
        else "signature mismatch (tampered or wrong key)",
        "key_source": source,
    }
