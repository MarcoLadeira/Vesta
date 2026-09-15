"""Real local-model execution for Vesta (open issue #13).

Vesta's whole promise is "do the cheap work locally instead of paying a cloud
model." Until now the router only *planned* that. This actually runs a local,
OpenAI-compatible or Ollama endpoint so Vesta can answer L0/L1 tasks (summaries,
classification, first-pass) for $0 and a genuinely avoided cloud call.

Safety: only loopback/private endpoints are used by default (reuses the #19
endpoint classifier), so Vesta never silently sends a prompt to a public host.
No third-party dependencies - just stdlib urllib. Tests inject a fake runner;
nothing here calls the network unless a real local server is present.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import threading
import time
import urllib.error
import uuid
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .cancellation import LocalRunCancelled
from .command_runner import redact
from .local_models import classify_endpoint


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"
DISCOVERY_TIMEOUT = 0.2
_LOCAL_MODELS_LOCK = threading.RLock()
_LOCAL_MODELS_CACHE: list[dict[str, Any]] = []
_LOCAL_DISCOVERY_COMPLETE = False


class IncompleteStreamError(RuntimeError):
    """The provider closed a response after emitting only a partial stream."""


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


def _decode_http_json(raw: bytes, headers: dict[str, str]) -> Any:
    result = json.loads(raw.decode("utf-8"))
    if isinstance(result, dict):
        wrapped = _HTTPPayload(result)
        wrapped.response_headers = {
            str(key).lower(): str(value) for key, value in headers.items()
        }
        return wrapped
    return result


def _error_detail_from_body(raw: bytes) -> str:
    """Pull a human-readable message out of an OpenAI/Gemini-style error body."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        text = raw.decode("utf-8", errors="replace").strip()
        return text[:300] if text else "no response body"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or error.get("status") or ""
            if detail:
                return str(detail)[:300]
        elif isinstance(error, str) and error:
            return error[:300]
    return json.dumps(body)[:300]


def _raise_for_status(status: int, raw: bytes) -> None:
    """Raise on a non-2xx free/local-API response instead of parsing it as an
    answer (#219). ``http.client`` — unlike ``urllib.request`` — never raises
    on its own for 4xx/5xx, so an invalid key, an exhausted quota, or an
    unknown model id was previously decoded as a normal completion with an
    empty ``choices`` list, surfacing as a generic "no answer" message no
    matter what actually went wrong. Raising here routes the real cause
    through the existing ``runner_error`` -> ``normalize_provider_error`` path.
    """
    if status < 400:
        return
    detail = _error_detail_from_body(raw)
    if status == 404:
        raise RuntimeError(f"model not found (HTTP 404): {detail}")
    if status >= 500:
        raise RuntimeError(f"provider unavailable (HTTP {status}): {detail}")
    raise RuntimeError(f"HTTP {status}: {detail}")


def _http_json_cancellable(
    url: str,
    *,
    method: str = "POST",
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
    cancel: threading.Event | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    """POST/GET JSON with true mid-flight cancellation (issues #107, #152).

    ``urllib`` blocks with no interrupt, so a Stop click could only be ignored
    while the request kept running. This uses ``http.client`` directly and a
    small watcher thread: when ``cancel`` fires, the connection is closed,
    which aborts the blocked read immediately and raises
    :class:`LocalRunCancelled`. Free-tier public APIs (#152) route their auth'd
    request through here too, so Stop aborts the network call itself, not just
    the result rendering. Response headers are preserved for quota extraction.
    """
    parsed = urllib.parse.urlsplit(url)
    conn_cls = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    conn = conn_cls(parsed.hostname or "127.0.0.1", parsed.port, timeout=timeout)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    if cancel is None:
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            status = response.status
            raw = response.read()
            response_headers = dict(response.getheaders())
        finally:
            with contextlib.suppress(Exception):
                conn.close()
        _raise_for_status(status, raw)
        return _decode_http_json(raw, response_headers)

    # Cancellable path: the blocking read runs on a worker thread while this
    # thread polls the cancel Event. On cancel we shutdown+close the socket —
    # the FIN/RST makes the server abort — and return immediately, regardless
    # of how the platform wakes the blocked recv (Windows select() won't wake
    # on close/shutdown; we don't depend on it).
    box: dict[str, Any] = {}
    done = threading.Event()

    def _work() -> None:
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            box["status"] = response.status
            box["raw"] = response.read()
            box["headers"] = dict(response.getheaders())
        except Exception as exc:  # noqa: BLE001 - reported by the coordinator
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=_work, daemon=True).start()
    while not done.wait(0.15):
        if cancel.is_set():
            with contextlib.suppress(Exception):
                sock = getattr(conn, "sock", None)
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(Exception):
                conn.close()
            raise LocalRunCancelled()
    with contextlib.suppress(Exception):
        conn.close()
    if cancel.is_set():
        raise LocalRunCancelled()
    if "error" in box:
        raise box["error"]
    _raise_for_status(int(box.get("status") or 200), box["raw"])
    return _decode_http_json(box["raw"], box.get("headers") or {})


#: One decoded wire line: the text delta, whether a terminal marker was seen,
#: any usage on this line, and whether the stream is over.
#:
#: ``terminal`` and ``stop`` are separate on purpose. An OpenAI server sends
#: ``finish_reason`` (terminal) and then, with ``stream_options.include_usage``,
#: a *further* chunk carrying the token counts. Treating the first as the end of
#: the stream would silently drop the cost of every run.
_Decoded = tuple[str, bool, dict[str, Any], bool]


def _sse_delta(line: bytes) -> _Decoded | None:
    """Decode one OpenAI server-sent-event line."""
    if not line or not line.startswith(b"data:"):
        return None
    data = line[len(b"data:") :].strip()
    if data == b"[DONE]":
        return "", True, {}, True
    try:
        obj = json.loads(data)
    except ValueError:
        return None
    choice = (obj.get("choices") or [{}])[0]
    terminal = bool(str(choice.get("finish_reason") or "").strip())
    delta = str((choice.get("delta") or {}).get("content") or "")
    usage = obj["usage"] if isinstance(obj.get("usage"), dict) else {}
    return delta, terminal, usage, False


def _ndjson_delta(line: bytes) -> _Decoded | None:
    """Decode one Ollama NDJSON line.

    Ollama streams whole JSON objects, one per line, ending with an object
    whose ``done`` is true — no ``data:`` prefix and no ``[DONE]`` sentinel.
    That object is both the terminal marker and the end of the stream, and it
    may carry the last token alongside the counts.
    """
    if not line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if obj.get("error"):
        raise RuntimeError(str(obj["error"]))
    delta = str((obj.get("message") or {}).get("content") or "")
    done = bool(obj.get("done"))
    usage: dict[str, Any] = {}
    if done:
        usage = {
            key: obj[key]
            for key in ("prompt_eval_count", "eval_count", "total_duration")
            if key in obj
        }
    return delta, done, usage, done


def _stream_chat(
    url: str,
    *,
    payload: dict[str, Any],
    timeout: float,
    cancel: threading.Event | None,
    extra_headers: dict[str, str] | None,
    on_delta: Any,
    decode: Any = None,
    accept: str = "text/event-stream",
) -> tuple[str, dict[str, Any]]:
    """POST a chat completion with ``stream: true`` and invoke ``on_delta(text)``
    for each content token as it arrives (#154).

    ``decode`` parses one wire line into ``(delta, terminal, usage)`` and
    selects the protocol: SSE by default, NDJSON for Ollama. Everything else —
    the connection, the cancellation check between lines, the requirement that a
    terminal marker actually arrived — is identical, and sharing it is why both
    providers now behave the same way when a stream is cut short (#295 gate 10).

    Returns ``(full_text, usage)``. Cancellation closes the socket between lines
    and raises :class:`LocalRunCancelled`. Any transport/HTTP error raises so the
    caller can fall back to the blocking path — Vesta never fakes progress.
    """
    decode = decode or _sse_delta
    parsed = urllib.parse.urlsplit(url)
    conn_cls = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    conn = conn_cls(parsed.hostname or "127.0.0.1", parsed.port, timeout=timeout)
    body = json.dumps({**payload, "stream": True}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": accept,
        **(extra_headers or {}),
    }
    path = (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")
    chunks: list[str] = []
    usage: dict[str, Any] = {}
    terminal_seen = False
    try:
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        if int(getattr(response, "status", 0)) >= 400:
            raise RuntimeError(f"streaming request failed (HTTP {response.status})")
        while True:
            if cancel is not None and cancel.is_set():
                with contextlib.suppress(Exception):
                    sock = getattr(conn, "sock", None)
                    if sock is not None:
                        sock.shutdown(socket.SHUT_RDWR)
                raise LocalRunCancelled()
            line = response.readline()
            if not line:
                break
            decoded = decode(line.strip())
            if decoded is None:
                continue
            delta, terminal, line_usage, stop = decoded
            if delta:
                chunks.append(delta)
                with contextlib.suppress(Exception):  # a rendering hiccup never
                    on_delta(delta)  # breaks the stream
            if line_usage:
                usage = line_usage
            if terminal:
                terminal_seen = True
            if stop:
                break
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    if not terminal_seen:
        raise IncompleteStreamError(
            "incomplete stream: response ended before a terminal marker"
        )
    return "".join(chunks), usage


class LocalRunner:
    """Base interface. Subclasses talk to a specific local server shape."""

    name = "base"
    model = ""

    def available(self) -> bool:
        return False

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        timeout: float = 60.0,
        cancel: threading.Event | None = None,
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
        self,
        prompt: str,
        *,
        system: str | None = None,
        timeout: float = 60.0,
        cancel: threading.Event | None = None,
        on_text: Any = None,
    ) -> str:
        """Answer ``prompt``, streaming tokens to ``on_text`` when given.

        Ollama used to be the one adapter that could not stream, so callers fell
        back to a blocking request and the user watched nothing happen until the
        whole answer landed. Worse, a failure part-way through discarded output
        that a streaming provider would have kept (#295 gate 10). Ollama speaks
        NDJSON rather than SSE, which is the only reason it was left out.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        url = f"{self.base_url}/api/chat"
        if on_text is not None:
            streamed = self._stream_answer(
                url, messages, timeout=timeout, cancel=cancel, on_text=on_text
            )
            if streamed is not None:
                return streamed
        result = _http_json_cancellable(
            url,
            method="POST",
            payload={"model": self.model, "messages": messages, "stream": False},
            timeout=timeout,
            cancel=cancel,
        )
        text = str((result.get("message") or {}).get("content", "")).strip()
        if on_text is not None and text:  # the blocking fallback still emits
            with contextlib.suppress(Exception):
                on_text(text)
        return text

    def _stream_answer(
        self,
        url: str,
        messages: list[dict[str, Any]],
        *,
        timeout: float,
        cancel: threading.Event | None,
        on_text: Any,
    ) -> str | None:
        """Stream tokens, or ``None`` so the caller falls back — never faked."""
        try:
            text, _usage = _stream_chat(
                url,
                payload={"model": self.model, "messages": messages},
                timeout=timeout,
                cancel=cancel,
                extra_headers=None,
                on_delta=on_text,
                decode=_ndjson_delta,
                accept="application/x-ndjson",
            )
        except (LocalRunCancelled, IncompleteStreamError):
            # Both are terminal by design: reissuing would duplicate content the
            # user can already see, and hide an interruption as a clean answer.
            raise
        except Exception:  # noqa: BLE001 - any streaming failure falls back
            return None
        return text.strip() or None


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

    def _auth_headers(self) -> dict[str, str]:
        """No auth for a local endpoint; FreeAPIRunner adds a bearer token."""
        return {}

    def _stream_answer(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout: float,
        cancel: threading.Event | None,
        on_text: Any,
    ) -> tuple[str, dict[str, Any]] | None:
        """Stream tokens to ``on_text`` (#154). Returns (text, usage) on success,
        or None so the caller falls back to the blocking path — never faked."""
        try:
            text, usage = _stream_chat(
                f"{self.base_url}/chat/completions",
                payload={"model": self.model, "messages": messages},
                timeout=timeout,
                cancel=cancel,
                extra_headers=self._auth_headers(),
                on_delta=on_text,
            )
        except LocalRunCancelled:
            raise
        except IncompleteStreamError:
            # A partial stream may already be visible in the UI. Reissuing the
            # request would duplicate content, spend, and side effects while
            # hiding the provider interruption as a clean completion.
            raise
        except Exception:  # noqa: BLE001 - any streaming failure falls back
            return None
        return (text, usage) if text.strip() else None

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        timeout: float = 60.0,
        cancel: threading.Event | None = None,
        on_text: Any = None,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        if on_text is not None:
            streamed = self._stream_answer(
                messages, timeout=timeout, cancel=cancel, on_text=on_text
            )
            if streamed is not None:
                return streamed[0].strip()
        result = _http_json_cancellable(
            f"{self.base_url}/chat/completions",
            method="POST",
            payload={"model": self.model, "messages": messages, "stream": False},
            timeout=timeout,
            cancel=cancel,
            extra_headers=self._auth_headers(),
        )
        choices = result.get("choices") or [{}]
        text = str((choices[0].get("message") or {}).get("content", "")).strip()
        if on_text is not None and text:  # blocking fallback still owns the emit
            with contextlib.suppress(Exception):
                on_text(text)
        return text


# The continuous tool loop asks the model to end with a single versioned
# decision object so a claimed completion can be verified (opaihub/tool_loop.py).
# Bare prose is still accepted, but is only reported COMPLETED when real progress
# backs it — reading alone never fakes success.
_TOOL_LOOP_PROTOCOL = (
    "You are running in Vesta's continuous tool loop. Use the provided tools to "
    "make real progress on the task. When — and only when — the task is genuinely "
    "finished, reply with a single JSON object and nothing else:\n"
    '{"opai_decision_version": 1, "state": "completed", "summary": "<what you '
    'accomplished>", "evidence": ["<tool call id or name that proves it>"]}\n'
    'If you need the user to decide something, reply with state "needs_user_input" '
    'and a "question". Never claim a completion you cannot back with evidence.'
)

# Human-readable text for a run that stopped without a final answer, keyed by the
# controller's stopped_reason. Completed runs carry the model's own answer.
_STOP_MESSAGES = {
    "no_progress": "Stopped: no real progress was being made toward the goal.",
    "exploration_limit": (
        "Stopped: explored as far as the budget allows without reaching a "
        "concrete change."
    ),
    "repeated_failure": "Stopped: the same action kept failing and could not be recovered.",
    "controller_timeout": "Stopped: the task ran too long without reaching a milestone.",
    "task_deadline": (
        "Stopped: the task reached its deadline. Work completed so far was "
        "retained, but final verification did not finish."
    ),
    "external_ceiling": "Stopped: reached the configured external tool-call ceiling.",
    "invalid_decision": "Stopped: the provider did not return a valid completion decision.",
    "provider_error": "Stopped: the provider was temporarily unavailable.",
    # #674: distinct from provider_error on purpose — a missing reasoning
    # field is not "temporarily unavailable" and retrying would not help.
    "reasoning_continuity_error": (
        "Stopped: a thinking-mode reply lost the reasoning context the "
        "provider requires to continue, and retrying will not recover it."
    ),
}


class FreeAPIRunner(OpenAICompatibleRunner):
    """OpenAI-compatible runner for verified free-tier APIs.

    Unlike local runners these reach public endpoints and require an API key
    stored in an env var.  ``available()`` checks key presence only — no
    network ping — to avoid latency in the model picker enumeration.  All
    calls go through Vesta's policy confirmation gate because they hit a
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

    def _chat(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout: float,
        cancel: threading.Event | None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto"})
        payload.update(self._extra_chat_fields())
        return _http_json_cancellable(
            f"{self.base_url}/chat/completions",
            method="POST",
            payload=payload,
            timeout=timeout,
            cancel=cancel,
            extra_headers=self._auth_headers(),
        )

    def _usage(self, result: dict[str, Any]) -> dict[str, Any]:
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
        return {
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "tokens": int(total_tokens or 0),
            "measurement": "provider" if usage else "estimated",
            "quota_snapshot": quota,
        }

    def _cost_for(self, usage: dict[str, Any]) -> tuple[float | None, str]:
        """Real dollar cost for one call, and how it was measured.

        Free tier: ``$0`` is the genuinely actual cost of this call, not a
        placeholder — never estimated, never omitted. :class:`PaidAPIRunner`
        overrides this to price real usage against real per-token rates.
        """
        return 0.0, "actual"

    def _extra_chat_fields(self) -> dict[str, Any]:
        """Payload fields merged into every ``_chat`` request beyond the base.

        Empty here — no free-tier provider needs anything extra.
        :class:`PaidAPIRunner` overrides this for DeepSeek's ``thinking``/
        ``reasoning_effort`` fields (#674), keeping this one seam as the only
        place a subclass need touch to extend the payload.
        """
        return {}

    def _thinking_requested(self) -> bool:
        """Whether this runner is dispatching turns with thinking mode on.

        ``False`` here — no free-tier provider supports it. Read by
        ``complete_with_tools`` to stamp :class:`~opaihub.tool_loop.ChatTurn`
        so the tool loop knows whether a turn's ``reasoning_content`` is
        required (#674, #673 A4) or merely absent-because-inapplicable.
        """
        return False

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        timeout: float = 60.0,
        cancel: threading.Event | None = None,
        on_text: Any = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # Stream tokens live when a sink is given (#154), else the blocking path.
        if on_text is not None:
            streamed = self._stream_answer(
                messages, timeout=timeout, cancel=cancel, on_text=on_text
            )
            if streamed is not None:
                text, usage = streamed
                if usage:
                    self.last_usage = {
                        "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
                        "output_tokens": int(usage.get("completion_tokens", 0) or 0),
                        "tokens": int(usage.get("total_tokens", 0) or 0),
                        "measurement": "provider" if usage else "estimated",
                    }
                return text.strip()
        # Cancellation truth (#152): route the auth'd request through the
        # cancellable transport so Stop aborts the network call itself, not
        # just the result renderer. LocalRunCancelled propagates to run_ask.
        result = self._chat(messages, timeout=timeout, cancel=cancel)
        self.last_usage = self._usage(result)
        choices = result.get("choices") or [{}]
        text = str((choices[0].get("message") or {}).get("content", "")).strip()
        if not text:
            # HTTP 200 with an empty completion is almost always the provider's
            # safety/content filter, not a key or quota problem (#219) — say so
            # instead of falling through to the generic "check your key" copy.
            finish_reason = str(choices[0].get("finish_reason") or "").strip().lower()
            if finish_reason in {"content_filter", "safety"}:
                raise RuntimeError(
                    "response blocked by the provider's safety filter "
                    f"(finish_reason={finish_reason})"
                )
        if on_text is not None and text:  # blocking fallback still owns the emit
            with contextlib.suppress(Exception):
                on_text(text)
        return text

    def complete_with_tools(
        self,
        prompt: str,
        *,
        project_root: Path,
        allow_edits: bool,
        system: str | None = None,
        timeout: float = 60.0,
        cancel: threading.Event | None = None,
        max_tool_calls: int | None = None,
        tool_calling_enabled: bool = True,
        guard: Any = None,
        allow_command: str | None = None,
        tool_loop_policy: Any = None,
        repository_handle: Any = None,
        provider_id: str | None = None,
        deadline_budget: Any = None,
        deadline_clock: Any = None,
        autonomy: str | None = None,
    ) -> dict[str, Any]:
        """Run a continuous, checkpointed repository tool loop.

        Thin transport adapter over :class:`~opaihub.tool_loop.ToolLoopController`:
        the controller owns continuation, context compaction, and honest
        completion. ``12`` is a maintenance checkpoint, never a terminal budget.
        ``max_tool_calls`` is a deprecated, recoverable external ceiling that the
        GUI never sets; hitting it is a recoverable stop, not a fake completion.

        Authority is split (Task 6): ``allow_edits`` governs *mutations* (whether
        edit tools are exposed at all), while ``tool_calling_enabled`` governs
        whether any tools are offered. ``guard`` — a callable ``(turn_index) ->
        GuardDecision`` — runs before every provider turn; a non-allow decision
        stops the run with the guard's typed state (blocked / needs-consent /
        cancelled), never a fake completion. ``allow_command`` threads a
        one-shot user-approved confirm-class command down to the executor (F17).

        Per-turn ledger accounting (Task 6/7): every provider round-trip records
        a sequenced ``record_model_call_started``/``record_model_call_finalized``
        pair (ledger v2, ``call_id = run_id:turn_index``), best-effort and never
        fatal to the turn. This is *additive* instrumentation alongside the
        caller's existing aggregate ``record_model_call`` — usage.py still reads
        only the legacy aggregate event, so the aggregate call site is not (yet)
        retired; removing it is Task 8's usage.py v2 migration, not this one.

        ``deadline_budget`` caps every provider turn by the task time remaining;
        ``deadline_clock`` is injectable only to make that hard-clock behavior
        deterministic under test.
        """

        from .completion import CompletionState
        from .github_connector import public_read_allowed
        from .ledger import record_model_call_finalized, record_model_call_started
        from .provider_tools import RepositoryToolExecutor
        from .tool_loop import (
            ChatTurn,
            ToolLoopController,
            ToolLoopPolicy,
            ToolLoopProviderError,
            ToolLoopTaskDeadlineExceeded,
        )
        from .usage_report import ProviderTurnUsage

        deadline_clock = deadline_clock or time.monotonic
        task_started_at = deadline_clock()
        run_id = uuid.uuid4().hex[:16]
        resolved_provider_id = provider_id or (
            urllib.parse.urlsplit(self.base_url).hostname or self.name
        )
        turn_counter = {"n": 0}

        executor = RepositoryToolExecutor(
            project_root,
            allow_edits=allow_edits,
            allow_github_public_read=public_read_allowed(),
            allow_command=allow_command,
            repository_handle=repository_handle,
            autonomy=autonomy,
        )
        base_messages: list[dict[str, Any]] = []
        if system:
            base_messages.append({"role": "system", "content": system})
        base_messages.append({"role": "system", "content": _TOOL_LOOP_PROTOCOL})
        base_messages.append({"role": "user", "content": prompt})

        def chat(
            messages: list[dict[str, Any]], *, tools: list[dict[str, Any]]
        ) -> ChatTurn:
            turn_counter["n"] += 1
            turn_index = turn_counter["n"]
            call_id = f"{run_id}:{turn_index}"
            turn_timeout = timeout
            task_limited = False
            if deadline_budget is not None:
                elapsed = deadline_clock() - task_started_at
                remaining = deadline_budget.task_deadline_seconds - elapsed
                if remaining <= 0:
                    raise ToolLoopTaskDeadlineExceeded(
                        elapsed_seconds=elapsed,
                        provider_responsive=True if turn_index > 1 else None,
                        phase="before_provider_turn",
                        teardown_state="not_required",
                    )
                task_limited = remaining <= timeout
                turn_timeout = min(timeout, remaining)
            with contextlib.suppress(Exception):  # ledger never blocks a turn
                record_model_call_started(
                    project_root,
                    prompt,
                    call_id=call_id,
                    run_id=run_id,
                    turn_index=turn_index,
                    model_id=self.model,
                    provider_id=resolved_provider_id,
                    model_tier="L2",
                    provider_type="free_api",
                    confirmed=True,
                )
            try:
                result = self._chat(
                    messages, tools=tools, timeout=turn_timeout, cancel=cancel
                )
            except LocalRunCancelled:
                raise
            except ToolLoopTaskDeadlineExceeded:
                raise
            except TimeoutError as exc:
                if task_limited:
                    elapsed = deadline_clock() - task_started_at
                    raise ToolLoopTaskDeadlineExceeded(
                        elapsed_seconds=elapsed,
                        provider_responsive=True if turn_index > 1 else None,
                        phase="provider_turn",
                        teardown_state="transport_closed",
                    ) from exc
                raise ToolLoopProviderError(redact(str(exc))) from exc
            except Exception as exc:  # transport failure -> retryable state
                raise ToolLoopProviderError(redact(str(exc))) from exc
            message = (result.get("choices") or [{}])[0].get("message") or {}
            calls = message.get("tool_calls")
            calls = calls if isinstance(calls, list) else []
            usage = self._usage(result)
            # #674: the raw reasoning field, kept only long enough to be
            # replayed by the tool loop's own continuity contract
            # (ToolProtocolAtom.to_messages) — never logged, never recorded
            # to the ledger (record_model_call_finalized below takes token
            # counts and cost only), never placed on ToolLoopResult. It is
            # discarded for good the moment compaction folds this turn's
            # atom into a summary line (A4's retention-boundary rule).
            reasoning_content = message.get("reasoning_content")
            with contextlib.suppress(Exception):  # ledger never blocks a turn
                # #673: real cost for a paid runner, genuine $0 for free —
                # _cost_for is the one seam between them (PaidAPIRunner
                # overrides it; this base class's $0 is not a placeholder).
                cost_usd, cost_provenance = self._cost_for(usage)
                record_model_call_finalized(
                    project_root,
                    prompt,
                    call_id=call_id,
                    usage=ProviderTurnUsage.from_provider(
                        turn_index=turn_index,
                        total=usage.get("tokens"),
                        input_tokens=usage.get("input_tokens"),
                        output_tokens=usage.get("output_tokens"),
                        cost_usd=cost_usd,
                        cost_provenance=cost_provenance,
                        provider_quota=usage.get("quota_snapshot"),
                    ),
                )
            if deadline_budget is not None:
                elapsed = deadline_clock() - task_started_at
                if elapsed > deadline_budget.task_deadline_seconds:
                    raise ToolLoopTaskDeadlineExceeded(
                        elapsed_seconds=elapsed,
                        provider_responsive=True,
                        phase="provider_turn_complete",
                        teardown_state="not_required",
                    )
            return ChatTurn(
                content=str(message.get("content") or ""),
                tool_calls=tuple(call for call in calls if isinstance(call, dict)),
                usage=usage,
                reasoning_content=(
                    str(reasoning_content) if reasoning_content else None
                ),
                thinking_requested=self._thinking_requested(),
            )

        # The turn's message contract picks the budgets (tool calls, wall clock,
        # compaction threshold) so a multi-file refactor is not held to a
        # one-file fix's allowance. Absent a contract the defaults apply exactly
        # as before. The deprecated external ceiling is layered on top either
        # way, so an explicit caller-set ceiling still wins.
        policy = (
            tool_loop_policy if isinstance(tool_loop_policy, ToolLoopPolicy) else None
        )
        if policy is None:
            policy = ToolLoopPolicy(max_tool_calls=max_tool_calls)
        elif max_tool_calls is not None:
            policy = replace(policy, max_tool_calls=max_tool_calls)
        controller = ToolLoopController(policy)
        outcome = controller.run(
            chat=chat,
            executor=executor,
            base_messages=base_messages,
            allow_mutations=allow_edits,
            tool_calling_enabled=tool_calling_enabled,
            guard=guard,
            cancel=cancel,
            deadline_budget=deadline_budget,
            provider_id=resolved_provider_id,
            model_id=self.model,
            operation_id=run_id,
            operation_kind="model_call_free",
        )

        if outcome.completion_state is CompletionState.CANCELLED:
            raise LocalRunCancelled("Provider tool loop cancelled")

        self.last_usage = {
            "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens,
            "tokens": outcome.input_tokens + outcome.output_tokens,
            # One task re-sends the growing context every step, so the token
            # total is only honest next to the call count (#334).
            "model_calls": outcome.model_calls,
            "measurement": outcome.measurement,
            "quota_snapshot": outcome.quota_snapshot,
        }
        completed = outcome.completion_state is CompletionState.COMPLETED
        text = outcome.answer
        if not text and not completed:
            text = _STOP_MESSAGES.get(outcome.stopped_reason, "")
        timeout_info = outcome.timeout_event
        if timeout_info is not None:
            from .deadlines import enrich_timeout_event

            timeout_info = enrich_timeout_event(timeout_info, run_id=run_id)
        return {
            "text": text,
            "tool_trace": [dict(item) for item in outcome.tool_trace],
            "stopped_reason": (
                ""
                if completed
                else (outcome.stopped_reason or outcome.completion_state.value)
            ),
            "last_error": outcome.last_error,
            "completion_state": outcome.completion_state.value,
            "user_question": outcome.user_question,
            "blocked_reason": outcome.blocked_reason,
            "timed_out": timeout_info is not None,
            "timeout_event": timeout_info,
            "boundary_error": (
                dict(outcome.boundary_error) if outcome.boundary_error else None
            ),
            # #649/#565: repetition is operational telemetry, kept distinct
            # from provider spend and monetary savings claims.
            "repetition": dict(outcome.repetition or {}),
            # #569: a stop must be explainable. The envelope names the cause and
            # carries the evidence behind it, so surfaces can say what actually
            # happened instead of "provider failed". Built only for a run that
            # did not complete — a success has nothing to diagnose.
            "failure": None if completed else _diagnose_outcome(outcome).to_dict(),
            # #653: and a named cause is only useful if something proposes a
            # bounded next step. The deterministic library picks one — or
            # honestly declines — and records every candidate it considered,
            # so an absent recovery reads as a decision rather than silence.
            "recovery": None
            if completed
            else _propose_recovery(outcome, project_root=project_root),
        }


@dataclass(frozen=True)
class ThinkingControl:
    """The model control #673 A5 calls for: Auto/On/Off + reasoning effort.

    ``mode``:

    - ``"off"`` — thinking is never requested. The safe default and the
      whole of #673 Phase 1's behaviour, unchanged.
    - ``"on"`` — every turn is dispatched with DeepSeek's ``thinking``
      field enabled, continuity handled by the tool loop (#674).
    - ``"auto"`` — currently identical to ``"off"``. A5's own text says
      "thinking mode should follow task policy rather than always enabling
      expensive reasoning", but Vesta has no task-policy signal to drive that
      decision yet; wiring one is future work, not silently guessed at here.
      ``"auto"`` exists as a distinct value now so that future work has
      somewhere to attach without a call-site migration, not because it
      currently behaves differently from ``"off"``.

    ``effort`` (``"high"`` or ``"max"``) is only meaningful when thinking is
    requested — constructing this with an effort set while ``mode`` is not
    ``"on"`` is rejected rather than silently ignored, the same fail-fast
    discipline as the budget/recipe validators elsewhere in this codebase.
    """

    mode: str = "auto"
    effort: str | None = None

    _VALID_MODES = frozenset({"auto", "on", "off"})
    _VALID_EFFORTS = frozenset({"high", "max"})

    def __post_init__(self) -> None:
        if self.mode not in self._VALID_MODES:
            raise ValueError(
                f"thinking mode must be one of {sorted(self._VALID_MODES)}, "
                f"got {self.mode!r}"
            )
        if self.effort is not None:
            if self.effort not in self._VALID_EFFORTS:
                raise ValueError(
                    "reasoning_effort must be one of "
                    f"{sorted(self._VALID_EFFORTS)}, got {self.effort!r}"
                )
            if self.mode != "on":
                raise ValueError(
                    "reasoning_effort requires mode='on' "
                    f"(got mode={self.mode!r}); a caller that wants effort "
                    "must say so explicitly rather than have it ignored"
                )

    @property
    def requests_thinking(self) -> bool:
        """Whether a dispatched turn should ask the provider to think.

        Deliberately narrower than ``mode != 'off'`` — see the ``"auto"``
        case in the class docstring.
        """
        return self.mode == "on"

    def payload_fields(self) -> dict[str, Any]:
        """Fields to merge into the outbound chat-completions payload.

        Empty when thinking is not requested — Phase 1's proven-safe
        behaviour of never sending the field at all, unchanged.
        """
        if not self.requests_thinking:
            return {}
        fields: dict[str, Any] = {"thinking": {"type": "enabled"}}
        if self.effort is not None:
            fields["reasoning_effort"] = self.effort
        return fields


class PaidAPIRunner(FreeAPIRunner):
    """OpenAI-compatible runner for paid, key-gated direct APIs (#673).

    Everything about the transport is identical to :class:`FreeAPIRunner` —
    same auth, same streaming, same tool-loop machinery via
    ``complete_with_tools`` — real per-token spend is the only thing that
    differs, so this overrides exactly the seams that carry cost and
    thinking mode: ``name`` (so free-tier telemetry never mislabels a paid
    call), ``_cost_for`` (so the ledger records a real number instead of
    ``$0``), and ``_extra_chat_fields``/``_thinking_requested`` (#674).

    Thinking mode (#673 Phase 1 scoped this out; #674 completes it): DeepSeek
    tool calls made under thinking mode require the provider's
    ``reasoning_content`` to be replayed on the next turn or the provider
    rejects the continuation. That continuity is the tool loop's
    responsibility (``opaihub/tool_loop.py``'s ``ToolProtocolAtom`` and
    ``ReasoningContinuityError``); this runner's job is only to request
    thinking when a caller's :class:`ThinkingControl` says to, and to hand
    the raw ``reasoning_content`` field to the tool loop untouched. Passing
    no control at all reproduces Phase 1 exactly: thinking is off by default.
    """

    name = "paid-api"

    def _auth_headers(self) -> dict[str, str]:
        from .execution_scope import managed_budget_gate

        gate = managed_budget_gate(Path.cwd(), next_cost_usd=None)
        if gate["denied"]:
            raise RuntimeError("; ".join(gate["reasons"]))
        return super()._auth_headers()

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        *,
        pricing_model_id: str,
        thinking: ThinkingControl | None = None,
    ) -> None:
        super().__init__(base_url, model, api_key)
        # Separate from `model` (the id sent to the provider) on purpose: a
        # future alias/rename in the picker must not silently change which
        # price row a call is costed against.
        self._pricing_model_id = pricing_model_id
        self._thinking = thinking if thinking is not None else ThinkingControl()

    def _extra_chat_fields(self) -> dict[str, Any]:
        return self._thinking.payload_fields()

    def _thinking_requested(self) -> bool:
        return self._thinking.requests_thinking

    def _cost_for(self, usage: dict[str, Any]) -> tuple[float | None, str]:
        from .deepseek_pricing import estimate_cost_usd

        cost_usd, measurement = estimate_cost_usd(
            self._pricing_model_id,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )
        if cost_usd is None:
            # #673 A6: unknown cost must never display as zero. None is
            # ProviderTurnUsage.from_provider's own signal for "unresolved" —
            # it becomes UNKNOWN_USAGE, not a $0 line item, even though real
            # money may have moved: the model is unknown to the pricing
            # table. "unknown" is usage_report's provenance vocabulary
            # (deepseek_pricing's own "unavailable" is provider_catalog's
            # separate vocabulary — from_provider ignores this string
            # whenever cost_usd is None, but keep it a real member of
            # _COST_PROVENANCE rather than leak the other module's word).
            return None, "unknown"
        if measurement == "estimated_stale":
            # deepseek_pricing still returns a real, computed number for a
            # stale snapshot (never None — see its own docstring on why) but
            # names it with a word ProviderTurnUsage's closed provenance set
            # does not contain. "estimated" is the closest true member: the
            # figure IS a real, arithmetic estimate, just against a price
            # table due for a refresh. The legacy record_model_call path
            # (opai/app_state.py) has no such closed set and keeps the more
            # specific "estimated_stale" label as-is.
            measurement = "estimated"
        return cost_usd, measurement


# Controller stop reasons that already name their own cause, so the envelope
# must not re-derive one by sniffing error text (#569).
_STOP_REASON_CATEGORIES = {
    "no_progress": "NO_PROGRESS",
    "exploration_limit": "NO_PROGRESS",
    "repeated_failure": "REPEATED_FAILURE",
    "repeated_success": "NO_PROGRESS",
    "controller_timeout": "NO_PROGRESS",
    "cancelled": "CANCELLED",
    # #674: forced rather than left to classify_failure's text heuristics —
    # the message is this module's own wording, not provider error text, so
    # nothing guarantees a regex signature would match it.
    "reasoning_continuity_error": "PROVIDER_ERROR",
}


def _diagnose_outcome(outcome: Any) -> Any:
    """Build a FailureEnvelope for a run that did not complete."""

    from .failure_envelope import EvidenceCollector, FailureCategory, FailureEnvelope

    named = _STOP_REASON_CATEGORIES.get(str(outcome.stopped_reason or ""))
    category = getattr(FailureCategory, named) if named else None

    # The trace is where the real cause lives: the first failing tool call is
    # usually the critical error, and the rest is its consequence.
    collector = EvidenceCollector()
    for index, item in enumerate(outcome.tool_trace, start=1):
        collector.observe(
            index,
            str(item.get("tool") or ""),
            ok=bool(item.get("ok")),
            detail=str(item.get("error_code") or item.get("message") or ""),
        )
    failing = next(
        (item for item in outcome.tool_trace if not item.get("ok")),
        {},
    )
    return FailureEnvelope.diagnose(
        error_text=outcome.last_error
        or str(failing.get("message") or failing.get("error_code") or ""),
        tool=str(failing.get("tool") or ""),
        evidence=collector.as_tuple(),
        outcome=str(outcome.stopped_reason or ""),
        progress=outcome.progress,
        category=category,
    )


def _propose_recovery(
    outcome: Any, *, project_root: Path | None = None
) -> dict[str, Any] | None:
    """#653: the deterministic recovery proposal for an incomplete run.

    Selection only — nothing is executed here. Automatic execution belongs to
    the convergence controller (#648), which does not exist yet; proposing
    without executing is the honest half available today, and it is what
    stops this library being another primitive nobody adopted.

    Two envelopes are tried, in a fixed order so the result stays
    deterministic. ``_diagnose_outcome`` forces the category from the loop's
    ``stopped_reason`` — ``resolved = category or classify_failure(...)`` in
    ``failure_envelope.diagnose`` — which correctly answers "why did the loop
    stop" but hides the tool-level cause underneath it. A GitHub CLI
    deprecation that ends a run via ``repeated_failure`` would otherwise
    never reach the tool-drift recipe. So when the loop-level envelope
    selects nothing and a failing tool left usable error text, the
    text-classified cause gets a second look, and both attempts are merged
    into one trace so every candidate stays visible.

    The failing tool is passed as ``failing_action`` so a recipe can never
    re-suggest the call that just failed.
    """

    from .recovery_recipes import select_recipe_traced

    try:
        failing = next((item for item in outcome.tool_trace if not item.get("ok")), {})
        failing_action = str(failing.get("tool") or "")
        # Only facts the tool loop actually knows. Anything absent stays
        # absent: `violates` fails closed on the requirements that protect
        # the user, and inventing a fact here to unblock a recipe would
        # defeat exactly that.
        context = {"has_repository": project_root is not None}

        trace = select_recipe_traced(
            _diagnose_outcome(outcome),
            failing_action=failing_action,
            context=context,
        )
        if trace.selected is None:
            error_text = str(
                outcome.last_error
                or failing.get("message")
                or failing.get("error_code")
                or ""
            )
            if error_text:
                from .failure_envelope import FailureEnvelope

                second = select_recipe_traced(
                    FailureEnvelope.diagnose(
                        error_text=error_text,
                        tool=failing_action,
                        evidence=(),
                        outcome=str(outcome.stopped_reason or ""),
                    ),
                    failing_action=failing_action,
                    context=context,
                )
                if second.selected is not None:
                    trace = replace(
                        second, considered=trace.considered + second.considered
                    )
    except Exception:  # noqa: BLE001 - a proposal must never break the result
        return None

    payload = trace.to_dict()
    if trace.selected is not None:
        payload["action"] = trace.selected.action.to_dict()
        payload["caps"] = trace.selected.caps.to_dict()
        payload["terminal_verdict"] = trace.selected.terminal_verdict.value
        payload["requires_reconciliation"] = trace.selected.requires_reconciliation
        payload["family"] = (
            trace.selected.family.value if trace.selected.family else None
        )
    return payload


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
    model connected", and Vesta's Auto route still works.
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
    model_id: str,
    project_root: Path | None = None,
    *,
    thinking: ThinkingControl | None = None,
) -> LocalRunner | None:
    """Build a runner bound to a specific ``provider:model`` id from the picker.

    ``thinking`` (#674, #673 A5) only matters for a ``paid:`` model whose
    runner supports it; every other branch ignores it, so passing it for a
    non-DeepSeek pick is harmless rather than an error. No picker surface
    sets it yet -- that UI is A5's own remaining scope -- so today it is only
    reachable by a caller passing it explicitly.
    """
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

    # Paid direct-API models: "paid:<provider>:<model_id>" (#673) — same
    # key-from-env-var contract as the free tier, real per-token cost.
    if model_id.startswith("paid:"):
        from .credentials import CredentialStore
        from .paid_api_models import spec_for_model_id as paid_spec_for_model_id

        spec = paid_spec_for_model_id(model_id)
        if spec is None:
            return None
        api_key = CredentialStore().get(spec["provider"]) or ""
        return PaidAPIRunner(
            spec["api_base"],
            spec["model_id"],
            api_key,
            pricing_model_id=spec["model_id"],
            thinking=thinking,
        )

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
