"""Reproducible release-candidate preflight (#32).

A closed-source release candidate needs a repeatable, inspectable process that
can prove what *would* ship before any artifact is published. This module is
that process: one deterministic readiness result assembled from independent,
individually inspectable checks —

- the working tree is clean,
- the version is consistent across pyproject and both packages,
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
import re
import socket
import subprocess  # nosec B404 - fixed git argv, never a shell
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

# ---- result model --------------------------------------------------------- #
PASS = "pass"  # nosec B105 - a check-status literal, not a credential
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


class ReleaseError(RuntimeError):
    """A release operation was refused because it is unsafe or disabled."""


class ReleaseDryRunError(ReleaseError):
    """A side-effectful publish step was attempted during a dry run."""


@dataclass(frozen=True)
class CheckResult:
    """One inspectable release check. A ``blocker`` FAIL stops readiness."""

    id: str
    title: str
    status: str
    blocker: bool = False
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.status == FAIL and self.blocker

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
class ReleaseReadiness:
    """The aggregate verdict over every check."""

    version: str
    dry_run: bool
    checks: tuple[CheckResult, ...]
    generated_at: str

    @property
    def blockers(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.blocking)

    @property
    def ready(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {PASS: 0, FAIL: 0, WARN: 0, SKIP: 0}
        for check in self.checks:
            counts[check.status] = counts.get(check.status, 0) + 1
        return {
            "kind": "opai_rc_preflight",
            "version": self.version,
            "dry_run": self.dry_run,
            "ready": self.ready,
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
TestRunner = Callable[[Path], "tuple[bool, str]"]
Clock = Callable[[], str]


def _default_git(root: Path, args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(  # nosec B603 B607 - fixed git argv, no shell
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _default_tests(root: Path) -> tuple[bool, str]:
    completed = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(root / "scripts" / "ci_local.py"), "--fast"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    tail = (completed.stdout or completed.stderr or "").strip().splitlines()[-3:]
    return completed.returncode == 0, "\n".join(tail)


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
    git: GitRunner = _default_git
    tests: TestRunner = _default_tests
    now: Clock = _default_clock

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()


# ---- version identity ----------------------------------------------------- #
_PYPROJECT_VERSION = re.compile(r'(?m)^\s*version\s*=\s*"([^"]+)"')
_DUNDER_VERSION = re.compile(r'(?m)^\s*__version__\s*=\s*"([^"]+)"')
_STAGE = re.compile(r'(?m)^\s*__release_stage__\s*=\s*"([^"]+)"')
_PEP440 = re.compile(r"^(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?$")
_STAGE_KIND = {"a": "alpha", "b": "beta", "rc": "rc"}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


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
    pyproject = _first(_PYPROJECT_VERSION, _read(root / "pyproject.toml"))
    opai_v = _first(_DUNDER_VERSION, _read(root / "opai" / "__init__.py"))
    hub_v = _first(_DUNDER_VERSION, _read(root / "opaihub" / "__init__.py"))
    stage = _first(_STAGE, _read(root / "opai" / "__init__.py"))
    found = {
        "pyproject.toml": pyproject,
        "opai/__init__.py": opai_v,
        "opaihub/__init__.py": hub_v,
    }
    versions = {v for v in found.values() if v}
    evidence = {"versions": found, "release_stage": stage}
    if None in found.values():
        missing = [name for name, v in found.items() if not v]
        return CheckResult(
            "version_consistency",
            "Version is declared consistently",
            FAIL,
            blocker=True,
            detail=f"Version not found in: {', '.join(missing)}",
            evidence=evidence,
        )
    if len(versions) != 1:
        return CheckResult(
            "version_consistency",
            "Version is declared consistently",
            FAIL,
            blocker=True,
            detail=f"Version mismatch across sources: {found}",
            evidence=evidence,
        )
    version = next(iter(versions))
    if changelog_heading_for(version, stage or "") is None:
        return CheckResult(
            "version_consistency",
            "Version is declared consistently",
            FAIL,
            blocker=True,
            detail=(
                f"PEP 440 version {version!r} and release stage {stage!r} disagree"
            ),
            evidence=evidence,
        )
    return CheckResult(
        "version_consistency",
        "Version is declared consistently",
        PASS,
        detail=f"{version} ({stage})",
        evidence=evidence,
    )


def resolve_version(ctx: ReleaseContext) -> tuple[str, str]:
    """The single agreed version + stage (best-effort; empty on inconsistency)."""
    root = ctx.root
    version = _first(_DUNDER_VERSION, _read(root / "opai" / "__init__.py")) or ""
    stage = _first(_STAGE, _read(root / "opai" / "__init__.py")) or ""
    return version, stage


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
    tag = f"v{version}"
    result = ctx.git(ctx.root, ["tag", "--list", tag])
    if result.returncode != 0:
        return CheckResult(
            "release_tag",
            "Release tag does not already exist",
            WARN,
            detail="Could not list git tags (not a git checkout?)",
            evidence={"tag": tag},
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
            detail="Test gate not run (pass run_tests=True to include it)",
        )
    ok, detail = ctx.tests(ctx.root)
    return CheckResult(
        "tests",
        "The test gate passes",
        PASS if ok else FAIL,
        blocker=not ok,
        detail=detail,
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_artifacts(ctx: ReleaseContext) -> CheckResult:
    manifest_path = ctx.artifacts_manifest
    if manifest_path is None:
        return CheckResult(
            "artifacts",
            "Declared artifacts exist, match checksums, and are signed",
            SKIP,
            detail="No artifact manifest supplied (build artifacts on the release host)",
        )
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return CheckResult(
            "artifacts",
            "Declared artifacts exist, match checksums, and are signed",
            FAIL,
            blocker=True,
            detail=f"Cannot read artifact manifest: {exc}",
        )
    entries = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not entries:
        return CheckResult(
            "artifacts",
            "Declared artifacts exist, match checksums, and are signed",
            FAIL,
            blocker=True,
            detail="Artifact manifest declares no artifacts",
        )
    base = Path(manifest_path).resolve().parent
    problems: list[str] = []
    checked: list[dict[str, Any]] = []
    for entry in entries:
        name = str((entry or {}).get("path") or "")
        expected = str((entry or {}).get("sha256") or "").lower()
        signed = bool((entry or {}).get("signed"))
        candidate = (base / name).resolve()
        record = {"path": name, "signed": signed}
        if not name or not expected:
            problems.append(f"{name or '<unnamed>'}: missing path or sha256")
        elif not candidate.is_file():
            problems.append(f"{name}: file not found")
        else:
            actual = sha256_of(candidate)
            record["sha256_ok"] = actual == expected
            if actual != expected:
                problems.append(f"{name}: checksum mismatch")
            elif not signed:
                problems.append(f"{name}: unsigned")
        checked.append(record)
    if problems:
        return CheckResult(
            "artifacts",
            "Declared artifacts exist, match checksums, and are signed",
            FAIL,
            blocker=True,
            detail="; ".join(problems[:10]),
            evidence={"checked": checked},
        )
    return CheckResult(
        "artifacts",
        "Declared artifacts exist, match checksums, and are signed",
        PASS,
        detail=f"{len(checked)} artifact(s) verified",
        evidence={"checked": checked},
    )


CHECKS: tuple[Callable[[ReleaseContext], CheckResult], ...] = (
    check_clean_tree,
    check_version_consistency,
    check_changelog,
    check_license,
    check_required_docs,
    check_tag_is_new,
    check_tests,
    check_artifacts,
)


def run_preflight(ctx: ReleaseContext) -> ReleaseReadiness:
    """Run every check and aggregate a deterministic readiness verdict."""
    version, _ = resolve_version(ctx)
    checks = tuple(check(ctx) for check in CHECKS)
    return ReleaseReadiness(
        version=version or "unknown",
        dry_run=ctx.dry_run,
        checks=checks,
        generated_at=ctx.now(),
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
    lines = [
        f"# RC preflight — {readiness.version}",
        "",
        f"**{'READY' if readiness.ready else 'BLOCKED'}**"
        + (" · dry-run" if readiness.dry_run else "")
        + f" · generated {readiness.generated_at}",
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
