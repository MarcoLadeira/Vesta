# Release process

OPai ships from a **reproducible, inspectable release-candidate preflight** so we
can prove exactly what would ship before any artifact is published. One command
produces a deterministic readiness verdict; a dry-run path performs every safe
step with no external side effects; and a rollback restores the previous tested
release without touching user state.

Implementation: [`opaihub/release_preflight.py`](../opaihub/release_preflight.py).
Issue: [#32](https://github.com/MarcoLadeira/OPai/issues/32).

## One command: readiness

```bash
opai release preflight
```

Runs every release check and prints a human-readable report (`--format json` for
machine-readable). **Exit code is 0 only when the release is ready**; any blocker
exits non-zero — the same green/red contract as CI.

Checks:

| Check | Blocks on |
| --- | --- |
| Working tree is a clean git checkout | any uncommitted path |
| Version is declared consistently | mismatch across `pyproject.toml`, `opai/__init__.py`, `opaihub/__init__.py`, or a PEP 440 / release-stage disagreement |
| Changelog documents this release | the top `CHANGELOG.md` entry is missing, wrong, or empty for the current version |
| A license is present | `LICENSE` missing or trivially short |
| Required documentation is present | any of `README.md`, `CHANGELOG.md`, `LICENSE`, `CONTRIBUTING.md` missing/empty |
| Release tag does not already exist | `v<version>` is already tagged (bump first) |
| The test gate passes | `--run-tests` given and `scripts/ci_local.py --fast` fails |
| Artifacts exist, match checksums, are signed | `--artifacts <manifest>` given and any file is missing, checksum-mismatched, or unsigned |

Common flags:

```bash
opai release preflight --run-tests                 # include the local gate
opai release preflight --artifacts dist/manifest.json   # verify built artifacts
opai release preflight --format json --out preflight.json  # archive sanitized evidence
```

The artifact manifest is JSON:

```json
{ "artifacts": [ { "path": "OPai-Setup.exe", "sha256": "<hex>", "signed": true } ] }
```

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
opai release rollback --previous-manifest previous/manifest.json --to 0.2.0a1
```

Execute it against an installed release, protecting user state:

```bash
opai release rollback --execute \
  --previous-manifest previous/manifest.json --to 0.2.0a1 \
  --release-root /opt/opai --pointer /opt/opai/active.json \
  --protect /opt/opai/.opaihub
```

Rollback restores the previous artifacts and repoints the active release. It
**never** reads or writes any `--protect` directory, so the local ledger,
preferences, and credentials survive a rollback byte-for-byte; a manifest that
tries to write into protected state is refused.

## CI evidence

`.github/workflows/release-preflight.yml` runs the dry-run preflight on release
branches and on demand, then uploads the sanitized preflight evidence and a
rollback plan as build artifacts, so every candidate has an auditable record.
