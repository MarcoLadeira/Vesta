from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path


EXTERNAL_STATE_ENV = {
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "OPAI_HUB_ROOT",
    "LOCAL_MODEL_URL",
    "LOCAL_MODEL_NAME",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
}


def run(argv: list[str], cwd: Path, *, env: Mapping[str, str] | None = None) -> None:
    print("+", " ".join(argv))
    subprocess.run(argv, cwd=str(cwd), check=True, env=env, timeout=120)


def wheel_build_command(python: Path, root: Path, wheelhouse: Path) -> list[str]:
    """Build OPai and every declared runtime dependency into one wheelhouse."""

    return [
        str(python),
        "-m",
        "pip",
        "wheel",
        str(root),
        "-w",
        str(wheelhouse),
    ]


def required_smoke_commands(python: Path) -> list[list[str]]:
    executable = str(python)
    return [
        [executable, "-m", "opai", "--help"],
        [executable, "-m", "opai", "doctor"],
        [executable, "-m", "opaihub", "validate"],
        [executable, "-m", "opai", "gui", "--once"],
    ]


def isolated_environment(
    home: Path,
    *,
    base: Mapping[str, str] | None = None,
    executable_dir: Path | None = None,
) -> dict[str, str]:
    """Return a clean-user child environment without external OPai state."""

    child = dict(os.environ if base is None else base)
    for name in EXTERNAL_STATE_ENV:
        child.pop(name, None)
    locations = {
        "HOME": home,
        "USERPROFILE": home,
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_DATA_HOME": home / ".local" / "share",
        "APPDATA": home / "AppData" / "Roaming",
        "LOCALAPPDATA": home / "AppData" / "Local",
    }
    child.update({name: str(path) for name, path in locations.items()})
    if executable_dir is not None:
        path_entries = [str(executable_dir)]
        if os.name == "nt":
            system_root = child.get("SystemRoot") or child.get("WINDIR")
            if system_root:
                path_entries.extend([str(Path(system_root) / "System32"), system_root])
        else:
            path_entries.extend(["/usr/local/bin", "/usr/bin", "/bin"])
        child["PATH"] = os.pathsep.join(path_entries)
    return child


def prepare_smoke_project(home: Path) -> Path:
    """Create a minimal project whose parent chain cannot reach the real home."""

    project = home / "outside-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'opai-smoke-project'\nversion = '0'\n",
        encoding="utf-8",
    )
    return project


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

        run(wheel_build_command(Path(sys.executable), root, wheelhouse), root)
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

        smoke_home = work_dir / "smoke-home"
        smoke_home.mkdir(parents=True, exist_ok=True)
        smoke_env = isolated_environment(smoke_home, executable_dir=python.parent)

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

        outside_repo = prepare_smoke_project(smoke_home)
        for command in required_smoke_commands(python):
            run(command, outside_repo, env=smoke_env)
        run(
            [str(python), "-m", "opai", "version"],
            outside_repo,
            env=smoke_env,
        )
        run([str(python), "-c", gui_assets_check], outside_repo, env=smoke_env)
        run(
            [str(python), "-m", "opai", "hub", "list-tools"],
            outside_repo,
            env=smoke_env,
        )
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
            env=smoke_env,
        )
    finally:
        if created_work_dir and not args.keep_venv:
            shutil.rmtree(work_dir, ignore_errors=True)
    print("OPai isolated install smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
