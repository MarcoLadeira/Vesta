# Cache Correctness and Bounded Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OPai cache reuse content-aware, bounded, expiring, observable, and safe to bypass when repository state cannot be proven complete.

**Architecture:** Centralize cacheability in a bounded Git content-fingerprint assessment. Preserve compatibility wrappers for existing fingerprint/cache callers, then layer a schema-versioned result-cache envelope and local cache-outcome events over it. Every uncertainty becomes a cache miss/bypass, not stale reuse.

**Tech Stack:** Python 3.10+, unittest/pytest, Git porcelain/NUL output, SHA-256, atomic `os.replace`, append-only JSONL ledger, Ruff.

## Global Constraints

- No raw prompt, file content, absolute repository path, or credential is persisted in cache metadata or ledger events.
- Cache correctness outranks cache-hit rate: unknown state always bypasses.
- Clean Git repositories must not trigger a repository file walk or content reads.
- Result cache expiry is exactly 3,600 seconds by default and stored per entry.
- Cache outcome events are not model-call events and must not affect spend accounting.
- Use red/green TDD for every behavior change and retain existing public function return types where callers depend on them.

---

### Task 1: Add a bounded repository fingerprint assessment

**Files:**
- Modify: `opaihub/evidence_cache.py`
- Modify: `tests/test_evidence_cache.py`
- Create: `tests/test_result_cache_correctness.py`

**Interfaces:**
- Produces: `FingerprintLimits`, `RepoFingerprint`, and
  `assess_repo_fingerprint(project_root, *, limits=DEFAULT_FINGERPRINT_LIMITS)`.
- Keeps: `repo_fingerprint(project_root) -> str` as a digest-only compatibility wrapper.

- [ ] **Step 1: Write failing fingerprint tests**

  Add fixture helpers that initialize and commit a Git repository. Add tests
  that modify `app.py` twice at the same path and assert two different
  `assessment.digest` values; add a safe untracked-file test; add a clean-path
  test that patches `Path.open` and asserts it is never used; and add one test
  per bypass reason using tiny injected `FingerprintLimits` values.

- [ ] **Step 2: Run the new tests and verify red**

  Run:

  ```powershell
  python -m pytest tests/test_evidence_cache.py tests/test_result_cache_correctness.py -q
  ```

  Expected: same-path dirty-content assertions fail because the current digest
  only includes porcelain paths, and no structured assessment exists.

- [ ] **Step 3: Implement the assessment and bounded manifest**

  Add immutable dataclasses and a Git helper that returns NUL-safe output. For
  a clean status, hash `git`, HEAD, and empty status without opening source
  files. For dirty state, hash sorted relative path, byte count, and SHA-256
  digest for changed tracked plus non-ignored untracked files. Return a
  non-cacheable assessment before any cache read/write for all documented
  unsafe conditions.

- [ ] **Step 4: Verify green and preserve evidence-cache behavior**

  Run:

  ```powershell
  python -m pytest tests/test_evidence_cache.py tests/test_result_cache_correctness.py -q
  ```

  Expected: content changes invalidate safely; clean paths avoid file reads;
  bounded failures have deterministic bypass reasons.

### Task 2: Version and expire result-cache entries atomically

**Files:**
- Modify: `opaihub/result_cache.py`
- Modify: `tests/test_local_execution.py`
- Modify: `tests/test_result_cache_correctness.py`

**Interfaces:**
- Produces: `DEFAULT_TTL_SECONDS = 3600`, `RESULT_CACHE_VERSION = 2`, and
  `lookup_with_meta(...) -> CacheLookup`.
- Keeps: `cache_key(...) -> str`, `lookup(...) -> dict | None`, and
  `store(...)` compatibility behavior for cacheable repositories.

- [ ] **Step 1: Write failing result-envelope tests**

  Add tests that store with a fixed UTC clock, lookup before and after expiry,
  rewrite an entry with schema version 1, malformed JSON, and malformed
  timestamps, and assert no answer returns. Add a threaded writer/reader test
  that repeatedly stores distinct complete answers while readers only receive
  dictionaries containing complete required fields.

- [ ] **Step 2: Run envelope tests and verify red**

  Run:

  ```powershell
  python -m pytest tests/test_local_execution.py tests/test_result_cache_correctness.py -q
  ```

  Expected: current entries have no schema/expiry and direct writes expose the
  old format.

- [ ] **Step 3: Implement the versioned entry and atomic write**

  Validate cacheability before deriving a read/write path. Serialize schema,
  created/expires timestamps, key, model, and answer to a temporary file in
  the destination directory, flush it, then replace the destination with
  `os.replace`. Make `lookup_with_meta` return explicit outcomes and let
  `lookup` return its entry only on a valid hit.

- [ ] **Step 4: Verify green**

  Run:

  ```powershell
  python -m pytest tests/test_local_execution.py tests/test_result_cache_correctness.py -q
  ```

  Expected: old/corrupt/expired entries are safe misses, concurrent reads never
  parse partial JSON, and safe near-duplicate tasks still hit.

### Task 3: Propagate safe bypasses and local cache evidence

**Files:**
- Modify: `opaihub/ledger.py`
- Modify: `opaihub/ask.py`
- Modify: `opaihub/evidence_cache.py`
- Modify: `opaihub/intent_router.py`
- Modify: `tests/test_savings_honesty.py`
- Modify: `tests/test_ai_model_bugfixes.py`
- Modify: `tests/test_evidence_cache.py`
- Modify: `tests/test_result_cache_correctness.py`

**Interfaces:**
- Produces: `record_cache_lookup(project_root, task, *, cache_kind, outcome,
  reason=None, age_seconds=None, avoided_model_call=False)`.
- Produces: cache metadata on `run_ask` results without altering spend/event
  multiplicity contracts.

- [ ] **Step 1: Write failing integration tests**

  Add a result-cache hit test that asserts exactly one `cache_lookup` event with
  `outcome == "hit"` and `avoided_model_call is True`. Add a dirty oversized
  input test that asserts runner execution, `outcome == "bypass"`, no result
  entry, and no model-call spend. Add evidence-cache and intent-router tests
  asserting uncacheable assessment skips reuse instead of returning a stale
  value.

- [ ] **Step 2: Run integration tests and verify red**

  Run:

  ```powershell
  python -m pytest tests/test_savings_honesty.py tests/test_ai_model_bugfixes.py tests/test_evidence_cache.py tests/test_result_cache_correctness.py -q
  ```

  Expected: no cache lookup event exists and unsafe repository state can still
  reach the prior cache API.

- [ ] **Step 3: Record cache outcome separately from route and spend**

  Implement `record_cache_lookup` with the existing `EVENT_CACHE`, only
  task-hash-derived ledger fields, and no raw content. In `run_ask`, use
  `lookup_with_meta` on read-only requests, record one cache event when
  `record=True`, use a task-only hash in the base result, and retain one route
  decision only after a real answer or safe cache hit. Make evidence and GUI
  work caches skip both read and write/reuse when `cacheable` is false.

- [ ] **Step 4: Verify green**

  Run:

  ```powershell
  python -m pytest tests/test_savings_honesty.py tests/test_ai_model_bugfixes.py tests/test_evidence_cache.py tests/test_result_cache_correctness.py -q
  ```

  Expected: cache evidence distinguishes hit/miss/bypass/expiry without
  affecting actual spend and no unsafe state is reused.

### Task 4: Regression, performance, and release verification

**Files:**
- Verify: all Task 1–3 files
- Modify: `docs/ALPHA_READINESS_2026-07-12.md` with implemented issue status
  and measured verification evidence.

- [ ] **Step 1: Run cache, GUI, routing, and ledger suites**

  Run:

  ```powershell
  python -m pytest tests/test_local_execution.py tests/test_result_cache_correctness.py tests/test_evidence_cache.py tests/test_ai_model_bugfixes.py tests/test_pipeline_routing_and_safety.py tests/test_savings_honesty.py tests/test_cost_ledger.py tests/test_free_models.py -q
  ```

- [ ] **Step 2: Measure clean and dirty fingerprint paths**

  Run a local `perf_counter` loop over 10 clean and 10 bounded-dirty calls.
  Record mean milliseconds and confirm the clean path opens no source files.
  Do not make a performance claim without the measured output.

- [ ] **Step 3: Run static/release gates**

  Run:

  ```powershell
  python -B -m ruff format --check .
  python -B -m ruff check --no-cache .
  python -B -m bandit -r opai opaihub opcoding -q
  python -B -m opaihub validate
  git diff --check
  ```

- [ ] **Step 4: Commit the bounded cache correction**

  Run:

  ```powershell
  git add opaihub/evidence_cache.py opaihub/result_cache.py opaihub/ledger.py opaihub/ask.py opaihub/intent_router.py tests/test_local_execution.py tests/test_result_cache_correctness.py tests/test_evidence_cache.py tests/test_savings_honesty.py tests/test_ai_model_bugfixes.py docs/ALPHA_READINESS_2026-07-12.md
  git commit -m "fix(cache): make reuse content-aware and expiring"
  ```

  Expected: the commit has cache correctness code, regression tests, and
  release-evidence documentation only.
