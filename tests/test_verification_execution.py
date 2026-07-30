"""Hermetic verification evidence and verdict regression tests (#539)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from opaihub.verification_execution import (
    CheckRecord,
    CheckStatus,
    VerificationAttempt,
    VerificationExecutionContext,
    VerificationManifest,
    VerificationVerdict,
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
