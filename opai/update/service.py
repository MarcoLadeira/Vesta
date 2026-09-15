"""One canonical updater service shared by GUI, CLI, doctor, and adapters."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from opaihub.atomic_io import InterprocessLockTimeout
from packaging.version import InvalidVersion, Version

from .adapters import DeveloperGitUpdateAdapter, UpdateAdapter
from .cadence import (
    CADENCE_POLICY_VERSION,
    cadence_reason,
    LEGACY_INTERVAL_SECONDS,
    discovery_interval_seconds,
)
from .download import DownloadError, SecureDownloader
from .errors import UpdateError
from .manifest import ManifestError, verify_manifest
from .relaunch import relaunch_command, schedule_relaunch
from .report import user_facing
from .ownership import (
    describe_ownership,
    running_identity,
    safe_launcher_identity,
)
from .models import (
    UpdateTrigger,
    InstallType,
    InstalledBuild,
    UpdateCandidate,
    UpdateOperation,
    UpdateOwner,
    UpdatePolicy,
    UpdateState,
    can_transition,
)
from .runtime import ActiveWorkStatus
from .storage import UpdateStore


ManifestFetcher = Callable[[str], bytes]
RuntimeProbe = Callable[[], ActiveWorkStatus]

# When this process began, near enough. A "restart to use it" banner is only
# meaningful while the process it is talking to is the one that was running
# when the update landed; compared against the operation's own updated_at,
# this is how the app knows the restart it asked for already happened.
_PROCESS_STARTED_AT = datetime.now(timezone.utc)


_SAFE_DIAGNOSTICS = {
    "offline": "The update service is unreachable right now.",
    "feed_unavailable": "The update feed could not be read.",
    "artifact_hash_mismatch": "The downloaded update did not match its signed digest.",
    "artifact_size_mismatch": "The downloaded update was incomplete or the wrong size.",
    "publisher_mismatch": "The update publisher identity did not match this installation.",
    "active_work_blocked": "Vesta is waiting for active work to reach a safe boundary.",
    "installer_failed": "The platform updater could not start the installation.",
    "rollback_failed": "The platform updater could not restore the last-known-good build.",
    "recovery_unavailable": "A verified recovery package is not available for this update.",
}


def _diagnostic(category: str) -> str:
    return _SAFE_DIAGNOSTICS.get(
        category, "The update operation could not be completed safely."
    )


_MANUAL_FAILURE_MESSAGES = {
    "operation_busy": "Another update operation is running. Try again in a moment.",
    "policy_blocked": "Updates for this installation are managed elsewhere.",
    "operation_not_downloadable": "Vesta could not start this update.",
}

_UNSUPPORTED_INSTALL_DIAGNOSTIC = (
    "This installation is not transactionally replaceable; use a "
    "verified package or the explicit developer update command."
)

_FEEDLESS_INSTALL_TYPES = frozenset(
    {
        InstallType.PORTABLE,
        InstallType.SOURCE_CHECKOUT,
        InstallType.UNKNOWN,
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained_regular_file(path: Path, root: Path, expected_sha256: str) -> bool:
    """Revalidate a deferred artifact at its final mutation boundary."""

    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
        linked = path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )
        return (
            not linked
            and resolved.is_file()
            and len(expected_sha256) == 64
            and _sha256(resolved) == expected_sha256
        )
    except (OSError, ValueError):
        return False


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _resolve_trigger(value: object) -> UpdateTrigger | None:
    """Best-effort trigger, because callers pass strings and enums alike."""

    if isinstance(value, UpdateTrigger):
        return value
    try:
        return UpdateTrigger(str(value))
    except ValueError:
        return None


def _jittered_check_interval(policy: UpdatePolicy) -> float:
    """Apply stable local jitter without sending or persisting client identity.

    Seeded from `poll_jitter_seed`, never from `rollout_cohort`. They answer
    different questions -- one spreads network load, the other decides staged
    release eligibility -- and sharing a seed meant that moving an installation
    between rollout buckets silently changed how often it polled.
    """

    seed = policy.poll_jitter_seed
    if seed < 0:
        # A policy written before the seed existed falls back to the cohort so
        # its spread does not jump on upgrade; the store gives it a seed of its
        # own on the next write.
        seed = policy.rollout_cohort if policy.rollout_cohort >= 0 else 50
    percent = ((seed * 37) % 21) - 10
    return policy.minimum_check_interval_seconds * (1 + percent / 100)


class UpdateService:
    def __init__(
        self,
        *,
        store: UpdateStore,
        installed: InstalledBuild,
        trust: Mapping[str, Any],
        manifest_fetcher: ManifestFetcher,
        downloader: SecureDownloader,
        adapter: UpdateAdapter,
        runtime_probe: RuntimeProbe,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.installed = installed
        self.trust = dict(trust)
        self.manifest_fetcher = manifest_fetcher
        self.downloader = downloader
        self.adapter = adapter
        self.runtime_probe = runtime_probe
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._restart_available: bool | None = None

    def effective_check_interval_seconds(self) -> int:
        """The interval this installation actually gates on, jitter included.

        Exposed so the scheduler and the service cannot disagree: the scheduler
        was deciding "due at 600" while `check()` gated at 540, which makes the
        cadence a matter of which of the two you ask.
        """

        return int(_jittered_check_interval(self.policy()))

    def policy(self) -> UpdatePolicy:
        return self._migrate_cadence(self.store.load_policy())

    def _migrate_cadence(self, policy: UpdatePolicy) -> UpdatePolicy:
        """Adopt this installation's cadence, once, without silencing the user.

        Changing a dataclass default does nothing to an installation that has
        already run: the interval is persisted in policy.json, and every
        machine that has ever started Vesta has 14400 written into it. Without
        this, new installs would discover updates every ten minutes, existing
        ones would keep waiting four hours, and the difference would be
        invisible to every test that starts from a fresh temp directory --
        which is all of them.

        Only an *untouched* legacy interval is migrated. A user who has chosen
        their own interval keeps it, and the version stamp is written either
        way so the question is asked exactly once.
        """

        if policy.cadence_policy_version >= CADENCE_POLICY_VERSION:
            return self._ensure_jitter_seed(policy)
        changes: dict[str, object] = {"cadence_policy_version": CADENCE_POLICY_VERSION}
        # Provenance, not arithmetic. An interval the user or a managed policy
        # chose is never touched, whatever it happens to equal -- inferring
        # "untouched" from equality with the historic default silently
        # overwrote anyone who had deliberately chosen exactly four hours.
        chosen_by_someone = policy.cadence_source in {"user", "managed"}
        legacy_untouched = (
            policy.cadence_source in {"", "default"}
            and policy.minimum_check_interval_seconds == LEGACY_INTERVAL_SECONDS
        )
        if not chosen_by_someone and legacy_untouched:
            target = discovery_interval_seconds(
                self.installed.install_type, policy.channel
            )
            if target != policy.minimum_check_interval_seconds:
                changes["minimum_check_interval_seconds"] = target
                changes["cadence_source"] = "migrated"
        try:
            return self._ensure_jitter_seed(self.store.update_policy(**changes))
        except (OSError, UpdateError, ValueError):
            # A policy that cannot be rewritten is still a usable policy.
            return policy

    def _ensure_jitter_seed(self, policy: UpdatePolicy) -> UpdatePolicy:
        """Give this installation a polling seed of its own, once."""

        if policy.poll_jitter_seed >= 0:
            return policy
        try:
            return self.store.update_policy(poll_jitter_seed=secrets.randbelow(100))
        except (OSError, UpdateError, ValueError):
            return policy

    def set_policy(self, **changes: object) -> UpdatePolicy:
        allowed = {
            "check_for_updates",
            "automatic_downloads",
            "automatic_install_on_quit",
            "channel",
            "minimum_check_interval_seconds",
            "critical_update_enforcement_hours",
            "remind_later_until",
        }
        if not changes or any(key not in allowed for key in changes):
            raise UpdateError("policy_change_invalid")
        current = self.policy()
        downloads = bool(
            changes.get("automatic_downloads", current.automatic_downloads)
        )
        if changes.get("automatic_install_on_quit") is True and not downloads:
            raise UpdateError("automatic_download_required")
        if "minimum_check_interval_seconds" in changes:
            # Record who chose it, so no future migration has to guess from the
            # number. A user who picks exactly the historic default is now
            # indistinguishable from one who picks anything else -- which is
            # the point.
            changes = {**changes, "cadence_source": "user"}
        return self.store.update_policy(**changes)

    def _recovery_candidate(self, target: UpdateCandidate) -> UpdateCandidate:
        raw = target.native.get("recovery")
        if not target.rollback_compatible or not isinstance(raw, Mapping):
            raise UpdateError("recovery_unavailable")
        try:
            recovery = UpdateCandidate.from_dict(raw)
            target_version = Version(target.version)
            recovery_version = Version(recovery.version)
        except (TypeError, ValueError, InvalidVersion) as exc:
            raise UpdateError("recovery_unavailable") from exc
        parsed = urlparse(recovery.artifact_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or recovery_version >= target_version
            or recovery.version != self.installed.version
            or recovery.build_id != self.installed.build_id
            or recovery.channel != self.installed.channel
            or recovery.platform.casefold() != self.installed.platform.casefold()
            or recovery.architecture.casefold()
            != self.installed.architecture.casefold()
            or recovery.install_type is not self.installed.install_type
            or recovery.publisher_identity != self.installed.publisher_identity
            or recovery.native.get("recovery") is not None
        ):
            raise UpdateError("recovery_unavailable")
        return recovery

    def _prepare_recovery(
        self,
        target: UpdateCandidate,
        operation_id: str,
    ) -> dict[str, object]:
        recovery = self._recovery_candidate(target)
        recovery_operation = f"{operation_id}-recovery"
        try:
            source = self.downloader.download(
                recovery,
                self.store.paths.downloads,
                recovery_operation,
                lambda _downloaded, _total: None,
            )
            verification = self.adapter.verify(source, recovery)
            if (
                not verification.verified
                or verification.publisher_identity != recovery.publisher_identity
            ):
                raise UpdateError("recovery_unavailable")
            staged = self.downloader.stage(
                source,
                recovery,
                self.store.paths.staging,
                recovery_operation,
            )
        except DownloadError as exc:
            raise UpdateError(
                "recovery_unavailable",
                retriable=exc.retriable,
            ) from exc
        if not _contained_regular_file(
            staged,
            self.store.paths.staging,
            recovery.artifact_sha256,
        ):
            raise UpdateError("recovery_unavailable")
        previous = recovery.to_dict()
        previous.update(
            {
                "package_identity": self.installed.package_identity,
                "artifact_path": str(staged),
                "artifact_sha256": recovery.artifact_sha256,
            }
        )
        return previous

    def _guard(self):
        try:
            return self.store.operation_guard()
        except InterprocessLockTimeout as exc:  # pragma: no cover - enter raises lazily
            raise UpdateError("operation_busy", retriable=True) from exc

    def _current_for(self, operation_id: str) -> UpdateOperation:
        current = self.store.load_operation()
        if current.operation_id != operation_id:
            raise UpdateError("stale_operation")
        return current

    def _save(self, operation: UpdateOperation) -> UpdateOperation:
        return self.store.save_operation(operation)

    def _begin_check(
        self, current: UpdateOperation, trigger: str = ""
    ) -> UpdateOperation:
        if current.state is UpdateState.IDLE or can_transition(
            current.state, UpdateState.CHECKING
        ):
            return current.transition(
                UpdateState.CHECKING,
                last_check_at=self._now().isoformat(),
                result_from_cache=False,
                last_trigger=trigger or current.last_trigger,
                error_category="",
                safe_diagnostic="",
                candidate={},
                downloaded_bytes=0,
                total_bytes=0,
                staged_artifact="",
                staged_sha256="",
                native_transaction="",
                desired_state="",
            )
        raise UpdateError("operation_busy", retriable=True)

    def _retry_changes(self, operation: UpdateOperation) -> dict[str, object]:
        retries = min(operation.retry_count + 1, 8)
        delay = min(60 * (2 ** (retries - 1)), 60 * 60)
        return {
            "retry_count": retries,
            "next_retry_at": (self._now() + timedelta(seconds=delay)).isoformat(),
        }

    def _auto_fast_forward(self, checking: UpdateOperation, behind: int) -> str | None:
        """Fast-forward a source checkout automatically, when that is safe.

        A packaged install has had automatic updates for a long time:
        discovery, staged download, health-gated activation, rollback. A source
        checkout had none of it. ``maintain()`` re-checked it every few hours,
        landed in UNSUPPORTED_INSTALL each time, and told the user to run a
        command -- which is not an automatic update, it is a recurring
        reminder. That is what "auto-update does not work" meant for anyone
        running Vesta from source.

        Safety is delegated, not re-implemented. ``apply_source`` already
        refuses a dirty working tree unless forced, and only ever
        fast-forwards, so a checkout carrying local commits is left alone
        rather than rebased behind its owner's back. Calling it with
        ``force=False`` is therefore the whole guard: an unsafe checkout gets
        ``ok: False`` and this returns ``None``.

        Two conditions are decided here. Consent: ``check_for_updates`` alone
        is permission to *look*; ``automatic_downloads`` is the switch that
        says "act without asking me", and it gates every other automatic path
        in this service. And quiet: a fast-forward reinstalls the package
        under a live process, so it waits for the same active-work boundary
        the packaged path waits for. Refusing on busy costs nothing -- the
        next maintenance cycle tries again, and the manual button is still
        right there for anyone who wants it now.

        Calls the adapter rather than ``apply_developer_source`` deliberately:
        that method re-checks canonical state when it finishes, and this runs
        inside the caller's ``operation_guard``. The guard is reentrant
        in-process, so the re-check would not block -- it would quietly take a
        second lease and advance the fencing token, leaving the check that is
        still running holding a token that is no longer current. The nested
        check would then do nothing anyway: ``_begin_check`` refuses a state
        that is already CHECKING. The surrounding check produces the canonical
        transition, which is what that re-check was for.

        Progress is published as it goes, against the operation the caller is
        still holding. The GUI polls the persisted operation every couple of
        seconds, so the fetch/fast-forward/reinstall stages appear while they
        run instead of the window sitting frozen through a reinstall that
        takes seconds and shows nothing. The stages are written with
        ``replace`` rather than ``transition``: the operation genuinely is
        still the same check, and inventing CHECKING -> CHECKING to say so
        would be a lie about the state machine.

        Returns the message to show, or ``None`` -- and on ``None`` the caller
        reports the manual state exactly as before, so withholding this never
        removes the button that was already there.

        Deliberately more conservative than Claude Code's updater, which
        replaces its own managed install and can afford to assume nothing else
        in the directory matters. This directory is somebody's working tree.
        """

        policy = self.policy()
        if not (policy.check_for_updates and policy.automatic_downloads):
            return None
        if not self.runtime_probe().safe_to_install:
            return None
        adapter = self.adapter
        if not isinstance(adapter, DeveloperGitUpdateAdapter):
            return None

        def report(label: str, done: int, total: int) -> None:
            # Each stage is written against `checking` itself, so the progress
            # fields are the only thing that ever differs from the operation
            # the caller is holding -- and whichever transition it makes next
            # is therefore free of a half-drawn bar without having to clear it.
            self._save(
                replace(
                    checking,
                    progress_label=label,
                    downloaded_bytes=max(0, int(done)),
                    total_bytes=max(0, int(total)),
                )
            )

        try:
            result = adapter.apply_source(force=False, progress=report)
        except Exception:  # noqa: BLE001 - raw git errors must not invent a state
            # An automatic path never surfaces a new failure: the manual
            # report the caller falls back to is accurate and still actionable.
            return None
        if not bool(result.get("ok")):
            return None
        plural = "" if behind == 1 else "s"
        version = str(result.get("installed_version") or "").strip()
        return (
            f"Updated automatically: fast-forwarded {behind} commit{plural} from "
            + (f"origin/main to {version}. " if version else "origin/main. ")
            + "Restart Vesta to use it."
        )

    def _check_developer_source(
        self, checking: UpdateOperation, *, force: bool
    ) -> UpdateOperation:
        """Check a source checkout against its own git remote.

        The signed feed only governs packaged builds; a developer checkout's
        source of truth is ``origin``'s main branch. The git result maps onto
        the same durable states so every surface renders it unchanged.
        """
        adapter = self.adapter
        if not isinstance(adapter, DeveloperGitUpdateAdapter):
            # An assert here was stripped under -O, so the "caller guarantees"
            # went unenforced in exactly the builds that ship. A wrong adapter
            # now lands in the same retried offline state a git failure
            # produces, instead of calling a method it may not have.
            result: dict[str, object] = {"checked": False, "reason": "offline"}
        else:
            try:
                # Always forced. Two freshness layers guarded this path -- the
                # policy interval here and check_for_update's own one-hour TTL
                # underneath -- so a check the policy had just decided was due
                # could still be answered from an hour-old git result, while
                # the operation recorded a successful remote check that never
                # happened. Cadence is the policy's decision; by the time
                # control reaches here it has been made.
                result = adapter.check_source(force=True)
            except Exception:  # noqa: BLE001 - raw git errors normalize to safe state
                result = {"checked": False, "reason": "offline"}
        if bool(result.get("checked")):
            behind = result.get("commits_behind")
            behind = behind if isinstance(behind, int) and behind >= 0 else 0
            changes = {
                "candidate": {},
                "last_successful_check_at": self._now().isoformat(),
                "remote_checked_at": self._now().isoformat(),
                "result_from_cache": False,
                "retry_count": 0,
                "next_retry_at": "",
            }
            if behind == 0:
                return checking.transition(UpdateState.UP_TO_DATE, **changes)
            plural = "" if behind == 1 else "s"

            applied = self._auto_fast_forward(checking, behind)
            if applied is not None:
                return checking.transition(
                    UpdateState.COMPLETED, safe_diagnostic=applied, **changes
                )

            return checking.transition(
                UpdateState.UNSUPPORTED_INSTALL,
                error_category="manual_update_required",
                safe_diagnostic=(
                    f"This source checkout is {behind} commit{plural} behind "
                    "origin/main; update with the explicit developer update command."
                ),
                **changes,
            )
        reason = str(result.get("reason") or "").casefold()
        if "origin" in reason or "git checkout" in reason:
            # No remote to compare against: genuinely manual, not an outage.
            # (Reachability failures — timeouts, "could not reach" — fall
            # through to the retried offline state below.)
            return checking.transition(
                UpdateState.UNSUPPORTED_INSTALL,
                candidate={},
                error_category="manual_update_required",
                safe_diagnostic=_UNSUPPORTED_INSTALL_DIAGNOSTIC,
                last_successful_check_at=self._now().isoformat(),
                remote_checked_at=self._now().isoformat(),
                result_from_cache=False,
                retry_count=0,
                next_retry_at="",
            )
        return checking.transition(
            UpdateState.UNAVAILABLE,
            error_category="offline",
            safe_diagnostic=_diagnostic("offline"),
            **self._retry_changes(checking),
        )

    def check(
        self,
        *,
        force: bool = False,
        allow_automatic_download: bool = False,
        trigger: str = "",
    ) -> UpdateOperation:
        # The obligation travels with the reason. `force` stays for callers
        # that predate the trigger, but a trigger that requires remote evidence
        # can no longer be satisfied by a cached answer just because somebody
        # forgot the boolean.
        resolved = _resolve_trigger(trigger)
        if resolved is not None and resolved.remote_required:
            force = True
        trigger = resolved.value if resolved is not None else str(trigger or "")
        policy = self.policy()
        current = self.store.load_operation()
        checked = _parse_time(current.last_successful_check_at)
        retry_at = _parse_time(current.next_retry_at)
        interval = _jittered_check_interval(policy)
        now = self._now()
        eligible_at = (
            (checked + timedelta(seconds=interval)).isoformat() if checked else ""
        )
        if not force and (
            (
                current.state is UpdateState.UNAVAILABLE
                and retry_at is not None
                and self._now() < retry_at
            )
            or (
                current.state is not UpdateState.UNAVAILABLE
                and checked is not None
                and (self._now() - checked).total_seconds() < interval
            )
        ):
            # A tick that is answered from the persisted operation is not a
            # remote check, and must never be recorded as one. Only the
            # attempt and the cache flag move here -- `remote_checked_at` and
            # `last_successful_check_at` are left exactly where the last real
            # contact with the update source put them.
            try:
                with self.store.operation_guard():
                    latest = self._current_for(current.operation_id)
                    return self._save(
                        replace(
                            latest,
                            last_check_at=now.isoformat(),
                            result_from_cache=True,
                            next_check_eligible_at=eligible_at,
                            last_trigger=trigger or latest.last_trigger,
                            updated_at=now.isoformat(),
                        )
                    )
            except (InterprocessLockTimeout, UpdateError):
                return current
        try:
            with self.store.operation_guard():
                policy = self.policy()
                current = self.store.load_operation()
                checking = self._save(self._begin_check(current, trigger))
                if not policy.discovery_allowed:
                    return self._save(checking.transition(UpdateState.POLICY_BLOCKED))
                unsupported = self.installed.install_type in _FEEDLESS_INSTALL_TYPES
                feed_url = str(self.trust.get("feed_url") or "")
                if unsupported and not feed_url:
                    # No signed feed is configured for this installation, and a
                    # feed lookup could never yield a transactionally
                    # installable candidate for it. A source checkout still has
                    # a real source of truth — its own git remote — so check
                    # that instead of reporting a misleading feed error.
                    if isinstance(self.adapter, DeveloperGitUpdateAdapter):
                        return self._save(
                            self._check_developer_source(checking, force=force)
                        )
                    return self._save(
                        checking.transition(
                            UpdateState.UNSUPPORTED_INSTALL,
                            candidate={},
                            error_category="manual_update_required",
                            safe_diagnostic=_UNSUPPORTED_INSTALL_DIAGNOSTIC,
                            last_successful_check_at=self._now().isoformat(),
                            remote_checked_at=self._now().isoformat(),
                            result_from_cache=False,
                            retry_count=0,
                            next_retry_at="",
                        )
                    )
                try:
                    payload = self.manifest_fetcher(feed_url)
                except (OSError, TimeoutError):
                    return self._save(
                        checking.transition(
                            UpdateState.UNAVAILABLE,
                            error_category="offline",
                            safe_diagnostic=_diagnostic("offline"),
                            **self._retry_changes(checking),
                        )
                    )
                except Exception:  # noqa: BLE001 - raw feed errors are normalized here
                    return self._save(
                        checking.transition(
                            UpdateState.UNAVAILABLE,
                            error_category="feed_unavailable",
                            safe_diagnostic=_diagnostic("feed_unavailable"),
                            **self._retry_changes(checking),
                        )
                    )
                try:
                    verified = verify_manifest(
                        payload,
                        trust=self.trust,
                        installed=self.installed,
                        cohort=policy.rollout_cohort,
                        prior_metadata_version=max(
                            checking.highest_metadata_version,
                            self.store.load_metadata_floor(self.installed.channel),
                        ),
                        now=self._now(),
                        metadata_only=unsupported,
                    )
                except ManifestError as exc:
                    return self._save(
                        checking.transition(
                            UpdateState.UNAVAILABLE,
                            error_category=exc.code,
                            safe_diagnostic="The signed update metadata was rejected.",
                            **self._retry_changes(checking),
                        )
                    )
                durable_version = self.store.record_metadata_version(
                    verified.channel, verified.metadata_version
                )
                changes = {
                    "highest_metadata_version": durable_version,
                    "last_successful_check_at": self._now().isoformat(),
                    "remote_checked_at": self._now().isoformat(),
                    "result_from_cache": False,
                    "retry_count": 0,
                    "next_retry_at": "",
                }
                if unsupported:
                    return self._save(
                        checking.transition(
                            UpdateState.UNSUPPORTED_INSTALL,
                            candidate={},
                            error_category="manual_update_required",
                            safe_diagnostic=_UNSUPPORTED_INSTALL_DIAGNOSTIC,
                            **changes,
                        )
                    )
                if verified.candidate is None:
                    return self._save(
                        checking.transition(
                            UpdateState.UP_TO_DATE,
                            candidate={},
                            **changes,
                        )
                    )
                changes["candidate"] = verified.candidate.to_dict()
                if policy.owner is not UpdateOwner.OPAI:
                    result = self._save(
                        checking.transition(UpdateState.POLICY_BLOCKED, **changes)
                    )
                else:
                    result = self._save(
                        checking.transition(UpdateState.AVAILABLE, **changes)
                    )
        except InterprocessLockTimeout as exc:
            raise UpdateError("operation_busy", retriable=True) from exc
        if (
            allow_automatic_download
            and policy.automatic_downloads
            and result.state is UpdateState.AVAILABLE
        ):
            return self.download(result.operation_id)
        return result

    def restart_available(self) -> bool:
        """Whether Vesta can start itself again, decided by reading, not trying.

        The surface needs this before it offers a restart: a button that
        closes the window and does not bring it back is worse than no button.

        Cached, because ``status()`` is polled every couple of seconds and the
        answer is a property of how *this* process was started -- it cannot
        change while the process lives.
        """

        if self._restart_available is None:
            self._restart_available = relaunch_command() is not None
        return self._restart_available

    def restart_into_update(self) -> dict[str, object]:
        """Finish an applied update by restarting into it.

        A source update lands on disk while the old code is still loaded in
        memory, so the restart is the last step of the install, not a courtesy.
        Claude Code and Codex both stop one step earlier and tell you to run
        the command again; this does it.

        The supervisor is armed *before* anything closes, so a failure to arm
        is reported while the window is still there to read it. The caller
        quits only on ``ok``.
        """

        command = relaunch_command()
        if command is None:
            return {
                "ok": False,
                "message": (
                    "Vesta could not work out how it was started, so it will not "
                    "close itself. Quit and open it again to finish the update."
                ),
            }
        if not schedule_relaunch(command):
            return {
                "ok": False,
                "message": (
                    "Vesta could not arrange its own restart. Quit and open it "
                    "again to finish the update."
                ),
            }
        return {"ok": True, "message": "Restarting Vesta into the update…"}

    def apply_developer_source(self, *, force: bool = False) -> dict[str, object]:
        """Deliberate fast-forward of a developer source checkout to origin/main.

        Explicit user action only — the update policy gates discovery, never a
        direct command. ``force`` stashes local changes and restores them after
        the fast-forward (the CLI's ``--force``); without it a dirty tree
        refuses. The canonical state is re-checked either way, so every surface
        reflects the outcome. The returned payload carries a pre-composed,
        safe-to-render ``message`` for toasts.
        """
        if not isinstance(self.adapter, DeveloperGitUpdateAdapter):
            raise UpdateError("developer_update_requires_source_checkout")
        result = dict(self.adapter.apply_source(force=force))
        try:
            self.check(force=True)
        except UpdateError:
            pass
        if bool(result.get("ok")):
            version = str(result.get("installed_version") or "").strip()
            message = (
                f"Updated to {version} — restart Vesta to use it."
                if version
                else "Updated — restart Vesta to use it."
            )
            if result.get("local_changes_restored"):
                message += " Local changes were stashed and restored."
            # Make the outcome a state, not just a sentence.
            #
            # The re-check above has just concluded the checkout is up to date,
            # which is true and useless: UP_TO_DATE is not a state any banner
            # renders, so the update UI vanished the moment the apply
            # succeeded, and the only word about the pending restart was a
            # transient toast -- which by construction has no button on it.
            # Reported as three separate bugs: the banner "went away", it
            # "told me to restart", and it "didn't give me the option to".
            #
            # COMPLETED is the state that says installed-but-not-yet-running,
            # and it is where the surface offers Restart now.
            try:
                with self.store.operation_guard():
                    current = self.store.load_operation()
                    checking = self._save(current.transition(UpdateState.CHECKING))
                    self._save(
                        checking.transition(
                            UpdateState.COMPLETED,
                            safe_diagnostic=message,
                            error_category="",
                            progress_label="",
                        )
                    )
            except (InterprocessLockTimeout, UpdateError):
                # The state is cosmetic here; the update itself already landed.
                pass
        else:
            message = str(result.get("error") or "The update could not be applied.")
        result["message"] = message
        return result

    def download(self, operation_id: str) -> UpdateOperation:
        try:
            with self.store.operation_guard():
                current = self._current_for(operation_id)
                if current.state in {
                    UpdateState.READY_TO_INSTALL,
                    UpdateState.INSTALL_ON_QUIT,
                    UpdateState.WAITING_FOR_IDLE,
                    UpdateState.INSTALLING,
                    UpdateState.RESTARTING,
                    UpdateState.HEALTH_CHECKING,
                    UpdateState.COMPLETED,
                }:
                    return current
                if current.state not in {
                    UpdateState.AVAILABLE,
                    UpdateState.FAILED_RETRIABLE,
                    UpdateState.PAUSED,
                }:
                    raise UpdateError("operation_not_downloadable")
                policy = self.policy()
                if not policy.installation_allowed:
                    return self._save(
                        current.transition(
                            UpdateState.POLICY_BLOCKED,
                            error_category="installation_owned_elsewhere",
                            safe_diagnostic=(
                                "Updates for this installation are managed outside Vesta."
                            ),
                        )
                    )
                candidate = UpdateCandidate.from_dict(current.candidate)
                downloading = self._save(
                    current.transition(
                        UpdateState.DOWNLOADING,
                        downloaded_bytes=0,
                        total_bytes=candidate.artifact_size,
                        error_category="",
                        safe_diagnostic="",
                    )
                )

                def progress(downloaded: int, total: int) -> None:
                    nonlocal downloading
                    downloading = replace(
                        downloading,
                        downloaded_bytes=downloaded,
                        total_bytes=total,
                    )
                    self._save(downloading)

                try:
                    source = self.downloader.download(
                        candidate,
                        self.store.paths.downloads,
                        current.operation_id,
                        progress,
                    )
                    verifying = self._save(
                        downloading.transition(UpdateState.VERIFYING)
                    )
                    if source.stat().st_size != candidate.artifact_size:
                        raise DownloadError("artifact_size_mismatch")
                    digest = _sha256(source)
                    if digest != candidate.artifact_sha256:
                        raise DownloadError("artifact_hash_mismatch")
                    native = self.adapter.verify(source, candidate)
                    if not native.verified:
                        raise DownloadError(
                            native.error_category or "native_signature_invalid"
                        )
                    if native.publisher_identity != candidate.publisher_identity:
                        raise DownloadError("publisher_mismatch")
                    staged = self.downloader.stage(
                        source,
                        candidate,
                        self.store.paths.staging,
                        current.operation_id,
                    )
                    try:
                        previous = self._prepare_recovery(
                            candidate,
                            current.operation_id,
                        )
                    except UpdateError as exc:
                        raise DownloadError(
                            exc.category,
                            retriable=exc.retriable,
                        ) from exc
                    ready = self._save(
                        verifying.transition(
                            UpdateState.READY_TO_INSTALL,
                            staged_artifact=str(staged),
                            staged_sha256=digest,
                            last_known_good=previous,
                            downloaded_bytes=candidate.artifact_size,
                            total_bytes=candidate.artifact_size,
                        )
                    )
                except DownloadError as exc:
                    latest = self.store.load_operation()
                    if not exc.retriable:
                        if latest.state is UpdateState.DOWNLOADING:
                            latest = self._save(
                                latest.transition(UpdateState.VERIFYING)
                            )
                        return self._save(
                            latest.transition(
                                UpdateState.FAILED_TERMINAL,
                                error_category=exc.category,
                                safe_diagnostic=_diagnostic(exc.category),
                                staged_artifact="",
                                staged_sha256="",
                            )
                        )
                    return self._save(
                        latest.transition(
                            UpdateState.FAILED_RETRIABLE,
                            error_category=exc.category,
                            safe_diagnostic=_diagnostic(exc.category),
                        )
                    )
        except InterprocessLockTimeout as exc:
            raise UpdateError("operation_busy", retriable=True) from exc
        policy = self.policy()
        if policy.automatic_install_on_quit:
            return self.install(ready.operation_id, mode="on_quit")
        return ready

    def defer(self, operation_id: str) -> UpdateOperation:
        try:
            with self.store.operation_guard():
                current = self._current_for(operation_id)
                if current.state is UpdateState.DEFERRED:
                    return current
                if current.state not in {
                    UpdateState.AVAILABLE,
                    UpdateState.READY_TO_INSTALL,
                    UpdateState.WAITING_FOR_IDLE,
                    UpdateState.INSTALL_ON_QUIT,
                }:
                    raise UpdateError("operation_not_deferrable")
                self.store.update_policy(
                    last_user_decision="later",
                    last_user_decision_at=self._now().isoformat(),
                )
                return self._save(
                    current.transition(
                        UpdateState.DEFERRED,
                        desired_state="later",
                        error_category="",
                        safe_diagnostic="",
                    )
                )
        except InterprocessLockTimeout as exc:
            raise UpdateError("operation_busy", retriable=True) from exc

    def resume(self, operation_id: str) -> UpdateOperation:
        try:
            with self.store.operation_guard():
                current = self._current_for(operation_id)
                if current.state is not UpdateState.DEFERRED:
                    return current
                target = (
                    UpdateState.READY_TO_INSTALL
                    if current.staged_artifact and current.staged_sha256
                    else UpdateState.AVAILABLE
                )
                return self._save(
                    current.transition(
                        target,
                        desired_state="",
                        error_category="",
                        safe_diagnostic="",
                    )
                )
        except InterprocessLockTimeout as exc:
            raise UpdateError("operation_busy", retriable=True) from exc

    def skip(self, operation_id: str) -> UpdateOperation:
        try:
            with self.store.operation_guard():
                current = self._current_for(operation_id)
                if current.state is UpdateState.SKIPPED:
                    return current
                if current.state is not UpdateState.AVAILABLE:
                    raise UpdateError("operation_not_skippable")
                candidate = UpdateCandidate.from_dict(current.candidate)
                self.store.update_policy(
                    skipped_version=candidate.version,
                    last_user_decision="skip",
                    last_user_decision_at=self._now().isoformat(),
                )
                return self._save(
                    current.transition(UpdateState.SKIPPED, desired_state="skip")
                )
        except InterprocessLockTimeout as exc:
            raise UpdateError("operation_busy", retriable=True) from exc

    def install(
        self,
        operation_id: str,
        *,
        mode: str,
        consequence_accepted: bool = False,
    ) -> UpdateOperation:
        if mode not in {"now", "when_idle", "on_quit"}:
            raise UpdateError("install_mode_invalid")
        try:
            with self.store.operation_guard():
                current = self._current_for(operation_id)
                policy = self.policy()
                if not policy.installation_allowed:
                    if current.state in {
                        UpdateState.READY_TO_INSTALL,
                        UpdateState.WAITING_FOR_IDLE,
                        UpdateState.INSTALL_ON_QUIT,
                    }:
                        return self._save(
                            current.transition(
                                UpdateState.POLICY_BLOCKED,
                                error_category="installation_owned_elsewhere",
                                safe_diagnostic=(
                                    "Updates for this installation are managed outside Vesta."
                                ),
                            )
                        )
                    return current
                if mode == "on_quit" and current.state in {
                    UpdateState.READY_TO_INSTALL,
                    UpdateState.WAITING_FOR_IDLE,
                }:
                    return self._save(current.transition(UpdateState.INSTALL_ON_QUIT))
                if (
                    mode == "when_idle"
                    and current.state is UpdateState.READY_TO_INSTALL
                ):
                    return self._save(current.transition(UpdateState.WAITING_FOR_IDLE))
                if current.state not in {
                    UpdateState.READY_TO_INSTALL,
                    UpdateState.WAITING_FOR_IDLE,
                    UpdateState.INSTALL_ON_QUIT,
                }:
                    if current.state in {
                        UpdateState.RESTARTING,
                        UpdateState.HEALTH_CHECKING,
                        UpdateState.COMPLETED,
                    }:
                        return current
                    raise UpdateError("operation_not_installable")
                active = self.runtime_probe()
                if not active.safe_to_install:
                    if current.state is UpdateState.READY_TO_INSTALL:
                        current = current.transition(UpdateState.WAITING_FOR_IDLE)
                    return self._save(
                        replace(
                            current,
                            error_category="active_work_blocked",
                            safe_diagnostic=_diagnostic("active_work_blocked"),
                        )
                    )
                candidate = UpdateCandidate.from_dict(current.candidate)
                staged = Path(current.staged_artifact)
                if (
                    current.staged_sha256 != candidate.artifact_sha256
                    or not _contained_regular_file(
                        staged, self.store.paths.staging, current.staged_sha256
                    )
                ):
                    if current.state is UpdateState.WAITING_FOR_IDLE:
                        current = current.transition(UpdateState.INSTALLING)
                    elif current.state is UpdateState.INSTALL_ON_QUIT:
                        current = current.transition(UpdateState.INSTALLING)
                    else:
                        current = current.transition(UpdateState.INSTALLING)
                    return self._save(
                        current.transition(
                            UpdateState.FAILED_TERMINAL,
                            error_category="staged_artifact_substituted",
                            safe_diagnostic="The staged update changed after verification.",
                        )
                    )
                previous = current.last_known_good
                recovery_path = Path(str(previous.get("artifact_path") or ""))
                recovery_digest = str(previous.get("artifact_sha256") or "")
                if (
                    not candidate.rollback_compatible
                    or previous.get("version") != self.installed.version
                    or previous.get("build_id") != self.installed.build_id
                    or previous.get("install_type") != self.installed.install_type.value
                    or previous.get("publisher_identity")
                    != self.installed.publisher_identity
                    or not _contained_regular_file(
                        recovery_path,
                        self.store.paths.staging,
                        recovery_digest,
                    )
                ):
                    installing = self._save(
                        current.transition(
                            UpdateState.INSTALLING,
                            error_category="",
                            safe_diagnostic="",
                        )
                    )
                    return self._save(
                        installing.transition(
                            UpdateState.FAILED_TERMINAL,
                            error_category="recovery_unavailable",
                            safe_diagnostic=_diagnostic("recovery_unavailable"),
                        )
                    )
                installing = self._save(
                    current.transition(
                        UpdateState.INSTALLING,
                        error_category="",
                        safe_diagnostic="",
                    )
                )
                result = self.adapter.install(staged, candidate, mode=mode)
                if not result.started:
                    category = result.error_category or "installer_failed"
                    return self._save(
                        installing.transition(
                            UpdateState.FAILED_TERMINAL,
                            error_category=category,
                            safe_diagnostic=_diagnostic(category),
                        )
                    )
                return self._save(
                    installing.transition(
                        UpdateState.RESTARTING,
                        native_transaction=result.transaction_id,
                        native_result_path=result.result_path,
                        native_action="install",
                        health_deadline_at=(
                            self._now() + timedelta(minutes=5)
                        ).isoformat(),
                    )
                )
        except InterprocessLockTimeout as exc:
            raise UpdateError("operation_busy", retriable=True) from exc

    def install_on_quit(self) -> UpdateOperation:
        current = self.store.load_operation()
        if current.state is not UpdateState.INSTALL_ON_QUIT:
            return current
        return self.install(current.operation_id, mode="on_quit")

    def reconcile_native_result(self) -> UpdateOperation:
        """Persist a completed native helper failure without doing network work."""

        current = self.store.load_operation()
        if current.state not in {UpdateState.RESTARTING, UpdateState.ROLLING_BACK}:
            return current
        native_result = self._native_result(current)
        if not native_result or native_result.get("success") is not False:
            return current
        category = str(native_result.get("error_category") or "installer_failed")
        try:
            with self.store.operation_guard():
                latest = self._current_for(current.operation_id)
                target = (
                    UpdateState.FAILED_RETRIABLE
                    if latest.state is UpdateState.RESTARTING
                    else UpdateState.NEEDS_ATTENTION
                )
                return self._save(
                    latest.transition(
                        target,
                        error_category=category,
                        safe_diagnostic=_diagnostic(category),
                    )
                )
        except InterprocessLockTimeout as exc:
            raise UpdateError("operation_busy", retriable=True) from exc

    def _restart_already_happened(self, operation: UpdateOperation) -> bool:
        """Whether a COMPLETED operation is still waiting on a restart.

        A source fast-forward finishes with the new code on disk and the old
        code still loaded in memory, so it lands COMPLETED saying "restart to
        use it". Nothing cleared that. The next check falls inside the
        four-hour minimum interval, so ``check(force=False)`` returns the same
        operation untouched -- and the banner asking for a restart survives
        the restart it asked for, for up to four hours, or until someone hits
        Check again hard enough to force a real check. That is the bug.

        The test is the process itself: if this process started *after* the
        update was recorded, the restart it asked for has happened. Packaged
        completions are unaffected -- they carry no diagnostic, so there was
        never a banner to clear.
        """

        if operation.state is not UpdateState.COMPLETED:
            return False
        if not operation.safe_diagnostic:
            return False
        recorded = _parse_time(operation.updated_at)
        return recorded is not None and recorded < _PROCESS_STARTED_AT

    _STALE_PROCESS_DIAGNOSTIC = (
        "Vesta was updated on disk. Restart to use the new version."
    )

    def _running_build_is_stale(self) -> bool:
        """Whether the files on disk moved on from what this process loaded."""

        try:
            from pathlib import Path as _Path

            import opai
            from .running_build import running_build_is_stale

            return running_build_is_stale(_Path(opai.__file__).parent / "assets")
        except Exception:  # noqa: BLE001 - a staleness hint may never break maintenance
            return False

    def maintain(self) -> UpdateOperation:
        """Advance periodic discovery and persisted safe-boundary work."""

        current = self.reconcile_native_result()
        # The disk moved on without us.
        #
        # `check()` asks whether the *checkout* is behind its remote, and after
        # an update it correctly says no -- while the window in front of the
        # user still runs whatever it loaded at launch. Nothing compared those
        # two, so a session could sit for hours showing a UI several merges old
        # with every surface agreeing there was nothing to update.
        #
        # COMPLETED is the honest state for that: installed, not yet running.
        # It is also where the surface offers Restart now.
        if (
            current.state
            not in {
                UpdateState.COMPLETED,
                UpdateState.RESTARTING,
                UpdateState.HEALTH_CHECKING,
                UpdateState.ROLLING_BACK,
                UpdateState.DOWNLOADING,
                UpdateState.VERIFYING,
                UpdateState.INSTALLING,
            }
            and self._running_build_is_stale()
        ):
            try:
                with self.store.operation_guard():
                    latest = self._current_for(current.operation_id)
                    checking = self._save(latest.transition(UpdateState.CHECKING))
                    return self._save(
                        checking.transition(
                            UpdateState.COMPLETED,
                            safe_diagnostic=self._STALE_PROCESS_DIAGNOSTIC,
                            error_category="",
                            progress_label="",
                        )
                    )
            except (InterprocessLockTimeout, UpdateError):
                return current
        if self._restart_already_happened(current):
            try:
                with self.store.operation_guard():
                    latest = self._current_for(current.operation_id)
                    if self._restart_already_happened(latest):
                        checking = self._save(latest.transition(UpdateState.CHECKING))
                        return self._save(
                            checking.transition(
                                UpdateState.UP_TO_DATE,
                                safe_diagnostic="",
                                error_category="",
                                progress_label="",
                                downloaded_bytes=0,
                                total_bytes=0,
                            )
                        )
            except (InterprocessLockTimeout, UpdateError):
                # Another surface is mid-operation; the banner is stale, not
                # harmful, and the next maintenance cycle clears it.
                return current
        if current.state in {UpdateState.RESTARTING, UpdateState.ROLLING_BACK}:
            deadline = _parse_time(current.health_deadline_at)
            if deadline is not None and self._now() >= deadline:
                if current.state is UpdateState.ROLLING_BACK:
                    with self.store.operation_guard():
                        latest = self._current_for(current.operation_id)
                        return self._save(
                            latest.transition(
                                UpdateState.NEEDS_ATTENTION,
                                error_category="rollback_health_timeout",
                                safe_diagnostic=(
                                    "The recovery build did not confirm health before its deadline."
                                ),
                            )
                        )
                with self.store.operation_guard():
                    latest = self._current_for(current.operation_id)
                    checking = self._save(
                        latest.transition(UpdateState.HEALTH_CHECKING)
                    )
                    candidate = UpdateCandidate.from_dict(checking.candidate)
                    pending = self._save(
                        checking.transition(
                            UpdateState.ROLLBACK_PENDING,
                            quarantined_versions=tuple(
                                dict.fromkeys(
                                    (*checking.quarantined_versions, candidate.version)
                                )
                            ),
                            error_category="health_startup_timeout",
                            safe_diagnostic=(
                                "The updated application did not confirm health before its deadline."
                            ),
                        )
                    )
                return self.rollback(pending.operation_id)
            return current
        if current.state is UpdateState.WAITING_FOR_IDLE:
            if self.runtime_probe().safe_to_install:
                return self.install(current.operation_id, mode="when_idle")
            return current
        if current.state in {
            UpdateState.IDLE,
            UpdateState.UP_TO_DATE,
            UpdateState.AVAILABLE,
            UpdateState.UNAVAILABLE,
            UpdateState.UNSUPPORTED_INSTALL,
            UpdateState.POLICY_BLOCKED,
            UpdateState.DEFERRED,
            UpdateState.SKIPPED,
            UpdateState.COMPLETED,
            UpdateState.ROLLED_BACK,
            UpdateState.FAILED_RETRIABLE,
            UpdateState.FAILED_TERMINAL,
            UpdateState.NEEDS_ATTENTION,
        }:
            return self.check(force=False, allow_automatic_download=True)
        return current

    def _native_result(self, operation: UpdateOperation) -> dict[str, object]:
        if not operation.native_result_path:
            return {}
        path = Path(operation.native_result_path)
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(self.store.paths.staging.resolve(strict=True))
            linked = path.is_symlink() or (
                hasattr(path, "is_junction") and path.is_junction()
            )
        except (OSError, ValueError):
            return {}
        if linked or not resolved.is_file():
            return {}
        try:
            if resolved.stat().st_size > 64 * 1024:
                return {}
            value = json.loads(resolved.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return (
            value
            if isinstance(value, dict) and value.get("schema_version") == 1
            else {}
        )

    def startup_health(self, assets_root: Path) -> dict[str, bool]:
        """Run local, side-effect-free startup checks over real packaged state."""

        assets = Path(assets_root)
        required = ("index.html", "app.js", "settings.js", "styles.css")
        assets_ok = all((assets / name).is_file() for name in required)
        try:
            doctor_ok = bool(self.adapter.doctor(self.installed).get("available"))
        except Exception:  # noqa: BLE001 - converted to a health fact
            doctor_ok = False
        return {
            "assets_ok": assets_ok,
            "state_schema_ok": self.store.state_schema_ok(),
            "doctor_ok": doctor_ok,
        }

    def confirm_health(
        self,
        operation_id: str,
        *,
        running: InstalledBuild,
        interactive: bool,
        assets_ok: bool,
        state_schema_ok: bool,
        doctor_ok: bool,
    ) -> UpdateOperation:
        try:
            with self.store.operation_guard():
                current = self._current_for(operation_id)
                if current.state is UpdateState.COMPLETED:
                    return current
                if current.state is not UpdateState.RESTARTING:
                    raise UpdateError("health_not_expected")
                checking = self._save(current.transition(UpdateState.HEALTH_CHECKING))
                candidate = UpdateCandidate.from_dict(checking.candidate)
                category = ""
                if (
                    running.version != candidate.version
                    or running.build_id != candidate.build_id
                ):
                    category = "health_build_mismatch"
                elif not interactive:
                    category = "health_not_interactive"
                elif not assets_ok:
                    category = "health_assets_failed"
                elif not state_schema_ok:
                    category = "health_state_failed"
                elif not doctor_ok:
                    category = "health_doctor_failed"
                if not category:
                    installed_record = running.to_dict()
                    if checking.staged_artifact and checking.staged_sha256:
                        installed_record.update(
                            {
                                "artifact_path": checking.staged_artifact,
                                "artifact_sha256": checking.staged_sha256,
                            }
                        )
                    return self._save(
                        checking.transition(
                            UpdateState.COMPLETED,
                            installed_build=installed_record,
                            error_category="",
                            safe_diagnostic="",
                        )
                    )
                quarantined = tuple(
                    dict.fromkeys((*checking.quarantined_versions, candidate.version))
                )
                return self._save(
                    checking.transition(
                        UpdateState.ROLLBACK_PENDING,
                        quarantined_versions=quarantined,
                        error_category=category,
                        safe_diagnostic="The updated application did not pass its startup health check.",
                    )
                )
        except InterprocessLockTimeout as exc:
            raise UpdateError("operation_busy", retriable=True) from exc

    def rollback(self, operation_id: str) -> UpdateOperation:
        try:
            with self.store.operation_guard():
                current = self._current_for(operation_id)
                if current.state is UpdateState.ROLLED_BACK:
                    return current
                if current.state not in {
                    UpdateState.ROLLBACK_PENDING,
                    UpdateState.NEEDS_ATTENTION,
                }:
                    raise UpdateError("rollback_not_available")
                rolling = self._save(current.transition(UpdateState.ROLLING_BACK))
                recovery_path = str(rolling.last_known_good.get("artifact_path") or "")
                if recovery_path:
                    artifact = Path(recovery_path)
                    try:
                        artifact.resolve(strict=True).relative_to(
                            self.store.paths.staging.resolve(strict=True)
                        )
                        linked = artifact.is_symlink() or (
                            hasattr(artifact, "is_junction") and artifact.is_junction()
                        )
                        expected = str(
                            rolling.last_known_good.get("artifact_sha256") or ""
                        )
                        valid_recovery = (
                            not linked
                            and len(expected) == 64
                            and _sha256(artifact) == expected
                        )
                    except (OSError, ValueError):
                        valid_recovery = False
                    if not valid_recovery:
                        return self._save(
                            rolling.transition(
                                UpdateState.NEEDS_ATTENTION,
                                error_category="rollback_artifact_invalid",
                                safe_diagnostic="The last-known-good recovery package could not be verified.",
                            )
                        )
                result = self.adapter.rollback(rolling.last_known_good)
                if result.started:
                    return self._save(
                        replace(
                            rolling,
                            native_transaction=result.transaction_id,
                            native_result_path=result.result_path,
                            native_action="rollback",
                            health_deadline_at=(
                                self._now() + timedelta(minutes=5)
                            ).isoformat(),
                        )
                    )
                return self._save(
                    rolling.transition(
                        UpdateState.NEEDS_ATTENTION,
                        error_category=result.error_category or "rollback_failed",
                        safe_diagnostic=_diagnostic("rollback_failed"),
                    )
                )
        except InterprocessLockTimeout as exc:
            raise UpdateError("operation_busy", retriable=True) from exc

    def confirm_recovery(
        self,
        operation_id: str,
        *,
        running: InstalledBuild,
        interactive: bool,
        assets_ok: bool,
        state_schema_ok: bool,
        doctor_ok: bool,
    ) -> UpdateOperation:
        """Claim rollback only after the restored process proves exact health."""

        try:
            with self.store.operation_guard():
                current = self._current_for(operation_id)
                if current.state is UpdateState.ROLLED_BACK:
                    return current
                if current.state is not UpdateState.ROLLING_BACK:
                    raise UpdateError("recovery_health_not_expected")
                expected = current.last_known_good
                healthy = (
                    running.version == str(expected.get("version") or "")
                    and running.build_id == str(expected.get("build_id") or "")
                    and interactive
                    and assets_ok
                    and state_schema_ok
                    and doctor_ok
                )
                if healthy:
                    return self._save(
                        current.transition(
                            UpdateState.ROLLED_BACK,
                            error_category="",
                            safe_diagnostic="",
                        )
                    )
                return self._save(
                    current.transition(
                        UpdateState.NEEDS_ATTENTION,
                        error_category="rollback_health_failed",
                        safe_diagnostic=(
                            "The recovery build did not pass its startup health check."
                        ),
                    )
                )
        except InterprocessLockTimeout as exc:
            raise UpdateError("operation_busy", retriable=True) from exc

    def check_now(self) -> dict[str, object]:
        """The manual path: remote evidence, or an explicit reason there is none.

        `check()` already raises honestly when it cannot run, but every caller
        was free to swallow that and re-read `status()` -- which the desktop
        surface did, so a user could press Check for updates, have nothing
        happen at all, and be shown the previous "up to date" with no way to
        tell the difference. That is the one thing an update surface must never
        do, because the updater is how fixes arrive.

        The outcome is returned rather than persisted: it belongs to the click,
        not to the installation.
        """

        try:
            operation = self.check(
                trigger=UpdateTrigger.MANUAL, allow_automatic_download=True
            )
        except UpdateError as exc:
            return {
                "ok": False,
                "reason": exc.category,
                "message": _MANUAL_FAILURE_MESSAGES.get(
                    exc.category, "Vesta could not check for updates just now."
                ),
            }
        if operation.result_from_cache:
            # Belt and braces: a manual check answered from cache would be a
            # contract violation, and silence is how it would go unnoticed.
            return {
                "ok": False,
                "reason": "cached_result",
                "message": "Vesta could not confirm this with the update source.",
            }
        if operation.state is UpdateState.UNAVAILABLE:
            return {
                "ok": False,
                "reason": operation.error_category or "unavailable",
                "message": operation.safe_diagnostic
                or "Vesta couldn't reach the update source.",
            }
        return {"ok": True, "reason": "", "message": "", "state": operation.state.value}

    def status(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "installed": self.installed.to_dict(),
            "policy": self.policy().to_dict(),
            "operation": self.store.load_operation().to_public_dict(),
            "restart_available": self.restart_available(),
            "discovery": self.discovery_diagnostics(),
        }

    def discovery_diagnostics(self) -> dict[str, object]:
        """Everything needed to answer "why did it not notice?" in one place.

        The canonical facts, computed once and shared by the GUI, the CLI and
        doctor -- there is deliberately no second interpretation layer, because
        two answers to "when did it last check" is how the first one stops
        being trusted.
        """

        policy = self.policy()
        operation = self.store.load_operation()
        interval = int(_jittered_check_interval(policy))
        checked = _parse_time(operation.last_successful_check_at)
        next_eligible = (
            (checked + timedelta(seconds=interval)).isoformat() if checked else ""
        )
        source = "signed release feed"
        if isinstance(self.adapter, DeveloperGitUpdateAdapter):
            source = "origin/main"
        elif policy.owner is not UpdateOwner.OPAI:
            source = f"managed by {policy.owner.value}"
        ownership = describe_ownership(
            self.installed.install_type, management_source=policy.management_source
        )
        running = running_identity()
        # Running and disk identity are separate facts. `installed.version`
        # comes from release-identity.json when that file exists, which an
        # update rewrites -- so it can report the new version while this
        # process is still the old build. Reported apart, and the disk one only
        # when it actually differs, so no surface can claim a version is
        # installed while the code answering the question is not it.
        disk_version = str(self.installed.version)
        disk_build = str(self.installed.build_id)
        stale_process = bool(
            running["version"] and disk_version and running["version"] != disk_version
        )
        diagnostics: dict[str, object] = {
            "install_type": self.installed.install_type.value,
            "channel": policy.channel,
            "update_owner": policy.owner.value,
            "update_source": source,
            "running_version": running["version"],
            "running_build_id": running["build_id"],
            "disk_version": disk_version if stale_process else "",
            "disk_build_id": disk_build if stale_process else "",
            "version_differs_from_disk": stale_process,
            "launcher": safe_launcher_identity(),
            "ownership": ownership.to_dict(),
            "self_updatable": ownership.self_updatable,
            "cadence_seconds": int(policy.minimum_check_interval_seconds),
            "cadence_reason": cadence_reason(
                self.installed.install_type, policy.channel
            ),
            "effective_interval_seconds": interval,
            # The four distinct facts. A surface that shows "checked just now"
            # must read `remote_checked_at`, never `last_check_at`.
            "last_scheduler_tick_at": operation.scheduler_tick_at,
            "last_check_attempt_at": operation.last_check_at,
            "last_remote_check_at": operation.remote_checked_at,
            "last_successful_remote_check_at": operation.last_successful_check_at,
            "showing_cached_result": bool(operation.result_from_cache),
            "next_remote_check_eligible_at": operation.next_check_eligible_at
            or next_eligible,
            "last_trigger": operation.last_trigger,
            "retry_count": int(operation.retry_count),
            "next_retry_at": operation.next_retry_at,
            "state": operation.state.value,
            "discovery_allowed": bool(policy.discovery_allowed),
            "automatic_downloads": bool(policy.automatic_downloads),
            "automatic_install_on_quit": bool(policy.automatic_install_on_quit),
            "restart_required": operation.state is UpdateState.COMPLETED,
            "restart_available": self.restart_available(),
        }
        # Rendered here, not in the browser.
        #
        # The desktop needs the same sentence the CLI prints, and there must be
        # exactly one place that decides what "checked remotely 2 min ago"
        # means. A JavaScript copy of that rule would be a second
        # interpretation layer, and the two would drift the first time either
        # was touched -- which is the failure this whole epic is about.
        # What the surface may say. Timings, cache provenance and commands are
        # not in here on purpose -- they are diagnostics, and they belong to
        # `vesta update doctor`, not to someone who just wants the new version.
        diagnostics["summary"] = user_facing(
            {"operation": operation.to_public_dict(), "discovery": diagnostics}
        )
        return diagnostics

    def doctor(self) -> dict[str, object]:
        operation = self.store.load_operation()
        rollback_available = bool(
            operation.last_known_good.get("artifact_path")
            and operation.last_known_good.get("artifact_sha256")
        )
        return {
            **self.status(),
            "native": self.adapter.doctor(self.installed),
            "rollback_available": rollback_available,
            "state_scope": "application",
        }
