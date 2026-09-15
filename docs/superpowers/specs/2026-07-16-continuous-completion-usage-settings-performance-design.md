# Continuous Completion, Honest Usage, and Responsive Settings Design

- **Date:** 2026-07-16
- **Status:** Written specification approved by the product owner on 2026-07-16
- **Repository:** `MarcoLadeira/OPai`
- **Target branch:** `codex/alpha-reliability`

## Outcome

Vesta must complete productive coding work without exposing an internal tool-call
allowance as a user-facing limit. It must reduce token waste automatically,
report usage with enough provenance to be trusted, let users remove limits and
reset the visible accounting baseline, render Settings as a clear product
surface, and remain responsive on ordinary Windows workspaces.

This design treats those requests as one reliability programme. The same
unbounded context growth that makes a productive run stop at 12 tools also
causes surprisingly high input-token totals and contributes to slow UI data
aggregation. The remedy is a checkpointed execution controller, bounded
context, one honest accounting model, and snapshot-driven UI reads.

## Verified current behaviour

The diagnosis was reproduced against `origin/main` and the workspace shown in
the supplied screenshots:

- `FreeAPIRunner.complete_with_tools()` treats `MAX_TOOL_CALLS = 12` as a hard
  terminal. Every successful read counts, an oversized batch is rejected in
  full, and a run that uses exactly 12 tools never receives a final synthesis
  turn.
- `run_explicit_model()` preserves `stopped_reason`, but `app_state` maps the
  result to `answered_by_free_api` and the GUI pipeline records and renders it
  as `Vesta completed`. The contradictory success activity and stop message are
  therefore both real.
- Tool calling is currently coupled to edit authority: `run_explicit_model()`
  calls `complete_with_tools()` only when `allow_edits=True`. A correctly
  classified read-only discovery task therefore loses repository and GitHub
  tools. On current main, Full Auto alone does not grant edits for the supplied
  prompt; the reproduced edit-capable path occurs when a stale Build focus is
  also present.
- The model receives the full growing conversation and full tool observations
  again on every provider turn. The QuotePack ledger contains 16 Gemini task
  records totalling 914,371 provider-reported tokens: 905,445 input and 8,926
  output. About 99% of that total is input. Two newer tasks used 12 provider
  turns each; 14 older records do not contain a turn count, so the displayed
  `38 model calls` is a lower bound and is currently presented too confidently.
- The 1,000,000,000 figure is a user-configured Vesta soft limit, not a Gemini
  quota. The UI and preference sanitizer reject zero and blank values, and no
  reset API or ledger event exists.
- A reached soft limit currently interrupts a task for confirmation. That
  conflicts with the product requirement that usage guidance save money
  without arbitrarily preventing work.
- Warm `settings_payload()` is roughly 133-153 ms and cold calls range from
  about 590 ms to more than one second. It eagerly builds every page, repeatedly
  reads ledgers, policies, registries, credentials, and executable paths, then
  renders and wires hidden pages.
- `boot_payload()` is roughly 1.0-1.4 seconds on the measured Windows workspace.
  It launches ten serial Git processes and computes the same workspace summary
  three times before first paint.
- The latest release-preflight merge also causes default `pytest` discovery to
  collect four embedded benchmark fixture test modules, two of which fail
  collection. The maintained `tests/` suite is separate; default discovery
  must be scoped so the canonical command is reliable.

## Product and safety invariants

1. An internal maintenance interval is never a user quota and never becomes a
   terminal success or stop message.
2. A productive free/local run continues automatically until it completes,
   the user cancels it, the provider fails, a genuine provider/financial
   boundary is reached, a paid/cloud boundary needs consent, or deterministic
   goal-progress checks prove the run is stuck.
3. A stuck run is recoverable and resumable. It is never recorded as completed.
4. Vesta remains local-first. This work does not add paid features or silently
   authorize cloud calls, remote writes, or destructive actions.
5. User-configured token thresholds are advisory. Dollar caps, explicit
   paid/cloud consent, provider billing errors, and provider quotas are
   enforced as real boundaries before every provider call.
6. Resetting a visible counter never deletes audit evidence. Zero or blank
   means no advisory limit (`Unlimited`), not zero allowed work.
7. Provider-reported, estimated, legacy, and mixed values are never presented
   as if they have the same precision.
8. Generic command execution cannot bypass a dedicated consent-aware GitHub or
   Git operation.
9. Usage incurred before cancellation, failure, reset, or process death is
   either reconciled from provider data or explicitly marked unknown; it is
   never silently converted to zero.

## Options considered

1. **Raise or remove the 12-call constant.** This hides the immediate symptom
   but retains infinite-loop risk, quadratic context growth, false completion
   propagation, and remote-command bypasses. Rejected.
2. **Stop every 12 calls and show a Continue button.** This is recoverable but
   still makes an implementation detail interrupt ordinary work and does not
   save tokens automatically. Rejected.
3. **Use checkpointed continuous execution with deterministic progress,
   context compaction, typed outcomes, and in-flight safety checks.** This
   removes the arbitrary stop while keeping cancellation, financial,
   permission, provider, and no-progress controls. Selected.

## Architecture

### 1. Continuous execution controller

The current loop is refactored around an explicit policy and state value rather
than one integer budget. The existing value of 12 becomes a default
`checkpoint_interval`, not a maximum:

```python
@dataclass(frozen=True)
class ToolLoopPolicy:
    checkpoint_interval: int = 12
    max_stagnant_checkpoints: int = 3
    max_exploration_calls_per_subgoal: int = 12
    max_controller_minutes_without_milestone: int = 10
    max_observation_chars: int = 8_000
    max_checkpoint_chars: int = 16_000
    max_serialized_context_chars: int = 48_000
    provider_context_compaction_ratio: float = 0.35
    recent_protocol_atoms_to_keep: int = 2
    fingerprint_capacity: int = 256
```

The limits bound memory and detect thrashing within one goal; they do not limit
the amount of verified productive work. Tests inject smaller values and a fake
clock. There is no default fixed maximum tool-call count for a run that keeps
completing goal milestones. An explicit legacy `max_tool_calls` argument remains
temporarily supported as an externally requested recoverable safety pause; it
is not populated by the GUI and is deprecated in favour of `ToolLoopPolicy`.

`ToolLoopState` tracks provider turns, tool calls, token breakdown, compaction
count, latest checkpoint, bounded evidence identifiers, changed-path summary,
test evidence, errors, current subgoals, and progress since the previous
checkpoint. Fingerprints, errors, paths, and summaries use bounded LRU/ring
structures; complete changed-file truth is re-read from Git at finalization.
`ToolLoopState` produces the canonical `UsageReport` and completion metadata.

At a tool boundary the controller:

1. validates a complete tool batch and does not split it solely because it
   crosses the maintenance interval; calls still execute sequentially,
   cancellation is checked between them, and partial side effects remain
   recorded if a later call fails or is cancelled;
2. records bounded, redacted observations and a stable novelty fingerprint;
3. compacts before any request whose estimated serialized context crosses the
   adaptive provider threshold, including before a checkpoint decision;
4. when the interval is reached, asks for a tools-disabled, versioned JSON
   completion/progress decision;
5. if work remains and verified goal progress is present, creates a compact
   checkpoint,
   replaces replayable history with the original instruction plus checkpoint
   and bounded recent turns, then continues automatically;
6. if no progress is present, issues a recovery/re-plan turn before increasing
   the stagnant-checkpoint count;
7. after three stagnant checkpoints, or after the exploration/time circuit
   breaker expires without a verified milestone, returns a resumable
   `stuck_no_progress` state with the specific repeated actions/errors and
   checkpoint instead of claiming success.

The decision payload is strict and provider-independent:

```json
{
  "schemaVersion": 1,
  "decision": "completed|continue|needs_user_input",
  "remainingSubgoals": ["bounded description"],
  "satisfiedSubgoals": ["bounded description"],
  "evidenceIds": ["ev-..."],
  "answer": "final answer only when completed",
  "userQuestion": "question only when needs_user_input"
}
```

Providers without structured-output support receive the same JSON-only prompt;
Vesta validates the parsed object. Ambiguous prose, invalid JSON, unknown fields,
missing evidence, or a premature completion claim triggers one bounded re-plan
attempt, then a recoverable error rather than success.

Every no-tool response in a tool-enabled run is parsed through this decision
contract, not accepted as an implicit completion. The final user-facing prose is
the validated `answer` field. The circuit-breaker clock measures controller
activity and excludes time spent waiting inside a provider request or an
approved test/build tool, each of which retains its own timeout.

Progress is goal-tied, not novelty-tied. A checkpoint advances only when a
satisfied subgoal cites controller-issued evidence for a relevant file-state
change, Git-state transition, test/build result, resolved error, or bounded
discovery result. Reading a different file is exploration evidence but does not
reset the circuit breaker by itself. Repeated or varied failures and endless
streams of unique reads therefore cannot run forever.

Completion also passes a deterministic task-class verifier. Edit work needs an
attributable file-state result plus applicable validation evidence or an
explicit verified no-change outcome. Ship work retains its PR/CI gates.
Discovery needs a real bounded result such as a GitHub issue identifier. A
plain question needs a non-empty final answer and no outstanding subgoals.

Before every provider continuation, `ExecutionGuard` checks cancellation,
panic mode, cloud consent, provider quota/auth/billing state, and projected
per-task/daily/monthly spend against the current ledger snapshot. This closes
the current gap where budget routing evidence is collected but not enforced.
Free calls project zero dollars. A guard pause preserves the checkpoint and
never reports success.

### 2. Bounded context and automatic token savings

Tool observations are redacted and bounded before they enter provider history.
The durable trace keeps metadata and digests, not repeated raw payloads.
Checkpoint compaction preserves:

- the original system and user instructions;
- completed subgoals and actions;
- changed paths and Git state;
- test/build commands and outcomes;
- unresolved errors and consent requirements;
- the bounded recent protocol turns needed for continuity.

Assistant tool calls and all corresponding tool responses are retained or
removed as one protocol atom, so compaction cannot orphan a call identifier.
Duplicate observations and superseded atoms are dropped only at a valid turn
boundary. The new checkpoint is a normal bounded message, so the
OpenAI-compatible request remains protocol-valid. Unicode/JSON truncation is
performed before serialization and remains valid JSON. Compaction metadata
records how many turns and estimated observation tokens were removed.

Compaction is adaptive rather than waiting for call 12. Before each request the
controller estimates the serialized context against the provider context limit
and compacts at the earlier of 35% of that limit or 48,000 serialized
characters. Providers with no declared limit use the absolute boundary.
The replay of the measured 12-turn observation-size fixture must keep maximum
request context bounded and cumulative serialized input at or below 50% of the
legacy full-history algorithm. Providers that report cached input, reasoning,
or total tokens retain those values rather than having Vesta reconstruct them
from input plus output.

This design reduces cumulative input amplification while allowing useful work
to continue. It does not promise a lower number when a provider does not expose
the required measurements.

### 3. Typed completion, persistence, and resume

Runner results gain an orthogonal, versioned `completion_state`. Existing
surface-specific `status` fields remain during alpha:

```text
completed
cancelled
needs_user_input
needs_consent
retryable_provider_error
provider_blocked
stuck_no_progress
failed
```

`provider_blocked` carries a typed reason such as `auth`, `rate_limit`, `quota`,
`billing`, `panic`, `daily_cap`, `monthly_cap`, or `task_cap`. The compatibility
mapping is explicit:

| Completion state | Runner/pipeline compatibility | Checkpoint/outcome | UI |
| --- | --- | --- | --- |
| `completed` | existing answered status | completed/completed | Done |
| `cancelled` | cancelled | cancelled/cancelled | Stopped by you |
| `needs_user_input` | `needs_*` | interrupted/blocked | Question + Resume |
| `needs_consent` | existing confirmation status | read_only/blocked | Approval |
| `retryable_provider_error` | runner error | interrupted/failed | Retry/Resume |
| `provider_blocked` | typed existing error | blocked/blocked | Specific recovery |
| `stuck_no_progress` | incomplete | interrupted/failed | Stuck + Resume |
| `failed` | failed | failed/failed | Failed |

`answered_locally` and `answered_by_free_api` are emitted only for `completed`.
A non-complete result propagates through `ask`, `app_state`, `gui_pipeline`,
workflow checkpoints, task outcomes, saved chat, CLI, and the frontend without
being rewritten as `answered`, `DONE`, or `Vesta completed`. Dual-read adapters
accept older results that lack `completion_state`; new writers always emit it.

Every non-complete recoverable result includes a privacy-safe checkpoint and
recovery action. Only `completed` records completed-route evidence and a
completed task outcome. Provider usage already incurred is still recorded
honestly for non-complete runs.

The existing `RunCheckpoint` advances to schema 2 and is reused rather than
creating a second persistence system. It atomically stores a stable `run_id`,
controller revision, model and mode, bounded redacted subgoals/evidence,
side-effect fingerprints, next turn, usage-event identifiers, and recovery
state under `.vestahub`. Raw provider messages, raw tool output, credentials, and
unredacted source snippets remain ephemeral. A cross-process lease prevents two
windows from resuming the same run concurrently.

The saved redacted chat instruction plus durable compact summary reconstructs a
resume request after restart. Tool call IDs and mutating-action fingerprints
prevent already completed side effects from being replayed. If necessary task
content was redacted or is unavailable, resume returns `needs_user_input`
rather than guessing. Settings/chat exposes one-click Resume, startup recovery
finds interrupted checkpoints, and transient provider errors use injectable
bounded exponential backoff before yielding a resumable state.

### 4. Intent and GitHub capability for the reproduced prompt

Run mode is an authority ceiling, not an instruction to mutate. Discovery
prompts such as “find me a git issue that we can solve” remain read-only even
when Full Auto is selected. A stale Build focus cannot override the current
message's explicit discovery intent.

Tool use is decoupled from mutation authority. `tool_calling_enabled` determines
whether the runner uses the tool loop; `allow_mutations` determines which
schemas it receives. Read-only discovery therefore gets repository and approved
GitHub read tools while file-write, command, commit, push, and PR tools remain
absent.

The repository tool vocabulary adds a bounded read-only GitHub issue listing
and search operation through the existing connector, scoped to the active
GitHub origin. It supports state, label, query, and result-limit filters,
excludes pull requests, caps pagination and response size, URL-encodes filters,
and returns only number, title, labels, URL, state, and a bounded excerpt.
Issue text is untrusted quoted data and cannot add instructions to the agent.

A stored GitHub token authorizes the existing read capability. A public
repository without a token can use an explicitly consented public-read path;
otherwise the controller returns `needs_consent`/connection setup and resumes
after approval. Rate-limit failures are typed and recoverable. Remote reads are
never misrepresented as local operations.

Autonomous `run_command` changes from a command denylist to a capability
allowlist. Executable basenames, `.exe` aliases, case, path form, Git global
options, and the first effective subcommand are normalized before execution.
Only an explicit local-read Git subcommand set is allowed; `-c`, `-C`, aliases,
remote/submodule/LFS operations, all `gh` forms, shells, interpreters, wrappers,
and network utilities are rejected before the executor is reached. The initial
allowlist is `status`, `diff`, `log`, `show`, `rev-parse`, and
`branch --show-current`, with only `--no-pager` accepted as a Git global option.
Fixed
test/build operations use dedicated tools. Remote work uses the existing
permission-aware tools only.

### 5. Usage accounting and baseline reset

`ToolLoopState` emits one canonical versioned `UsageReport`; model-call ledger
events, workflow cost, task outcome, Settings, and the saved result consume that
object without independently estimating it. Provider `total_tokens` remains
independent from nullable input/output components, and token provenance remains
independent from cost provenance.

New-schema usage is recorded per provider turn rather than as one aggregate at
task completion. Before dispatch Vesta appends `model_call_started` with a stable
`run_id`, `turn_index`, canonical model identifier, current model-specific
`usage_epoch`, and unique event identifier. On response it appends the matching
version-2 `model_call` with nullable token components, per-component provenance,
provider quota, cache/reasoning values when reported, and cost. An unresolved
start after a crash is an `unreconciled` provider turn with unknown tokens, not
zero usage. Completion does not append a second aggregate event.

Every ledger append uses an OS-level cross-process lock and monotonic
`ledger_sequence`. The sequence head is updated atomically and recovered from
the JSONL tail after a crash. This gives resets and model calls one total order
across GUI/CLI processes while preserving the existing process lock as a fast
inner guard.

Reset appends a per-model `usage_baseline_reset` event, advances that model's
`usage_epoch`, and never truncates or rewrites model-call events. A call belongs
to the epoch captured when it was dispatched. Therefore a call started before a
reset and finalized afterwards remains in lifetime history but does not reappear
in the new visible baseline. Aggregation counts the intersection of the chosen
local-calendar window and the latest reset epoch. Timestamps remain UTC in the
ledger; day/month labels explicitly use the device's current timezone and are
tested across offset/DST boundaries.

Snapshots expose distinct concepts instead of overloading one progress bar:

```json
{
  "modelId": "free:gemini:gemini-3.1-flash-lite",
  "usedSinceBaseline": {
    "inputTokens": {"value": 0, "provenance": "provider|estimated|mixed|unknown", "coverage": 1.0},
    "outputTokens": {"value": 0, "provenance": "provider|estimated|mixed|unknown", "coverage": 1.0},
    "totalTokens": {"value": 0, "provenance": "provider|derived|estimated|mixed|unknown", "coverage": 1.0}
  },
  "lifetime": {"totalTokens": {"value": 0, "provenance": "mixed"}, "taskInvocations": 0},
  "providerTurns": {"value": 0, "confidence": "exact|lower-bound|unknown"},
  "legacyUnknownTurnTasks": 0,
  "unreconciledTurns": 0,
  "advisoryLimit": {"metric": "tokens|requests", "threshold": 100000, "window": "minute|day|month"},
  "advisoryStatus": "unset|ok|approaching|reached",
  "providerQuota": null,
  "baselineResetAt": null,
  "contextEfficiency": {"inputAmplification": 1.0, "compactions": 0, "provenance": "provider"}
}
```

Provider quota is a separate nested value and never replaces Vesta-tracked token
usage. A stale quota snapshot is labelled stale/unknown unless an absolute reset
time or a bounded freshness rule proves it current. Token measurement and cost
measurement are independent. Legacy records without provider-turn counts yield
a lower-bound display such as `38+`, not an exact count.

Canonical model aliases map renamed catalog identifiers to one stable identity.
Models present only in ledger history remain visible as historical rows instead
of disappearing when a catalog entry changes. Legacy aggregate events remain
immutable and are adapted as one task plus their recorded lower-bound call
count.

New execution records include per-turn input, output, provider total, cached
input, peak input context, compaction count, and compacted observation estimates
when the provider exposes them; unsupported components remain null. The defined
`inputAmplification` is cumulative provider-reported input divided by the peak
single-turn input context, where 1.0 is ideal. It is omitted unless every value
needed for that window is provider-reported. No exact replay or cache claim is
inferred from legacy aggregates.

### 6. Unlimited and advisory semantics

The preference API accepts a positive integer as an advisory threshold. Zero,
blank, `None`, or an explicit Remove action deletes that model's entry and
renders `Unlimited`. Existing positive values, including the screenshot's
one-billion workaround, are preserved until the user removes them; migration
does not silently reinterpret user data.

Reaching an advisory threshold never prevents model dispatch. It produces a
deduplicated notice for the current baseline/window. The old
`needs_limit_confirmation` pre-dispatch branch is removed. Token thresholds use
finalized token totals; request thresholds use exact version-2 provider turns
and disclose lower-bound legacy coverage. Genuine provider quota/billing
errors, dollar caps, and paid/cloud consent are enforced by `ExecutionGuard`.

Preference schema validation drops invalid legacy zero/negative entries. Saves
and removals use the same cross-process mutation lock, read-merge-write under
that lock, a same-directory temporary file, `fsync`, and atomic replace. A
failed write keeps the prior valid preference file. Reset uses the ledger lock,
so saves, removals, calls, and resets preserve unrelated model state across
multiple Vesta windows.

### 7. Settings information architecture and interaction design

Settings retains its modular section registry but becomes a stable product
shell instead of rebuilding every hidden page. Desktop layout uses a 220 px
navigation rail, a 32 px gap, and a readable 680-760 px active content column
inside a wider surface. To remove the screenshot's double-navigation squeeze,
opening Settings collapses the global workspace sidebar to a 64 px icon rail at
1040 px and above and restores its prior state on exit. Below 1040 px the global
sidebar is a closed drawer; below 760 px the Settings rail becomes an accessible
compact page switcher and every form stacks without horizontal overflow. The Qt
minimum is lowered to 720 px so the narrow product contract is reachable.

Each section has a title, one-sentence purpose, current status summary, grouped
controls, and progressive disclosure for advanced data. Search uses a static
section/control metadata index; selecting a result navigates to and lazily loads
that section rather than constructing or scanning hidden DOM.

The Cost Firewall page contains:

- summary cards for spend, savings, and active protection;
- concise daily/monthly budget status;
- one compact model-usage row per model showing used-since-baseline,
  measured/estimated/mixed badge, input/output breakdown, tasks and provider
  turns, and context efficiency where available;
- clear `Reset usage` and `Remove limit` actions;
- an `Unlimited` state and a collapsed advisory-threshold editor;
- provider quota in a separate labelled area when current data exists;
- visually distinct `Advisory` token notices and `Blocking protection` dollar/
  provider states, so a notification cannot look like a task prohibition;
- plain-language disclosure that reset retains local audit history.

Mutations patch the affected row and invalidate only its section data. They do
not replace the entire Settings DOM, erase focus, or trigger all provider
diagnostics. Buttons have at least 44 px touch targets, keyboard focus is
visible, status is announced through appropriate live regions, and reduced
motion is respected.

The asynchronous bridge is versioned and symmetric for reads and mutations:

```text
requestSettingsSection(sectionId, revision, requestId)
settingsSectionReady({sectionId, revision, requestId, data, error})
requestSettingsMutation(action, payload, revision, requestId)
settingsMutationReady({action, revision, requestId, data, error})
```

Unknown sections/actions fail closed. Responses include no secrets, and the
frontend drops a response whose root, revision, or request ID is stale. A
bounded worker pool supports cancellation and orderly shutdown. Compatibility
slots call the same service rather than reimplementing data assembly.

### 8. Snapshot-driven performance

Boot and Settings use shared read snapshots rather than repeatedly querying the
same sources.

`WorkspaceReadSnapshot` contains one repository-context result, one bounded Git
snapshot, one parsed ledger view, loaded preferences, and cached registry/policy
data. Workspace, status, inspector, budgets, usage, and savings derive from that
snapshot. Its fingerprint includes the source mtimes/state needed for explicit
invalidation; concurrent requests for the same root/revision coalesce and every
consumer receives an immutable copy.

First paint uses a small shell payload containing preferences, static
registries, cached model catalog, and safe placeholders. Repository status,
inspector, and ledger-backed detail hydrate asynchronously and stale responses
are discarded by revision. Cold snapshot construction performs at most two Git
processes. A cache hit younger than the two-second bounded-staleness window
performs none; after that window Vesta refreshes rather than promising indefinite
truth without a Git/file-system observation.

Settings requests section data by section identifier and revision. Only the
active section is rendered. Provider CLI/version diagnostics run only when the
Providers section is visible or the user explicitly refreshes it. Parsed YAML
registries, policies, executable detection, and stable credential status are
cached process-wide with bounded lifetime and explicit mutation invalidation.
A bounded worker pool and in-flight coalescing replace one new worker per click.

The invalidation contract is explicit:

| Change | Invalidation source |
| --- | --- |
| Vesta ledger/preference/reset write | synchronous revision bump |
| Vesta Git/edit tool | synchronous workspace revision bump |
| branch/index/HEAD change | Git metadata watcher plus two-second fallback |
| external tracked/untracked edit | workspace watcher plus two-second fallback |
| credential/login/provider mutation | provider-cache revision bump |
| workspace switch | resolved-root/gitdir key change and request cancellation |
| second Vesta process | file watcher plus two-second fallback |

Cache keys include the resolved workspace root and Git common directory.
Failures and cancellations release in-flight entries; a failed result is not
cached as healthy data.

Performance markers separate Vesta overhead from provider latency. The supplied
13.9-second activity is a provider/run duration, not evidence that all of it is
GUI overhead. The release harness records process launch, WebEngine shell paint,
chat-interactive, submit click, provider dispatch, first provider byte, first
rendered chunk, active-section request/paint, and history render/scroll.

Performance acceptance budgets on the documented reference Windows workspace
(QuotePack-scale Git history and ledger) are:

- first shell content p95 at or below 250 ms cold and 100 ms warm;
- shell-visible to chat-interactive p95 at or below 250 ms;
- full process launch to visible shell p95 at or below 2 seconds cold, recorded
  separately because QtWebEngine startup is outside payload construction;
- submit-to-provider-dispatch Vesta overhead p95 at or below 100 ms warm and
  200 ms cold, excluding confirmation wait and provider network time;
- first-provider-byte to rendered text p95 at or below 50 ms;
- no synchronous GUI operation above 50 ms;
- Settings shell paint at or below 100 ms after navigation;
- active Settings section p95 at or below 300 ms cold and 100 ms warm;
- repeated section navigation from cache at or below 50 ms;
- rendering 100 saved messages at or below 100 ms with no scroll long task over
  50 ms;
- at most two Git processes on cold snapshot and zero on a valid cache hit;
- one ledger parse per snapshot;
- no provider-version process unless Providers is visible or explicitly
  refreshed.

CI uses deterministic call counters, serialized-size assertions, fake clocks,
and injected delays rather than flaky absolute timing. The release command
`vesta perf gui --workspace <fixture> --cold-samples 5 --warm-samples 20` records
p50/p95 timing, payload sizes, long tasks, Git/ledger/process counts, hardware,
and cache preparation. Its JSON artifact is required PR evidence on the
reference Windows environment; CI enforces the deterministic budgets.

### 9. Default test discovery

Pytest configuration scopes canonical discovery to `tests/`. Embedded benchmark
fixture repositories remain executable only through their benchmark harness and
are not imported as Vesta test modules. Directly targeted fixture tests remain
possible from their intended working directory. This makes plain `pytest`
equivalent to the maintained canonical Python suite.

## Data migration and compatibility

- The preference schema advances additively. Existing positive limits are
  preserved; absent, zero, or negative values normalize to Unlimited.
- Old aggregate model-call events remain immutable. Missing provider-turn
  counts/components are surfaced as lower-bound/null data; version-2 per-turn
  events are never double-counted with a legacy completion aggregate.
- Reset events are additive and older clients safely ignore unknown event types.
- Model alias migration is read-time and reversible; stored event identifiers
  are not rewritten, and historical-only models remain visible.
- Existing `settings_payload()` and synchronous bridge methods remain bounded
  compatibility adapters while the frontend moves to section requests.
- Existing runner/status fields remain during alpha. New writers include
  `completion_state`; dual-read adapters map an old missing value from status,
  but no new caller may infer success from `stopped_reason` or answer text.
- Version-1 checkpoints remain readable as non-resumable evidence. Version-2
  checkpoints use atomic replace and a lease; older readers ignore their new
  fields rather than deleting them.
- No raw prompt, tool output, credential, or absolute source path is added to
  usage or reset events.

## Error handling

- Malformed provider tool calls or checkpoint decisions receive one bounded
  recovery opportunity and then return `retryable_provider_error`, `failed`, or
  `stuck_no_progress`, never success.
- Cancellation is checked before provider calls, between calls in a tool batch,
  before compaction, and during snapshot hydration. Already completed side
  effects and usage-start events remain in the checkpoint/ledger.
- A reset/save failure keeps the existing UI value, restores the enabled state,
  and shows an inline actionable error.
- A preference atomic-replace failure preserves the previous valid file. A
  ledger sequence-head mismatch is recovered from the append-only tail while
  holding the interprocess lock.
- Corrupt or unreadable legacy usage events are skipped using the ledger's
  existing safe-read rules and lower snapshot confidence.
- Provider auth, transient transport, rate limit, quota, billing, and financial
  cap outcomes retain distinct reason codes and recovery actions.
- Snapshot failures return partial safe data and an explicit degraded state;
  they do not freeze first paint.
- Stale asynchronous responses are dropped by request/revision identity.

## Test strategy

Tests are written before production changes and include:

### Execution and state propagation

- exactly 12 successful tools followed by a final answer completes;
- more than 12 productive tools cross multiple checkpoints and complete;
- a batch crossing an interval is not sliced for that reason; validation,
  cancellation after the first call, partial-effect evidence, duplicate/missing
  call IDs, and later-call failure behave deterministically;
- redundant successes, identical/varied failures, and an infinite stream of
  unique successful reads trigger the goal-tied no-progress policy;
- structured decisions cover valid complete/continue/question states, malformed
  JSON, ambiguous prose, unknown/missing fields, false evidence IDs, premature
  completion, and providers without native structured output;
- edit, ship, discovery, and plain-answer completion verifiers reject claims
  without task-appropriate evidence;
- context atoms never split tool-call/response groups; Unicode/JSON observation
  bounds remain parseable, bounded rings do not grow, and the measured 12-turn
  replay uses at most 50% of legacy cumulative serialized input;
- cancellation works during provider, tool, compaction, and hydration phases;
- incomplete states preserve checkpoints and never emit or persist completed
  status at runner, app-state, GUI pipeline, workflow, outcome, saved-chat, or
  frontend/CLI layers; old results without `completion_state` still dual-read;
- stuck/failure -> atomic serialize -> process restart -> one-click resume ->
  completion neither repeats side effects nor duplicates usage;
- concurrent resume attempts obey the lease, missing/redacted task context asks
  the user, and transient retry/backoff uses a fake clock;
- cancellation, crash, provider failure, quota, billing, panic, task/daily/
  monthly cap, and consent states remain recoverable and usage already incurred
  is finalized or explicitly unreconciled;
- the supplied “find me a git issue that we can solve” journey discovers an
  issue through a read-only tool loop; write/command schemas are absent even
  under Full Auto and a stale Build focus cannot change that;
- GitHub search tests active-origin scoping, PR exclusion, encoding, pagination/
  size caps, token/public-read consent, rate limit, and malicious issue text;
- the autonomous command allowlist blocks case/path/`.exe` variants, absolute
  executables, `cmd`/PowerShell/interpreter wrappers, `git -c`, `git -C`,
  aliases, `ls-remote`, submodule/LFS/remote operations, every `gh` form, and
  network utilities before execution, while approved local Git reads and
  dedicated consent-aware tools still work.

### Usage and migration

- zero and blank remove a limit; positive values persist; other models are
  untouched;
- reached advisory thresholds never prevent dispatch and notices deduplicate;
- reset appends but never truncates; per-model isolation, monotonic sequence,
  same-second ordering, multiple resets, lifetime totals, local-calendar window
  intersection, restart, and reset-during-in-flight-call semantics are correct;
- multiprocess call/reset and preference save/remove races preserve every event
  and unrelated setting; injected append/replace failure preserves prior data;
- model-call start/finalize is idempotent, unresolved starts show unknown usage,
  and completion/cancellation never adds a duplicate aggregate;
- provider totals are preserved even when total differs from input plus output;
  total-only, components-only, derived-total, cached/reasoning, and absent
  breakdowns remain nullable with per-field provenance/coverage;
- measured, estimated, mixed, legacy, and cost/token precision remain separate;
- provider quota never overwrites token usage and stale quota is disclosed;
- token/request advisory metrics and minute/day/month windows are correct;
- renamed/removed model aliases aggregate to one canonical row while raw ledger
  identity and historical-only rows remain available;
- legacy 14 single-record tasks plus two 12-turn tasks render `38+`, not exact
  `38`;
- the screenshot-scale fixture explains 914,371 as 905,445 input plus 8,926
  output across 16 tasks.

### Settings, accessibility, and performance

- only the active section requests and renders data;
- hidden sections do not run provider diagnostics;
- mutations preserve DOM focus and update only the affected control;
- versioned async reads/mutations cover error responses, cancellation, worker
  shutdown, cache invalidation, concurrent coalescing, failure release, stale
  root/revision/request rejection, and cross-workspace isolation;
- the invalidation matrix covers Vesta and external file/Git changes, branch/
  index/HEAD changes, ledger/preferences, credentials, workspace switches,
  multiple windows, and two-second bounded staleness;
- one snapshot performs one ledger parse, one registry/policy load, and at most
  the allowed Git calls;
- responsive E2E coverage at 720, 768, 1040, and 1280 px has no overflow,
  retains 44 px targets, keyboard navigation, labels, focus, and live status;
- Settings-mode tests prove the global sidebar collapses/drawers correctly and
  restores its prior state;
- Unlimited, reset-baseline, huge-token, mixed-confidence, provider-quota, and
  error states receive deterministic visual coverage using pinned Chromium,
  bundled fonts, disabled motion, fixed clock/color scheme/device scale, and an
  accessibility scan;
- deterministic performance tests enforce call counts, long-task/context-size
  limits, marker ordering, fake delays, and cache states; the Windows release
  harness records five cold and twenty warm samples for the absolute budgets;
- default `pytest`, JavaScript unit tests, Playwright E2E, static/security
  checks, package build, install smoke, and release preflight pass.

## Acceptance criteria

- The internal tool checkpoint cannot terminate a productive task or appear in
  user-facing copy.
- No incomplete result can be recorded or rendered as completed.
- Long productive runs continue with bounded replay context and auditable
  compaction metadata.
- Endless exploration, wrapper-command bypasses, and repeated/varied failure
  cycles stop in a resumable non-success state without unbounded memory or
  provider input growth.
- The exact reproduced GitHub-discovery prompt has a permission-safe completion
  path.
- Zero/blank reliably produces Unlimited, Reset usage visibly returns to zero,
  and the immutable ledger remains intact.
- A reset during an active call cannot re-add pre-reset usage; concurrent
  windows cannot lose usage events or unrelated preferences.
- Usage explains input/output, task/turn, precision, baseline, provider quota,
  unreconciled calls, context amplification, and legacy limitations without
  false certainty.
- Advisory token thresholds never block work; paid/cloud and genuine provider
  boundaries remain enforced.
- Settings meets the documented layout, responsive, accessibility, mutation,
  and lazy-loading contracts.
- Boot, chat dispatch/rendering, and Settings meet deterministic call-count/
  long-task gates and measured local performance budgets.
- Plain `pytest` runs only the maintained Vesta suite.
- All canonical, security, packaging, release-preflight, and E2E gates pass
  before the pull request is merged.

## Delivery, review, and rollback

Implementation is split into reviewable commits for execution security/state,
continuous compaction, usage/reset, Settings UX/lazy data, snapshot performance,
and release tests. A final independent code review and security review run
before the branch is pushed.

The pull request contains before/after performance evidence, accounting fixture
evidence, screenshots at desktop and narrow widths, migration notes, and the
exact test commands. It is merged only after required checks pass.

Rollback is additive and component-scoped. Reverting UI or snapshot commits
falls back to compatibility payloads. Reverting reset support leaves reset
events harmlessly ignored and preserves all ledger history. Reverting the
continuous controller restores the former bounded loop but must also restore
typed propagation together so an incomplete run can never become false
success. No rollback deletes user data.
