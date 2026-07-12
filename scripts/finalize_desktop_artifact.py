"""Write post-signing evidence only after a platform verifier has succeeded."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opaihub.desktop_artifacts import (  # noqa: E402
    EVIDENCE_SCHEMA_VERSION,
    PROVENANCE_NAME,
    ArtifactReleaseError,
    ReleaseRef,
    write_bundle_evidence,
)


def _release_ref_from_bundle(
    bundle: Path,
) -> tuple[ReleaseRef, str, dict[str, Any] | None]:
    try:
        provenance = json.loads((bundle / PROVENANCE_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactReleaseError(
            "bundle has no valid pre-signing provenance"
        ) from exc
    if provenance.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise ArtifactReleaseError("bundle provenance schema is not supported")
    tag = provenance.get("tag")
    commit = provenance.get("commit")
    platform = provenance.get("platform")
    build_metadata = provenance.get("build")
    if (
        not isinstance(tag, str)
        or not isinstance(commit, str)
        or not isinstance(platform, str)
    ):
        raise ArtifactReleaseError("bundle provenance is incomplete")
    if provenance.get("rehearsal"):
        raise ArtifactReleaseError(
            "an untagged rehearsal artifact cannot become signed release output"
        )
    if build_metadata is not None and not isinstance(build_metadata, dict):
        raise ArtifactReleaseError("bundle build metadata is invalid")
    return ReleaseRef(tag=tag, commit=commit), platform, build_metadata


def _log_sha256(path: Path, bundle: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(bundle)
    except ValueError:
        pass
    else:
        raise ArtifactReleaseError(
            "signing verification log must remain outside the bundle"
        )
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise ArtifactReleaseError("signing verification log is unreadable") from exc
    if not data:
        raise ArtifactReleaseError("signing verification log is empty")
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Finalize artifact checksums after platform signing verification."
    )
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--verification-log", required=True, type=Path)
    parser.add_argument("--tool", required=True)
    parser.add_argument(
        "--status", choices=("signed", "signed-and-notarized"), required=True
    )
    args = parser.parse_args()
    try:
        bundle = args.bundle.expanduser().resolve()
        release, platform, build_metadata = _release_ref_from_bundle(bundle)
        log_hash = _log_sha256(args.verification_log, bundle)
        paths = write_bundle_evidence(
            bundle,
            release,
            platform=platform,
            signing_status=args.status,
            signing_evidence={
                "verified": True,
                "tool": str(args.tool).strip(),
                "log_sha256": log_hash,
            },
            build_metadata=build_metadata,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "bundle": str(bundle),
                    "status": args.status,
                    "evidence": {name: str(path) for name, path in paths.items()},
                },
                indent=2,
            )
        )
        return 0
    except (ArtifactReleaseError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
