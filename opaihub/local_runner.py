"""Real local-model execution for OPai (open issue #13).

OPai's whole promise is "do the cheap work locally instead of paying a cloud
model." Until now the router only *planned* that. This actually runs a local,
OpenAI-compatible or Ollama endpoint so OPai can answer L0/L1 tasks (summaries,
classification, first-pass) for $0 and a genuinely avoided cloud call.

Safety: only loopback/private endpoints are used by default (reuses the #19
endpoint classifier), so OPai never silently sends a prompt to a public host.
No third-party dependencies - just stdlib urllib. Tests inject a fake runner;
nothing here calls the network unless a real local server is present.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .local_models import classify_endpoint

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"
DISCOVERY_TIMEOUT = 0.2
_LOCAL_MODELS_LOCK = threading.RLock()
_LOCAL_MODELS_CACHE: list[dict[str, Any]] = []
_LOCAL_DISCOVERY_COMPLETE = False


def cache_local_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    global _LOCAL_DISCOVERY_COMPLETE, _LOCAL_MODELS_CACHE
    with _LOCAL_MODELS_LOCK:
        _LOCAL_MODELS_CACHE = [dict(model) for model in models]
        _LOCAL_DISCOVERY_COMPLETE = True
        return [dict(model) for model in _LOCAL_MODELS_CACHE]


def cached_local_models() -> list[dict[str, Any]]:
    with _LOCAL_MODELS_LOCK:
        return [dict(model) for model in _LOCAL_MODELS_CACHE]


class _HTTPPayload(dict):
    """JSON object with response metadata kept out of serialization."""

    response_headers: dict[str, str]


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    # Callers provide either local endpoints or registry-owned HTTPS URLs.
    request = urllib.request.Request(  # nosec B310
        url, data=data, method=method, headers=headers
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        result = json.loads(response.read().decode("utf-8"))
        if isinstance(result, dict):
            wrapped = _HTTPPayload(result)
            wrapped.response_headers = {
                str(key).lower(): str(value) for key, value in response.headers.items()
            }
            return wrapped
        return result


class LocalRunner:
    """Base interface. Subclasses talk to a specific local server shape."""

    name = "base"
    model = ""

    def available(self) -> bool:
        return False

    def complete(
        self, prompt: str, *, system: str | None = None, timeout: float = 60.0
    ) -> str:
        raise NotImplementedError


class OllamaRunner(LocalRunner):
    name = "ollama"

    def __init__(
        self, base_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_OLLAMA_MODEL
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def available(self) -> bool:
        try:
            tags = _http_json(f"{self.base_url}/api/tags", timeout=DISCOVERY_TIMEOUT)
        except (urllib.error.URLError, OSError, ValueError):
            return False
        return isinstance(tags, dict) and "models" in tags

    def complete(
        self, prompt: str, *, system: str | None = None, timeout: float = 60.0
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        result = _http_json(
            f"{self.base_url}/api/chat",
            method="POST",
            payload={"model": self.model, "messages": messages, "stream": False},
            timeout=timeout,
        )
        return str((result.get("message") or {}).get("content", "")).strip()


class OpenAICompatibleRunner(LocalRunner):
    """LM Studio, vLLM, llama.cpp server, or any local /v1 endpoint."""

    name = "openai-compatible"

    def __init__(self, base_url: str, model: str = "local-model"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def available(self) -> bool:
        try:
            models = _http_json(f"{self.base_url}/models", timeout=DISCOVERY_TIMEOUT)
        except (urllib.error.URLError, OSError, ValueError):
            return False
        return isinstance(models, dict)

    def complete(
        self, prompt: str, *, system: str | None = None, timeout: float = 60.0
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        result = _http_json(
            f"{self.base_url}/chat/completions",
            method="POST",
            payload={"model": self.model, "messages": messages, "stream": False},
            timeout=timeout,
        )
        choices = result.get("choices") or [{}]
        return str((choices[0].get("message") or {}).get("content", "")).strip()


class FreeAPIRunner(OpenAICompatibleRunner):
    """OpenAI-compatible runner for verified free-tier APIs.

    Unlike local runners these reach public endpoints and require an API key
    stored in an env var.  ``available()`` checks key presence only — no
    network ping — to avoid latency in the model picker enumeration.  All
    calls go through OPai's policy confirmation gate because they hit a
    public host. Provider quotas and billing configuration remain authoritative.
    """

    name = "free-api"

    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        endpoint = urllib.parse.urlsplit(base_url)
        if endpoint.scheme.lower() != "https" or not endpoint.hostname:
            raise ValueError("Free-tier API endpoints must use HTTPS")
        super().__init__(base_url, model)
        self._api_key = api_key.strip()
        self.last_usage: dict[str, Any] = {}

    def available(self) -> bool:
        return bool(self._api_key)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def complete(
        self, prompt: str, *, system: str | None = None, timeout: float = 60.0
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        result = _http_json(
            f"{self.base_url}/chat/completions",
            method="POST",
            payload={"model": self.model, "messages": messages, "stream": False},
            timeout=timeout,
            extra_headers=self._auth_headers(),
        )
        usage = result.get("usage") or result.get("usageMetadata") or {}
        input_tokens = usage.get("prompt_tokens", usage.get("promptTokenCount", 0))
        output_tokens = usage.get(
            "completion_tokens", usage.get("candidatesTokenCount", 0)
        )
        total_tokens = usage.get("total_tokens", usage.get("totalTokenCount", 0)) or (
            int(input_tokens or 0) + int(output_tokens or 0)
        )
        headers = getattr(result, "response_headers", {})
        quota = None
        try:
            request_limit = int(headers.get("x-ratelimit-limit-requests", "0"))
            request_remaining = int(headers.get("x-ratelimit-remaining-requests", "0"))
            if request_limit:
                quota = {
                    "metric": "requests",
                    "limit": request_limit,
                    "remaining": request_remaining,
                    "window": "day",
                    "resetsAt": headers.get("x-ratelimit-reset-requests"),
                }
        except (TypeError, ValueError):
            quota = None
        self.last_usage = {
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "tokens": int(total_tokens or 0),
            "measurement": "provider" if usage else "estimated",
            "quota_snapshot": quota,
        }
        choices = result.get("choices") or [{}]
        return str((choices[0].get("message") or {}).get("content", "")).strip()


def _candidate_runners() -> list[tuple[str, LocalRunner]]:
    candidates: list[tuple[str, LocalRunner]] = []
    local_model_url = os.environ.get("LOCAL_MODEL_URL")
    if local_model_url:
        base = local_model_url.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        candidates.append(
            (
                local_model_url,
                OpenAICompatibleRunner(
                    base, os.environ.get("LOCAL_MODEL_NAME", "local-model")
                ),
            )
        )
    ollama_host = os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_URL
    if "://" not in ollama_host:
        ollama_host = "http://" + ollama_host
    candidates.append(
        (
            ollama_host,
            OllamaRunner(
                ollama_host, os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
            ),
        )
    )
    return candidates


def detect_local_runner(
    project_root: Path | None = None, *, allow_public: bool = False
) -> LocalRunner | None:
    """Return the first reachable local runner on a loopback/private endpoint."""
    with _LOCAL_MODELS_LOCK:
        discovery_complete = _LOCAL_DISCOVERY_COMPLETE
        cached = [dict(model) for model in _LOCAL_MODELS_CACHE]
    if discovery_complete:
        return (
            runner_for_model(str(cached[0].get("id") or ""), project_root)
            if cached
            else None
        )
    for url, runner in _candidate_runners():
        classification = classify_endpoint(url)
        if not classification["is_local"] and not allow_public:
            continue
        if runner.available():
            return runner
    return None


def list_local_models(
    project_root: Path | None = None, *, allow_public: bool = False
) -> list[dict[str, Any]]:
    """List the models available on reachable loopback/private endpoints.

    Returns entries like ``{"id": "ollama:llama3.2", "provider": "ollama",
    "model": "llama3.2", "endpoint": "..."}``. Used to populate the GUI model
    picker. Network failures are swallowed - an empty list just means "no local
    model connected", and OPai's Auto route still works.
    """
    models: list[dict[str, Any]] = []
    for url, runner in _candidate_runners():
        if not classify_endpoint(url)["is_local"] and not allow_public:
            continue
        try:
            if isinstance(runner, OllamaRunner):
                tags = _http_json(
                    f"{runner.base_url}/api/tags", timeout=DISCOVERY_TIMEOUT
                )
                for entry in tags.get("models") or []:
                    name = entry.get("name") or entry.get("model")
                    if name:
                        models.append(
                            {
                                "id": f"ollama:{name}",
                                "provider": "ollama",
                                "model": name,
                                "endpoint": runner.base_url,
                            }
                        )
            elif isinstance(runner, OpenAICompatibleRunner):
                data = _http_json(
                    f"{runner.base_url}/models", timeout=DISCOVERY_TIMEOUT
                )
                for entry in data.get("data") or []:
                    mid = entry.get("id")
                    if mid:
                        models.append(
                            {
                                "id": f"openai:{mid}",
                                "provider": "openai-compatible",
                                "model": mid,
                                "endpoint": runner.base_url,
                            }
                        )
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return cache_local_models(models)


def runner_for_model(
    model_id: str, project_root: Path | None = None
) -> LocalRunner | None:
    """Build a runner bound to a specific ``provider:model`` id from the picker."""
    if not model_id or ":" not in model_id:
        return None

    # Free API models: "free:<provider>:<model_id>" — key from env var.
    if model_id.startswith("free:"):
        from .credentials import CredentialStore
        from .free_models import spec_for_model_id

        spec = spec_for_model_id(model_id)
        if spec is None:
            return None
        api_key = CredentialStore().get(spec["provider"]) or ""
        return FreeAPIRunner(spec["api_base"], spec["model_id"], api_key)

    provider, name = model_id.split(":", 1)
    for _url, runner in _candidate_runners():
        if provider == "ollama" and isinstance(runner, OllamaRunner):
            return OllamaRunner(runner.base_url, name)
        if provider in {"openai", "openai-compatible"} and isinstance(
            runner, OpenAICompatibleRunner
        ):
            return OpenAICompatibleRunner(runner.base_url, name)
    if provider == "ollama":
        return OllamaRunner(DEFAULT_OLLAMA_URL, name)
    return None
