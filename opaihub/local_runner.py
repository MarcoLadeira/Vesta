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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .local_models import classify_endpoint

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(  # nosec B310 - scheme validated by caller (loopback only)
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


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
            tags = _http_json(f"{self.base_url}/api/tags", timeout=2.0)
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
            models = _http_json(f"{self.base_url}/models", timeout=2.0)
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
    for url, runner in _candidate_runners():
        classification = classify_endpoint(url)
        if not classification["is_local"] and not allow_public:
            continue
        if runner.available():
            return runner
    return None
