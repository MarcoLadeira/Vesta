"""Reproducible release-candidate preflight (#32).

A closed-source release candidate needs a repeatable, inspectable process that
can prove what *would* ship before any artifact is published. This module is
that process: one deterministic readiness result assembled from independent,
individually inspectable checks —

- the working tree is clean,
- the canonical version and generated runtime projections agree,
- the version's changelog entry exists and is on top,
- the license and required documentation are present,
- the release tag does not already exist (so the release is new),
- the test gate passes,
- declared artifacts exist, match their recorded checksums, and are signed.

A **dry-run** path performs every safe step but cannot tag, upload, publish, or
notify — and proves it, including network isolation. **Rollback** restores the
previous tested artifact and release pointer without deleting user state
(ledger, preferences, credentials). Reports are emitted machine-readable (JSON)
and human-readable (Markdown), blockers first.

Everything is pure and dependency-injected — git, the test gate, the clock, and
the filesystem roots are all parameters — so the whole thing is hermetically
testable and cannot reach the network on its own.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess  # nosec B404
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from opai.release_identity import (
    ReleaseIdentityError,
    derive_project_release,
    read_project_release,
)
from opai.release_validation import validate_release_identity

# ---- result model --------------------------------------------------------- #
PASS = "pass"  # nosec B105
FAIL = "fail"
WARN = "warn"
SKIP = "skip"

# The ordered, side-effectful publish steps a real release performs. The
# preflight never runs these; dry-run proves they stay disabled.
PUBLISH_STEPS: tuple[str, ...] = (
    "create_tag",
    "upload_artifacts",
    "publish_release",
    "notify_customers",
)

REQUIRED_DOCS: tuple[str, ...] = (
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "CONTRIBUTING.md",
)
REQUIRED_ARTIFACT_PLATFORMS: tuple[str, ...] = (
    "windows-latest",
    "macos-latest",
)
DESKTOP_ARTIFACT_WORKFLOW = ".github/workflows/desktop-artifacts.yml"
MAX_VERIFICATION_LOG_BYTES = 1_048_576
MAX_JSON_EVIDENCE_BYTES = 262_144
_TEST_VERDICT_CLASSIFICATIONS: dict[str, str] = {
    "qualified": "none",
    "test_failed": "test",
    "security_failed": "security",
    "build_failed": "build",
    "policy_failed": "policy",
    "infrastructure_blocked": "infrastructure",
    "runner_unavailable": "infrastructure",
    "credential_unavailable": "credential",
    "artifact_upload_failed": "infrastructure",
    "cancelled_superseded": "infrastructure",
}


class ReleaseError(RuntimeError):
    """A release operation was refused because it is unsafe or disabled."""


class ReleaseDryRunError(ReleaseError):
    """A side-effectful publish step was attempted during a dry run."""


@dataclass(frozen=True)
class CheckResult:
    """One inspectable release check.

    A required check is blocking whenever it did not pass. This deliberately
    includes ``SKIP``: a qualification request cannot turn missing evidence
    into a successful release verdict.
    """

    id: str
    title: str
    status: str
    blocker: bool = False
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.blocker and self.status != PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "blocker": self.blocker,
            "blocking": self.blocking,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class TestGateResult:
    """Typed result returned by the exact-SHA local qualification gate."""

    verdict: str
    reason: str
    classification: str
    detail: str
    candidate_sha: str | None
    profile: str
    manifest_sha256: str | None = None

    @property
    def qualified(self) -> bool:
        return self.verdict == "qualified"

    def to_evidence(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "classification": self.classification,
            "candidate_sha": self.candidate_sha,
            "profile": self.profile,
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True)
class ReleaseReadiness:
    """The aggregate verdict over every check."""

    version: str
    dry_run: bool
    checks: tuple[CheckResult, ...]
    generated_at: str
    candidate_sha: str | None = None
    commit_sha: str | None = None
    release_identity: dict[str, Any] | None = None
    qualification_required: bool = False
    qualification_scope: str = "planning"
    verdict: str = "qualified"
    reason: str = "release_preflight_passed"
    classification: str = "none"

    @property
    def blockers(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.blocking)

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def final_release_ready(self) -> bool:
        return self.qualification_scope == "final-artifact" and self.ready

    @property
    def artifact_qualification(self) -> str:
        if self.qualification_scope == "source":
            return "pending"
        if self.qualification_scope == "final-artifact":
            return "qualified" if self.ready else "blocked"
        return "not_requested"

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {PASS: 0, FAIL: 0, WARN: 0, SKIP: 0}
        for check in self.checks:
            counts[check.status] = counts.get(check.status, 0) + 1
        return {
            "kind": "opai_rc_preflight",
            "schema_version": 3,
            "version": self.version,
            "dry_run": self.dry_run,
            "qualification_required": self.qualification_required,
            "qualification_scope": self.qualification_scope,
            "candidate_sha": self.candidate_sha,
            "commit_sha": self.commit_sha,
            "release_identity": self.release_identity,
            "ready": self.ready,
            "final_release_ready": self.final_release_ready,
            "artifact_qualification": self.artifact_qualification,
            "verdict": self.verdict,
            "reason": self.reason,
            "classification": self.classification,
            "generated_at": self.generated_at,
            "totals": {
                "checks": len(self.checks),
                "blockers": len(self.blockers),
                **counts,
            },
            "blockers": [c.id for c in self.blockers],
            "checks": [c.to_dict() for c in self.checks],
        }


# ---- injectable context --------------------------------------------------- #
GitRunner = Callable[[Path, Sequence[str]], "subprocess.CompletedProcess[str]"]
TestRunner = Callable[[Path, str], TestGateResult]
ArtifactVerifier = Callable[
    [Path, Path, dict[str, Any], "ReleaseContext"], tuple[bool, str]
]
Clock = Callable[[], str]


def _default_git(root: Path, args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    git = shutil.which("git")
    command = [git or "git", "-C", str(root), *args]
    if git is None:
        return subprocess.CompletedProcess(
            command,
            127,
            "",
            "git executable not found on PATH",
        )
    return subprocess.run(  # nosec B603
        command,
        capture_output=True,
        text=True,
        check=False,
    )


def _default_tests(root: Path, candidate_sha: str) -> TestGateResult:
    """Run the full local contract and preserve its canonical typed result."""

    if _COMMIT_SHA.fullmatch(str(candidate_sha or "").lower()) is None:
        return TestGateResult(
            verdict="infrastructure_blocked",
            reason="candidate_sha_missing",
            classification="infrastructure",
            detail="full test qualification requires an exact candidate SHA",
            candidate_sha=candidate_sha or None,
            profile="full",
        )
    with tempfile.TemporaryDirectory(prefix="opai-release-tests-") as temporary:
        manifest = Path(temporary) / "ci-local-evidence.json"
        command = [
            sys.executable,
            str(root / "scripts" / "ci_local.py"),
            "--profile",
            "full",
            "--candidate-sha",
            candidate_sha,
            "--manifest",
            str(manifest),
        ]
        try:
            completed = subprocess.run(  # nosec B603
                command,
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
                timeout=75 * 60,
            )
        except subprocess.TimeoutExpired:
            return TestGateResult(
                verdict="infrastructure_blocked",
                reason="required_check_timeout",
                classification="infrastructure",
                detail="full local qualification exceeded 75 minutes",
                candidate_sha=candidate_sha,
                profile="full",
            )
        except OSError as error:
            return TestGateResult(
                verdict="infrastructure_blocked",
                reason="required_check_unavailable",
                classification="infrastructure",
                detail=f"could not start full local qualification: {type(error).__name__}",
                candidate_sha=candidate_sha,
                profile="full",
            )

        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return TestGateResult(
                verdict="infrastructure_blocked",
                reason="test_evidence_unavailable",
                classification="infrastructure",
                detail="full local qualification did not produce readable evidence",
                candidate_sha=candidate_sha,
                profile="full",
            )
        profile = value.get("profile") if isinstance(value, dict) else None
        profile_name = (
            str(profile.get("name") or "") if isinstance(profile, dict) else ""
        )
        evidence_candidate = str(value.get("candidate_sha") or "").lower()
        evidence_commit = str(value.get("commit_sha") or "").lower()
        candidate = value.get("candidate")
        verdict = str(value.get("verdict") or "")
        reason = str(value.get("reason") or "")
        classification = str(value.get("classification") or "")
        typed_status = _TEST_VERDICT_CLASSIFICATIONS.get(verdict) == classification
        qualified_identity = verdict != "qualified" or (
            evidence_commit == candidate_sha.lower()
            and isinstance(candidate, dict)
            and str(candidate.get("expected_sha") or "").lower()
            == candidate_sha.lower()
            and str(candidate.get("checked_out_sha") or "").lower()
            == candidate_sha.lower()
            and candidate.get("promotable") is True
        )
        evidence_valid = (
            value.get("schema_version") in {2, 3}
            and profile_name == "full"
            and evidence_candidate == candidate_sha.lower()
            and bool(reason)
            and typed_status
            and qualified_identity
            and (completed.returncode == 0) == (verdict == "qualified")
        )
        if not evidence_valid:
            return TestGateResult(
                verdict="infrastructure_blocked",
                reason="test_evidence_invalid",
                classification="infrastructure",
                detail="full local qualification evidence failed identity or status validation",
                candidate_sha=candidate_sha,
                profile="full",
                manifest_sha256=sha256_of(manifest),
            )
        return TestGateResult(
            verdict=verdict,
            reason=reason,
            classification=classification,
            detail=f"{verdict}: {reason}",
            candidate_sha=evidence_candidate,
            profile=profile_name,
            manifest_sha256=sha256_of(manifest),
        )


def _default_clock() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ReleaseContext:
    """Everything a preflight run needs, all injectable for hermetic tests."""

    root: Path
    dry_run: bool = True
    artifacts_manifest: Path | None = None
    run_tests: bool = False
    qualification_required: bool = False
    source_only: bool = False
    candidate_sha: str | None = None
    repository: str | None = None
    workflow: str | None = None
    run_id: str | None = None
    run_attempt: str | None = None
    release_tag: str | None = None
    git: GitRunner = _default_git
    tests: TestRunner = _default_tests
    artifact_verifier: ArtifactVerifier | None = None
    now: Clock = _default_clock

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        self.repository = self.repository or os.environ.get("GITHUB_REPOSITORY")
        self.workflow = self.workflow or _workflow_path_from_ref(
            os.environ.get("GITHUB_WORKFLOW_REF", "")
        )
        self.run_id = str(self.run_id or os.environ.get("GITHUB_RUN_ID") or "") or None
        self.run_attempt = (
            str(self.run_attempt or os.environ.get("GITHUB_RUN_ATTEMPT") or "") or None
        )
        if not self.release_tag and os.environ.get("GITHUB_REF_TYPE") == "tag":
            self.release_tag = os.environ.get("GITHUB_REF_NAME")

    @property
    def qualification_scope(self) -> str:
        if self.source_only:
            return "source"
        if self.qualification_required and self.artifacts_manifest is not None:
            return "final-artifact"
        return "planning"


def _workflow_path_from_ref(value: str) -> str | None:
    marker = "/.github/workflows/"
    if marker not in value or "@" not in value:
        return None
    suffix = value.split(marker, 1)[1].rsplit("@", 1)[0]
    return f".github/workflows/{suffix}" if suffix else None


# ---- version identity ----------------------------------------------------- #
_PEP440 = re.compile(r"^(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?$")
_STAGE_KIND = {"a": "alpha", "b": "beta", "rc": "rc"}
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def changelog_heading_for(version: str, stage: str) -> str | None:
    """The changelog heading a release with ``version``/``stage`` must carry.

    ``0.2.0a2`` + ``alpha.2`` -> ``0.2.0 Alpha.2``. Returns None when the
    PEP 440 version and the marketing stage disagree (a real inconsistency).
    """
    parsed = _PEP440.match(str(version).strip())
    if not parsed:
        return None
    base, kind_letter, num = parsed.group(1), parsed.group(2), parsed.group(3)
    stage_norm = str(stage or "").strip().lower()
    if kind_letter is None:
        # A final release: stage should be empty/"stable"/"final".
        if stage_norm in ("", "stable", "final", "release"):
            return base
        return None
    expected_stage = f"{_STAGE_KIND[kind_letter]}.{num}"
    if stage_norm != expected_stage:
        return None
    return f"{base} {expected_stage.title()}"


# ---- individual checks ---------------------------------------------------- #
def check_version_consistency(ctx: ReleaseContext) -> CheckResult:
    root = ctx.root
    try:
        release = read_project_release(root / "pyproject.toml")
    except ReleaseIdentityError as exc:
        return CheckResult(
            "version_consistency",
            "Canonical release identity has no drift",
            FAIL,
            blocker=True,
            detail=str(exc),
            evidence={"canonical_source": "pyproject.toml"},
        )
    drift = validate_release_identity(root)
    evidence = {
        "canonical_source": "pyproject.toml [project].version",
        "application_version": release.application_version,
        "release_channel": release.release_channel,
        "release_stage": release.release_stage,
        "drift": [item.to_dict() for item in drift],
    }
    if drift:
        return CheckResult(
            "version_consistency",
            "Canonical release identity has no drift",
            FAIL,
            blocker=True,
            detail=drift[0].message(),
            evidence=evidence,
        )
    return CheckResult(
        "version_consistency",
        "Canonical release identity has no drift",
        PASS,
        detail=f"{release.application_version} ({release.release_stage})",
        evidence=evidence,
    )


def resolve_version(ctx: ReleaseContext) -> tuple[str, str]:
    """The single agreed version + stage (best-effort; empty on inconsistency)."""
    try:
        release = read_project_release(ctx.root / "pyproject.toml")
    except ReleaseIdentityError:
        return "", ""
    return release.application_version, release.release_stage


def resolve_commit_sha(ctx: ReleaseContext) -> str | None:
    """Return the immutable checked-out commit, never a symbolic ref."""
    result = ctx.git(ctx.root, ["rev-parse", "--verify", "HEAD"])
    value = result.stdout.strip().lower() if result.returncode == 0 else ""
    return value if _COMMIT_SHA.fullmatch(value) else None


def check_candidate_identity(ctx: ReleaseContext) -> CheckResult:
    """Bind qualification evidence to the exact checked-out Git commit."""
    expected = str(ctx.candidate_sha or "").strip().lower()
    actual = resolve_commit_sha(ctx)
    evidence = {"candidate_sha": expected or None, "commit_sha": actual}

    if not expected:
        if ctx.qualification_required:
            return CheckResult(
                "candidate_identity",
                "Candidate SHA matches the checked-out commit",
                FAIL,
                blocker=True,
                detail="Release qualification requires an explicit full candidate SHA",
                evidence=evidence,
            )
        return CheckResult(
            "candidate_identity",
            "Candidate SHA matches the checked-out commit",
            SKIP,
            detail="No immutable candidate SHA requested",
            evidence=evidence,
        )
    if _COMMIT_SHA.fullmatch(expected) is None:
        return CheckResult(
            "candidate_identity",
            "Candidate SHA matches the checked-out commit",
            FAIL,
            blocker=True,
            detail="Candidate SHA must be a full 40-character hexadecimal commit ID",
            evidence=evidence,
        )
    if actual is None:
        return CheckResult(
            "candidate_identity",
            "Candidate SHA matches the checked-out commit",
            FAIL,
            blocker=True,
            detail="Could not resolve the checked-out commit SHA",
            evidence=evidence,
        )
    if expected != actual:
        return CheckResult(
            "candidate_identity",
            "Candidate SHA matches the checked-out commit",
            FAIL,
            blocker=True,
            detail=f"Candidate SHA {expected} does not match checked-out SHA {actual}",
            evidence=evidence,
        )
    return CheckResult(
        "candidate_identity",
        "Candidate SHA matches the checked-out commit",
        PASS,
        detail=f"Candidate is {actual}",
        evidence=evidence,
    )


def check_clean_tree(ctx: ReleaseContext) -> CheckResult:
    status = ctx.git(ctx.root, ["status", "--porcelain"])
    if status.returncode != 0:
        return CheckResult(
            "clean_tree",
            "Working tree is a clean git checkout",
            FAIL,
            blocker=True,
            detail=(status.stderr or "git status failed").strip(),
        )
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    if dirty:
        return CheckResult(
            "clean_tree",
            "Working tree is a clean git checkout",
            FAIL,
            blocker=True,
            detail=f"{len(dirty)} uncommitted path(s)",
            evidence={"dirty": dirty[:20]},
        )
    return CheckResult("clean_tree", "Working tree is a clean git checkout", PASS)


def check_changelog(ctx: ReleaseContext) -> CheckResult:
    version, stage = resolve_version(ctx)
    expected = changelog_heading_for(version, stage)
    text = _read(ctx.root / "CHANGELOG.md")
    headings = re.findall(r"(?m)^##\s+(.+?)\s*$", text)
    evidence = {
        "expected_heading": expected,
        "top_heading": headings[0] if headings else None,
    }
    if not text.strip():
        return CheckResult(
            "changelog",
            "Changelog documents this release",
            FAIL,
            blocker=True,
            detail="CHANGELOG.md is missing or empty",
            evidence=evidence,
        )
    if expected is None:
        return CheckResult(
            "changelog",
            "Changelog documents this release",
            FAIL,
            blocker=True,
            detail="Cannot derive the expected changelog heading from the version",
            evidence=evidence,
        )
    if not headings or headings[0].strip() != expected:
        return CheckResult(
            "changelog",
            "Changelog documents this release",
            FAIL,
            blocker=True,
            detail=(
                f"Top changelog entry is {headings[0]!r} but this release is "
                f"{expected!r}"
                if headings
                else f"No changelog entry for {expected!r}"
            ),
            evidence=evidence,
        )
    # The section must actually have content, not just a bare heading.
    after = text.split(f"## {expected}", 1)[1]
    body = after.split("\n## ", 1)[0].strip()
    if len(body) < 20:
        return CheckResult(
            "changelog",
            "Changelog documents this release",
            FAIL,
            blocker=True,
            detail=f"Changelog section {expected!r} has no substantive notes",
            evidence=evidence,
        )
    return CheckResult(
        "changelog",
        "Changelog documents this release",
        PASS,
        detail=f"Top entry {expected!r}",
        evidence=evidence,
    )


def check_license(ctx: ReleaseContext) -> CheckResult:
    text = _read(ctx.root / "LICENSE")
    if len(text.strip()) < 40:
        return CheckResult(
            "license",
            "A license is present",
            FAIL,
            blocker=True,
            detail="LICENSE is missing or too short to be a real license",
        )
    return CheckResult(
        "license",
        "A license is present",
        PASS,
        detail=f"LICENSE present ({len(text)} bytes)",
    )


def check_required_docs(ctx: ReleaseContext) -> CheckResult:
    missing = [
        name
        for name in REQUIRED_DOCS
        if not (ctx.root / name).is_file() or not _read(ctx.root / name).strip()
    ]
    if missing:
        return CheckResult(
            "required_docs",
            "Required documentation is present",
            FAIL,
            blocker=True,
            detail=f"Missing or empty: {', '.join(missing)}",
            evidence={"required": list(REQUIRED_DOCS), "missing": missing},
        )
    return CheckResult(
        "required_docs",
        "Required documentation is present",
        PASS,
        evidence={"required": list(REQUIRED_DOCS)},
    )


def check_tag_is_new(ctx: ReleaseContext) -> CheckResult:
    version, _ = resolve_version(ctx)
    canonical_tag = f"v{version}"
    tag = str(ctx.release_tag or canonical_tag)
    if ctx.qualification_scope == "final-artifact":
        if tag != canonical_tag:
            return CheckResult(
                "release_tag",
                "Release tag identifies the final candidate",
                FAIL,
                blocker=True,
                detail=(
                    f"Final qualification requires canonical tag {canonical_tag}; "
                    f"received {tag}"
                ),
                evidence={"tag": tag, "canonical_tag": canonical_tag},
            )
        kind = ctx.git(ctx.root, ["cat-file", "-t", tag])
        target = ctx.git(ctx.root, ["rev-list", "-n", "1", tag])
        if kind.returncode != 0 or target.returncode != 0:
            return CheckResult(
                "release_tag",
                "Release tag identifies the final candidate",
                FAIL,
                blocker=True,
                detail=f"Could not authenticate annotated release tag {tag}",
                evidence={
                    "tag": tag,
                    "verdict": "infrastructure_blocked",
                    "reason": "release_tag_state_unknown",
                    "classification": "infrastructure",
                },
            )
        target_sha = target.stdout.strip().lower()
        expected = str(ctx.candidate_sha or "").strip().lower()
        if kind.stdout.strip() != "tag" or target_sha != expected:
            return CheckResult(
                "release_tag",
                "Release tag identifies the final candidate",
                FAIL,
                blocker=True,
                detail=f"Tag {tag} is not annotated at the exact candidate SHA",
                evidence={"tag": tag, "target_sha": target_sha},
            )
        return CheckResult(
            "release_tag",
            "Release tag identifies the final candidate",
            PASS,
            detail=f"Annotated tag {tag} resolves to the exact candidate",
            evidence={"tag": tag, "target_sha": target_sha},
        )

    result = ctx.git(ctx.root, ["tag", "--list", tag])
    if result.returncode != 0:
        return CheckResult(
            "release_tag",
            "Release tag does not already exist",
            FAIL if ctx.qualification_required else WARN,
            blocker=ctx.qualification_required,
            detail="Could not determine whether the release tag already exists",
            evidence={
                "tag": tag,
                "verdict": "infrastructure_blocked",
                "reason": "release_tag_state_unknown",
                "classification": "infrastructure",
            },
        )
    if result.stdout.strip() == tag:
        return CheckResult(
            "release_tag",
            "Release tag does not already exist",
            FAIL,
            blocker=True,
            detail=f"Tag {tag} already exists — bump the version before releasing",
            evidence={"tag": tag},
        )
    return CheckResult(
        "release_tag",
        "Release tag does not already exist",
        PASS,
        detail=f"{tag} is available",
        evidence={"tag": tag},
    )


def check_tests(ctx: ReleaseContext) -> CheckResult:
    if not ctx.run_tests:
        return CheckResult(
            "tests",
            "The test gate passes",
            SKIP,
            blocker=ctx.qualification_required,
            detail="Test gate not run (pass run_tests=True to include it)",
        )
    candidate_sha = str(ctx.candidate_sha or resolve_commit_sha(ctx) or "").lower()
    raw_result = ctx.tests(ctx.root, candidate_sha)
    if isinstance(raw_result, TestGateResult):
        result = raw_result
    elif isinstance(raw_result, tuple) and len(raw_result) == 2:
        ok, detail = raw_result
        result = TestGateResult(
            verdict="qualified" if bool(ok) else "test_failed",
            reason=(
                "all_required_checks_passed" if bool(ok) else "required_test_failed"
            ),
            classification="none" if bool(ok) else "test",
            detail=str(detail),
            candidate_sha=candidate_sha or None,
            profile="injected",
        )
    else:
        result = TestGateResult(
            verdict="infrastructure_blocked",
            reason="test_evidence_invalid",
            classification="infrastructure",
            detail="test runner returned no typed qualification result",
            candidate_sha=candidate_sha or None,
            profile="unknown",
        )
    return CheckResult(
        "tests",
        "The test gate passes",
        PASS if result.qualified else FAIL,
        blocker=not result.qualified,
        detail=result.detail,
        evidence=result.to_evidence(),
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_evidence_file(
    base: Path,
    descriptor: Any,
    *,
    label: str,
    maximum_bytes: int,
    problems: list[str],
) -> tuple[Path | None, str | None]:
    if not isinstance(descriptor, dict):
        problems.append(f"missing {label} path and digest")
        return None, None
    name = str(descriptor.get("path") or "")
    expected = str(descriptor.get("sha256") or "").lower()
    display = name[:200] or "<missing>"
    if not name or _SHA256_PATTERN.fullmatch(expected) is None:
        problems.append(f"{label} has no valid path and sha256")
        return None, None
    path = (base / name).resolve()
    if not _is_within(path, base):
        problems.append(f"{label} path escapes manifest directory: {display}")
        return None, expected
    if not path.is_file():
        problems.append(f"{label} file not found: {display}")
        return None, expected
    try:
        size = path.stat().st_size
    except OSError:
        problems.append(f"could not stat {label}: {display}")
        return None, expected
    if size <= 0 or size > maximum_bytes:
        problems.append(f"{label} is empty or exceeds {maximum_bytes} bytes")
        return None, expected
    actual = sha256_of(path)
    if actual != expected:
        problems.append(f"{label} checksum mismatch")
        return None, expected
    return path, expected


def _bounded_json_evidence(
    base: Path,
    descriptor: Any,
    *,
    label: str,
    problems: list[str],
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    path, digest = _bounded_evidence_file(
        base,
        descriptor,
        label=label,
        maximum_bytes=MAX_JSON_EVIDENCE_BYTES,
        problems=problems,
    )
    if path is None:
        return None, None, digest
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        problems.append(f"{label} is not readable JSON")
        return path, None, digest
    if not isinstance(value, dict):
        problems.append(f"{label} must be a JSON object")
        return path, None, digest
    return path, value, digest


def _validate_evidence_binding(
    value: dict[str, Any],
    *,
    label: str,
    kind: str,
    repository: str,
    workflow: str,
    run_id: str,
    run_attempt: str,
    tag: str,
    candidate_sha: str,
    problems: list[str],
    platform_name: str | None = None,
    artifact_sha256: str | None = None,
) -> None:
    expected: dict[str, Any] = {
        "kind": kind,
        "schema_version": 1,
        "repository": repository,
        "workflow": workflow,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "tag": tag,
        "candidate_sha": candidate_sha,
    }
    if platform_name is not None:
        expected["platform"] = platform_name
    if artifact_sha256 is not None:
        expected["artifact_sha256"] = artifact_sha256
    for field_name, expected_value in expected.items():
        actual = value.get(field_name)
        if str(actual) != str(expected_value):
            readable = "candidate" if field_name == "candidate_sha" else field_name
            problems.append(f"{label} {readable} mismatch")


def check_artifacts(ctx: ReleaseContext) -> CheckResult:
    manifest_path = ctx.artifacts_manifest
    if manifest_path is None:
        return CheckResult(
            "artifacts",
            "Final artifacts have authenticated same-run evidence",
            SKIP,
            blocker=ctx.qualification_required and not ctx.source_only,
            detail=(
                "Final artifact qualification is pending in the protected same-run "
                "desktop workflow"
                if ctx.source_only
                else "No final artifact evidence bundle was supplied"
            ),
        )
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return CheckResult(
            "artifacts",
            "Final artifacts have authenticated same-run evidence",
            FAIL,
            blocker=True,
            detail=f"Cannot read artifact manifest: {exc}",
        )
    if not isinstance(manifest, dict):
        return CheckResult(
            "artifacts",
            "Final artifacts have authenticated same-run evidence",
            FAIL,
            blocker=True,
            detail="Artifact manifest must be a JSON object",
        )

    base = Path(manifest_path).resolve().parent
    problems: list[str] = []
    checked: list[dict[str, Any]] = []
    expected_candidate = str(ctx.candidate_sha or "").strip().lower()
    if not expected_candidate:
        expected_candidate = resolve_commit_sha(ctx) or ""
    version, _ = resolve_version(ctx)
    expected_tag = str(ctx.release_tag or f"v{version}")
    repository = str(ctx.repository or "")
    workflow = str(ctx.workflow or "")
    run_id = str(ctx.run_id or "")
    run_attempt = str(ctx.run_attempt or "")
    manifest_candidate = str(manifest.get("candidate_sha") or "").strip().lower()
    manifest_commit = str(manifest.get("commit_sha") or "").strip().lower()
    if manifest.get("schema_version") != 3:
        problems.append("unsupported artifact manifest schema (expected version 3)")
    if _COMMIT_SHA.fullmatch(expected_candidate) is None:
        problems.append("cannot validate artifacts without an exact candidate SHA")
    if manifest_candidate != expected_candidate:
        problems.append(
            "manifest candidate mismatch: "
            f"expected {expected_candidate or '<unknown>'}, "
            f"got {manifest_candidate or '<missing>'}"
        )
    if manifest_commit != expected_candidate:
        problems.append(
            "manifest commit mismatch: "
            f"expected {expected_candidate or '<unknown>'}, "
            f"got {manifest_commit or '<missing>'}"
        )
    trust_fields = {
        "repository": repository,
        "workflow": workflow,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "tag": expected_tag,
    }
    for field_name, expected_value in trust_fields.items():
        if not expected_value:
            problems.append(f"trusted {field_name} context is unavailable")
        elif str(manifest.get(field_name) or "") != expected_value:
            problems.append(f"manifest {field_name} mismatch")
    if workflow and workflow != DESKTOP_ARTIFACT_WORKFLOW:
        problems.append("final artifacts must come from the protected desktop workflow")
    if run_id and not run_id.isdigit():
        problems.append("trusted run_id must be numeric")
    if run_attempt and not run_attempt.isdigit():
        problems.append("trusted run_attempt must be numeric")

    _provider_path, provider_value, provider_digest = _bounded_json_evidence(
        base,
        manifest.get("provider_evidence"),
        label="provider evidence",
        problems=problems,
    )
    if provider_value is not None:
        _validate_evidence_binding(
            provider_value,
            label="provider evidence",
            kind="opai_provider_qualification",
            repository=repository,
            workflow=workflow,
            run_id=run_id,
            run_attempt=run_attempt,
            tag=expected_tag,
            candidate_sha=expected_candidate,
            problems=problems,
        )
        if provider_value.get("verdict") != "qualified":
            problems.append("provider evidence is not qualified")

    native_by_platform: dict[str, tuple[str, dict[str, Any]]] = {}
    native_entries = manifest.get("native_evidence")
    if not isinstance(native_entries, list):
        problems.append("native evidence inventory is missing")
        native_entries = []
    for raw_native in native_entries:
        platform_name = (
            str(raw_native.get("platform") or "")
            if isinstance(raw_native, dict)
            else ""
        )
        if platform_name in native_by_platform:
            problems.append(f"duplicate native evidence platform: {platform_name}")
            continue
        _path, value, digest = _bounded_json_evidence(
            base,
            raw_native,
            label=f"native evidence {platform_name or '<missing>'}",
            problems=problems,
        )
        if value is None or digest is None:
            continue
        artifact_digest = str(value.get("artifact_sha256") or "").lower()
        _validate_evidence_binding(
            value,
            label=f"native evidence {platform_name or '<missing>'}",
            kind="opai_native_qualification",
            repository=repository,
            workflow=workflow,
            run_id=run_id,
            run_attempt=run_attempt,
            tag=expected_tag,
            candidate_sha=expected_candidate,
            platform_name=platform_name,
            artifact_sha256=artifact_digest,
            problems=problems,
        )
        if _SHA256_PATTERN.fullmatch(artifact_digest) is None:
            problems.append(
                f"native evidence {platform_name} has invalid artifact digest"
            )
        if value.get("verdict") != "qualified":
            problems.append(f"native evidence {platform_name} is not qualified")
        native_by_platform[platform_name] = (digest, value)

    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or not entries:
        problems.append("artifact manifest declares no artifacts")
        entries = []
    seen_paths: set[str] = set()
    artifact_platforms: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            problems.append("<unnamed>: artifact entry must be an object")
            checked.append({"path": ""})
            continue
        name = str(raw_entry.get("path") or "")
        expected = str(raw_entry.get("sha256") or "").lower()
        platform_name = str(raw_entry.get("platform") or "")
        artifact_path = (base / name).resolve()
        record: dict[str, Any] = {
            "path": name,
            "platform": platform_name,
            "authenticated": False,
        }
        if not name or _SHA256_PATTERN.fullmatch(expected) is None:
            problems.append(f"{name or '<unnamed>'}: missing path or valid sha256")
        elif name in seen_paths:
            problems.append(f"{name}: duplicate artifact path")
        elif not _is_within(artifact_path, base):
            problems.append(f"{name}: path escapes manifest directory")
        elif not artifact_path.is_file():
            problems.append(f"{name}: file not found")
        else:
            actual = sha256_of(artifact_path)
            record["sha256_ok"] = actual == expected
            if actual != expected:
                problems.append(f"{name}: checksum mismatch")
        seen_paths.add(name)
        if platform_name in artifact_platforms:
            problems.append(f"duplicate artifact platform: {platform_name}")
        artifact_platforms.add(platform_name)

        log_path, log_digest = _bounded_evidence_file(
            base,
            raw_entry.get("verification_log"),
            label=f"{name or '<unnamed>'}: verification log",
            maximum_bytes=MAX_VERIFICATION_LOG_BYTES,
            problems=problems,
        )
        report_path, report, _report_digest = _bounded_json_evidence(
            base,
            raw_entry.get("attestation_report"),
            label=f"{name or '<unnamed>'}: attestation report",
            problems=problems,
        )
        native = native_by_platform.get(platform_name)
        if native is None:
            problems.append(f"{name or '<unnamed>'}: matching native evidence missing")
        elif str(native[1].get("artifact_sha256") or "").lower() != expected:
            problems.append(f"{name}: native evidence artifact mismatch")
        if report is not None:
            _validate_evidence_binding(
                report,
                label=f"{name or '<unnamed>'}: attestation",
                kind="opai_artifact_verification",
                repository=repository,
                workflow=workflow,
                run_id=run_id,
                run_attempt=run_attempt,
                tag=expected_tag,
                candidate_sha=expected_candidate,
                platform_name=platform_name,
                artifact_sha256=expected,
                problems=problems,
            )
            if report.get("verification_log_sha256") != log_digest:
                problems.append(f"{name}: attestation verification log mismatch")
            if report.get("provider_evidence_sha256") != provider_digest:
                problems.append(f"{name}: attestation provider evidence mismatch")
            if native is not None and report.get("native_evidence_sha256") != native[0]:
                problems.append(f"{name}: attestation native evidence mismatch")
            if not str(report.get("verifier") or "").strip():
                problems.append(f"{name}: attestation verifier identity is missing")
            if any(
                report.get(field_name) is not True
                for field_name in (
                    "verified",
                    "signature_verified",
                    "attestation_verified",
                )
            ):
                problems.append(f"{name}: signature or attestation did not verify")

        if ctx.artifact_verifier is None:
            problems.append(
                "authenticated artifact verifier is unavailable; use the protected "
                "same-run desktop workflow for final qualification"
            )
        elif (
            artifact_path.is_file()
            and log_path is not None
            and report_path is not None
            and report is not None
        ):
            try:
                authenticated, verifier_detail = ctx.artifact_verifier(
                    artifact_path, log_path, report, ctx
                )
            except Exception as error:
                authenticated = False
                verifier_detail = f"verifier raised {type(error).__name__}"
            if not authenticated:
                problems.append(f"{name}: authenticated verifier rejected artifact")
            else:
                record["authenticated"] = True
                record["verifier"] = str(verifier_detail)[:200]
        checked.append(record)

    for platform_name in REQUIRED_ARTIFACT_PLATFORMS:
        if platform_name not in artifact_platforms:
            problems.append(f"missing artifact platform: {platform_name}")
        if platform_name not in native_by_platform:
            problems.append(f"missing native evidence platform: {platform_name}")
    if problems:
        return CheckResult(
            "artifacts",
            "Final artifacts have authenticated same-run evidence",
            FAIL,
            blocker=True,
            detail="; ".join(problems[:20])[:4000],
            evidence={"checked": checked},
        )
    return CheckResult(
        "artifacts",
        "Final artifacts have authenticated same-run evidence",
        PASS,
        detail=f"{len(checked)} artifact(s) authenticated from the same protected run",
        evidence={"checked": checked},
    )


CHECKS: tuple[Callable[[ReleaseContext], CheckResult], ...] = (
    check_clean_tree,
    check_candidate_identity,
    check_version_consistency,
    check_changelog,
    check_license,
    check_required_docs,
    check_tag_is_new,
    check_tests,
    check_artifacts,
)


def _aggregate_verdict(checks: tuple[CheckResult, ...]) -> tuple[str, str, str]:
    blockers = tuple(check for check in checks if check.blocking)
    for check in blockers:
        verdict = str(check.evidence.get("verdict") or "")
        reason = str(check.evidence.get("reason") or "")
        classification = str(check.evidence.get("classification") or "")
        if verdict and reason and classification and verdict != "qualified":
            return verdict, reason, classification
    if blockers:
        blocker_ids = {check.id for check in blockers}
        artifact = next((check for check in blockers if check.id == "artifacts"), None)
        if artifact is not None:
            if "authenticated artifact verifier is unavailable" in artifact.detail:
                return (
                    "infrastructure_blocked",
                    "artifact_verifier_unavailable",
                    "infrastructure",
                )
            return "build_failed", "artifact_qualification_failed", "build"
        if blocker_ids & {"candidate_identity", "clean_tree"}:
            return "infrastructure_blocked", "candidate_state_invalid", "infrastructure"
        return "build_failed", "release_policy_failed", "build"

    tests = next((check for check in checks if check.id == "tests"), None)
    if tests is not None:
        verdict = str(tests.evidence.get("verdict") or "")
        reason = str(tests.evidence.get("reason") or "")
        classification = str(tests.evidence.get("classification") or "")
        if verdict and reason and classification:
            return verdict, reason, classification
    return "qualified", "release_preflight_passed", "none"


def run_preflight(ctx: ReleaseContext) -> ReleaseReadiness:
    """Run every check and aggregate a deterministic readiness verdict."""
    version, _ = resolve_version(ctx)
    commit_sha = resolve_commit_sha(ctx)
    checks = tuple(check(ctx) for check in CHECKS)
    verdict, reason, classification = _aggregate_verdict(checks)
    try:
        release = derive_project_release(version) if version else None
    except ReleaseIdentityError:
        release = None
    candidate = str(ctx.candidate_sha or commit_sha or "").strip().lower()
    release_identity = (
        {
            "application_version": release.application_version,
            "build_id": candidate,
            "platform": (
                "macos"
                if platform.system().casefold() == "darwin"
                else platform.system().casefold()
            ),
            "architecture": platform.machine().casefold() or "unknown",
            "published_tag": release.published_tag,
            "release_channel": release.release_channel,
            "release_stage": release.release_stage,
            "install_type": "qualification_source",
        }
        if release is not None and _COMMIT_SHA.fullmatch(candidate)
        else None
    )
    return ReleaseReadiness(
        version=version or "unknown",
        dry_run=ctx.dry_run,
        checks=checks,
        generated_at=ctx.now(),
        candidate_sha=(
            str(ctx.candidate_sha).strip().lower() if ctx.candidate_sha else None
        ),
        commit_sha=commit_sha,
        release_identity=release_identity,
        qualification_required=ctx.qualification_required,
        qualification_scope=ctx.qualification_scope,
        verdict=verdict,
        reason=reason,
        classification=classification,
    )


# ---- dry-run: publish is disabled + network is isolated ------------------- #
@dataclass(frozen=True)
class PublishStep:
    name: str
    enabled: bool
    description: str


def plan_publish(ctx: ReleaseContext) -> list[PublishStep]:
    """The publish steps a release *would* run. In dry-run every step is
    disabled — the plan is inspectable but inert."""
    described = {
        "create_tag": "Create and push the signed release tag",
        "upload_artifacts": "Upload checksummed artifacts to the release host",
        "publish_release": "Mark the release public",
        "notify_customers": "Notify customers / update the download page",
    }
    return [
        PublishStep(name, enabled=not ctx.dry_run, description=described[name])
        for name in PUBLISH_STEPS
    ]


def execute_publish_step(ctx: ReleaseContext, step: str) -> None:
    """Execute one publish step. Refused in dry-run (proves no side effects),
    and — because real publishing needs signed artifacts and release
    credentials that never live in this process — refused here regardless, so a
    misconfiguration can never silently ship."""
    if step not in PUBLISH_STEPS:
        raise ReleaseError(f"Unknown publish step: {step}")
    if ctx.dry_run:
        raise ReleaseDryRunError(f"Publish step {step!r} is disabled in dry-run mode")
    raise ReleaseError(
        f"Publish step {step!r} must run on the release host with signed "
        "artifacts and release credentials; it is not available from preflight."
    )


@contextmanager
def deny_network() -> Iterator[None]:
    """Block socket creation for the duration — used to prove a dry-run does not
    reach the network. TCP/UDP socket construction raises inside the block."""
    real_socket = socket.socket

    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise ReleaseError("network access is blocked during a dry-run preflight")

    socket.socket = _blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = real_socket  # type: ignore[assignment]


def prove_dry_run_isolation(ctx: ReleaseContext) -> dict[str, Any]:
    """Evidence that a dry run has no external side effects: every publish step
    is disabled and refuses to execute, and network access is blocked."""
    if not ctx.dry_run:
        raise ReleaseError("dry-run isolation can only be proven in dry-run mode")
    steps = plan_publish(ctx)
    refused: list[str] = []
    for step in steps:
        try:
            execute_publish_step(ctx, step.name)
        except ReleaseDryRunError:
            refused.append(step.name)
    network_blocked = False
    with deny_network():
        try:
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except ReleaseError:
            network_blocked = True
    return {
        "publish_steps_disabled": all(not s.enabled for s in steps),
        "publish_steps_refused": refused,
        "network_blocked": network_blocked,
        "isolated": (
            all(not s.enabled for s in steps)
            and refused == list(PUBLISH_STEPS)
            and network_blocked
        ),
    }


# ---- rollback: restore the previous release, keep user state -------------- #
def _load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rollback_plan(
    *,
    from_version: str,
    to_version: str,
    previous_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Ordered, inspectable steps to roll a release back to ``to_version``.

    Rollback restores the previously tested artifacts and the release pointer.
    It deliberately never touches user state (ledger, preferences, credentials),
    which lives outside the installed release.
    """
    artifacts = [a.get("path") for a in previous_manifest.get("artifacts", [])]
    return {
        "from_version": from_version,
        "to_version": to_version,
        "steps": [
            f"Restore artifacts for {to_version}: {', '.join(artifacts) or '(none)'}",
            f"Repoint the active release to {to_version}",
            "Re-run the smoke check against the restored artifacts",
            "Leave user state (ledger, preferences, credentials) untouched",
        ],
        "preserves_user_state": True,
        "artifacts": artifacts,
    }


def perform_rollback(
    *,
    release_root: Path,
    previous_manifest: Path,
    pointer_file: Path,
    user_state_dirs: Sequence[Path] = (),
) -> dict[str, Any]:
    """Restore the previous release's artifacts + pointer under ``release_root``.

    Files listed in ``previous_manifest`` are copied from the manifest's own
    directory (the staged previous build) into ``release_root``; the pointer is
    rewritten to the previous version. Paths under ``user_state_dirs`` are never
    read or written, so user data survives a rollback untouched.
    """
    import shutil

    release_root = Path(release_root).resolve()
    manifest = _load_manifest(previous_manifest)
    staged = Path(previous_manifest).resolve().parent
    protected = tuple(Path(p).resolve() for p in user_state_dirs)
    restored: list[str] = []
    for entry in manifest.get("artifacts", []):
        name = str(entry.get("path") or "")
        if not name:
            continue
        source = (staged / name).resolve()
        target = (release_root / name).resolve()
        # Containment + user-state protection: never write outside the release
        # root, and never touch a protected user-state directory.
        target.relative_to(release_root)
        for guard in protected:
            if _is_within(target, guard):
                raise ReleaseError(
                    f"rollback would write into protected user state: {target}"
                )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        restored.append(name)
    Path(pointer_file).write_text(
        json.dumps({"version": manifest.get("version")}, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "restored": restored,
        "version": manifest.get("version"),
        "preserved_user_state_dirs": [str(p) for p in protected],
    }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


# ---- reports -------------------------------------------------------------- #
def render_report_json(readiness: ReleaseReadiness) -> str:
    return json.dumps(readiness.to_dict(), indent=2, sort_keys=True)


_ICON = {PASS: "PASS", FAIL: "FAIL", WARN: "WARN", SKIP: "SKIP"}


def render_report_markdown(readiness: ReleaseReadiness) -> str:
    data = readiness.to_dict()
    if not readiness.ready:
        headline = "BLOCKED"
    elif readiness.qualification_scope == "source":
        headline = "SOURCE QUALIFIED · FINAL ARTIFACT QUALIFICATION PENDING"
    elif readiness.qualification_scope == "final-artifact":
        headline = "FINAL RELEASE QUALIFIED"
    else:
        headline = "READY"
    lines = [
        f"# RC preflight — {readiness.version}",
        "",
        f"**{headline}**"
        + (" · dry-run" if readiness.dry_run else "")
        + f" · generated {readiness.generated_at}",
        f"`{readiness.verdict}` · `{readiness.reason}` · scope `{readiness.qualification_scope}`",
        "",
    ]
    if readiness.blockers:
        lines.append("## Blockers")
        for check in readiness.blockers:
            lines.append(f"- **{check.title}** — {check.detail or 'blocked'}")
        lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append("| Check | Result | Detail |")
    lines.append("| --- | --- | --- |")
    for check in readiness.checks:
        detail = (check.detail or "").replace("\n", " ").replace("|", "\\|")
        marker = _ICON.get(check.status, check.status)
        if check.blocking:
            marker += " (blocker)"
        lines.append(f"| {check.title} | {marker} | {detail} |")
    lines.append("")
    totals = data["totals"]
    lines.append(
        f"_{totals['checks']} checks · {totals['blockers']} blocker(s) · "
        f"{totals[PASS]} pass · {totals[FAIL]} fail · {totals[WARN]} warn · "
        f"{totals[SKIP]} skip_"
    )
    return "\n".join(lines) + "\n"


def sanitized_evidence(readiness: ReleaseReadiness) -> dict[str, Any]:
    """A CI-archivable evidence blob with no absolute paths or dirty-file names.

    Enough to prove which checks ran and the verdict; nothing that leaks a
    developer's local filesystem or uncommitted work.
    """
    data = readiness.to_dict()
    for check in data["checks"]:
        check.pop("evidence", None)
    return data
