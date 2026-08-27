"""Immutable verification evidence and verdicts for managed OPai work (#539)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import subprocess  # nosec B404 - commands are validated argv and use shell=False
import sys
import time
from typing import Any, Callable, Iterable, Mapping
import uuid

from .command_runner import redact
from . import shadow_journal
from .atomic_io import atomic_write_text, interprocess_transaction
from .process_tree import adopt, isolated_group_kwargs, terminate_tree
from .state import state_dir
from .verification_policy import PolicyCheck, VerificationPolicy


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    WAIVED = "waived"
    ARTIFACT_LOST = "artifact_lost"


class VerificationVerdict(str, Enum):
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ArtifactReference:
    path: str
    digest: str

    def __post_init__(self) -> None:
        path = str(self.path or "").replace("\\", "/").strip("/")
        if (
            not path
            or path.startswith("../")
            or "/../" in path
            or Path(path).is_absolute()
        ):
            raise ValueError("artifact path must be relative and contained")
        digest = str(self.digest or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("artifact digest must be a SHA-256 hex value")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "digest", digest)

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "digest": self.digest}


@dataclass(frozen=True)
class EvidenceManifestReference:
    path: Path
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).expanduser().resolve())
        digest = str(self.digest or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("manifest digest must be a SHA-256 hex value")
        object.__setattr__(self, "digest", digest)

    def to_dict(self) -> dict[str, str]:
        return {"path": str(self.path), "digest": self.digest}


def _identifier(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_IDENTIFIER.fullmatch(text):
        raise ValueError(
            f"{field_name} must contain only letters, digits, '.', '_' or '-'"
        )
    return text


def _sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _redacted_policy_payload(policy: VerificationPolicy) -> dict[str, Any]:
    """Keep policy provenance while ensuring argv never persists a secret."""

    payload = policy.to_dict()
    for check in payload.get("checks") or ():
        if isinstance(check, dict):
            check["command"] = [
                redact(str(part)) for part in check.get("command") or ()
            ]
    return payload


def _file_digest(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > 10_000_000:
            return ""
        return sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _environment_fingerprint(
    worktree: Path, *, repository_id: str, head_sha: str
) -> dict[str, Any]:
    """Portable, content-free facts needed to reproduce a verification run."""

    lock_names = (
        "poetry.lock",
        "uv.lock",
        "requirements.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "Cargo.lock",
    )
    config_names = (
        "pyproject.toml",
        "opai-verification-policy.yaml",
        "opai-team-policy.yaml",
    )
    return {
        "os": platform.system() or os.name,
        "architecture": platform.machine() or "unknown",
        "python": platform.python_version(),
        "python_executable": str(Path(sys.executable).resolve()),
        "repository_id": repository_id,
        "head_sha": head_sha,
        "dependency_locks": {
            name: digest
            for name in lock_names
            if (digest := _file_digest(worktree / name))
        },
        "config_digests": {
            name: digest
            for name in config_names
            if (digest := _file_digest(worktree / name))
        },
    }


@dataclass(frozen=True)
class VerificationExecutionContext:
    """Task/run binding and canonical repository facts for check execution."""

    task_id: str
    run_id: str
    worktree: Path
    repository_id: str
    head_sha: str
    environment_fingerprint: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "task_id", _identifier(self.task_id, field_name="task_id")
        )
        object.__setattr__(
            self, "run_id", _identifier(self.run_id, field_name="run_id")
        )
        root = Path(self.worktree).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("worktree must be an existing directory")
        object.__setattr__(self, "worktree", root)
        repository_id = str(self.repository_id or "").strip()
        if not repository_id:
            raise ValueError("repository_id is required")
        object.__setattr__(self, "repository_id", repository_id[:256])
        head_sha = str(self.head_sha or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40,64}", head_sha):
            raise ValueError("head_sha must be a Git SHA")
        object.__setattr__(self, "head_sha", head_sha)
        fingerprint = dict(self.environment_fingerprint or {})
        if not fingerprint:
            fingerprint = _environment_fingerprint(
                root, repository_id=repository_id, head_sha=head_sha
            )
        object.__setattr__(self, "environment_fingerprint", fingerprint)

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "worktree": str(self.worktree),
            "repository_id": self.repository_id,
            "head_sha": self.head_sha,
            "environment_fingerprint": self.environment_fingerprint,
        }

    @classmethod
    def from_repository_handle(cls, handle: Any) -> "VerificationExecutionContext":
        """Bind check execution to the canonical worktree captured by #521."""

        identity = getattr(handle, "identity", None)
        if identity is None:
            raise ValueError("repository handle is required for verification")
        return cls(
            task_id=getattr(handle, "task_id", ""),
            run_id=getattr(handle, "run_id", ""),
            worktree=getattr(identity, "worktree_root", ""),
            repository_id=getattr(identity, "repository_id", ""),
            head_sha=getattr(identity, "head_sha", ""),
        )


@dataclass(frozen=True)
class VerificationAttempt:
    """One observed check attempt; provider text is never an input."""

    check_id: str
    index: int
    status: CheckStatus
    command: tuple[str, ...]
    working_directory: Path
    started_at: datetime
    ended_at: datetime
    exit_status: int | None
    output_summary: str
    environment_digest: str
    teardown_verified: bool
    artifacts: tuple[ArtifactReference, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "check_id", _identifier(self.check_id, field_name="check_id")
        )
        if isinstance(self.index, bool) or self.index < 1:
            raise ValueError("attempt index must be positive")
        if not isinstance(self.status, CheckStatus):
            object.__setattr__(self, "status", CheckStatus(str(self.status)))
        command = tuple(str(part).strip() for part in self.command)
        if any(not part for part in command):
            raise ValueError("command entries must be non-empty")
        object.__setattr__(self, "command", command)
        object.__setattr__(
            self,
            "working_directory",
            Path(self.working_directory).expanduser().resolve(),
        )
        if self.ended_at < self.started_at:
            raise ValueError("attempt end precedes start")
        if self.exit_status is not None and (
            isinstance(self.exit_status, bool) or not isinstance(self.exit_status, int)
        ):
            raise ValueError("exit_status must be an integer or null")
        summary = str(self.output_summary or "").strip()
        if not summary:
            raise ValueError("output_summary is required")
        object.__setattr__(self, "output_summary", summary[:2_000])
        digest = str(self.environment_digest or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("environment_digest must be a SHA-256 hex value")
        object.__setattr__(self, "environment_digest", digest)
        artifacts = tuple(
            item if isinstance(item, ArtifactReference) else ArtifactReference(**item)
            for item in self.artifacts
        )
        object.__setattr__(self, "artifacts", artifacts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "index": self.index,
            "status": self.status.value,
            "command": [redact(part) for part in self.command],
            "working_directory": str(self.working_directory),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "exit_status": self.exit_status,
            "output_summary": self.output_summary,
            "environment_digest": self.environment_digest,
            "teardown_verified": self.teardown_verified,
            "artifacts": [item.to_dict() for item in self.artifacts],
        }


@dataclass(frozen=True)
class CheckRecord:
    """All immutable attempts plus an explicit skipped/waived state for one check."""

    check_id: str
    kind: str
    requirement: str
    attempts: tuple[VerificationAttempt, ...] = ()
    status: CheckStatus | None = None
    waiver: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "check_id", _identifier(self.check_id, field_name="check_id")
        )
        attempts = tuple(self.attempts)
        if any(item.check_id != self.check_id for item in attempts):
            raise ValueError("attempt check_id must match record")
        if tuple(item.index for item in attempts) != tuple(range(1, len(attempts) + 1)):
            raise ValueError("attempt indexes must be contiguous from one")
        object.__setattr__(self, "attempts", attempts)
        if self.status is not None and not isinstance(self.status, CheckStatus):
            object.__setattr__(self, "status", CheckStatus(str(self.status)))
        if (
            attempts
            and self.status is not None
            and self.status is not attempts[-1].status
        ):
            raise ValueError("record status must match the final attempt")
        if not attempts and self.status not in {
            CheckStatus.SKIPPED,
            CheckStatus.WAIVED,
            CheckStatus.UNAVAILABLE,
            CheckStatus.BLOCKED,
            CheckStatus.ARTIFACT_LOST,
            None,
        }:
            raise ValueError("terminal record status requires an attempt")
        waiver = {str(key): str(value) for key, value in dict(self.waiver).items()}
        if self.status is CheckStatus.WAIVED:
            required = {"actor", "reason", "policy_source", "scope"}
            if required - set(waiver) or any(
                not waiver[key].strip() for key in required
            ):
                raise ValueError(
                    "waived checks require actor, reason, policy_source and scope"
                )
        elif waiver:
            raise ValueError("only waived checks may carry a waiver")
        object.__setattr__(self, "waiver", waiver)

    @classmethod
    def from_attempts(
        cls, check: PolicyCheck, attempts: Iterable[VerificationAttempt]
    ) -> "CheckRecord":
        values = tuple(attempts)
        return cls(
            check_id=check.check_id,
            kind=check.kind,
            requirement=check.requirement,
            attempts=values,
            status=values[-1].status if values else None,
        )

    @classmethod
    def waived(
        cls,
        check: PolicyCheck,
        *,
        actor: str,
        reason: str,
        policy_source: str,
        scope: str,
    ) -> "CheckRecord":
        return cls(
            check_id=check.check_id,
            kind=check.kind,
            requirement=check.requirement,
            status=CheckStatus.WAIVED,
            waiver={
                "actor": actor,
                "reason": reason,
                "policy_source": policy_source,
                "scope": scope,
            },
        )

    @property
    def flake_suspected(self) -> bool:
        return bool(
            self.attempts
            and self.attempts[-1].status is CheckStatus.PASSED
            and any(
                item.status is not CheckStatus.PASSED for item in self.attempts[:-1]
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "kind": self.kind,
            "requirement": self.requirement,
            "status": self.status.value if self.status is not None else "missing",
            "attempts": [item.to_dict() for item in self.attempts],
            "flake_suspected": self.flake_suspected,
            "waiver": dict(self.waiver),
        }


@dataclass(frozen=True)
class VerificationManifest:
    """Digest-bound evidence required to reproduce a verification verdict."""

    policy: dict[str, Any]
    context: VerificationExecutionContext
    checks: tuple[CheckRecord, ...]
    digest: str = ""
    integrity_errors: tuple[str, ...] = field(default_factory=tuple, compare=False)

    def __post_init__(self) -> None:
        policy = dict(self.policy)
        policy_digest = str(policy.get("digest") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", policy_digest):
            raise ValueError("policy digest is required")
        checks = tuple(self.checks)
        if len({item.check_id for item in checks}) != len(checks):
            raise ValueError("manifest has duplicate check records")
        known = {
            str(item.get("id") or "")
            for item in policy.get("checks") or ()
            if isinstance(item, dict)
        }
        if any(item.check_id not in known for item in checks):
            raise ValueError("manifest record is not declared by policy")
        object.__setattr__(self, "policy", policy)
        object.__setattr__(self, "checks", checks)
        object.__setattr__(
            self,
            "integrity_errors",
            tuple(str(item)[:300] for item in self.integrity_errors),
        )
        computed = _sha256(self.payload_without_digest())
        supplied = str(self.digest or "").lower()
        if supplied and supplied != computed:
            raise ValueError("manifest digest does not match content")
        object.__setattr__(self, "digest", computed)

    @classmethod
    def from_policy(
        cls,
        policy: VerificationPolicy,
        context: VerificationExecutionContext,
        checks: Iterable[CheckRecord],
        *,
        digest: str = "",
    ) -> "VerificationManifest":
        if not isinstance(policy, VerificationPolicy):
            raise TypeError("policy must be a VerificationPolicy")
        return cls(
            policy=_redacted_policy_payload(policy),
            context=context,
            checks=tuple(checks),
            digest=digest,
        )

    def payload_without_digest(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "context": self.context.to_dict(),
            "checks": [item.to_dict() for item in self.checks],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.payload_without_digest(),
            "digest": self.digest,
            **(
                {"integrity_errors": list(self.integrity_errors)}
                if self.integrity_errors
                else {}
            ),
        }


def verification_verdict(manifest: VerificationManifest) -> VerificationVerdict:
    """Derive terminal verification truth from policy requirements and evidence."""

    if not isinstance(manifest, VerificationManifest):
        raise TypeError("manifest must be a VerificationManifest")
    if manifest.integrity_errors:
        return VerificationVerdict.UNVERIFIED
    records = {item.check_id: item for item in manifest.checks}
    required = [
        item
        for item in manifest.policy.get("checks") or ()
        if isinstance(item, dict) and item.get("requirement") == "required"
    ]
    statuses: list[CheckStatus | None] = []
    partial = False
    for check in required:
        record = records.get(str(check.get("id") or ""))
        if record is None or record.status is None:
            return VerificationVerdict.UNVERIFIED
        statuses.append(record.status)
        partial = (
            partial
            or record.flake_suspected
            or record.status
            in {
                CheckStatus.WAIVED,
                CheckStatus.SKIPPED,
            }
        )
    if CheckStatus.CANCELLED in statuses:
        return VerificationVerdict.CANCELLED
    if CheckStatus.TIMEOUT in statuses:
        return VerificationVerdict.TIMEOUT
    if CheckStatus.FAILED in statuses:
        return VerificationVerdict.FAILED
    if CheckStatus.BLOCKED in statuses:
        return VerificationVerdict.BLOCKED
    if CheckStatus.UNAVAILABLE in statuses or CheckStatus.ARTIFACT_LOST in statuses:
        return VerificationVerdict.UNVERIFIED
    if partial:
        return VerificationVerdict.PARTIALLY_VERIFIED
    if any(status is not CheckStatus.PASSED for status in statuses):
        return VerificationVerdict.UNVERIFIED
    return VerificationVerdict.VERIFIED


def _bounded_environment() -> dict[str, str]:
    """Return the minimum inherited process environment needed to find tools."""

    allowed = (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PYTHONPATH",
    )
    return {
        key: value
        for key in allowed
        if isinstance((value := os.environ.get(key)), str) and value
    }


def _environment_digest(environment: dict[str, str], worktree: Path) -> str:
    return _sha256({"environment": environment, "worktree": str(worktree)})


def _output_summary(stdout: object, stderr: object, *, limit: int) -> str:
    if isinstance(limit, bool) or limit < 32:
        raise ValueError("max_output_chars must be at least 32")
    text = "\n".join(
        value
        for value in (str(stdout or "").strip(), str(stderr or "").strip())
        if value
    )
    text = redact(text).strip() or "No output captured."
    if len(text) > limit:
        suffix = " … [truncated]"
        text = text[: max(1, limit - len(suffix))].rstrip() + suffix
    return text[:limit]


def _termination_tracker(
    context: VerificationExecutionContext, check: PolicyCheck, index: int
) -> Any:
    """A ``CancellationTracker`` for one stopped verification check (#666).

    Scoped by check and attempt inside the canonical verification worktree, so
    the durable journal sits beside the manifest it evidences. Created only
    when a stop actually happens; ``None`` when it cannot be constructed —
    evidence must never block the stop path itself.
    """

    with contextlib.suppress(Exception):
        from .cancellation_lifecycle import CancellationTracker

        raw = f"verification-{check.check_id}-{index}-{uuid.uuid4().hex[:8]}"
        scope = "".join(ch if (ch.isalnum() or ch in "-_.") else "-" for ch in raw)[
            :128
        ]
        return CancellationTracker(context.worktree, scope)
    return None


def _record_stop_observed(tracker: Any, *, reason_code: str) -> None:
    """Request -> acknowledge -> draining at the moment each really happened."""
    if tracker is None:
        return
    with contextlib.suppress(Exception):
        tracker.request(reason_code=reason_code)
        tracker.acknowledge(reason_code="verification_loop_observed")
        tracker.begin_draining(reason_code="check_process_in_flight")


def _terminate(process: subprocess.Popen[str], *, tracker: Any = None) -> bool:
    """Terminate the whole owned process tree and confirm the parent exited.

    Uses :func:`process_tree.terminate_tree` rather than killing the direct
    child alone: a timed-out or cancelled check can have spawned grandchildren
    (test workers, browsers, language servers) that survive a plain
    ``kill()`` on Windows and are left running as orphans (#108, #539).

    ``force_terminating`` is recorded around the real kill and ``terminated``
    only when the exit is actually observed (#666) — the phase reflects what
    happened, never what was hoped.
    """

    if process.poll() is not None:
        return True
    if tracker is not None:
        with contextlib.suppress(Exception):
            tracker.force_terminate(reason_code="process_tree_termination_started")
    terminate_tree(process, timeout=5)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    confirmed = process.poll() is not None
    if tracker is not None and confirmed:
        with contextlib.suppress(Exception):
            tracker.mark_terminated(reason_code="process_exit_observed")
    return confirmed


def _terminal_record(check: PolicyCheck, status: CheckStatus) -> CheckRecord:
    return CheckRecord(
        check_id=check.check_id,
        kind=check.kind,
        requirement=check.requirement,
        status=status,
    )


def _run_attempt(
    check: PolicyCheck,
    context: VerificationExecutionContext,
    *,
    index: int,
    cancel: Callable[[], bool] | None,
    max_output_chars: int,
) -> VerificationAttempt:
    started = datetime.now().astimezone()
    environment = _bounded_environment()
    environment_digest = _environment_digest(environment, context.worktree)
    if cancel is not None and cancel():
        return VerificationAttempt(
            check_id=check.check_id,
            index=index,
            status=CheckStatus.CANCELLED,
            command=check.command,
            working_directory=context.worktree,
            started_at=started,
            ended_at=datetime.now().astimezone(),
            exit_status=None,
            output_summary="Verification was cancelled before the command started.",
            environment_digest=environment_digest,
            teardown_verified=True,
        )
    kwargs: dict[str, Any] = {
        "cwd": str(context.worktree),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "shell": False,
        "env": environment,
    }
    kwargs.update(isolated_group_kwargs())
    try:
        process = subprocess.Popen(list(check.command), **kwargs)  # nosec B603
    except FileNotFoundError as exc:
        return VerificationAttempt(
            check_id=check.check_id,
            index=index,
            status=CheckStatus.UNAVAILABLE,
            command=check.command,
            working_directory=context.worktree,
            started_at=started,
            ended_at=datetime.now().astimezone(),
            exit_status=127,
            output_summary=_output_summary("", str(exc), limit=max_output_chars),
            environment_digest=environment_digest,
            teardown_verified=True,
        )
    except OSError as exc:
        return VerificationAttempt(
            check_id=check.check_id,
            index=index,
            status=CheckStatus.BLOCKED,
            command=check.command,
            working_directory=context.worktree,
            started_at=started,
            ended_at=datetime.now().astimezone(),
            exit_status=None,
            output_summary=_output_summary("", str(exc), limit=max_output_chars),
            environment_digest=environment_digest,
            teardown_verified=True,
        )
    adopt(process)
    deadline = time.monotonic() + check.timeout_seconds
    stdout = ""
    stderr = ""
    while True:
        remaining = deadline - time.monotonic()
        try:
            stdout, stderr = process.communicate(
                timeout=max(0.001, min(0.1, remaining))
            )
            status = (
                CheckStatus.PASSED if process.returncode == 0 else CheckStatus.FAILED
            )
            teardown_verified = True
            exit_status = process.returncode
            break
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.stdout or stdout, exc.stderr or stderr
            if cancel is not None and cancel():
                tracker = _termination_tracker(context, check, index)
                _record_stop_observed(tracker, reason_code="cancel_requested")
                teardown_verified = _terminate(process, tracker=tracker)
                status = CheckStatus.CANCELLED
                exit_status = None
                break
            if time.monotonic() >= deadline:
                tracker = _termination_tracker(context, check, index)
                _record_stop_observed(tracker, reason_code="check_timeout")
                teardown_verified = _terminate(process, tracker=tracker)
                status = CheckStatus.TIMEOUT
                exit_status = None
                break
    return VerificationAttempt(
        check_id=check.check_id,
        index=index,
        status=status,
        command=check.command,
        working_directory=context.worktree,
        started_at=started,
        ended_at=datetime.now().astimezone(),
        exit_status=exit_status,
        output_summary=_output_summary(stdout, stderr, limit=max_output_chars),
        environment_digest=environment_digest,
        teardown_verified=teardown_verified,
    )


def execute_policy(
    policy: VerificationPolicy,
    context: VerificationExecutionContext,
    *,
    cancel: Callable[[], bool] | None = None,
    max_output_chars: int = 65_536,
) -> VerificationManifest:
    """Run only declared policy argv in the bound worktree and retain attempts."""

    if not isinstance(policy, VerificationPolicy):
        raise TypeError("policy must be a VerificationPolicy")
    if not isinstance(context, VerificationExecutionContext):
        raise TypeError("context must be a VerificationExecutionContext")
    records: list[CheckRecord] = []
    for check in policy.checks:
        if check.requirement in {"optional", "forbidden"}:
            records.append(_terminal_record(check, CheckStatus.SKIPPED))
            continue
        if policy.status != "ready" or {
            "canonical_worktree",
            "bounded_environment",
        } - set(check.environment):
            records.append(_terminal_record(check, CheckStatus.BLOCKED))
            continue
        if not check.command:
            records.append(_terminal_record(check, CheckStatus.UNAVAILABLE))
            continue
        attempts: list[VerificationAttempt] = []
        for index in range(1, check.retries + 2):
            attempt = _run_attempt(
                check,
                context,
                index=index,
                cancel=cancel,
                max_output_chars=max_output_chars,
            )
            attempts.append(attempt)
            if attempt.status is not CheckStatus.FAILED:
                break
        records.append(CheckRecord.from_attempts(check, attempts))
    return VerificationManifest.from_policy(policy, context, records)


def _evidence_directory(root: Path, context: VerificationExecutionContext) -> Path:
    state_root = state_dir(root).resolve(strict=False)
    target = state_root / "verification-evidence" / context.task_id / context.run_id
    try:
        target.resolve(strict=False).relative_to(state_root)
    except (OSError, ValueError) as exc:
        raise ValueError("verification evidence path escaped state directory") from exc
    return target


def _manifest_from_dict(payload: dict[str, Any]) -> VerificationManifest:
    context_raw = payload.get("context") or {}
    context = VerificationExecutionContext(
        task_id=context_raw.get("task_id"),
        run_id=context_raw.get("run_id"),
        worktree=Path(context_raw.get("worktree") or ""),
        repository_id=context_raw.get("repository_id"),
        head_sha=context_raw.get("head_sha"),
        environment_fingerprint=dict(context_raw.get("environment_fingerprint") or {}),
    )
    records: list[CheckRecord] = []
    for raw in payload.get("checks") or ():
        attempts = []
        for item in raw.get("attempts") or ():
            attempts.append(
                VerificationAttempt(
                    check_id=item.get("check_id"),
                    index=item.get("index"),
                    status=item.get("status"),
                    command=tuple(item.get("command") or ()),
                    working_directory=Path(item.get("working_directory") or ""),
                    started_at=datetime.fromisoformat(item.get("started_at")),
                    ended_at=datetime.fromisoformat(item.get("ended_at")),
                    exit_status=item.get("exit_status"),
                    output_summary=item.get("output_summary"),
                    environment_digest=item.get("environment_digest"),
                    teardown_verified=bool(item.get("teardown_verified")),
                    artifacts=tuple(
                        ArtifactReference(**artifact)
                        for artifact in item.get("artifacts") or ()
                    ),
                )
            )
        status = raw.get("status")
        records.append(
            CheckRecord(
                check_id=raw.get("check_id"),
                kind=raw.get("kind"),
                requirement=raw.get("requirement"),
                attempts=tuple(attempts),
                status=None if status == "missing" else status,
                waiver=dict(raw.get("waiver") or {}),
            )
        )
    return VerificationManifest(
        policy=dict(payload.get("policy") or {}),
        context=context,
        checks=tuple(records),
        digest=payload.get("digest") or "",
        integrity_errors=tuple(payload.get("integrity_errors") or ()),
    )


def verification_manifest_from_dict(payload: Mapping[str, Any]) -> VerificationManifest:
    """Parse a manifest payload while preserving integrity findings for consumers."""

    if not isinstance(payload, Mapping):
        raise TypeError("verification manifest must be a mapping")
    return _manifest_from_dict(dict(payload))


def _valid_manifest_record(record) -> bool:
    """A mirrored evidence manifest must verify its own digest.

    Same self-verifying property as the policy artifact, and for the same
    reason: a manifest records *what was actually run and what it produced*,
    so serving an altered one is worse than serving none. Recomputing the
    digest means an event altered inside the journal is skipped on replay
    rather than believed.

    Deliberately structural-plus-digest only. It does *not* re-verify the
    on-disk output artifacts -- ``load_verification_manifest`` does that, and
    marks missing or replaced evidence unverified. Re-doing it here would make
    rebuilding a projection depend on the very files the journal is supposed
    to outlive.
    """

    if not isinstance(record, Mapping):
        return False
    claimed = str(record.get("digest") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", claimed):
        return False
    payload = {key: value for key, value in record.items() if key != "digest"}
    return _sha256(payload) == claimed


def _read_manifest_raw(target: Path) -> dict[str, Any]:
    """Read the manifest as persisted, without the loader's strict validation."""

    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _manifest_target(root: Path, context: "VerificationExecutionContext") -> Path:
    return (
        _evidence_directory(Path(root).expanduser().resolve(), context)
        / "manifest.json"
    )


def manifest_shadow_projection(
    root: Path, context: "VerificationExecutionContext"
) -> dict[str, Any]:
    """Rebuild one run's evidence manifest from its shadow journal."""

    return shadow_journal.projection(
        _manifest_target(root, context), is_valid_record=_valid_manifest_record
    )


def manifest_contradiction_report(
    root: Path, context: "VerificationExecutionContext"
) -> dict[str, Any] | None:
    """``None`` when the persisted manifest and its shadow agree, else what differs."""

    target = _manifest_target(root, context)
    return shadow_journal.contradiction_report(
        target,
        lambda: _read_manifest_raw(target),
        is_valid_record=_valid_manifest_record,
        identity={"task_id": context.task_id, "run_id": context.run_id},
    )


def persist_verification_manifest(
    root: Path, manifest: VerificationManifest
) -> EvidenceManifestReference:
    """Atomically persist bounded output artifacts and one canonical manifest."""

    root = Path(root).expanduser().resolve()
    if root != manifest.context.worktree:
        raise ValueError("evidence root must be the canonical verification worktree")
    directory = _evidence_directory(root, manifest.context)
    target = directory / "manifest.json"
    records: list[CheckRecord] = []
    with interprocess_transaction(target):
        for record in manifest.checks:
            attempts: list[VerificationAttempt] = []
            for attempt in record.attempts:
                relative = f"outputs/{attempt.check_id}-{attempt.index}.txt"
                output_path = directory / relative
                content = attempt.output_summary + "\n"
                atomic_write_text(output_path, content)
                artifact = ArtifactReference(
                    path=relative, digest=sha256(output_path.read_bytes()).hexdigest()
                )
                attempts.append(replace(attempt, artifacts=(artifact,)))
            records.append(replace(record, attempts=tuple(attempts)))
        persisted = VerificationManifest(
            policy=manifest.policy, context=manifest.context, checks=tuple(records)
        )
        atomic_write_text(
            target,
            json.dumps(persisted.to_dict(), indent=2, sort_keys=True, ensure_ascii=True)
            + "\n",
        )
        # #613 Stage 2: mirrored inside the same lock, after the artifacts
        # it references are on disk. The manifest carries the policy digest it
        # ran under, so with verification_policy already mirrored the shadow
        # can rebuild the whole chain -- which policy, which run, which
        # evidence -- from journals alone.
        shadow_journal.record_snapshot(
            target, persisted.to_dict(), is_valid_record=_valid_manifest_record
        )
    return EvidenceManifestReference(path=target, digest=persisted.digest)


def load_verification_manifest(path: Path) -> VerificationManifest:
    """Load a manifest and mark any missing/replaced evidence as unverified."""

    target = Path(path).expanduser().resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest must be a JSON object")
        manifest = _manifest_from_dict(payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid verification manifest: {exc}") from exc
    errors: list[str] = []
    for record in manifest.checks:
        for attempt in record.attempts:
            for artifact in attempt.artifacts:
                candidate = (target.parent / artifact.path).resolve(strict=False)
                try:
                    candidate.relative_to(target.parent)
                    actual = sha256(candidate.read_bytes()).hexdigest()
                except (OSError, ValueError):
                    errors.append(f"artifact unavailable: {artifact.path}")
                    continue
                if actual != artifact.digest:
                    errors.append(f"artifact digest mismatch: {artifact.path}")
    return replace(manifest, integrity_errors=tuple(errors))
