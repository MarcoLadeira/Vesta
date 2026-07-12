# Desktop Artifact Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Produce a reproducible, source-free, fully free desktop artifact pipeline with explicit release evidence and no silent signing bypass.

**Architecture:** Use PySide6 Deploy/Nuitka in standalone mode for the GUI and direct Nuitka standalone mode for the CLI. Keep release policy in a small standard-library helper that verifies exact tags, portable lifecycle, manifest/inventory/scan contracts, and a finished bundle; scripts and the manual workflow are thin callers. A real frozen QtWebEngine window/bridge smoke replaces `gui --once` as desktop-render proof.

**Tech Stack:** Python 3.10+ standard library, PySide6 Deploy/Nuitka, GitHub Actions YAML, unittest, existing hermetic wheel smoke.

---

## Review amendments applied before execution

- Build tools are pinned in `requirements/desktop-build.txt` and run from a
  dedicated virtual environment, not from an ambient PATH.
- The GUI deployment includes the QtWebEngine modules/resources, production web
  assets, fonts, icon/mascot, and hub data explicitly. The CLI avoids a Qt
  payload by using direct Nuitka.
- The alpha artifact is a documented portable archive with extract/replace/
  delete/restore lifecycle rehearsal. It is not presented as an installer.
- A true GUI smoke creates a frozen `QWebEngineView`, waits for the local page
  and QWebChannel bridge, closes it, and checks its helper-process delta.
- A production release ref is an exact requested safe semantic-version tag
  reachable from protected `main`. Signing status is derived from platform
  verification against protected publisher identity; the protected signing job
  freezes the finished archive before artifact execution, a separate
  credential-free job smokes a downloaded copy, and a final OIDC job attests the
  original archive outside its mutable manifest. An unsigned archive is
  channel-labelled `unsigned-prealpha` and cannot pass production mode.
- Artifact inventory and logs are scanned for secret-shaped values, private
  URLs, and development overrides under a hostile, source-independent runtime
  environment.
- The workflow uses a hash-locked bootstrap followed by a distinct native lock
  for Windows or macOS. A missing platform lock fails closed rather than
  resolving packages during a release; macOS lock generation remains a
  controlled-platform evidence gate.

## Execution record — 2026-07-12

- [x] Task 1: exact-tag/rehearsal provenance, atomic evidence, checksums, and
  signed-status validation are implemented in `opaihub/desktop_artifacts.py`.
- [x] Task 2: separate GUI/CLI entry points, exact PySide6/Nuitka pins, and
  explicit runtime data/module deployment specs are implemented.
- [x] Task 3: source-independent build, smoke, inventory scan, hostile
  environment, real QtWebEngine/QWebChannel smoke, and helper-process checks
  are implemented and dry-run locally without downloads.
- [x] Task 4: the manual exact-tag Windows/macOS workflow, signing verifier
  finalizer, unsigned-production policy, and portable lifecycle runbook are
  implemented and contract-tested.
- [x] Security amendment: production signing credentials are scoped only to
  protected signing steps; the signer receives no OIDC or attestation
  permission and archives before artifact execution; a separate non-secret
  smoke job has no OIDC/attestation permission and reads only the validated
  non-secret publisher-identity record from signing output; the final
  no-execution job attests the original ZIP. PFX/keychain material is deleted
  before the signing step ends; signed claims require a live platform verifier
  with a pinned publisher identity; macOS signs/verifies the CLI as well as the
  GUI.
- [x] Reproducibility amendment: a local Windows CPython 3.13 hash-lock
  installation is proven with a bootstrap lock before Nuitka's sdist build.
- [ ] Task 5: hosted clean-platform artifact builds, signing/notarisation,
  controlled macOS lock generation/installation, actual frozen-GUI smoke,
  upgrade/uninstall/rollback rehearsal, and full release-quality verification
  still require recorded external evidence.

### Task 1: Define the artifact contract with failing tests — complete

**Files:**

- Create: tests/test_desktop_artifacts.py
- Create: opaihub/desktop_artifacts.py

- [x] **Step 1: Write the failing test**

    def test_release_ref_requires_an_exact_v_tag():
        with self.assertRaises(ArtifactReleaseError):
            release_ref(root, run_git=fake_git_without_exact_tag)

    def test_manifest_rejects_a_modified_distributable_file():
        write_bundle_evidence(bundle, release, platform="windows")
        (bundle / "cli" / "opai.exe").write_bytes(b"tampered")
        self.assertFalse(verify_bundle(bundle)["ok"])

- [x] **Step 2: Run the test to verify it fails**

Run: python -m pytest tests/test_desktop_artifacts.py -q
Expected: import failure because the release helper does not exist.

- [x] **Step 3: Write the minimal implementation**

    @dataclass(frozen=True)
    class ReleaseRef:
        tag: str
        commit: str

    def release_ref(root: Path, *, allow_untagged: bool = False) -> ReleaseRef: ...
    def write_bundle_evidence(bundle: Path, release: ReleaseRef, *, platform: str) -> None: ...
    def verify_bundle(bundle: Path) -> dict[str, object]: ...

- [x] **Step 4: Run the test to verify it passes**

Run: python -m pytest tests/test_desktop_artifacts.py -q
Expected: all contract tests pass.

- [x] **Step 5: Commit**

    git add opaihub/desktop_artifacts.py tests/test_desktop_artifacts.py
    git commit -m "feat(release): add desktop artifact evidence contract"

### Task 2: Add source-free GUI and CLI build entry points — complete

**Files:**

- Create: scripts/desktop_gui_entry.py
- Create: scripts/desktop_cli_entry.py
- Create: requirements/desktop-build.txt
- Modify: tests/test_desktop_artifacts.py
- Modify: opaihub/desktop_artifacts.py

- [x] **Step 1: Write the failing test**

    def test_deployment_specs_pin_build_tools_and_include_gui_runtime_data():
        specs = deployment_specs(root, output)
        self.assertEqual(specs.gui.tool, "pyside6-deploy")
        self.assertEqual(specs.cli.tool, "python -m nuitka")
        self.assertIn("QtWebEngineWidgets", specs.gui.qt_modules)
        self.assertIn("=opaihub/data", "\n".join(specs.gui.extra_args))
        self.assertEqual(load_build_pins(root)["PySide6"], "6.11.1")
        self.assertEqual(load_build_pins(root)["Nuitka"], "4.0")

- [x] **Step 2: Run the test to verify it fails**

Run: python -m pytest tests/test_desktop_artifacts.py -q
Expected: deployment_specs is not defined.

- [x] **Step 3: Write the minimal implementation**

    # scripts/desktop_gui_entry.py
    from opai.cli import gui_main
    raise SystemExit(gui_main())

    # scripts/desktop_cli_entry.py
    from opai.cli import main
    raise SystemExit(main())

- [x] **Step 4: Run the test to verify it passes**

Run: python -m pytest tests/test_desktop_artifacts.py -q
Expected: all deployment-specification tests pass.

- [x] **Step 5: Commit**

    git add scripts/desktop_gui_entry.py scripts/desktop_cli_entry.py opaihub/desktop_artifacts.py tests/test_desktop_artifacts.py
    git commit -m "feat(release): define PySide desktop deployment inputs"

### Task 3: Add a build and smoke CLI — complete

**Files:**

- Create: scripts/build_desktop_artifacts.py
- Create: scripts/smoke_desktop_artifacts.py
- Modify: tests/test_desktop_artifacts.py
- Modify: opaihub/desktop_artifacts.py

- [x] **Step 1: Write the failing test**

    def test_build_dry_run_uses_official_pyside_deploy_for_both_components():
        commands = build_commands(root, output, deploy_script=Path("deploy.py"), dry_run=True)
        self.assertEqual(len(commands), 2)
        self.assertTrue(all("--dry-run" in command for command in commands))

    def test_smoke_uses_bundle_cli_not_the_source_checkout():
        commands = smoke_commands(bundle, home)
        self.assertTrue(all(str(bundle) in command[0] for command in commands))

- [x] **Step 2: Run the test to verify it fails**

Run: python -m pytest tests/test_desktop_artifacts.py -q
Expected: missing build/smoke command helpers.

- [x] **Step 3: Write the minimal implementation**

Build writes only into a new empty output directory. --dry-run emits a
PySide6 Deploy GUI command and direct Nuitka CLI command without downloading or
compiling. Smoke verifies checksums/assets/inventory, invokes the bundled CLI
under a disposable hostile environment, then launches the frozen GUI smoke and
checks its load result and helper-process delta.

- [x] **Step 4: Run the test to verify it passes**

Run: python -m pytest tests/test_desktop_artifacts.py -q and python scripts/build_desktop_artifacts.py --dry-run --allow-untagged
Expected: tests pass and one PySide6 Deploy plus one direct Nuitka command are
printed without an artifact build.

- [x] **Step 5: Commit**

    git add scripts/build_desktop_artifacts.py scripts/smoke_desktop_artifacts.py opaihub/desktop_artifacts.py tests/test_desktop_artifacts.py
    git commit -m "feat(release): add desktop artifact build and smoke tools"

### Task 4: Make release proof executable but manual — complete

**Files:**

- Create: .github/workflows/desktop-artifacts.yml
- Modify: tests/test_desktop_artifacts.py
- Create: docs/DESKTOP_ARTIFACT_RELEASE.md

- [x] **Step 1: Write the failing test**

    def test_artifact_workflow_is_manual_and_has_tagged_windows_macos_matrix():
        workflow = workflow_text()
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("macos-latest", workflow)
        self.assertIn("release_tag", workflow)
        self.assertIn("release_channel", workflow)
        self.assertIn("allow_unsigned_prealpha", workflow)
        self.assertIn("codesign", workflow)
        self.assertIn("Get-AuthenticodeSignature", workflow)

- [x] **Step 2: Run the test to verify it fails**

Run: python -m pytest tests/test_desktop_artifacts.py -q
Expected: workflow/runbook are absent.

- [x] **Step 3: Write the minimal implementation**

    on:
      workflow_dispatch:
        inputs:
          release_tag:
            required: true
            type: string
          release_channel:
            required: true
            type: choice
            options: [unsigned-prealpha, production]
          allow_unsigned_prealpha:
            required: true
            type: boolean
            default: false

The workflow checks out the exact requested safe semantic-version tag, builds
in the pinned environment, signs and verifies in the protected job, writes
hashes/provenance and archives before artifact execution, smokes/scans a
downloaded copy without release credentials, then attests the original archive.
It refuses to label an unsigned/notarisation-missing artifact as production.

- [x] **Step 4: Run the test to verify it passes**

Run: python -m pytest tests/test_desktop_artifacts.py -q
Expected: all workflow/runbook contract tests pass.

- [x] **Step 5: Commit**

    git add .github/workflows/desktop-artifacts.yml docs/DESKTOP_ARTIFACT_RELEASE.md tests/test_desktop_artifacts.py
    git commit -m "ci(release): add manual desktop artifact rehearsal"

### Task 5: Final local release-proof gate

**Files:**

- Modify only if verification identifies a real defect.

- [x] **Step 1: Run the focused release proof**

Run: python -m pytest tests/test_desktop_artifacts.py tests/test_runtime_dependencies.py -q, python scripts/build_desktop_artifacts.py --dry-run --allow-untagged, and python scripts/smoke-install.py.

- [x] **Step 2: Run static and security checks**

Run: python -m ruff format --check ., python -m ruff check ., python -m bandit -r opai opaihub opcoding -q, python -m opaihub validate, and git diff --check.

- [x] **Step 3: Record non-local blockers honestly**

Document the remaining platform/release evidence: clean Windows/macOS native
artifact smoke, signing/notarisation, portable upgrade/uninstall rehearsal,
rollback dry run, and no-orphan GUI exit proof.
