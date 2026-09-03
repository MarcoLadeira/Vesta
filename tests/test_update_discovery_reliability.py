"""Discovery reliability: cadence, freshness truth, triggers and migration.

The defect these pin is not "the updater is broken" -- the install machinery
worked. It is that *asking* was conflated with *checking*. The front end ticked
every fifteen minutes, an ordinary check inside the freshness window returned
the persisted answer, and the operation recorded that as though the update
source had been contacted. A source checkout could therefore sit hours behind
origin/main while every surface agreed there was nothing to update.

Reproduced before the fix, as a controlled sequence:

    first check     : available | remote calls: 1
    after 3h59m     : available | remote calls: 2
    after 16 ticks  : remote calls: 2      <- sixteen ticks, zero remote calls
    forced          : remote calls: 3
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_update_service import NOW, _installed, _service  # noqa: E402

from opai.update.cadence import (  # noqa: E402
    CADENCE_POLICY_VERSION,
    LEGACY_INTERVAL_SECONDS,
    MINIMUM_INTERVAL_SECONDS,
    discovery_interval_seconds,
)
from opai.update.models import (  # noqa: E402
    InstallType,
    UpdatePolicy,
    UpdateState,
)
from opai.update.scheduler import (  # noqa: E402
    TRIGGER_NETWORK_RESTORED,
    TRIGGER_PERIODIC,
    TRIGGER_RESUME,
    TRIGGER_STARTUP,
    UpdateScheduler,
)
from opai.update.storage import UpdateStore, UpdaterPaths  # noqa: E402


def _clocked(tmp_path: Path, **kwargs):
    """A service and scheduler sharing one movable clock."""

    service, fetcher, downloader, adapter = _service(tmp_path, **kwargs)
    clock = {"t": NOW}
    service._now = lambda: clock["t"]
    scheduler = UpdateScheduler(service, now=lambda: clock["t"])
    return service, scheduler, fetcher, clock


def _advance(scheduler, clock, seconds: int, step: int = 120) -> None:
    """Let time pass the way it actually passes: with the heartbeat running.

    Jumping the clock and ticking once is not the same experiment -- the
    scheduler reads a large gap between ticks as the machine having slept, and
    it is right to. Tests about *cadence* have to keep the heartbeat going.
    """

    elapsed = 0
    while elapsed < seconds:
        clock["t"] += timedelta(seconds=step)
        elapsed += step
        scheduler.tick()


# --------------------------------------------------------------------------
# The reproduction, kept as a regression test.
# --------------------------------------------------------------------------


def test_repeated_ticks_inside_the_window_reach_the_update_source_once(tmp_path: Path):
    # The defect, stated as a test: many ticks, one remote call -- and the tick
    # that is answered from cache must say so rather than pass itself off as a
    # fresh result.
    service, scheduler, fetcher, clock = _clocked(
        tmp_path, policy=UpdatePolicy(rollout_cohort=42)
    )
    scheduler.tick()
    assert len(fetcher.calls) == 1

    for _ in range(16):
        clock["t"] += timedelta(seconds=30)
        operation = scheduler.tick()

    assert len(fetcher.calls) == 1, "ticks inside the window must not refetch"
    assert operation.result_from_cache is True


def test_a_cached_tick_never_moves_the_remote_clock(tmp_path: Path):
    # "Last checked remotely" is the one timestamp a user reads to decide
    # whether to trust the state. It may only move when the update source was
    # actually contacted.
    service, scheduler, fetcher, clock = _clocked(
        tmp_path, policy=UpdatePolicy(rollout_cohort=42)
    )
    fresh = scheduler.tick()
    assert fresh.remote_checked_at
    assert fresh.result_from_cache is False

    clock["t"] += timedelta(seconds=30)
    cached = scheduler.tick()

    assert cached.remote_checked_at == fresh.remote_checked_at
    assert cached.last_successful_check_at == fresh.last_successful_check_at
    assert cached.result_from_cache is True
    # The attempt is still recorded: "the scheduler is dead" and "the scheduler
    # ran and correctly did nothing" are different bugs.
    assert cached.last_check_at


# --------------------------------------------------------------------------
# Cadence.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "install_type,channel,expected",
    [
        (InstallType.SOURCE_CHECKOUT, "stable", 10 * 60),
        (InstallType.SOURCE_CHECKOUT, "alpha", 10 * 60),
        (InstallType.PORTABLE, "alpha", 15 * 60),
        (InstallType.PORTABLE, "beta", 30 * 60),
        (InstallType.PORTABLE, "stable", 60 * 60),
    ],
)
def test_cadence_is_a_property_of_what_the_install_tracks(
    install_type, channel, expected
):
    # Install type wins over channel: a source checkout tracks a git branch
    # whatever channel it nominally reports.
    assert discovery_interval_seconds(install_type, channel) == expected


def test_no_cadence_can_ask_faster_than_the_floor(tmp_path: Path):
    service, scheduler, _, _ = _clocked(
        tmp_path,
        policy=UpdatePolicy(rollout_cohort=42, minimum_check_interval_seconds=60),
    )
    assert scheduler.interval_seconds() >= MINIMUM_INTERVAL_SECONDS


def test_a_source_checkout_checks_at_developer_cadence(tmp_path: Path):
    service, scheduler, fetcher, clock = _clocked(
        tmp_path,
        policy=UpdatePolicy(rollout_cohort=42),
        installed=_installed(install_type=InstallType.SOURCE_CHECKOUT),
    )
    scheduler.tick()
    assert len(fetcher.calls) == 1

    # Read the interval rather than assume it: the policy value is jittered per
    # rollout cohort, so a hard-coded ten minutes is a coin flip on which side
    # of the boundary the test lands.
    interval = service.effective_check_interval_seconds()
    assert 5 * 60 <= interval <= 12 * 60, "developer cadence, not the stable one"

    _advance(scheduler, clock, interval - 180)
    assert len(fetcher.calls) == 1, "not due yet"

    _advance(scheduler, clock, 360)
    operation = service.store.load_operation()
    assert len(fetcher.calls) == 2
    assert operation.last_trigger == TRIGGER_PERIODIC


def test_a_packaged_stable_install_stays_quiet(tmp_path: Path):
    service, scheduler, fetcher, clock = _clocked(
        tmp_path, policy=UpdatePolicy(rollout_cohort=42)
    )
    scheduler.tick()
    # Stable is hourly now rather than four-hourly (#832), so "quiet" is
    # measured well inside that window: half an hour must not refetch.
    _advance(scheduler, clock, 30 * 60)
    assert len(fetcher.calls) == 1, "stable must not poll every half hour"


# --------------------------------------------------------------------------
# Triggers.
# --------------------------------------------------------------------------


def test_startup_forces_a_check_even_with_a_fresh_cached_answer(tmp_path: Path):
    # A restart asks "did anything land while I was closed?". An ordinary check
    # would be satisfied by whatever was cached before the process died, which
    # is exactly the answer that cannot be trusted.
    store = UpdateStore(UpdaterPaths.for_home(tmp_path))
    store.save_policy(UpdatePolicy(rollout_cohort=42))
    service, scheduler, fetcher, clock = _clocked(
        tmp_path, policy=UpdatePolicy(rollout_cohort=42)
    )
    service.check(force=True)  # a previous session's successful check
    assert len(fetcher.calls) == 1

    operation = scheduler.tick()  # first tick of a new process

    assert len(fetcher.calls) == 2
    assert operation.last_trigger == TRIGGER_STARTUP
    assert operation.result_from_cache is False


def test_a_long_gap_between_ticks_is_treated_as_a_resume(tmp_path: Path):
    service, scheduler, fetcher, clock = _clocked(
        tmp_path, policy=UpdatePolicy(rollout_cohort=42)
    )
    scheduler.tick()
    clock["t"] += timedelta(seconds=30)
    scheduler.tick()
    assert len(fetcher.calls) == 1

    clock["t"] += timedelta(hours=9)  # the machine slept
    operation = scheduler.tick()

    assert operation.last_trigger == TRIGGER_RESUME
    assert len(fetcher.calls) == 2


def test_resume_while_still_fresh_does_not_storm_the_source(tmp_path: Path):
    service, scheduler, fetcher, clock = _clocked(
        tmp_path, policy=UpdatePolicy(rollout_cohort=42)
    )
    scheduler.tick()
    clock["t"] += timedelta(hours=9)
    scheduler.tick()
    calls_after_resume = len(fetcher.calls)

    for _ in range(5):
        clock["t"] += timedelta(seconds=30)
        scheduler.tick()

    assert len(fetcher.calls) == calls_after_resume


def test_an_offline_state_retries_when_the_network_returns(tmp_path: Path):
    service, scheduler, fetcher, clock = _clocked(
        tmp_path,
        policy=UpdatePolicy(rollout_cohort=42),
        fetch_error=OSError("unreachable"),
    )
    operation = scheduler.tick()
    assert operation.state is UpdateState.UNAVAILABLE
    offline_calls = len(fetcher.calls)

    scheduler.network_changed(False)
    _advance(scheduler, clock, 10 * 60)
    assert len(fetcher.calls) == offline_calls, "no retry while still offline"

    # Advanced with the heartbeat running: a bare clock jump is a *resume*,
    # which would also produce a check and would test the wrong path.
    scheduler.network_changed(True)
    _advance(scheduler, clock, 30 * 60)
    operation = service.store.load_operation()

    assert len(fetcher.calls) > offline_calls
    assert operation.last_trigger == TRIGGER_NETWORK_RESTORED


def test_discovery_disabled_by_policy_stops_the_scheduler(tmp_path: Path):
    service, scheduler, fetcher, clock = _clocked(
        tmp_path, policy=UpdatePolicy(rollout_cohort=42, check_for_updates=False)
    )
    scheduler.tick()  # startup still runs; the service enforces the policy
    before = len(fetcher.calls)
    clock["t"] += timedelta(days=1)
    scheduler.tick()
    assert len(fetcher.calls) == before


# --------------------------------------------------------------------------
# Manual.
# --------------------------------------------------------------------------


def test_manual_check_bypasses_the_freshness_window(tmp_path: Path):
    service, scheduler, fetcher, clock = _clocked(
        tmp_path, policy=UpdatePolicy(rollout_cohort=42)
    )
    scheduler.tick()
    assert len(fetcher.calls) == 1

    clock["t"] += timedelta(seconds=5)
    operation = service.check(force=True, trigger="manual")

    assert len(fetcher.calls) == 2, "manual intent must reach the update source"
    assert operation.result_from_cache is False


def test_manual_check_that_cannot_reach_the_source_never_reports_up_to_date(
    tmp_path: Path,
):
    # "Could not check" must never be rendered as "you're up to date".
    service, scheduler, fetcher, clock = _clocked(
        tmp_path,
        policy=UpdatePolicy(rollout_cohort=42),
        fetch_error=OSError("unreachable"),
    )
    operation = service.check(force=True, trigger="manual")
    assert operation.state is not UpdateState.UP_TO_DATE
    assert operation.state is UpdateState.UNAVAILABLE


# --------------------------------------------------------------------------
# Migration -- the half that decides whether any of this reaches anyone.
# --------------------------------------------------------------------------


def test_an_untouched_legacy_interval_is_migrated_once(tmp_path: Path):
    service, _, _, _ = _service(
        tmp_path,
        policy=UpdatePolicy(
            rollout_cohort=42,
            minimum_check_interval_seconds=LEGACY_INTERVAL_SECONDS,
        ),
        installed=_installed(install_type=InstallType.SOURCE_CHECKOUT),
    )
    store = UpdateStore(UpdaterPaths.for_home(tmp_path))

    policy = service.policy()

    assert policy.minimum_check_interval_seconds == 10 * 60
    assert policy.cadence_policy_version == CADENCE_POLICY_VERSION
    # Persisted, not merely returned: an install that already exists is the
    # whole point, and a value that lives only in memory helps nobody.
    persisted = json.loads(store.paths.policy.read_text(encoding="utf-8"))
    assert persisted["minimum_check_interval_seconds"] == 10 * 60


def test_an_interval_the_user_chose_is_never_overwritten(tmp_path: Path):
    service, _, _, _ = _service(
        tmp_path,
        policy=UpdatePolicy(rollout_cohort=42, minimum_check_interval_seconds=90 * 60),
        installed=_installed(install_type=InstallType.SOURCE_CHECKOUT),
    )

    policy = service.policy()

    assert policy.minimum_check_interval_seconds == 90 * 60
    # ...and the question is not asked again on every read.
    assert policy.cadence_policy_version == CADENCE_POLICY_VERSION


def test_migration_is_idempotent(tmp_path: Path):
    service, _, _, _ = _service(
        tmp_path,
        policy=UpdatePolicy(
            rollout_cohort=42,
            minimum_check_interval_seconds=LEGACY_INTERVAL_SECONDS,
        ),
        installed=_installed(install_type=InstallType.SOURCE_CHECKOUT),
    )
    first = service.policy().minimum_check_interval_seconds
    service.set_policy(minimum_check_interval_seconds=30 * 60)
    assert service.policy().minimum_check_interval_seconds == 30 * 60
    assert first == 10 * 60


# --------------------------------------------------------------------------
# Diagnostics.
# --------------------------------------------------------------------------


def test_diagnostics_distinguish_a_cached_result_from_a_fresh_one(tmp_path: Path):
    service, scheduler, _, clock = _clocked(
        tmp_path, policy=UpdatePolicy(rollout_cohort=42)
    )
    scheduler.tick()
    fresh = service.discovery_diagnostics()
    assert fresh["showing_cached_result"] is False
    assert fresh["last_remote_check_at"]

    clock["t"] += timedelta(seconds=30)
    scheduler.tick()
    cached = service.discovery_diagnostics()

    assert cached["showing_cached_result"] is True
    assert cached["last_remote_check_at"] == fresh["last_remote_check_at"]
    assert cached["next_remote_check_eligible_at"]


def test_diagnostics_name_the_update_source_and_why_the_cadence_applies(
    tmp_path: Path,
):
    service, _, _, _ = _service(
        tmp_path, installed=_installed(install_type=InstallType.SOURCE_CHECKOUT)
    )
    diagnostics = service.discovery_diagnostics()
    assert diagnostics["install_type"] == "source_checkout"
    assert diagnostics["cadence_seconds"] == 10 * 60
    assert "source checkout" in diagnostics["cadence_reason"]


# --------------------------------------------------------------------------
# Multi-window.
# --------------------------------------------------------------------------


def test_two_windows_sharing_a_store_do_not_each_reach_the_source(tmp_path: Path):
    # Both windows tick at the same instant. The store's interprocess guard
    # serialises them, and the second sees the first's fresh result.
    service, scheduler_a, fetcher, clock = _clocked(
        tmp_path, policy=UpdatePolicy(rollout_cohort=42)
    )
    scheduler_b = UpdateScheduler(service, now=lambda: clock["t"])

    scheduler_a.tick()
    scheduler_b.tick()  # its own startup force
    clock["t"] += timedelta(seconds=30)
    before = len(fetcher.calls)
    scheduler_a.tick()
    scheduler_b.tick()

    assert len(fetcher.calls) == before, "neither window refetches inside the window"


def test_a_failing_check_does_not_retry_on_every_heartbeat(tmp_path: Path):
    # A failed check leaves `last_successful_check_at` empty, and "never
    # succeeded" reads as "due now" -- so an installation that cannot reach its
    # update source retried on every single 2s heartbeat. Only a test that
    # actually fails a check can see this; one that mocks a success never will.
    service, scheduler, fetcher, clock = _clocked(
        tmp_path,
        policy=UpdatePolicy(rollout_cohort=42),
        fetch_error=OSError("unreachable"),
    )
    scheduler.tick()
    after_first = len(fetcher.calls)

    ticks = 300
    _advance(scheduler, clock, ticks * 2, step=2)  # ten minutes of 2s heartbeats

    retries = len(fetcher.calls) - after_first
    # The property is the ratio, not a magic number: retries follow the
    # service's exponential backoff, so they are a handful across hundreds of
    # heartbeats rather than one per heartbeat.
    assert retries <= 10, "offline retries must follow backoff, not the heartbeat"
    assert retries < ticks / 10


# --------------------------------------------------------------------------
# Policy provenance and jitter identity (#832 scope items 4-6).
# --------------------------------------------------------------------------


def test_an_interval_the_user_chose_survives_even_at_the_legacy_value(tmp_path: Path):
    # The defect this fixes was mine. Migration inferred "the user did not
    # choose this" from the value being numerically equal to the historic
    # default, so anyone who had deliberately selected exactly four hours was
    # silently moved to ten minutes.
    service, _, _, _ = _service(
        tmp_path,
        policy=UpdatePolicy(
            rollout_cohort=42,
            minimum_check_interval_seconds=LEGACY_INTERVAL_SECONDS,
            cadence_source="user",
        ),
        installed=_installed(install_type=InstallType.SOURCE_CHECKOUT),
    )

    policy = service.policy()

    assert policy.minimum_check_interval_seconds == LEGACY_INTERVAL_SECONDS
    assert policy.cadence_source == "user"


def test_a_managed_interval_is_never_migrated(tmp_path: Path):
    service, _, _, _ = _service(
        tmp_path,
        policy=UpdatePolicy(
            rollout_cohort=42,
            minimum_check_interval_seconds=LEGACY_INTERVAL_SECONDS,
            cadence_source="managed",
        ),
        installed=_installed(install_type=InstallType.SOURCE_CHECKOUT),
    )
    assert service.policy().minimum_check_interval_seconds == LEGACY_INTERVAL_SECONDS


def test_a_legacy_policy_without_provenance_is_still_migrated_once(tmp_path: Path):
    # Policies written before provenance existed carry none, and the numeric
    # heuristic is the only signal available for them. It runs once, and the
    # result is recorded as "migrated" so it never runs again.
    service, _, _, _ = _service(
        tmp_path,
        policy=UpdatePolicy(
            rollout_cohort=42,
            minimum_check_interval_seconds=LEGACY_INTERVAL_SECONDS,
        ),
        installed=_installed(install_type=InstallType.SOURCE_CHECKOUT),
    )

    policy = service.policy()

    assert policy.minimum_check_interval_seconds == 10 * 60
    assert policy.cadence_source == "migrated"


def test_choosing_an_interval_records_that_the_user_chose_it(tmp_path: Path):
    service, _, _, _ = _service(tmp_path, policy=UpdatePolicy(rollout_cohort=42))

    service.set_policy(minimum_check_interval_seconds=90 * 60)

    policy = service.policy()
    assert policy.cadence_source == "user"
    assert policy.minimum_check_interval_seconds == 90 * 60


def test_polling_jitter_does_not_read_rollout_eligibility(tmp_path: Path):
    # Two questions, two seeds. Sharing one meant moving an installation
    # between staged-rollout buckets silently changed how often it polled, and
    # tuning the poll spread would have moved installations between buckets.
    from opai.update.service import _jittered_check_interval

    base = dict(minimum_check_interval_seconds=3600, poll_jitter_seed=7)
    same_seed_different_cohorts = {
        int(_jittered_check_interval(UpdatePolicy(rollout_cohort=c, **base)))
        for c in (0, 42, 99)
    }
    assert len(same_seed_different_cohorts) == 1, "cohort must not move the interval"

    different_seeds = {
        int(
            _jittered_check_interval(
                UpdatePolicy(
                    rollout_cohort=42,
                    minimum_check_interval_seconds=3600,
                    poll_jitter_seed=seed,
                )
            )
        )
        for seed in range(0, 100, 7)
    }
    assert len(different_seeds) > 1, "the seed must actually spread load"


def test_a_policy_without_a_seed_gets_one_without_changing_its_spread(tmp_path: Path):
    # Falling back to the cohort keeps an upgrading installation's spread
    # stable; the store then gives it a seed of its own.
    from opai.update.service import _jittered_check_interval

    legacy = UpdatePolicy(rollout_cohort=42, minimum_check_interval_seconds=3600)
    assert legacy.poll_jitter_seed == -1
    before = int(_jittered_check_interval(legacy))

    service, _, _, _ = _service(tmp_path, policy=legacy)
    policy = service.policy()

    assert policy.poll_jitter_seed >= 0, "a seed of its own, persisted"
    assert before == int(
        _jittered_check_interval(
            UpdatePolicy(rollout_cohort=42, minimum_check_interval_seconds=3600)
        )
    )


def test_stable_no_longer_waits_four_hours(tmp_path: Path):
    # A stable user could be four hours behind a fix that had already shipped,
    # through the very channel those fixes arrive on.
    assert discovery_interval_seconds(InstallType.PORTABLE, "stable") == 60 * 60
    assert discovery_interval_seconds(InstallType.PORTABLE, "beta") == 30 * 60
