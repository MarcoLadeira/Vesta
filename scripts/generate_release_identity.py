"""Generate the tracked runtime release projection from ``pyproject.toml``."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Sequence


CURRENT_DOCUMENTATION = (
    Path("README.md"),
    Path("docs/INSTALL_PROOF.md"),
    Path("site/index.html"),
)


def _identity_module(root: Path) -> ModuleType:
    path = root / "vesta" / "release_identity.py"
    spec = importlib.util.spec_from_file_location(
        "_vesta_release_identity_generator", path
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
        repository / "vesta" / "_generated_release.py",
        identity.render_generated_release(release),
    )


def documentation_projections(root: Path) -> tuple[tuple[Path, str], ...]:
    repository = Path(root).expanduser().resolve()
    identity = _identity_module(repository)
    release = identity.read_project_release(repository / "pyproject.toml")
    projections: list[tuple[Path, str]] = []
    for relative in CURRENT_DOCUMENTATION:
        path = repository / relative
        lines = path.read_text(encoding="utf-8").splitlines()
        indexes = [
            index
            for index, line in enumerate(lines)
            if line.strip().startswith("<!-- vesta-release-identity:")
        ]
        if len(indexes) != 1:
            raise RuntimeError(
                f"current documentation must contain one release marker: {path}"
            )
        try:
            end = lines.index("<!-- /vesta-release-identity -->", indexes[0] + 1)
        except ValueError as exc:
            raise RuntimeError(
                f"current documentation release block is unterminated: {path}"
            ) from exc
        block = identity.render_documentation_projection(
            release, surface=relative.as_posix()
        ).splitlines()
        lines[indexes[0] : end + 1] = block
        projections.append((path, "\n".join(lines) + "\n"))
    return tuple(projections)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    target, expected = generate(args.root)
    projections = ((target, expected), *documentation_projections(args.root))
    if args.check:
        drifted = [
            path
            for path, content in projections
            if not path.is_file() or path.read_text(encoding="utf-8") != content
        ]
        if not drifted:
            print(
                "release identity projections are current: "
                + ", ".join(str(path) for path, _ in projections)
            )
            return 0
        print(
            "release identity projection drift: "
            + ", ".join(str(path) for path in drifted)
            + "; "
            "run python scripts/generate_release_identity.py",
            file=sys.stderr,
        )
        return 1
    for path, content in projections:
        path.write_text(content, encoding="utf-8")
    print(
        "generated release identity projections: "
        + ", ".join(str(path) for path, _ in projections)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
