# Lifecycle and Result Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generated lifecycle and evidence-derived result contracts authoritative in every supported OPai execution surface.

**Architecture:** The versioned JSON lifecycle schema is the sole editable lifecycle definition. A deterministic standard-library generator produces Python, browser, fixture, and Markdown projections. A typed immutable `RunResult` validates terminal evidence and becomes the source that local/explicit/background execution, GUI, CLI, receipt, and browser rendering consume.

**Tech Stack:** Python 3.10+, JSON, dataclasses, unittest, Node/Vitest, GitHub Actions.

## Global Constraints

- Generation works offline without credentials or providers; generated source is build-time only.
- Lifecycle identifiers, reasons, and schema version are persisted meanings; display text is not.
- `cancel_requested` is non-terminal, `cancelled` requires reconciliation, and `verifying -> running` uses `verification_repair`.
- Future/unknown state, status, and schema data is explicitly incompatible or degraded, never implicit success/failure.
- Legacy strings are input/output compatibility only and never decide a transition, retry, policy, or completion.

---

### Task 1: Generate canonical lifecycle projections

**Files:**
- Create: `opaihub/lifecycle_schema.json`
- Create: `scripts/generate_lifecycle.py`
- Create: `opaihub/generated_lifecycle.py`
- Create: `opai/assets/web/generated-lifecycle.js`
- Create: `opaihub/data/lifecycle-fixtures.json`
- Create: `docs/lifecycle-schema.md`
- Test: `tests/test_lifecycle_generation.py`

**Interfaces:**
- Consumes: a schema with version, state classifications, transitions, required reason classes, guards, exit codes, and legacy mappings.
- Produces: Python `STATE_IDS`, `TERMINAL_STATE_IDS`, `TRANSITIONS`, `EXIT_CODES`, and `transition_spec`; browser `window.OPaiLifecycle`; byte-stable fixture vectors and documentation.

- [ ] **Step 1: Write the failing generator tests**

```python
def test_generator_check_rejects_changed_projection(self):
    generated = ROOT / "opaihub/generated_lifecycle.py"
    generated.write_text(generated.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    self.assertEqual(run_generator("--check"), 1)

def test_fixture_contains_generated_repair_and_terminal_contract(self):
    fixture = load_fixture()
    self.assertIn({"from": "verifying", "to": "running", "reason": "verification_repair"}, fixture["vectors"])
    self.assertEqual(fixture["terminal_states"], sorted(generated.TERMINAL_STATE_IDS))
```

- [ ] **Step 2: Run the test and observe the expected failure**

Run: `python -m unittest tests.test_lifecycle_generation -v`

Expected: FAIL because neither a source schema nor a generation command exists.

- [ ] **Step 3: Implement deterministic schema rendering**

```python
def main(argv: Sequence[str] | None = None) -> int:
    outputs = render_all(load_schema(SCHEMA_PATH))
    return 1 if write_or_check(outputs, check="--check" in (argv or ())) else 0
```

Define `queued`, `preparing`, `running`, `awaiting_input`,
`cancel_requested`, `verifying`, `completed`, `partial`, `blocked`,
`failed`, `cancelled`, `timeout`, and `needs_attention`. Include every legal
edge, required reason class, new-attempt property, cancellation eligibility,
verification/delivery guard, CLI exit mapping, presentation category, and
legacy mapping. Add the generated-file header to every output.

- [ ] **Step 4: Generate artifacts and establish green**

Run: `python scripts/generate_lifecycle.py; python -m unittest tests.test_lifecycle_generation -v; python scripts/generate_lifecycle.py --check`

Expected: all commands exit 0 after the mutation in the test is restored.

- [ ] **Step 5: Commit the generation slice**

```bash
git add opaihub/lifecycle_schema.json scripts/generate_lifecycle.py opaihub/generated_lifecycle.py opai/assets/web/generated-lifecycle.js opaihub/data/lifecycle-fixtures.json docs/lifecycle-schema.md tests/test_lifecycle_generation.py
git commit -m "feat: generate canonical lifecycle projections"
```

### Task 2: Consume generated state truth in Python and browser reducers

**Files:**
- Modify: `opaihub/run_state.py`
- Create: `opaihub/lifecycle_diagnostics.py`
- Modify: `opai/assets/web/message-state.js`
- Modify: `opai/assets/web/index.html`
- Test: `tests/test_run_state.py`
- Test: `tests/test_run_state_parity.py`
- Test: `opai/assets/web/__tests__/message-state.test.js`

**Interfaces:**
- Consumes: `generated_lifecycle.transition_spec(from_state, to_state)` and `window.OPaiLifecycle.canTransition(fromState, toState)`.
- Produces: backwards-compatible `RunState`, generated terminal/exit/transition APIs, durable illegal-transition records, and browser messages which cannot author canonical edges.

- [ ] **Step 1: Write failing cross-language repair and diagnostic tests**

```python
def test_terminal_attack_preserves_state_and_writes_durable_diagnostic(tmp_path):
    self.assertEqual(transition("completed", "running", project_root=tmp_path), RunState.COMPLETED)
    event = read_diagnostics(tmp_path)[-1]
    self.assertEqual((event["from"], event["to"]), ("completed", "running"))

def test_repair_vector_is_legal_in_python_and_browser(self):
    self.assertTrue(can_transition("verifying", "running"))
    self.assertEqual(browser_reduce("verifying", "running"), "running")
```

```javascript
it("uses the generated repair transition", () => {
  expect(transition({ requestId: "r1", status: "verifying" }, "running").status).toBe("running");
});
```

- [ ] **Step 2: Run tests and observe the expected failure**

Run: `python -m unittest tests.test_run_state tests.test_run_state_parity -v; npm run test:unit -- --run opai/assets/web/__tests__/message-state.test.js`

Expected: FAIL because browser `verifying` has no generated repair edge and the durable diagnostic path is absent.

- [ ] **Step 3: Replace hand-maintained graph authority**

```python
def transition(current, nxt, *, source="", project_root=None):
    prior, target = _coerce(current), _coerce(nxt)
    if transition_spec(prior.value, target.value) is not None:
        return target
    record_illegal_transition(project_root, prior.value, target.value, source)
    return prior
```

Import generated state data instead of defining state/terminal/exit tables in
`run_state.py`. Add the generated browser script before `message-state.js` and
use it for canonical transition/terminal decisions. Retain presentation aliases
only as canonical projections. Persist redacted diagnostic events through the
#517-compatible local event path when a project root is supplied.

- [ ] **Step 4: Run reducer, diagnostic, and parity checks**

Run: `python -m unittest tests.test_run_state tests.test_run_state_parity -v; npm run test:unit -- --run opai/assets/web/__tests__/message-state.test.js`

Expected: PASS; Python and browser accept repair, terminal mutation fails
without state regression, and the rejection is durable/visible.

- [ ] **Step 5: Commit generated state adoption**

```bash
git add opaihub/run_state.py opaihub/lifecycle_diagnostics.py opai/assets/web/generated-lifecycle.js opai/assets/web/message-state.js opai/assets/web/index.html tests/test_run_state.py tests/test_run_state_parity.py opai/assets/web/__tests__/message-state.test.js
git commit -m "feat: consume generated lifecycle contract"
```

### Task 3: Add RunResult and legacy-status boundary adapters

**Files:**
- Create: `opaihub/run_result.py`
- Create: `opaihub/legacy_status.py`
- Modify: `opaihub/completion.py`
- Test: `tests/test_run_result.py`
- Test: `tests/test_legacy_status_boundaries.py`

**Interfaces:**
- Consumes: canonical terminal/reason data plus provider, verification, delivery, cost, authority, and diagnostic evidence mappings.
- Produces: `RunResult.from_payload()`, `RunResult.from_dict()`, `RunResult.to_dict()`, `RunResult.to_json()`, `legacy_status_to_result()`, and `legacy_status_usage()`.

- [ ] **Step 1: Write failing invariant and compatibility tests**

```python
def test_completed_mutation_requires_reconciled_verification_delivery_and_cost():
    with self.assertRaisesRegex(ValueError, "verification"):
        RunResult.from_payload(state="completed", mutating=True, verification={"verdict": "failed"})

def test_unknown_legacy_status_degrades_to_needs_attention():
    result = legacy_status_to_result({"status": "future_vendor_state"})
    self.assertEqual(result.lifecycle["state"], "needs_attention")
    self.assertEqual(result.compatibility["state"], "incompatible")

def test_previous_schema_round_trip_is_byte_stable():
    self.assertEqual(RunResult.from_dict(previous_fixture).to_json(), expected_json)
```

- [ ] **Step 2: Run tests and observe the expected failure**

Run: `python -m unittest tests.test_run_result tests.test_legacy_status_boundaries -v`

Expected: FAIL because no canonical result or legacy boundary adapter exists.

- [ ] **Step 3: Implement the immutable evidence envelope**

```python
@dataclass(frozen=True)
class RunResult:
    schema_version: int
    identity: Mapping[str, Any]
    lifecycle: Mapping[str, Any]
    provider: Mapping[str, Any]
    recovery: Mapping[str, Any]
    verification: Mapping[str, Any]
    delivery: Mapping[str, Any]
    economics: Mapping[str, Any]
    authority: Mapping[str, Any]
    diagnostics: Mapping[str, Any]
    presentation: Mapping[str, Any]
```

Require terminal state/reason pairing, final transition timestamp, and
reconciliation. Reject `completed` without applicable verification, delivery,
and cost-integrity evidence; treat unknown retry reason as disallowed. Store
record references rather than mutable snapshots. The legacy adapter is the
only importer/exporter of status strings, increments explicit usage telemetry,
and documents zero authoritative reads/writes for one release as removal gate.

- [ ] **Step 4: Run the contract tests to green**

Run: `python -m unittest tests.test_run_result tests.test_legacy_status_boundaries -v`

Expected: PASS; impossible terminal evidence is rejected and unknown legacy
input never becomes a generic failure or success.

- [ ] **Step 5: Commit the result contract slice**

```bash
git add opaihub/run_result.py opaihub/legacy_status.py opaihub/completion.py tests/test_run_result.py tests/test_legacy_status_boundaries.py docs/lifecycle-schema.md
git commit -m "feat: add canonical run result envelope"
```

### Task 4: Adopt RunResult across local, background, GUI, CLI, receipts, and browser

**Files:**
- Modify: `opaihub/ask.py`
- Modify: `opaihub/background_runs.py`
- Modify: `opaihub/gui_pipeline.py`
- Modify: `opai/app_state.py`
- Modify: `opai/cli.py`
- Modify: `opaihub/receipt.py`
- Modify: `opai/assets/web/message-state.js`
- Test: `tests/test_run_result_integration.py`
- Test: `tests/test_background_persistence.py`

**Interfaces:**
- Consumes: `RunResult.from_payload(payload, context=...)`.
- Produces: `payload["run_result"]` and `payload["run_result_json"]`, canonical terminal/exit/presentation decisions, and output-only legacy status.

- [ ] **Step 1: Write failing cross-path and race tests**

```python
def test_identical_timeout_serializes_identically_in_every_execution_path():
    expected = make_timeout_result().to_json()
    self.assertEqual(local_timeout()["run_result_json"], expected)
    self.assertEqual(gui_timeout()["run_result_json"], expected)
    self.assertEqual(cli_timeout_json(), expected)
    self.assertEqual(background_timeout()["run_result_json"], expected)

def test_cancel_completion_race_keeps_one_reconciled_result():
    result = finalise_racing_cancel_and_complete()
    self.assertTrue(result.lifecycle["reconciled"])
    self.assertIn(result.lifecycle["state"], {"cancelled", "completed", "needs_attention"})
```

- [ ] **Step 2: Run integration tests and observe the expected failure**

Run: `python -m unittest tests.test_run_result_integration tests.test_background_persistence -v`

Expected: FAIL because the paths currently return independently interpreted status strings.

- [ ] **Step 3: Attach one result before every presentation boundary**

```python
def with_run_result(payload: Mapping[str, Any], *, context: RunResultContext) -> dict[str, Any]:
    result = RunResult.from_payload(payload, context=context)
    return {**payload, "run_result": result.to_dict(), "run_result_json": result.to_json()}
```

Invoke this after provider collection and before GUI phase, background
finalization, CLI exit/rendering, receipt building, and browser mapping. Use
the envelope—not `status` or `completion_state`—for terminal/retry/policy
decisions. Preserve partial/cancelled costs and evidence; background/import
records use only the legacy adapter.

- [ ] **Step 4: Run cross-path result tests**

Run: `python -m unittest tests.test_run_result_integration tests.test_background_persistence tests.test_run_state_parity -v`

Expected: PASS; same input produces the same state, reason, recovery,
evidence, and JSON in every supported surface.

- [ ] **Step 5: Commit result adoption**

```bash
git add opaihub/ask.py opaihub/background_runs.py opaihub/gui_pipeline.py opai/app_state.py opai/cli.py opaihub/receipt.py opai/assets/web/message-state.js tests/test_run_result_integration.py tests/test_background_persistence.py tests/test_run_state_parity.py
git commit -m "feat: project execution outcomes through RunResult"
```

### Task 5: Enforce drift prevention, property coverage, and complete verification

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/ci-selfhosted.yml`
- Create: `tests/test_lifecycle_property_sequences.py`
- Create: `tests/test_lifecycle_architecture.py`
- Modify: `docs/lifecycle-schema.md`

**Interfaces:**
- Consumes: generated fixtures, current/previous schema inputs, reducers, and result adapters.
- Produces: CI stale-output failure, 10,000 adversarial sequence evidence, static boundary enforcement, compatibility/migration documentation.

- [ ] **Step 1: Write failing property and architecture tests**

```python
def test_ten_thousand_sequences_never_regress_a_terminal_or_bypass_completion_guard():
    for sequence in generated_sequences(seed=612618, count=10_000):
        final_state, evidence = reduce_sequence(sequence)
        self.assertFalse(sequence_mutated_terminal(sequence))
        self.assertFalse(final_state == "completed" and not evidence["completion_guard"])

def test_runtime_modules_cannot_use_legacy_status_as_authority():
    assert_no_legacy_status_authority(RUNTIME_BOUNDARY_FILES)
```

- [ ] **Step 2: Run enforcement tests and observe the expected failure**

Run: `python -m unittest tests.test_lifecycle_property_sequences tests.test_lifecycle_architecture -v`

Expected: FAIL because generated drift and legacy-authority rules are not yet enforced.

- [ ] **Step 3: Add deterministic enforcement**

```yaml
- name: Generated lifecycle drift check
  run: python scripts/generate_lifecycle.py --check
```

Test current/previous version fixtures, duplicate/reordered/stale/terminal
attack sequences, cancellation, and repair. Document migration map, usage
telemetry, removal gate, and schema incompatibility behavior in generated docs.

- [ ] **Step 4: Run focused enforcement checks**

Run: `python scripts/generate_lifecycle.py --check; python -m unittest tests.test_lifecycle_generation tests.test_lifecycle_property_sequences tests.test_lifecycle_architecture -v`

Expected: PASS, including at least 10,000 generated sequences.

- [ ] **Step 5: Run complete fresh verification**

Run: `python -m unittest discover -s tests; python -m ruff format --check .; python -m ruff check .; npm ci; npm run test:unit; git diff origin/main...HEAD --check; git diff --check`

Expected: every command exits 0, generated files are current, and no
out-of-scope or whitespace changes remain.

- [ ] **Step 6: Commit enforcement and validation evidence**

```bash
git add .github/workflows/ci.yml .github/workflows/ci-selfhosted.yml tests/test_lifecycle_property_sequences.py tests/test_lifecycle_architecture.py docs/lifecycle-schema.md docs/superpowers/plans/2026-08-02-issues-612-618-lifecycle-result-truth.md
git commit -m "test: enforce lifecycle and result contract drift checks"
```
