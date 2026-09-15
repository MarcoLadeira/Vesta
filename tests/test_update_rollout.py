from __future__ import annotations

import json
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from opai.update.manifest import ManifestError, verify_manifest
from opai.update.models import UpdateCandidate
from opai.update.report import user_facing
from opai.update.rollout import COUNTERS, IDENTITY, RolloutError, revise_rollout
from test_update_service import NOW, _candidate, _installed, _manifest


def _evidence(candidate):
    return {
        **{name: candidate[name] for name in IDENTITY},
        "observed_at": NOW.isoformat(),
        "counts": {
            **dict.fromkeys(COUNTERS, 0),
            "download_attempts": 10000,
            "install_attempts": 10000,
            "health_confirmations": 10000,
        },
    }


def _revision(*, previous=1, target=5, status="active", evidence=None, candidate=None):
    key = Ed25519PrivateKey.generate()
    candidate = candidate or _candidate(rollout_percentage=previous)
    payload, trust = _manifest(candidate, signing_key=key)
    result = revise_rollout(
        payload,
        trust=trust,
        installed=_installed(),
        keys={"root-1": key},
        release_id="v0.3.0",
        percentage=target,
        status=status,
        reason="release drill",
        evidence=[_evidence(candidate)] if evidence is None else evidence,
        prior_metadata_version=7,
        now=NOW,
    )
    return result, trust


@pytest.mark.parametrize(
    "previous,target", [(0, 1), (1, 5), (5, 25), (25, 50), (50, 100)]
)
def test_every_expansion_requires_healthy_signed_revision(previous, target):
    result, trust = _revision(previous=previous, target=target)
    signed = json.loads(result)["signed"]
    assert signed["metadata_version"] == 8
    assert signed["releases"][0]["rollout_percentage"] == target
    assert signed["rollout_action"]["from_percentage"] == previous
    verified = verify_manifest(
        result,
        trust=trust,
        installed=_installed(),
        cohort=0,
        prior_metadata_version=8,
        now=NOW,
    )
    assert verified.candidate.rollout_percentage == target


@pytest.mark.parametrize("percentage", [0, 1, 5, 25, 50, 100])
def test_signed_cohort_boundaries_cover_exact_percentage(percentage):
    key = Ed25519PrivateKey.generate()
    payload, trust = _manifest(
        _candidate(rollout_percentage=percentage, cohort_start=97), signing_key=key
    )
    included = []
    for cohort in range(100):
        try:
            verify_manifest(
                payload,
                trust=trust,
                installed=_installed(),
                cohort=cohort,
                prior_metadata_version=7,
                now=NOW,
            )
            included.append(cohort)
        except ManifestError as exc:
            assert exc.code == "rollout_excluded"
    assert len(included) == percentage
    assert included == [c for c in range(100) if (c - 97) % 100 < percentage]


@pytest.mark.parametrize("status", ["paused", "yanked", "quarantined"])
def test_halts_are_signed_and_exclude_old_clients_without_health(status):
    result, trust = _revision(target=0, status=status, evidence=[])
    candidate = json.loads(result)["signed"]["releases"][0]
    assert candidate["rollout_percentage"] == 0
    with pytest.raises(ManifestError, match=f"rollout_{status}"):
        verify_manifest(
            result,
            trust=trust,
            installed=_installed(),
            cohort=0,
            prior_metadata_version=8,
            now=NOW,
        )


def test_reduction_does_not_need_health_evidence():
    result, _ = _revision(previous=50, target=5, evidence=[])
    assert json.loads(result)["signed"]["releases"][0]["rollout_percentage"] == 5


def test_expansion_cannot_skip_stages_or_omit_evidence():
    with pytest.raises(RolloutError, match="one stage"):
        _revision(target=100)
    with pytest.raises(RolloutError, match="fresh health"):
        _revision(evidence=[])


@pytest.mark.parametrize(
    "metric",
    [
        "startup_failures",
        "verification_failures",
        "quarantines",
        "unsafe_installs",
        "duplicate_transactions",
        "relaunch_failures",
        "stale_builds",
    ],
)
@pytest.mark.parametrize("target", [1, 5])
def test_bad_health_automatically_pauses_expansion_or_monitoring(metric, target):
    evidence = _evidence(_candidate())
    evidence["counts"][metric] = 1
    result, _ = _revision(target=target, evidence=[evidence])
    signed = json.loads(result)["signed"]
    assert signed["releases"][0]["rollout_status"] == "paused"
    assert signed["releases"][0]["rollout_percentage"] == 0
    assert metric in signed["rollout_action"]["reason"]


@pytest.mark.parametrize(
    "failure", ["stale", "identity", "negative", "unknown", "missing"]
)
def test_untrustworthy_health_cannot_authorize_progression(failure):
    evidence = _evidence(_candidate())
    if failure == "stale":
        evidence["observed_at"] = (NOW - timedelta(minutes=6)).isoformat()
    elif failure == "identity":
        evidence["build_id"] = "another-build"
    elif failure == "negative":
        evidence["counts"]["startup_failures"] = -1
    elif failure == "unknown":
        evidence["workspace_path"] = "private"
    else:
        evidence["counts"].pop("health_confirmations")
    with pytest.raises(RolloutError):
        _revision(evidence=[evidence])


@pytest.mark.parametrize("previous", ["yanked", "quarantined"])
@pytest.mark.parametrize("target,status", [(0, "active"), (0, "paused"), (1, "active")])
def test_terminal_candidate_cannot_be_reactivated_through_zero(
    previous, target, status
):
    with pytest.raises(RolloutError, match="higher fixed"):
        _revision(
            candidate=_candidate(rollout_percentage=0, rollout_status=previous),
            target=target,
            status=status,
        )


@pytest.mark.parametrize("value", [True, 1.5, -1, 101])
def test_rollout_percentage_must_be_bounded_integer(value):
    with pytest.raises(ValueError):
        UpdateCandidate.from_dict(_candidate(rollout_percentage=value))


@pytest.mark.parametrize(
    "category",
    ["candidate_quarantined", "rollout_yanked", "rollout_paused", "rollout_excluded"],
)
def test_release_holds_have_user_facing_reasons(category):
    result = user_facing(
        {"operation": {"state": "policy_blocked", "error_category": category}}
    )
    assert result["title"] == "Update held back"
    assert "elsewhere" not in result["message"]


@pytest.mark.parametrize("confirmed,expected", [(0, "paused"), (1, "active")])
def test_pending_rollback_is_not_counted_as_success(confirmed, expected):
    evidence = _evidence(_candidate())
    evidence["counts"]["rollback_attempts"] = 1
    evidence["counts"]["rollback_confirmations"] = confirmed
    result, _ = _revision(evidence=[evidence])
    assert json.loads(result)["signed"]["releases"][0]["rollout_status"] == expected


@pytest.mark.parametrize("missing,expected", [(5, "active"), (6, "paused")])
def test_activation_threshold_uses_post_launch_confirmation(missing, expected):
    evidence = _evidence(_candidate())
    evidence["counts"]["health_confirmations"] -= missing
    result, _ = _revision(evidence=[evidence])
    assert json.loads(result)["signed"]["releases"][0]["rollout_status"] == expected


def test_operator_command_writes_verified_revision_and_refuses_overwrite(tmp_path):
    import base64
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PrivateFormat,
        NoEncryption,
    )
    from scripts.revise_update_rollout import main

    # Dates in this fixture are fixed; the command receives the same clock.
    from unittest.mock import patch

    key = Ed25519PrivateKey.generate()
    payload, trust = _manifest(signing_key=key)
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(payload)
    for name, value in {
        "trust.json": trust,
        "identity.json": _installed().to_dict(),
        "key.json": {
            "key_id": "root-1",
            "private_key": base64.b64encode(
                key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
            ).decode(),
        },
    }.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "revision.json"
    args = [
        "--manifest",
        str(manifest),
        "--trust",
        str(tmp_path / "trust.json"),
        "--installed-identity",
        str(tmp_path / "identity.json"),
        "--metadata-key",
        str(tmp_path / "key.json"),
        "--prior-metadata-version",
        "7",
        "--release-id",
        "v0.3.0",
        "--percentage",
        "0",
        "--status",
        "paused",
        "--reason",
        "test incident",
        "--output",
        str(output),
    ]
    with patch("scripts.revise_update_rollout.datetime") as clock:
        clock.now.return_value = NOW
        assert main(args) == 0
        original = output.read_bytes()
        assert main(args) == 2
        assert output.read_bytes() == original
    verified = verify_manifest(
        original,
        trust=trust,
        installed=_installed(),
        cohort=0,
        prior_metadata_version=8,
        now=NOW,
        metadata_only=True,
    )
    assert verified.metadata_version == 8
