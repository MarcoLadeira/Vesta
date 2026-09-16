# Hermetic Runtime Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make clean wheel installs and both Python test harnesses independent of undeclared packages, real provider variables, OS keyrings, and installed CLIs.

**Architecture:** Declare core parsing dependencies in package metadata, fail registry loads with one explicit error contract, execute artifact smoke commands under a disposable environment, and enforce clean/hostile invariants in a three-platform CI matrix. Tests inject credentials rather than reading the developer machine.

**Tech Stack:** Python 3.10+, setuptools, PyYAML, unittest, pytest, GitHub Actions.

---

### Task 1: Registry dependency and error contract

**Files:**
- Modify: `pyproject.toml`
- Modify: `vestahub/loader.py`
- Create: `tests/test_runtime_dependencies.py`

- [x] Write tests asserting a bounded PyYAML requirement and path-aware errors for missing PyYAML, malformed YAML, and invalid UTF-8.
- [x] Run `python -m pytest tests/test_runtime_dependencies.py -q` and verify the new assertions fail on current main.
- [x] Add `PyYAML>=6.0.2,<7` to core dependencies and implement `RegistryLoadError` without the YAML-as-JSON fallback.
- [x] Run the focused tests and `python -m vestahub validate`.

### Task 2: Artifact smoke contract

**Files:**
- Modify: `scripts/smoke-install.py`
- Extend: `tests/test_runtime_dependencies.py`
- Modify: `README.md`
- Modify: `docs/PUBLISHING.md`

- [x] Write tests for dependency wheel collection, disposable home/environment construction, and the four required CLI surfaces.
- [x] Verify the tests fail because the current wheel build uses `--no-deps` and omits three required commands.
- [x] Build dependency wheels, sanitize provider variables, redirect home/config directories, and run `vesta --help`, `vesta doctor`, `vestahub validate`, and `vesta gui --once` outside the checkout.
- [x] Update installation documentation so ordinary installs resolve declared dependencies.
- [x] Run `python scripts/smoke-install.py` and verify the installed wheel rather than the checkout supplies every command.

### Task 3: Hostile-machine test hermeticity

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/fixtures/hostile_keyring/keyring/__init__.py`
- Extend: `tests/test_runtime_dependencies.py`

- [x] Write a pytest assertion proving provider variables and the live keyring are isolated while explicit per-test injection still works.
- [x] Verify it fails before the autouse fixture exists.
- [x] Add the narrow autouse fixture and populated fake keyring package; do not scrub unrelated environment state.
- [x] Run affected tests under hostile provider variables and the fake keyring with both pytest and unittest discovery.

### Task 4: Cross-platform CI evidence

**Files:**
- Modify: `.github/workflows/ci.yml`
- Extend: `tests/test_runtime_dependencies.py`

- [x] Write workflow-contract tests for Windows/Linux/macOS wheel smoke and both hostile test harnesses.
- [x] Verify the workflow assertions fail on the current two-job CI definition.
- [x] Add a three-platform `clean-install` matrix and one hostile-environment job with explicit fake provider state.
- [x] Run workflow-contract tests and validate the final YAML structure.

### Task 5: Full release gate

- [x] Run full unittest and pytest suites, JavaScript unit tests, Chromium E2E, Ruff format/lint, Bandit, dependency and secret scans, registry validation, and isolated wheel smoke.
- [x] Review the diff for credential values, network calls, overlap with #137, and unrelated changes.
- [ ] Commit, push, open a PR closing #136/#150, wait for all required CI, and merge only when GitHub reports a clean merge state.
