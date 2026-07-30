"""Hermetic verification evidence and verdict regression tests (#539)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

from opaihub.verification_execution import (
    CheckRecord,
    CheckStatus,
    VerificationAttempt,
    VerificationExecutionContext,
    VerificationManifest,
    VerificationVerdict,
    execute_policy,
    load_verification_manifest,
    persist_verification_manifest,
    verification_verdict,
)
from opaihub.verification_policy import PolicyCheck, PolicySource, VerificationPolicy


def _policy() -> VerificationPolicy:
    return VerificationPolicy(
        status="ready",
        classification={"mode": "implement", "edit_capable": True},
        checks=(
            PolicyCheck(
                "unit",
                "unit",
                "required",
                "Run the relevant unit suite.",
                command=("python", "-m", "pytest", "-q"),
            ),
        ),
        sources=(PolicySource("builtin", "Test policy"),),
    )


def _context(root: Path) -> VerificationExecutionContext:
    return VerificationExecutionContext(
        task_id="task-1",
        run_id="run-1",
        worktree=root,
        repository_id="repo-1",
        head_sha="a" * 40,
    )


def _attempt(root: Path, status: CheckStatus, *, index: int = 1) -> VerificationAttempt:
    started = datetime(2026, 7, 30, tzinfo=timezone.utc)
    return VerificationAttempt(
        check_id="unit",
        index=index,
        status=status,
        command=("python", "-m", "pytest", "-q"),
        working_directory=root,
        started_at=started,
        ended_at=started + timedelta(seconds=1),
        exit_status=0 if status is CheckStatus.PASSED else 1,
        output_summary="test evidence",
        environment_digest="b" * 64,
        teardown_verified=True,
    )


def _manifest(root: Path, records: tuple[CheckRecord, ...]) -> VerificationManifest:
    return VerificationManifest.from_policy(_policy(), _context(root), records)


def _policy_for_command(
    command: tuple[str, ...], *, timeout_seconds: int = 5, retries: int = 0
) -> VerificationPolicy:
    return VerificationPolicy(
        status="ready",
        classification={"mode": "implement", "edit_capable": True},
        checks=(
            PolicyCheck(
                "unit",
                "unit",
                "required",
                "Run the relevant unit suite.",
                command=command,
                timeout_seconds=timeout_seconds,
                retries=retries,
            ),
        ),
        sources=(PolicySource("builtin", "Test policy"),),
    )


def test_missing_required_check_is_unverified(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, ())

    assert verification_verdict(manifest) is VerificationVerdict.UNVERIFIED


def test_passing_required_check_is_verified(tmp_path: Path) -> None:
    record = CheckRecord.from_attempts(
        _policy().checks[0], (_attempt(tmp_path, CheckStatus.PASSED),)
    )

    assert (
        verification_verdict(_manifest(tmp_path, (record,)))
        is VerificationVerdict.VERIFIED
    )


def test_failed_then_passing_required_check_remains_partially_verified(
    tmp_path: Path,
) -> None:
    record = CheckRecord.from_attempts(
        _policy().checks[0],
        (
            _attempt(tmp_path, CheckStatus.FAILED),
            _attempt(tmp_path, CheckStatus.PASSED, index=2),
        ),
    )

    assert record.flake_suspected is True
    assert (
        verification_verdict(_manifest(tmp_path, (record,)))
        is VerificationVerdict.PARTIALLY_VERIFIED
    )


def test_waived_required_check_remains_partially_verified(tmp_path: Path) -> None:
    record = CheckRecord.waived(
        _policy().checks[0],
        actor="maintainer@example.test",
        reason="Temporary external outage",
        policy_source="team",
        scope="task-1",
    )

    assert (
        verification_verdict(_manifest(tmp_path, (record,)))
        is VerificationVerdict.PARTIALLY_VERIFIED
    )


def test_manifest_rejects_a_digest_that_does_not_match_its_content(
    tmp_path: Path,
) -> None:
    passed = CheckRecord.from_attempts(
        _policy().checks[0], (_attempt(tmp_path, CheckStatus.PASSED),)
    )
    with pytest.raises(ValueError, match="digest"):
        VerificationManifest.from_policy(
            _policy(), _context(tmp_path), (passed,), digest="0" * 64
        )


def test_runner_executes_only_declared_argv_in_the_canonical_worktree(
    tmp_path: Path,
) -> None:
    policy = _policy_for_command(
        (sys.executable, "-c", "from pathlib import Path; print(Path.cwd().name)")
    )

    manifest = execute_policy(policy, _context(tmp_path))
    attempt = manifest.checks[0].attempts[0]

    assert attempt.status is CheckStatus.PASSED
    assert attempt.working_directory == tmp_path.resolve()
    assert attempt.command == policy.checks[0].command
    assert verification_verdict(manifest) is VerificationVerdict.VERIFIED


def test_missing_executable_is_unavailable_not_success(tmp_path: Path) -> None:
    manifest = execute_policy(
        _policy_for_command(("opai-command-that-does-not-exist",)), _context(tmp_path)
    )

    assert manifest.checks[0].status is CheckStatus.UNAVAILABLE
    assert verification_verdict(manifest) is VerificationVerdict.UNVERIFIED


def test_timeout_retains_bounded_redacted_diagnostics_and_teardown(
    tmp_path: Path,
) -> None:
    command = (
        sys.executable,
        "-c",
        "import time; print('token=sk-12345678901234567890'); time.sleep(10)",
    )

    manifest = execute_policy(
        _policy_for_command(command, timeout_seconds=1),
        _context(tmp_path),
        max_output_chars=64,
    )
    attempt = manifest.checks[0].attempts[0]

    assert attempt.status is CheckStatus.TIMEOUT
    assert attempt.teardown_verified is True
    assert "12345678901234567890" not in attempt.output_summary
    assert len(attempt.output_summary) <= 64
    assert verification_verdict(manifest) is VerificationVerdict.TIMEOUT


def test_cancelled_check_never_executes_as_a_pass(tmp_path: Path) -> None:
    manifest = execute_policy(
        _policy_for_command((sys.executable, "-c", "print('should not run')")),
        _context(tmp_path),
        cancel=lambda: True,
    )

    assert manifest.checks[0].status is CheckStatus.CANCELLED
    assert verification_verdict(manifest) is VerificationVerdict.CANCELLED


def test_persisted_manifest_round_trips_with_bounded_redacted_output(
    tmp_path: Path,
) -> None:
    manifest = execute_policy(
        _policy_for_command(
            (sys.executable, "-c", "print('token=sk-12345678901234567890')")
        ),
        _context(tmp_path),
    )

    reference = persist_verification_manifest(tmp_path, manifest)
    restored = load_verification_manifest(reference.path)

    assert reference.path.is_relative_to(
        tmp_path / ".opaihub" / "verification-evidence"
    )
    assert restored.digest == reference.digest
    assert "12345678901234567890" not in reference.path.read_text(encoding="utf-8")
    fingerprint = restored.context.environment_fingerprint
    assert {"os", "architecture", "python", "repository_id", "head_sha"} <= set(
        fingerprint
    )
    assert fingerprint["head_sha"] == "a" * 40
    assert verification_verdict(restored) is VerificationVerdict.VERIFIED


def test_missing_persisted_output_artifact_downgrades_to_unverified(
    tmp_path: Path,
) -> None:
    manifest = execute_policy(
        _policy_for_command((sys.executable, "-c", "print('ok')")), _context(tmp_path)
    )
    reference = persist_verification_manifest(tmp_path, manifest)
    artifact = next(reference.path.parent.glob("outputs/*.txt"))
    artifact.unlink()

    restored = load_verification_manifest(reference.path)

    assert verification_verdict(restored) is VerificationVerdict.UNVERIFIED


def test_provider_tool_trace_cannot_override_an_unverified_manifest(
    tmp_path: Path,
) -> None:
    from opaihub.completion import (
        CompletionVerdict,
        evaluate_completion,
        objective_from_request,
    )

    result = evaluate_completion(
        objective_from_request("Fix parser.py and run tests.", mode="implement"),
        {
            "status": "answered",
            "changed_files": ["parser.py"],
            "tool_trace": [{"tool": "run_tests", "ok": True}],
            "verification_manifest": _manifest(tmp_path, ()).to_dict(),
        },
    )

    assert result.verdict is CompletionVerdict.PARTIAL
    assert result.reason_code == "verification_unverified"
