"""Fail when OPai application/release identity projections drift."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if sys.path[0] != str(ROOT):
    sys.path.insert(0, str(ROOT))

from opai.release_validation import validate_release_identity  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    repository = args.root.expanduser().resolve()
    drift = validate_release_identity(repository)
    if drift:
        for item in drift:
            print(item.message(), file=sys.stderr)
        return 1
    print(f"release identity validation passed: {repository}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
