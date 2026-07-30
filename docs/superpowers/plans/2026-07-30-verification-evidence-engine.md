# Verification Evidence Engine Implementation Plan

> For agentic workers: use superpowers:executing-plans task-by-task; steps use checkbox syntax.

Goal: execute versioned verification policies into immutable, redacted evidence manifests and derive non-forgeable verification verdicts for #539.

Architecture: opaihub.verification_execution owns records, canonical-worktree validation, argv execution, persistence, and recomputation. completion, CLI, GUI, and run summary consume one manifest rather than provider claims.

Tech stack: Python 3.10+, subprocess/hashlib/json, existing OPai atomic I/O, repository safety, redaction, CLI/GUI pipeline, pytest.

## Global Constraints

- Execute only explicit PolicyCheck.command argv; never parse project scripts or provider text into commands.
- Use shell=False, canonical worktree containment, a bounded environment, bounded redacted output, and process-group teardown.
- Retain every retry; flakes, waivers, skips, unavailable commands, and missing evidence cannot become fully verified.
- Persist under .opaihub/verification-evidence and validate manifest/artifact SHA-256 digests before a verdict.
- Keep legacy completion for non-managed/read-only work; a managed manifest is authoritative for edit completion.
- #523 owns GitHub delivery; #527 owns broad data classification; #526 owns authorization policy.

---

### Task 1: Immutable evidence contract

Files: create opaihub/verification_execution.py; create tests/test_verification_execution.py.

Interfaces: define CheckStatus, VerificationVerdict, EnvironmentFingerprint, OutputReference, ArtifactReference, VerificationAttempt, CheckRecord, VerificationManifest, and verification_verdict(manifest). Consume VerificationPolicy and PolicyCheck.

- [ ] Step 1: Write failing tests for absent required check -> unverified, failed then passed required check -> partially_verified, invalid manifest digest -> ValueError, and waived required check -> partially_verified.
- [ ] Step 2: Run python -m pytest tests/test_verification_execution.py -q; confirm import/behavior failure.
- [ ] Step 3: Implement frozen dataclasses, unique check IDs, ordered attempt retention, canonical sorted JSON, and SHA-256 digest excluding the digest field.
- [ ] Step 4: Run the contract tests; expect pass for verified, partial, blocked, failed, cancelled, timeout, unavailable, waiver, and flaky behavior.
- [ ] Step 5: Commit feat(verify): define evidence manifest contract.

### Task 2: Canonical context and hermetic runner

Files: modify opaihub/verification_execution.py and tests/test_verification_execution.py.

Interfaces: add VerificationExecutionContext.from_repository_handle and execute_policy(policy, context, cancel=None, executor=None). Consume RepositoryHandle and existing command redaction.

- [ ] Step 1: Write failing tests: a root outside the repository handle is blocked; a missing executable is unavailable; a non-zero command fails; timeout retains redacted output and teardown evidence; cancellation creates a cancelled attempt.
- [ ] Step 2: Run python -m pytest tests/test_verification_execution.py -k worktree -q; confirm failure.
- [ ] Step 3: Run only nonempty policy argv through Popen with shell=False, an isolated process group, bounded allowlisted environment, capped decoding, cancellation polling, wall-clock timeout, and verified teardown. No command becomes unavailable.
- [ ] Step 4: Run the runner tests for worktree/timeout/executable/output/cancel; expect pass including injected Linux/Windows/macOS platform fixtures.
- [ ] Step 5: Commit feat(verify): execute policy checks with bounded evidence.

### Task 3: Atomic artifacts and restart integrity

Files: modify opaihub/verification_execution.py and tests/test_verification_execution.py.

Interfaces: add persist_verification_manifest(root, manifest), load_verification_manifest(path), and EvidenceManifestReference. Consume atomic_write_text, interprocess_transaction, and state_dir.

- [ ] Step 1: Write failing tests: persisted manifest round-trips with redacted output and artifact digests; missing output artifact, invalid JSON, root escape, and digest mismatch derive unverified.
- [ ] Step 2: Run python -m pytest tests/test_verification_execution.py -k persisted -q; confirm failure.
- [ ] Step 3: Atomically write capped output references and canonical manifest below the verified state root. Loader and verdict resolver fail closed when integrity cannot be proven.
- [ ] Step 4: Run persistence/restart tests; expect pass.
- [ ] Step 5: Commit feat(verify): persist reproducible evidence manifests.

### Task 4: Completion and summary adapters

Files: modify opaihub/completion.py, opaihub/run_summary.py, tests/test_completion_contract.py, and tests/test_verification_execution.py.

Interfaces: consume a verification_manifest mapping in evaluate_completion. Map verified to completed; partially_verified/unverified to partial; blocked, failed, cancelled, and timeout to their existing CompletionVerdict equivalents.

- [ ] Step 1: Write a failing false-success test where an answered provider result with a passing tool_trace carries an unverified manifest and must be partial.
- [ ] Step 2: Run python -m pytest tests/test_completion_contract.py -k manifest -q; confirm failure.
- [ ] Step 3: Validate the manifest before mapping it and render only manifest digest/check summary in the run receipt, never raw output.
- [ ] Step 4: Run completion and evidence tests; expect pass.
- [ ] Step 5: Commit feat(verify): derive completion from evidence manifests.

### Task 5: CLI and GUI integration

Files: modify opai/cli.py, opaihub/gui_pipeline.py, opaihub/checkpoints.py, opaihub/workflow_state.py, tests/test_positioning_and_cli.py, tests/test_pipeline_routing_and_safety.py, and tests/test_completion_contract.py.

Interfaces: add opai verify run --task TASK [--json]. GUI results, receipts, checkpoint state, and workflow state carry the exact verification_manifest reference/digest produced by the executor.

- [ ] Step 1: Write failing CLI test for verify run persisting a manifest and returning non-zero when required check command is absent.
- [ ] Step 2: Write failing GUI test showing a provider-declared passing test cannot override an unavailable manifest.
- [ ] Step 3: Run the integration tests and confirm failure.
- [ ] Step 4: Implement CLI execution. After an edit-capable provider result, reuse the captured repository handle to execute and persist one policy manifest before decorate/finalization. Preserve terminal blocked provider behavior without any fabricated check pass.
- [ ] Step 5: Run CLI, GUI, checkpoint, workflow, receipt, and completion parity tests; expect equal digest/verdict across every surface.
- [ ] Step 6: Commit feat(verify): expose evidence verdicts across surfaces.

### Task 6: Documentation, review, and validation

Files: modify README.md and CHANGELOG.md.

- [ ] Step 1: Document policy dry run versus verify run execution, local evidence storage, and the distinction between verified evidence and a model claim.
- [ ] Step 2: Review every subprocess, output persistence, checksum, and verdict diff for shell execution, raw secret persistence, or provider-controlled completion.
- [ ] Step 3: Run focused test suite: python -m pytest tests/test_verification_execution.py tests/test_completion_contract.py tests/test_positioning_and_cli.py tests/test_pipeline_routing_and_safety.py -q.
- [ ] Step 4: Run format/lint/diff checks, then the full suite once. Any inherited failure must be reproduced on origin/main before publishing.
- [ ] Step 5: Commit docs: explain verification evidence manifests.
