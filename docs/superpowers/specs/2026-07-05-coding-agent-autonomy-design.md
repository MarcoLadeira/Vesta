# OPai Coding-Agent Autonomy Design

## Goal

Make OPai infer the user's current coding intent, operate in the active repository with proportionate autonomy, protect unrelated work, and expose truthful workflow state without weakening destructive-action, secret, paid-service, or production safeguards.

The implementation is a focused coding-workbench vertical slice: OPai owns a durable runtime state machine and gives providers bounded, structured actions instead of delegating workflow truth to prose prompts.

## Root cause

OPai currently splits intent across task focus, run mode, provider CLI flags, generated instructions, and command policy. Those layers do not share one precedence model. A stale read-only focus can prepend “Do not modify files” while Safe Auto separately permits edits; `approve-edits` is read-only in the pipeline despite its label; and static Git policy asks again for push or merge even when the current request already authorizes that workflow.

Repository handling persists recent folders but not a canonical Git identity. It does not retain the remote, branch, dirty paths, or whether dirty paths overlap the requested change. GitHub issue selection and PR/merge orchestration are not implemented in the application layer. The activity UI has strong primitives, but it cannot show an effective user-intent mode or durable workflow outcome.

## Architecture

### Durable runtime and task packet

`opaihub.agent_runtime` drives a validated state machine from intent and repository resolution through context, implementation, tests/repair, diff review, PR checks, merge, and terminal outcomes. Every state contains a user-facing message, machine metadata, blocker, next actions, timestamps, and immutable event history. State is persisted atomically; a privacy-safe JSONL workflow ledger records hashes and redacted metadata, never raw prompts or credentials.

`opaihub.task_packet` builds the provider-neutral context packet: current request, effective mode, repository identity, issue, dirty assessment, relevant files, constraints, capability boundaries, done criteria, test strategy, last failure, and next action. Providers receive this packet plus the capability contract; OPai remains the source of workflow state.

### Agent-computer interface

`opaihub.aci` exposes structured observations for repository overview, search, bounded file reads, patch proposals/application, diff/status, commands/tests, failure parsing, worktree operations, and GitHub workflow actions. It uses argv-only subprocesses, fixed timeouts, hidden Windows processes, output limits, redaction, and injected runners. Destructive Git operations and production/auth mutations are not exposed as ordinary actions.

### Test/repair and safety gates

`opaihub.test_loop` models discover → focused tests → full tests → parsed failure → bounded repair retries. `opaihub.safety_gates` evaluates secrets, risky files, destructive commands, production/auth scope, unrelated diffs, tests, branch/conflicts, and PR checks. Merge remains fail-closed.

### Agent policy

`opaihub.agent_policy` is the single source of truth for Explain, Review, Implement, Ship, and Dangerous modes. It classifies the latest explicit instruction, records allowed capabilities, and renders a provider-neutral capability contract. Explicit current verbs such as “fix,” “implement,” “make a PR,” and “merge after tests pass” override generic or earlier boilerplate. Dangerous operations always require a fresh confirmation.

The policy is derived inside the GUI pipeline from the raw user message. UI focus remains a hint, never an authority that can contradict a newer explicit request. Output-format instructions remain presentational.

### Repository context

`opaihub.repo_context` resolves a selected folder to its enclosing Git worktree when present, reads branch and remote metadata without opening a terminal, snapshots dirty paths, and persists the active repository under OPai's local state directory. Dirty paths are classified as unrelated when they do not overlap intended paths and conflicting when they do. Unknown intended paths yield a conservative “needs inspection” result rather than pretending the tree is clean.

An isolated-worktree helper creates a `codex/` branch from the requested base only after validating the target path and conflict classification. It never resets, cleans, deletes, or overwrites the source checkout.

### GitHub workflow

`opaihub.github_workflow` contains pure issue scoring plus an injected GitHub adapter. “Small important issue” favors release blockers, user impact, testability, and bounded size while penalizing architectural scope and risk. The adapter wraps structured `gh` commands and can be replaced with a fake in tests. PR creation links the selected issue. Merge requires passing tests/checks, the expected branch, no secrets, no unrelated files, and no conflicts.

### Pipeline and provider contract

The GUI passes the raw message to `handle_gui_message`. The pipeline resolves policy, repo context, and workflow state, then builds a capability contract before invoking the selected provider. The contract says exactly which repository operations the current request authorizes and distinguishes them from actions that still require confirmation. Provider environment sanitization and structured error handling are unchanged.

### UI and status

Boot and response payloads expose active repository path, branch, remote, dirty state, effective agent mode, workflow phase, test status, PR URL, merge status, and blockers. The existing workspace header and activity timeline render this state. No modal is introduced for normal implementation steps.

### Expanded workbench surface

The mockable GitHub adapter also reads linked/open PRs and check state, updates PR metadata, posts status comments, and performs guarded merge operations. Provider JSON/stream-JSON transports remain intact while their events are normalized into OPai observations. The cockpit payload adds state history, issue, blocker, next actions, changed files, test/failure observations, provider/cost metadata, and merge gates.

Versioned local templates define issue triage, bug fix, feature, review, refactor, dependency update, test repair, CI repair, docs, PR preparation, and guarded merge workflows. `tests/agent_evals` contains deterministic OPaiBench scenarios without cloud calls.

## Safety invariants

- Never expose credential values or raw secret-bearing diagnostics.
- Never force-push, hard-reset, clean, delete user files/branches, or mutate production without confirmation.
- Never merge unless all ship gates pass.
- Never overwrite dirty paths that overlap the requested change.
- Preserve provider child-environment sanitization and hidden subprocess behavior.
- Keep Explain and Review read-only.

## Verification

Unit tests cover the fourteen requested behavioral cases. Integration tests verify pipeline policy resolution, repository persistence, GUI payloads, and adapter command construction with fakes. The full Python suite, Ruff, and available web unit tests run before publication.
