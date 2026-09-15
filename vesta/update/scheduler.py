"""When discovery runs. The service still decides what it finds.

Scheduling used to live in the browser:

    setTimeout(() => bridge.checkForUpdates(true), 3000);
    setInterval(() => bridge.checkForUpdates(false), 15 * 60 * 1000);

which put updater policy in the one place that cannot know any of the things
the decision depends on -- the install type, the channel, the retry state,
whether another window is already doing it -- and gave every webview an equal
vote on when to talk to the update source.

This owns that decision instead. It is deliberately a pure decision function
plus a thin driver: `decide()` takes the current state and a clock and returns
either a reason to check or None, so the cadence can be tested without timers,
threads or a running application.

Cross-process safety is not this class's invention: `UpdateService.check()`
already performs its discovery inside the store's interprocess operation
guard. Two windows that tick at the same moment serialise there, and the
loser sees the winner's fresh result and returns it as cached. What this adds
is that they no longer *each* decide to talk to the network first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .cadence import MINIMUM_INTERVAL_SECONDS, discovery_interval_seconds
from .models import UpdateOperation, UpdateState

# Triggers, recorded on the operation so diagnostics can say what woke it.
TRIGGER_STARTUP = "startup"
TRIGGER_PERIODIC = "periodic"
TRIGGER_RESUME = "resume"
TRIGGER_NETWORK_RESTORED = "network_restored"
TRIGGER_MANUAL = "manual"

# A tick arriving this much later than the one before it means the machine was
# asleep, or the process was suspended, rather than that time passed normally.
_RESUME_GAP_SECONDS = 10 * 60

# States that mean the last attempt could not reach the update source. When
# connectivity returns, these are worth retrying sooner than the ordinary
# cadence -- but never faster than the floor.
_OFFLINE_STATES = frozenset({UpdateState.UNAVAILABLE})


@dataclass(frozen=True)
class Decision:
    """Whether to check now, and why. `reason` is empty when not checking."""

    should_check: bool
    reason: str = ""
    force: bool = False
    detail: str = ""


class UpdateScheduler:
    """Owns startup, periodic, resume and network-restoration discovery."""

    def __init__(
        self,
        service,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._service = service
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._started = False
        self._last_tick: datetime | None = None
        self._network_available = True

    # -- policy -------------------------------------------------------------

    def interval_seconds(self) -> int:
        """This installation's discovery cadence, floored."""

        # Asked of the service, not computed here: the service gates `check()`
        # on the jittered interval, and a scheduler with its own arithmetic
        # would refuse ticks the service would have allowed, or the reverse.
        policy = self._service.policy()
        configured = int(self._service.effective_check_interval_seconds())
        cadence = discovery_interval_seconds(
            self._service.installed.install_type, policy.channel
        )
        return max(MINIMUM_INTERVAL_SECONDS, configured or cadence)

    def decide(self, operation: UpdateOperation) -> Decision:
        """Whether this tick should check, and why. Pure: no I/O, no clock skew."""

        now = self._now()
        previous_tick, self._last_tick = self._last_tick, now

        if not self._started:
            # The question a restart is implicitly asking is "did anything
            # land while I was closed?", and only a forced check answers it --
            # an ordinary one would be satisfied by whatever was cached before
            # the process died.
            return Decision(True, TRIGGER_STARTUP, force=True)

        if not self._service.policy().discovery_allowed:
            return Decision(False)

        if previous_tick is not None:
            gap = (now - previous_tick).total_seconds()
            if gap >= _RESUME_GAP_SECONDS:
                # The machine slept or the process was suspended. Whatever is
                # on screen is at least `gap` old, so re-establish it.
                return Decision(
                    True, TRIGGER_RESUME, force=True, detail=f"{int(gap)}s gap"
                )

        # Backoff first, and for every state.
        #
        # A failed check leaves `last_successful_check_at` empty, and the
        # periodic branch below treats "never succeeded" as "due now" -- so
        # without this an installation that cannot reach its update source
        # retries on every single heartbeat. That is the tight loop this
        # scheduler exists to prevent, and it is only visible once a test
        # actually fails a check rather than mocking a success.
        retry_at = _parse(operation.next_retry_at)
        if retry_at is not None and now < retry_at:
            return Decision(False)

        if operation.state in _OFFLINE_STATES:
            if not self._network_available:
                return Decision(False)
            # The backoff above has already elapsed, so this is the moment the
            # service itself nominated for the next attempt.
            return Decision(True, TRIGGER_NETWORK_RESTORED, force=True)

        checked = _parse(operation.last_successful_check_at)
        if checked is None:
            return Decision(True, TRIGGER_PERIODIC)
        due_at = checked + timedelta(seconds=self.interval_seconds())
        if now >= due_at:
            return Decision(True, TRIGGER_PERIODIC)
        return Decision(False)

    # -- driving ------------------------------------------------------------

    def tick(self) -> UpdateOperation:
        """One scheduler wake-up. Records the wake whether or not it checks."""

        operation = self._service.store.load_operation()
        decision = self.decide(operation)
        self._started = True
        if not decision.should_check:
            return self._record_tick(operation)
        return self._service.check(
            force=decision.force,
            allow_automatic_download=True,
            trigger=decision.reason,
        )

    def network_changed(self, available: bool) -> None:
        """Tell the scheduler connectivity came back, or went away."""

        self._network_available = bool(available)

    def _record_tick(self, operation: UpdateOperation) -> UpdateOperation:
        """A wake that decided not to check is still a fact worth recording.

        Without it, diagnostics cannot distinguish "the scheduler is dead" from
        "the scheduler is running and correctly deciding there is nothing to
        do", which are very different bugs to be chasing.
        """

        from dataclasses import replace

        from .errors import UpdateError
        from vestahub.atomic_io import InterprocessLockTimeout

        stamp = self._now().isoformat()
        try:
            with self._service.store.operation_guard():
                latest = self._service.store.load_operation()
                return self._service._save(
                    replace(
                        latest,
                        scheduler_tick_at=stamp,
                        # A wake that declines to check leaves a cached result
                        # on screen just as surely as a freshness gate does.
                        # There are two ways not to check and both have to say
                        # so, or the flag means "the service declined" rather
                        # than "what you are looking at is not fresh".
                        result_from_cache=True,
                        updated_at=stamp,
                    )
                )
        except (InterprocessLockTimeout, UpdateError, OSError):
            return operation


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
