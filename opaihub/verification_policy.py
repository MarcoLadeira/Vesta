"""Deterministic verification-policy resolution for managed OPai tasks (#538).

This module deliberately declares *what* verification a task needs.  It never
executes repository commands and does not compute a terminal completion verdict;
those responsibilities belong to the structured evidence runner in #539.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .loader import RegistryLoadError, load_registry


POLICY_SCHEMA_VERSION = 1
RESOLVER_SEMANTICS_VERSION = 1
REPOSITORY_POLICY_FILE = "opai-verification-policy.yaml"
TEAM_POLICY_FILE = "opai-team-policy.yaml"

_EDIT_MODES = frozenset({"implement", "ship", "build", "edit", "fix"})
_VALID_REQUIREMENTS = frozenset({"required", "optional", "forbidden", "conditional"})
_VALID_CHECK_KINDS = frozenset(
    {
        "build",
        "lint",
        "typecheck",
        "unit",
        "integration",
        "e2e",
        "static_analysis",
        "security",
        "custom",
    }
)
_NORMAL = re.compile(r"[^a-z0-9]+")
_HUMAN_REVIEW_PATTERNS = (
    (re.compile(r"\blegal\b.*\b(?:approve|approval|review)\b", re.I), "legal approval"),
    (re.compile(r"\bsecurity\b.*\b(?:approve|approval|review)\b", re.I), "security review"),
    (re.compile(r"\b(?:manual|human|maintainer)\b.*\b(?:approve|approval|review)\b", re.I), "human review"),
)


def _normal(value: object) -> str:
    return _NORMAL.sub("_", str(value or "").strip().lower()).strip("_")


def _bounded_text(value: object, *, field_name: str, limit: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if len(text) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return text


@dataclass(frozen=True)
class PolicySource:
    """One trusted layer which contributed to the effective policy."""

    source: str
    reason: str
    status: str = "applied"

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _bounded_text(self.source, field_name="source", limit=64))
        object.__setattr__(self, "reason", _bounded_text(self.reason, field_name="reason"))
        object.__setattr__(self, "status", _normal(self.status) or "applied")

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "reason": self.reason, "status": self.status}


@dataclass(frozen=True)
class PolicyFinding:
    """A lint or compatibility finding that may block verification policy use."""

    code: str
    message: str
    source: str = "resolver"
    severity: str = "error"

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _normal(self.code))
        object.__setattr__(self, "message", _bounded_text(self.message, field_name="message"))
        object.__setattr__(self, "source", _bounded_text(self.source, field_name="source", limit=64))
        object.__setattr__(self, "severity", _normal(self.severity) or "error")

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "source": self.source,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class PolicyCheck:
    """A declared verification check; commands are metadata, never executed here."""

    check_id: str
    kind: str
    requirement: str
    reason: str
    source: str = "builtin"
    command: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ("exit_status", "output_summary")
    timeout_seconds: int = 600
    retries: int = 0

    def __post_init__(self) -> None:
        check_id = _normal(self.check_id)
        kind = _normal(self.kind)
        requirement = _normal(self.requirement)
        if not check_id:
            raise ValueError("check_id is required")
        if kind not in _VALID_CHECK_KINDS:
            raise ValueError(f"unknown check kind: {kind or self.kind}")
        if requirement not in _VALID_REQUIREMENTS:
            raise ValueError(f"invalid check requirement: {self.requirement}")
        if isinstance(self.timeout_seconds, bool) or self.timeout_seconds < 1 or self.timeout_seconds > 86_400:
            raise ValueError("timeout_seconds must be between 1 and 86400")
        if isinstance(self.retries, bool) or self.retries < 0 or self.retries > 5:
            raise ValueError("retries must be between 0 and 5")
        command = tuple(str(item).strip() for item in self.command)
        if any(not item for item in command):
            raise ValueError("command entries must be non-empty")
        object.__setattr__(self, "check_id", check_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "requirement", requirement)
        object.__setattr__(self, "reason", _bounded_text(self.reason, field_name="reason"))
        object.__setattr__(self, "source", _bounded_text(self.source, field_name="source", limit=64))
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "conditions", tuple(_normal(item) for item in self.conditions if _normal(item)))
        object.__setattr__(self, "evidence", tuple(_normal(item) for item in self.evidence if _normal(item)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "kind": self.kind,
            "requirement": self.requirement,
            "reason": self.reason,
            "source": self.source,
            "command": list(self.command),
            "conditions": list(self.conditions),
            "evidence": list(self.evidence),
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
        }


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    requirement: str
    automated: bool
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "criterion_id", _normal(self.criterion_id))
        object.__setattr__(self, "requirement", _bounded_text(self.requirement, field_name="requirement"))
        object.__setattr__(self, "reason", _bounded_text(self.reason, field_name="reason"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.criterion_id,
            "requirement": self.requirement,
            "automated": self.automated,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class HumanReviewRequirement:
    requirement: str
    reason: str
    source: str = "task"

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirement", _bounded_text(self.requirement, field_name="requirement"))
        object.__setattr__(self, "reason", _bounded_text(self.reason, field_name="reason"))
        object.__setattr__(self, "source", _bounded_text(self.source, field_name="source", limit=64))

    def to_dict(self) -> dict[str, str]:
        return {"requirement": self.requirement, "reason": self.reason, "source": self.source}


@dataclass(frozen=True)
class VerificationPolicy:
    """Versioned effective policy returned by all policy-consuming surfaces."""

    status: str
    classification: dict[str, Any]
    checks: tuple[PolicyCheck, ...]
    sources: tuple[PolicySource, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    human_reviews: tuple[HumanReviewRequirement, ...] = ()
    findings: tuple[PolicyFinding, ...] = ()
    schema_version: int = POLICY_SCHEMA_VERSION
    resolver_semantics_version: int = RESOLVER_SEMANTICS_VERSION
    digest: str = field(default="", compare=True)

    def __post_init__(self) -> None:
        status = _normal(self.status)
        if status not in {"ready", "blocked", "degraded"}:
            raise ValueError("status must be ready, blocked, or degraded")
        if self.schema_version != POLICY_SCHEMA_VERSION:
            raise ValueError("unsupported policy schema version")
        if self.resolver_semantics_version != RESOLVER_SEMANTICS_VERSION:
            raise ValueError("unsupported resolver semantics version")
        checks = tuple(self.checks)
        if len({check.check_id for check in checks}) != len(checks):
            raise ValueError("verification policy has duplicate check ids")
        if not isinstance(self.classification, dict):
            raise TypeError("classification must be a dictionary")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "checks", checks)
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "acceptance_criteria", tuple(self.acceptance_criteria))
        object.__setattr__(self, "human_reviews", tuple(self.human_reviews))
        object.__setattr__(self, "findings", tuple(self.findings))
        payload = self.payload_without_digest()
        computed = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        if self.digest and self.digest != computed:
            raise ValueError("verification policy digest does not match content")
        object.__setattr__(self, "digest", computed)

    @property
    def required_checks(self) -> tuple[PolicyCheck, ...]:
        return tuple(check for check in self.checks if check.requirement == "required")

    def payload_without_digest(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "resolver_semantics_version": self.resolver_semantics_version,
            "status": self.status,
            "classification": dict(self.classification),
            "checks": [check.to_dict() for check in self.checks],
            "sources": [source.to_dict() for source in self.sources],
            "acceptance_criteria": [item.to_dict() for item in self.acceptance_criteria],
            "human_reviews": [item.to_dict() for item in self.human_reviews],
            "findings": [item.to_dict() for item in self.findings],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload_without_digest(), "digest": self.digest}

    def safe_summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "resolver_semantics_version": self.resolver_semantics_version,
            "status": self.status,
            "digest": self.digest,
            "required_check_ids": [check.check_id for check in self.required_checks],
            "human_review_requirements": [item.requirement for item in self.human_reviews],
            "finding_codes": [item.code for item in self.findings],
        }


def classify_repository_and_task(root: Path, *, task: str, mode: str) -> dict[str, Any]:
    """Classify trusted local structure and the user task without running tools."""

    path = Path(root).expanduser().resolve()
    task_text = str(task or "").strip()
    if not task_text:
        raise ValueError("task is required")
    normalized_mode = _normal(mode) or "explain"
    lowered = task_text.lower()
    families: list[str] = []
    if any((path / marker).exists() for marker in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        families.append("python")
    if (path / "package.json").exists():
        families.append("node")
    if not families:
        families.append("unknown")
    risks: list[str] = []
    if re.search(r"\b(migrat|schema|database|ddl)\w*\b", lowered):
        risks.append("migration")
    if re.search(r"\b(auth|payment|secret|credential|token|security)\w*\b", lowered):
        risks.append("security")
    if re.search(r"\b(ci|workflow|release|publish|deploy)\w*\b", lowered):
        risks.append("delivery")
    if re.search(r"\b(policy|verification)\w*\b", lowered):
        risks.append("policy")
    docs_only = bool(re.search(r"\b(doc|readme|documentation)\w*\b", lowered)) and not bool(
        re.search(r"\b(fix|implement|code|test|bug|refactor)\w*\b", lowered)
    )
    return {
        "repository_families": families,
        "mode": normalized_mode,
        "edit_capable": normalized_mode in _EDIT_MODES,
        "docs_only": docs_only,
        "risk_categories": risks,
    }


def _builtin_checks(classification: dict[str, Any]) -> list[PolicyCheck]:
    if not classification["edit_capable"]:
        return []
    checks = [
        PolicyCheck(
            "static_analysis",
            "static_analysis",
            "required",
            "Validate the changed source is structurally analyzable.",
        )
    ]
    families = set(classification["repository_families"])
    if "python" in families:
        checks.extend(
            [
                PolicyCheck("lint", "lint", "required", "Lint Python changes."),
                PolicyCheck("unit", "unit", "required", "Run Python unit tests."),
            ]
        )
    if "node" in families:
        checks.extend(
            [
                PolicyCheck("lint", "lint", "required", "Lint JavaScript or TypeScript changes."),
                PolicyCheck("unit", "unit", "required", "Run JavaScript or TypeScript unit tests."),
            ]
        )
    if classification["docs_only"]:
        checks = [
            PolicyCheck(
                "static_analysis",
                "static_analysis",
                "required",
                "Validate documentation formatting and references.",
            )
        ]
    if "migration" in classification["risk_categories"]:
        checks.append(
            PolicyCheck("integration", "integration", "required", "Exercise migration compatibility.")
        )
    if "security" in classification["risk_categories"]:
        checks.append(
            PolicyCheck("security", "security", "required", "Run security analysis for sensitive changes.")
        )
    return checks


def _acceptance_from_task(task: str) -> tuple[tuple[AcceptanceCriterion, ...], tuple[HumanReviewRequirement, ...]]:
    lowered = task.lower()
    criteria: list[AcceptanceCriterion] = []
    if re.search(r"\b(test|verify|verification|ci)\b", lowered):
        criteria.append(
            AcceptanceCriterion("tests_pass", "Relevant verification checks pass", True, "Requested explicitly")
        )
    if re.search(r"\b(build|compile)\b", lowered):
        criteria.append(AcceptanceCriterion("build", "Build succeeds", True, "Requested explicitly"))
    reviews: list[HumanReviewRequirement] = []
    for pattern, requirement in _HUMAN_REVIEW_PATTERNS:
        if pattern.search(task):
            reviews.append(HumanReviewRequirement(requirement, "Task requires a human decision."))
    return tuple(criteria), tuple(reviews)


def _policy_finding(code: str, message: str, *, source: str) -> PolicyFinding:
    return PolicyFinding(code, message, source=source, severity="error")


def _load_policy_mapping(path: Path, *, source: str) -> tuple[Mapping[str, Any] | None, PolicyFinding | None]:
    """Load one trusted policy mapping, treating malformed content as a block."""

    try:
        data = load_registry(path)
    except (OSError, RegistryLoadError) as exc:
        return None, _policy_finding("malformed_policy", str(exc), source=source)
    if not isinstance(data, Mapping):
        return None, _policy_finding(
            "invalid_policy_shape", "Verification policy content must be a mapping.", source=source
        )
    return data, None


def _overlay_from_repository(root: Path) -> tuple[Mapping[str, Any] | None, PolicyFinding | None]:
    path = root / REPOSITORY_POLICY_FILE
    if not path.exists():
        return None, None
    return _load_policy_mapping(path, source="repository")


def _overlay_from_team(root: Path) -> tuple[Mapping[str, Any] | None, PolicyFinding | None]:
    path = root / TEAM_POLICY_FILE
    if not path.exists():
        return None, None
    team, finding = _load_policy_mapping(path, source="team")
    if finding is not None or team is None:
        return None, finding
    overlay = team.get("verification_policy")
    if overlay is None:
        return None, None
    if not isinstance(overlay, Mapping):
        return None, _policy_finding(
            "invalid_policy_shape", "team verification_policy must be a mapping.", source="team"
        )
    return overlay, None


def _overlay_check(
    raw: Mapping[str, Any], *, source: str, inherited: PolicyCheck | None
) -> PolicyCheck:
    check_id = _normal(raw.get("id"))
    if not check_id:
        raise ValueError("check id is required")
    kind = _normal(raw.get("kind")) or (inherited.kind if inherited else "")
    requirement = _normal(raw.get("requirement")) or (
        inherited.requirement if inherited else "required"
    )
    reason = str(raw.get("reason") or (inherited.reason if inherited else "")).strip()
    command_raw = raw.get("command", inherited.command if inherited else ())
    if isinstance(command_raw, str):
        raise ValueError("command must be an argv list, not a shell string")
    if not isinstance(command_raw, (list, tuple)):
        raise ValueError("command must be a list")
    conditions_raw = raw.get("conditions", inherited.conditions if inherited else ())
    evidence_raw = raw.get("evidence", inherited.evidence if inherited else ("exit_status", "output_summary"))
    if not isinstance(conditions_raw, (list, tuple)) or not isinstance(evidence_raw, (list, tuple)):
        raise ValueError("conditions and evidence must be lists")
    timeout = raw.get("timeout_seconds", inherited.timeout_seconds if inherited else 600)
    retries = raw.get("retries", inherited.retries if inherited else 0)
    return PolicyCheck(
        check_id=check_id,
        kind=kind,
        requirement=requirement,
        reason=reason,
        source=source,
        command=tuple(str(item) for item in command_raw),
        conditions=tuple(str(item) for item in conditions_raw),
        evidence=tuple(str(item) for item in evidence_raw),
        timeout_seconds=timeout,
        retries=retries,
    )


def _apply_overlay(
    checks: list[PolicyCheck],
    overlay: Mapping[str, Any],
    *,
    source: str,
    findings: list[PolicyFinding],
) -> None:
    """Apply an overlay only when it cannot weaken inherited requirements."""

    supported = {"schema_version", "checks", "human_reviews"}
    for field_name in overlay:
        if str(field_name) not in supported:
            findings.append(
                _policy_finding(
                    "unknown_policy_field",
                    f"Unsupported verification policy field: {field_name}",
                    source=source,
                )
            )
    version = overlay.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != POLICY_SCHEMA_VERSION:
        findings.append(
            _policy_finding(
                "schema_incompatible",
                f"Policy schema_version must be {POLICY_SCHEMA_VERSION}.",
                source=source,
            )
        )
        return
    raw_checks = overlay.get("checks", [])
    if not isinstance(raw_checks, list):
        findings.append(
            _policy_finding("invalid_checks", "checks must be a list.", source=source)
        )
        return
    positions = {check.check_id: index for index, check in enumerate(checks)}
    seen: set[str] = set()
    for raw in raw_checks:
        if not isinstance(raw, Mapping):
            findings.append(
                _policy_finding("invalid_check", "Each check must be a mapping.", source=source)
            )
            continue
        check_id = _normal(raw.get("id"))
        if not check_id:
            findings.append(_policy_finding("invalid_check", "Check id is required.", source=source))
            continue
        if check_id in seen:
            findings.append(
                _policy_finding("duplicate_check_id", f"Duplicate check id: {check_id}", source=source)
            )
            continue
        seen.add(check_id)
        inherited = checks[positions[check_id]] if check_id in positions else None
        try:
            candidate = _overlay_check(raw, source=source, inherited=inherited)
        except (TypeError, ValueError) as exc:
            findings.append(_policy_finding("invalid_check", str(exc), source=source))
            continue
        if inherited is not None:
            if inherited.requirement == "required" and candidate.requirement != "required":
                findings.append(
                    _policy_finding(
                        "required_check_downgrade",
                        f"{check_id} is required by {inherited.source} and cannot be downgraded.",
                        source=source,
                    )
                )
                continue
            if candidate.kind != inherited.kind:
                findings.append(
                    _policy_finding(
                        "check_kind_conflict",
                        f"{check_id} cannot change kind from {inherited.kind} to {candidate.kind}.",
                        source=source,
                    )
                )
                continue
            checks[positions[check_id]] = candidate
        else:
            positions[check_id] = len(checks)
            checks.append(candidate)


def _overlay_human_reviews(
    overlay: Mapping[str, Any], *, source: str, findings: list[PolicyFinding]
) -> tuple[HumanReviewRequirement, ...]:
    raw_reviews = overlay.get("human_reviews", [])
    if not isinstance(raw_reviews, list):
        findings.append(
            _policy_finding("invalid_human_reviews", "human_reviews must be a list.", source=source)
        )
        return ()
    reviews: list[HumanReviewRequirement] = []
    for raw in raw_reviews:
        if not isinstance(raw, Mapping):
            findings.append(
                _policy_finding("invalid_human_review", "Each human review must be a mapping.", source=source)
            )
            continue
        try:
            reviews.append(
                HumanReviewRequirement(
                    raw.get("requirement"), raw.get("reason"), source=source
                )
            )
        except ValueError as exc:
            findings.append(_policy_finding("invalid_human_review", str(exc), source=source))
    return tuple(reviews)


def resolve_verification_policy(
    root: Path,
    *,
    task: str,
    mode: str,
    delivery: str = "local",
) -> VerificationPolicy:
    """Resolve trusted built-in defaults for a task without command execution."""

    normalized_delivery = _normal(delivery) or "local"
    if normalized_delivery not in {"local", "ship"}:
        raise ValueError("delivery must be local or ship")
    classification = classify_repository_and_task(root, task=task, mode=mode)
    criteria, reviews = _acceptance_from_task(task)
    checks = _builtin_checks(classification)
    findings: list[PolicyFinding] = []
    if classification["edit_capable"] and set(classification["repository_families"]) == {"unknown"}:
        reviews += (
            HumanReviewRequirement(
                "verification tooling review",
                "Repository tooling could not be classified from local structure.",
                source="builtin",
            ),
        )
        findings.append(
            PolicyFinding(
                "tooling_unavailable",
                "No supported repository family was detected; required tooling must be reviewed.",
                severity="warning",
            )
        )
    root_path = Path(root).expanduser().resolve()
    sources: list[PolicySource] = [PolicySource("builtin", "Versioned OPai safe defaults")]
    for source, loader in (("team", _overlay_from_team), ("repository", _overlay_from_repository)):
        overlay, finding = loader(root_path)
        if finding is not None:
            findings.append(finding)
            sources.append(PolicySource(source, "Policy could not be loaded safely", status="blocked"))
            continue
        if overlay is None:
            continue
        _apply_overlay(checks, overlay, source=source, findings=findings)
        reviews += _overlay_human_reviews(overlay, source=source, findings=findings)
        sources.append(PolicySource(source, "Trusted policy overlay applied"))
    status = "blocked" if any(finding.severity == "error" for finding in findings) else "ready"
    return VerificationPolicy(
        status=status,
        classification={**classification, "delivery": normalized_delivery},
        checks=tuple(checks),
        sources=tuple(sources),
        acceptance_criteria=criteria,
        human_reviews=reviews,
        findings=tuple(findings),
    )
