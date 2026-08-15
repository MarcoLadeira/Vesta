"""Verification of OPai's small signed cross-platform release manifest."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

from .models import InstalledBuild, UpdateCandidate


class ManifestError(ValueError):
    """Typed, safe manifest rejection suitable for updater state."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VerifiedManifest:
    metadata_version: int
    expires_at: str
    channel: str
    candidate: UpdateCandidate | None
    signing_key_ids: tuple[str, ...]


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _timestamp(value: Any, *, code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError(code) from exc
    if parsed.tzinfo is None:
        raise ManifestError(code)
    return parsed.astimezone(timezone.utc)


def _trusted_signers(
    signed: Mapping[str, Any], signatures: Any, trust: Mapping[str, Any]
) -> tuple[str, ...]:
    if trust.get("schema_version") != 1 or not isinstance(trust.get("keys"), list):
        raise ManifestError("trust_store_invalid")
    threshold = trust.get("threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        raise ManifestError("trust_store_invalid")
    keys = {
        str(item.get("key_id") or ""): item
        for item in trust["keys"]
        if isinstance(item, Mapping) and item.get("key_id")
    }
    if not isinstance(signatures, list):
        raise ManifestError("metadata_signature_invalid")
    valid: list[str] = []
    rejection = "metadata_signature_invalid"
    for signature in signatures:
        if not isinstance(signature, Mapping):
            continue
        key_id = str(signature.get("key_id") or "")
        key = keys.get(key_id)
        if key is None:
            rejection = "metadata_key_unknown"
            continue
        if key.get("revoked") is True:
            rejection = "metadata_key_revoked"
            continue
        try:
            public_bytes = base64.b64decode(
                str(key.get("public_key") or ""), validate=True
            )
            signature_bytes = base64.b64decode(
                str(signature.get("signature") or ""), validate=True
            )
            Ed25519PublicKey.from_public_bytes(public_bytes).verify(
                signature_bytes, _canonical(signed)
            )
        except (ValueError, binascii.Error, InvalidSignature):
            rejection = "metadata_signature_invalid"
            continue
        if key_id not in valid:
            valid.append(key_id)
    if len(valid) < threshold:
        raise ManifestError(rejection)
    return tuple(sorted(valid))


def _rollout_includes(candidate: UpdateCandidate, cohort: int) -> bool:
    if candidate.rollout_percentage == 100:
        return True
    if candidate.rollout_percentage == 0:
        return False
    distance = (cohort - candidate.cohort_start) % 100
    return distance < candidate.rollout_percentage


def _validate_candidate(
    candidate: UpdateCandidate,
    *,
    manifest_channel: str,
    installed: InstalledBuild,
    cohort: int,
    signer_ids: tuple[str, ...],
) -> bool:
    if candidate.channel != manifest_channel or candidate.channel != installed.channel:
        raise ManifestError("channel_mismatch")
    if candidate.platform.casefold() != installed.platform.casefold():
        raise ManifestError("platform_mismatch")
    if candidate.architecture.casefold() != installed.architecture.casefold():
        raise ManifestError("architecture_mismatch")
    if candidate.install_type is not installed.install_type:
        raise ManifestError("install_type_mismatch")
    if candidate.publisher_identity != installed.publisher_identity:
        raise ManifestError("publisher_mismatch")
    if not set(candidate.metadata_key_ids).issubset(signer_ids):
        raise ManifestError("mix_and_match_detected")
    parsed_url = urlparse(candidate.artifact_url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise ManifestError("insecure_artifact_url")
    redirect_origins = candidate.native.get("allowed_redirect_origins") or []
    if not isinstance(redirect_origins, list) or len(redirect_origins) > 4:
        raise ManifestError("artifact_redirect_policy_invalid")
    for value in redirect_origins:
        parsed = urlparse(str(value))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ManifestError("artifact_redirect_policy_invalid")
    try:
        current = Version(installed.version)
        target = Version(candidate.version)
        minimum = (
            Version(candidate.minimum_current_version)
            if candidate.minimum_current_version
            else None
        )
        maximum = (
            Version(candidate.maximum_current_version)
            if candidate.maximum_current_version
            else None
        )
    except InvalidVersion as exc:
        raise ManifestError("version_invalid") from exc
    if target == current and candidate.build_id == installed.build_id:
        return False
    if target <= current:
        raise ManifestError("target_rollback")
    if minimum is not None and current < minimum:
        raise ManifestError("current_version_unsupported")
    if maximum is not None and current > maximum:
        raise ManifestError("current_version_unsupported")
    if candidate.minimum_updater_protocol > installed.updater_protocol_version:
        raise ManifestError("updater_protocol_unsupported")
    if not _rollout_includes(candidate, cohort):
        raise ManifestError("rollout_excluded")
    return True


def verify_manifest(
    payload: bytes,
    *,
    trust: Mapping[str, Any],
    installed: InstalledBuild,
    cohort: int,
    prior_metadata_version: int,
    now: datetime | None = None,
    metadata_only: bool = False,
) -> VerifiedManifest:
    """Authenticate and select the one candidate for this exact installation."""
    try:
        envelope = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("metadata_malformed") from exc
    if not isinstance(envelope, Mapping) or not isinstance(
        envelope.get("signed"), Mapping
    ):
        raise ManifestError("metadata_malformed")
    signed = envelope["signed"]
    signer_ids = _trusted_signers(signed, envelope.get("signatures"), trust)
    if signed.get("schema_version") != 1:
        raise ManifestError("metadata_schema_unsupported")
    metadata_version = signed.get("metadata_version")
    if (
        isinstance(metadata_version, bool)
        or not isinstance(metadata_version, int)
        or metadata_version < 1
    ):
        raise ManifestError("metadata_version_invalid")
    if metadata_version < int(prior_metadata_version):
        raise ManifestError("metadata_rollback")
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires = _timestamp(signed.get("expires_at"), code="metadata_expiry_invalid")
    generated = _timestamp(
        signed.get("generated_at"), code="metadata_generated_invalid"
    )
    if expires <= stamp:
        raise ManifestError("metadata_expired")
    if generated > stamp + timedelta(minutes=10):
        raise ManifestError("metadata_from_future")
    channel = str(signed.get("channel") or "")
    if channel != installed.channel:
        raise ManifestError("channel_mismatch")
    releases = signed.get("releases")
    if not isinstance(releases, list):
        raise ManifestError("metadata_has_no_releases")
    if metadata_only:
        return VerifiedManifest(
            metadata_version=metadata_version,
            expires_at=expires.isoformat(),
            channel=channel,
            candidate=None,
            signing_key_ids=signer_ids,
        )
    first_error: ManifestError | None = None
    current_seen = False
    for raw_candidate in releases:
        if not isinstance(raw_candidate, Mapping):
            first_error = first_error or ManifestError("candidate_malformed")
            continue
        try:
            candidate = UpdateCandidate.from_dict(raw_candidate)
            eligible = _validate_candidate(
                candidate,
                manifest_channel=channel,
                installed=installed,
                cohort=cohort,
                signer_ids=signer_ids,
            )
        except (TypeError, ValueError) as exc:
            error = (
                exc
                if isinstance(exc, ManifestError)
                else ManifestError("candidate_malformed")
            )
            first_error = first_error or error
            continue
        if not eligible:
            current_seen = True
            continue
        return VerifiedManifest(
            metadata_version=metadata_version,
            expires_at=expires.isoformat(),
            channel=channel,
            candidate=candidate,
            signing_key_ids=signer_ids,
        )
    if not releases or current_seen:
        return VerifiedManifest(
            metadata_version=metadata_version,
            expires_at=expires.isoformat(),
            channel=channel,
            candidate=None,
            signing_key_ids=signer_ids,
        )
    raise first_error or ManifestError("no_eligible_candidate")
