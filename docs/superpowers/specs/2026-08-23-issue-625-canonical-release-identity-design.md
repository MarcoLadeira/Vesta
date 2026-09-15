# Issue #625 Canonical Release Identity and Bootstrap Design

## Outcome

Vesta has one human-edited application version, deterministic build and artifact
identity, and a dependency-light startup preflight that reports the real missing
requirement before CLI, GUI, provider, or runtime initialization. Generated
metadata and validation gates make drift a build or CI failure instead of a
support-time surprise.

## Scope and concurrency boundary

This change owns release/version metadata, packaging projections, bootstrap and
preflight diagnostics, CLI/GUI identity presentation, release evidence, and the
tests and documentation for those paths.

It does not redesign #613's journal or schema migration implementation, and it
does not change #616's operation identity, reconciliation, provider side-effect,
cost-ledger, or cancellation semantics. Where runtime schema compatibility is
needed, the preflight consumes the narrow public compatibility boundary already
present in the repository.

The branch starts from `origin/main` commit
`792970d9e92e32defb0ccf9fa648ff8cb0d03220`. At that point #616 is isolated in
draft PR #722 and changes only its planning document; recent #613 stages are
already on main.

## Current state discovered

- `pyproject.toml` declares application version `0.2.1a1`.
- `vesta.__version__` and `vestahub.__version__` manually repeat `0.2.1a1`;
  `vesta.__release_stage__` separately repeats `alpha.1`.
- `[tool.vesta].release`, CLI parser description, README prose, and
  `docs/INSTALL_PROOF.md` contain older `0.2.0` identities.
- CLI version, doctor, support bundle, and GUI About currently project the
  manually maintained `vesta` module constants.
- The desktop/update pipeline already models semantic version, release channel,
  exact 40-character candidate SHA, platform, architecture, install type,
  package/publisher identity, updater protocol, and native Windows/macOS
  metadata separately. It also embeds `release-identity.json` in packaged
  layouts and rejects artifact/release identity mismatches.
- The web GUI computes an asset content hash for display, but startup does not
  yet bind that asset set to the embedded application/build identity and fail
  closed on a missing or mismatched set.
- Installation detection distinguishes native packages and source-looking
  paths, but there is no single explicit contract for installed wheel,
  packaged desktop, editable install, supported source invocation, and
  unsupported raw source invocation.
- PyYAML errors have focused registry tests, while CLI/GUI entrypoints still
  need a common pre-deep-import classification boundary so dependency failure
  cannot enter a parser/configuration fallback.

## Version and metadata inventory

| Surface | Current role | Target classification |
| --- | --- | --- |
| `pyproject.toml [project].version` | Distribution version | Canonical human-edited application version |
| `pyproject.toml [tool.vesta].release` | Older display release | Remove as a duplicate source |
| `vesta/__init__.py` version/stage | CLI/GUI/doctor projection | Runtime projection from canonical identity |
| `vestahub/__init__.py` version | Duplicate distribution version | Runtime projection from canonical identity |
| `opcoding/__init__.py` version | Legacy component value | Classify explicitly; do not silently treat as app version |
| `importlib.metadata.version("vesta")` | Installed distribution metadata | Generated packaging projection and validation input |
| `vesta/update/models.py` schema/protocol constants | Compatibility contracts | Independently versioned compatibility metadata |
| lifecycle/provider/receipt/support schemas | Data/protocol contracts | Independently versioned compatibility metadata |
| `release-identity.json` | Packaged runtime identity | Generated immutable build projection |
| MSIX manifest and macOS `Info.plist` | Native package metadata | Generated artifact projection |
| update feeds/inventory/checksums/provenance | Release evidence | Generated artifact/release projection |
| CLI version/doctor/support bundle/receipts | User and support output | Runtime projection |
| GUI About/window metadata | User-visible identity | Runtime projection |
| README/install/release docs | Mixed current and historical text | Generated or structurally validated documentation |
| tests and web fixtures | Scenario inputs | Explicit test fixtures, never canonical sources |

Historical changelog and archived release-note values remain historical. Current
install/version claims must be generated or validated. Test versions such as
`0.3.0` remain fixtures when they represent an update candidate rather than the
running Vesta application.

## Canonical identity architecture

1. Keep `[project].version` in `pyproject.toml` as the only human-edited Vesta
   application version. It is already the build backend's package-metadata
   input and therefore avoids a second packaging ecosystem.
2. Add a dependency-light release-identity module that exposes a typed identity
   with application version, channel, display name/tag, immutable build ID,
   platform/architecture/install type, and separately named compatibility
   versions.
3. Packaged applications load only their embedded identity and asset manifest.
   They never inspect Git or a neighbouring checkout. Installed wheels use
   distribution metadata plus packaged generated build metadata. Editable and
   supported source installs may use a verified checkout fallback; when Git or
   build SHA is unavailable they report an honest development/unknown build
   identity rather than borrowing ambient metadata.
4. Build and release commands inject the exact candidate SHA and generate all
   artifact projections. Release preflight proves the candidate SHA, package
   version, native metadata, asset manifest, artifact filename, qualification
   evidence, and final inventory agree.
5. A structural drift validator understands the declared projections and
   reports expected value, actual value, surface/path, and remediation. CI
   includes a mutation fixture proving disagreement fails.

## Bootstrap architecture

A stdlib-only preflight runs from every supported entrypoint before importing
the deep CLI, GUI bridge, provider, configuration, or state-mutation graph. It
classifies startup mode and validates only the prerequisites for the requested
surface.

Stable categories are:

- `missing_dependency`
- `missing_packaged_asset`
- `unsupported_startup_mode`
- `package_metadata_unavailable`
- `package_integrity_failure`
- `incompatible_schema`
- `provider_dependency_missing`
- `malformed_user_configuration`

Diagnostics contain a stable category, missing component, safe summary, and
exact remediation. They reuse the repository's existing redaction and failure
envelope at the boundary without importing the runtime before preflight. CLI
prints compact actionable text by default and structured JSON when requested;
the windowed launcher displays the same safe envelope without a raw traceback.

Packaged desktop startup additionally validates that required web assets exist,
their content manifest matches, and their application version/build ID matches
the embedded runtime identity. It never falls back to source-tree assets or a
classic GUI after a package-integrity failure. A deliberate supported fallback
for a source/developer GUI remains a separate startup-mode decision.

Schema compatibility is checked through the narrow public compatibility API
before any state-mutating runtime initialization. #625 adds the contract and
ordering test, not a new journal or migration implementation.

## Verification strategy

Implementation proceeds in green slices with a failing test observed before
each behavior change:

1. Canonical identity lookup and channel/build fallbacks across wheel, editable,
   source archive, tagged/untagged development, and packaged-near-checkout modes.
2. Structural drift validator, including deliberately mutated generated and
   documentation projections with actionable errors.
3. CLI, GUI, doctor, support bundle, receipt, and qualification projections.
4. Generated web-asset identity plus missing/corrupt/mismatched packaged asset
   failures.
5. Startup-mode and dependency preflight for PyYAML, Qt, QtWebEngine, provider
   executable, package metadata, and malformed configuration separation.
6. Pre-mutation schema compatibility ordering.
7. Candidate-SHA propagation through desktop artifacts, Windows/macOS native
   metadata, update feeds, manifests, provenance, checksums, and release proof.
8. Current documentation/install proof validation and relevant repository gates.

The full repository gate runs only when the branch is merge-ready; development
uses focused identity, bootstrap, CLI/GUI, packaging, update, release-preflight,
and documentation tests plus lint/static checks on touched files.

## Acceptance mapping plan

The pull request description is the live execution record. Each acceptance
criterion will remain unchecked until it links to concrete implementation and
test evidence. `Refs #625` changes to `Closes #625` only after the complete
diff, latest main, required checks, and current issue body are re-verified.
