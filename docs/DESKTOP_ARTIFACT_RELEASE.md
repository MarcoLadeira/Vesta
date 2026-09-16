# Vesta Desktop Artifact Release Runbook

The Vesta alpha is fully free. Desktop artifacts must never add a payment,
license, entitlement, activation, account, or telemetry requirement.

## Production trust boundary

Pushing an annotated `v*` tag automatically runs an unsigned rehearsal without
credentials. Before either platform build starts, same-run jobs qualify the
exact tag's Python, hostile-environment, supply-chain, and web contracts. A
production dispatch from `main` additionally proves the tag commit is reachable
from that reviewed main revision and runs the protected provider canary. The
signing job depends on all source, web, native-build, and provider jobs, then
pauses at `vesta-production-signing` before it can read any certificate or
notarisation value. It signs, verifies, finalizes, and archives the bundle, but
never executes it.

Before production use, a repository owner must configure that environment with:

1. The `VESTA_WINDOWS_PFX_BASE64`, `VESTA_WINDOWS_PFX_PASSWORD`,
   `VESTA_APPLE_DEVELOPER_ID`, `VESTA_APPLE_SIGNING_CERTIFICATE_BASE64`,
   `VESTA_APPLE_SIGNING_CERTIFICATE_PASSWORD`, `VESTA_APPLE_NOTARY_APPLE_ID`,
   `VESTA_APPLE_NOTARY_TEAM_ID`, and `VESTA_APPLE_NOTARY_APP_SPECIFIC_PASSWORD`
   values as **environment secrets only**. Remove same-named repository or
   organisation secrets so an unprotected job cannot read them. The workflow
   creates a unique, temporary notarization profile in a temporary keychain; it
   never assumes a pre-existing profile on a hosted runner.
2. The non-secret, environment-scoped variables
   `VESTA_WINDOWS_SIGNER_THUMBPRINT` (the exact 40-hex Authenticode signer
   thumbprint) and `VESTA_MACOS_TEAM_ID` (the exact ten-character Developer ID
   Team ID). The macOS notarization team must match the protected Team ID.
3. A required release reviewer, with self-review prevention enabled, and no
   administrator bypass for the signing environment.
4. A deployment policy restricted to `main`. The reviewer must compare the
   requested annotated tag object, its peeled commit, and the recorded
   provenance before approving the job.
5. A repository tag ruleset matching `v*` that restricts tag creation to the
   release role and prevents update or deletion. The workflow re-reads the
   remote annotated-tag object immediately before attestation, but a protected
   immutable tag is still the long-lived release-name boundary.

Configure the separate `vesta-provider-canary` environment with main-only
deployment policy, a required reviewer, non-production provider credentials,
`VESTA_PROVIDER_CANARY_PROVIDERS`, `VESTA_PROVIDER_CANARY_MODELS`, and
`VESTA_PROVIDER_CANARY_MAX_USD` (greater than zero and no more than `1.00`). The
runner accepts only one fixed remote call whose exact provider/model and
provider-observed usage are bound to a known cost in the sandbox ledger; its
cumulative observed cost must remain within that threshold. Missing,
estimated, local, cached, or fallback evidence fails closed. Because this is a
post-call qualification check, the non-production provider accounts must also
enforce hard provider-side spend limits. These values are never exposed to tag
builds or pull requests.

The workflow never injects signing variables into the unsigned build or smoke
job. The build-to-sign handoff is a tar transport rather than a raw Actions
directory artifact, so POSIX execute modes and symlinks survive the handoff. It
deletes the Windows PFX and the macOS temporary keychain/certificate before the
signing step ends.

The unsigned rehearsal likewise creates its platform ZIP before smoke, safely
clean-extracts that exact ZIP into a new runner-temporary directory, rechecks
its candidate/tag/platform provenance, and executes only the extracted copy.
The uploaded rehearsal ZIP is therefore the same byte sequence whose contents
were exercised.

A separate credential-free smoke job downloads only the already archived signed
ZIP, safely extracts that exact archive into an empty runner-temporary directory,
and runs it with a strict runtime allowlist. On macOS the extraction uses
`ditto` after archive-path validation so executable modes, symlinks, and stapled
metadata survive. The job has no environment secrets, OIDC token, or attestation
permission. It verifies the exact publisher using the non-secret identity record
written after protected signing, rather than reading protected environment
variables.

The resulting smoke report binds the ZIP SHA-256, canonical application
version/channel/tag, candidate commit, asset fingerprint, compatibility
coordinates, annotated-tag object SHA, platform/architecture/install type,
workflow run ID, signed-artifact ID/name/producer attempt, and smoke attempt. A
third job downloads the signed ZIP and smoke report by their
immutable artifact IDs, independently recalculates and validates that binding,
revalidates the current remote tag object, stages uniquely named immutable
evidence, revalidates the tag again immediately after staging, and only then
creates the OIDC attestation. The staging artifact is
explicitly non-publishable unless the entire attestation job is green.
Stage-before-attest prevents a failed staging upload from causing a duplicate
attestation on a later failed-only rerun without letting a moved tag create a
final-looking artifact.

Artifact names retain `run_attempt` to remain immutable. Consumers do not guess
the current attempt's producer name: they query this workflow run, select the
newest non-expired matching producer attempt no newer than the consumer, and
download its exact artifact ID. Consequently, **Re-run failed jobs** can safely
reuse a successful earlier build/sign/smoke output, while a full rerun consumes
the newer producer. Every third-party Action is pinned to a reviewed full commit
SHA.

Before a protected signing step can read credentials, the workflow requires a
safe annotated package-version tag whose canonical value is the package's PEP
440 version with a `v` prefix (`<package-version>` maps to
`v<package-version>`), whose tag object
directly targets the recorded commit, and whose commit is reachable from
protected `main`.

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
gh attestation verify Vesta-<tag>-<platform>-production.zip \
  --repo MarcoLadeira/Vesta \
  --signer-workflow MarcoLadeira/Vesta/.github/workflows/desktop-artifacts.yml \
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
then installs Vesta with `--no-deps`, so the checked-in lock remains the only
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
checked out an exact annotated safe package-version tag matching the package
version (the canonical application version requires the matching `v`-prefixed
tag) and reachable from
protected `main`,
verified the expected Windows signer thumbprint or macOS Team ID, written
post-signing checksums, completed the credential-free native artifact smoke,
and attested the finished ZIP. A missing protected identity, certificate,
timestamp, notarization credential, failed notarization, absent verification
log, or failed archive attestation is a hard stop.

Windows production verifies every shipped `.exe`, `.dll`, and `.pyd`; macOS
production signs and verifies both `gui/Vesta.app` and `cli/vesta`, then submits
the portable bundle for notarization. A current native verifier rejects a
signature from an unexpected publisher; it does not merely accept any trusted
certificate. The GitHub artifact attestation binds the complete ZIP outside the
bundle and is the final public-release authenticity gate.

## Build rehearsal

1. Confirm the target platform's exact lock exists and has been reviewed. The
   current repository has a verified Windows lock; do not start a macOS release
   until the matching macOS lock has been generated and reviewed on macOS.
2. Create a new isolated Python 3.13 environment and install, in order, the
   bootstrap lock, that platform's full lock, and Vesta with `--no-deps` and
   `--no-build-isolation`. Do not use an ambient `pyside6-deploy` or `nuitka`
   executable.
3. Confirm `pyproject.toml`, `vesta/__init__.py`, and `vestahub/__init__.py` declare
   the same PEP 440 version, then push its canonical reviewed annotated
   `v<package-version>` tag. The `v*` push automatically
   runs the unsigned rehearsal and all credential-free source/web/native gates.
   For production only, dispatch **Vesta desktop artifact rehearsal** from
   `main`, enter that same tag, and select `production`. Manual unsigned dispatch
   remains available for diagnosis and requires the explicit
   `allow_unsigned_prealpha` acknowledgement.
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
     --repo MarcoLadeira/Vesta \
     --signer-workflow MarcoLadeira/Vesta/.github/workflows/desktop-artifacts.yml \
     --source-ref refs/heads/main \
     --deny-self-hosted-runners
   ```

   Then inspect `provenance.json` and `signing-status.json`, and use the
   operating system's signature/publisher view. An in-bundle checksum catches
   accidental corruption; the external attestation and expected publisher
   identity provide the authenticity boundary.
2. Extract the archive into a user-controlled application directory such as
   `C:\Apps\Vesta` or `/Applications/Vesta Alpha`. Start `Vesta` for the desktop
   UI or `vesta` for the command line; neither requires a source checkout.
3. For an upgrade, close Vesta, keep the previous extracted directory intact,
   extract the new portable bundle into a sibling directory, run the smoke
   journey, and then switch shortcuts to the new directory.
4. For uninstall, close Vesta and delete only the extracted application
   directory. Per-user Vesta state is outside the portable bundle and is not
   silently deleted; clear it separately only if the user explicitly requests
   that action.
5. For rollback, switch the shortcut back to the retained previous directory.
   Do not overwrite it before the new artifact has passed first launch, missing
   provider, read-only question, exit, and restart checks.

## Required smoke journey

The workflow runs `scripts/smoke_desktop_artifacts.py`, which starts from a
disposable home and fixture project; uses a strict environment allowlist rather
than inheriting credentials, `PYTHONPATH`, `VESTA_HUB_ROOT`, local-model
overrides, or source-working-tree influence;
checks checksums and inventory; scans artifact/log text for secret-shaped
values, private URLs, and development overrides; runs the current platform
signature verifier for signed artifacts against the protected signer thumbprint
or Team ID; runs CLI help and Doctor; and starts a real QtWebEngine GUI window.
The GUI smoke waits for the local page and QWebChannel boot, closes it, records
the result outside the artifact, and checks for a new surviving
`QtWebEngineProcess` helper.

As audited on 2026-08-09, this private repository has no protected environments
or signing/provider values configured, the macOS build lock is absent, GitHub
Actions jobs are blocked before startup by account billing/spend limits, and
GitHub's artifact-attestation action requires GitHub Enterprise Cloud for
private/internal repositories. Artifact attestation is therefore an external
infrastructure blocker on the current plan, not repository-side success.
The following therefore still require recorded human/platform evidence before a
public alpha claim: restored Actions billing, protected-environment
configuration, controlled macOS lock generation and installation validation,
clean Windows and macOS artifact launches,
provider setup and missing-provider recovery, upgrade/uninstall rehearsal,
rollback rehearsal, accessibility keyboard journey, live signing/notarization
verification, and public archive-attestation verification.
