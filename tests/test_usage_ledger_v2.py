"""Sequenced provider-turn usage ledger and reset-epoch contracts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from opaihub import ledger
from opaihub.atomic_io import atomic_write_text
from opaihub.ledger import (
    EVENT_MODEL_CALL,
    EVENT_MODEL_CALL_NOT_DISPATCHED,
    EVENT_MODEL_CALL_STARTED,
    EVENT_MODEL_CALL_USAGE_OBSERVED,
    EVENT_USAGE_BASELINE_RESET,
    MODEL_CALL_SCHEMA_VERSION,
    ledger_head_path,
    ledger_path,
    read_events,
    reconcile_observed_model_calls,
    record_event,
    record_model_call_finalized,
    record_model_call_not_dispatched,
    record_model_call_started,
    record_model_call_usage_observed,
    reset_usage_baseline,
    summarize_ledger,
    pending_model_call_observations,
    unresolved_model_calls,
)
from opaihub.usage_report import ProviderTurnUsage


MODEL = "account:claude:opus-4.8"
CANONICAL_MODEL = "account:claude:opus"


def _turn(index: int = 1, *, total: int = 120) -> ProviderTurnUsage:
    return ProviderTurnUsage.from_provider(
        turn_index=index,
        total=total,
        input_tokens=80,
        output_tokens=20,
        cached_input_tokens=10,
        reasoning_tokens=5,
        cost_usd=0.25,
        cost_provenance="actual",
        provider_quota={"remaining": 9},
    )


def _start(root: Path, *, call_id: str = "run-1:1", model: str = MODEL):
    return record_model_call_started(
        root,
        "private task text",
        call_id=call_id,
        run_id=call_id.rsplit(":", 1)[0],
        turn_index=int(call_id.rsplit(":", 1)[1]),
        model_id=model,
        provider_id="claude",
        model_tier="L3",
        provider_type="account",
        confirmed=True,
    )


def _head(root: Path) -> dict:
    return json.loads(ledger_head_path(root).read_text(encoding="utf-8"))


def test_every_new_event_gets_a_strict_same_second_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ledger, "_now_iso", lambda: "2030-01-01T00:00:00+00:00")

    first = record_event(tmp_path, "route_decision", task="one")
    second = record_event(tmp_path, "cache_lookup", task="two")
    third = record_event(tmp_path, "context_compaction", task="three")

    assert [
        first["ledger_sequence"],
        second["ledger_sequence"],
        third["ledger_sequence"],
    ] == [1, 2, 3]
    assert {event["created_at"] for event in read_events(tmp_path)} == {
        "2030-01-01T00:00:00+00:00"
    }
    assert _head(tmp_path)["last_sequence"] == 3


def test_warm_append_replays_only_the_tail_not_the_full_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_event(tmp_path, "route_decision", task="one")

    def unexpected_full_read(_root: Path, limit: int | None = None) -> list[dict]:
        del limit
        pytest.fail("a valid sequence head should not trigger a full ledger replay")

    monkeypatch.setattr(ledger, "read_events", unexpected_full_read)

    event = record_event(tmp_path, "route_decision", task="two")

    assert event["ledger_sequence"] == 2
    assert _head(tmp_path)["ledger_offset"] == ledger_path(tmp_path).stat().st_size


def test_non_call_events_do_not_create_or_sync_the_call_index(tmp_path: Path) -> None:
    record_event(tmp_path, "route_decision", task="one")
    record_event(tmp_path, "cache_lookup", task="two")

    assert not ledger.ledger_index_path(tmp_path).exists()


def test_legacy_unsequenced_events_remain_readable(tmp_path: Path) -> None:
    path = ledger_path(tmp_path)
    path.parent.mkdir(parents=True)
    legacy = {"event_type": EVENT_MODEL_CALL, "tokens": 7, "model_id": MODEL}
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    current = record_event(tmp_path, "route_decision", task="new")

    assert read_events(tmp_path)[0] == legacy
    assert current["ledger_sequence"] == 1


def test_stale_corrupt_and_ahead_heads_recover_from_the_jsonl_tail(
    tmp_path: Path,
) -> None:
    record_event(tmp_path, "route_decision", task="one")
    old_head = ledger_head_path(tmp_path).read_text(encoding="utf-8")
    record_event(tmp_path, "route_decision", task="two")

    atomic_write_text(ledger_head_path(tmp_path), old_head)
    assert (
        record_event(tmp_path, "route_decision", task="three")["ledger_sequence"] == 3
    )

    atomic_write_text(ledger_head_path(tmp_path), "{not-json")
    assert record_event(tmp_path, "route_decision", task="four")["ledger_sequence"] == 4

    ahead = _head(tmp_path)
    ahead["last_sequence"] = 999
    atomic_write_text(ledger_head_path(tmp_path), json.dumps(ahead))
    assert record_event(tmp_path, "route_decision", task="five")["ledger_sequence"] == 5
    assert [event["ledger_sequence"] for event in read_events(tmp_path)] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert _head(tmp_path)["last_sequence"] == 5


def test_valid_looking_head_state_corruption_rebuilds_active_calls(
    tmp_path: Path,
) -> None:
    _start(tmp_path)
    damaged = _head(tmp_path)
    damaged["active_calls"] = {}
    atomic_write_text(ledger_head_path(tmp_path), json.dumps(damaged))

    finalized = record_model_call_finalized(
        tmp_path, "task", call_id="run-1:1", usage=_turn()
    )

    assert finalized["ledger_sequence"] == 2
    assert [event["event_type"] for event in read_events(tmp_path)] == [
        EVENT_MODEL_CALL_STARTED,
        EVENT_MODEL_CALL,
    ]


def test_head_offset_inside_a_record_cannot_duplicate_a_sequence(
    tmp_path: Path,
) -> None:
    record_event(tmp_path, "route_decision", task="one")
    head_after_one = _head(tmp_path)
    record_event(tmp_path, "route_decision", task="two")
    lines = ledger_path(tmp_path).read_bytes().splitlines(keepends=True)
    head_after_one["ledger_offset"] = len(lines[0]) + max(1, len(lines[1]) // 2)
    atomic_write_text(ledger_head_path(tmp_path), json.dumps(head_after_one))

    third = record_event(tmp_path, "route_decision", task="three")

    assert third["ledger_sequence"] == 3
    assert [event["ledger_sequence"] for event in read_events(tmp_path)] == [1, 2, 3]


def test_complete_json_tail_without_newline_cannot_duplicate_a_sequence(
    tmp_path: Path,
) -> None:
    record_event(tmp_path, "route_decision", task="one")
    torn_tail = {
        "created_at": "2030-01-01T00:00:00+00:00",
        "event_type": "route_decision",
        "ledger_sequence": 2,
        "task_hash": "crash-tail",
    }
    with ledger_path(tmp_path).open("ab") as handle:
        handle.write(json.dumps(torn_tail, sort_keys=True).encode("utf-8"))

    third = record_event(tmp_path, "route_decision", task="three")

    assert third["ledger_sequence"] == 3
    assert [event["ledger_sequence"] for event in read_events(tmp_path)] == [1, 2, 3]


def test_start_and_finalize_are_idempotent(tmp_path: Path) -> None:
    started = _start(tmp_path)
    repeated_start = _start(tmp_path)
    finalized = record_model_call_finalized(
        tmp_path, "private task text", call_id="run-1:1", usage=_turn()
    )
    repeated_final = record_model_call_finalized(
        tmp_path, "different retry text", call_id="run-1:1", usage=_turn(total=999)
    )

    assert repeated_start == started
    assert repeated_final == finalized
    assert [event["event_type"] for event in read_events(tmp_path)] == [
        EVENT_MODEL_CALL_STARTED,
        EVENT_MODEL_CALL,
    ]
    assert started["schema_version"] == MODEL_CALL_SCHEMA_VERSION
    assert finalized["schema_version"] == MODEL_CALL_SCHEMA_VERSION
    assert finalized["total_tokens"] == 120
    head = _head(tmp_path)
    assert head["active_calls"] == {}
    assert "finalized_calls" not in head


def test_usage_observation_is_a_durable_outbox_until_finalized(tmp_path: Path) -> None:
    _start(tmp_path)
    observed = record_model_call_usage_observed(
        tmp_path,
        "private task text",
        call_id="run-1:1",
        usage=_turn(),
    )

    assert observed["event_type"] == EVENT_MODEL_CALL_USAGE_OBSERVED
    assert observed["cost_usd"] == 0.25
    assert pending_model_call_observations(tmp_path) == [observed]
    assert len(unresolved_model_calls(tmp_path)) == 1

    [finalized] = reconcile_observed_model_calls(tmp_path)

    assert finalized["event_type"] == EVENT_MODEL_CALL
    assert finalized["cost_usd"] == 0.25
    assert pending_model_call_observations(tmp_path) == []
    assert unresolved_model_calls(tmp_path) == []
    assert summarize_ledger(tmp_path)["model_call_count"] == 1
    assert reconcile_observed_model_calls(tmp_path) == []
    assert [event["event_type"] for event in read_events(tmp_path)] == [
        EVENT_MODEL_CALL_STARTED,
        EVENT_MODEL_CALL_USAGE_OBSERVED,
        EVENT_MODEL_CALL,
    ]


def test_replayed_usage_observation_cannot_double_charge(tmp_path: Path) -> None:
    _start(tmp_path)
    first = record_model_call_usage_observed(
        tmp_path, "task", call_id="run-1:1", usage=_turn()
    )
    repeated = record_model_call_usage_observed(
        tmp_path, "different task", call_id="run-1:1", usage=_turn(total=999)
    )

    assert repeated == first
    assert len(pending_model_call_observations(tmp_path)) == 1
    assert len(reconcile_observed_model_calls(tmp_path)) == 1
    assert reconcile_observed_model_calls(tmp_path) == []
    assert summarize_ledger(tmp_path)["model_call_count"] == 1


def test_malformed_observation_is_not_promoted_to_authoritative_cost(
    tmp_path: Path,
) -> None:
    _start(tmp_path)
    record_event(
        tmp_path,
        EVENT_MODEL_CALL_USAGE_OBSERVED,
        task="task",
        call_id="run-1:1",
        turn_index=1,
        cost_usd="not-a-number",
        cost_usd_provenance="actual",
    )

    assert reconcile_observed_model_calls(tmp_path) == []
    assert len(pending_model_call_observations(tmp_path)) == 1
    assert len(unresolved_model_calls(tmp_path)) == 1
    assert summarize_ledger(tmp_path)["model_call_count"] == 0


def test_proven_non_dispatch_closes_without_counting_a_model_call(
    tmp_path: Path,
) -> None:
    _start(tmp_path)

    closed = record_model_call_not_dispatched(
        tmp_path,
        "task",
        call_id="run-1:1",
        reason_code="AUTH_INVALID",
    )
    repeated = record_model_call_not_dispatched(
        tmp_path,
        "different task",
        call_id="run-1:1",
        reason_code="CONFIG_INVALID",
    )

    assert repeated == closed
    assert closed["event_type"] == EVENT_MODEL_CALL_NOT_DISPATCHED
    assert closed["dispatch_proof"] == "not_dispatched"
    assert unresolved_model_calls(tmp_path) == []
    assert summarize_ledger(tmp_path)["model_call_count"] == 0
    assert [event["event_type"] for event in read_events(tmp_path)] == [
        EVENT_MODEL_CALL_STARTED,
        EVENT_MODEL_CALL_NOT_DISPATCHED,
    ]


def test_ambiguous_failure_cannot_be_recorded_as_non_dispatch(tmp_path: Path) -> None:
    _start(tmp_path)

    with pytest.raises(ValueError, match="does not prove non-dispatch"):
        record_model_call_not_dispatched(
            tmp_path,
            "task",
            call_id="run-1:1",
            reason_code="PROVIDER_TIMEOUT",
        )

    assert len(unresolved_model_calls(tmp_path)) == 1


def test_finalized_usage_keeps_measured_cost_and_tier_estimate_separate(
    tmp_path: Path,
) -> None:
    _start(tmp_path)
    usage = ProviderTurnUsage.from_provider(
        turn_index=1,
        total=100,
        cost_usd=0.0,
        cost_provenance="actual",
        tier_estimate_usd=0.25,
        estimated_actual_usd=0.25,
        estimated_actual_provenance="estimated",
    )

    event = record_model_call_finalized(
        tmp_path,
        "task",
        call_id="run-1:1",
        usage=usage,
    )

    assert event["cost_usd"] == 0.0
    assert event["cost_usd_provenance"] == "actual"
    assert event["tier_estimate_usd"] == 0.25
    assert event["tier_estimate_usd_provenance"] == "estimated"
    assert event["estimated_actual_usd"] == 0.25
    assert event["estimated_actual_usd_provenance"] == "estimated"


def test_repeated_start_with_conflicting_identity_fails_closed(tmp_path: Path) -> None:
    _start(tmp_path)

    with pytest.raises(ValueError, match="call_id already belongs"):
        _start(tmp_path, model="account:claude:sonnet")

    assert len(read_events(tmp_path)) == 1


def test_process_death_leaves_an_explicit_unresolved_start(tmp_path: Path) -> None:
    started = _start(tmp_path)

    assert _head(tmp_path)["active_calls"] == {"run-1:1": started}
    assert "finalized_calls" not in _head(tmp_path)


def test_reset_during_inflight_call_does_not_readd_old_usage(tmp_path: Path) -> None:
    started = _start(tmp_path)
    reset = reset_usage_baseline(tmp_path, MODEL)
    finalized = record_model_call_finalized(
        tmp_path, "task", call_id="run-1:1", usage=_turn()
    )

    assert started["usage_epoch"] < reset["usage_epoch"]
    assert finalized["usage_epoch"] == started["usage_epoch"]
    assert reset["event_type"] == EVENT_USAGE_BASELINE_RESET
    assert [event["event_type"] for event in read_events(tmp_path)] == [
        EVENT_MODEL_CALL_STARTED,
        EVENT_USAGE_BASELINE_RESET,
        EVENT_MODEL_CALL,
    ]


def test_reset_is_canonical_model_scoped_and_append_only(tmp_path: Path) -> None:
    first = _start(tmp_path, call_id="run-1:1")
    reset = reset_usage_baseline(tmp_path, "account:claude:claude-opus")
    after = _start(tmp_path, call_id="run-2:2", model="account:claude:opus")
    other = _start(
        tmp_path,
        call_id="run-3:3",
        model="account:claude:sonnet",
    )

    assert first["canonical_model_id"] == CANONICAL_MODEL
    assert reset["canonical_model_id"] == CANONICAL_MODEL
    assert reset["previous_usage_epoch"] == 0
    assert after["usage_epoch"] == 1
    assert other["usage_epoch"] == 0
    assert len(read_events(tmp_path)) == 4
    assert _head(tmp_path)["model_epochs"][CANONICAL_MODEL]["usage_epoch"] == 1
    assert _head(tmp_path)["model_epochs"]["account:claude:sonnet"]["usage_epoch"] == 0


def test_handled_failure_can_finalize_unknown_usage_without_inventing_zero(
    tmp_path: Path,
) -> None:
    _start(tmp_path)
    unknown = ProviderTurnUsage(turn_index=1)

    event = record_model_call_finalized(
        tmp_path, "task", call_id="run-1:1", usage=unknown
    )

    assert event["input_tokens"] is None
    assert event["output_tokens"] is None
    assert event["total_tokens"] is None
    assert event["total_tokens_provenance"] == "unknown"
    assert event["cost_usd"] is None


def test_finalize_requires_a_matching_start_and_turn(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown call_id"):
        record_model_call_finalized(
            tmp_path, "task", call_id="missing:1", usage=_turn()
        )
    _start(tmp_path)
    with pytest.raises(ValueError, match="turn_index"):
        record_model_call_finalized(
            tmp_path, "task", call_id="run-1:1", usage=_turn(index=2)
        )


def test_append_failure_preserves_ledger_and_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_event(tmp_path, "route_decision", task="prior")
    before_ledger = ledger_path(tmp_path).read_bytes()
    before_head = ledger_head_path(tmp_path).read_bytes()
    real_append = ledger._append_event_line

    def fail_append(_path: Path, _event: dict) -> None:
        raise OSError("injected append failure")

    monkeypatch.setattr(ledger, "_append_event_line", fail_append)
    with pytest.raises(OSError, match="append failure"):
        record_event(tmp_path, "route_decision", task="not-written")
    assert ledger_path(tmp_path).read_bytes() == before_ledger
    assert ledger_head_path(tmp_path).read_bytes() == before_head

    monkeypatch.setattr(ledger, "_append_event_line", real_append)
    assert record_event(tmp_path, "route_decision", task="next")["ledger_sequence"] == 2


def test_head_replace_failure_is_recovered_from_durable_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_event(tmp_path, "route_decision", task="prior")
    real_atomic_write = ledger.atomic_write_text

    def fail_head(_path: Path, _text: str, **_kwargs: object) -> None:
        raise OSError("injected head failure")

    monkeypatch.setattr(ledger, "atomic_write_text", fail_head)
    with pytest.raises(OSError, match="head failure"):
        record_event(tmp_path, "route_decision", task="durable-tail")
    assert [event["ledger_sequence"] for event in read_events(tmp_path)] == [1, 2]

    monkeypatch.setattr(ledger, "atomic_write_text", real_atomic_write)
    assert (
        record_event(tmp_path, "route_decision", task="after-recovery")[
            "ledger_sequence"
        ]
        == 3
    )
    assert _head(tmp_path)["last_sequence"] == 3


def test_start_retry_after_head_failure_does_not_duplicate_dispatch_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_atomic_write = ledger.atomic_write_text

    def fail_head(_path: Path, _text: str, **_kwargs: object) -> None:
        raise OSError("injected head failure")

    monkeypatch.setattr(ledger, "atomic_write_text", fail_head)
    with pytest.raises(OSError, match="head failure"):
        _start(tmp_path)
    monkeypatch.setattr(ledger, "atomic_write_text", real_atomic_write)

    recovered = _start(tmp_path)

    assert recovered["ledger_sequence"] == 1
    assert len(read_events(tmp_path)) == 1
    assert _head(tmp_path)["active_calls"]["run-1:1"] == recovered


def test_completed_call_index_is_rebuildable_and_keeps_head_bounded(
    tmp_path: Path,
) -> None:
    for index in range(1, 41):
        call_id = f"run-{index}:{index}"
        _start(tmp_path, call_id=call_id)
        record_model_call_finalized(
            tmp_path,
            "task",
            call_id=call_id,
            usage=_turn(index=index),
        )

    head = _head(tmp_path)
    assert head["active_calls"] == {}
    assert "finalized_calls" not in head
    assert ledger_head_path(tmp_path).stat().st_size < 32_000
    index_path = ledger.ledger_index_path(tmp_path)
    assert index_path.exists()

    index_path.unlink()
    repeated = record_model_call_finalized(
        tmp_path,
        "retry",
        call_id="run-40:40",
        usage=_turn(index=40, total=999),
    )

    assert repeated["total_tokens"] == 120
    assert len(read_events(tmp_path)) == 80


def test_logically_damaged_call_index_rebuilds_before_idempotency_lookup(
    tmp_path: Path,
) -> None:
    started = _start(tmp_path)
    finalized = record_model_call_finalized(
        tmp_path,
        "task",
        call_id="run-1:1",
        usage=_turn(),
    )
    with sqlite3.connect(ledger.ledger_index_path(tmp_path)) as connection:
        connection.execute("DELETE FROM calls WHERE call_id = ?", ("run-1:1",))

    assert _start(tmp_path) == started
    assert (
        record_model_call_finalized(
            tmp_path,
            "retry",
            call_id="run-1:1",
            usage=_turn(total=999),
        )
        == finalized
    )
    assert len(read_events(tmp_path)) == 2


def test_oversized_call_id_is_rejected_before_lookup_or_persistence(
    tmp_path: Path,
) -> None:
    call_id = f"{'r' * 4_095}:1"

    with pytest.raises(ValueError, match="call_id must be at most 4096 characters"):
        _start(tmp_path, call_id=call_id)

    assert not ledger_path(tmp_path).exists()
    assert not ledger.ledger_index_path(tmp_path).exists()


def test_secret_like_call_id_is_rejected_instead_of_redacted_into_a_collision(
    tmp_path: Path,
) -> None:
    call_id = f"sk-{'a' * 24}:1"

    with pytest.raises(
        ValueError, match="call_id must not contain secret-like content"
    ):
        _start(tmp_path, call_id=call_id)

    assert not ledger_path(tmp_path).exists()
    assert not ledger.ledger_index_path(tmp_path).exists()


def test_persisted_nested_strings_are_bounded(tmp_path: Path) -> None:
    _start(tmp_path)
    usage = ProviderTurnUsage.from_provider(
        turn_index=1,
        total=1,
        provider_quota={"remaining": "x" * 50_000},
    )

    event = record_model_call_finalized(
        tmp_path, "task", call_id="run-1:1", usage=usage
    )

    assert 0 < len(event["provider_quota"]["remaining"]) <= 4_096
