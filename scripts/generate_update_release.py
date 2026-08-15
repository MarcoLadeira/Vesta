"""Generate native feeds and a signed compact manifest from final artifacts."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from opai.update.models import InstallType  # noqa: E402
from opai.update.release import (  # noqa: E402
    ReleaseArtifact,
    ReleaseError,
    generate_release_files,
)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ReleaseError("timestamp arguments require an explicit timezone")
    return parsed


def _object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReleaseError("release descriptor must be a JSON object")
    return value


def _private_keys(
    paths: list[Path],
) -> tuple[dict[str, Ed25519PrivateKey], dict[str, str]]:
    private: dict[str, Ed25519PrivateKey] = {}
    public: dict[str, str] = {}
    for path in paths:
        value = _object(path)
        key_id = str(value.get("key_id") or "")
        raw = base64.b64decode(str(value.get("private_key") or ""), validate=True)
        if not key_id or key_id in private or len(raw) != 32:
            raise ReleaseError("metadata key file is invalid or duplicated")
        key = Ed25519PrivateKey.from_private_bytes(raw)
        private[key_id] = key
        public[key_id] = base64.b64encode(
            key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode()
    return private, public


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--metadata-key", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        descriptor = _object(args.descriptor)
        raw_artifacts = descriptor.pop("artifacts", None)
        if not isinstance(raw_artifacts, list):
            raise ReleaseError("release descriptor artifacts must be a list")
        artifacts = [
            ReleaseArtifact(
                path=Path(str(item["path"])),
                url=str(item["url"]),
                platform=str(item["platform"]),
                architecture=str(item["architecture"]),
                install_type=InstallType(str(item["install_type"])),
                publisher_identity=str(item["publisher_identity"]),
                package_identity=str(item["package_identity"]),
                native=dict(item.get("native") or {}),
            )
            for item in raw_artifacts
            if isinstance(item, dict)
        ]
        if len(artifacts) != len(raw_artifacts):
            raise ReleaseError("release descriptor contains a malformed artifact")
        keys, public = _private_keys(args.metadata_key)
        declared_public = descriptor.get("metadata_public_keys")
        if declared_public is not None:
            if not isinstance(declared_public, dict):
                raise ReleaseError("metadata public keys must be an object")
            public = {str(key): str(value) for key, value in declared_public.items()}
        result = generate_release_files(
            output=args.output,
            artifacts=artifacts,
            version=str(descriptor["version"]),
            build_id=str(descriptor["build_id"]),
            channel=str(descriptor["channel"]),
            metadata_version=int(descriptor["metadata_version"]),
            generated_at=_timestamp(str(descriptor["generated_at"])),
            expires_at=_timestamp(str(descriptor["expires_at"])),
            feed_base_url=str(descriptor["feed_base_url"]),
            release_id=str(descriptor["release_id"]),
            release_title=str(descriptor["release_title"]),
            release_notes=str(descriptor.get("release_notes") or ""),
            release_notes_url=str(descriptor["release_notes_url"]),
            metadata_keys=keys,
            metadata_public_keys=public,
            signature_threshold=int(descriptor.get("signature_threshold") or 1),
            sparkle_public_key=str(descriptor["sparkle_public_key"]),
            rollout_percentage=int(descriptor.get("rollout_percentage", 100)),
            cohort_start=int(descriptor.get("cohort_start", 0)),
            minimum_current_version=str(
                descriptor.get("minimum_current_version") or ""
            ),
            maximum_current_version=str(
                descriptor.get("maximum_current_version") or ""
            ),
            criticality=str(descriptor.get("criticality") or "normal"),
            required_after=str(descriptor.get("required_after") or ""),
            revoked_key_ids=tuple(descriptor.get("revoked_key_ids") or ()),
            native_feed_base_url=str(descriptor.get("native_feed_base_url") or ""),
            native_feed_suffix=str(descriptor.get("native_feed_suffix") or ""),
            feed_redirect_origins=tuple(descriptor.get("feed_redirect_origins") or ()),
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    **{name: str(path) for name, path in result.__dict__.items()},
                },
                sort_keys=True,
            )
        )
        return 0
    except (KeyError, OSError, ValueError, json.JSONDecodeError, ReleaseError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
