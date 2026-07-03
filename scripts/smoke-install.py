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


def project_version(root: Path) -> str:
    for line in (root / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("No project version found in pyproject.toml.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and smoke-test an isolated OPai wheel install."
    )
    parser.add_argument(
        "--keep-venv", action="store_true", help="Keep the temporary smoke-test venv"
    )
    parser.add_argument(
        "--work-dir",
        help="Optional external smoke-test work directory. Defaults to a temp folder.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    version = project_version(root)
    work_dir = Path(args.work_dir).expanduser().resolve() if args.work_dir else None
    created_work_dir = False
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="opai-smoke-")).resolve()
        created_work_dir = True
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        wheelhouse = work_dir / "wheelhouse"
        wheelhouse.mkdir(parents=True, exist_ok=True)

        run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                ".",
                "--no-deps",
                "-w",
                str(wheelhouse),
            ],
            root,
        )
        wheels = sorted(
            wheelhouse.glob(f"opai-{version}-*.whl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not wheels:
            raise SystemExit(f"No OPai {version} wheel was built.")

        venv_root = work_dir / "smoke-install-venv"
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
                f"opai=={version}",
            ],
            root,
        )

        # The web GUI ships as package data; a wheel without it silently falls
        # back to the legacy Qt window (issue #139). Fail the smoke instead.
        gui_assets_check = (
            "from opai.gui_web import WEB_DIR\n"
            "import sys\n"
            "required = ['index.html', 'app.js', 'styles.css', 'activity.js',"
            " 'message-state.js']\n"
            "missing = [n for n in required if not (WEB_DIR / n).is_file()]\n"
            "if missing:\n"
            "    sys.exit('wheel is missing web GUI assets: ' + ', '.join(missing))\n"
            "print('web GUI assets present:', len(required))\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            outside_repo = Path(tmp)
            run([str(python), "-m", "opai", "version"], outside_repo)
            run([str(python), "-c", gui_assets_check], outside_repo)
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
    finally:
        if created_work_dir and not args.keep_venv:
            shutil.rmtree(work_dir, ignore_errors=True)
    print("OPai isolated install smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
