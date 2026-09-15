"""Prepare a signed rollout revision for review and existing feed publication."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opai.update.models import InstalledBuild  # noqa: E402
from opai.update.rollout import HealthPolicy, revise_rollout  # noqa: E402
from scripts.generate_update_release import _object, _private_keys  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--trust", required=True, type=Path)
    parser.add_argument("--installed-identity", required=True, type=Path)
    parser.add_argument("--metadata-key", required=True, action="append", type=Path)
    parser.add_argument("--prior-metadata-version", required=True, type=int)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--percentage", required=True, type=int)
    parser.add_argument(
        "--status",
        choices=("active", "paused", "yanked", "quarantined"),
        default="active",
    )
    parser.add_argument("--reason", required=True)
    parser.add_argument("--health", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        keys, _ = _private_keys(args.metadata_key)
        evidence = (
            json.loads(args.health.read_text(encoding="utf-8")) if args.health else []
        )
        if not isinstance(evidence, list) or not all(
            isinstance(x, dict) for x in evidence
        ):
            raise ValueError("health must contain a list of aggregate records")
        payload = revise_rollout(
            args.manifest.read_bytes(),
            trust=_object(args.trust),
            installed=InstalledBuild(**_object(args.installed_identity)),
            keys=keys,
            release_id=args.release_id,
            percentage=args.percentage,
            status=args.status,
            reason=args.reason,
            evidence=evidence,
            policy=HealthPolicy(**_object(args.policy)) if args.policy else None,
            prior_metadata_version=args.prior_metadata_version,
            now=datetime.now(timezone.utc),
        )
        # Exclusive creation avoids replacing a reviewed or live manifest.
        with args.output.open("xb") as target:
            target.write(payload)
        action = json.loads(payload)["signed"]["rollout_action"]
        print(json.dumps({"ok": True, "published": False, "action": action}))
        return 0
    except (OSError, ValueError, TypeError, KeyError):
        # Key file paths and parser/transport diagnostics are not public evidence.
        print(
            json.dumps({"ok": False, "error": "rollout_revision_rejected"}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
