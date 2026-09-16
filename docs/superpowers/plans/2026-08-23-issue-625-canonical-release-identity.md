# Issue #625 Canonical Release Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every Vesta runtime, artifact, evidence, and current documentation surface one canonical application/build identity and fail every incomplete or unsupported startup before deep initialization with a stable actionable diagnostic.

**Architecture:** `pyproject.toml [project].version` remains the only human-edited application version. A generated Python projection and embedded build/artifact JSON supply dependency-light runtime identity, while a structural validator and build hooks reject drift; a stdlib-only bootstrap module classifies startup mode and prerequisites before importing the CLI/GUI/runtime graph. Existing updater, desktop artifact, release-preflight, failure-redaction, and schema contracts are extended through narrow adapters rather than replaced.

**Tech Stack:** Python 3.10+, setuptools, importlib metadata/resources, pytest/unittest, PySide6/QtWebEngine optional desktop runtime, existing Vesta release/update modules, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-23-issue-625-canonical-release-identity-design.md`

## Global Constraints

- Do not modify #613 journal persistence, replay, or migration internals.
- Do not modify #616 operation identity, reconciliation, provider side effects, cost ledger, or cancellation.
- Keep `pyproject.toml [project].version` as the only human-edited application version.
- Keep application version, release channel, immutable build ID, artifact identity, and schema/protocol compatibility versions separate.
- Packaged processes may use only embedded metadata/resources and must never inspect neighbouring Git/source trees.
- Every production behavior change follows a witnessed failing test, minimal implementation, and focused green verification.
- Use existing packaging, updater, desktop artifact, release preflight, diagnostics/redaction, and CI architecture.
- Use `Refs #625` until every acceptance criterion is verified; only then change it to `Closes #625`.

---

### Task 1: Canonical application identity and generated projection

**Files:**
- Create: `vesta/release_identity.py`
- Create: `vesta/_generated_release.py`
- Create: `scripts/generate_release_identity.py`
- Create: `tests/test_release_identity.py`
- Modify: `vesta/__init__.py`
- Modify: `vestahub/__init__.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `[project].version` from `pyproject.toml`; optional embedded `release-identity.json`; `importlib.metadata.version("vesta")`.
- Produces: immutable `ReleaseIdentity`, `current_release_identity()`, `identity_payload()`, `APPLICATION_VERSION`, `RELEASE_CHANNEL`, `DISPLAY_NAME`, and `PUBLISHED_TAG`.

- [ ] **Step 1: Write failing canonical identity tests**

```python
def test_generated_projection_matches_the_only_editable_version():
    identity = load_release_identity(source_root=ROOT, distribution_version=None)
    assert identity.application_version == "0.2.1a1"
    assert identity.release_channel == "alpha"

def test_packaged_identity_ignores_neighbouring_checkout(tmp_path):
    embedded = write_identity(tmp_path, version="0.2.1a1", build_id="a" * 40)
    neighbour = write_checkout(tmp_path / "checkout", version="9.9.9")
    identity = load_release_identity(identity_paths=[embedded], source_root=neighbour)
    assert identity.application_version == "0.2.1a1"
    assert identity.build_id == "a" * 40
```

- [ ] **Step 2: Verify the new tests fail for the missing API**

Run: `python -m pytest tests/test_release_identity.py -q`

Expected: collection/import failure because `vesta.release_identity` does not exist.

- [ ] **Step 3: Implement the generated projection and dependency-light loader**

```python
@dataclass(frozen=True)
class ReleaseIdentity:
    application_version: str
    release_channel: str
    build_id: str
    display_name: str
    published_tag: str
    platform: str
    architecture: str
    install_type: str
    metadata_source: str

def current_release_identity() -> ReleaseIdentity:
    return load_release_identity()
```

The loader validates embedded schema/version/build data, verifies installed
distribution metadata against the generated projection, and uses the generated
development projection only for a verified source/editable mode. Replace both
manually maintained package versions with imports from the generated projection
and remove `[tool.vesta].release`.

- [ ] **Step 4: Generate and verify the projection**

Run: `python scripts/generate_release_identity.py --check`

Expected: PASS with `0.2.1a1`, `alpha`, display `Vesta 0.2.1 Alpha.1`, and tag `v0.2.1a1` matching the checked-in generated module.

- [ ] **Step 5: Run focused identity/package tests**

Run: `python -m pytest tests/test_release_identity.py tests/test_packaging.py -q`

Expected: PASS.

- [ ] **Step 6: Commit and push the green slice**

```text
feat(release): establish canonical application identity
```

### Task 2: Structural drift gate and CI integration

**Files:**
- Create: `vesta/release_validation.py`
- Create: `scripts/check_release_identity.py`
- Create: `tests/test_release_identity_drift.py`
- Modify: `scripts/ci_local.py`
- Modify: `tests/test_ci_local.py`
- Modify: `tests/test_ci_architecture.py`
- Modify: `scripts/desktop_release_transport.py`
- Modify: `vestahub/release_preflight.py`
- Modify: `tests/test_release_transport_python_compat.py`
- Modify: `tests/test_release_preflight.py`

**Interfaces:**
- Consumes: canonical project version, generated projection, declared current documentation projections, and release tag.
- Produces: `validate_release_identity(root) -> tuple[IdentityDrift, ...]` and an actionable CLI exit code.

- [ ] **Step 1: Write failing mutation and release-preflight tests**

```python
def test_generated_projection_drift_names_expected_actual_path_and_fix(tmp_path):
    root = copy_release_fixture(tmp_path)
    mutate_generated_version(root, "9.9.9")
    drift = validate_release_identity(root)
    assert drift[0].expected == "0.2.1a1"
    assert drift[0].actual == "9.9.9"
    assert drift[0].path.name == "_generated_release.py"
    assert "generate_release_identity.py" in drift[0].remediation

def test_release_preflight_reads_one_version_source(tmp_path):
    root = release_root(tmp_path, version="0.2.1a1")
    assert check_version_consistency(ReleaseContext(root=root)).passed
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_release_identity_drift.py tests/test_release_preflight.py tests/test_release_transport_python_compat.py -q`

Expected: FAIL because the structural validator is missing and existing release
checks still require duplicate `__version__` literals.

- [ ] **Step 3: Implement structural projections and actionable diagnostics**

```python
@dataclass(frozen=True)
class IdentityDrift:
    surface: str
    path: Path
    expected: str
    actual: str
    remediation: str

def validate_release_identity(root: Path) -> tuple[IdentityDrift, ...]:
    expected = read_project_release(root / "pyproject.toml").application_version
    generated = read_generated_release(root / "vesta" / "_generated_release.py")
    if generated.application_version == expected:
        return ()
    return (
        IdentityDrift(
            surface="generated_runtime_projection",
            path=root / "vesta" / "_generated_release.py",
            expected=expected,
            actual=generated.application_version,
            remediation="Run: python scripts/generate_release_identity.py",
        ),
    )
```

Make release transport and preflight consume the canonical parser/validator,
not regexes for package `__version__` duplicates. Add a `release-identity-drift`
local-CI step after the existing generated lifecycle drift gate.

- [ ] **Step 4: Verify GREEN and the deliberate failure output**

Run: `python -m pytest tests/test_release_identity_drift.py tests/test_release_preflight.py tests/test_release_transport_python_compat.py tests/test_ci_local.py tests/test_ci_architecture.py -q`

Run: `python scripts/check_release_identity.py`

Expected: tests and live repository check PASS; the mutation fixture observes a
non-zero result containing expected value, conflicting value, surface/path, and
regeneration command.

- [ ] **Step 5: Commit and push the green slice**

```text
test(release): add canonical identity drift gate
```

### Task 3: Embedded wheel/source build identity

**Files:**
- Create: `setup.py`
- Create: `tests/test_release_build_metadata.py`
- Modify: `pyproject.toml`
- Modify: `vesta/release_identity.py`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: setuptools distribution version plus `VESTA_BUILD_ID` and optional `VESTA_RELEASE_CHANNEL` in the build environment.
- Produces: `_embedded_build.json` inside wheel/sdist build outputs without modifying the source checkout.

- [ ] **Step 1: Write failing wheel, sdist, editable, Git-unavailable, and corrupt-metadata tests**

```python
def test_wheel_embeds_exact_candidate_sha(tmp_path):
    wheel = build_wheel(tmp_path, env={"VESTA_BUILD_ID": "b" * 40})
    identity = json_from_wheel(wheel, "vesta/_embedded_build.json")
    assert identity == {"schema_version": 1, "build_id": "b" * 40}

def test_source_archive_without_build_sha_is_honest(tmp_path):
    identity = load_release_identity(source_root=archive_root(tmp_path), git=None)
    assert identity.build_id == "unknown"
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_release_build_metadata.py tests/test_packaging.py -q`

Expected: FAIL because no setuptools build hook embeds immutable build metadata.

- [ ] **Step 3: Implement build-only JSON generation**

```python
class CanonicalBuildPy(build_py):
    def run(self):
        super().run()
        write_embedded_build(Path(self.build_lib), self.distribution.get_version())

setup(cmdclass={"build_py": CanonicalBuildPy, "sdist": CanonicalSdist})
```

Reject malformed candidate SHAs, copy exact identity into wheel/sdist output,
and leave the checkout untouched. Runtime lookup must prefer explicit packaged
identity, then embedded wheel identity, then verified editable/source fallback.

- [ ] **Step 4: Verify GREEN across build/install modes**

Run: `python -m pytest tests/test_release_build_metadata.py tests/test_release_identity.py tests/test_packaging.py -q`

Expected: PASS for wheel, editable, source archive, tagged/untagged development,
Git unavailable, missing/corrupt dist-info, and packaged-near-checkout cases.

- [ ] **Step 5: Commit and push the green slice**

```text
feat(version): embed exact build identity in distributions
```

### Task 4: CLI, GUI, doctor, receipt, and support projections

**Files:**
- Modify: `vesta/cli.py`
- Modify: `vesta/app_state.py`
- Modify: `vesta/gui_view_model.py`
- Modify: `vesta/gui_web.py`
- Modify: `vesta/gui_desktop.py`
- Modify: `vestahub/receipt.py`
- Modify: `vestahub/support_bundle.py`
- Modify: `tests/test_positioning_and_cli.py`
- Modify: `tests/test_gui_web.py`
- Modify: `tests/test_desktop_gui.py`
- Modify: `tests/test_receipt.py`
- Modify: `tests/test_support_bundle.py`

**Interfaces:**
- Consumes: `identity_payload()` and independent schema/protocol constants.
- Produces: consistent human and JSON identity on `--version`, `version`, doctor, GUI About, receipt, and support bundle.

- [ ] **Step 1: Write failing surface-agreement tests**

```python
def test_machine_version_output_separates_application_build_and_protocol(capsys):
    assert main(["version", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["application_version"] == "0.2.1a1"
    assert payload["build_id"]
    assert payload["compatibility"]["updater_protocol_version"] == 1

def test_receipt_and_support_bundle_carry_the_same_release_identity(project):
    assert build_receipt(project)["release_identity"] == build_support_bundle(project)["release_identity"]
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_positioning_and_cli.py tests/test_gui_web.py tests/test_desktop_gui.py tests/test_receipt.py tests/test_support_bundle.py -q`

Expected: FAIL because current payloads expose only manually maintained
version/stage fields and receipts lack build identity.

- [ ] **Step 3: Project the canonical payload without changing business logic**

```python
payload = identity_payload(include_compatibility=True)
bundle["release_identity"] = payload
receipt["release_identity"] = payload
```

Keep backward-compatible top-level version fields only as derived aliases. Make
parser descriptions and GUI/window About text dynamic. Include adapter/provider
catalog and lifecycle/update compatibility values under explicit names.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_positioning_and_cli.py tests/test_gui_web.py tests/test_desktop_gui.py tests/test_receipt.py tests/test_support_bundle.py -q`

Expected: PASS.

- [ ] **Step 5: Commit and push the green slice**

```text
feat(cli): unify version diagnostics and evidence metadata
```

### Task 5: Dependency-light startup and configuration classification

**Files:**
- Create: `vesta/bootstrap.py`
- Create: `vesta/__main__.py`
- Create: `tests/test_bootstrap_preflight.py`
- Modify: `pyproject.toml`
- Modify: `scripts/desktop_cli_entry.py`
- Modify: `scripts/desktop_gui_entry.py`
- Modify: `vestahub/registry.py`
- Modify: `tests/test_runtime_dependencies.py`

**Interfaces:**
- Consumes: argv, startup mode, `importlib.util.find_spec`, embedded package metadata, optional provider executable, and configuration path.
- Produces: `BootstrapDiagnostic`, `BootstrapFailure`, `preflight()`, `cli_main()`, and `gui_main()` with stable categories/remediation.

- [ ] **Step 1: Write failing category and ordering tests**

```python
def test_missing_pyyaml_stops_before_deep_cli_import(monkeypatch):
    diagnostic = preflight(["doctor"], probes=missing("yaml"))
    assert diagnostic.category == "missing_dependency"
    assert diagnostic.component == "PyYAML"
    assert "development extra" in diagnostic.remediation

def test_malformed_config_is_not_a_dependency_error(tmp_path):
    with pytest.raises(BootstrapFailure) as caught:
        load_registry(tmp_path / "broken.yaml")
    assert caught.value.category == "malformed_user_configuration"
```

Cover missing PyYAML, package metadata, PySide6, QtWebEngine, provider executable,
unsupported raw source mode, malformed configuration, multiple PATH candidates,
and safe compact JSON/text rendering.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_bootstrap_preflight.py tests/test_runtime_dependencies.py -q`

Expected: FAIL because entrypoints import `vesta.cli` directly and registry
dependency/configuration errors do not share a typed startup contract.

- [ ] **Step 3: Implement the stdlib-only bootstrap boundary**

```python
class BootstrapCategory(str, Enum):
    MISSING_DEPENDENCY = "missing_dependency"
    MISSING_PACKAGED_ASSET = "missing_packaged_asset"
    UNSUPPORTED_STARTUP_MODE = "unsupported_startup_mode"
    PACKAGE_METADATA_UNAVAILABLE = "package_metadata_unavailable"
    INCOMPATIBLE_SCHEMA = "incompatible_schema"
    PROVIDER_DEPENDENCY_MISSING = "provider_dependency_missing"
    MALFORMED_USER_CONFIGURATION = "malformed_user_configuration"

def cli_main(argv=None):
    require_preflight(argv or sys.argv[1:])
    from vesta.cli import main
    return main(argv)
```

Point installed and frozen entrypoints at this module. Render bounded/redacted
diagnostics with no traceback by default. Convert registry dependency and parse
failures to the same typed contract while retaining existing actionable text.

- [ ] **Step 4: Verify GREEN plus subprocess startup reproductions**

Run: `python -m pytest tests/test_bootstrap_preflight.py tests/test_runtime_dependencies.py -q`

Expected: PASS and missing YAML never reaches JSON/config parsing.

- [ ] **Step 5: Commit and push the green slice**

```text
fix(bootstrap): classify startup prerequisites before runtime imports
```

### Task 6: Packaged web asset integrity and schema-before-mutation

**Files:**
- Create: `vesta/runtime_compatibility.py`
- Modify: `vesta/gui_web.py`
- Modify: `vesta/bootstrap.py`
- Modify: `vesta/update/packaging.py`
- Modify: `vesta/update/release.py`
- Modify: `tests/test_gui_web.py`
- Modify: `tests/test_bootstrap_preflight.py`
- Modify: `tests/test_update_packaging.py`
- Modify: `tests/test_update_release.py`

**Interfaces:**
- Consumes: embedded release identity, deterministic production web-asset hash, and a read-only runtime schema probe.
- Produces: `validate_packaged_assets()`, `RuntimeCompatibility`, and fail-closed preflight before deep runtime/state imports.

- [ ] **Step 1: Write failing missing/corrupt/mismatched asset and schema ordering tests**

```python
@pytest.mark.parametrize("failure", ["missing", "corrupt", "wrong_build"])
def test_packaged_gui_fails_closed_on_asset_integrity(failure, packaged_gui):
    diagnostic = packaged_gui.preflight(after=apply_failure(failure))
    assert diagnostic.category in {"missing_packaged_asset", "package_integrity_failure"}
    assert diagnostic.remediation == "Reinstall this Vesta artifact from a verified release."

def test_newer_runtime_schema_blocks_before_mutation(tmp_path):
    mutated = False
    with pytest.raises(BootstrapFailure, match="incompatible_schema"):
        bootstrap_with_schema(tmp_path, observed=2, supported=1, mutate=lambda: set_true())
    assert mutated is False
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_gui_web.py tests/test_bootstrap_preflight.py tests/test_update_packaging.py tests/test_update_release.py -q`

Expected: FAIL because embedded identity does not yet bind the web asset digest
and the bootstrap has no read-only compatibility adapter.

- [ ] **Step 3: Bind exact assets and add the narrow compatibility contract**

```python
@dataclass(frozen=True)
class RuntimeCompatibility:
    observed_schema: int
    maximum_supported_schema: int
    compatible: bool

def assess_runtime_schema(observed: int, maximum_supported: int) -> RuntimeCompatibility:
    return RuntimeCompatibility(observed, maximum_supported, observed <= maximum_supported)
```

Add `web_assets_sha256` to generated packaged identity and release validation.
Packaged GUI startup checks required files and digest against the embedded value,
with no source/classic-UI fallback on failure. The bootstrap calls a read-only
schema probe before importing state-mutating runtime code; #613 can implement the
probe without changing this contract.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_gui_web.py tests/test_bootstrap_preflight.py tests/test_update_packaging.py tests/test_update_release.py -q`

Expected: PASS.

- [ ] **Step 5: Commit and push the green slice**

```text
fix(packaging): bind packaged assets to runtime identity
```

### Task 7: Candidate identity through native artifacts and qualification evidence

**Files:**
- Modify: `vestahub/desktop_artifacts.py`
- Modify: `scripts/build_desktop_artifacts.py`
- Modify: `scripts/finalize_desktop_artifact.py`
- Modify: `scripts/smoke_desktop_artifacts.py`
- Modify: `scripts/generate_update_release.py`
- Modify: `scripts/prepare_native_update.py`
- Modify: `scripts/qualify_native_update.py`
- Modify: `.github/workflows/desktop-artifacts.yml`
- Modify: `.github/workflows/release-preflight.yml`
- Modify: `tests/test_desktop_artifacts.py`
- Modify: `tests/test_update_release_pipeline.py`
- Modify: `tests/test_release_preflight.py`

**Interfaces:**
- Consumes: canonical application identity, exact candidate SHA, platform/architecture/install identity, web-asset digest, and native qualification evidence.
- Produces: reproducible filenames/manifests, native Windows/macOS metadata, provenance, checksum inventory, and qualification bundle bound to one build/artifact.

- [ ] **Step 1: Write failing exact-build evidence tests**

```python
def test_bundle_evidence_binds_application_candidate_platform_and_artifact(tmp_path):
    evidence = build_evidence(tmp_path, version="0.2.1a1", candidate_sha="c" * 40)
    assert evidence["release_identity"]["application_version"] == "0.2.1a1"
    assert evidence["release_identity"]["build_id"] == "c" * 40
    assert evidence["artifact"]["platform"] in {"windows", "macos"}
    assert evidence["artifact"]["sha256"]
```

Also cover incorrect artifact version, tag mismatch, Windows manifest mismatch,
macOS plist mismatch, and qualification evidence from another candidate/artifact.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_desktop_artifacts.py tests/test_update_release_pipeline.py tests/test_release_preflight.py -q`

Expected: focused assertions fail where evidence lacks the complete canonical
identity or accepts independently supplied version/channel values.

- [ ] **Step 3: Thread one candidate identity through existing machinery**

```python
identity = canonical_build_identity(
    application_version=APPLICATION_VERSION,
    build_id=candidate_sha,
    platform=platform_name,
    architecture=architecture,
    artifact_sha256=artifact_digest,
)
```

Derive artifact names, release manifests, MSIX/AppInstaller version,
`Info.plist`, update feeds, provenance, checksums, and qualification reports from
this object. Keep existing signing, attestation, release-host, provider
qualification, and updater boundaries intact.

- [ ] **Step 4: Verify GREEN across packaging/release milestones**

Run: `python -m pytest tests/test_desktop_artifacts.py tests/test_update_packaging.py tests/test_update_release.py tests/test_update_release_pipeline.py tests/test_release_preflight.py -q`

Expected: PASS.

- [ ] **Step 5: Commit and push the green slice**

```text
fix(release): bind artifacts and qualification to candidate identity
```

### Task 8: Current documentation, full acceptance evidence, and merge qualification

**Files:**
- Modify: `README.md`
- Modify: `docs/INSTALL_PROOF.md`
- Modify: `docs/RELEASE.md`
- Modify: `docs/DESKTOP_ARTIFACT_RELEASE.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_release_identity_drift.py`
- Modify: pull request #723 description

**Interfaces:**
- Consumes: canonical identity validation, all focused test evidence, current issue #625 body, latest main, and GitHub required checks.
- Produces: truthful current docs, AC1–AC10 evidence map, `Closes #625`, and merged main.

- [ ] **Step 1: Write failing current-documentation projection tests**

```python
def test_current_install_proof_uses_canonical_projection(repo_root):
    assert validate_release_identity(repo_root) == ()

def test_archived_release_notes_are_classified_as_history(repo_root):
    assert not any(drift.path.name == "RELEASE_0_2_0_ALPHA_1.md" for drift in validate_release_identity(repo_root))
```

- [ ] **Step 2: Verify RED and correct only current claims**

Run: `python -m pytest tests/test_release_identity_drift.py -q`

Expected: FAIL on current README/install/release projections while historical
changelog/release-note fixtures remain excluded by classification.

- [ ] **Step 3: Generate or validate current documentation**

Replace hardcoded current-version prose with generated/validated projections or
commands that report the installed canonical identity. Do not rewrite historical
release notes or unrelated documentation.

- [ ] **Step 4: Run focused and touched-file gates**

Run: `python scripts/generate_release_identity.py --check`

Run: `python scripts/check_release_identity.py`

Run: `python -m pytest tests/test_release_identity.py tests/test_release_identity_drift.py tests/test_release_build_metadata.py tests/test_bootstrap_preflight.py tests/test_runtime_dependencies.py tests/test_positioning_and_cli.py tests/test_gui_web.py tests/test_desktop_gui.py tests/test_receipt.py tests/test_support_bundle.py tests/test_packaging.py tests/test_desktop_artifacts.py tests/test_update_packaging.py tests/test_update_release.py tests/test_update_release_pipeline.py tests/test_release_preflight.py -q`

Run: `python -m ruff format --check <touched Python files>`

Run: `python -m ruff check <touched Python files>`

Expected: PASS with pristine output.

- [ ] **Step 5: Commit and push documentation**

```text
docs(release): derive install proof from canonical identity
```

- [ ] **Step 6: Re-read #625, sync latest main, and review the complete diff**

Run: `git fetch origin main --prune`

Run: `git diff --check origin/main...HEAD`

Run: `git diff --stat origin/main...HEAD`

Expected: no unrelated #613/#616 changes and no whitespace errors.

- [ ] **Step 7: Run the required merge-ready repository gate once**

Run: `python scripts/ci_local.py --profile full`

Expected: PASS; any environment-only skip is recorded exactly and must satisfy
the repository's required-check policy.

- [ ] **Step 8: Update PR evidence and merge through required checks**

Map AC1–AC10 to files/tests, change `Refs #625` to `Closes #625`, wait for
required checks, merge with the repository's established convention, then
verify main CI and issue closure.
