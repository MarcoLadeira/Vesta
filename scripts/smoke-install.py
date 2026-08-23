from __future__ import annotations

import argparse
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
import re


EXTERNAL_STATE_ENV = {
    "MOONSHOT_API_KEY",
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

_EXPECTED_PROVIDER_CATALOG_IDS = (
    "claude",
    "codex",
    "copilot",
    "kimi",
    "gemini",
    "groq",
    "mistral",
    "deepseek",
    "ollama",
    "openai-compatible",
)
_EXACT_BUILD_ID = re.compile(r"^[0-9a-f]{40}$")


def resolve_candidate_build_id(
    candidate_sha: str | None, environment: Mapping[str, str]
) -> str:
    value = str(candidate_sha or environment.get("OPAI_BUILD_ID") or "").lower()
    if _EXACT_BUILD_ID.fullmatch(value) is None:
        raise ValueError(
            "candidate SHA must name the exact lowercase commit for wheel smoke"
        )
    return value


def run(argv: list[str], cwd: Path, *, env: Mapping[str, str] | None = None) -> None:
    print("+", " ".join(argv))
    subprocess.run(  # nosec B603
        argv, cwd=str(cwd), check=True, env=env, timeout=120
    )


def wheel_build_command(python: Path, root: Path, wheelhouse: Path) -> list[str]:
    """Build OPai and every declared runtime dependency into one wheelhouse."""

    return [
        str(python),
        "-m",
        "pip",
        "wheel",
        "--no-build-isolation",
        "--constraint",
        str(root / "requirements-ci.txt"),
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


def provider_catalog_smoke_command(python: Path) -> list[str]:
    """Load the packaged provider catalog from the isolated wheel install."""

    check = (
        "from opaihub import provider_catalog\n"
        f"expected = {_EXPECTED_PROVIDER_CATALOG_IDS!r}\n"
        "if not provider_catalog.catalog_bytes():\n"
        "    raise SystemExit('wheel provider catalog is empty')\n"
        "records = provider_catalog.all_catalog_records()\n"
        "if tuple(record['provider_id'] for record in records) != expected:\n"
        "    raise SystemExit('wheel provider catalog inventory is invalid')\n"
        "print('provider catalog records:', len(records))\n"
    )
    return [str(python), "-I", "-c", check]


def installed_identity_smoke_command(python: Path, expected_build_id: str) -> list[str]:
    """Assert the installed CLI reports the exact candidate embedded at build."""

    if _EXACT_BUILD_ID.fullmatch(expected_build_id) is None:
        raise ValueError(
            "expected build identity must be an exact lowercase commit SHA"
        )
    check = (
        "import json, subprocess, sys\n"
        "result = subprocess.run([sys.executable, '-m', 'opai', 'version', '--json'], "
        "check=False, capture_output=True, text=True, timeout=30)\n"
        "if result.returncode != 0:\n"
        "    raise SystemExit(result.stdout + result.stderr)\n"
        "payload = json.loads(result.stdout)\n"
        "identity = payload.get('release_identity', {})\n"
        f"expected = {expected_build_id!r}\n"
        "if identity.get('build_id') != expected:\n"
        "    raise SystemExit(f'installed build_id {identity.get(\"build_id\")!r} ' "
        "+ f'does not match qualified candidate {expected}')\n"
        "print('installed build identity:', expected)\n"
    )
    return [str(python), "-I", "-c", check]


def run_post_install_smoke_checks(
    python: Path,
    cwd: Path,
    environment: Mapping[str, str],
    *,
    expected_build_id: str,
) -> None:
    """Run the checks that must prove the just-installed wheel is usable."""

    run(provider_catalog_smoke_command(python), cwd, env=environment)
    run(
        installed_identity_smoke_command(python, expected_build_id),
        cwd,
        env=environment,
    )
    for command in required_smoke_commands(python):
        run(command, cwd, env=environment)


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
    parser.add_argument(
        "--candidate-sha",
        help="Exact candidate commit to embed and assert (or set OPAI_BUILD_ID).",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    version = project_version(root)
    try:
        expected_build_id = resolve_candidate_build_id(args.candidate_sha, os.environ)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    work_dir = Path(args.work_dir).expanduser().resolve() if args.work_dir else None
    created_work_dir = False
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="opai-smoke-")).resolve()
        created_work_dir = True
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        wheelhouse = work_dir / "wheelhouse"
        wheelhouse.mkdir(parents=True, exist_ok=True)

        build_environment = dict(os.environ)
        build_environment["OPAI_BUILD_ID"] = expected_build_id
        run(
            wheel_build_command(Path(sys.executable), root, wheelhouse),
            root,
            env=build_environment,
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
        smoke_home = work_dir / "smoke-home"
        smoke_home.mkdir(parents=True, exist_ok=True)
        smoke_env = isolated_environment(smoke_home, executable_dir=python.parent)
        outside_repo = prepare_smoke_project(smoke_home)
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
        run_post_install_smoke_checks(
            python,
            outside_repo,
            smoke_env,
            expected_build_id=expected_build_id,
        )

        # The web GUI ships as package data; a wheel without it silently falls
        # back to the legacy Qt window (issue #139). Fail the smoke instead.
        gui_assets_check = (
            "from opai.gui_web import WEB_DIR\n"
            "import sys\n"
            "required = ['index.html', 'app.js', 'styles.css', 'activity.js',"
            " 'message-state.js', 'settings.js', 'onboarding.js']\n"
            "missing = [n for n in required if not (WEB_DIR / n).is_file()]\n"
            "if missing:\n"
            "    sys.exit('wheel is missing web GUI assets: ' + ', '.join(missing))\n"
            "print('web GUI assets present:', len(required))\n"
        )

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
