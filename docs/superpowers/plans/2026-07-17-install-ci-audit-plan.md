# Production Installers, CI Smoke Tests, and Paid-Era Language Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Solve GitHub issues #353, #354, #355, and #358 by productionizing the OPai installers, adding a cross-platform install smoke-test CI job, and cleaning up paid-era language remnants.

**Architecture:** Create a testable Python installer module (`scripts/install_opai.py`) that encapsulates all install logic. Rewrite `install.ps1` and `install.sh` as thin wrappers that delegate to it. Add a GitHub Actions `install-smoke` job that exercises the installers on Windows, macOS, and Linux. Fix `CHANGELOG.md` and add a regression check for paid-era terms.

**Tech Stack:** Python 3.10+, PowerShell, Bash, GitHub Actions, unittest

---

## File Structure

- **Create:** `scripts/install_opai.py` — testable installer logic
- **Create:** `tests/test_install_opai.py` — unit tests for installer logic
- **Modify:** `install.ps1` — thin PowerShell wrapper
- **Modify:** `install.sh` — thin Bash wrapper
- **Modify:** `.github/workflows/ci.yml` — add `install-smoke` job
- **Modify:** `CHANGELOG.md` — remove/annotate paid-era language
- **Create:** `scripts/check_no_paid_terms.py` — regression check for banned terms
- **Create:** `tests/test_no_paid_terms.py` — tests for regression check

---

### Task 1: Create Python Installer Module and Tests

**Files:**
- Create: `scripts/install_opai.py`
- Test: `tests/test_install_opai.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_install_opai.py
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeCompleted

import scripts.install_opai as install_opai


class PythonVersionTests(unittest.TestCase):
    def test_check_python_version_returns_true_for_310_plus(self):
        with mock.patch.object(sys, "version_info", (3, 10, 0)):
            self.assertTrue(install_opai.check_python_version())

    def test_check_python_version_returns_false_for_39(self):
        with mock.patch.object(sys, "version_info", (3, 9, 0)):
            self.assertFalse(install_opai.check_python_version())


class InstallerBackendTests(unittest.TestCase):
    def test_install_with_pipx_returns_false_when_pipx_missing(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(install_opai.install_with_pipx())

    def test_install_with_pipx_runs_pipx_install(self):
        with mock.patch("shutil.which", return_value="/usr/bin/pipx"):
            with mock.patch("subprocess.run", return_value=FakeCompleted()) as run:
                self.assertTrue(install_opai.install_with_pipx())
                run.assert_called_once()
                self.assertIn("pipx", run.call_args[0][0])
                self.assertIn("install", run.call_args[0][0])
                self.assertIn("opai", run.call_args[0][0])

    def test_install_with_pipx_returns_false_on_failure(self):
        with mock.patch("shutil.which", return_value="/usr/bin/pipx"):
            with mock.patch("subprocess.run", return_value=FakeCompleted(returncode=1)):
                self.assertFalse(install_opai.install_with_pipx())

    def test_install_with_uv_returns_false_when_uv_missing(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(install_opai.install_with_uv())

    def test_install_with_uv_runs_uv_tool_install(self):
        with mock.patch("shutil.which", return_value="/usr/bin/uv"):
            with mock.patch("subprocess.run", return_value=FakeCompleted()) as run:
                self.assertTrue(install_opai.install_with_uv())
                run.assert_called_once()
                self.assertIn("uv", run.call_args[0][0])
                self.assertIn("tool", run.call_args[0][0])
                self.assertIn("install", run.call_args[0][0])


class SourceInstallTests(unittest.TestCase):
    def test_install_from_source_clones_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "source"
            with mock.patch("subprocess.run", return_value=FakeCompleted()) as run:
                self.assertTrue(install_opai.install_from_source(
                    "https://github.com/MarcoLadeira/OPai.git",
                    "main",
                    install_root,
                ))
                clone_call = run.call_args_list[0][0][0]
                self.assertIn("git", clone_call)
                self.assertIn("clone", clone_call)

    def test_install_from_source_pulls_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "source"
            (install_root / ".git").mkdir(parents=True)
            with mock.patch("subprocess.run", return_value=FakeCompleted()) as run:
                self.assertTrue(install_opai.install_from_source(
                    "https://github.com/MarcoLadeira/OPai.git",
                    "main",
                    install_root,
                ))
                fetch_call = run.call_args_list[0][0][0]
                self.assertIn("git", fetch_call)
                self.assertIn("-C", fetch_call)


class VerifyInstallTests(unittest.TestCase):
    def test_verify_install_runs_opai_version_and_doctor(self):
        with mock.patch("subprocess.run", return_value=FakeCompleted()) as run:
            self.assertTrue(install_opai.verify_install())
            commands = [call[0][0] for call in run.call_args_list]
            self.assertTrue(any("--version" in cmd for cmd in commands))
            self.assertTrue(any("doctor" in cmd for cmd in commands))

    def test_verify_install_returns_false_on_failure(self):
        with mock.patch("subprocess.run", return_value=FakeCompleted(returncode=1)):
            self.assertFalse(install_opai.verify_install())


class MainTests(unittest.TestCase):
    def test_main_check_mode_returns_0_when_verify_passes(self):
        with mock.patch.object(install_opai, "check_python_version", return_value=True):
            with mock.patch.object(install_opai, "verify_install", return_value=True):
                result = install_opai.main(["--check"])
                self.assertEqual(result, 0)

    def test_main_check_mode_returns_3_when_verify_fails(self):
        with mock.patch.object(install_opai, "check_python_version", return_value=True):
            with mock.patch.object(install_opai, "verify_install", return_value=False):
                result = install_opai.main(["--check"])
                self.assertEqual(result, 3)

    def test_main_source_only_skips_pypi(self):
        with mock.patch.object(install_opai, "check_python_version", return_value=True):
            with mock.patch.object(install_opai, "install_with_pipx") as pipx:
                with mock.patch.object(install_opai, "install_from_source", return_value=True):
                    with mock.patch.object(install_opai, "verify_install", return_value=True):
                        result = install_opai.main(["--source-only"])
                        self.assertEqual(result, 0)
                        pipx.assert_not_called()

    def test_main_returns_2_when_all_install_methods_fail(self):
        with mock.patch.object(install_opai, "check_python_version", return_value=True):
            with mock.patch.object(install_opai, "install_with_pipx", return_value=False):
                with mock.patch.object(install_opai, "install_with_uv", return_value=False):
                    with mock.patch.object(install_opai, "install_from_source", return_value=False):
                        result = install_opai.main([])
                        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_install_opai.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.install_opai'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/install_opai.py
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/MarcoLadeira/OPai.git"
DEFAULT_INSTALL_ROOT = Path.home() / ".opai" / "source"


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(argv))
    return subprocess.run(argv, **kwargs)


def check_python_version() -> bool:
    if sys.version_info >= (3, 10):
        return True
    print("Python 3.10+ is required.", file=sys.stderr)
    return False


def install_with_pipx(package: str = "opai") -> bool:
    pipx = shutil.which("pipx")
    if not pipx:
        return False
    result = run([pipx, "install", package], capture_output=True)
    return result.returncode == 0


def install_with_uv(package: str = "opai") -> bool:
    uv = shutil.which("uv")
    if not uv:
        return False
    result = run([uv, "tool", "install", package], capture_output=True)
    return result.returncode == 0


def install_from_source(repo_url: str, branch: str, install_root: Path) -> bool:
    install_root = Path(install_root)
    if (install_root / ".git").exists():
        commands = [
            ["git", "-C", str(install_root), "fetch", "origin", branch],
            ["git", "-C", str(install_root), "checkout", branch],
            ["git", "-C", str(install_root), "pull", "--ff-only", "origin", branch],
        ]
    else:
        install_root.parent.mkdir(parents=True, exist_ok=True)
        commands = [
            ["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(install_root)]
        ]

    for cmd in commands:
        result = run(cmd, capture_output=True)
        if result.returncode != 0:
            return False

    result = run(
        [sys.executable, "-m", "pip", "install", "-e", str(install_root)],
        capture_output=True,
    )
    return result.returncode == 0


def verify_install() -> bool:
    for cmd in [
        [sys.executable, "-m", "opai", "--version"],
        [sys.executable, "-m", "opai", "doctor"],
    ]:
        result = run(cmd, capture_output=True)
        if result.returncode != 0:
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Production OPai installer")
    parser.add_argument("--check", action="store_true", help="Verify existing install only")
    parser.add_argument("--source-only", action="store_true", help="Skip PyPI, install from source")
    parser.add_argument("--repo-url", default=REPO_URL)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    if not check_python_version():
        return 1

    if args.check:
        return 0 if verify_install() else 3

    if not args.source_only:
        if install_with_pipx() or install_with_uv():
            return 0 if verify_install() else 3

    if install_from_source(args.repo_url, args.branch, args.install_root):
        return 0 if verify_install() else 3

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_install_opai.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/install_opai.py tests/test_install_opai.py
git commit -m "feat: add testable Python installer module (issues #353 #354)"
```

---

### Task 2: Rewrite install.ps1 as Production Wrapper

**Files:**
- Modify: `install.ps1`
- Test: `tests/test_install_ps1.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_install_ps1.py
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeCompleted


ROOT = Path(__file__).resolve().parents[1]


class InstallPs1Tests(unittest.TestCase):
    def test_install_ps1_calls_python_installer_script(self):
        script = ROOT / "install.ps1"
        self.assertTrue(script.exists())
        content = script.read_text(encoding="utf-8")
        self.assertIn("scripts/install_opai.py", content)
        self.assertIn("python", content.lower())

    def test_install_ps1_supports_check_flag(self):
        content = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("--check", content)

    def test_install_ps1_supports_source_only_flag(self):
        content = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("--source-only", content)

    def test_install_ps1_falls_back_to_downloading_installer(self):
        content = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("raw.githubusercontent.com", content)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_install_ps1.py -v`
Expected: FAIL — current `install.ps1` does not reference `scripts/install_opai.py`

- [ ] **Step 3: Write minimal implementation**

Replace `install.ps1` with:

```powershell
param(
    [switch] $Check,
    [switch] $SourceOnly,
    [string] $RepoUrl,
    [string] $Branch,
    [string] $InstallRoot,
    [string] $ProjectRoot
)

$ErrorActionPreference = "Stop"

$Python = if ($env:OPAI_PYTHON) {
    $env:OPAI_PYTHON
} else {
    (Get-Command python -CommandType Application -ErrorAction Stop).Source
}

$ScriptPath = Join-Path $PSScriptRoot "scripts/install_opai.py"
if (-not (Test-Path $ScriptPath)) {
    $ScriptPath = Join-Path $env:TEMP "install_opai.py"
    $Url = "https://raw.githubusercontent.com/MarcoLadeira/OPai/main/scripts/install_opai.py"
    Invoke-WebRequest -Uri $Url -OutFile $ScriptPath
}

$InstallArgs = @()
if ($Check) { $InstallArgs += "--check" }
if ($SourceOnly) { $InstallArgs += "--source-only" }
if ($RepoUrl) { $InstallArgs += "--repo-url", $RepoUrl }
if ($Branch) { $InstallArgs += "--branch", $Branch }
if ($InstallRoot) { $InstallArgs += "--install-root", $InstallRoot }
if ($ProjectRoot) { $InstallArgs += "--project-root", $ProjectRoot }

& $Python $ScriptPath @InstallArgs
exit $LASTEXITCODE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_install_ps1.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add install.ps1 tests/test_install_ps1.py
git commit -m "feat: rewrite install.ps1 as production wrapper (issue #353)"
```

---

### Task 3: Rewrite install.sh as Production Wrapper

**Files:**
- Modify: `install.sh`
- Test: `tests/test_install_sh.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_install_sh.py
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from _helpers import FakeCompleted


ROOT = Path(__file__).resolve().parents[1]


class InstallShTests(unittest.TestCase):
    def test_install_sh_calls_python_installer_script(self):
        script = ROOT / "install.sh"
        self.assertTrue(script.exists())
        content = script.read_text(encoding="utf-8")
        self.assertIn("scripts/install_opai.py", content)
        self.assertIn("python", content)

    def test_install_sh_supports_check_flag(self):
        content = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("--check", content)

    def test_install_sh_supports_source_only_flag(self):
        content = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("--source-only", content)

    def test_install_sh_falls_back_to_downloading_installer(self):
        content = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("raw.githubusercontent.com", content)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_install_sh.py -v`
Expected: FAIL — current `install.sh` does not reference `scripts/install_opai.py`

- [ ] **Step 3: Write minimal implementation**

Replace `install.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

PYTHON="${OPAI_PYTHON:-python}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/scripts/install_opai.py"

if [ ! -f "$SCRIPT" ]; then
    SCRIPT="$(mktemp)"
    curl -fsSL "https://raw.githubusercontent.com/MarcoLadeira/OPai/main/scripts/install_opai.py" -o "$SCRIPT"
fi

exec "$PYTHON" "$SCRIPT" "$@"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_install_sh.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add install.sh tests/test_install_sh.py
git commit -m "feat: rewrite install.sh as production wrapper (issue #354)"
```

---

### Task 4: Add Cross-Platform Install Smoke CI Job

**Files:**
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_ci_install_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ci_install_smoke.py
from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class CiInstallSmokeTests(unittest.TestCase):
    def test_ci_has_install_smoke_job(self):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        self.assertIn("install-smoke", workflow["jobs"])

    def test_install_smoke_runs_on_three_platforms(self):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        job = workflow["jobs"]["install-smoke"]
        operating_systems = set(job["strategy"]["matrix"]["os"])
        self.assertEqual(
            operating_systems,
            {"windows-latest", "ubuntu-latest", "macos-latest"},
        )

    def test_install_smoke_runs_platform_installer(self):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        job = workflow["jobs"]["install-smoke"]
        command_text = str(job)
        self.assertIn("install.ps1", command_text)
        self.assertIn("install.sh", command_text)

    def test_install_smoke_runs_smoke_install_script(self):
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        job = workflow["jobs"]["install-smoke"]
        command_text = str(job)
        self.assertIn("scripts/smoke-install.py", command_text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ci_install_smoke.py -v`
Expected: FAIL — no `install-smoke` job exists in `ci.yml`

- [ ] **Step 3: Write minimal implementation**

Add the following job to `.github/workflows/ci.yml` after the `clean-install` job:

```yaml
  install-smoke:
    name: Install smoke (${{ matrix.os }})
    if: github.event_name != 'pull_request'
    runs-on: ${{ matrix.os }}
    timeout-minutes: 15
    strategy:
      fail-fast: false
      matrix:
        os:
          - windows-latest
          - ubuntu-latest
          - macos-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Run platform installer (Windows)
        if: runner.os == 'Windows'
        shell: pwsh
        run: .\install.ps1 --check

      - name: Run platform installer (Unix)
        if: runner.os != 'Windows'
        run: ./install.sh --check

      - name: Run wheel install smoke test
        run: python scripts/smoke-install.py

      - name: Upload install logs on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: install-smoke-logs-${{ matrix.os }}
          path: |
            *.log
            logs/
          if-no-files-found: ignore
          retention-days: 7
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ci_install_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml tests/test_ci_install_smoke.py
git commit -m "ci: add cross-platform install smoke job (issue #355)"
```

---

### Task 5: Fix Paid-Era Language Remnants

**Files:**
- Modify: `CHANGELOG.md`
- Create: `scripts/check_no_paid_terms.py`
- Test: `tests/test_no_paid_terms.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_no_paid_terms.py
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NoPaidTermsTests(unittest.TestCase):
    def test_changelog_has_no_paid_tier_ctas(self):
        content = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        banned = ["Founding Pro", "Team Pilot", "paid users", "checkout"]
        for term in banned:
            self.assertNotIn(term, content, f"CHANGELOG.md still contains banned term: {term}")

    def test_no_paid_terms_script_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_no_paid_terms.py")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_no_paid_terms.py -v`
Expected: FAIL — `CHANGELOG.md` still contains "Founding Pro", "Team Pilot", "paid users", and `scripts/check_no_paid_terms.py` does not exist

- [ ] **Step 3: Write minimal implementation**

**Update `CHANGELOG.md`:**

Replace the `0.2.0 Alpha.1` section with:

```markdown
## 0.2.0 Alpha.1

- Added the public static launch funnel under `site/`, ready for Cloudflare
  Pages deployment.
- Switched the launch funnel to free public alpha access: no checkout, license,
  invitation, or private access requirement.
- Documented the 30-day go-to-market plan, public usage signals, and release
  checklist.
- Kept CLI telemetry off by default; site analytics are limited to Cloudflare
  Web Analytics.
```

**Create `scripts/check_no_paid_terms.py`:**

```python
from __future__ import annotations

import re
import sys
from pathlib import Path

BANNED_PATTERNS = [
    r"\bFounding Pro\b",
    r"\bTeam Pilot\b",
    r"\bpaid users\b",
    r"\bcheckout\b",
    r"\bLemon Squeezy\b",
    r"\bGumroad\b",
    r"\bprivate access\b",
]

SCAN_PATHS = [
    "CHANGELOG.md",
    "README.md",
    "site/index.html",
]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []

    for rel in SCAN_PATHS:
        path = root / rel
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        for pattern in BANNED_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                line = content[: match.start()].count("\n") + 1
                violations.append(f"{rel}:{line}: {match.group(0)}")

    if violations:
        print("Paid-era terms found in active docs:")
        for v in violations:
            print(f"  {v}")
        return 1

    print("No paid-era terms found in active docs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_no_paid_terms.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md scripts/check_no_paid_terms.py tests/test_no_paid_terms.py
git commit -m "docs: fix paid-era language remnants and add regression check (issue #358)"
```

---

## Final Verification

- [ ] Run `python -m pytest tests/test_install_opai.py tests/test_install_ps1.py tests/test_install_sh.py tests/test_ci_install_smoke.py tests/test_no_paid_terms.py -v`
- [ ] Run `python scripts/ci_local.py --full`
- [ ] Verify `python scripts/check_no_paid_terms.py` passes

---

## Self-Review

**Spec coverage:**
- #353 (install.ps1 production): Task 2
- #354 (install.sh production): Task 3
- #355 (CI smoke test): Task 4
- #358 (paid-era language): Task 5

**Placeholder scan:** No TBD/TODO/placeholder text. All code is complete.

**Type consistency:** `scripts/install_opai.py` exports `check_python_version`, `install_with_pipx`, `install_with_uv`, `install_from_source`, `verify_install`, `main` — used consistently across tests and shell wrappers.
