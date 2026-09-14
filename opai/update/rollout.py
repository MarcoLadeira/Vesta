"""Health-gated revisions of the canonical signed release manifest.

This module produces metadata for the existing publication pipeline. It never
publishes artifacts, installs software, or creates another eligibility store.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .manifest import _canonical, verify_manifest
from .models import InstalledBuild, UpdateCandidate


class RolloutError(ValueError):
    """An operator request lacks valid release identity or health evidence."""


@dataclass(frozen=True)
class HealthPolicy:
    minimum_samples: int = 1000
    maximum_age_seconds: int = 300
    maximum_download_failure_ppm: int = 10000
    maximum_activation_failure_ppm: int = 500
    maximum_rollback_failure_ppm: int = 100

    def __post_init__(self):
        for name, value in self.__dict__.items():
            if type(value) is not int or value < 0:
                raise RolloutError(f"invalid policy: {name}")
        if self.minimum_samples < 1 or not 1 <= self.maximum_age_seconds <= 300:
            raise RolloutError(
                "health policy needs samples and freshness within five minutes"
            )
        if any(
            value > 1000000
            for name, value in self.__dict__.items()
            if name.endswith("ppm")
        ):
            raise RolloutError(
                "failure threshold exceeds one million parts per million"
            )


# Counts, never prompts, workspace paths, credentials, or arbitrary diagnostics.
COUNTERS = frozenset(
    {
        "download_attempts",
        "download_failures",
        "verification_failures",
        "install_attempts",
        "install_failures",
        "relaunch_failures",
        "health_confirmations",
        "rollback_attempts",
        "rollback_failures",
        "rollback_confirmations",
        "startup_failures",
        "stale_builds",
        "quarantines",
        "unsafe_installs",
        "duplicate_transactions",
    }
)
IDENTITY = (
    "channel",
    "version",
    "build_id",
    "platform",
    "architecture",
    "artifact_sha256",
)
STAGES = (0, 1, 5, 25, 50, 100)


def health_reasons(
    evidence: Mapping,
    candidate: UpdateCandidate,
    *,
    now: datetime,
    policy: HealthPolicy,
) -> tuple[str, ...]:
    """Validate a bounded aggregate and return reasons to halt expansion.

    The release operator must supply counts from a trusted health collector;
    signing the resulting metadata is still the only installation authority.
    Missing, stale, or inconsistent evidence cannot authorize expansion.
    """
    if set(evidence) != set(IDENTITY) | {"observed_at", "counts"}:
        raise RolloutError("health evidence has missing or unapproved fields")
    if any(evidence[name] != getattr(candidate, name) for name in IDENTITY):
        raise RolloutError("health evidence belongs to a different artifact")
    try:
        observed = datetime.fromisoformat(
            str(evidence["observed_at"]).replace("Z", "+00:00")
        )
        age = (now - observed).total_seconds()
    except (ValueError, TypeError) as exc:
        raise RolloutError("health evidence timestamp is invalid") from exc
    if not 0 <= age <= policy.maximum_age_seconds:
        raise RolloutError("health evidence is stale or from the future")
    counts = evidence["counts"]
    if not isinstance(counts, Mapping) or set(counts) != COUNTERS:
        raise RolloutError("health counters are incomplete or unapproved")
    if any(type(n) is not int or not 0 <= n <= 10**12 for n in counts.values()):
        raise RolloutError("health counts must be bounded nonnegative integers")
    if (
        counts["download_failures"] > counts["download_attempts"]
        or counts["install_attempts"]
        > counts["download_attempts"] - counts["download_failures"]
        or counts["health_confirmations"] + counts["install_failures"]
        > counts["install_attempts"]
        or counts["rollback_failures"] + counts["rollback_confirmations"]
        > counts["rollback_attempts"]
        or counts["rollback_attempts"] > counts["install_attempts"]
    ):
        raise RolloutError("health counters are inconsistent")
    reasons = []
    if counts["install_attempts"] < policy.minimum_samples:
        reasons.append("insufficient_health_samples")
    for name in (
        "verification_failures",
        "startup_failures",
        "stale_builds",
        "quarantines",
        "unsafe_installs",
        "duplicate_transactions",
        "relaunch_failures",
    ):
        if counts[name]:
            reasons.append(name)
    for label, failures, attempts, limit in (
        (
            "download_failure_rate",
            counts["download_failures"],
            counts["download_attempts"],
            policy.maximum_download_failure_ppm,
        ),
        (
            "activation_failure_rate",
            counts["install_attempts"] - counts["health_confirmations"],
            counts["install_attempts"],
            policy.maximum_activation_failure_ppm,
        ),
        (
            "rollback_failure_rate",
            counts["rollback_attempts"] - counts["rollback_confirmations"],
            counts["rollback_attempts"],
            policy.maximum_rollback_failure_ppm,
        ),
    ):
        if failures * 1000000 > attempts * limit:
            reasons.append(label)
    return tuple(reasons)


def revise_rollout(
    payload: bytes,
    *,
    trust: Mapping,
    installed: InstalledBuild,
    keys: Mapping[str, Ed25519PrivateKey],
    release_id: str,
    percentage: int,
    status: str,
    reason: str,
    evidence: Sequence[Mapping] = (),
    policy: HealthPolicy | None = None,
    prior_metadata_version: int,
    now: datetime,
) -> bytes:
    """Sign one auditable release-control revision, without publishing it.

    Expansion is staged and requires fresh matching evidence for every artifact.
    A health breach automatically converts a requested expansion to a signed
    zero-percent pause. Explicit pause/yank/reduction needs no health evidence.
    Inactive status also uses zero percent so older clients fail closed.
    """
    policy = policy or HealthPolicy()
    verified = verify_manifest(
        payload,
        trust=trust,
        installed=installed,
        cohort=0,
        prior_metadata_version=prior_metadata_version,
        now=now,
        metadata_only=True,
    )
    if type(percentage) is not int or percentage not in STAGES:
        raise RolloutError("percentage must be a deliberate rollout stage")
    if status not in {"active", "paused", "yanked", "quarantined"}:
        raise RolloutError("unknown rollout status")
    if status != "active" and percentage != 0:
        raise RolloutError("inactive rollout must use zero percent")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 256:
        raise RolloutError("a bounded audit reason is required")
    signed = json.loads(payload)["signed"]
    candidates = [UpdateCandidate.from_dict(raw) for raw in signed["releases"]]
    selected = [
        candidate for candidate in candidates if candidate.release_id == release_id
    ]
    if not selected:
        raise RolloutError("release is absent from the signed channel")
    if any(c.channel != verified.channel for c in candidates):
        raise RolloutError("candidate channel does not match signed channel")
    if (
        len(
            {
                (c.version, c.build_id, c.rollout_percentage, c.rollout_status)
                for c in selected
            }
        )
        != 1
    ):
        raise RolloutError("release artifacts disagree on identity or rollout")
    if len({(c.platform, c.architecture, c.install_type) for c in selected}) != len(
        selected
    ):
        raise RolloutError("release contains duplicate platform artifacts")
    if selected[0].rollout_status in {"yanked", "quarantined"} and status not in {
        "yanked",
        "quarantined",
    }:
        raise RolloutError("a higher fixed release must supersede this candidate")
    previous = selected[0].rollout_percentage
    if percentage > previous:
        if selected[0].rollout_status in {"yanked", "quarantined"}:
            raise RolloutError("a higher fixed release must supersede this candidate")
        if previous not in STAGES or percentage != STAGES[STAGES.index(previous) + 1]:
            raise RolloutError("rollout expansion must advance one stage at a time")
    if percentage > previous or (evidence and status == "active"):
        if len(evidence) != len(selected):
            raise RolloutError("fresh health evidence is required for each artifact")
        reasons = set()
        for candidate in selected:
            matches = [
                item
                for item in evidence
                if all(item.get(name) == getattr(candidate, name) for name in IDENTITY)
            ]
            if len(matches) != 1:
                raise RolloutError(
                    "health evidence is missing or duplicated for an artifact"
                )
            reasons.update(
                health_reasons(matches[0], candidate, now=now, policy=policy)
            )
        if reasons:
            status, percentage = "paused", 0
            reason = "health_gate:" + ",".join(sorted(reasons))
    for raw in signed["releases"]:
        if raw["release_id"] == release_id:
            raw.update(
                rollout_percentage=percentage,
                rollout_status=status,
                rollout_reason=reason[:256],
            )
            UpdateCandidate.from_dict(raw)
    signed["metadata_version"] = verified.metadata_version + 1
    signed["generated_at"] = now.isoformat()
    # Never extend the publisher's original expiry while revising rollout state.
    signed["rollout_action"] = {
        "release_id": release_id,
        "from_percentage": previous,
        "to_percentage": percentage,
        "status": status,
        "reason": reason[:256],
        "at": now.isoformat(),
    }
    result = (
        json.dumps(
            {
                "signed": signed,
                "signatures": [
                    {
                        "key_id": key_id,
                        "signature": base64.b64encode(
                            key.sign(_canonical(signed))
                        ).decode(),
                    }
                    for key_id, key in sorted(keys.items())
                ],
            },
            sort_keys=True,
            indent=2,
        ).encode()
        + b"\n"
    )
    verify_manifest(
        result,
        trust=trust,
        installed=installed,
        cohort=0,
        prior_metadata_version=verified.metadata_version + 1,
        now=now,
        metadata_only=True,
    )
    return result
