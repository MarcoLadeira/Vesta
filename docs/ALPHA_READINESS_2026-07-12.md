# OPai Free Public Alpha Readiness

**Assessment date:** 2026-07-12
**Repository:** MarcoLadeira/OPai
**Evidence snapshot:** origin/main at f4c27b715d83b9febbcaab59d1d58dec8acf28ab
**Release target:** [OPai 0.2.0 Free Public Alpha (milestone 3)](https://github.com/MarcoLadeira/OPai/milestone/3)

Line references in this document describe the evidence snapshot above. Later
programme commits may move those lines or resolve a finding; the snapshot keeps
the audit falsifiable.

## 1. Executive verdict

**Verdict: no-go for a credible public alpha today. OPai is a strong pre-alpha
with a substantial, tested core, but it must not be described as public-alpha
ready until execution authority, edit-result truth, spend truth, and packaged
release proof are closed.**

Confidence is **high (about 90%) for the traced Python execution, permission,
ledger, Git, and persistence findings**, because the relevant production paths
and tests were inspected and the complete Python suite passed. Confidence is
**moderate for end-user desktop and cross-platform behaviour**, because this
cycle did not execute the JavaScript/E2E suites, make live provider calls, run
on macOS, or launch a packaged Windows/macOS artifact.

The four immediate trust failures are concrete:

1. Ordinary implementation policy advertises push and pull-request authority
   without explicit publication intent (opaihub/agent_policy.py:25-36).
2. Copilot Safe Auto and Full Auto add an unrestricted all-tools flag
   (opaihub/accounts.py:1159-1168).
3. The prose-only local ask path can be used for edit intent and then mapped to
   an answered edit workflow without a mutation (opaihub/gui_pipeline.py:1060-1079).
4. a GUI free-provider call can emit two route records plus a model-call record,
   while budgets and summaries count route estimates as spend
   (opai/app_state.py:741-784; opaihub/gui_pipeline.py:783-804;
   opaihub/budget.py:77-92; opaihub/ledger.py:375-390).

The selected response is the smallest sound programme:
[epic #291](https://github.com/MarcoLadeira/OPai/issues/291), comprising
[#283](https://github.com/MarcoLadeira/OPai/issues/283),
[#284](https://github.com/MarcoLadeira/OPai/issues/284),
[#285](https://github.com/MarcoLadeira/OPai/issues/285), and
[#286](https://github.com/MarcoLadeira/OPai/issues/286). After it lands, release
confidence—not feature expansion—is next:
[epic #293](https://github.com/MarcoLadeira/OPai/issues/293).

The launch is fully free. Pricing, entitlements, licensing, and closed-source
fulfilment are post-launch discovery informed by actual use; they are not alpha
gates. Safety, truthful execution, privacy, and honest accounting must never be
reserved for a paid upgrade.

### Verification baseline

| Check | Verified result | Interpretation |
|---|---|---|
| Python tests | 1,503 tests passed, 2 skipped, 434 seconds | Broad and healthy baseline; it does not override the traced contract defects. |
| Focused Python tests | 90 tests plus 13 subtests passed | Relevant routing/safety surfaces have usable test seams. |
| Ruff | Check and format-check passed | Static style baseline is clean. |
| Bandit | Passed with exit code 0 | No scanner finding in the scanned Python scope; not a substitute for the permission-path audit. |
| Registry validation | Passed | Packaged registry data is structurally valid. |
| Compileall | Passed | Python sources compile. |
| npm audit | 0 vulnerabilities across 79 dependencies | Dependency snapshot only. |
| JavaScript unit/E2E | Not run; development dependencies were not installed | Desktop interaction and accessibility evidence remains incomplete. |
| Packaged desktop artifacts | Not produced or launched | Public desktop release remains unproved. |

### Programme update after the snapshot

The findings above are intentionally preserved as a falsifiable snapshot of
`origin/main`. The current alpha-trust branch has implemented the first trust
and cost-correctness tranche; this does **not** change the no-go verdict until
the remaining release proof is independently completed.

| Gate | Current implementation state | Stored evidence / remaining proof |
|---|---|---|
| #283 explicit publication intent | Implemented: ordinary implementation does not grant publication authority, and an explicit prohibition wins. | `c9125e4`; final full-suite and cross-surface evidence remain release-gate work. |
| #284 Copilot edit safety | Implemented: unsupported Copilot edits fail before process launch. | `2874389`; supported real-provider smoke remains pending. |
| #285 truthful local edit behaviour | Implemented: local edit intent fails closed and does not create mutation success evidence. | `8584fff`; GUI/CLI parity matrix remains pending. |
| #286 exact-once spend | Implemented: authoritative `model_call` events are the spend source, while route estimates remain counterfactual evidence. | `faf25c5`; #288 must still join one terminal task outcome. |
| #287 bounded result reuse | Implemented: Git-state fingerprints include safe dirty/untracked bytes, uncertain states bypass reuse, entries are schema-versioned/atomic/one-hour expiring, and cache decisions are recorded separately from spend. A store compares the lookup key again after model execution, so a workspace change during an answer is never cached as the newer state. | `4db4a0c`, `dae64f5`, `f2cd6a9`, and the follow-up guard on this branch; final focused/full-suite evidence is required before issue closure. |
| #290 desktop artifact release trust | Implemented locally: OS-specific hash-lock selection fails closed; Windows lock installation is proven; build provenance is bound into evidence; protected signing, credential-free artifact smoke, and no-execution archive attestation are separate jobs; expected publishers and consumer attestation verification are pinned. | A native macOS lock, protected-environment configuration, and a real clean-platform production rehearsal remain required. No public artifact exists yet. |

The measured synthetic Git fixture cost for the #287 fingerprint was **177.93
ms mean for a clean repository** and **182.85 ms mean with one small dirty
file** (10 samples each, Windows local run, 2026-07-12). This is a small,
repeatable guardrail rather than a production latency claim; the clean path
opened no repository files in its contract test.

The hard remaining public-alpha block is artifact/release confidence:
[#290](https://github.com/MarcoLadeira/OPai/issues/290) and
[#293](https://github.com/MarcoLadeira/OPai/issues/293). Outcome measurement
([#288](https://github.com/MarcoLadeira/OPai/issues/288)), real-provider smoke,
GUI/CLI parity, a controlled macOS lock/platform run, and clean Windows/macOS
artifact journeys are still required. Local JavaScript and Playwright evidence
now exists on this branch, but it is not packaged-artifact or accessibility
proof. The launch remains fully free; none of those gates requires or introduces
payment, entitlement, licensing, or feature restriction.

### Current-branch local evidence (2026-07-12)

This record supplements the immutable `origin/main` snapshot above. It is not a
claim that a public artifact has been built, signed, notarised, or released.

| Check | Current branch result | Boundary still outside local evidence |
|---|---|---|
| Python regression | Complete unittest suite passed: 1,574 tests, 2 skipped. Focused alpha/release suite passed: 106 tests plus 22 subtests. | A hosted clean-platform native build and artifact lifecycle run. |
| Web quality | npm audit found 0 vulnerabilities; 27 Vitest unit tests and 261 Playwright tests passed. | Packaged-artifact accessibility and native desktop UI validation. |
| Static/security | Ruff format/check, registry validation, compileall, pip check, pip-audit, and Bandit completed locally; independent workflow review found no remaining concrete P0/P1 path. | Protected-environment reviewer configuration and a real OIDC attestation verification. |
| Release workflow | Safe semantic-version tag validation, SHA-pinned Actions, expected publisher checks, archive-before-execution, credential-free smoke, and workflow/`refs/heads/main`/hosted-runner-pinned consumer verification are implemented and contract-tested. | A macOS-native lock, production signing/notarisation, clean artifact smoke, upgrade/uninstall/rollback rehearsal, and a published verified asset. |

## 2. Repository truth and architecture overview

### 2.1 Product and runtime shape

OPai is a Python 3.10+ local-first coding control plane with a desktop GUI and
multiple CLI surfaces. Setuptools builds the package; PyYAML is the only core
runtime dependency. PySide6 and keyring are optional under the desktop-gui
extra (pyproject.toml:1-14). Console entry points are opai, op, op-hub, and
opcoding, while opai-gui is a windowed launcher (pyproject.toml:16-26).

The primary desktop is a local HTML/CSS/JavaScript application rendered in
QtWebEngine. Python owns sensitive logic and exposes JSON through QWebChannel
(opai/gui_web.py:1-17). Remote URL access and JavaScript clipboard access are
disabled, and index.html is loaded from the package (opai/gui_web.py:929-951).
The classic Qt UI in opai/gui_desktop.py remains a fallback
(opai/cli.py:285-300).

The dominant shared core is opaihub/. Application-facing orchestration lives in
opai/app_state.py; the CLI parser and commands live in opai/cli.py; the web GUI
bridge lives in opai/gui_web.py; and one-turn task orchestration lives in
opaihub/gui_pipeline.py.

### 2.2 Execution and data flow

| Stage | Desktop path | CLI path | Shared truth |
|---|---|---|---|
| Start | opai gui or opai-gui → opai/gui_web.py | opai/cli.py:2372-2376 | Package entry points in pyproject.toml |
| Collect request | JavaScript composer → QWebChannel Bridge | Argument parser; streaming path when --model is supplied | Mode/model vocabulary and provider registry |
| Resolve repository and policy | Bridge calls handle_gui_message | opai/cli_stream.py:159-171 calls the same function when --model is present | opaihub/gui_pipeline.py:195-212; opaihub/agent_policy.py |
| Route execution | GUI pipeline calls opai/app_state.py or opaihub/ask.py | Shared pipeline with --model; legacy run_ask without --model | Account, free API, and local runners |
| Execute tools | Provider-native CLI or bounded repository tools | Same when using shared pipeline | opaihub/provider_tools.py and opaihub/aci.py |
| Observe result | Activity events, answer, receipt, workflow/diff state | Activity lines, answer, receipt footer or JSON | Workflow ledger, usage ledger, checkpoints, diff review |
| Persist | Per-project .opaihub plus local recents/keychain | Same project state and ledger | opaihub/state.py and specialist stores |

The main GUI request is moved to a worker thread and carries a request ID and
cancellation event (opai/gui_web.py:700-751). The GUI pipeline resolves the
active repository, records dirty state, creates runtime state, constructs a
capability contract, dispatches one provider/local turn, then decorates the
result with workflow, diff, receipt, and activity data
(opaihub/gui_pipeline.py:255-399).

There is one important divergence: opai ask --model uses the GUI pipeline
(opai/cli.py:647-661; opai/cli_stream.py:96-171), while opai ask without a model
still calls run_ask directly (opai/cli.py:662-669). That legacy path does not
carry the complete edit/mode/provider contract.

### 2.3 GUI and CLI shared versus divergent paths

**Shared**

- Provider-backed CLI asks and desktop asks share handle_gui_message.
- Both surfaces consume the same agent policy, repository context, provider
  runners, workflow state, ledger records, and receipt shape.
- Ctrl+C in the streaming CLI and Stop in the GUI both set cancellation events
  that reach the runner (opai/cli_stream.py:183-223;
  opai/gui_web.py:770-776).
- Provider errors are normalized through one redacted contract
  (opai/provider_contract.py:286-320).

**Divergent**

- The model-less CLI ask retains the older local-only path.
- The classic Qt GUI and web GUI are two presentation implementations, although
  both call the pipeline.
- Some GUI bridge slots remain synchronous even when their payload builders walk
  a repository, read ledgers, or discover models (opai/gui_web.py:437-529,
  808-816).
- Prompt recents are persisted, but prior conversation turns are not supplied
  to handle_gui_message; its contract is explicitly “one chat turn”
  (opaihub/gui_pipeline.py:195-212). This is not multi-turn project memory.

### 2.4 Providers

Provider groups are:

- **Account CLIs:** Claude, Codex, and Copilot.
- **Free-tier public APIs:** Gemini, Groq, and Mistral.
- **Local:** Ollama and OpenAI-compatible endpoints.

A provider adapter contract exists with explicit execution requests,
capabilities, execution plans, normalized events, probes, and usage extraction
(opaihub/provider_adapters.py:19-111, 160-224, 278-354). Provider knowledge is
nevertheless split across account specs, free model specs, local runners, model
registry data, and inline command construction. This is tracked under
[epic #295](https://github.com/MarcoLadeira/OPai/issues/295).

Free API calls require an explicit cloud confirmation before repository context
leaves the device (opai/app_state.py:696-728). Local endpoint discovery accepts
only loopback/private endpoints by default
(opaihub/local_models.py:15-56; opaihub/local_runner.py:500-568).

### 2.5 Persistence and privacy boundaries

The canonical per-project state root is .opaihub
(opaihub/state.py:15-20). It contains project state, GUI preferences, result and
evidence caches, workflow events, run checkpoints, budget state, usage ledger,
and governance audit records.

- Usage events append to .opaihub/ledger/usage.jsonl and store a one-way task
  hash rather than the raw task by default (opaihub/ledger.py:57-100).
- Governance events append to a redacted, hash-chained audit log with a
  verifiable head (opaihub/audit.py:41-55, 98-125, 149-213).
- Workflow metadata is recursively redacted before persistence
  (opaihub/workflow_ledger.py:22-64).
- Checkpoint files are written through a temporary file and atomic replace
  (opaihub/checkpoints.py:98-114).
- Per-workspace recent prompts live under ~/.opai/recents and are redacted,
  bounded, and clearable (opai/gui_recents.py:1-6, 31-86).
- Free API keys use environment variables or the operating-system credential
  store; status APIs do not reveal a value or fingerprint
  (opaihub/credentials.py:1-5, 70-117).

### 2.6 Permissions, tool execution, and autonomy

The intended permission stack is layered:

1. Agent intent resolves to a central capability policy.
2. Run mode limits provider execution.
3. Repository tools allow a bounded vocabulary and project-root paths.
4. Dirty-path overlap is checked before writes.
5. Cloud, paid, destructive, and oversized actions require policy confirmation.
6. GitHub push/PR tools additionally require an edit-capable run, a token, and
   persisted allow-push consent.

The foundations are good, but two production paths violate the model:
implementation policy currently includes publication
(opaihub/agent_policy.py:24-43), and Copilot edit modes bypass tool bounds with
--allow-all-tools (opaihub/accounts.py:1159-1168).

Full Auto itself is deliberately pinned: it is effective only with an explicit
flag and acknowledgement timestamp, otherwise it downgrades to Safe Auto
(opaihub/autonomy.py:30-84; opaihub/gui_preferences.py:130-190). Preserve this
design.

### 2.7 Git and change review

The active repository service resolves the Git root, branch, remote, and dirty
paths (opaihub/repo_context.py:83-114). Dirty-path classification blocks overlap
with intended writes while allowing unrelated user work to remain untouched
(opaihub/repo_context.py:170-212). The bounded provider tool executor rechecks
this before patching or writing (opaihub/provider_tools.py:313-403).

Diff review is path-attributed, bounded, marks risky files, records
approve/reject/pending decisions, and does not mutate source merely because a
review decision was stored (opaihub/diff_review.py:175-283). GitHub merge
orchestration has explicit test, branch, secret, unrelated-file, conflict, and
check gates (opaihub/github_workflow.py:115-147, 519-529).

Actual push/PR tools fail closed unless a GitHub token and revocable
allow-push consent exist (opaihub/github_connector.py:95-204, 254-264;
opaihub/provider_tools.py:157-172). The remaining defect is the earlier policy
layer advertising publication for ordinary implementation.

### 2.8 Release and test system

The local release gate runs Ruff format/check, the full unittest suite,
registry validation, and Bandit (scripts/ci_local.py:34-44). Hosted CI also
defines Python tests, dependency/security checks, JavaScript/E2E, and a
Windows/Ubuntu/macOS clean-wheel matrix.

Neither hosted nor self-hosted CI is automatic: both have only
workflow_dispatch triggers (.github/workflows/ci.yml:3-11;
.github/workflows/ci-selfhosted.yml:3-12). The self-hosted workflow records that
its broker can remain “online” while jobs queue indefinitely
(.github/workflows/ci-selfhosted.yml:6-9;
[#264](https://github.com/MarcoLadeira/OPai/issues/264)).

The clean-install smoke builds and installs the core wheel, checks packaged web
assets, and executes CLI smoke commands
(scripts/smoke-install.py:130-185). It does not install desktop-gui, render the
real PySide6 window, or produce and launch distributable Windows/macOS desktop
artifacts. That is the central release-proof gap in
[#290](https://github.com/MarcoLadeira/OPai/issues/290).

## 3. Critical user-journey status

Status meanings: **working** is supported by code and test evidence;
**partial** has a useful path plus an important gap; **blocked** is not safe to
promise for a public alpha.

| Journey | Status | Verified behaviour | Remaining release evidence or defect |
|---|---|---|---|
| Installation and first launch | **Blocked** | Wheel/CLI smoke and packaged web-asset checks exist; opai-gui entry point exists. | No desktop artifact, GUI-extra install, clean first-launch, upgrade, uninstall, signing/notarisation, or macOS artifact proof. See [#290](https://github.com/MarcoLadeira/OPai/issues/290) and [#293](https://github.com/MarcoLadeira/OPai/issues/293). |
| Provider configuration | **Partial** | Connection Doctor, guided account sign-in, free-key keyring storage, live tests, and recovery actions exist (opai/assets/web/app.js:1545-1741). | Needs packaged-app and real-provider smoke; provider capability metadata is fragmented. |
| Open or select a repository | **Working with release validation pending** | The bridge opens a native folder picker, resolves Git root, saves active context, displays branch/dirty count, and keeps workspace recents. | Exercise nested repos, invalid folders, permissions, and reopen behaviour from artifacts. |
| Ask a read-only question | **Partial** | Account, free API, and local paths answer; cloud requires consent; errors are typed. | The model-less CLI uses a different path; local responses do not stream; multi-turn context is absent. |
| Request a code change | **Blocked** | Account providers and free APIs have edit-oriented plumbing and bounded tools. | Copilot over-authority and prose-only local false success make the generic promise unsafe. See [#284](https://github.com/MarcoLadeira/OPai/issues/284) and [#285](https://github.com/MarcoLadeira/OPai/issues/285). |
| Review a proposed change | **Partial/strong** | Current-task paths receive bounded diff evidence, risk markers, and persisted decisions. | A false-success local edit can reach review state with no real change; packaged GUI review is unproved. |
| Apply a change | **Partial** | Repository tools enforce root/path/size/call bounds and dirty overlap; build loop has deterministic apply/rollback. | Provider-specific edit support is inconsistent; Copilot edit must fail closed until bounded. |
| Cancel an operation | **Working per run** | GUI Stop, window close, CLI Ctrl+C, local HTTP cancellation, and account process-tree termination are implemented. | No global active-session registry or crash-time orphan sweep. See [#295](https://github.com/MarcoLadeira/OPai/issues/295). |
| Recover from failure | **Partial/strong** | Typed provider errors, stale-auth cache invalidation, pre-edit checkpoints, interrupted-run recovery, partial output retention, and rollback helpers exist. | Recovery rate is not instrumented; session ownership is not global; packaged crash recovery is untested. |
| Switch between GUI and CLI | **Partial** | CLI asks with --model use the exact GUI pipeline and receipt/event contracts. | Model-less CLI ask and classic GUI remain divergent paths; behavioural parity is not measured. |
| View token and cost information | **Blocked for trust** | Local ledger, route comparison, model-call events, budgets, receipts, and confidence labels exist. | Free GUI calls and summaries can count spend more than once. See [#286](https://github.com/MarcoLadeira/OPai/issues/286). |
| Return to an existing project | **Partial** | Project state, preferences, workflow state, local prompt recents, and caches persist. | Recents are prompts, not conversation memory; there is no visible/editable project memory source. See [#294](https://github.com/MarcoLadeira/OPai/issues/294). |
| Work in a dirty worktree | **Working/strong** | Baseline dirty files are captured; intended-path conflicts block; unrelated work is preserved; checkpoints distinguish run-attributed paths. | Add artifact/E2E coverage and keep this contract unchanged. |
| Handle missing credentials | **Working with validation pending** | Missing key/account states are typed, actionable, and secret-safe; connection status does not expose values. | Verify every provider from a clean packaged installation. |
| Handle provider failure | **Partial/strong** | Stable error codes cover auth, rate limit, timeout, unavailable, invalid config, and stream failure; recovery actions are surfaced. | Real provider contract smoke and lifecycle metrics are absent. |
| Handle partial tool execution | **Partial** | Partial account output survives cancellation; checkpoints and workflow events retain evidence. | No single-flight registry, terminal-outcome invariant, or comprehensive partial-session reconciliation. |
| Handle unsupported or dangerous action | **Blocked until trust patch** | Destructive intent is confirmation-gated; command policy and safety gates fail closed; Full Auto is pinned. | Copilot all-tools and generic publish capabilities contradict the boundary. See [#283](https://github.com/MarcoLadeira/OPai/issues/283) and [#284](https://github.com/MarcoLadeira/OPai/issues/284). |

## 4. Product definition and measurable alpha target

### 4.1 Product definition

**Target users:** solo developers, indie founders, freelancers, and small teams
who already use one or more coding agents and want cost control without losing
useful autonomy.

**Core problem:** agentic development is powerful but opaque. Users repeat
repository context, invoke stronger models than necessary, cannot always tell
what ran, and struggle to reconcile cost with a useful completed outcome.

**Product promise:** give users more useful completed work per token, request,
and euro while preserving correctness, reliability, autonomy, transparency,
privacy, and control.

**Primary differentiation:** OPai is a local-first cost and execution control
plane over providers the user already has. Its defensible value is not “shorter
prompts”; it is measured routing, bounded reusable context, safe tool execution,
recoverable workflow state, and receipts that reconcile model work to outcomes.

**Critical alpha scope**

- A fully free Windows and macOS public alpha.
- One polished desktop task surface and a capable CLI over shared execution
  semantics.
- Read-only questions and bounded repository edits through supported providers.
- Explicit permission and publication boundaries.
- Cancellation, failure recovery, dirty-worktree protection, diff review, and
  understandable receipts.
- Honest local token/cost records with one authoritative spend event per model
  invocation.
- Reproducible artifacts, clean-install smoke, and visible limitations.

**Explicit alpha non-goals**

- Paid tiers, licensing, entitlement enforcement, checkout, or closed-source
  delivery.
- A new editor or IDE.
- A full local tool-execution agent if only prose local models are available.
- A bounded Copilot edit adapter; read-only plus honest mismatch is acceptable.
- Multi-turn durable memory, semantic-index rollout, team governance expansion,
  and OPai Build.
- A claimed savings percentage before outcome-linked baselines exist.

**Constraints and principal risks**

- Python/PySide6 distribution and signing differ across Windows and macOS.
- Provider CLIs expose different permissions, events, and cost signals.
- Private-repository CI minutes are constrained and the self-hosted broker is
  unreliable.
- Reuse can save calls only if invalidation is conservative; stale cache output
  is a correctness failure.
- Local-first operation must not silently turn into public-network execution.

### 4.2 Measurable alpha target

The alpha target is **not a feature count**. It is a release gate:

1. Equivalent GUI and CLI requests resolve the same capability decision.
2. Plain implementation has zero publish/merge/deploy authority; explicit
   prohibitions always win.
3. No supported command construction contains an unrestricted Copilot tool
   flag.
4. Unsupported edit combinations emit one typed blocked result before runner,
   cache, model, receipt, route, diff, or changed-file success.
5. Every real model invocation creates exactly one authoritative model_call;
   route events remain comparison evidence and never increase spend.
6. All selected contract tests, the full Python suite, Ruff, Bandit, registry
   validation, secret scan, JavaScript unit/E2E, and applicable provider
   failure/cancellation tests pass.
7. Fresh Windows and macOS machines can build/install or download, launch,
   complete the agreed smoke journey, exit without orphans, upgrade, uninstall,
   and follow rollback instructions from reproducible artifacts.
8. Signing/notarisation state and every known limitation are visible; absent
   credentials never become a generic success or crash.

The following product metrics are desired but have no trustworthy current
baseline: cost and tokens per completed task, context-use percentage, duplicate
call avoidance, task completion, recovery, time to first useful result,
GUI/CLI parity, receipt coverage, install success, and crash-free sessions.
Instrumentation must land before numerical targets are set.

## 5. Alpha-readiness assessment: 24 founder areas

Each finding below uses the founder brief’s classification vocabulary. Priority
means release ordering, not severity theatre.

### 1. Core task success

- **Classification / priority:** Confirmed defect; P0 alpha blocker.
- **Evidence:** Local run_ask is prose-only and always checks the answer cache
  before invoking a model (opaihub/ask.py:67-140). The GUI local branch does not
  pass edit intent, then maps answered_locally/cache_hit to answered
  (opaihub/gui_pipeline.py:1060-1079). Copilot editing is also unsafe rather
  than bounded (opaihub/accounts.py:1159-1168).
- **User impact:** A user can ask for a code change and receive a success-shaped
  answer even though no code changed, or grant Copilot more authority than the
  selected mode implies.
- **Release impact:** The product cannot credibly promise its primary coding
  journey.
- **Root cause:** Capability support is inferred after routing rather than
  checked at the execution boundary; read-answer and mutation paths share
  status vocabulary.
- **Recommended outcome:** Reject unsupported local and Copilot edits before
  execution with one capability_mismatch shape; preserve supported read paths.
- **Dependencies:** Shared policy/pipeline contract; issues
  [#284](https://github.com/MarcoLadeira/OPai/issues/284) and
  [#285](https://github.com/MarcoLadeira/OPai/issues/285).
- **Risk:** Existing users see less apparent functionality; that is an honest
  capability reduction, not a regression.
- **Validation:** Runner-not-called, cache-not-called, no-event/no-receipt,
  changed-files-empty, GUI/CLI parity, and full regression tests.

### 2. Cost-saving effectiveness

- **Classification / priority:** Confirmed defect plus missing product
  capability; P0 for exact accounting, P1 for outcome measurement.
- **Evidence:** A free call records a route inside run_explicit_model
  (opaihub/ask.py:159-224), a model_call in app state
  (opai/app_state.py:761-784), and another GUI route
  (opaihub/gui_pipeline.py:783-804). Budget spend accepts both route and
  model_call (opaihub/budget.py:77-92), and ledger actual spend sums both
  (opaihub/ledger.py:375-390).
- **User impact:** The same call can look more expensive than it was; budget
  headroom can be consumed by estimates rather than incurred calls.
- **Release impact:** This invalidates the core cost-efficiency differentiation.
- **Root cause:** Route comparison evidence and authoritative spend events lack
  distinct aggregation semantics; GUI and application state both own routing
  telemetry.
- **Recommended outcome:** model_call is the only spend event; route remains a
  separate counterfactual; add outcome-linked metrics only after exactness.
- **Dependencies:** [#286](https://github.com/MarcoLadeira/OPai/issues/286)
  before [#288](https://github.com/MarcoLadeira/OPai/issues/288), under
  [epic #292](https://github.com/MarcoLadeira/OPai/issues/292).
- **Risk:** Historic ledgers contain legacy semantics. Do not rewrite them
  silently; version and explain summaries.
- **Validation:** One route plus one model_call per integrated call; budget and
  spend summary equal the sum of model_calls; route estimates remain queryable.

### 3. Desktop UX

- **Classification / priority:** UX problem and performance/reliability risk;
  P1 high, with freeze paths release-critical.
- **Evidence:** The web UI has live status, activity, diff review, recovery
  cards, keyboard actions, and responsive presentation. However boot,
  statusLine, dashboard, settingsData, refreshModels, and applyTool are
  synchronous QWebChannel slots (opai/gui_web.py:437-529, 808-816). Local
  runners explicitly request non-streamed responses
  (opaihub/local_runner.py:200-269).
- **User impact:** Large repositories can freeze the window, and local-first
  answers can show only a spinner for tens of seconds.
- **Release impact:** Public-alpha users may interpret a responsive-workflow
  defect as a crash or duplicate a request.
- **Root cause:** Expensive payload work is mixed into GUI-thread slots; local
  transport was implemented as full-response JSON.
- **Recommended outcome:** Move heavy slots to cancellable workers with stale
  result guards; add honest local streaming or explicit non-streaming state.
- **Dependencies:** Existing Calm Stream work and
  [epic #295](https://github.com/MarcoLadeira/OPai/issues/295), especially
  existing issues #146 and #154.
- **Risk:** Async conversion can reorder state and introduce stale updates.
- **Validation:** GUI heartbeat/p95 interaction test on small and large repos,
  stale-response tests, local first-token timing, cancel during each slot.

### 4. CLI UX

- **Classification / priority:** UX problem and architectural weakness; P1.
- **Evidence:** opai ask --model streams activity, preserves Ctrl+C semantics,
  prints JSON when requested, and calls the GUI pipeline
  (opai/cli_stream.py:85-223). Without --model it uses legacy run_ask
  (opai/cli.py:647-669).
- **User impact:** Behaviour, statuses, permissions, and receipts can change
  because a user omitted a model flag rather than because intent changed.
- **Release impact:** “Fully capable CLI” and parity claims are only partially
  true.
- **Root cause:** Backward-compatible local ask was retained beside the newer
  shared pipeline.
- **Recommended outcome:** Route both forms through one execution contract while
  retaining stable output/exit codes and an explicit local-only selection.
- **Dependencies:** Trust patch first; then provider lifecycle and CLI contract
  tests.
- **Risk:** Scripts may depend on legacy output.
- **Validation:** Golden JSON/exit-code matrix for GUI-equivalent and legacy CLI
  invocations; deprecation compatibility tests.

### 5. GUI/CLI parity

- **Classification / priority:** Architectural weakness; P1 alpha-critical.
- **Evidence:** Shared execution exists only for provider/model-selected CLI
  asks (opai/cli_stream.py:1-11). The model-less path is separate, and the
  classic and web GUI remain separate renderers.
- **User impact:** The same natural-language task can receive different mode,
  cancellation, cost, and mismatch behaviour by surface.
- **Release impact:** Parity is not yet a release-quality invariant.
- **Root cause:** Shared core adoption is incremental and no cross-surface
  decision-table gate covers all entry points.
- **Recommended outcome:** Establish one request/result contract and a
  surface-adapter layer; do not rewrite the functioning core.
- **Dependencies:** [#291](https://github.com/MarcoLadeira/OPai/issues/291) and
  [#295](https://github.com/MarcoLadeira/OPai/issues/295).
- **Risk:** Consolidation can break CLI compatibility or GUI-specific recovery.
- **Validation:** Feed equivalent fixtures to GUI bridge, streaming CLI, and
  model-less CLI; assert policy, terminal status, events, changed files, and
  ledger deltas.

### 6. Architecture

- **Classification / priority:** Architectural weakness with strong reusable
  foundations; P1.
- **Evidence:** opaihub provides shared policy, provider, repository, ledger,
  checkpoint, and workflow modules; gui_pipeline is a real shared orchestration
  seam. Yet provider metadata and lifecycle ownership are distributed, and
  local read/edit semantics split between run_ask and run_explicit_model.
- **User impact:** Inconsistent edge-case behaviour appears despite a coherent
  product surface.
- **Release impact:** Small provider changes can cross many modules and regress
  permission or accounting truth.
- **Root cause:** Capability, lifecycle, and spend ownership evolved feature by
  feature rather than around one execution boundary.
- **Recommended outcome:** Narrowly consolidate capability checks, terminal
  outcomes, and telemetry ownership around existing interfaces.
- **Dependencies:** Trust and cost epics; no platform rewrite.
- **Risk:** A broad “clean architecture” refactor would delay alpha and discard
  proven safeguards.
- **Validation:** Architecture contract tests plus diff review proving no
  duplicate execution/accounting path remains.

### 7. Provider abstraction

- **Classification / priority:** Confirmed security defect and architectural
  weakness; P0/P1.
- **Evidence:** ProviderCapabilities and ProviderExecutionPlan already intersect
  intent, run mode, and physical support
  (opaihub/provider_adapters.py:33-111). Copilot command construction bypasses
  that bounded model with --allow-all-tools
  (opaihub/accounts.py:1159-1168). Provider facts also remain split across
  several registries.
- **User impact:** Provider selection does not reliably communicate or enforce
  the same capability promise.
- **Release impact:** Copilot edits are a P0 trust failure; fragmented health
  metadata is a P1 reliability burden.
- **Root cause:** Native CLI flags are constructed after generic capability
  planning, and the adapter contract is not the sole source of truth.
- **Recommended outcome:** Fail Copilot edits closed now; later make one
  capability/health record feed picker, doctor, router, and runner.
- **Dependencies:** [#284](https://github.com/MarcoLadeira/OPai/issues/284) then
  [#295](https://github.com/MarcoLadeira/OPai/issues/295).
- **Risk:** Provider CLI versions and flags change outside OPai.
- **Validation:** Command snapshots by provider/mode; unsupported-capability
  matrix; versioned live smoke with no repository mutation.

### 8. Prompt and context efficiency

- **Classification / priority:** Cost-efficiency opportunity with correctness
  risk; P1 for safe cache semantics, P2 for evaluated retrieval.
- **Evidence:** Local prompts include compact languages, markers, test commands,
  changed-file tail, and diff stat (opaihub/ask.py:32-51). Context packs enforce
  a character budget (opaihub/context_pack.py:76-141). The shared fingerprint
  uses HEAD plus porcelain lines, not dirty file bytes
  (opaihub/evidence_cache.py:55-82). CostTelemetry records aggregate
  input/output/total token counts and a measurement source
  (opaihub/cost_telemetry.py:38-56, 97-132), but it has no provider-specific
  prompt-cache read/write fields, retention/configuration record, or stable-
  prefix contract.
- **User impact:** Compact context saves tokens, but same-path dirty content can
  reuse a stale answer.
- **Release impact:** Incorrect reuse undermines both task quality and savings
  claims.
- **Root cause:** The result cache was optimized for a cheap repository-state
  signal before a bounded content hash and expiry contract existed, while
  provider prompt caching is not modelled separately from OPai result reuse.
- **Recommended outcome:** Content-aware, expiring, conservative result reuse;
  bypass when the fingerprint is incomplete. Treat provider prompt caching as
  an adapter-owned concern: stable instructions/tool definitions precede
  variable task and repository deltas, native cache read/write fields remain
  provider-specific, and unavailable data remains unknown. Evaluate retrieval
  before default.
- **Dependencies:** [#287](https://github.com/MarcoLadeira/OPai/issues/287)
  before [#289](https://github.com/MarcoLadeira/OPai/issues/289).
- **Risk:** Hashing large worktrees adds latency and must never persist content.
- **Validation:** Same-path byte-change, untracked, ignored, binary, oversized,
  expiry, corruption, and clean fast-path tests; adapter fixtures preserve any
  provider-native cache fields without inventing a universal hit rate; quality
  remains non-inferior.

### 9. Repository understanding

- **Classification / priority:** Missing product capability and research
  question; P2, not a launch blocker.
- **Evidence:** LocalSemanticIndex exists and is reachable from ACI
  (opaihub/aci.py:118-146; opaihub/semantic_index.py:135 onward), but production
  ask context still uses a small evidence summary rather than indexed retrieval
  (opaihub/ask.py:32-51).
- **User impact:** Repeated tasks can repeat scans/context and may omit relevant
  symbol/import relationships.
- **Release impact:** The alpha can launch without this, but OPai cannot yet
  prove its deeper reusable-repository differentiation.
- **Root cause:** Index components were built before an evaluated production
  selection contract and outcome metrics.
- **Recommended outcome:** Incremental, explainable context selection with safe
  fallback and user-visible provenance.
- **Dependencies:** Exact accounting, cache correctness, and task-outcome
  instrumentation; [#289](https://github.com/MarcoLadeira/OPai/issues/289) and
  [#294](https://github.com/MarcoLadeira/OPai/issues/294).
- **Risk:** Retrieval omissions silently reduce code quality.
- **Validation:** Representative task fixtures comparing completion quality,
  selected tokens, update latency, and fallback rate against current behaviour.

### 10. Agent autonomy

- **Classification / priority:** Confirmed defect and security risk; P0.
- **Evidence:** Generic IMPLEMENT contains create_branch, commit, push, and
  create_pr; SHIP only adds merge_pr (opaihub/agent_policy.py:24-43). The
  generated capability contract advertises the resulting list to providers
  (opaihub/agent_policy.py:213-279).
- **User impact:** “Fix this locally” grants a broader described authority than
  requested.
- **Release impact:** Violates least authority and makes publication controls
  ambiguous.
- **Root cause:** Local implementation and publication were modelled as one
  capability bundle.
- **Recommended outcome:** Separate local implementation, publication, and ship;
  add publish only for a positive explicit request, with clause-aware negation.
- **Dependencies:** [#283](https://github.com/MarcoLadeira/OPai/issues/283).
- **Risk:** Natural-language matching can over-grant on negated or quoted text.
- **Validation:** Positive, negative, mixed-clause, and surface-parity decision
  tables; ordinary edit never exposes publish/merge/deploy.

### 11. User permissions and safety

- **Classification / priority:** Security risk with confirmed strong controls;
  P0 for bypass removal.
- **Evidence:** Destructive intent resolves to a confirmation-required,
  read-only policy (opaihub/agent_policy.py:46-50, 123-163). Policy forces cloud
  confirmation (opaihub/policy.py:133-203), command rules fail closed
  (opaihub/guarded.py:127-166), and Full Auto requires a durable acknowledgement.
  Copilot edit modes still request all tools.
- **User impact:** Most dangerous paths are understandable and gated, but one
  provider escapes the boundary.
- **Release impact:** A single bypass is enough to block a safety claim.
- **Root cause:** Provider-native permissions are not uniformly constrained by
  OPai’s tool executor.
- **Recommended outcome:** Remove the unrestricted flag and return a typed
  mismatch until a bounded Copilot adapter exists. Preserve existing layered
  gates.
- **Dependencies:** [#284](https://github.com/MarcoLadeira/OPai/issues/284).
- **Risk:** Users may assume Safe Auto means “works everywhere”; copy must say
  which providers support edits.
- **Validation:** Static search for unsafe flags, runner-not-called tests,
  provider/mode capability UI, destructive-action manual matrix.

### 12. Reliability and failure recovery

- **Classification / priority:** Reliability risk with mature per-call
  foundations; P1 high.
- **Evidence:** Account runners isolate process groups and terminate trees
  (opaihub/process_tree.py:1-85; opaihub/accounts.py:216-254); local HTTP closes
  mid-flight on cancellation (opaihub/local_runner.py:97-176); checkpoints
  capture pre-edit state and recover interrupted runs
  (opaihub/checkpoints.py:167-310). There is no global active-session registry.
- **User impact:** Stop and crash recovery are materially safer than typical
  prototypes, but retries can overlap and crash-surviving processes are not
  centrally visible.
- **Release impact:** Per-run cancellation is credible; single-flight/orphan
  guarantees are not.
- **Root cause:** Process ownership is local to each runner call.
- **Recommended outcome:** Add one request/session registry, single-flight
  cancellation, startup orphan sweep, and terminal-outcome invariant.
- **Dependencies:** Stable request IDs and
  [#295](https://github.com/MarcoLadeira/OPai/issues/295).
- **Risk:** Cross-platform process handles and stale PID reuse.
- **Validation:** Retry-race, window-close, Ctrl+C, timeout, crash/restart, child
  process, and orphan-count tests on Windows and macOS.

### 13. Git integration

- **Classification / priority:** Confirmed permission defect around an otherwise
  strong integration; P0.
- **Evidence:** Push/PR tools require edit mode, stored token, and allow-push
  consent (opaihub/provider_tools.py:157-172). The connector validates tokens
  before storage and revokes consent on disconnect
  (opaihub/github_connector.py:110-204). Generic implementation nevertheless
  advertises push/PR (opaihub/agent_policy.py:25-36).
- **User impact:** Runtime tools often remain unavailable, but the provider
  contract says publication is authorized, producing confusing or unsafe intent.
- **Release impact:** Publication must be explicit before alpha.
- **Root cause:** Tool availability has stronger gates than policy capability
  resolution.
- **Recommended outcome:** Make policy at least as restrictive as tool exposure;
  retain double consent and merge gates.
- **Dependencies:** [#283](https://github.com/MarcoLadeira/OPai/issues/283).
- **Risk:** Existing tests may encode overbroad capabilities.
- **Validation:** End-to-end matrix across plain edit, commit request, push
  request, PR request, explicit prohibition, missing token, and consent off.

### 14. Performance

- **Classification / priority:** Performance problem; P1 high.
- **Evidence:** Multiple GUI slots synchronously compute overview, workspace,
  dashboard, settings, model discovery, or repair payloads
  (opai/gui_web.py:437-529, 808-816). Local runners use stream=False
  (opaihub/local_runner.py:231, 269, 314). Repository-work and ledger caches do
  exist, so this is not a blank slate.
- **User impact:** Freezes and delayed first output can trigger duplicate sends,
  forced quits, or abandonment.
- **Release impact:** Large-repository desktop confidence is incomplete.
- **Root cause:** Expensive reads are hidden behind synchronous bridge contracts,
  and local transport buffers full output.
- **Recommended outcome:** Async bridge workers, bounded payload caches,
  measurable startup/interaction latency, and true or honestly labelled local
  streaming.
- **Dependencies:** Provider lifecycle/session work and existing #146/#154.
- **Risk:** Caching can become stale; async results can overwrite newer state.
- **Validation:** Cold/warm startup, large-repo dashboard, settings/model refresh,
  time-to-first-token, memory, and stale-request benchmarks.

### 15. Security and privacy

- **Classification / priority:** Security risk due to one confirmed provider
  bypass; otherwise a strength to preserve. P0.
- **Evidence:** Credentials use keyring/environment without exposing values
  (opaihub/credentials.py:1-5, 70-117); provider child environments strip
  session-hijacking variable names without logging values
  (opaihub/proc.py:35-111); diagnostics and workflow data are redacted
  (opai/provider_contract.py:162-178, 286-320;
  opaihub/workflow_ledger.py:22-64); the web view blocks remote content
  (opai/gui_web.py:934-951). MCP config rendering makes path-granting servers
  read-only by default and blocks .git, .env, secrets, and caches
  (opaihub/mcp.py:23-103, 147-153); MCP runtime blocks unapproved servers and
  remote transport without current-task authorization
  (opaihub/mcp_runtime.py:151-170, 240-265). Copilot all-tools remains the
  confirmed exception. This cycle did not execute adversarial indirect-prompt-
  injection, malicious-tool-output, token-passthrough, or local-server
  sandbox fixtures.
- **User impact:** Secret and egress defaults are strong, but unrestricted tool
  authority can expose more repository/system surface than intended. Retrieved
  repository text, web/tool output, and persisted memory are still untrusted
  instructions: they must not be able to expand deterministic capabilities.
- **Release impact:** Security posture is promising but not releasable with the
  bypass or without evidence that MCP/path/remote and indirect-instruction
  boundaries resist adversarial inputs.
- **Root cause:** Provider-native command permissions are outside the bounded
  repository-tool path; MCP enforcement exists, but the repository has not yet
  made the untrusted-content boundary an explicit adversarial test contract.
- **Recommended outcome:** Remove bypass; retain keyring, redaction, local web
  isolation, MCP approval/path/write/remote controls, endpoint classification,
  and audit design. Add fixtures proving injected repository/tool/MCP content
  cannot alter authority, leak tokens, escape allowed paths, or make a remote
  call without explicit authorization.
- **Dependencies:** [#284](https://github.com/MarcoLadeira/OPai/issues/284).
- **Risk:** Static secret regexes cannot catch every credential form; provider
  CLIs and local/remote MCP servers are external trust dependencies. Prompt
  injection can still influence planning within an allowed capability, so
  output validation and human confirmation remain necessary.
- **Validation:** Command audit, egress tests, seeded-secret redaction tests,
  keyring failure tests, malicious repository/tool/MCP fixtures, remote/token
  passthrough denial tests, local-server path/write sandbox tests, and release
  secret scan.

### 16. Testing

- **Classification / priority:** Reliability risk; P0 contract tests and release
  matrix, despite a strong baseline.
- **Evidence:** 1,503 Python tests pass, and local static/security gates are
  green. There are 104 Python test files and 50 JavaScript test files. This
  cycle did not run Vitest or Playwright, and some current tests explicitly
  expect the unsafe Copilot flag/overbroad policy behaviour.
- **User impact:** Many regressions are caught, but desktop and permission
  contracts can still be wrong while the main suite is green.
- **Release impact:** Passing Python tests alone is insufficient for a desktop
  alpha.
- **Root cause:** Tests grew with implementation behaviour; automatic CI and
  artifact-level validation lagged.
- **Recommended outcome:** Change unsafe expectations test-first; run Python,
  JavaScript, E2E, provider contracts, failure/cancel, packaging, and platform
  smokes as one release evidence set.
- **Dependencies:** Trust implementation and
  [#293](https://github.com/MarcoLadeira/OPai/issues/293).
- **Risk:** Mock-heavy suites can prove shape without proving a real provider or
  Qt runtime.
- **Validation:** Preserve red-first evidence for each defect; clean-environment
  real-process/artifact smoke; automatic required checks.

### 17. Observability

- **Classification / priority:** Missing product capability plus confirmed
  accounting defect; P0/P1.
- **Evidence:** OPai emits structured activity, usage ledger, workflow ledger,
  checkpoints, receipts, and a hash-chained governance audit. Spend and route
  semantics are currently conflated, and no durable task-outcome record joins
  terminal success, model/tool cost, cache decisions, context, latency, and
  recovery.
- **User impact:** Users can inspect a run, but cannot reliably compare useful
  work per euro or diagnose aggregate failure/recovery.
- **Release impact:** Exact spend is P0; broader product metrics are a P1
  foundation for differentiation.
- **Root cause:** Event streams were designed independently around features
  rather than one request/outcome lifecycle.
- **Recommended outcome:** Fix exact-once spend, then add privacy-safe terminal
  outcomes keyed by stable request IDs and reconciled to ledgers.
- **Dependencies:** [#286](https://github.com/MarcoLadeira/OPai/issues/286),
  [#288](https://github.com/MarcoLadeira/OPai/issues/288), and
  [#295](https://github.com/MarcoLadeira/OPai/issues/295).
- **Risk:** Bad outcome definitions reward short failed answers; telemetry must
  not store prompts or source.
- **Validation:** Ledger reconciliation, one terminal per task, unknown-value
  preservation, privacy fixtures, GUI/CLI summary parity.

### 18. Packaging and installation

- **Classification / priority:** Missing product capability; P0 alpha blocker.
- **Evidence:** Setuptools packages Python and web assets
  (pyproject.toml:35-50); smoke-install verifies the core wheel and asset
  presence (scripts/smoke-install.py:130-185). Desktop dependencies are optional
  (pyproject.toml:12-14), and no real desktop executable/app artifact is built
  or launched.
- **User impact:** A non-developer cannot yet receive one artifact and know the
  desktop app will launch.
- **Release impact:** A desktop public alpha does not exist as a distributable,
  verified product.
- **Root cause:** Wheel correctness was prioritized before GUI artifact,
  signing, upgrade, and removal lifecycles.
- **Recommended outcome:** Reproducible free Windows and macOS artifacts,
  clean-runner launch smoke, assets/first-run/exit/upgrade/uninstall/rollback,
  checksums, provenance, and explicit signing status.
- **Dependencies:** [#290](https://github.com/MarcoLadeira/OPai/issues/290) under
  [#293](https://github.com/MarcoLadeira/OPai/issues/293).
- **Risk:** Signing/notarisation credentials and platform-specific deployment
  behaviour are external.
- **Validation:** Fresh VMs/runners with no source checkout or developer
  Python; artifact launch and lifecycle matrix.

### 19. Cross-platform behaviour

- **Classification / priority:** Reliability risk; P0 release proof.
- **Evidence:** Windows/POSIX process-group termination is explicitly implemented
  (opaihub/process_tree.py:31-85); installers exist for PowerShell and POSIX;
  hosted CI defines Windows, Ubuntu, and macOS wheel jobs
  (.github/workflows/ci.yml:126-156). The workflow is manual and no desktop
  artifact evidence was produced.
- **User impact:** Cross-platform code paths may work, but users cannot rely on
  installer, window, cancellation, path, or process behaviour on both target
  operating systems.
- **Release impact:** The Windows/macOS product vision is unproved.
- **Root cause:** Cross-platform unit design exists without continuous,
  artifact-level execution.
- **Recommended outcome:** Make target-platform artifact smoke a visible release
  gate; keep Linux wheel coverage useful but not a desktop launch blocker.
- **Dependencies:** [#293](https://github.com/MarcoLadeira/OPai/issues/293),
  issue #18, and reliable CI delivery.
- **Risk:** A single maintainer machine can hide clean-HOME, locale, path,
  keychain, permissions, and signing problems.
- **Validation:** Clean Windows/macOS matrix for install, GUI/CLI, local paths,
  keychain fallback, cancel, close, upgrade, uninstall, and rollback.

### 20. Documentation and onboarding

- **Classification / priority:** UX problem and product-truth defect; P0 copy
  correction, P1 journey validation.
- **Evidence:** Connection Doctor and local runtime onboarding provide typed
  states and consent-gated next steps
  (opaihub/local_onboarding.py:32-40, 149-184, 218-252). README still says alpha
  access is controlled and paid users receive private packages
  (README.md:216-239), while commercial docs prescribe controlled/paid alpha
  (docs/COMMERCIAL_ACCESS_AND_IP_PROTECTION.md:9-66).
- **User impact:** A user cannot tell whether launch is free, private, paid, or
  open-core, even though setup guidance is technically useful.
- **Release impact:** Contradictory promises prevent a coherent launch and can
  misrepresent access.
- **Root cause:** Product strategy changed to fully free launch faster than
  identity, release, and commercial documents.
- **Recommended outcome:** State free launch clearly; move pricing to
  post-launch evidence-led discovery; retain honest credential/local setup
  guidance and known limitations.
- **Dependencies:** Trust programme documentation update, then release epic.
- **Risk:** Updating only one document leaves contradictory entry points.
- **Validation:** Repository-wide claim scan, fresh-user comprehension test,
  quickstart from artifact, no payment/license requirement in release flow.

### 21. Accessibility

- **Classification / priority:** UX validation gap; P1.
- **Evidence:** The web UI includes semantic labels, live regions, keyboard
  actions, focus-visible styles, reduced-motion rules, keyboard-operable
  confirmations, and changed-file review controls
  (opai/assets/web/index.html:18-153;
  opai/assets/web/styles.css:91, 713-755;
  opai/assets/web/app.js:1784-1817, 1941-2018). Playwright accessibility tests
  exist but were not run in this cycle.
- **User impact:** Keyboard users have meaningful support, but full
  screen-reader, focus-order, contrast, zoom, and error-announcement journeys
  are unverified.
- **Release impact:** Public desktop accessibility confidence is incomplete.
- **Root cause:** Accessibility features were implemented incrementally without
  a complete artifact-level audit.
- **Recommended outcome:** Run automated E2E plus manual keyboard/screen-reader
  journeys on target artifacts; fix blockers without redesigning working UI.
- **Dependencies:** JavaScript tool installation and release artifacts; existing
  epic #220.
- **Risk:** ARIA presence can mask incorrect focus or announcement behaviour.
- **Validation:** Keyboard-only critical journey, Windows Narrator and macOS
  VoiceOver smoke, 200% zoom, reduced motion, contrast, error/recovery live
  regions.

### 22. Release operations

- **Classification / priority:** Missing release capability; P0.
- **Evidence:** Hosted and self-hosted workflows are workflow_dispatch only
  (.github/workflows/ci.yml:3-11;
  .github/workflows/ci-selfhosted.yml:3-12). The self-hosted runner can wedge
  while appearing online. Release documentation and local preflight components
  exist, but no automatic required check or artifact proof gates this snapshot.
- **User impact:** A release can depend on maintainer memory and pass source
  tests while shipping an unusable desktop.
- **Release impact:** No repeatable, enforceable public-alpha release decision.
- **Root cause:** CI cost constraints and runner reliability led to manual
  enforcement.
- **Recommended outcome:** Reliable automatic merge gate plus deliberate
  cross-platform release matrix, reproducible preflight/dry run, checksums,
  rollback rehearsal, and published evidence.
- **Dependencies:** [#293](https://github.com/MarcoLadeira/OPai/issues/293),
  existing #32 and #264.
- **Risk:** Re-enabling hosted jobs without budget controls can exhaust minutes;
  relying only on self-hosted CI can silently queue forever.
- **Validation:** Deliberately broken PR must fail; healthy PR starts within a
  bound; release dry run is non-publishing; rollback rehearsal is recorded.

### 23. Product positioning

- **Classification / priority:** Product-definition/UX problem; P1, with launch
  copy required before release.
- **Evidence:** README and pyproject clearly position a local-first AI coding cost
  firewall (README.md:1-14; pyproject.toml:5-10), while identity docs describe
  cost-aware control. Other docs still describe paid controlled alpha and
  license-gated Pro features. Outcome-linked savings are not yet measurable.
- **User impact:** The “why” is compelling, but access, proof, and the boundary
  between control plane and coding agent are confusing.
- **Release impact:** Messaging can overclaim measured savings or send users to
  obsolete paid/private flows.
- **Root cause:** Positioning combines current controls, future platform
  ambitions, and outdated monetisation plans.
- **Recommended outcome:** Launch as a fully free, local-first execution and cost
  control plane; describe measured facts and unknowns; price only after usage
  evidence.
- **Dependencies:** Exact accounting
  ([#292](https://github.com/MarcoLadeira/OPai/issues/292)) and release proof
  ([#293](https://github.com/MarcoLadeira/OPai/issues/293)).
- **Risk:** “Cost firewall” can imply guaranteed savings when only route
  estimates exist.
- **Validation:** Claim-to-code/evidence matrix, launch-copy review, user
  interviews after instrumented alpha tasks.

### 24. Technical debt that directly threatens alpha

- **Classification / priority:** Confirmed cross-cutting alpha risk; P0.
- **Evidence:** Four examples share the same pattern: implementation bundles
  publication, Copilot bypasses bounded tools, local prose results masquerade as
  edit completion, and route/model events have competing spend ownership.
  Manual CI then reduces the chance those boundaries are continuously enforced.
- **User impact:** The product can be polished and test-rich while telling an
  untrue story about authority, mutation, or cost.
- **Release impact:** This is the minimum debt cluster that blocks alpha
  credibility.
- **Root cause:** Duplicated ownership at boundaries—not generally poor code or
  a need for a rewrite.
- **Recommended outcome:** Complete
  [epic #291](https://github.com/MarcoLadeira/OPai/issues/291), then exact
  release proof. Defer broad refactors.
- **Dependencies:** Issues #283-#286 and release epic #293.
- **Risk:** Treating all technical debt as P0 dilutes focus; treating these four
  as polish creates user harm.
- **Validation:** Decision-table tests, no-runner mismatch tests, ledger
  reconciliation, full regression, artifact gates, and independent diff review.

## 6. Confirmed strengths that should not be redesigned

| Strength | Evidence | Preserve because |
|---|---|---|
| Explicit Full Auto pinning | opaihub/autonomy.py:30-84; opaihub/gui_preferences.py:130-190 | A stale preference cannot silently reopen unrestricted mode. |
| Dirty-worktree conflict handling | opaihub/repo_context.py:170-212; opaihub/provider_tools.py:343-403 | It protects unrelated user work without banning all work in a dirty repository. |
| Bounded free-provider repository tools | Read/write/Git vocabularies and 12-call/size caps at opaihub/provider_tools.py:17-30, 542-625 | It makes smaller/free models useful without granting arbitrary shell access. |
| True per-run cancellation | opaihub/process_tree.py:1-107; opaihub/local_runner.py:97-176; opai/gui_web.py:421-434 | Stop reaches child processes and local HTTP rather than hiding a late answer. |
| Secret-safe provider boundary | opaihub/credentials.py, opaihub/proc.py, opai/provider_contract.py | Credentials remain in environment/keychain and errors/events are redacted. |
| Local web isolation | opai/gui_web.py:929-951 | The presentation layer has no remote URL or clipboard read authority. |
| Recoverable checkpoints | opaihub/checkpoints.py:98-114, 167-310 | Edit-capable execution has pre-run evidence and interrupted-state recovery. |
| Tamper-evident governance audit | opaihub/audit.py:41-55, 98-125, 149-213 | It is append-only, redacted, hash-chained, and verifiable. |
| Explicit GitHub consent and merge gates | opaihub/github_connector.py:110-204; opaihub/github_workflow.py:115-147 | Connecting a token alone does not authorize publication or merge. |
| Typed provider error/recovery vocabulary | opai/provider_contract.py:24-134, 199-320 | GUI, CLI, and backend can explain actionable failures consistently. |

The programme should narrow unsafe edges around these systems, not replace them.

## 7. Alpha blockers versus P1 and post-alpha

| Horizon | Work | Why | Exit condition |
|---|---|---|---|
| **P0 public-alpha blocker** | [#283 explicit publish intent](https://github.com/MarcoLadeira/OPai/issues/283) | Least-authority and publication truth | Plain edits cannot push/open PR/merge/deploy; negation wins. |
| **P0 public-alpha blocker** | [#284 Copilot fail-closed edits](https://github.com/MarcoLadeira/OPai/issues/284) | Removes unrestricted provider authority | No all-tools construction; read works; edit emits mismatch before launch. |
| **P0 public-alpha blocker** | [#285 truthful local edit mismatch](https://github.com/MarcoLadeira/OPai/issues/285) | Stops fabricated mutation success | Edit intent calls no cache/model and produces no success evidence. |
| **P0 public-alpha blocker** | [#286 exact-once spend](https://github.com/MarcoLadeira/OPai/issues/286) | Restores cost truth and budget correctness | One route + one model_call; only model_call spends. |
| **P0 public-alpha blocker** | [#290 desktop artifacts](https://github.com/MarcoLadeira/OPai/issues/290) and [#293 release confidence](https://github.com/MarcoLadeira/OPai/issues/293) | Source/wheel proof is not a desktop release | Clean Windows/macOS artifact lifecycle passes with provenance and limitations. |
| **P0/P1 release-critical** | Existing #146 GUI freeze and #264 CI runner reliability, grouped in [#295](https://github.com/MarcoLadeira/OPai/issues/295) / [#293](https://github.com/MarcoLadeira/OPai/issues/293) | Responsive desktop and enforceable checks | No long main-thread block on critical journey; required validation starts reliably. |
| **P1 public-alpha evidence gate** | [#287 content-aware cache and expiry](https://github.com/MarcoLadeira/OPai/issues/287) | Correctness before reuse savings | Dirty/untracked bytes, schema, expiry, corrupt, and uncertain inputs cannot return a stale result. If it is not landed, disable result reuse rather than only bypassing dirty repositories. |
| **P1 public-alpha evidence gate** | [#288 outcome metrics](https://github.com/MarcoLadeira/OPai/issues/288) | Establishes honest product baselines | Every run has at most one terminal outcome that reconciles calls, cost, cache decisions, recovery, and quality evidence without raw prompt/source telemetry. |
| **P1 release-critical subset** | Provider lifecycle/session registry in [#295](https://github.com/MarcoLadeira/OPai/issues/295) | Single-flight, orphan, and recovery confidence | The launch-supported provider paths have one active process per request, one terminal outcome, bounded cancellation, and zero surviving children in tests. |
| **P1 high** | Full JavaScript/accessibility and real-provider smoke | Closes desktop validation gap | Vitest/Playwright and manual keyboard/screen-reader journeys pass on artifacts. |
| **Post-alpha / evidence-gated** | [#289 bounded index integration](https://github.com/MarcoLadeira/OPai/issues/289) and [#294 repository intelligence](https://github.com/MarcoLadeira/OPai/issues/294) | High-leverage differentiation, not required for truthful launch | Non-inferior quality with measured lower context and conservative fallback. |
| **Post-alpha** | Visible/editable project memory (existing #131) | Improves returning-user efficiency | User can inspect, correct, clear, and trace every persisted fact. |
| **Post-alpha** | OPai Build/product expansion | Broader creation workflow | Reprioritise from alpha evidence, not roadmap momentum. |
| **Post-launch discovery** | Pricing, editions, licensing, paid fulfilment | User has explicitly chosen a fully free launch | Use outcome/support/cost evidence to decide what, if anything, becomes paid. |

## 8. Instrumentation gaps

Current values below are **unknown unless stated otherwise**. Existing estimates
must not be presented as measured outcomes.

| Metric wanted | What exists now | Gap | Instrumentation outcome |
|---|---|---|---|
| Cost per completed task | Route estimates and model-call events | Spend is currently double-countable; no completed-task denominator | Stable request/outcome ID; terminal success category; exact model-call reconciliation. |
| Tokens per completed task | Some provider usage or estimates | No outcome join; some providers do not report tokens | Store measured/estimated/unknown separately on the outcome. |
| Percentage of context actually used | Context byte/token estimates | No attribution from selected context to useful output | Record selected/available context and reasons; evaluate on fixtures, not guessed attention. |
| Cache hit rate | Route cache_hit and local result cache | Result-cache hit/miss/bypass/expiry semantics are incomplete; invalidation unsafe | Versioned cache decision event with age and bypass reason; no raw prompt/content. |
| Duplicate model-call avoidance | Cache can avoid a call | No durable proof that an equivalent call would otherwise occur | Record auditable avoided-call reason tied to one request; tokens remain unknown unless defensible. |
| Task completion rate | Workflow terminal states exist | No one-terminal-per-request outcome or quality criterion | Versioned terminal categories: completed, failed, blocked, cancelled, recovered; test/user evidence field. |
| Failure recovery rate | Typed errors/checkpoints | Recovery attempts and successful continuations are not joined | Parent/child attempt IDs and recovery terminal. |
| Time to first useful result | Activity timestamps | No defined “useful” boundary or aggregate | Define first answer text, first applied diff, or first verified result by task type; preserve unknown. |
| GUI/CLI behavioural parity | Shared-pipeline tests in places | No parity denominator across equivalent requests | Cross-surface fixture run with exact decision/status/event/ledger comparison. |
| Actions with understandable receipts | Receipts exist for several flows | No denominator and unsupported paths can fabricate success-shaped receipt state | Receipt-required terminal contract plus coverage summary. |
| Installation success | Wheel smoke exists | No end-user artifact attempt records by platform | Privacy-safe release test ledger or manual matrix with platform/version/result. |
| Crash-free session rate | No current metric | No session-start/end/crash marker | Local session lifecycle markers with clean-exit reconciliation; opt-in aggregate only if later justified. |
| Model calls avoided | Route estimates and cache decisions | Comparison event can be mistaken for spend/savings | Separate route counterfactual, model_call, and avoided_call event semantics. |
| Added latency from optimisation | Some elapsed activity | No before/after benchmark around cache/index/context | Local deterministic benchmark with warm/cold and bypass cases. |
| Quality impact of optimisation | Eval harness components | No production-context non-inferiority gate | Fixed representative tasks and outcome rubric before enabling retrieval/cache changes. |

[#288](https://github.com/MarcoLadeira/OPai/issues/288) owns the task-outcome
foundation. [#287](https://github.com/MarcoLadeira/OPai/issues/287) owns cache
decision truth, and [#289](https://github.com/MarcoLadeira/OPai/issues/289)
consumes both before claiming context savings.

## 9. Selected immediate programme

### Programme

**[Epic #291 — Alpha trust: truthful capabilities, execution, and publication](https://github.com/MarcoLadeira/OPai/issues/291)**

Execution order:

1. [#283 — Require explicit publish intent before push or pull-request capabilities](https://github.com/MarcoLadeira/OPai/issues/283)
2. [#284 — Fail closed when Copilot cannot safely execute repository edits](https://github.com/MarcoLadeira/OPai/issues/284)
3. [#285 — Return truthful capability mismatch for prose-only local edit requests](https://github.com/MarcoLadeira/OPai/issues/285)
4. [#286 — Count model spend exactly once across GUI and CLI ledgers](https://github.com/MarcoLadeira/OPai/issues/286)

Issue #286 is **primarily owned by
[epic #292 — Cost efficiency](https://github.com/MarcoLadeira/OPai/issues/292)**,
but it belongs in the immediate Alpha Trust programme because an inflated
receipt or budget is the same class of user-trust failure as a fabricated edit
or overbroad permission. Trust is the programme outcome; roadmap ownership does
not dictate implementation sequence.

### Why this is first

- It removes concrete user harm and permission risk.
- It repairs the core edit journey without a speculative provider rewrite.
- It restores the evidence layer used by the product’s primary differentiation.
- Each defect has a narrow seam and can be implemented test-first without paid
  calls, cloud services, packaging changes, or unrelated user work.
- Together the issues make GUI and CLI failures typed and honest.

### User-visible result

- “Fix this” remains local unless publication is explicitly requested.
- “Do not push/open a PR” always keeps publication unavailable.
- Copilot remains available for read-only questions; unsupported edits stop
  before a process starts and explain the safe alternatives.
- Local prose models remain useful for questions; edit requests stop honestly
  instead of claiming a change.
- Cost views and budgets count each real model call once and keep route
  comparisons separately visible.

### Deliberately out of scope

- Building a Copilot sandbox or local autonomous tool adapter.
- Content-aware cache hashing/expiry.
- Multi-turn conversation or durable Memory Vault.
- Semantic-index production integration.
- GUI performance redesign, local token streaming, packaging, signing, CI
  repair, pricing, and licensing.

### Validation

- Red-first policy tests for positive/negative/mixed publish clauses.
- Runner-not-called and no-unrestricted-flag Copilot tests.
- Runner/cache-not-called local mismatch tests.
- GUI/CLI blocked-state, receipt-empty, changed-files-empty, and no-success-event
  parity.
- Exact event-count and budget/summary reconciliation tests.
- Focused suites, full Python suite, Ruff, Bandit, registry validation, secret
  scan, and independent diff review.

## 10. Roadmap issue map

| Issue | Programme role | Alpha relationship |
|---|---|---|
| [#283](https://github.com/MarcoLadeira/OPai/issues/283) | Explicit publish intent | Immediate P0 |
| [#284](https://github.com/MarcoLadeira/OPai/issues/284) | Copilot edit fail-closed | Immediate P0 |
| [#285](https://github.com/MarcoLadeira/OPai/issues/285) | Local prose edit truth | Immediate P0 |
| [#286](https://github.com/MarcoLadeira/OPai/issues/286) | Exact-once spend | Immediate P0; primarily cost epic |
| [#287](https://github.com/MarcoLadeira/OPai/issues/287) | Content-aware expiring result cache | P1 correctness |
| [#288](https://github.com/MarcoLadeira/OPai/issues/288) | Outcome and avoided-call metrics | P1 measurement |
| [#289](https://github.com/MarcoLadeira/OPai/issues/289) | Evaluated bounded context selection | Post-trust P2 |
| [#290](https://github.com/MarcoLadeira/OPai/issues/290) | Windows/macOS desktop artifacts | P0 release proof |
| [#291](https://github.com/MarcoLadeira/OPai/issues/291) | Alpha trust epic | First programme |
| [#292](https://github.com/MarcoLadeira/OPai/issues/292) | Cost truth/reuse/outcomes epic | P0/P1 |
| [#293](https://github.com/MarcoLadeira/OPai/issues/293) | Free alpha release-confidence epic | Next programme |
| [#294](https://github.com/MarcoLadeira/OPai/issues/294) | Repository intelligence/cache/memory epic | Post-trust differentiation |
| [#295](https://github.com/MarcoLadeira/OPai/issues/295) | Provider lifecycle/recovery epic | P1 with release-critical children |

## 11. Readiness gate checklist

### Trust and core execution

- [ ] #283: local implementation never grants publish/merge/deploy by default.
- [ ] #283: explicit publication requests grant only required capabilities; an
  explicit prohibition wins.
- [ ] #284: no supported Copilot command uses an unrestricted all-tools flag.
- [ ] #284: Copilot read-only works; edit intent launches no process and returns
  a typed mismatch.
- [ ] #285: local read-only works; edit intent calls no cache/model/runner and
  produces no success receipt/diff/changed-file claim.
- [ ] Equivalent GUI and CLI fixtures have the same capability and terminal
  outcome.

### Cost and evidence truth

- [ ] #286: one integrated provider call records one route comparison and one
  authoritative model_call.
- [ ] Budgets and actual-spend summaries count model_call only.
- [ ] Route estimated actual remains separately visible as counterfactual
  evidence.
- [ ] Historical semantics are documented rather than silently rewritten.
- [ ] #287 lands: dirty/untracked bytes, schema, expiry, corrupt, oversized,
  and uncertain states cannot yield a stale result; or result reuse is disabled
  before public launch.
- [ ] #288 lands for public-alpha evidence: every supported run has one
  terminal outcome that reconciles authoritative model calls, cost/cache
  decisions, recovery, and quality evidence; unknown values remain unknown.
- [ ] Provider prompt-cache data, when a provider exposes it, remains native to
  that adapter; stable-prefix placement is documented and unsupported cache
  fields are not fabricated.

### Reliability and desktop journeys

- [ ] Stop, Ctrl+C, timeout, retry, and window close leave zero child processes
  in Windows/macOS tests.
- [ ] Interrupted edit checkpoints recover to an understandable, reviewable
  state.
- [ ] Critical heavy bridge work does not block the GUI heartbeat on the agreed
  large-repository fixture.
- [ ] Missing credential, invalid credential, rate limit, provider unavailable,
  partial stream, and unsupported capability have typed recovery journeys.
- [ ] The supported provider paths have bounded cancellation, one terminal
  outcome, and no surviving child process in the platform test matrix.
- [ ] Dirty overlapping files block; unrelated dirty files are preserved.

### Test and release proof

- [ ] Focused trust/accounting suites pass.
- [ ] Full Python suite, Ruff, Bandit, registry validation, compileall, dependency
  audit, and secret scan pass.
- [ ] Vitest and Playwright suites pass from a clean dependency install.
- [ ] Required CI starts reliably and cannot be bypassed by a normal merge.
- [ ] Reproducible Windows and macOS free-alpha artifacts are built from the
  release commit.
- [ ] Artifact GUI assets render; CLI runs without source checkout.
- [ ] First launch, folder open, provider setup/missing-provider, read question,
  supported edit/review, cancellation, exit, upgrade, uninstall, and rollback
  pass on clean target systems.
- [ ] Checksums, provenance, signing/notarisation status, known limitations, and
  rollback instructions are published.
- [ ] Release copy says the whole launch is free and contains no payment,
  entitlement, or private-package gate.

**Until every applicable P0 and explicitly release-critical P1 box is checked
with stored evidence, OPai remains a pre-alpha and the public-alpha claim stays
blocked.**

## 12. Next programme: free alpha release confidence

After the trust patch, execute
**[epic #293 — Free alpha release confidence: artifacts, install, CI, rollback](https://github.com/MarcoLadeira/OPai/issues/293)**.

Recommended order:

1. Stabilise CI delivery and make the fast trust/test gate automatic, resolving
   the self-hosted queue failure without uncontrolled hosted spend.
2. Complete clean install/activation and deterministic preflight/rollback work
   in existing issues #18 and #32.
3. Deliver [#290](https://github.com/MarcoLadeira/OPai/issues/290): reproducible
   free Windows and macOS desktop artifacts with real GUI/CLI smoke.
4. Run the complete critical-journey, accessibility, process-cleanup, upgrade,
   uninstall, and rollback matrix.
5. Publish only the evidence-backed free alpha, with explicit signing status and
   known limitations.

The expected user-visible outcome is simple: a person without the repository,
developer Python, or maintainer knowledge can obtain OPai for free, launch it,
understand provider state, open a repository, complete a supported task, stop
it safely, see truthful cost evidence, and remove or roll back the application.

## Evidence limitations

- No paid or live cloud model call was made; provider conclusions come from
  production command construction, local contracts, and tests.
- JavaScript unit and Playwright E2E suites were not executed because their
  development dependencies were not installed in the established baseline.
- No macOS runtime, Windows packaged desktop artifact, signing, notarisation,
  installer, upgrade, uninstall, or rollback journey was executed.
- The complete Python suite was executed once at the baseline commit; this
  document does not claim subsequent implementation commits pass until they are
  re-run.
- GitHub issue/milestone state was read on 2026-07-12 and can change after this
  snapshot.
- Static Bandit and dependency checks reduce known risk but do not prove absence
  of vulnerabilities in provider CLIs, QtWebEngine, OS keyrings, or packaging.
