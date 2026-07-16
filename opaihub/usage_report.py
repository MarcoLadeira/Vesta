"""Canonical, immutable provider-turn usage accounting.

Missing measurements stay unknown, provider totals remain independent from
their components, and every metric retains its own provenance.  This module is
dependency-light so runners, ledgers, workflow telemetry, and Settings can all
consume the same object.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping

from .model_identity import canonical_usage_model_id

UsageProvenance = Literal[
    "provider", "derived", "estimated", "mixed", "actual", "unknown"
]
_PROVENANCE = {"provider", "derived", "estimated", "mixed", "actual", "unknown"}
_TOKEN_PROVENANCE = {"provider", "derived", "estimated", "mixed", "unknown"}
_COST_PROVENANCE = {"actual", "derived", "estimated", "mixed", "unknown"}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class UsageValue:
    value: int | float | None
    provenance: UsageProvenance = "unknown"

    def __post_init__(self) -> None:
        if self.provenance not in _PROVENANCE:
            raise ValueError(f"Unknown usage provenance: {self.provenance!r}")
        if self.value is None:
            if self.provenance != "unknown":
                raise ValueError("missing usage values must have unknown provenance")
            return
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise TypeError("usage values must be numeric or None")
        if not math.isfinite(float(self.value)) or self.value < 0:
            raise ValueError("usage values must be finite and non-negative")
        if self.provenance == "unknown":
            raise ValueError("known usage values require known provenance")

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "provenance": self.provenance}


UNKNOWN_USAGE = UsageValue(None, "unknown")


@dataclass(frozen=True)
class ProviderTurnUsage:
    turn_index: int
    input_tokens: UsageValue = UNKNOWN_USAGE
    output_tokens: UsageValue = UNKNOWN_USAGE
    total_tokens: UsageValue = UNKNOWN_USAGE
    cached_input_tokens: UsageValue = UNKNOWN_USAGE
    reasoning_tokens: UsageValue = UNKNOWN_USAGE
    cost_usd: UsageValue = UNKNOWN_USAGE
    provider_quota: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.turn_index, bool)
            or not isinstance(self.turn_index, int)
            or self.turn_index < 1
        ):
            raise ValueError("turn_index must be a positive integer")
        for name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
        ):
            measurement = getattr(self, name)
            if not isinstance(measurement, UsageValue):
                raise TypeError(f"{name} must be a UsageValue")
            if measurement.value is not None and not isinstance(measurement.value, int):
                raise TypeError(f"{name} must contain an integer token count")
            if measurement.provenance not in _TOKEN_PROVENANCE:
                raise ValueError(f"invalid token provenance for {name}")
        if not isinstance(self.cost_usd, UsageValue):
            raise TypeError("cost_usd must be a UsageValue")
        if self.cost_usd.provenance not in _COST_PROVENANCE:
            raise ValueError("invalid cost provenance")
        if self.provider_quota is not None:
            if not isinstance(self.provider_quota, Mapping):
                raise TypeError("provider_quota must be a mapping or None")
            object.__setattr__(self, "provider_quota", _freeze(self.provider_quota))

    @staticmethod
    def _provider_value(value: int | None) -> UsageValue:
        return UsageValue(value, "provider") if value is not None else UNKNOWN_USAGE

    @classmethod
    def from_provider(
        cls,
        *,
        turn_index: int,
        total: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        cost_usd: int | float | None = None,
        cost_provenance: UsageProvenance = "actual",
        provider_quota: Mapping[str, Any] | None = None,
    ) -> ProviderTurnUsage:
        total_value = cls._provider_value(total)
        if total is None and input_tokens is not None and output_tokens is not None:
            total_value = UsageValue(input_tokens + output_tokens, "derived")
        cost_value = (
            UsageValue(cost_usd, cost_provenance)
            if cost_usd is not None
            else UNKNOWN_USAGE
        )
        return cls(
            turn_index=turn_index,
            input_tokens=cls._provider_value(input_tokens),
            output_tokens=cls._provider_value(output_tokens),
            total_tokens=total_value,
            cached_input_tokens=cls._provider_value(cached_input_tokens),
            reasoning_tokens=cls._provider_value(reasoning_tokens),
            cost_usd=cost_value,
            provider_quota=provider_quota,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "turnIndex": self.turn_index,
            "inputTokens": self.input_tokens.to_dict(),
            "outputTokens": self.output_tokens.to_dict(),
            "totalTokens": self.total_tokens.to_dict(),
            "cachedInputTokens": self.cached_input_tokens.to_dict(),
            "reasoningTokens": self.reasoning_tokens.to_dict(),
            "costUsd": self.cost_usd.to_dict(),
            "providerQuota": _thaw(self.provider_quota),
        }


def _summarize(turns: tuple[ProviderTurnUsage, ...], field: str) -> dict[str, Any]:
    measurements = [getattr(turn, field) for turn in turns]
    known = [item for item in measurements if item.value is not None]
    coverage = len(known) / len(measurements) if measurements else 0.0
    if not known:
        return {"value": None, "provenance": "unknown", "coverage": coverage}
    provenances = {item.provenance for item in known}
    provenance = (
        next(iter(provenances))
        if coverage == 1.0 and len(provenances) == 1
        else "mixed"
    )
    return {
        "value": sum(item.value for item in known if item.value is not None),
        "provenance": provenance,
        "coverage": round(coverage, 6),
    }


@dataclass(frozen=True)
class UsageReport:
    schema_version: int
    run_id: str
    model_id: str
    provider_id: str
    turns: tuple[ProviderTurnUsage, ...]
    peak_input_tokens: int | None = None
    compaction_count: int = 0
    compacted_observation_tokens: int | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise ValueError("schema_version must be positive")
        if not str(self.run_id).strip():
            raise ValueError("run_id is required")
        if not str(self.model_id).strip():
            raise ValueError("model_id is required")
        if not str(self.provider_id).strip():
            raise ValueError("provider_id is required")
        if not isinstance(self.turns, tuple):
            object.__setattr__(self, "turns", tuple(self.turns))
        if not all(isinstance(turn, ProviderTurnUsage) for turn in self.turns):
            raise TypeError("turns must contain ProviderTurnUsage values")
        indexes = [turn.turn_index for turn in self.turns]
        if len(set(indexes)) != len(indexes):
            raise ValueError("turn indexes must be unique within a usage report")
        for name in ("peak_input_tokens", "compacted_observation_tokens"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if (
            isinstance(self.compaction_count, bool)
            or not isinstance(self.compaction_count, int)
            or self.compaction_count < 0
        ):
            raise ValueError("compaction_count must be a non-negative integer")

    @property
    def canonical_model_id(self) -> str:
        return canonical_usage_model_id(self.model_id)

    @property
    def input_amplification(self) -> float | None:
        if not self.turns:
            return None
        inputs: list[int] = []
        for turn in self.turns:
            value = turn.input_tokens
            if value.provenance != "provider" or not isinstance(value.value, int):
                return None
            inputs.append(value.value)
        peak = max(inputs, default=0)
        if peak <= 0:
            return None
        return round(sum(inputs) / peak, 6)

    @property
    def latest_provider_quota(self) -> dict[str, Any] | None:
        for turn in reversed(self.turns):
            if turn.provider_quota is not None:
                return _thaw(turn.provider_quota)
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "inputTokens": _summarize(self.turns, "input_tokens"),
            "outputTokens": _summarize(self.turns, "output_tokens"),
            "totalTokens": _summarize(self.turns, "total_tokens"),
            "cachedInputTokens": _summarize(self.turns, "cached_input_tokens"),
            "reasoningTokens": _summarize(self.turns, "reasoning_tokens"),
            "costUsd": _summarize(self.turns, "cost_usd"),
        }

    @classmethod
    def aggregate(cls, reports: Iterable[UsageReport]) -> UsageReport:
        received = tuple(reports)
        values = tuple(
            report
            for index, report in enumerate(received)
            if report not in received[:index]
        )
        if not values:
            raise ValueError("at least one usage report is required")
        first = values[0]
        for report in values[1:]:
            if report.run_id != first.run_id:
                raise ValueError("usage reports must belong to the same run")
            if (
                report.canonical_model_id != first.canonical_model_id
                or report.provider_id != first.provider_id
            ):
                raise ValueError("usage reports must use the same model and provider")
        compacted = [
            report.compacted_observation_tokens
            for report in values
            if report.compacted_observation_tokens is not None
        ]
        peaks = [
            report.peak_input_tokens
            for report in values
            if report.peak_input_tokens is not None
        ]
        turns_by_index: dict[int, ProviderTurnUsage] = {}
        for report in values:
            for turn in report.turns:
                previous = turns_by_index.get(turn.turn_index)
                if previous is not None and previous != turn:
                    raise ValueError(f"conflicting usage for turn {turn.turn_index}")
                turns_by_index[turn.turn_index] = turn
        return cls(
            schema_version=max(report.schema_version for report in values),
            run_id=first.run_id,
            model_id=first.model_id,
            provider_id=first.provider_id,
            turns=tuple(turns_by_index[index] for index in sorted(turns_by_index)),
            peak_input_tokens=max(peaks) if peaks else None,
            compaction_count=sum(report.compaction_count for report in values),
            compacted_observation_tokens=sum(compacted) if compacted else None,
        )

    def to_dict(self) -> dict[str, Any]:
        inputs = [
            turn.input_tokens.value
            for turn in self.turns
            if turn.input_tokens.provenance == "provider"
            and isinstance(turn.input_tokens.value, int)
        ]
        derived_peak = (
            max(inputs) if len(inputs) == len(self.turns) and inputs else None
        )
        context_efficiency: dict[str, Any] = {
            "compactions": self.compaction_count,
            "compactedObservationTokens": self.compacted_observation_tokens,
        }
        if self.input_amplification is not None:
            context_efficiency.update(
                {
                    "inputAmplification": self.input_amplification,
                    "provenance": "provider",
                }
            )
        return {
            "schemaVersion": self.schema_version,
            "runId": self.run_id,
            "modelId": self.model_id,
            "canonicalModelId": self.canonical_model_id,
            "providerId": self.provider_id,
            "turns": [turn.to_dict() for turn in self.turns],
            "aggregate": self.summary(),
            "peakInputTokens": self.peak_input_tokens or derived_peak,
            "providerQuota": self.latest_provider_quota,
            "contextEfficiency": context_efficiency,
        }

    def to_ledger_fields(self) -> dict[str, Any]:
        summary = self.summary()
        fields: dict[str, Any] = {
            "usage_schema_version": self.schema_version,
            "run_id": self.run_id,
            "model_id": self.model_id,
            "canonical_model_id": self.canonical_model_id,
            "provider_id": self.provider_id,
            "provider_turns": len(self.turns),
            "peak_input_tokens": self.to_dict()["peakInputTokens"],
            "compaction_count": self.compaction_count,
            "compacted_observation_tokens": self.compacted_observation_tokens,
            "provider_quota": self.latest_provider_quota,
            "input_amplification": self.input_amplification,
        }
        for camel, snake in (
            ("inputTokens", "input_tokens"),
            ("outputTokens", "output_tokens"),
            ("totalTokens", "total_tokens"),
            ("cachedInputTokens", "cached_input_tokens"),
            ("reasoningTokens", "reasoning_tokens"),
            ("costUsd", "cost_usd"),
        ):
            measurement = summary[camel]
            fields[snake] = measurement["value"]
            fields[f"{snake}_provenance"] = measurement["provenance"]
            fields[f"{snake}_coverage"] = measurement["coverage"]
        return fields
