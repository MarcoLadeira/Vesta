"""Guided, end-to-end local-model onboarding readiness (#3).

Free, local-first value is only credible if a new user can go from
"no local model" to a verified local route without OPai silently downloading
software, starting a service, or making a network call. Discovery already
tells you a runtime binary exists; this module adds the missing *state
machine*: is the server actually running, does it have a model, is the
endpoint reachable, or is it responding with the wrong shape — each with a
concrete, consent-gated next command.

Nothing here downloads a model, starts a service, or reaches a public host on
its own. Install/start/pull commands are returned as text with
``requires_consent`` set; the caller shows them and the user runs them. The
reachability probe and ``which`` are injectable, so tests are hermetic.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .local_runner import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DISCOVERY_TIMEOUT,
)
from .local_models import classify_endpoint

# Ordered runtime states, worst → best. `ready` is the only routable state.
STATES = (
    "not_installed",
    "stopped",
    "unreachable",
    "incompatible",
    "no_model",
    "ready",
)


@dataclass(frozen=True)
class ProbeResult:
    reachable: bool
    valid: bool  # response had the expected shape (models/tags list)
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeSpec:
    id: str
    label: str
    binary: str
    endpoint_env: str
    default_url: str
    models_path: str  # e.g. /api/tags (ollama) or /models (openai)
    install_cmd: str
    start_cmd: str
    pull_cmd: str  # pull/download a model (may be "")


_RUNTIMES: tuple[RuntimeSpec, ...] = (
    RuntimeSpec(
        id="ollama",
        label="Ollama",
        binary="ollama",
        endpoint_env="OLLAMA_HOST",
        default_url=DEFAULT_OLLAMA_URL,
        models_path="/api/tags",
        install_cmd="curl -fsSL https://ollama.com/install.sh | sh",
        start_cmd="ollama serve",
        pull_cmd=f"ollama pull {DEFAULT_OLLAMA_MODEL}",
    ),
    RuntimeSpec(
        id="openai-compatible",
        label="LM Studio / OpenAI-compatible",
        binary="lmstudio",
        endpoint_env="LOCAL_MODEL_URL",
        default_url="http://127.0.0.1:1234/v1",
        models_path="/models",
        install_cmd="Install LM Studio (or any OpenAI-compatible server) and start it",
        start_cmd="Start your local server, then set LOCAL_MODEL_URL to its /v1 endpoint",
        pull_cmd="Download a model inside LM Studio (or your server) and load it",
    ),
)

ProbeFn = Callable[[str, str], ProbeResult]
WhichFn = Callable[[str], "str | None"]


def _default_probe(base_url: str, models_path: str) -> ProbeResult:
    """Reach the local endpoint once and report reachable/valid/models.

    Loopback/private only by default (the #19 classifier); a public host is
    never probed here. Failures degrade to unreachable — never an exception.
    """
    from .local_runner import _http_json

    if not classify_endpoint(base_url)["is_local"]:
        return ProbeResult(reachable=False, valid=False)
    url = base_url.rstrip("/") + models_path
    try:
        data = _http_json(url, timeout=DISCOVERY_TIMEOUT)
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return ProbeResult(reachable=False, valid=False)
    names: list[str] = []
    if isinstance(data, dict) and "models" in data:  # ollama /api/tags
        for entry in data.get("models") or []:
            name = (entry or {}).get("name") or (entry or {}).get("model")
            if name:
                names.append(str(name))
        return ProbeResult(reachable=True, valid=True, models=tuple(names))
    if isinstance(data, dict) and "data" in data:  # openai /models
        for entry in data.get("data") or []:
            mid = (entry or {}).get("id")
            if mid:
                names.append(str(mid))
        return ProbeResult(reachable=True, valid=True, models=tuple(names))
    # Reachable but the response is not a recognizable models list.
    return ProbeResult(reachable=True, valid=False)


def _resolve_url(spec: RuntimeSpec, env: Mapping[str, str]) -> str:
    raw = env.get(spec.endpoint_env) or spec.default_url
    url = str(raw).strip()
    if "://" not in url:
        url = "http://" + url
    if spec.id == "openai-compatible" and not url.rstrip("/").endswith("/v1"):
        url = url.rstrip("/") + "/v1"
    return url


def _classify(installed: bool, endpoint_set: bool, probe: ProbeResult) -> str:
    if probe.reachable and probe.valid and probe.models:
        return "ready"
    if probe.reachable and probe.valid:
        return "no_model"
    if probe.reachable and not probe.valid:
        return "incompatible"
    # Not reachable:
    if installed:
        return "stopped"  # binary present but the server isn't answering
    if endpoint_set:
        return "unreachable"  # a URL was configured but nothing is there
    return "not_installed"


def _next_step(spec: RuntimeSpec, state: str) -> dict[str, Any]:
    """A concrete, consent-gated next command for the runtime's state."""
    mapping = {
        "not_installed": (
            spec.install_cmd,
            True,
            "Installs software over the network — run it yourself.",
        ),
        "stopped": (
            spec.start_cmd,
            True,
            "Starts a local service — run it yourself.",
        ),
        "unreachable": (
            spec.start_cmd,
            False,
            "The configured endpoint isn't answering; start the server or fix the URL.",
        ),
        "incompatible": (
            "",
            False,
            "The endpoint answered but not with a models list — check the URL/version.",
        ),
        "no_model": (
            spec.pull_cmd,
            True,
            "Downloads a model over the network — run it yourself.",
        ),
        "ready": ("", False, ""),
    }
    command, requires_consent, reason = mapping[state]
    return {
        "command": command,
        "requires_consent": bool(requires_consent),
        "reason": reason,
    }


def local_runtime_readiness(
    *,
    which: WhichFn = shutil.which,
    probe: ProbeFn = _default_probe,
    env: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Per-runtime readiness with concrete, consent-gated repair steps (#3)."""
    environment = dict(os.environ if env is None else env)
    results: list[dict[str, Any]] = []
    for spec in _RUNTIMES:
        installed = which(spec.binary) is not None
        endpoint_set = bool(environment.get(spec.endpoint_env))
        url = _resolve_url(spec, environment)
        probed = probe(url, spec.models_path)
        state = _classify(installed, endpoint_set, probed)
        results.append(
            {
                "id": spec.id,
                "label": spec.label,
                "installed": installed,
                "endpoint": url,
                "reachable": probed.reachable,
                "state": state,
                "models": list(probed.models),
                "ready": state == "ready",
                "next_step": _next_step(spec, state),
            }
        )
    return results


def local_onboarding_status(
    project_root: Path | None = None,
    *,
    which: WhichFn = shutil.which,
    probe: ProbeFn = _default_probe,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Aggregate readiness across runtimes into one guided-onboarding view."""
    runtimes = local_runtime_readiness(which=which, probe=probe, env=env)
    ready = [item for item in runtimes if item["ready"]]
    if ready:
        summary = (
            f"{ready[0]['label']} is ready with {len(ready[0]['models'])} model(s)."
        )
        next_action = ""
    else:
        # Guide toward the runtime closest to ready (highest STATES index).
        best = max(runtimes, key=lambda item: STATES.index(item["state"]))
        summary = (
            f"No local model is ready. Closest: {best['label']} ({best['state']})."
        )
        next_action = best["next_step"]["command"]
    return {
        "ready": bool(ready),
        "runtimes": runtimes,
        "ready_models": [
            f"{item['id']}:{name}" for item in ready for name in item["models"]
        ],
        "summary": summary,
        "next_action": next_action,
        "privacy": (
            "OPai never downloads a model, starts a service, or reaches a public "
            "host on its own. Commands are shown for you to run."
        ),
    }


def local_route_smoke_test(
    project_root: Path,
    *,
    runner: Any = None,
    task: str = "Reply with the single word: ok",
) -> dict[str, Any]:
    """Run one privacy-safe local completion to prove the route works (#3).

    Uses ``run_ask`` with ``record=False``/``store_answer=False`` so nothing is
    persisted. In tests a fake runner is injected; no network or prompt storage.
    """
    from .ask import run_ask

    result = run_ask(
        project_root,
        task,
        runner=runner,
        record=False,
        store_answer=False,
    )
    status = str(result.get("status") or "")
    answered = status in {"answered_locally", "cache_hit"}
    return {
        "ran": True,
        "status": status,
        "answered": answered,
        "source": result.get("source", ""),
        "runner": result.get("runner", ""),
        "model": result.get("model", ""),
        "privacy": "Smoke test is not recorded and its answer is not cached.",
    }
