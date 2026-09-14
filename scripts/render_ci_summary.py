"""Render a bounded CI evidence manifest into a GitHub step summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    value = json.loads(args.manifest.read_text(encoding="utf-8"))
    profile = value.get("profile") or {}
    print(
        f"### Vesta {profile.get('name', 'unknown')} / {profile.get('component', 'all')} qualification"
    )
    print(f"- Verdict: `{value.get('verdict', 'infrastructure_blocked')}`")
    print(f"- Classification: `{value.get('classification', 'infrastructure')}`")
    print(f"- Reason: `{value.get('reason', 'unknown')}`")
    print(f"- Candidate: `{value.get('candidate_sha') or 'unknown'}`")
    print(f"- Checkout: `{value.get('commit_sha') or 'unknown'}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
