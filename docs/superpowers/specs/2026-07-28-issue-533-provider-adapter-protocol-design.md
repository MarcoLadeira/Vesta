# Issue #533 Provider Adapter Protocol and Conformance Suite Design

## Goal

Make provider truth executable: every supported provider must expose one
versioned adapter contract, advertise only its explicit capabilities, and pass
the same deterministic conformance suite before any GUI, CLI, doctor, router,
or runtime path may treat it as available or successful.

## Scope and boundaries

The initial catalog inventories exactly the supported provider IDs present on
2026-07-28: `claude`, `codex`, `copilot`, `kimi`, `gemini`, `groq`, `mistral`,
`ollama`, and `openai-compatible`. A deterministic `mock` adapter is a test
fixture, not a selectable production route.

This work owns provider-adapter truth. It does not choose a route or price
policy (#350), define the task/run lifecycle (#295), implement health circuit
breakers (#534), or implement failover (#535). It imports their existing
canonical contracts instead of recreating parallel versions.

No test makes a cloud call by default. Real-provider smoke paths require an
explicit provider-specific environment gate and valid local credentials; a
missing gate or credential is a reported skip, never a passing probe.

## Current-state assessment

`opaihub.provider_adapters.ProviderAdapter` already provides a useful narrow
wrapper for provider kind, CLI command construction, event parsing, probes,
and usage extraction. `opaihub.provider_capabilities.ProviderProfile` gives
GUI, CLI, and doctor a partial shared capability view. However, both expose
untyped dictionaries, derive truth from provider kind in several places, lack
a protocol/catalog version, merge readiness facts into a single health view,
and have no common stream/cancellation/terminal conformance runner. Those
gaps make it possible for a provider-specific path to drift from the canonical
completion and usage contracts.

## Chosen architecture

Add a protocol-first core under the existing public APIs, then have the
existing adapter, profile, picker, CLI, and doctor code consume it. This keeps
callers compatible while putting all new truth behind one validated boundary.
An all-at-once rewrite of provider runners is intentionally out of scope.

### 1. Versioned protocol model

`opaihub/provider_protocol.py` defines protocol version `1` and frozen,
JSON-safe data models for:

- `AdapterRequest`: provider/model identity, requested capabilities, request
  correlation, cancellation deadline, and existing OPai policy context.
- `ProviderEvent`: one ordered event carrying a declared kind, sequence,
  monotonic timestamp, adapter/catalog/protocol versions, and safe metadata.
  Allowed kinds are `started`, `text_delta`, `tool_call`, `structured_output`,
  `usage`, `cancel_ack`, `error`, and `terminal`.
- `UsageObservation`: input/output/total values and a measurement provenance of
  `actual`, `derived`, `estimated`, or `unavailable`. Missing measurements
  remain unavailable; zero is never invented.
- `TerminalResult`: a non-provider-owned terminal proposal that references an
  existing `CompletionState`/`CompletionVerdict` from #295. The protocol may
  report evidence and a typed provider failure, but cannot create a new
  completion vocabulary or pronounce a model's prose verified.
- `ProviderFailure`: only canonical error codes from `opai.provider_contract`,
  with retryability and safe user action. Raw provider/SDK text stays diagnostic
  input and cannot set cost, authority, verification, or completion truth.
- `CapabilityStatus`: `supported`, `partial`, `unsupported`, or `unknown`.
  `unknown` is rejected at admission, not treated as a compatible default.

Validation enforces strictly increasing event sequences, exactly one terminal
event, no event after terminal, well-formed usage provenance, and an explicit
cancel acknowledgement before a cancellation terminal result. The validator
does not own run-state transition rules; it hands validated evidence to the
existing runtime contract.

### 2. Version-pinned provider catalog

`opaihub/provider_catalog.py` loads the package catalog snapshot
`opaihub/data/provider_catalog/v1.json`. The catalog has a schema version,
protocol-version compatibility range, source/provenance, publication date, and
one explicit record per supported provider. Each record declares capabilities,
requirements, measurement semantics, cancellation mode, stream mode, known
deliberately unsupported behavior, and source-attributed pricing metadata with
its observed-at/expiry bounds. The pricing data is replay evidence only; #350
continues to own route selection and cost policy. The catalog contains no
secrets, credentials, or dynamic health claims.

The catalog represents readiness as independent facts: installed, configured,
authenticated, authorised, and healthy. Health remains `unknown` until
measured. A provider cannot be admitted merely because a binary is installed
or an account is authenticated.

`ProviderAdapter`, `provider_profile`, `all_provider_profiles`,
`available_models`, and doctor payload assembly read the same catalog-backed
record. Existing response keys remain available during the migration, but their
values derive from the canonical record rather than provider-name heuristics.

A matching immutable test fixture under `tests/fixtures/provider_catalog/v1.json`
replays version 1. Tests compare the production snapshot, the fixture, the
supported-provider set, and the documentation matrix. Any catalog or protocol
version change must add a migration note and a `CHANGELOG.md` entry; silent
snapshot drift fails CI.

### 3. Adapter boundary and capability negotiation

Each production adapter implements the same small protocol surface:

1. describe its catalog record and observed readiness facts;
2. validate requested capabilities before starting work;
3. produce normalized protocol events from transport output;
4. acknowledge cancellation or report that acknowledgement cannot be proven;
5. normalize usage and failures; and
6. emit one validated terminal proposal.

The existing `ProviderAdapter` remains the compatibility façade. Provider-
specific parsers and subprocess/API runners stay where they are, but their
output must pass the protocol validator. Unsupported capabilities fail before
an execution plan or child process is created. A no-op, an SDK-specific status
string, or a natural-language answer cannot masquerade as capability support.

`MockProviderAdapter` accepts scripted events and virtual time. It supplies
repeatable successful, partial, timeout, authentication, quota/rate-limit,
malformed-output, outage, and cancellation scenarios without executing a real
CLI or network request.

### 4. Timing and degraded-result policy

The v1 protocol declares three per-attempt SLOs, evaluated from protocol event
timestamps rather than wall-clock sleeps: first observable event within 30
seconds, cancellation acknowledgement within 2 seconds when cancellation is
advertised, and one terminal result within 10 seconds after cancellation
acknowledgement. Catalog records may declare a stricter supported value but
may not silently loosen the v1 defaults.

An adapter that cannot supply the required acknowledgement or terminal proof
emits a typed degraded result with the missing evidence. It never emits a
successful terminal result solely because the child process exited or returned
prose. The optional smoke suite records observed timings against these SLOs;
it does not fabricate a pass when real credentials are unavailable.

### 5. Conformance suite

`tests/provider_conformance.py` supplies reusable assertions that run every
catalog entry and mock script through the same contract. The suite covers:

- successful and partial streaming;
- cancellation before output and during streaming;
- timeout, authentication failure, quota/rate limit, network/provider outage,
  and malformed output;
- unsupported/unknown capability rejection before execution;
- actual, derived, estimated, and unavailable usage;
- valid canonical terminal proposals and rejection of invalid terminal states;
- GUI, CLI, and doctor agreement on one provider record; and
- rejection of prose/SDK status attempting to set completion, cost, authority,
  or verification truth.

Property-based tests use a test-only pinned `hypothesis` dependency installed
by CI. Generated streams prove ordering, duplicate-terminal rejection,
malformed usage rejection, cancellation-race handling, and unknown-capability
rejection. Each property is deterministic under an explicit seed in CI, and
the failing seed is reported for replay.

`tests/test_live_provider_smoke.py` becomes a provider-parameterised optional
conformance smoke harness. It invokes only a sandbox-safe, no-repository-
content request path exposed by each adapter, requires explicit opt-in gates,
and reports a capability-specific skip if prerequisites are absent.

### 6. Coverage matrix and compatibility evidence

`docs/PROVIDER_ADAPTER_CONFORMANCE.md` is generated from the catalog and
committed. It lists every provider and mock with supported, partial, and
deliberately unsupported semantics for availability, authentication,
authorisation, streaming, cancellation, usage provenance, tools, structured
output, and real-smoke eligibility. The generator/test fails when prose,
provider family, or model naming is used to infer a cell.

The document also names v1 compatibility rules: an adapter may consume only a
catalog version it declares compatible; an incompatible catalog/protocol pair
returns an actionable `degraded` record with an upgrade/migration action. It
must not fall back to stale provider data.

## File-level implementation boundary

- Create `opaihub/provider_protocol.py` for data models, validation, SLO
  evaluation, and protocol/version compatibility checks.
- Create `opaihub/provider_catalog.py` and
  `opaihub/data/provider_catalog/v1.json` for one authoritative provider
  snapshot.
- Modify `opaihub/provider_adapters.py` and
  `opaihub/provider_capabilities.py` to use the catalog/protocol while
  preserving existing public functions.
- Modify the existing model/doctor payload builders only as necessary so GUI,
  CLI, and doctor carry the canonical record and degraded version state.
- Create fixture, conformance, catalog, property, and surface-parity tests;
  extend the optional live-provider smoke test without enabling cloud calls.
- Create the generated coverage matrix and update `CHANGELOG.md` and package/
  CI test dependency configuration for the test-only property framework.

## Safety and non-goals

The change never reads or emits credentials, launches a provider during normal
tests, routes work, retries a provider, breaks a circuit, fails over mid-run,
or changes canonical run/completion semantics. It makes those existing
boundaries explicit and testable. Later #534 and #535 work can rely on the
resulting evidence without #533 pre-implementing their policies.

## Acceptance mapping

| Issue requirement | Proof in this design |
| --- | --- |
| Inventory every provider and gaps | Version-pinned catalog plus generated coverage matrix |
| Contract failures and streaming cases | Reusable deterministic conformance scripts for every provider |
| Fail closed on unsupported capability | Pre-execution capability negotiation validation |
| Preserve #295/#350 truth | Protocol references canonical completion and usage provenance only |
| Usage provenance | `actual`, `derived`, `estimated`, `unavailable` observation schema |
| GUI/CLI/doctor one contract | Shared catalog-backed payload and parity tests |
| Version incompatibility | Protocol/catalog compatibility check gives actionable degraded state |
| Replay and property hardening | Pinned fixture, Hypothesis generated traces, optional smoke harness |
| Timing evidence | Event-timestamp SLO evaluation and degraded result on missing proof |
| Prevent provider prose authority | Negative conformance tests at the protocol validator boundary |
