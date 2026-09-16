# Issue #533 Provider Adapter Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a versioned, catalog-backed provider adapter protocol and
deterministic conformance suite for all nine supported providers.

**Architecture:** A new protocol module validates provider-neutral events,
usage, cancellation evidence, SLOs, and version compatibility. A version-pinned
catalog becomes the one source for profiles, adapter capabilities, doctor,
picker, CLI payloads, and the generated coverage matrix. Existing provider
runners and #295 completion evaluation remain authoritative for execution and
terminal truth.

**Tech Stack:** Python 3.10+, `unittest`, Hypothesis 6.160.0 (test-only),
JSON catalog fixtures, GitHub Actions, and the existing optional live-provider
smoke gates.

---

## Constraints

- Keep `ProviderAdapter`, `ProviderProfile`, `available_models`, and doctor
  response keys compatible unless a new additive protocol field is required.
- Never make network, CLI, or provider calls in deterministic tests.
- Never allow an adapter to create a `completed` claim, a cost fact, an
  authority decision, or a verification verdict from prose/SDK status.
- Preserve #295's `CompletionState` and #350's cost-policy ownership.
- Real-provider smoke is opt-in only and transmits no repository content.

## File structure

| File | Responsibility |
| --- | --- |
| `vestahub/provider_protocol.py` | Versioned request/event/usage/readiness data models and validation. |
| `vestahub/provider_catalog.py` | Load and validate the immutable v1 catalog; render the matrix. |
| `vestahub/data/provider_catalog/v1.json` | Production catalog snapshot with source, expiry, capability, and pricing metadata. |
| `vestahub/provider_adapters.py` | Compatibility facade over catalog capabilities, readiness, event normalization, and safe probe traces. |
| `vestahub/provider_capabilities.py` | Build legacy `ProviderProfile` values from catalog records. |
| `vesta/app_state.py`, `vestahub/accounts.py` | Carry additive catalog/protocol/readiness records to GUI, CLI, and doctor. |
| `tests/provider_conformance.py` | Deterministic mock adapter, scripted traces, and shared conformance assertions. |
| `tests/test_provider_catalog.py`, `tests/test_provider_protocol.py`, `tests/test_provider_conformance.py` | Catalog, protocol, property, parity, and negative-contract tests. |
| `tests/fixtures/provider_catalog/v1.json` | Byte-identical replay fixture for the catalog. |
| `tests/test_live_provider_smoke.py` | Opt-in protocol probe smoke parameterised by every catalog provider. |
| `docs/PROVIDER_ADAPTER_CONFORMANCE.md` | Generated, committed coverage matrix. |
| `pyproject.toml`, `.github/workflows/ci.yml`, `.github/workflows/ci-selfhosted.yml` | Pinned test dependency and reproducible CI installation. |
| `CHANGELOG.md`, `CONTRIBUTING.md` | Protocol-v1 migration evidence and local test setup. |

### Task 1: Add the pinned catalog and reproducible test dependency

**Files:**
- Create: `vestahub/data/provider_catalog/v1.json`
- Create: `vestahub/provider_catalog.py`
- Create: `tests/fixtures/provider_catalog/v1.json`
- Create: `tests/test_provider_catalog.py`
- Modify: `pyproject.toml:15-42`
- Modify: `.github/workflows/ci.yml:56-66, 109-114`
- Modify: `.github/workflows/ci-selfhosted.yml:39-40`
- Modify: `CONTRIBUTING.md:8-13`

- [ ] **Step 1: Write failing catalog and fixture tests**

```python
class ProviderCatalogTests(unittest.TestCase):
    def test_v1_catalog_inventories_exactly_the_supported_providers(self):
        self.assertEqual(
            provider_ids(),
            frozenset({
                "claude", "codex", "copilot", "kimi", "gemini", "groq",
                "mistral", "ollama", "openai-compatible",
            }),
        )

    def test_fixture_replays_the_packaged_snapshot_byte_for_byte(self):
        self.assertEqual(catalog_bytes(), fixture_path().read_bytes())

    def test_every_record_has_explicit_capability_and_pricing_provenance(self):
        for record in all_catalog_records():
            self.assertEqual(record.protocol_version, 1)
            self.assertIn(record.pricing.measurement, {"actual", "derived", "estimated", "unavailable"})
            self.assertTrue(record.pricing.source)
            self.assertTrue(record.pricing.observed_at)
            self.assertTrue(record.pricing.expires_at)
```

- [ ] **Step 2: Verify the tests fail because the catalog module is absent**

Run: `python -m unittest tests.test_provider_catalog -v`

Expected: `ModuleNotFoundError: No module named 'vestahub.provider_catalog'`.

- [ ] **Step 3: Add the v1 schema, loader, and dependency wiring**

```toml
[project.optional-dependencies]
test = ["hypothesis==6.160.0"]

[tool.setuptools.package-data]
vestahub = [
    "data/hub/**/*",
    "data/provider_catalog/*.json",
]
```

```python
CATALOG_VERSION = "v1"
PROTOCOL_VERSION = 1

def catalog_path() -> Path:
    return Path(__file__).with_name("data") / "provider_catalog" / "v1.json"

def provider_record(provider_id: str) -> ProviderCatalogRecord:
    try:
        return _load_catalog().providers[_normalise_provider_id(provider_id)]
    except KeyError as exc:
        raise ValueError(f"Unsupported AI provider: {provider_id!r}") from exc
```

The JSON must give every provider explicit statuses for `chat`,
`code_execution`, `repo_editing`, `streaming`, `tool_calling`,
`structured_output`, and cancellation; requirements; source/expiry-bounded
pricing metadata; and deliberately unsupported behaviour. Copy the exact JSON
to the test fixture. Update each CI installation command that runs Python tests
to install `-e ".[test]"`, and retain the existing separate pytest install.

- [ ] **Step 4: Verify catalog tests and package data**

Run: `python -m unittest tests.test_provider_catalog -v`

Expected: all catalog tests pass without network access.

- [ ] **Step 5: Commit the self-contained catalog foundation**

```bash
git add pyproject.toml CONTRIBUTING.md .github/workflows/ci.yml .github/workflows/ci-selfhosted.yml vestahub/provider_catalog.py vestahub/data/provider_catalog/v1.json tests/fixtures/provider_catalog/v1.json tests/test_provider_catalog.py
git commit -m "feat(providers): add versioned provider catalog"
```

### Task 2: Define and test the protocol validator

**Files:**
- Create: `vestahub/provider_protocol.py`
- Create: `tests/test_provider_protocol.py`

- [ ] **Step 1: Write failing protocol and generated-property tests**

```python
class ProviderProtocolTests(unittest.TestCase):
    @given(st.permutations(("started", "text_delta", "terminal")))
    def test_only_ordered_streams_are_valid(self, kinds):
        events = [event(kind, sequence=index + 1) for index, kind in enumerate(kinds)]
        if kinds == ("started", "text_delta", "terminal"):
            validate_event_stream(events)
        else:
            with self.assertRaises(ProtocolViolation):
                validate_event_stream(events)

    def test_duplicate_terminal_and_post_terminal_event_are_rejected(self):
        with self.assertRaisesRegex(ProtocolViolation, "terminal"):
            validate_event_stream([event("started", 1), event("terminal", 2), event("terminal", 3)])

    def test_adapter_prose_cannot_assert_completion_cost_authority_or_verification(self):
        with self.assertRaises(ProtocolViolation):
            ProviderEvent("terminal", 2, 1.0, {"completion_state": "completed"})
```

- [ ] **Step 2: Verify the protocol tests fail for missing symbols**

Run: `python -m unittest tests.test_provider_protocol -v`

Expected: import failure for `vestahub.provider_protocol`.

- [ ] **Step 3: Implement the immutable protocol boundary**

```python
class EventKind(str, Enum):
    STARTED = "started"
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    STRUCTURED_OUTPUT = "structured_output"
    USAGE = "usage"
    CANCEL_ACK = "cancel_ack"
    ERROR = "error"
    TERMINAL = "terminal"

@dataclass(frozen=True)
class AdapterSLO:
    first_event_seconds: float = 30.0
    cancel_ack_seconds: float = 2.0
    terminal_after_cancel_seconds: float = 10.0

def validate_event_stream(
    events: Sequence[ProviderEvent],
    *,
    slo: AdapterSLO = AdapterSLO(),
    cancel_requested_at: float | None = None,
) -> None:
    if not events or events[0].kind is not EventKind.STARTED:
        raise ProtocolViolation("stream must begin with started")
    terminal_at: float | None = None
    cancel_ack_at: float | None = None
    previous_at = events[0].at
    for expected_sequence, event in enumerate(events, start=1):
        if event.sequence != expected_sequence or event.at < previous_at:
            raise ProtocolViolation("event sequence and timestamps must be monotonic")
        if terminal_at is not None:
            raise ProtocolViolation("event emitted after terminal")
        if {"completion_state", "cost", "authority", "verification"} & set(event.payload):
            raise ProtocolViolation("provider event cannot assert canonical truth")
        if event.kind is EventKind.USAGE:
            UsageObservation.from_payload(event.payload)
        if event.kind is EventKind.CANCEL_ACK:
            cancel_ack_at = event.at
        if event.kind is EventKind.TERMINAL:
            terminal_at = event.at
        previous_at = event.at
    if terminal_at is None:
        raise ProtocolViolation("stream must end with one terminal event")
    if len(events) > 1 and events[1].at - events[0].at > slo.first_event_seconds:
        raise ProtocolViolation("first observable event exceeded SLO")
    if cancel_requested_at is not None:
        if cancel_ack_at is None or cancel_ack_at - cancel_requested_at > slo.cancel_ack_seconds:
            raise ProtocolViolation("cancellation acknowledgement exceeded SLO")
        if terminal_at - cancel_ack_at > slo.terminal_after_cancel_seconds:
            raise ProtocolViolation("terminal acknowledgement exceeded SLO")
```

Use `CompletionState` only to validate a supplied non-success failure mapping;
the protocol must reject provider-supplied `completed` and leave verification to
the existing completion evaluator. Add `ProviderReadiness` with independently
typed installed/configured/authenticated/authorised/health facts and a
`degraded_reason` for version/SLO proof failures.

- [ ] **Step 4: Verify focused unit and property tests pass**

Run: `python -m unittest tests.test_provider_protocol -v`

Expected: all example and Hypothesis-generated traces pass; invalid traces are
rejected for the asserted rule.

- [ ] **Step 5: Commit the validated protocol core**

```bash
git add vestahub/provider_protocol.py tests/test_provider_protocol.py
git commit -m "feat(providers): validate adapter protocol events"
```

### Task 3: Migrate adapter and legacy profile truth to the catalog

**Files:**
- Modify: `vestahub/provider_adapters.py:13-16, 160-387`
- Modify: `vestahub/provider_capabilities.py:106-246, 277-320`
- Modify: `tests/test_provider_adapters.py`
- Modify: `tests/test_provider_capabilities.py`

- [ ] **Step 1: Write failing compatibility and admission tests**

```python
def test_adapter_capabilities_are_catalog_backed_for_every_provider():
    for provider_id in provider_ids():
        adapter = adapter_for(provider_id)
        self.assertEqual(adapter.kind, provider_record(provider_id).kind)
        self.assertEqual(adapter.profile.to_dict()["catalogVersion"], "v1")

def test_unknown_or_unsupported_capability_is_rejected_before_execution():
    adapter = adapter_for("ollama")
    with self.assertRaisesRegex(ValueError, "unsupported capability"):
        adapter.validate_request(AdapterRequest(provider_id="ollama", requested_capabilities=("repo_editing",)))
```

- [ ] **Step 2: Verify the compatibility tests fail against the legacy wrapper**

Run: `python -m unittest tests.test_provider_adapters tests.test_provider_capabilities -v`

Expected: missing `catalogVersion` and `validate_request` failures.

- [ ] **Step 3: Adapt existing APIs without changing their callers**

```python
@property
def capabilities(self) -> ProviderCapabilities:
    record = provider_record(self.provider_id)
    return ProviderCapabilities(
        repo_read=record.supports("repo_read"),
        patch_edit=record.supports("repo_editing"),
        run_tests=record.supports("run_tests"),
        native_tools=record.supports("tool_calling"),
        streaming=record.supports("streaming"),
    )

def validate_request(self, request: AdapterRequest) -> None:
    if request.provider_id != self.provider_id:
        raise ValueError("adapter/provider mismatch")
    for capability in request.requested_capabilities:
        if not provider_record(self.provider_id).supports(capability):
            raise ValueError(f"unsupported capability: {capability}")
```

Build `ProviderProfile` from catalog statuses and add only additive
`catalogVersion`, `protocolVersion`, `capabilityStatus`, and `providerState`
fields to serialized payloads. Retain the existing `ProviderHealth` enum and
mapping for old callers, but derive it from `ProviderReadiness` instead of
making it a substitute for authorisation or health evidence.

- [ ] **Step 4: Run migrated focused suites**

Run: `python -m unittest tests.test_provider_adapters tests.test_provider_capabilities tests.test_provider_contract -v`

Expected: existing behaviours remain green and new catalog/admission assertions
pass.

- [ ] **Step 5: Commit adapter migration**

```bash
git add vestahub/provider_adapters.py vestahub/provider_capabilities.py tests/test_provider_adapters.py tests/test_provider_capabilities.py
git commit -m "feat(providers): derive adapter truth from catalog"
```

### Task 4: Reconcile doctor, picker, and CLI payloads

**Files:**
- Modify: `vestahub/accounts.py:871-1025`
- Modify: `vesta/app_state.py:554-710`
- Create: `tests/test_provider_contract_surfaces.py`
- Modify: `tests/test_connection_doctor.py`

- [ ] **Step 1: Write failing cross-surface contract tests**

```python
def test_doctor_gui_and_cli_share_one_contract_for_every_provider(tmp_path):
    payload = available_models(tmp_path, discover_local=False)
    doctor = provider_connection_doctor(accounts=[], credentials=[])
    for profile in all_provider_profiles():
        provider_id = profile["provider_id"]
        self.assertEqual(profile["catalogVersion"], "v1")
        self.assertEqual(profile["protocolVersion"], 1)
        self.assertIn(provider_id, payload["providerContracts"])
        self.assertEqual(payload["providerContracts"][provider_id], profile["contract"])

def test_incompatible_protocol_is_actionable_degraded_not_stale_fallback():
    record = replace(provider_record("claude"), compatible_protocol_versions=())
    state = readiness_for(record, observation={})
    self.assertEqual(state.health, "degraded")
    self.assertIn("upgrade", state.next_action)
```

- [ ] **Step 2: Verify the new surface suite fails**

Run: `python -m unittest tests.test_provider_contract_surfaces -v`

Expected: missing canonical contract keys.

- [ ] **Step 3: Add only canonical additive payloads**

```python
entry["providerContract"] = provider_contract_payload(provider, observation=connection)
payload["providerCatalogVersion"] = CATALOG_VERSION
payload["providerProtocolVersion"] = PROTOCOL_VERSION
payload["providerContracts"] = {
    item["provider_id"]: item["contract"] for item in all_provider_profiles()
}
```

Doctor must build state from the catalog plus its existing safe local
observation. `available_models` must reuse the same `provider_contract_payload`
function. Do not make model enumeration probe a provider or render a new
completion status.

- [ ] **Step 4: Run surface and regression suites**

Run: `python -m unittest tests.test_provider_contract_surfaces tests.test_connection_doctor tests.test_provider_connections tests.test_cli_capability_cache -v`

Expected: GUI/CLI/doctor records agree and all legacy doctor/picker tests pass.

- [ ] **Step 5: Commit cross-surface wiring**

```bash
git add vestahub/accounts.py vesta/app_state.py tests/test_provider_contract_surfaces.py tests/test_connection_doctor.py
git commit -m "feat(providers): expose shared adapter contract to surfaces"
```

### Task 5: Build deterministic conformance and opt-in smoke suites

**Files:**
- Create: `tests/provider_conformance.py`
- Create: `tests/test_provider_conformance.py`
- Modify: `tests/test_live_provider_smoke.py`

- [ ] **Step 1: Write failing shared scenario tests**

```python
class ProviderConformanceTests(unittest.TestCase):
    def test_every_adapter_passes_the_deterministic_conformance_scenarios(self):
        for provider_id in provider_ids():
            for scenario in SCENARIOS:
                with self.subTest(provider=provider_id, scenario=scenario):
                    assert_conforms(scripted_trace(provider_id, scenario), provider_record(provider_id))

    def test_cancel_race_requires_ack_and_terminal_within_slo(self):
        with self.assertRaisesRegex(ProtocolViolation, "cancel"):
            assert_conforms(scripted_trace("mock", "cancel_without_ack"), mock_record())
```

`SCENARIOS` must be the exact names `success_stream`, `partial_stream`,
`cancel_before_output`, `cancel_during_stream`, `timeout`, `auth_failure`,
`quota_failure`, `rate_limit`, `provider_outage`, `malformed_output`, and
`unsupported_capability`.

- [ ] **Step 2: Verify the conformance suite fails before the harness exists**

Run: `python -m unittest tests.test_provider_conformance -v`

Expected: import failure for `tests.provider_conformance`.

- [ ] **Step 3: Implement the deterministic mock and reusable assertions**

```python
class MockProviderAdapter:
    def __init__(self, trace: Sequence[ProviderEvent]) -> None:
        self._trace = tuple(trace)

    def stream(self, request: AdapterRequest) -> Sequence[ProviderEvent]:
        return self._trace

def assert_conforms(events: Sequence[ProviderEvent], record: ProviderCatalogRecord) -> None:
    validate_event_stream(events, slo=record.slo)
    for event in events:
        if event.kind is EventKind.USAGE:
            UsageObservation.from_payload(event.payload)
```

Extend the existing live smoke class with one subtest per catalog provider. The
test runs only when both `VESTA_LIVE_PROVIDER_SMOKE=1` and that provider appears
in `VESTA_LIVE_PROVIDER_SMOKE_PROVIDERS`. It calls the adapter's existing safe
probe path, converts its result to a protocol readiness trace, and validates
the same catalog/SLO/version contract. It must skip with the provider name and
missing prerequisite when a gate, binary, endpoint, model, or credential is
absent.

- [ ] **Step 4: Run deterministic conformance and confirm live smoke skips**

Run: `python -m unittest tests.test_provider_conformance tests.test_live_provider_smoke -v`

Expected: all deterministic scenarios pass; every real-provider subtest is
skipped unless its explicit gate and prerequisites exist.

- [ ] **Step 5: Commit executable conformance evidence**

```bash
git add tests/provider_conformance.py tests/test_provider_conformance.py tests/test_live_provider_smoke.py
git commit -m "test(providers): enforce adapter conformance scenarios"
```

### Task 6: Generate coverage evidence and prevent version drift

**Files:**
- Create: `docs/PROVIDER_ADAPTER_CONFORMANCE.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_provider_catalog.py`

- [ ] **Step 1: Write a failing matrix-drift and migration-evidence test**

```python
def test_committed_coverage_matrix_is_rendered_from_the_v1_catalog():
    matrix = Path("docs/PROVIDER_ADAPTER_CONFORMANCE.md").read_text(encoding="utf-8")
    self.assertEqual(matrix, render_coverage_matrix())

def test_protocol_v1_is_recorded_in_the_changelog():
    self.assertIn("Provider adapter protocol v1", Path("CHANGELOG.md").read_text(encoding="utf-8"))
```

- [ ] **Step 2: Verify drift tests fail while the document and changelog entry are absent**

Run: `python -m unittest tests.test_provider_catalog.ProviderCatalogTests.test_committed_coverage_matrix_is_rendered_from_the_v1_catalog tests.test_provider_catalog.ProviderCatalogTests.test_protocol_v1_is_recorded_in_the_changelog -v`

Expected: missing matrix and migration evidence failures.

- [ ] **Step 3: Render and commit the exact catalog matrix**

```python
def render_coverage_matrix() -> str:
    rows = ["# Provider Adapter Conformance", "", "| Provider | Stream | Cancel | Usage | Tools | Structured output | Smoke |", "| --- | --- | --- | --- | --- | --- | --- |"]
    rows.extend(_matrix_row(record) for record in (*all_catalog_records(), mock_record()))
    return "\n".join(rows) + "\n"
```

The matrix must show `supported`, `partial`, or `unsupported` for every cell;
never a blank, inferred, or provider-name-derived capability. Add a dated
`Provider adapter protocol v1` changelog item that names the catalog migration
requirement and the compatibility-degraded outcome.

- [ ] **Step 4: Verify matrix and catalog drift checks**

Run: `python -m unittest tests.test_provider_catalog -v`

Expected: catalog, fixture, matrix, and changelog proof pass exactly.

- [ ] **Step 5: Commit release evidence**

```bash
git add docs/PROVIDER_ADAPTER_CONFORMANCE.md CHANGELOG.md tests/test_provider_catalog.py
git commit -m "docs(providers): publish adapter conformance matrix"
```

### Task 7: Full validation and review

**Files:**
- Verify: all files changed above

- [ ] **Step 1: Format and lint the changed Python and repository**

Run: `python -m ruff format --check . && python -m ruff check .`

Expected: exit code 0.

- [ ] **Step 2: Run the focused protocol and surface suite with hostile credentials**

Run: `$env:PYTHONPATH='tests/fixtures/hostile_keyring'; python -m unittest tests.test_provider_catalog tests.test_provider_protocol tests.test_provider_adapters tests.test_provider_capabilities tests.test_provider_contract_surfaces tests.test_connection_doctor tests.test_live_provider_smoke -v`

Expected: deterministic tests pass; live-provider tests skip without explicit
gates; no secrets appear in output.

- [ ] **Step 3: Run the complete Python suite and canonical local gate**

Run: `python -m unittest discover -s tests && python -m vestahub validate && python scripts/ci_local.py`

Expected: all commands exit 0.

- [ ] **Step 4: Review the final diff and scan staged content**

Run: `git diff --check origin/main && git diff --check && detect-secrets scan --all-files --exclude-files "(^|[\\/])\.opcoding([\\/]|$)|(^|[\\/])\.vestahub([\\/]|$)"`

Expected: no whitespace errors and no unreviewed secrets. Confirm only the
protocol/catalog/conformance files and required CI/docs changes are staged.

- [ ] **Step 5: Create the final implementation commit and prepare the PR**

```bash
git add -- pyproject.toml CONTRIBUTING.md CHANGELOG.md .github/workflows/ci.yml .github/workflows/ci-selfhosted.yml vestahub/provider_protocol.py vestahub/provider_catalog.py vestahub/provider_adapters.py vestahub/provider_capabilities.py vestahub/data/provider_catalog/v1.json vesta/app_state.py vestahub/accounts.py tests/provider_conformance.py tests/test_provider_catalog.py tests/test_provider_protocol.py tests/test_provider_conformance.py tests/test_provider_contract_surfaces.py tests/test_provider_adapters.py tests/test_provider_capabilities.py tests/test_connection_doctor.py tests/test_live_provider_smoke.py tests/fixtures/provider_catalog/v1.json docs/PROVIDER_ADAPTER_CONFORMANCE.md
git commit -m "feat(providers): enforce adapter conformance protocol"
git push -u origin codex/issue-533-provider-adapter-conformance
```

The draft PR body must close #533 and state the catalog version, conformance
coverage, skipped real-provider policy, SLOs, validation commands, and that no
credentialed smoke ran unless explicitly enabled.
