"""Immutable verification evidence and verdicts for managed OPai work (#539)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import signal
import subprocess  # nosec B404 - commands are validated argv and use shell=False
from typing import Any, Callable, Iterable

from .command_runner import redact
from .proc import no_window_kwargs
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


@dataclass(frozen=True)
class VerificationExecutionContext:
    """Task/run binding and canonical repository facts for check execution."""

    task_id: str
    run_id: str
    worktree: Path
    repository_id: str
    head_sha: str

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

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "worktree": str(self.worktree),
            "repository_id": self.repository_id,
            "head_sha": self.head_sha,
        }


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "index": self.index,
            "status": self.status.value,
            "command": list(self.command),
            "working_directory": str(self.working_directory),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "exit_status": self.exit_status,
            "output_summary": self.output_summary,
            "environment_digest": self.environment_digest,
            "teardown_verified": self.teardown_verified,
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
            policy=policy.to_dict(),
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
        return {**self.payload_without_digest(), "digest": self.digest}


def verification_verdict(manifest: VerificationManifest) -> VerificationVerdict:
    """Derive terminal verification truth from policy requirements and evidence."""

    if not isinstance(manifest, VerificationManifest):
        raise TypeError("manifest must be a VerificationManifest")
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


def _terminate(process: subprocess.Popen[str]) -> bool:
    """Terminate the process group and confirm the owned parent exited."""

    if process.poll() is not None:
        return True
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return process.poll() is not None


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
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs.update(no_window_kwargs())
    else:
        kwargs["start_new_session"] = True
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
    try:
        stdout, stderr = process.communicate(timeout=check.timeout_seconds)
        status = CheckStatus.PASSED if process.returncode == 0 else CheckStatus.FAILED
        teardown_verified = True
        exit_status = process.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or "", exc.stderr or ""
        teardown_verified = _terminate(process)
        status = CheckStatus.TIMEOUT
        exit_status = None
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
