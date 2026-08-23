"""Generate the tracked runtime release projection from ``pyproject.toml``."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Sequence


def _identity_module(root: Path) -> ModuleType:
    path = root / "opai" / "release_identity.py"
    spec = importlib.util.spec_from_file_location(
        "_opai_release_identity_generator", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load release identity generator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def generate(root: Path) -> tuple[Path, str]:
    repository = Path(root).expanduser().resolve()
    identity = _identity_module(repository)
    release = identity.read_project_release(repository / "pyproject.toml")
    return (
        repository / "opai" / "_generated_release.py",
        identity.render_generated_release(release),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    target, expected = generate(args.root)
    actual = target.read_text(encoding="utf-8") if target.is_file() else ""
    if args.check:
        if actual == expected:
            print(f"release identity projection is current: {target}")
            return 0
        print(
            f"release identity projection drift: {target}; "
            "run python scripts/generate_release_identity.py",
            file=sys.stderr,
        )
        return 1
    target.write_text(expected, encoding="utf-8")
    print(f"generated release identity projection: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
