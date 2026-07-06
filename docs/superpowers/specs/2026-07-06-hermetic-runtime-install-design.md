# Hermetic Runtime Installation Design

## Scope

Close #136 and #150 as one release-reliability slice. A built OPai wheel must install with its declared runtime dependencies and run the required dependency-light commands from a clean home. The Python suites must produce the same result on clean and provider-configured machines, without consulting real credentials.

## Runtime dependency boundary

PyYAML is a required core dependency because registry loading is core CLI behavior. `load_registry` will parse YAML exactly once and raise an actionable, path-aware `RegistryLoadError` for a missing parser, malformed YAML, or invalid UTF-8. Optional desktop dependencies remain extras because `opai gui --once` is intentionally headless.

The isolated-install runner will build OPai and dependency wheels into one wheelhouse, install them with `--no-index` into a fresh virtual environment, and execute `opai --help`, `opai doctor`, `opaihub validate`, and `opai gui --once` outside the source tree under a disposable home. It will continue checking packaged web assets.

## Hermetic test boundary

Pytest receives an autouse fixture that removes provider credential variables and disables the live OS keyring before every test; individual tests may still inject explicit environment mappings or backends. A deliberately populated fake keyring fixture and hostile provider variables exercise the full pytest and unittest commands in CI. Production helpers whose names begin with `test_` remain module-qualified or aliased so pytest cannot collect them.

## CI and documentation

A clean-wheel job runs the isolated artifact smoke on Windows, Linux, and macOS. A hostile-environment job runs both canonical Python harnesses with fake provider credentials and a populated fake keyring. Normal installation documentation installs declared dependencies; `--no-deps` is reserved for deliberate maintainer-only checks.

## Verification

Tests cover required metadata, missing/malformed/non-UTF-8 registries, isolated child environments, exact smoke commands, hostile keyring/environment behavior, and workflow matrices. The final gate includes full unittest, full pytest under hostile state, JavaScript units, Chromium E2E, Ruff, Bandit, dependency/secret audits, registry validation, and a real isolated wheel install.
