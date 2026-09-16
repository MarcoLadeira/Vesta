"""#613 Stage 2: the evidence manifest's shadow journal, and the chain it completes.

Stage 1 named ``vestahub/verification_execution.py`` JOURNAL_OWNED -- "artifacts:
verification runs". The manifest records what was actually run and what it
produced, and it carries the digest of the policy it ran under.

That last part is why this migration matters more than one more module. With
``verification_policy`` already mirrored, the two journals now chain: policy
digest -> manifest -> evidence. A run's rules and a run's results can be
rebuilt together from journals alone, which is the property #613 is ultimately
after and the first place in the migration where two mirrored records reference
each other.

Like the policy artifact, a manifest verifies itself, so the validator
recomputes its digest rather than checking shape alone. The deliberate limit is
that it stops there: it does *not* re-verify the on-disk output artifacts.
``load_verification_manifest`` already does that and marks missing or replaced
evidence unverified. Re-doing it in the validator would make rebuilding a
projection depend on the very files the journal exists to outlive.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from vestahub import shadow_journal
from vestahub.verification_execution import (
    CheckRecord,
    CheckStatus,
    VerificationAttempt,
    VerificationExecutionContext,
    VerificationManifest,
    manifest_contradiction_report,
    manifest_shadow_projection,
    persist_verification_manifest,
)
from vestahub.verification_policy import PolicyCheck, PolicySource, VerificationPolicy


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


def _persist(root: Path, status: CheckStatus = CheckStatus.PASSED):
    record = CheckRecord(
        check_id="unit",
        kind="unit",
        requirement="required",
        attempts=(_attempt(root, status),),
    )
    manifest = VerificationManifest.from_policy(_policy(), _context(root), (record,))
    reference = persist_verification_manifest(root, manifest)
    return manifest, reference


def _manifest_path(root: Path) -> Path:
    from vestahub.state import state_dir

    return (
        state_dir(root) / "verification-evidence" / "task-1" / "run-1" / "manifest.json"
    )


def test_persisting_is_mirrored_and_the_shadow_agrees(tmp_path):
    _manifest, reference = _persist(tmp_path)

    shadow = manifest_shadow_projection(tmp_path, _context(tmp_path))

    assert shadow["digest"] == reference.digest
    assert manifest_contradiction_report(tmp_path, _context(tmp_path)) is None


def test_the_shadow_carries_the_policy_digest_the_run_was_judged_under(tmp_path):
    """The chain #613 is after: policy -> manifest -> evidence, from journals."""

    _persist(tmp_path)

    shadow = manifest_shadow_projection(tmp_path, _context(tmp_path))

    assert shadow["policy"]["digest"] == _policy().digest


def test_a_failed_check_is_mirrored_as_faithfully_as_a_passing_one(tmp_path):
    """A losing verdict is the one somebody would want quietly rewritten."""

    _persist(tmp_path, CheckStatus.FAILED)

    shadow = manifest_shadow_projection(tmp_path, _context(tmp_path))
    statuses = [
        attempt["status"] for check in shadow["checks"] for attempt in check["attempts"]
    ]

    assert "failed" in [str(value).lower() for value in statuses]
    assert manifest_contradiction_report(tmp_path, _context(tmp_path)) is None


def test_re_persisting_moves_both_sides_together(tmp_path):
    _persist(tmp_path, CheckStatus.FAILED)
    _manifest, reference = _persist(tmp_path, CheckStatus.PASSED)

    shadow = manifest_shadow_projection(tmp_path, _context(tmp_path))

    assert shadow["digest"] == reference.digest
    assert manifest_contradiction_report(tmp_path, _context(tmp_path)) is None


def test_a_tampered_journal_event_is_skipped_rather_than_served(tmp_path):
    """Self-verifying: an altered event fails its own digest and is skipped."""

    _persist(tmp_path, CheckStatus.FAILED)
    journal = shadow_journal.journal_path_for(_manifest_path(tmp_path))
    lines = journal.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[-1])
    event["record"]["checks"] = []
    lines[-1] = json.dumps(event)
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")

    shadow = manifest_shadow_projection(tmp_path, _context(tmp_path))

    assert shadow.get("checks") != []


def test_a_forged_digest_is_self_consistent_but_still_contradicts_the_file(tmp_path):
    """Validator and comparator do not subsume each other; #613 keeps both."""

    _persist(tmp_path, CheckStatus.FAILED)
    journal = shadow_journal.journal_path_for(_manifest_path(tmp_path))
    lines = journal.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[-1])
    record = event["record"]
    record["checks"] = []
    payload = {key: value for key, value in record.items() if key != "digest"}
    record["digest"] = sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    lines[-1] = json.dumps(event)
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = manifest_contradiction_report(tmp_path, _context(tmp_path))

    assert report is not None
    assert "checks" in report["mismatched_fields"]
    assert report["task_id"] == "task-1"
    assert report["run_id"] == "run-1"


def test_an_out_of_band_manifest_write_is_reported(tmp_path):
    manifest, _reference = _persist(tmp_path)
    path = _manifest_path(tmp_path)
    tampered = {**json.loads(path.read_text(encoding="utf-8")), "checks": []}
    path.write_text(json.dumps(tampered), encoding="utf-8")

    report = manifest_contradiction_report(tmp_path, _context(tmp_path))

    assert report is not None
    assert "checks" in report["mismatched_fields"]


def test_a_lost_manifest_is_reported_against_a_surviving_shadow(tmp_path):
    """Evidence deleted, journal intact -- exactly what #613 wants visible."""

    _manifest, reference = _persist(tmp_path)
    _manifest_path(tmp_path).unlink()

    report = manifest_contradiction_report(tmp_path, _context(tmp_path))

    assert report is not None
    assert report["legacy"] == {}
    assert report["shadow"]["digest"] == reference.digest


def test_the_validator_does_not_depend_on_the_output_artifacts_surviving(tmp_path):
    """The deliberate limit: replay must not require the evidence files.

    ``load_verification_manifest`` is what checks artifact digests, and it marks
    missing evidence unverified. If the validator repeated that work, a journal
    would stop replaying the moment the files it describes were cleaned up --
    which is precisely the situation the journal is supposed to survive.
    """

    _manifest, reference = _persist(tmp_path)
    outputs = _manifest_path(tmp_path).parent / "outputs"
    for stale in outputs.glob("*.txt"):
        stale.unlink()

    shadow = manifest_shadow_projection(tmp_path, _context(tmp_path))

    assert shadow["digest"] == reference.digest


def test_a_never_persisted_run_agrees_as_both_empty(tmp_path):
    context = VerificationExecutionContext(
        task_id="task-1",
        run_id="never-run",
        worktree=tmp_path,
        repository_id="repo-1",
        head_sha="a" * 40,
    )

    assert manifest_shadow_projection(tmp_path, context) == {}
    assert manifest_contradiction_report(tmp_path, context) is None
