# Release process

Vesta uses a **reproducible, inspectable release-candidate preflight** to prove
which source revision was tested before any artifact is published. The local
command produces a scope-aware planning or source verdict. Final release
qualification is produced only by the protected workflow that builds and
authenticates the Windows and macOS artifacts in the same run. A dry-run path
performs every safe step with no external side effects, and rollback restores
the previous tested release without touching user state.

Implementation: [`opaihub/release_preflight.py`](../opaihub/release_preflight.py).
Issue: [#32](https://github.com/MarcoLadeira/OPai/issues/32).

## Planning, source qualification, and final qualification

```bash
opai release preflight
```

The default is a local planning report: tests and final artifacts may be shown as
`SKIP` without claiming release qualification. Release/RC branches use a strict,
credential-free **source** scope:

```bash
opai release preflight \
  --qualification-required \
  --source-only \
  --candidate-sha <exact-40-character-commit> \
  --run-tests
```

This runs `scripts/ci_local.py --profile full` for that exact SHA and preserves
its canonical `verdict`, `reason`, and `classification`. A successful report has
`qualification_scope: "source"`, `artifact_qualification: "pending"`, and
`final_release_ready: false`. It proves source readiness only.

Final Windows/macOS release qualification belongs to the protected, same-run
[`desktop-artifacts.yml`](../.github/workflows/desktop-artifacts.yml) workflow.
That workflow owns native signing/notarisation, provider evidence, credential-free
archive smoke, and GitHub attestation. A standalone preflight cannot authenticate
those platform-native results and therefore never promotes structural JSON alone.

Checks:

| Check | Blocks on |
| --- | --- |
| Working tree is a clean git checkout | any uncommitted path |
| Version is declared consistently | mismatch across `pyproject.toml`, `opai/__init__.py`, `opaihub/__init__.py`, or a PEP 440 / release-stage disagreement |
| Changelog documents this release | the top `CHANGELOG.md` entry is missing, wrong, or empty for the current version |
| A license is present | `LICENSE` missing or trivially short |
| Required documentation is present | any of `README.md`, `CHANGELOG.md`, `LICENSE`, `CONTRIBUTING.md` missing/empty |
| Source release tag is available | `v<version>` already exists, or strict mode cannot determine tag state |
| Candidate identity matches | candidate is absent/invalid, `HEAD` cannot be resolved, or the SHA differs |
| The full test gate passes | strict mode omits `--run-tests`, or the exact-SHA `full` profile is not `qualified` |
| Final artifacts have authenticated same-run evidence | final scope lacks either Windows/macOS artifact, native/provider evidence, bounded verification logs, an attestation report, exact run binding, or an authenticated native/cryptographic verifier |

Common flags:

```bash
opai release preflight --run-tests                     # include the full local gate
opai release preflight --source-only                   # source evidence; artifacts pending
opai release preflight --candidate-sha <sha>            # bind evidence to HEAD
opai release preflight --qualification-required        # make SKIP blocking
opai release preflight --artifacts dist/manifest.json  # structural/offline audit; not final trust
opai release preflight --format json --out preflight.json  # archive sanitized evidence
```

The final artifact inventory uses schema 3. It is generated and consumed inside
the protected desktop workflow; this abbreviated example shows its binding
contract:

```json
{
  "schema_version": 3,
  "repository": "MarcoLadeira/OPai",
  "workflow": ".github/workflows/desktop-artifacts.yml",
  "run_id": "123456789",
  "run_attempt": "1",
  "tag": "<canonical-published-tag>",
  "candidate_sha": "<40-hex-commit>",
  "commit_sha": "<same-40-hex-commit>",
  "release_identity": {
    "application_version": "<canonical-application-version>",
    "build_id": "<same-40-hex-commit>",
    "published_tag": "<canonical-published-tag>",
    "release_channel": "<canonical-release-channel>",
    "release_stage": "<canonical-release-stage>",
    "platform": "linux",
    "architecture": "x86_64",
    "install_type": "qualification_source"
  },
  "provider_evidence": {
    "path": "provider-qualification.json",
    "sha256": "<64-hex-evidence-digest>"
  },
  "native_evidence": [
    {
      "platform": "windows-latest",
      "path": "native-windows.json",
      "sha256": "<64-hex-evidence-digest>"
    },
    {
      "platform": "macos-latest",
      "path": "native-macos.json",
      "sha256": "<64-hex-evidence-digest>"
    }
  ],
  "artifacts": [
    {
      "path": "OPai-windows.zip",
      "sha256": "<64-hex-artifact-digest>",
      "platform": "windows-latest",
      "verification_log": {
        "path": "verification-windows.log",
        "sha256": "<64-hex-log-digest>"
      },
      "attestation_report": {
        "path": "attestation-windows.json",
        "sha256": "<64-hex-report-digest>"
      }
    },
    {
      "path": "OPai-macos.zip",
      "sha256": "<64-hex-artifact-digest>",
      "platform": "macos-latest",
      "verification_log": {
        "path": "verification-macos.log",
        "sha256": "<64-hex-log-digest>"
      },
      "attestation_report": {
        "path": "attestation-macos.json",
        "sha256": "<64-hex-report-digest>"
      }
    }
  ]
}
```

Every referenced file is bounded, remains under the manifest directory, and is
verified against its recorded digest. The provider, native, and attestation JSON
files repeat the repository, workflow, run, attempt, tag, candidate, platform,
artifact, and linked-evidence bindings that apply to them.

Those structural bindings are necessary, but they are not a trust root. A bare
`"signed": true` flag, self-authored `"verified": true` fields, or a
well-shaped candidate-authored report is rejected. Without an authenticated
native/cryptographic verifier supplied by the protected workflow, standalone
final qualification returns
`infrastructure_blocked` / `artifact_verifier_unavailable` and directs the
operator to the same-run desktop workflow.

`--out` writes a **sanitized** evidence file (verdict + per-check status, with no
absolute paths or uncommitted-file names) suitable for CI archiving.

## Dry-run is the default; publishing is disabled

Preflight never tags, uploads, publishes, or notifies. To prove a dry-run has no
external side effects:

```bash
opai release dry-run-proof
```

This shows every publish step is disabled and refuses to execute, and that
network access is blocked (`opaihub.release_preflight.deny_network`). Real
publishing runs only on the release host with signed artifacts and release
credentials — `execute_publish_step` refuses otherwise, so a misconfiguration can
never silently ship.

## Rollback

Plan a rollback to the previous tested release (read-only by default):

```bash
opai release rollback --previous-manifest previous/manifest.json --to "<previous-version>"
```

Execute it against an installed release, protecting user state:

```bash
opai release rollback --execute \
  --previous-manifest previous/manifest.json --to "<previous-version>" \
  --release-root /opt/opai --pointer /opt/opai/active.json \
  --protect /opt/opai/.opaihub
```

Rollback restores the previous artifacts and repoints the active release. It
**never** reads or writes any `--protect` directory, so the local ledger,
preferences, and credentials survive a rollback byte-for-byte; a manifest that
tries to write into protected state is refused.

## CI evidence

`.github/workflows/release-preflight.yml` runs strict source preflight for every
`release/**`/`rc/**` push and on demand. It passes the exact `github.sha`, runs
the full test profile, preserves the typed local verdict, and uploads a unique
run/attempt/SHA evidence artifact. It has no tag trigger and does not consume a
candidate artifact manifest, so it cannot claim final artifact qualification.
Tagged and production final qualification remain in the protected same-run
desktop workflow.
