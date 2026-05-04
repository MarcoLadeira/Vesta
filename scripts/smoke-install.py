from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(argv: list[str], cwd: Path) -> None:
    print("+", " ".join(argv))
    subprocess.run(argv, cwd=str(cwd), check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and smoke-test an isolated OPai wheel install."
    )
    parser.add_argument(
        "--keep-venv", action="store_true", help="Keep the temporary smoke-test venv"
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    wheelhouse = root / ".opaihub" / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)

    run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(wheelhouse)],
        root,
    )
    wheels = sorted(
        wheelhouse.glob("opai-0.1.1-*.whl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not wheels:
        raise SystemExit("No OPai wheel was built.")

    venv_root = root / ".opaihub" / "smoke-install-venv"
    if venv_root.exists():
        shutil.rmtree(venv_root)
    run([sys.executable, "-m", "venv", str(venv_root)], root)

    python = venv_root / (
        "Scripts/python.exe" if sys.platform.startswith("win") else "bin/python"
    )
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "opai==0.1.1",
        ],
        root,
    )

    with tempfile.TemporaryDirectory() as tmp:
        outside_repo = Path(tmp)
        run([str(python), "-m", "opai", "version"], outside_repo)
        run([str(python), "-m", "opaihub", "validate"], outside_repo)
        run([str(python), "-m", "opai", "hub", "list-tools"], outside_repo)
        run(
            [
                str(python),
                "-m",
                "opai",
                "welcome",
                "--compact",
                "--no-color",
                "--image",
                "ascii",
            ],
            outside_repo,
        )

    if not args.keep_venv:
        shutil.rmtree(venv_root)
    print("OPai isolated install smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
