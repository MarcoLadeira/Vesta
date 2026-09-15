# Branded Executable — Current State and Path (F3)

**Finding (QA E2E 2026-07-17, F3):** the desktop app runs as `pythonw.exe`;
OS trust/permission dialogs show "pythonw.exe", not "Vesta" (related to #148).

**Status: documented — packaging follow-up.** No engine code change is
required; this is a release-packaging item. This document records why the
dialogs say `pythonw.exe`, what already exists, and the recommended path.

## Why OS dialogs show `pythonw.exe`

There are two ways to launch the Vesta desktop app today:

1. **pip / source install (`opai-gui`).** `pyproject.toml` declares a
   `[project.gui-scripts]` entry `opai-gui = opai.cli:gui_main` (#148). On
   Windows, pip builds that entry into a small GUI launcher whose job is to
   start the app **without a console window** — and it does so by spawning
   `pythonw.exe`. The running process image is therefore the stock Python
   interpreter, and every OS surface that keys on the process image — SmartScreen /
   trust prompts, firewall and permission dialogs, Task Manager's *Processes*
   list, crash dialogs — names `pythonw.exe`, not Vesta.
2. **Native portable artifact (already built).** `scripts/build_desktop_artifacts.py`
   compiles the app with PySide6 Deploy + Nuitka into a real `OPai.exe` /
   `OPai.app` with the Vesta title and icon embedded
   (`opaihub/desktop_artifacts.py`, spec `title = Vesta`, bundled
   `opai/assets/opai-icon.png`). This channel is currently `unsigned-prealpha`
   per `docs/DESKTOP_ARTIFACT_RELEASE.md`.

So the finding applies to the **pip-installed developer path**, not to the
(native) release path — the branded binary already exists but is not yet the
thing most users run, and it is not yet signed.

## What is already mitigated

- **Taskbar / Alt-Tab grouping:** `opai/gui_identity.py` sets a stable Windows
  AppUserModelID (`OPai.Desktop`) before the first window is shown, so the
  taskbar and window switcher group Vesta under its own icon instead of
  `pythonw.exe`. This fixes window *grouping* only; it cannot rename the
  underlying process image, so trust/permission dialogs still say
  `pythonw.exe`.
- **Brand metadata in install evidence:** `opai/installer.py` records brand,
  version, and release stage in the install manifest, so support/diagnostics
  can always identify the product even when the process name cannot.

## Options

| Option | What changes | Cost | Effect on OS dialogs |
| --- | --- | --- | --- |
| A. Ship the existing Nuitka artifact as the primary download | Release process, not engine code | Low — pipeline exists | Dialogs show `OPai.exe` (publisher "Unknown" until signed) |
| B. Add Windows version-resource metadata to the Nuitka build (`--windows-product-name`, `--windows-company-name`, `--windows-file-version`) | `opaihub/desktop_artifacts.py` spec args | Very low | Properties/details show Vesta; dialogs still need signing for a publisher name |
| C. Code-sign the artifact (Authenticode cert; Apple Developer ID + notarization on macOS) | Release infra; environment secrets per `docs/DESKTOP_ARTIFACT_RELEASE.md` | Medium — certificate cost + protected CI environment | Dialogs show the verified publisher; SmartScreen warnings disappear over reputation |
| D. Replace the pip `gui-scripts` launcher with a compiled shim (PyInstaller/Briefcase) | New packaging for the pip path | High — duplicates the existing Nuitka pipeline | Same as A but for pip installs |

## Recommended path

1. **Make the native artifact the primary desktop download** (Option A) and
   treat the pip `opai-gui` launcher as a developer convenience whose process
   identity (`pythonw.exe`) is expected and documented — this document is that
   documentation.
2. **Add the Windows version-resource flags** (Option B) to the existing
   deploy spec so even unsigned builds self-identify in file properties. This
   is a small `opaihub/desktop_artifacts.py` change and is filed as a
   packaging follow-up (cross-workstream handoff; not part of this fix bundle).
3. **Complete the signing/notarization track** (Option C) exactly as
   `docs/DESKTOP_ARTIFACT_RELEASE.md` already specifies — protected
   environment, credential-free smoke, OIDC attestation. Option C is what
   ultimately puts "Vesta" (a verified publisher) into OS trust dialogs.
4. Do **not** invest in Option D: a second packaging pipeline for the pip path
   duplicates A–C for little user benefit.

## References

- QA report: `docs/QA_E2E_ISSUE219_2026-07-17.md` (finding F3)
- Resolution map: `docs/QA_E2E_ISSUE219_RESOLUTION.md`
- Release/signing runbook: `docs/DESKTOP_ARTIFACT_RELEASE.md`
- Build entry point: `scripts/build_desktop_artifacts.py`
- Window identity: `opai/gui_identity.py`
