# Run-Scoped Change Attribution Implementation Plan

**Issue:** [#620](https://github.com/MarcoLadeira/Vesta/issues/620)

**Goal:** Replace path-only `git status` comparisons with durable, fail-closed mutation evidence that can distinguish Vesta writes from pre-existing or concurrent user changes and bind verification and delivery to the exact attributed snapshot.

**Architecture:** Introduce a versioned `ChangeSet` domain in `vestahub/change_attribution.py`. It captures immutable repository, HEAD, index, and worktree identities around mutation intents; a pure reducer classifies each path from the recorded evidence. Runtime callers persist the canonical object and retain `changed_files` only as a compatibility projection. Any missing, malformed, or contradictory observation produces `uncertain`, never Vesta ownership.

## Invariants

- Capture the baseline before the first edit-capable operation and revalidate the repository handle immediately before every mutation.
- Preserve HEAD tree entries, index stages, worktree kind/mode/content identity, canonical raw path identity, and repository/worktree identity separately.
- Attribute a transition to Vesta only when it is explained by a contiguous chain of successful operation records with matching preconditions and postconditions.
- Treat unexplained drift, overlap, probe failure, repository/index movement, symlink or junction substitution, cancellation-late writes, and conflicting evidence as external or uncertain.
- Never reset, clean, delete, or broadly stage user changes. Delivery consumes an explicit authorized manifest and revalidates it before mutation.
- Bind verification, checkpoint recovery, GUI, CLI, receipt, and PR presentation to one serialized `ChangeSet` digest.

### Task 1: Versioned evidence schema and lossless Git identities

**Files:**

- Create `vestahub/change_attribution.py`.
- Create `tests/test_change_attribution.py`.

**TDD steps:**

- [ ] Add failing table-driven tests for clean, staged, unstaged, untracked, deleted, renamed, copied, chmod-only, symlink, binary, conflicted-index, and non-UTF-8 paths.
- [ ] Add immutable schema objects for repository snapshots, per-path HEAD/index/worktree identities, mutation intents/outcomes, and `ChangeSet` serialization.
- [ ] Capture identities with Git plumbing and NUL-delimited inputs; reject paths outside the canonical worktree and preserve index stages independently.
- [ ] Round-trip evidence through canonical JSON and verify stable schema/digest behavior.
- [ ] Run `python -m pytest tests/test_change_attribution.py -q` and commit the green slice.

### Task 2: Conservative attribution reducer

**Files:**

- Modify `vestahub/change_attribution.py`.
- Extend `tests/test_change_attribution.py`.

**TDD steps:**

- [ ] Add failing literal-fixture cases for `user_only`, `vesta_only`, `overlap`, `concurrent_external`, and `uncertain`.
- [ ] Require every Vesta-owned transition to match operation pre/post identities in order; record reasons and evidence references for every classification.
- [ ] Fail closed for missing observations, failed probes, malformed paths, repository/HEAD/index movement, duplicate operation IDs, and late writes after terminal cancellation.
- [ ] Add deterministic property tests for ordering, serialization, and the invariant that incomplete evidence can never become `vesta_only`.
- [ ] Run the focused suite and commit the green slice.

### Task 3: Durable checkpoint and runtime integration

**Files:**

- Modify `vestahub/checkpoints.py`.
- Modify `vesta/app_state.py`.
- Modify `vestahub/gui_pipeline.py`.
- Extend `tests/test_run_checkpoints.py` and the narrow app-state/GUI integration tests selected during implementation.

**TDD steps:**

- [ ] Prove the immutable baseline is persisted before an edit-capable provider/tool call.
- [ ] Persist operation intent before mutation and the observed outcome afterward, using repository-handle revalidation as the mutation gate.
- [ ] Replace `_changed_files`/`_changed_file_identities` ownership decisions with canonical `ChangeSet` capture and classification.
- [ ] Resume or reconcile interrupted operations without inventing ownership; detect writes observed after cancellation.
- [ ] Keep `changed_files` as a derived compatibility field containing only authorized Vesta-attributed paths.
- [ ] Run focused checkpoint/runtime suites and commit the green slice.

### Task 4: Verification snapshot binding

**Files:**

- Modify `vestahub/verification_execution.py`.
- Extend `tests/test_verification_execution.py`.

**TDD steps:**

- [ ] Add failing tests that reject verification evidence captured for a different HEAD, index, worktree, or `ChangeSet` digest.
- [ ] Bind verification context and results to the exact post-change snapshot.
- [ ] Re-probe after verification so tool/test side effects and concurrent writes are classified before delivery.
- [ ] Run the focused verification suite and commit the green slice.

### Task 5: Authorized commit and PR assembly

**Files:**

- Modify the canonical Git/delivery implementation identified by the repository map.
- Add or extend its integration/security tests.

**TDD steps:**

- [ ] Prove pre-existing staged/unstaged/untracked user changes are excluded from Vesta delivery.
- [ ] Stage only an explicit path-and-expected-entry manifest; revalidate the real index immediately before applying it.
- [ ] Reject stale, overlapping, uncertain, out-of-worktree, symlink-swapped, and post-verification drift.
- [ ] Prove no failure path invokes reset, clean, broad staging, or deletion of user work.
- [ ] Run focused delivery/security suites and commit the green slice.

### Task 6: Canonical result and surface projection

**Files:**

- Modify `vestahub/run_result.py`, `vestahub/run_result_projection.py`, and canonical completion/receipt projection modules as required.
- Modify GUI/CLI presentation consumers without reimplementing attribution.
- Extend run-result matrix/property/surface parity tests.

**TDD steps:**

- [ ] Project the validated schema version, digest, classifications, attributed paths, uncertainty, and evidence reference into `RunResult`.
- [ ] Make GUI, CLI, history/receipt, and PR summaries consume the same projection.
- [ ] Add golden parity tests and ensure uncertain attribution forces `needs_attention` and disables automatic delivery.
- [ ] Run focused projection/surface suites and commit the green slice.

### Task 7: Acceptance matrix and release evidence

**Files:**

- Extend `tests/test_change_attribution.py` and relevant integration suites.
- Add acceptance documentation only where the repository's release process requires it.

**TDD and verification steps:**

- [ ] Cover linked worktrees, unborn/detached HEAD, sparse checkout, submodules, case collisions, file/directory replacement, executable-bit changes, large/binary files, cancellation races, concurrent writers, and injected Git/I/O faults.
- [ ] Run targeted suites after every commit and the repository's required full gates before marking the PR ready.
- [ ] Review the complete branch diff for scope, secret exposure, destructive Git behavior, and compatibility leakage.
- [ ] Push each coherent commit, update the draft PR with evidence, and mark it ready only when all required checks pass.
