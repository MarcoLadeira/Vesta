"""Versioned verification-policy resolution and durable artifacts (#538)."""

from __future__ import annotations

from pathlib import Path

from opaihub.verification_policy import resolve_verification_policy


def test_python_edit_policy_is_deterministic_and_records_provenance(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n", encoding="utf-8"
    )

    first = resolve_verification_policy(
        tmp_path, task="Fix parser and run tests.", mode="implement"
    )
    second = resolve_verification_policy(
        tmp_path, task="Fix parser and run tests.", mode="implement"
    )

    assert first.digest == second.digest
    assert first.status == "ready"
    assert first.sources[0].source == "builtin"
    assert {check.kind for check in first.required_checks} >= {"unit", "lint"}


def test_unautomatable_acceptance_is_visible_human_review(tmp_path: Path) -> None:
    policy = resolve_verification_policy(
        tmp_path,
        task="Update docs and have legal approve the disclosure wording.",
        mode="implement",
    )

    assert any(review.requirement == "legal approval" for review in policy.human_reviews)


def test_repository_overlay_cannot_downgrade_inherited_required_check(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n", encoding="utf-8"
    )
    (tmp_path / "opai-verification-policy.yaml").write_text(
        "schema_version: 1\nchecks:\n  - id: unit\n    requirement: optional\n",
        encoding="utf-8",
    )

    policy = resolve_verification_policy(tmp_path, task="Fix code", mode="implement")

    assert policy.status == "blocked"
    assert any(finding.code == "required_check_downgrade" for finding in policy.findings)


def test_malformed_repository_policy_never_returns_permissive_policy(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n", encoding="utf-8"
    )
    (tmp_path / "opai-verification-policy.yaml").write_text("checks: [", encoding="utf-8")

    policy = resolve_verification_policy(tmp_path, task="Fix code", mode="implement")

    assert policy.status == "blocked"
    assert policy.required_checks
    assert any(finding.code == "malformed_policy" for finding in policy.findings)


def test_team_and_repository_overlays_record_their_source_hierarchy(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n", encoding="utf-8"
    )
    (tmp_path / "opai-team-policy.yaml").write_text(
        "team: demo\nverification_policy:\n  schema_version: 1\n  checks:\n"
        "    - id: security\n      kind: security\n      requirement: required\n"
        "      reason: Team requires security analysis.\n",
        encoding="utf-8",
    )
    (tmp_path / "opai-verification-policy.yaml").write_text(
        "schema_version: 1\nchecks:\n  - id: integration\n    kind: integration\n"
        "    requirement: required\n    reason: Repository requires integration coverage.\n",
        encoding="utf-8",
    )

    policy = resolve_verification_policy(tmp_path, task="Fix code", mode="implement")

    assert policy.status == "ready"
    assert [source.source for source in policy.sources] == [
        "builtin",
        "team",
        "repository",
    ]
    assert {check.check_id for check in policy.required_checks} >= {
        "security",
        "integration",
    }
