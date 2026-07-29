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
