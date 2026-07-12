# OPai Free-Launch Alpha Trust Programme Design

- **Date:** 2026-07-12
- **Status:** Approved for implementation
- **Repository:** `MarcoLadeira/OPai`
**Baseline:** `f4c27b715d83b9febbcaab59d1d58dec8acf28ab`

## Decision

OPai will launch as a fully free product. Paid editions, license enforcement,
and closed-source fulfillment are not alpha gates. Pricing will be introduced
only after real usage shows which future capabilities create durable paid
value.

The first implementation programme is therefore **trust-first**: make Safe
Auto enforceable, make completion status truthful, and make cost accounting
count each model call once. Packaging and automatic release proof remain alpha
blockers, but shipping known permission and accounting defects would make a
package less trustworthy rather than more ready.

## Verified baseline

- The repository is clean on `main` at the baseline commit.
- The Python suite passes: 1,503 tests, 0 failures, 2 skips.
- Ruff lint and format checks, Bandit, registry validation, and Python module
  compilation pass.
- The JavaScript dependency audit reports zero known vulnerabilities, but the
  locked Vitest and Playwright suites were not installed in the baseline
  environment.
- Both hosted and self-hosted GitHub workflows are manual-only. The current
  `main` commit has no GitHub check runs or statuses.
- The desktop wheel smoke builds only the core wheel and calls a Qt-free state
  builder. It does not install the desktop extra, render the real GUI, or
  produce Windows/macOS application artifacts.

## Product definition

### Target users

- Solo developers, indie founders, freelancers, and small teams already using
  Claude, Codex, Copilot, free APIs, or local models.
- Users who want lower AI cost without surrendering control, correctness,
  privacy, or visibility.

### Core problem

AI coding tools can spend money, consume context, and execute powerful tools
without giving the user a reliable account of what happened. Existing tools
also repeat repository analysis and expensive model work that could be cached,
retrieved, or handled deterministically.

### Product promise

OPai is the local-first cost and control plane around coding agents: it chooses
the cheapest safe execution path, constrains what that path may do, and returns
an understandable, locally verifiable receipt.

### Free alpha scope

- One shared GUI/CLI execution core.
- Ask, Plan, Safe Auto, Approve Edits, and explicitly pinned Full Auto modes.
- Connected account, free API, and local-model selection.
- Real cancellation, dirty-worktree protection, bounded repository tools,
  diff review, cost receipts, and local audit state.
- Honest degradation when a selected provider cannot perform the requested
  operation safely.
- Reproducible installation and validation evidence for Windows and macOS
  before the alpha is called release-ready.

### Explicit non-goals for the free alpha

- Free/Plus/Unlimited entitlements, quotas, or offline licenses.
- Closed-source commercial fulfillment.
- Multi-model comparison, team billing, or a marketplace.
- A complete Lovable-style app builder beyond the existing deterministic
  scaffold/customize/verify foundation.
- Pretending local models can edit when their runner lacks bounded tool calls.

## Confirmed trust failures

### 1. Copilot Safe Auto is not safely bounded

`AccountRunner.build_command()` adds `--allow-all-tools` to Copilot in both
Safe Auto and Full Auto. The production account path does not place Copilot
behind OPai's repository tool executor or command sandbox. A prompt contract
cannot substitute for an enforceable tool boundary.

**Impact:** a mode named Safe Auto can expose shell, network, Git, and other
native Copilot tools without granular OPai approval.

### 2. Ordinary implementation intent includes push and PR capabilities

The generic IMPLEMENT policy includes `push` and `create_pr`, and that policy is
inserted into native-provider prompts even when the user only asks for a local
code change.

**Impact:** the authorization represented to a native agent is broader than the
latest explicit user request.

### 3. Free API cost is recorded more than once

A successful GUI free-model call currently records a route in
`run_explicit_model`, a model call in `_ask_free_model`, and another GUI route.
Budget and ledger summaries add route estimates and model-call costs together.

**Impact:** spend, routed-task counts, baseline cost, and savings can be
inflated, contradicting OPai's central cost-truth promise.

### 4. Local implementation can report false success

The GUI's local path calls the prose-only `run_ask`, maps
`answered_locally` to `answered`, reports no changed files, and advances an
implementation runtime toward diff review.

**Impact:** a user can request a code change and receive a successful-looking
answer even though the selected runner had no editing capability.

### 5. Answer caches can survive dirty-content changes

The repository fingerprint includes Git HEAD and porcelain status lines, but
not the contents of already-dirty files. Result-cache entries have no expiry.

**Impact:** a changed dirty file can reuse an answer computed for older file
contents. This is a P1 cache-correctness child, not part of the first code
patch.

## Approaches considered

### A. Trust-first programme — selected

Constrain provider capabilities, make unsupported edit paths fail honestly,
and establish one authoritative spend event per model call.

**Why selected:** it addresses possible user harm, permission overreach, core
task false success, and cost-truth defects before adding reach or polish.

### B. Release-first programme

Restore enforceable CI and build signed three-platform desktop artifacts.

**Why deferred:** release proof is necessary, but it should validate a safe and
truthful execution core. Signing/notarization also requires external
credentials that are not needed for the first trust patch.

### C. Differentiation-first programme

Wire multi-turn memory, semantic retrieval, local tool calls, and the full
agent lifecycle into production.

**Why deferred:** these are valuable but broader. They increase blast radius
while known permission and accounting invariants remain broken.

## Architecture

### 1. Exact capability authorization

`AgentPolicy` remains the provider-neutral source of intent, but capabilities
become request-exact:

- IMPLEMENT permits repository reads, bounded edits, tests, branch creation,
  and local commits.
- Push and PR capabilities are added only when the current request explicitly
  asks to push or open a pull request.
- SHIP adds merge only when the request explicitly asks to ship or merge, and
  retains the existing merge gates.
- Read-only and dangerous classifications continue to override stale UI focus.

The capability contract shown to a provider must never advertise an operation
that OPai did not derive from the current request.

### 2. Fail-closed provider execution

Native providers require an enforcement profile, not only a prompt:

- Codex retains its read-only/workspace-write sandbox and approval policy.
- Claude retains its existing permission behavior; its effective contract is
  narrowed by the exact intent policy.
- Copilot is read-only through OPai for Ask, Plan, and Approve Edits.
- Copilot edit modes return `capability_mismatch` before process launch because
  its current non-interactive integration exposes only an all-tools bypass.
  OPai will not call that flag from production.
- OPai's bounded free-API repository tool loop remains available for providers
  that support structured tool calls.

This restriction is intentionally conservative. A later child issue may add a
granular Copilot adapter if the CLI exposes enforceable per-tool controls.

### 3. Truthful execution outcomes

Mutation intent and answer intent are distinct:

- A local runner without `complete_with_tools` cannot satisfy an edit request.
- The shared core returns `capability_mismatch` with an actionable explanation
  before checking the answer cache or invoking the model.
- GUI and CLI map that result consistently to a non-success state such as
  `needs_tool_capable_model`.
- No receipt, route success, changed-files claim, quota use, or diff-review
  transition is created for the rejected run.
- Ask and Plan remain available on the same local runner.

Actual local editing is a separate feature: implement bounded Ollama and
OpenAI-compatible tool calling, then remove the mismatch only for runners that
pass the provider-tool contract suite.

### 4. Single-entry cost accounting

The ledger distinguishes decisions from spend:

- A route event records why OPai selected a path and the comparison baseline.
- A model-call event is the authoritative spend event.
- A successful GUI free-model run creates one route event and one model-call
  event. The internal explicit-model helper does not add a second route when
  the GUI owns route recording.
- Budgets sum model-call spend only.
- `estimated_actual_spend_usd` sums model calls only. Route estimates remain
  available as a separately named comparison signal and are never added to
  spend.
- Failed, blocked, confirmation-required, and cancelled calls record no spend.
- Existing JSONL events require no destructive migration; summaries are
  recalculated under the corrected semantics.

### 5. Cache-correctness follow-up

A separate P1 change will hash the contents of tracked dirty and untracked
source files into the repository fingerprint while excluding dependencies,
generated trees, and OPai state. It will include performance fixtures and
explicit bypass rules for oversized/binary files. This avoids mixing a
security/accounting patch with a repository-index rewrite.

## Data flow after the change

1. GUI or CLI submits the current request, model, and run mode.
2. The shared policy resolves read, implement, publish, ship, or dangerous
   intent and produces exact capabilities.
3. The selected provider adapter proves it can enforce those capabilities.
4. If it cannot, OPai returns a typed mismatch without launching a process.
5. If it can, the provider runs within its enforceable boundary.
6. OPai records one route decision and, only after a real call, one model-call
   spend event.
7. Runtime status, receipt, changed files, and next actions derive from the
   verified result rather than the requested mode.

## Error handling and compatibility

- `capability_mismatch` is a stable, typed result rather than a generic runner
  error.
- Messages name the selected provider, the unsupported capability, and a safe
  alternative without exposing credentials or raw prompts.
- Existing Ask/Plan behavior and bounded free-API edit tools remain compatible.
- Copilot editing through OPai becomes intentionally unavailable until it can
  be bounded. Direct interactive Copilot use outside OPai is unaffected.
- Ledger files remain append-only and readable; only aggregation semantics and
  future event multiplicity change.

## Test design

Tests are written before implementation.

### Capability tests

- Generic implementation does not include push, PR, or merge.
- Explicit push/PR text adds only publish capabilities.
- Explicit ship/merge text adds merge requirements.
- Later read-only instructions still override earlier write instructions.
- Copilot Ask/Plan commands contain no all-tools bypass.
- Copilot Safe Auto and Full Auto edit requests fail before process launch.

### Outcome tests

- A local edit request with a prose-only runner returns a typed mismatch.
- The runner is not called, no answer cache is read, and no result is stored.
- GUI and CLI expose the same status and actionable message.
- Ask/Plan still invoke the local runner normally.

### Accounting tests

- One successful GUI free-model call produces one route and one model-call
  event.
- Budget spend equals the model-call event exactly.
- Ledger actual spend excludes route comparison estimates.
- Cancellation, failure, and confirmation-required paths add zero spend.
- Re-reading historical ledgers is deterministic.

### Verification commands

- Targeted pytest suites for policy, provider controls, pipeline routing,
  budgets, and ledger truth.
- `python -B -m unittest discover -s tests`
- `python -B -m ruff format --check .`
- `python -B -m ruff check --no-cache .`
- `python -B -m bandit -r opai opaihub opcoding -q`
- `python -B -m opaihub validate`
- `npm ci`, `npm run test:unit`, and relevant Playwright flows if web behavior
  or status rendering changes.
- `python -B -m opai gui --once` for the local headless smoke.

## Acceptance criteria

- No OPai production path passes Copilot `--allow-all-tools`.
- Safe Auto never launches a provider that cannot enforce its requested edit
  boundary.
- Ordinary implementation intent never advertises push, PR, or merge.
- Explicit PR and ship requests retain the appropriate gated capabilities.
- Unsupported local edits cannot return an answered/success state.
- A successful free-model GUI call is counted once for routing and once for
  spend, with spend derived only from the model call.
- All baseline tests and new regression tests pass without weakening existing
  assertions.
- Documentation and GitHub issues describe the free launch boundary and the
  remaining release blockers honestly.

## Roadmap and backlog design

The GitHub roadmap will use seven outcome-level alpha programmes and link
existing epics/issues rather than duplicate them:

1. Alpha trust and core task truth.
2. Cost efficiency and measurement truth.
3. Release, installation, and cross-platform confidence.
4. Repository intelligence, cache correctness, and memory.
5. GUI/CLI parity, onboarding, and accessibility.
6. Observability, evaluations, and recovery.
7. Post-alpha product expansion, including OPai Build.

The current namespaced taxonomy (`importance:*`, `area:*`, `type:*`,
`status:*`) remains canonical. New slash-style duplicates will not be created.
Completed epic #215 will be reconciled and closed; stale checklists such as
#276 will be refreshed. Existing monetization issues #79, #95, #96, and #97
will be preserved for future evidence-based pricing work but removed from the
free-alpha critical path. Issue #18 (clean cross-platform install), #32
(preflight), #146 (GUI responsiveness), and #264 (CI reliability) remain real
alpha risks.

## Rollback

The code patch is separable into policy, outcome, and accounting commits. A
rollback can revert any layer independently. No destructive ledger migration
is performed. If a provider compatibility regression appears, OPai continues
to fail closed and Ask/Plan remain available while the affected edit adapter is
disabled.
