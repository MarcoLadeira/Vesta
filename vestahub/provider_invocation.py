"""Pre-dispatch provider invocation plans.

The plan is compiled from a runner signature before any provider operation is
recorded or dispatched.  Call sites then execute the compiled plan exactly
once; runtime exceptions are provider/adapter failures, never capability
signals that justify a second call.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


INVOCATION_PROTOCOL_VERSION = 1


class ProviderInvocationCompatibilityError(ValueError):
    """The runner cannot satisfy the invocation contract without dispatch."""

    code = "ADAPTER_INCOMPATIBLE"


def _callable_identity(target: Callable[..., Any]) -> str:
    module = str(getattr(target, "__module__", "") or "").strip()
    qualname = str(
        getattr(target, "__qualname__", "")
        or getattr(target, "__name__", "")
        or type(target).__qualname__
    ).strip()
    identity = ".".join(part for part in (module, qualname) if part)
    return identity[:512] or "unknown-adapter"


@dataclass(frozen=True)
class ProviderInvocationPlan:
    """One validated call shape for one provider adapter method."""

    provider_id: str
    method_name: str
    adapter_id: str
    accepted_keywords: frozenset[str]
    accepts_var_keywords: bool
    native_operation_id: bool
    _target: Callable[..., Any] = field(repr=False, compare=False)
    _signature: inspect.Signature = field(repr=False, compare=False)
    protocol_version: int = INVOCATION_PROTOCOL_VERSION

    @classmethod
    def prepare(
        cls,
        target: Callable[..., Any],
        *,
        provider_id: str,
        method_name: str,
        require_request: bool = True,
    ) -> ProviderInvocationPlan:
        """Inspect and validate a callable without invoking it."""

        if not callable(target):
            raise ProviderInvocationCompatibilityError(f"{method_name} is not callable")
        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError) as exc:
            raise ProviderInvocationCompatibilityError(
                f"{method_name} has no inspectable invocation signature"
            ) from exc
        parameters = signature.parameters
        accepts_var_keywords = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        accepted_keywords = frozenset(
            name
            for name, parameter in parameters.items()
            if parameter.kind
            in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        )
        if require_request:
            try:
                signature.bind_partial(object())
            except TypeError as exc:
                raise ProviderInvocationCompatibilityError(
                    f"{method_name} cannot accept a provider request"
                ) from exc
        return cls(
            provider_id=str(provider_id or "unknown").strip().lower() or "unknown",
            method_name=str(method_name or "complete").strip() or "complete",
            adapter_id=_callable_identity(target),
            accepted_keywords=accepted_keywords,
            accepts_var_keywords=accepts_var_keywords,
            native_operation_id=(
                accepts_var_keywords or "operation_id" in accepted_keywords
            ),
            _target=target,
            _signature=signature,
        )

    def supports(self, name: str) -> bool:
        return self.accepts_var_keywords or name in self.accepted_keywords

    def keyword_arguments(
        self,
        *,
        required: Mapping[str, Any],
        optional: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compile one keyword set and fail before dispatch if it is invalid."""

        missing = sorted(name for name in required if not self.supports(name))
        if missing:
            names = ", ".join(missing)
            raise ProviderInvocationCompatibilityError(
                f"{self.method_name} does not support required arguments: {names}"
            )
        kwargs = dict(required)
        for name, value in (optional or {}).items():
            if self.supports(name):
                kwargs[name] = value
        try:
            self._signature.bind(object(), **kwargs)
        except TypeError as exc:
            raise ProviderInvocationCompatibilityError(
                f"{self.method_name} does not conform to invocation protocol "
                f"v{self.protocol_version}"
            ) from exc
        return kwargs

    def invoke(self, request: str, kwargs: Mapping[str, Any]) -> Any:
        """Execute the already-validated provider call exactly once."""

        return self._target(request, **dict(kwargs))

    def to_dict(
        self,
        *,
        operation_id: str = "",
        model_id: str = "",
    ) -> dict[str, Any]:
        return {
            "schema_version": self.protocol_version,
            "provider_id": self.provider_id,
            "model_id": str(model_id or ""),
            "adapter_id": self.adapter_id,
            "method": self.method_name,
            "operation_id": str(operation_id or ""),
            "native_operation_id": self.native_operation_id,
            "single_dispatch": True,
        }


__all__ = [
    "INVOCATION_PROTOCOL_VERSION",
    "ProviderInvocationCompatibilityError",
    "ProviderInvocationPlan",
]
