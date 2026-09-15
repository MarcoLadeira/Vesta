# Design: Production Installers, CI Smoke Tests, and Paid-Era Language Audit

## Scope

Solve four GitHub issues in the Vesta repository:

- **#353** — Fix and test Windows installer (`install.ps1`) for production use
- **#354** — Fix and test macOS/Linux installer (`install.sh`) for production use
- **#355** — Add cross-platform install smoke test CI job
- **#358** — Audit repo for paid-era language remnants and fix contradictions

## Approach

Grouped implementation in three workstreams:

- **Group A (installers):** #353 + #354 together
- **Group B (CI):** #355, dependent on Group A
- **Group C (docs):** #358, independent

## Group A: Production Installers

### Goals

- Turn `install.ps1` and `install.sh` from dev-clone scripts into production installers.
- Prefer PyPI installation; fall back to source install from GitHub.
- Isolate the install with `pipx` or `uv tool` when available.
- Verify the install works before exiting successfully.

### Behavior

1. Detect Python 3.10+.
2. Detect or install `pipx` (preferred) or `uv`.
3. Attempt PyPI install: `pipx install vesta` (or `uv tool install vesta`).
4. If PyPI is unavailable or the package is not yet published, fall back to:
   - Clone or update `https://github.com/MarcoLadeira/Vesta.git` into `~/.vesta/source`
   - `pip install -e .` inside that clone
5. Add the install bin directory to the user PATH if needed.
6. Run verification: `vesta --version` and `vesta doctor`.
7. Exit 0 on success, non-zero with a clear error on failure.

### Flags

- `--check` — verify an existing install without installing
- `--source-only` — skip PyPI and install from source
- `--help` — usage

### Error handling

- Missing Python → clear message + install hint, exit 1
- Missing pipx/uv → attempt install, or fall back to `pip install --user`, exit 2 if all fail
- PyPI install fails → log warning, fall back to source
- Verification fails → show output, exit 3

## Group B: CI Smoke Test Job

### Goals

- Add a GitHub Actions job that proves the installers work on Windows, macOS, and Linux.

### Implementation

- Extend `.github/workflows/ci.yml` with a new `install-smoke` job.
- Matrix: `os: [windows-latest, macos-latest, ubuntu-latest]`.
- Steps:
  1. Checkout
  2. Set up Python 3.13
  3. Run platform installer (`install.ps1` on Windows, `install.sh` elsewhere)
  4. Run `python scripts/smoke-install.py`
  5. Upload install logs on failure
- The job should be manual/dispatch-only to match existing CI policy.

## Group C: Paid-Era Language Audit

### Goals

- Remove or clearly archive any docs that contradict the current Free Public Alpha strategy.

### Targets

- `docs/GO_TO_MARKET_30_DAY_PLAN.md`
- `docs/BUSINESS_STRATEGY.md`
- `docs/EFFECTIVENESS_AND_SECURITY_AUDIT_2026_06_21.md`
- `CHANGELOG.md`
- Any other active docs mentioning paid tiers, pricing, or licenses as current policy

### Changes

- Add a prominent `> ARCHIVED — NOT CURRENT POLICY` header to legacy strategy docs.
- Update `CHANGELOG.md` to clarify that the current release is Free Public Alpha.
- Add a lightweight grep-based check (script or CI step) for banned terms in active docs.

## Testing Strategy

- **Local:** `python scripts/ci_local.py --full` must pass.
- **Installers:** Run `install.ps1 --check` on Windows and `install.sh --check` on Linux/macOS in CI.
- **CI:** The new `install-smoke` job must pass on all 3 OS.
- **Docs:** Grep for paid-tier terms returns no hits in active docs.

## Out of Scope

- Actually publishing to PyPI (requires credentials)
- Actually creating GitHub Releases (requires release credentials)
- Desktop artifact signing
