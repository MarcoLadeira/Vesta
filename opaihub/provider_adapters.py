"""Small, stable adapter contract shared by provider diagnostics and tests."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

ACCOUNT_PROVIDERS = {"claude", "codex", "copilot"}
FREE_PROVIDERS = {"gemini", "groq", "mistral"}
LOCAL_PROVIDERS = {"ollama", "openai-compatible"}
SUPPORTED_PROVIDERS = ACCOUNT_PROVIDERS | FREE_PROVIDERS | LOCAL_PROVIDERS


def test_free_provider_connection(
    provider_id: str,
    *,
    store: Any | None = None,
    opener: Any = urlrequest.urlopen,
) -> dict[str, Any]:
    """Verify a free API key using model metadata; never submit a prompt."""

    from opai.provider_contract import normalize_provider_error

    from .credentials import CredentialStore
    from .free_models import FREE_MODEL_SPECS

    provider = str(provider_id or "").strip().lower()
    if provider not in FREE_PROVIDERS:
        raise ValueError("Unsupported free-model provider")
    credentials = store or CredentialStore()
    status = credentials.status(provider)
    secret = credentials.get(provider)
    if not secret:
        error = normalize_provider_error(provider, "No API key configured")
        return {**status, "connected": False, "error": error}
    spec = next(item for item in FREE_MODEL_SPECS if item["provider"] == provider)
    request = urlrequest.Request(
        str(spec["api_base"]).rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {secret}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with opener(request, timeout=10.0) as response:  # nosec B310 - fixed HTTPS hosts
            response.read(1024)
            code = int(getattr(response, "status", 200) or 200)
    except (urlerror.HTTPError, urlerror.URLError, TimeoutError, OSError) as exc:
        error = normalize_provider_error(
            provider,
            f"{getattr(exc, 'code', '')} {getattr(exc, 'reason', '') or exc}",
            timed_out=isinstance(exc, TimeoutError),
        )
        return {**status, "connected": False, "error": error}
    return {
        **status,
        "connected": 200 <= code < 300,
        "lastCheckedAt": int(time.time() * 1000),
    }


@dataclass(frozen=True)
class ProviderAdapter:
    provider_id: str

    @property
    def kind(self) -> str:
        if self.provider_id in ACCOUNT_PROVIDERS:
            return "account"
        if self.provider_id in FREE_PROVIDERS:
            return "free"
        return "local"

    def probe(self, *, home: Path | None = None, force: bool = False) -> dict[str, Any]:
        """Run only local/presence diagnostics; never send a model prompt."""

        if self.kind == "account":
            from .accounts import test_account_connection

            return test_account_connection(self.provider_id, home=home, force=force)
        if self.kind == "free":
            if force:
                return test_free_provider_connection(self.provider_id)
            from .credentials import CredentialStore

            return CredentialStore().status(self.provider_id)
        from .local_runner import list_local_models

        models = list_local_models(home)
        return {
            "provider": self.provider_id,
            "available": any(
                self.provider_id
                in {str(item.get("provider")), str(item.get("id", "")).split(":", 1)[0]}
                for item in models
            ),
            "models": models,
        }

    def build_command(
        self,
        prompt: str,
        *,
        cli_path: str | None = None,
        model: str | None = None,
        mode: str = "ask",
        out_file: str | None = None,
    ) -> list[str] | None:
        if self.kind != "account":
            return None
        from .accounts import AccountRunner

        return AccountRunner(
            self.provider_id, cli_path or self.provider_id, model=model
        ).build_command(prompt, mode=mode, out_file=out_file)

    def parse_result(
        self,
        detail: Any,
        *,
        model: str | None = None,
        returncode: int | None = None,
        timed_out: bool = False,
    ) -> dict[str, Any]:
        from opai.provider_contract import normalize_provider_error

        return normalize_provider_error(
            self.provider_id,
            detail,
            model=model,
            returncode=returncode,
            timed_out=timed_out,
        )

    def extract_usage(
        self, body: dict[str, Any] | None, headers: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = body or {}
        usage = payload.get("usage") or payload.get("usageMetadata") or {}
        input_tokens = int(
            usage.get("prompt_tokens", usage.get("promptTokenCount", 0)) or 0
        )
        output_tokens = int(
            usage.get("completion_tokens", usage.get("candidatesTokenCount", 0)) or 0
        )
        total = int(
            usage.get("total_tokens", usage.get("totalTokenCount", 0))
            or input_tokens + output_tokens
        )
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tokens": total,
            "measurement": "provider" if usage else "estimated",
            "headers": {
                str(key).lower(): str(value) for key, value in (headers or {}).items()
            },
        }


def adapter_for(provider_id: str) -> ProviderAdapter:
    provider = str(provider_id or "").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("Unsupported AI provider")
    return ProviderAdapter(provider)
