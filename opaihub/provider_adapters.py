"""Small, stable adapter contract shared by provider diagnostics and tests."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from .provider_capabilities import provider_profile
from .provider_catalog import all_catalog_records


def _catalog_kind(record: dict[str, Any]) -> str:
    requirements = record["requirements"]
    if requirements["local_service"]:
        return "local"
    if requirements["cli"]:
        return "account"
    return "free"


_CATALOG_RECORDS = tuple(all_catalog_records())
ACCOUNT_PROVIDERS = frozenset(
    record["provider_id"]
    for record in _CATALOG_RECORDS
    if _catalog_kind(record) == "account"
)
FREE_PROVIDERS = frozenset(
    record["provider_id"]
    for record in _CATALOG_RECORDS
    if _catalog_kind(record) == "free"
)
LOCAL_PROVIDERS = frozenset(
    record["provider_id"]
    for record in _CATALOG_RECORDS
    if _catalog_kind(record) == "local"
)
SUPPORTED_PROVIDERS = frozenset(record["provider_id"] for record in _CATALOG_RECORDS)


@dataclass(frozen=True)
class ExecutionRequest:
    """Explicit provider execution boundary; workflow state remains in OPai."""

    prompt: str
    cwd: str
    mode: str = "ask"
    model: str | None = None
    sandbox: str = "read-only"
    permission: str = "on-request"
    max_retries: int = 0
    out_file: str | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    """Concrete execution abilities exposed by one provider transport."""

    repo_read: bool
    patch_edit: bool
    run_tests: bool
    native_tools: bool
    streaming: bool


@dataclass(frozen=True)
class ProviderExecutionPlan:
    """Effective capabilities after user policy and provider support intersect."""

    provider_id: str
    mode: str
    allow_edits: bool
    tools: tuple[str, ...]


_READ_TOOLS = ("find_files", "search_code", "read_file")
_WRITE_TOOLS = ("apply_patch", "run_tests")


def gemini_approval_mode(mode: str) -> str:
    """Map OPai autonomy to Gemini CLI's current approval vocabulary."""

    return {
        "ask": "plan",
        "plan": "plan",
        "approve-edits": "auto_edit",
        "safe-auto": "auto_edit",
        "full-auto": "yolo",
    }.get(str(mode or "").lower(), "plan")


def opai_mode_for_gemini_approval(approval_mode: str) -> str | None:
    """Translate a supported Gemini CLI approval mode back to OPai autonomy."""

    return {
        "plan": "plan",
        "default": "ask",
        "auto_edit": "safe-auto",
        "yolo": "full-auto",
    }.get(str(approval_mode or "").lower())


def resolve_execution_plan(
    adapter: ProviderAdapter,
    policy: Any,
    *,
    effective_mode: str,
) -> ProviderExecutionPlan:
    """Intersect current intent, autonomy, and physical provider abilities."""

    from .agent_policy import AgentMode

    capabilities = adapter.capabilities
    writable_intent = policy.mode in {AgentMode.IMPLEMENT, AgentMode.SHIP}
    requested_mode = str(effective_mode or "ask")
    editable_mode = requested_mode in {"safe-auto", "full-auto"}
    allow_edits = bool(writable_intent and editable_mode and capabilities.patch_edit)
    mode = (
        requested_mode
        if allow_edits
        else ("plan" if requested_mode == "plan" else "ask")
    )
    tools = _READ_TOOLS if capabilities.repo_read else ()
    if allow_edits:
        tools += tuple(
            tool
            for tool, supported in (
                ("apply_patch", capabilities.patch_edit),
                ("run_tests", capabilities.run_tests),
            )
            if supported
        )
    return ProviderExecutionPlan(adapter.provider_id, mode, allow_edits, tools)


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

    def __post_init__(self) -> None:
        provider = str(self.provider_id or "").strip().lower()
        provider_profile(provider)
        object.__setattr__(self, "provider_id", provider)

    @property
    def kind(self) -> str:
        return self.profile.kind

    @property
    def capabilities(self) -> ProviderCapabilities:
        profile = self.profile
        status = profile.capability_status
        return ProviderCapabilities(
            status["repo_read"] == "supported",
            status["repo_editing"] == "supported",
            status["run_tests"] == "supported",
            bool(profile.requires_cli and status["tool_calling"] == "supported"),
            status["streaming"] == "supported",
        )

    @property
    def profile(self) -> Any:
        """The rich capability + requirements record for this provider (#168).

        Distinct from ``capabilities`` (the narrow execution abilities used to
        build the tool set): ``profile`` is the picker/settings/doctor truth —
        chat / code execution / repo editing / streaming / tool calling plus what
        the provider requires and whether it can be cancelled.
        """
        return provider_profile(self.provider_id)

    def health(self, status: dict[str, Any] | None = None) -> Any:
        """Canonical :class:`ProviderHealth` for this provider (#168).

        Pure over a status/connection dict so callers control whether any probe
        ran — no hidden network or CLI calls here. With no status, health is
        honestly ``unknown`` rather than an assumed-healthy default.
        """
        from .provider_capabilities import ProviderHealth, canonical_health

        if not status:
            return ProviderHealth.UNKNOWN
        if not any(
            name in status
            for name in (
                "installed",
                "configured",
                "authenticated",
                "authorised",
                "authorized",
                "healthy",
            )
        ):
            return canonical_health(
                auth_status=status.get("authStatus") or status.get("auth_status"),
                cli_installed=status.get("cliInstalled", status.get("cli_present")),
                error_code=status.get("lastErrorCode") or status.get("error_code"),
                kind=status.get("kind", self.kind),
            )
        readiness = self.readiness(status)
        if readiness.installed is False:
            return ProviderHealth.NOT_INSTALLED
        if readiness.configured is False:
            return ProviderHealth.NOT_CONFIGURED
        if readiness.healthy is False or readiness.authorised is False:
            return ProviderHealth.DEGRADED
        if readiness.authenticated is True:
            return ProviderHealth.AUTHENTICATED
        if readiness.authenticated is False or readiness.configured is True:
            return ProviderHealth.CONFIGURED
        return ProviderHealth.UNKNOWN

    def readiness(self, status: dict[str, Any] | None = None) -> Any:
        """Return independent protocol readiness facts without inferring them.

        ``health()`` intentionally retains its legacy one-enum projection.  This
        method only accepts explicit readiness booleans, so an ``authStatus``
        label can never be mistaken for authentication, authorisation, or a
        general health verdict.
        """

        from .provider_protocol import ProviderReadiness

        source = status or {}

        def _bool(*names: str) -> bool | None:
            for name in names:
                value = source.get(name)
                if isinstance(value, bool):
                    return value
            return None

        healthy = _bool("healthy")
        reason = source.get("degradedReason", source.get("degraded_reason"))
        action = source.get("nextAction", source.get("next_action"))
        if healthy is False and (not isinstance(reason, str) or not reason.strip()):
            reason = "provider_unhealthy"
        if healthy is False and (not isinstance(action, str) or not action.strip()):
            action = "Check provider diagnostics, then retry."
        installed = _bool("installed")
        if installed is None and self.profile.requires_cli:
            installed = _bool("cliInstalled", "cli_present")
        return ProviderReadiness(
            provider_id=self.provider_id,
            installed=installed,
            configured=_bool("configured"),
            authenticated=_bool("authenticated"),
            authorised=_bool("authorised", "authorized"),
            healthy=healthy,
            degraded_reason=reason if healthy is False else None,
            next_action=action if healthy is False else None,
            protocol_version=self.profile.protocol_version,
        )

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

    def prepare_execution(
        self,
        request: ExecutionRequest,
        adapter_request: Any | None = None,
    ) -> dict[str, Any]:
        """Build a structured, cancellable account-provider invocation.

        Provider-specific environment values are intentionally not returned;
        ``AccountRunner`` sanitizes them at spawn time. The public contract only
        reports which override names would be removed.
        """

        if adapter_request is not None:
            self.validate_request(adapter_request)
        if self.kind != "account":
            raise ValueError(
                "Structured CLI execution is only available for account providers"
            )
        from .accounts import AccountRunner
        from .proc import provider_child_env

        mode = str(request.mode or "ask")
        if self.provider_id == "codex":
            expected_sandbox = (
                "workspace-write" if mode in {"safe-auto", "full-auto"} else "read-only"
            )
            # Codex exec has no hook protocol, so build_command keeps every
            # mode — full-auto included — on fail-closed ``on-request`` (F23).
            expected_permission = "on-request"
            if (
                request.sandbox != expected_sandbox
                or request.permission != expected_permission
            ):
                raise ValueError(
                    "Codex sandbox and permission must match the centralized OPai mode"
                )
        command = AccountRunner(
            self.provider_id,
            self.provider_id,
            model=request.model,
        ).build_command(
            request.prompt,
            mode=mode,
            out_file=request.out_file,
            stream=True,
        )
        _environment, removed = provider_child_env(self.provider_id)
        return {
            "provider": self.provider_id,
            "cwd": str(request.cwd),
            "command": command,
            "sandbox": request.sandbox,
            "permission": request.permission,
            "model": request.model or "",
            "supports_cancel": True,
            "environment_sanitized": True,
            "removed_environment_names": removed,
            "retry": {
                "max_attempts": max(1, int(request.max_retries) + 1),
                "retryable": ["TIMEOUT", "RATE_LIMIT", "PROVIDER_UNAVAILABLE"],
            },
        }

    def validate_request(self, adapter_request: Any) -> None:
        """Fail before transport setup unless a protocol request matches the catalog."""

        from .provider_protocol import (
            AdapterRequest,
            ProtocolViolation,
            negotiate_capabilities,
        )

        if not isinstance(adapter_request, AdapterRequest):
            raise ProtocolViolation("adapter_request must be an AdapterRequest")
        if adapter_request.provider_id != self.provider_id:
            raise ProtocolViolation(
                "adapter_request provider_id must match the adapter"
            )
        readiness = negotiate_capabilities(
            adapter_request,
            self.profile.capability_status,
        )
        if readiness.degraded_reason is not None:
            raise ProtocolViolation(readiness.degraded_reason)

    def normalize_event(self, event: dict[str, Any] | str) -> dict[str, Any]:
        """Normalize parser output into non-terminal protocol observations.

        Parser ``done`` and ``cost`` hints remain useful to their legacy stream
        owner, but this adapter boundary does not assert completion, price,
        verification, or authority.  It emits caller-managed observations that
        a later OPai layer may assign stream sequence numbers and timestamps to.
        """

        from opai.activity import parse_claude_line, parse_codex_line

        line = event if isinstance(event, str) else json.dumps(event)
        if self.provider_id == "claude":
            parsed = parse_claude_line(line)
        elif self.provider_id == "codex":
            parsed = parse_codex_line(line)
        else:
            parsed = {
                "events": [],
                "text": "",
                "error": "",
                "cost": None,
                "done": False,
            }
        return {
            "kind": "provider_event",
            "provider": self.provider_id,
            "events": parsed.get("events") or [],
            "text": parsed.get("text") or "",
            "error": parsed.get("error") or "",
            "cost": None,
            "done": False,
            "protocolObservations": self._protocol_observations(parsed),
        }

    def _protocol_observations(self, parsed: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert only safe observations; stream envelope ownership stays upstream."""

        from .provider_protocol import EventKind

        observations: list[dict[str, Any]] = []

        def _append(kind: EventKind, payload: dict[str, Any]) -> None:
            observations.append({"kind": kind.value, "payload": payload})

        text = parsed.get("text")
        if isinstance(text, str) and text:
            _append(EventKind.TEXT_DELTA, {"text": text})
        for activity in parsed.get("events") or []:
            if not isinstance(activity, dict):
                continue
            name = activity.get("type")
            if name in {
                "tool_call",
                "file_read",
                "file_edit",
                "command_run",
                "context_read",
            }:
                _append(EventKind.TOOL_CALL, {"name": str(name)})
        error = parsed.get("error")
        if isinstance(error, str) and error:
            _append(EventKind.ERROR, {"message": error})
        return observations

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
