# OPai Desktop Artifact Release Runbook

OPai 0.2.0 alpha is fully free. Desktop artifacts must never add a payment,
license, entitlement, activation, account, or telemetry requirement.

## Release channels

`unsigned-prealpha` is the only allowed channel before signing credentials and
platform verification are available. Its archive name, bundle name, and
`signing-status.json` must all say `unsigned-prealpha`. It is suitable for
invited alpha testing only and must not be described as a production release.

`production` is permitted only after the manual desktop-artifact workflow has
checked out an exact annotated `v*` tag, verified Windows Authenticode or macOS
codesign/Gatekeeper/notarisation, written post-signing checksums, and completed
the native artifact smoke. A missing certificate, failed timestamp, failed
notarisation, or absent verification log is a hard stop.

## Build rehearsal

1. Create a new isolated Python environment on the target platform.
2. Install `requirements/desktop-build.txt` and `.[desktop-gui]` into that
   environment. Do not use an ambient `pyside6-deploy` or `nuitka` executable.
3. Dispatch **OPai desktop artifact rehearsal** manually. Enter the exact
   annotated release tag, choose the release channel, and explicitly acknowledge
   `allow_unsigned_prealpha` only for an unsigned alpha rehearsal.
4. Retain the uploaded archive, `SHA256SUMS.txt`, `provenance.json`,
   `signing-status.json`, smoke report, and signing verification log together.

The local command below is safe planning evidence only; it neither downloads a
tool nor compiles an artifact:

```powershell
python scripts/build_desktop_artifacts.py --dry-run --allow-untagged
```

An actual build requires an exact tag and a build Python whose PySide6 and
Nuitka versions exactly match `requirements/desktop-build.txt`.

## Portable install, upgrade, uninstall, and rollback

The alpha artifact is a **portable** application, not an installer.

1. Verify the archive checksum against `SHA256SUMS.txt` and inspect
   `provenance.json` and `signing-status.json` before extracting it.
2. Extract the archive into a user-controlled application directory such as
   `C:\Apps\OPai` or `/Applications/OPai Alpha`. Start `OPai` for the desktop
   UI or `opai` for the command line; neither requires a source checkout.
3. For an upgrade, close OPai, keep the previous extracted directory intact,
   extract the new portable bundle into a sibling directory, run the smoke
   journey, and then switch shortcuts to the new directory.
4. For uninstall, close OPai and delete only the extracted application
   directory. Per-user OPai state is outside the portable bundle and is not
   silently deleted; clear it separately only if the user explicitly requests
   that action.
5. For rollback, switch the shortcut back to the retained previous directory.
   Do not overwrite it before the new artifact has passed first launch, missing
   provider, read-only question, exit, and restart checks.

## Required smoke journey

The workflow runs `scripts/smoke_desktop_artifacts.py`, which starts from a
disposable home and fixture project; removes credentials, `PYTHONPATH`,
`OPAI_HUB_ROOT`, local-model overrides, and source-working-tree influence;
checks checksums and inventory; scans artifact/log text for secret-shaped
values, private URLs, and development overrides; runs CLI help and Doctor; and
starts a real QtWebEngine GUI window. The GUI smoke waits for the local page and
QWebChannel boot, closes it, records the result outside the artifact, and checks
for a new surviving `QtWebEngineProcess` helper.

The following still require recorded human/platform evidence before a public
alpha claim: clean Windows and macOS artifact launches, provider setup and
missing-provider recovery, upgrade/uninstall rehearsal, rollback rehearsal,
accessibility keyboard journey, and live signing/notarisation verification.
