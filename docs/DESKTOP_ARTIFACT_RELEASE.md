# OPai Desktop Artifact Release Runbook

OPai 0.2.0 alpha is fully free. Desktop artifacts must never add a payment,
license, entitlement, activation, account, or telemetry requirement.

## Production trust boundary

An unsigned tag may build a rehearsal without credentials. Production signing is
different: dispatch it only from the protected `main` workflow ref. The separate
signing job pauses at the `opai-production-signing` environment before it can
read any certificate or notarisation value. It signs, verifies, finalizes, and
archives the bundle, but never executes it.

Before production use, a repository owner must configure that environment with:

1. The `OPAI_WINDOWS_PFX_BASE64`, `OPAI_WINDOWS_PFX_PASSWORD`,
   `OPAI_APPLE_DEVELOPER_ID`, `OPAI_APPLE_SIGNING_CERTIFICATE_BASE64`,
   `OPAI_APPLE_SIGNING_CERTIFICATE_PASSWORD`, `OPAI_APPLE_NOTARY_APPLE_ID`,
   `OPAI_APPLE_NOTARY_TEAM_ID`, and `OPAI_APPLE_NOTARY_APP_SPECIFIC_PASSWORD`
   values as **environment secrets only**. Remove same-named repository or
   organisation secrets so an unprotected job cannot read them. The workflow
   creates a unique, temporary notarization profile in a temporary keychain; it
   never assumes a pre-existing profile on a hosted runner.
2. The non-secret, environment-scoped variables
   `OPAI_WINDOWS_SIGNER_THUMBPRINT` (the exact 40-hex Authenticode signer
   thumbprint) and `OPAI_MACOS_TEAM_ID` (the exact ten-character Developer ID
   Team ID). The macOS notarization team must match the protected Team ID.
3. A required release reviewer, with self-review prevention enabled, and no
   administrator bypass for the signing environment.
4. A deployment policy restricted to `main`. The reviewer must compare the
   requested annotated tag and immutable commit with the recorded provenance
   before approving the job.

The workflow never injects signing variables into the unsigned build or smoke
job. It deletes the Windows PFX and the macOS temporary keychain/certificate
before the signing step ends. A separate credential-free smoke job downloads a
copy of the already archived signed output and runs the artifact with a strict
runtime allowlist; that job has no environment secrets, no OIDC token, no
attestation permission, and a non-persistent checkout credential. It verifies
the exact publisher using the non-secret identity record written only after the
protected signing step has validated it, rather than reading protected
environment variables. A third job downloads the immutable signed archive only
after smoke succeeds and creates the OIDC attestation without executing the
artifact. Every third-party Action is pinned to a reviewed full commit SHA.
Before a protected signing step can read credentials, the workflow requires a
safe semantic-version annotated tag whose commit is reachable from the
protected `main` commit.

## Archive authenticity

Native signatures authenticate executable code and expected publisher identity,
but a portable bundle also contains web/data resources that native signatures
do not necessarily bind. `SHA256SUMS.txt`, `provenance.json`, and
`signing-status.json` are therefore useful inventory evidence, not a root of trust:
an attacker who can rewrite the archive can rewrite those files too.

For every production ZIP, the protected attestation job creates a GitHub OIDC
artifact attestation after the credential-free smoke succeeds. The in-bundle
`production_ready` field deliberately stays `false`; it cannot truthfully
assert an outer trust mechanism that is not inside the archive. A public release
requires both a successful native signature check and a verified archive
attestation, pinned to this exact release workflow on `refs/heads/main` and to
GitHub-hosted runners:

```sh
gh attestation verify OPai-<tag>-<platform>-production.zip \
  --repo MarcoLadeira/OPai \
  --signer-workflow MarcoLadeira/OPai/.github/workflows/desktop-artifacts.yml \
  --source-ref refs/heads/main \
  --deny-self-hosted-runners
```

The attestation must be retrieved from the same GitHub repository as the
release asset and verified before extraction. If the protected environment or
repository plan cannot create that workflow-pinned attestation, production
release fails closed.

## Reproducible native build inputs

Native wheels are platform-specific. The workflow therefore selects one
hash-locked input file per runner instead of treating a Windows resolution as
evidence for macOS:

| Runner | Required lock |
| --- | --- |
| `windows-latest` | `requirements/desktop-build.windows.lock` |
| `macos-latest` | `requirements/desktop-build.macos.lock` |

`desktop-build-bootstrap.lock` installs the exact `pip`, `setuptools`, and
`wheel` versions first. It lets the full lock install Nuitka (an sdist) with
build isolation disabled, without resolving an unpinned backend. The workflow
then installs OPai with `--no-deps`, so the checked-in lock remains the only
dependency resolver.

Never copy a Windows hash list to macOS. Generate and review each platform lock
on that platform's CPython 3.13 environment, using an isolated
`pip-tools==7.5.3` environment and the following command:

```powershell
python -m piptools compile --resolver=backtracking --generate-hashes --allow-unsafe --strip-extras --no-header --output-file requirements/desktop-build.windows.lock requirements/desktop-build.in
```

On macOS, replace the output filename with
`requirements/desktop-build.macos.lock`. A missing matching lock is an
intentional hard stop in the workflow; it must not fall back to an unlocked
resolution. The Windows lock has local installation evidence. Generating and
validating the macOS lock on a controlled macOS runner is still an external
alpha-release gate.

## Release channels

`unsigned-prealpha` is the only allowed channel before signing credentials and
platform verification are available. Its archive name, bundle name, and
`signing-status.json` must all say `unsigned-prealpha`. It is suitable for
internal/rehearsal testing only and must not be described as a public or
production release.

`production` is permitted only after the manual desktop-artifact workflow has
checked out an exact annotated safe semantic-version tag (for example
`v0.2.0-alpha.2`) reachable from protected `main`,
verified the expected Windows signer thumbprint or macOS Team ID, written
post-signing checksums, completed the credential-free native artifact smoke,
and attested the finished ZIP. A missing protected identity, certificate,
timestamp, notarization credential, failed notarization, absent verification
log, or failed archive attestation is a hard stop.

Windows production verifies every shipped `.exe`, `.dll`, and `.pyd`; macOS
production signs and verifies both `gui/OPai.app` and `cli/opai`, then submits
the portable bundle for notarization. A current native verifier rejects a
signature from an unexpected publisher; it does not merely accept any trusted
certificate. The GitHub artifact attestation binds the complete ZIP outside the
bundle and is the final public-release authenticity gate.

## Build rehearsal

1. Confirm the target platform's exact lock exists and has been reviewed. The
   current repository has a verified Windows lock; do not start a macOS release
   until the matching macOS lock has been generated and reviewed on macOS.
2. Create a new isolated Python 3.13 environment and install, in order, the
   bootstrap lock, that platform's full lock, and OPai with `--no-deps` and
   `--no-build-isolation`. Do not use an ambient `pyside6-deploy` or `nuitka`
   executable.
3. Dispatch **OPai desktop artifact rehearsal** manually. Enter the exact
   annotated semantic-version release tag, choose the release channel, and
   explicitly acknowledge `allow_unsigned_prealpha` only for an unsigned alpha
   rehearsal.
4. Retain the uploaded archive, `SHA256SUMS.txt`, `provenance.json`,
   `signing-status.json`, smoke report, signing verification log, and archive
   attestation together.

The local command below is safe planning evidence only; it neither downloads a
tool nor compiles an artifact:

```powershell
python scripts/build_desktop_artifacts.py --dry-run --allow-untagged
```

An actual build requires an exact tag, a matching platform lock, and a build
Python whose PySide6 and Nuitka versions exactly match
`requirements/desktop-build.txt`.

## Portable install, upgrade, uninstall, and rollback

The alpha artifact is a **portable** application, not an installer.

1. Obtain the archive from the published GitHub Release and run the following
   before extracting it:

   ```sh
   gh attestation verify <archive> \
     --repo MarcoLadeira/OPai \
     --signer-workflow MarcoLadeira/OPai/.github/workflows/desktop-artifacts.yml \
     --source-ref refs/heads/main \
     --deny-self-hosted-runners
   ```

   Then inspect `provenance.json` and `signing-status.json`, and use the
   operating system's signature/publisher view. An in-bundle checksum catches
   accidental corruption; the external attestation and expected publisher
   identity provide the authenticity boundary.
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
disposable home and fixture project; uses a strict environment allowlist rather
than inheriting credentials, `PYTHONPATH`, `OPAI_HUB_ROOT`, local-model
overrides, or source-working-tree influence;
checks checksums and inventory; scans artifact/log text for secret-shaped
values, private URLs, and development overrides; runs the current platform
signature verifier for signed artifacts against the protected signer thumbprint
or Team ID; runs CLI help and Doctor; and starts a real QtWebEngine GUI window.
The GUI smoke waits for the local page and QWebChannel boot, closes it, records
the result outside the artifact, and checks for a new surviving
`QtWebEngineProcess` helper.

The following still require recorded human/platform evidence before a public
alpha claim: controlled macOS lock generation and installation validation,
clean Windows and macOS artifact launches, protected-environment configuration,
provider setup and missing-provider recovery, upgrade/uninstall rehearsal,
rollback rehearsal, accessibility keyboard journey, live signing/notarization
verification, and public archive-attestation verification.
