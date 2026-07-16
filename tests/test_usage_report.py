"""Provider-turn usage truth and aggregation invariants."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from opaihub.usage_report import ProviderTurnUsage, UsageReport, UsageValue


MODEL = "account:claude:opus-4.8"


def _report(*turns: ProviderTurnUsage, peak: int | None = None) -> UsageReport:
    return UsageReport(
        schema_version=1,
        run_id="run-1",
        model_id=MODEL,
        provider_id="claude",
        turns=tuple(turns),
        peak_input_tokens=peak,
    )


def test_provider_total_is_not_replaced_by_components() -> None:
    usage = ProviderTurnUsage.from_provider(
        turn_index=1, total=120, input_tokens=80, output_tokens=20
    )

    assert usage.total_tokens == UsageValue(120, "provider")


def test_total_is_derived_only_when_both_components_exist() -> None:
    complete = ProviderTurnUsage.from_provider(
        turn_index=1, input_tokens=80, output_tokens=20
    )
    partial = ProviderTurnUsage.from_provider(turn_index=2, input_tokens=80)

    assert complete.total_tokens == UsageValue(100, "derived")
    assert partial.total_tokens == UsageValue(None, "unknown")
    assert partial.output_tokens == UsageValue(None, "unknown")


def test_total_only_usage_keeps_unknown_breakdown_instead_of_zero() -> None:
    usage = ProviderTurnUsage.from_provider(turn_index=1, total=42)

    assert usage.total_tokens == UsageValue(42, "provider")
    assert usage.input_tokens.value is None
    assert usage.output_tokens.value is None


def test_cache_reasoning_quota_and_cost_are_preserved_independently() -> None:
    usage = ProviderTurnUsage.from_provider(
        turn_index=1,
        total=140,
        input_tokens=100,
        output_tokens=40,
        cached_input_tokens=70,
        reasoning_tokens=9,
        cost_usd=0.012,
        cost_provenance="actual",
        provider_quota={"remaining": 500, "resetAt": "2030-01-01T00:00:00Z"},
    )

    assert usage.cached_input_tokens == UsageValue(70, "provider")
    assert usage.reasoning_tokens == UsageValue(9, "provider")
    assert usage.cost_usd == UsageValue(0.012, "actual")
    assert usage.provider_quota == {
        "remaining": 500,
        "resetAt": "2030-01-01T00:00:00Z",
    }


def test_usage_values_reject_booleans_negative_values_and_false_precision() -> None:
    with pytest.raises((TypeError, ValueError)):
        UsageValue(True, "provider")
    with pytest.raises(ValueError):
        UsageValue(-1, "provider")
    with pytest.raises(ValueError):
        UsageValue(None, "provider")
    with pytest.raises(ValueError):
        UsageValue(1, "unknown")


def test_turn_index_and_schema_version_must_be_real_integers() -> None:
    with pytest.raises(ValueError, match="turn_index"):
        ProviderTurnUsage.from_provider(turn_index=1.5, total=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="schema_version"):
        UsageReport(
            schema_version=1.5,  # type: ignore[arg-type]
            run_id="run-1",
            model_id=MODEL,
            provider_id="claude",
            turns=(),
        )


def test_usage_contract_is_immutable() -> None:
    usage = ProviderTurnUsage.from_provider(turn_index=1, total=1)

    with pytest.raises(FrozenInstanceError):
        usage.turn_index = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        usage.provider_quota["x"] = 1  # type: ignore[index]


@pytest.mark.parametrize("mutable", [{"value"}, bytearray(b"value")])
def test_provider_quota_rejects_non_json_mutable_leaves(mutable: object) -> None:
    with pytest.raises(TypeError, match="JSON-compatible"):
        ProviderTurnUsage.from_provider(
            turn_index=1,
            total=1,
            provider_quota={"unsafe": mutable},
        )


def test_token_and_cost_metrics_reject_each_others_provenance_domains() -> None:
    with pytest.raises(ValueError, match="token provenance"):
        ProviderTurnUsage(
            turn_index=1,
            total_tokens=UsageValue(10, "actual"),
        )
    with pytest.raises(ValueError, match="cost provenance"):
        ProviderTurnUsage.from_provider(
            turn_index=1,
            total=10,
            cost_usd=0.01,
            cost_provenance="provider",
        )


def test_report_aggregate_preserves_mixed_precision_and_coverage() -> None:
    provider = ProviderTurnUsage.from_provider(
        turn_index=1, total=120, input_tokens=80, output_tokens=20
    )
    estimated = ProviderTurnUsage(
        turn_index=2,
        input_tokens=UsageValue(30, "estimated"),
        output_tokens=UsageValue(None, "unknown"),
        total_tokens=UsageValue(30, "estimated"),
        cached_input_tokens=UsageValue(None, "unknown"),
        reasoning_tokens=UsageValue(None, "unknown"),
        cost_usd=UsageValue(None, "unknown"),
    )

    summary = _report(provider, estimated).summary()

    assert summary["inputTokens"] == {
        "value": 110,
        "provenance": "mixed",
        "coverage": 1.0,
    }
    assert summary["outputTokens"] == {
        "value": 20,
        "provenance": "mixed",
        "coverage": 0.5,
    }
    assert summary["totalTokens"]["value"] == 150
    assert summary["totalTokens"]["provenance"] == "mixed"


def test_input_amplification_requires_complete_provider_input_coverage() -> None:
    complete = _report(
        ProviderTurnUsage.from_provider(
            turn_index=1, input_tokens=100, output_tokens=1
        ),
        ProviderTurnUsage.from_provider(turn_index=2, input_tokens=60, output_tokens=1),
    )
    incomplete = _report(
        ProviderTurnUsage.from_provider(
            turn_index=1, input_tokens=100, output_tokens=1
        ),
        ProviderTurnUsage(
            turn_index=2,
            input_tokens=UsageValue(60, "estimated"),
            output_tokens=UsageValue(1, "provider"),
            total_tokens=UsageValue(61, "estimated"),
            cached_input_tokens=UsageValue(None, "unknown"),
            reasoning_tokens=UsageValue(None, "unknown"),
            cost_usd=UsageValue(None, "unknown"),
        ),
    )

    assert complete.input_amplification == 1.6
    assert incomplete.input_amplification is None


def test_serialization_preserves_raw_model_id_and_adds_canonical_id() -> None:
    report = _report(ProviderTurnUsage.from_provider(turn_index=1, total=12))

    serialized = report.to_dict()
    ledger = report.to_ledger_fields()
    assert serialized["modelId"] == MODEL
    assert serialized["canonicalModelId"] == "account:claude:opus"
    assert ledger["model_id"] == MODEL
    assert ledger["canonical_model_id"] == "account:claude:opus"


def test_aggregate_combines_reports_without_losing_turns_or_metadata() -> None:
    first = _report(ProviderTurnUsage.from_provider(turn_index=1, total=10))
    second = UsageReport(
        schema_version=1,
        run_id="run-1",
        model_id=MODEL,
        provider_id="claude",
        turns=(ProviderTurnUsage.from_provider(turn_index=2, total=20),),
        compaction_count=2,
        compacted_observation_tokens=300,
    )

    combined = UsageReport.aggregate((first, second))

    assert len(combined.turns) == 2
    assert combined.summary()["totalTokens"]["value"] == 30
    assert combined.compaction_count == 2
    assert combined.compacted_observation_tokens == 300


def test_aggregate_deduplicates_identical_turns_instead_of_double_counting() -> None:
    turn = ProviderTurnUsage.from_provider(
        turn_index=1,
        total=10,
        cost_usd=0.5,
        cost_provenance="actual",
    )
    first = _report(turn)
    repeated = _report(turn)

    combined = UsageReport.aggregate((first, repeated))

    assert len(combined.turns) == 1
    assert combined.summary()["totalTokens"]["value"] == 10
    assert combined.summary()["costUsd"]["value"] == 0.5


def test_duplicate_turn_index_with_conflicting_usage_is_rejected() -> None:
    first = _report(ProviderTurnUsage.from_provider(turn_index=1, total=10))
    conflicting = _report(ProviderTurnUsage.from_provider(turn_index=1, total=11))

    with pytest.raises(ValueError, match="conflicting usage for turn 1"):
        UsageReport.aggregate((first, conflicting))


def test_direct_report_rejects_duplicate_turn_indexes() -> None:
    turn = ProviderTurnUsage.from_provider(turn_index=1, total=10)

    with pytest.raises(ValueError, match="turn indexes must be unique"):
        _report(turn, turn)


def test_aggregate_rejects_different_runs_or_models() -> None:
    first = _report(ProviderTurnUsage.from_provider(turn_index=1, total=10))
    other = UsageReport(
        schema_version=1,
        run_id="another-run",
        model_id=MODEL,
        provider_id="claude",
        turns=(),
    )

    with pytest.raises(ValueError, match="same run"):
        UsageReport.aggregate((first, other))
