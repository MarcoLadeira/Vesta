# Verification Execution and Evidence Engine Design

**Issue:** #539 (child of #522)
**Status:** Approved through the user's standing implementation approval

## Goal

Execute an already-resolved `VerificationPolicy` through one hermetic runner,
preserve immutable, redacted evidence for every attempt, and derive the
verification outcome without consulting provider text, changed-file counts, or
presentation state.

## Scope and boundaries

This design consumes the versioned policy and repository safety handle delivered
by #538 and #521. It does not infer commands from `package.json`, CI files, or
provider output. A policy check with no explicit argv is represented as
`unavailable`; it is never guessed or treated as passing.

#539 owns local structured check execution, evidence storage, manifest
validation, and the verification-to-completion adapter. #523 later consumes the
same manifest for PR/CI/merge delivery. #527 remains the authority for broad
data classification; this engine reuses the existing secret redactor and stores
only bounded local diagnostics.

## Architecture

`opaihub.verification_execution` is the only module allowed to create a
verification result. It contains immutable records for an execution context,
attempt, check record, artifact reference, evidence manifest, and verification
verdict.

1. **Execution context** binds a task/run id, the canonical worktree, the
   previously captured `RepositoryHandle`, and a minimal environment allowlist.
   It rejects a non-Git context, a changed/escaped cwd, a stale repository
   handle, non-argv commands, and attempts outside the declared environment.
2. **Runner** reads `PolicyCheck.command` only. It invokes argv without a shell,
   starts an isolated process group, caps output, applies a wall-clock timeout,
   cancels or tears down the owned process tree, and records whether teardown
   was confirmed. No output, command-not-found, cancellation, timeout, and
   launch failures become typed check outcomes rather than exceptions or
   successes.
3. **Evidence storage** writes redacted, bounded per-attempt output under
   `.opaihub/verification-evidence/<task>/<run>/`. The artifact digest, output
   digest, policy digest, repository fingerprint, tool/runtime facts, and check
   records form a canonical JSON manifest. Atomic writes make the manifest safe
   to recover after an application restart.
4. **Verdict resolver** revalidates the manifest digest and every artifact
   digest before deriving `verified`, `partially_verified`, `blocked`, `failed`,
   `cancelled`, `timeout`, or `unverified`. A required check is fully verified
   only when its terminal attempt passed, all required evidence/artifact
   references are intact, and it had no earlier failed attempt. A retry that
   later passes is explicitly flaky and remains not fully verified under the
   current policy. Waived, skipped, unavailable, absent, or damaged required
   checks are never fully verified.

## Trusted data flow

```text
VerificationPolicy + RepositoryHandle
             |
             v
  VerificationExecutionContext -- validates canonical worktree/environment
             |
             v
  Process-group runner -- attempts + bounded/redacted output artifacts
             |
             v
  immutable EvidenceManifest -- verifies digest/artifact integrity
             |
             +--> verification verdict --> completion adapter / GUI / CLI / receipt
```

The manifest is the sole input to all verification consumers. Legacy completion
evidence remains compatible for non-managed/read-only tasks, but edit-capable
runs that carry a verification manifest cannot reach `completed` unless the
manifest verdict is `verified`.

## Check outcomes and retries

Each required check has a `CheckRecord` even when no command starts. Attempt
statuses are `passed`, `failed`, `timeout`, `cancelled`, `unavailable`, or
`blocked`; record statuses additionally represent `skipped`, `waived`, and
`artifact_lost`. Every retry appends a new immutable attempt. A prior failure
sets `flake_suspected` if a later attempt passes. The current #538 policy does
not authorize a flaky-success exception, so this derives a partial verdict.

Waivers must contain an actor, reason, source, and scope. The engine records
their consequence as not fully verified. It accepts no waiver by default;
callers must construct one explicitly and future #526 authority work decides
whether such a waiver is authorized.

## Environment, platform, and artifacts

The environment fingerprint records OS, architecture, Python runtime, selected
allowlisted environment key names/values after redaction, repository identity,
worktree SHA, dependency-lock digests, and relevant policy/config digests. The
runner uses a minimal inherited environment needed to locate tools and enforces
`cwd == canonical_worktree`. POSIX uses a new session and process-group kill;
Windows uses a new process group and `taskkill /T` fallback. The manifest always
states teardown evidence and enforcement availability rather than claiming an
unsupported resource control.

Output is UTF-8 replacement-decoded, redacted before persistence, capped at
64 KiB per stream, and includes truncation plus digest metadata. Artifacts are
references inside the run evidence directory only; a missing or digest-mismatched
artifact invalidates the relevant check.

## Surface integration

`opai verify run` resolves a policy, persists its policy artifact, executes it,
then emits the manifest and derived verdict as JSON or a concise human summary.
The GUI pipeline runs the same engine after an edit-capable provider result with
the captured repository handle and inserts the unchanged manifest payload into
the result, receipt, workflow/checkpoint state, and run summary. The completion
adapter maps `verified` to the existing `completed` compatibility verdict;
every other verification verdict maps to a non-completed outcome with a typed
reason. No provider field can override that mapping.

## Failure handling

- Invalid/missing policy or repository binding: `blocked`.
- No declared command or missing executable: `unavailable` and `unverified`.
- Non-zero exit: `failed`.
- Expired timeout: `timeout`, with teardown result retained.
- Caller cancellation: `cancelled`.
- Missing/truncated-required evidence or digest mismatch: `unverified`.
- A manually waived/skipped/flaky required check: `partially_verified`.

## Verification strategy

Hermetic tests inject a subprocess factory/clock/platform abstraction and cover
command crash, timeout/process teardown, cancellation, missing executable,
artifact loss, output redaction/truncation, retry/flaky behavior, worktree
escape, restart/loading, and provider/presentation false-success attempts.
CLI and GUI integration tests prove all surfaces serialize the exact same
manifest digest and verdict. Static lint, formatting, targeted tests, and the
repository suite run as final gates; unrelated inherited failures are reported
with an `origin/main` reproduction instead of being attributed to this branch.
