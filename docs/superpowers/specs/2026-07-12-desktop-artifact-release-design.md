# Free Desktop Artifact Release Design

**Date:** 2026-07-12
**Status:** Approved for implementation under the owner's continuing alpha-programme direction
**Issue:** [#290 — Build and smoke-test free Windows and macOS desktop artifacts](https://github.com/MarcoLadeira/OPai/issues/290)

## Scope and non-goals

OPai needs a desktop artifact that a user can run without a source checkout,
their development Python, or a pre-existing OPai install. This slice produces
the reproducible build, inventory, smoke, provenance, and signing-status
contracts for the fully free alpha. It does not add a license, entitlement,
payment, telemetry, or hosted-release trigger.

Actual code signing, macOS notarisation, clean macOS execution, and publishing
remain external release gates because they require platform runners and
maintainer-controlled credentials. The implementation must expose those gates
instead of simulating their completion.

## Options considered

| Option | Assessment |
|---|---|
| Wheel-only release | Already has a useful hermetic smoke, but it does not prove a real desktop GUI application or a source-free launcher. Insufficient for #290. |
| Add PyInstaller | Would introduce a second packaging stack and a new deployment policy without using the PySide6-supported path. Rejected. |
| PySide6 Deploy plus Nuitka standalone bundles | Uses the official Qt/PySide deployment path, produces native Windows/macOS launchers, and lets OPai own a small, testable provenance/smoke layer. Selected. |

## Artifact contract

A build produces one platform-labelled **portable** bundle with two deployable
components. The archive filename always carries its release channel, for
example `OPai-v0.2.0a2-windows-unsigned-prealpha.zip`; an unsigned bundle is
never named or uploaded as a production artifact.

```text
OPai-<tag>-<platform>/
  gui/                  # standalone OPai GUI deployment
  cli/                  # standalone opai CLI deployment
  provenance.json       # tag, immutable commit, platform, build schema
  SHA256SUMS.txt        # hashes every distributable and evidence file except itself
  signing-status.json   # explicit signed / unsigned-prealpha state
```

For a production portable archive, a GitHub OIDC artifact attestation sits
outside this directory and binds the final ZIP. The in-bundle manifest is an
inventory check, not an authenticity root; `production_ready` remains false in
the mutable bundle until a consumer verifies the detached archive attestation.

The GUI entry point is `opai.cli:gui_main`; the CLI entry point is
`opai.cli:main`. The build configuration explicitly includes the web GUI asset
tree and hub registry data so a frozen application does not silently fall back
or lose its runtime registry. The output is standalone rather than one-file so
inspection, asset validation, and incident diagnosis remain practical.

For the alpha, installation is deliberately a portable lifecycle: extract into
a user-chosen application directory; upgrade by stopping OPai and replacing the
directory; uninstall by deleting that directory; rollback by restoring the
previous extracted directory. User state lives outside the artifact in OPai's
normal per-user state directory, so upgrade and uninstall do not silently
delete it. The runbook and clean-machine smoke must rehearse this lifecycle.

## Trust boundary

- A normal build requires `HEAD` to be exactly annotated by a safe semantic
  version Git tag such as `v0.2.0-alpha.2`. `--allow-untagged` is a clearly
  labelled local rehearsal escape hatch only.
- The build refuses to overwrite a non-empty output directory.
- The manifest hashes all distributable files after packaging and records the
  exact commit independently of the human-readable tag. It also hashes
  `provenance.json` and `signing-status.json`; only `SHA256SUMS.txt` is
  excluded to avoid a self-hash cycle.
- The smoke check validates the manifest, expected GUI assets, source-free CLI
  commands, and a real frozen QtWebEngine window. The GUI smoke waits for the
  local page and QWebChannel bridge to boot, writes a result, closes the window,
  and checks that it left no new helper process behind. `gui --once` remains a
  useful headless state check but is never GUI-render proof.
- An isolated build virtual environment uses exact, hash-locked native inputs
  selected for its operating system. `desktop-build-bootstrap.lock` installs
  the pinned backend before the full Windows/macOS lock installs Nuitka without
  build isolation; OPai is then installed with `--no-deps`. A lock must be
  generated and reviewed on its matching CPython 3.13 platform—Windows hashes
  are never copied to macOS. Provenance records the Git tag, commit,
  Python/tool versions, operating system, architecture, and build dependency
  report. The builder invokes the PySide6 Deploy executable from that
  environment, never an ambient shell PATH.
- The GUI deploy spec explicitly includes WebChannel, WebEngineCore,
  WebEngineWidgets, runtime web files, fonts, icon/mascot, hub data, and the
  QtWebEngine helper/resources. The CLI uses direct Nuitka standalone packaging
  so it does not inherit a GUI payload unnecessarily.
- Signing is verified executable state and expected publisher identity, not a
  caller-provided label. Windows release mode binds every code file to a
  protected signer thumbprint; macOS binds both components to a protected Team
  ID and records codesign, Gatekeeper, and notarization/stapling verification.
  Hashes are written after signing. The protected signing job freezes the ZIP
  before any artifact execution; a separate credential-free smoke job executes
  a downloaded copy, and a final no-artifact-execution job attests the original
  ZIP through GitHub OIDC. Without those platform commands, the expected
  identity, or the outer archive attestation succeeding, production workflow
  execution fails.
- The protected signing job checks out only pinned Actions, requires the safe
  semantic-version annotated release-tag commit to be reachable from protected
  `main`, creates temporary keychains/PFX files only inside credential-scoped
  steps, and deletes them before the step ends. It has neither OIDC nor
  attestation permission. The smoke job has no environment secrets, OIDC, or
  attestation permission. It reads a non-secret publisher-identity record
  written only after protected signing validation; the separate attestation job
  does not execute an artifact and pins consumers to this workflow and
  GitHub-hosted runners.
- The post-build scan checks artifact inventory and logs for secret-shaped
  values, private URLs, and development overrides. The smoke environment clears
  `PYTHONPATH`, provider credentials, `OPAI_HUB_ROOT`, and source-working-tree
  influence as well as its home/config directories.
- The hosted workflow is manual-only. Writing it consumes no Actions minutes;
  an operator must explicitly approve a run after release credentials and
  platform resources are ready.

## Components

| Component | Responsibility |
|---|---|
| `requirements/desktop-build.in` and platform `.lock` files | Reviewed direct inputs plus hash-locked native dependencies for each CPython 3.13 runner. |
| `requirements/desktop-build-bootstrap.lock` | Hash-locked pip/setuptools/wheel bootstrap for the sdist-only Nuitka build. |
| `requirements/desktop-build.txt` | Small exact PySide6/Nuitka contract recorded by build provenance. |
| `scripts/desktop_gui_entry.py` and `scripts/desktop_cli_entry.py` | Narrow native-launcher entry points with no policy or license logic. |
| `opai/gui_web.py` | An artifact-only QtWebEngine/bridge load-and-close smoke seam, exercised only in a real desktop artifact. |
| `opaihub/desktop_artifacts.py` | Pure release/tag, deployment-spec, manifest, publisher-identity, inventory, portable lifecycle, and smoke helpers. |
| `scripts/build_desktop_artifacts.py` | CLI that builds in a dedicated environment, invokes PySide6 Deploy for GUI plus direct Nuitka for CLI, collects component output, and writes unsigned build evidence. |
| `scripts/finalize_desktop_artifact.py` | Rewrites checksums only after a non-empty external verification log is hashed and identifies the verification tool; it rejects rehearsal provenance and logs inside the bundle. |
| `scripts/smoke_desktop_artifacts.py` | CLI that verifies a finished bundle, scans it, executes its CLI, invokes its real GUI smoke, and compares helper-process state. |
| `tests/test_desktop_artifacts.py` | Hermetic contract tests for tag/channel gating, tool pinning, data inclusion, manifest integrity, unsigned labelling, command construction, scan policy, and clean-home smoke commands. |
| `.github/workflows/desktop-artifacts.yml` | Manual two-platform release-rehearsal workflow with explicit unsigned-prealpha input and separate signing, credential-free smoke, and archive-attestation jobs. |
| `docs/DESKTOP_ARTIFACT_RELEASE.md` | Operator runbook, signing/notarisation gate, rollback, upgrade/uninstall rehearsal, and known limitations. |

## Verification strategy

The normal development gate is fully local: test the helper module, inspect
the generated deployment commands with PySide6 Deploy/Nuitka dry-runs, validate
the workflow contract, and run the current wheel smoke. A manual release
rehearsal then checks out the requested exact tag into a clean Windows and
macOS runner, builds in a fresh pinned virtual environment, runs the artifact
smoke and portable lifecycle rehearsal, scans the produced files/logs, uploads
the archive/provenance/checksums, verifies expected publisher identity, freezes
the final archive before it is ever executed, smokes a downloaded copy without
release credentials, then attests the original archive. It fails production
mode when signing, notarization, smoke, or outer-attestation evidence is absent.

The following remain intentionally unclaimed until a real platform run records
them: macOS lock generation/installation, protected signing-environment setup,
native GUI rendering on an artifact, macOS notarization, Windows signing,
archive-attestation verification, upgrade/uninstall, rollback, and no-orphan
verification after a real GUI exit.
