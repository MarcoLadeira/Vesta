# Agentic coding cost and trust research for Vesta's free alpha

- **Research date:** 2026-07-12
- **Decision:** launch Vesta fully free; optimize first for completed work, user control, and trustworthy evidence, then for lower model cost.
- **Repository baseline:** the approved [Alpha Trust Programme design](../superpowers/specs/2026-07-12-alpha-trust-programme-design.md) and the implementation state in this worktree.
- **Source policy:** primary research and official project, protocol, vendor, and platform documentation only. Claims below are paraphrased. URLs were verified on the research date.

## 1. Scope and decision context

Vesta's public-alpha promise is not "the cheapest answer." It is a local-first control plane that finds the cheapest **safe path to a useful outcome**, keeps the user in control of consequential actions, and produces evidence that can be reconciled locally. A cheap stale answer, an unbounded tool call, or a false completion is a product failure even when it saves tokens.

The immediate Alpha Trust Programme therefore remains the first delivery unit:

- derive exact capabilities from the latest request;
- fail closed when a provider cannot enforce those capabilities;
- never report an unsupported edit as completed;
- separate route comparisons from spend and count each real model call once; and
- preserve cancellation, dirty-worktree protection, redacted activity, and verifiable receipts.

The named follow-ups are distinct from that first patch:

- [#287](https://github.com/MarcoLadeira/OPai/issues/287): content-aware result-cache keys and bounded expiry (**P1, before public release**);
- [#288](https://github.com/MarcoLadeira/OPai/issues/288): task-outcome records and cost per completed task (**P1, before public release**);
- [#289](https://github.com/MarcoLadeira/OPai/issues/289): bounded, incremental repository retrieval with quality gates (**post-alpha default rollout; shadow evaluation may start earlier**); and
- [#290](https://github.com/MarcoLadeira/OPai/issues/290): real Windows and macOS desktop artifacts and clean-runner smoke (**P0 public-alpha blocker**).

"Prompt cache," "result cache," and "repository index" must remain separate concepts. In particular, vendor prompt caches differ in eligibility, breakpoints, retention, pricing, and usage fields. Vesta should expose a provider-neutral observation envelope, but it must preserve each provider's native cache semantics rather than invent a universal hit model.

## 2. Evidence table

| Area | Primary or official source | Verified principle | Implication for Vesta | Adoption timing |
|---|---|---|---|---|
| Agent architecture | [OpenAI, *A practical guide to building agents*](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/) | An agent is a model-directed loop over tools and instructions with explicit exit conditions. Start with one agent and add orchestration only when complexity or tool confusion justifies it. | Extend Vesta's existing run loop and typed state machine; do not replace it with a multi-agent framework for alpha. Bound turns, retries, and terminal states. | Alpha — immediate trust programme |
| Long-running harness | [Anthropic, *Effective harnesses for long-running agents*](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | Long tasks recover better when each session reads durable progress, inspects Git state, works incrementally, and verifies behavior before declaring completion. | Use Vesta checkpoints, runtime history, Git status/log, and small verifiable work units as the handoff contract. A progress artifact is state, not proof; tests and receipts decide completion. | Alpha foundation; P1 outcome join in #288 |
| Context windows | [Claude Platform, context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows) | More context is not automatically better; tool definitions, results, history, and output all consume the window, and cached prefixes still occupy it. | Budget and curate context instead of filling the advertised window. Record selected and available context, then couple any reduction to task quality. | P1 instrumentation in #288; retrieval rollout in #289 |
| Stable-prefix caching | [OpenAI API, prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) | Cache reuse depends on an exact shared prefix. Stable instructions, examples, images, and tool definitions belong before variable task data; provider usage reports cache reads and, where applicable, writes. | Build stable provider prefixes and put task/repository deltas last. Store the provider's native read/write token fields on the model-call event. | P1 — provider adapter instrumentation |
| Provider-specific caching | [Claude Platform, prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) | Claude exposes automatic and explicit breakpoints, ordering rules, retention choices, and distinct read/write accounting. Changing content before a breakpoint changes the cached prefix. | Keep Claude cache controls and accounting in its adapter. Do not translate them into OpenAI cache keys or assume a shared lifetime or price rule. | P1 — provider adapter instrumentation |
| Result-cache correctness | [Vesta issue #287](https://github.com/MarcoLadeira/OPai/issues/287) | A reusable answer is safe only when all relevant repository inputs and the cache schema/lifetime still match; uncertain or oversized inputs require an explicit bypass. | Hash bounded dirty and untracked content, add expiry/schema metadata, and record hit, miss, expiry, and bypass reasons without raw prompts or source. | P1 — before public release |
| Incremental parsing | [Tree-sitter, advanced parsing](https://tree-sitter.github.io/tree-sitter/using-parsers/3-advanced-parsing.html) | An edited syntax tree can be updated with the edit and passed back to the parser so unchanged structure is reused. Included ranges support mixed-language documents. | Treat Tree-sitter as an optional incremental symbol/import extractor behind the repository-index interface, not as an alpha-wide dependency. Invalidate changed files and dependent records, not the whole index. | Post-alpha — #289 |
| Bounded repository retrieval | [Vesta issue #289](https://github.com/MarcoLadeira/OPai/issues/289) | Retrieval is valuable only when incremental reuse, selection reasons, safe fallback, and non-inferior task quality are demonstrated together. | Reuse the existing local index in shadow evaluations first. Exclude secrets, ignored files, binaries, and unsupported sizes; fall back to the current context path on low confidence or corrupt state. | Post-alpha default; evaluate after #287 and #288 |
| Cost-aware routing | [Chen, Zaharia, and Zou, *FrugalGPT*](https://arxiv.org/abs/2305.05176) | Prompt adaptation, approximation, and model cascades can lower inference cost, but cascade results are workload- and evaluation-dependent. | Route deterministically when possible, then use the cheapest tier that meets an Vesta task-quality baseline. Escalate on typed insufficiency or failed evidence, not merely because a task sounds difficult. Reproduce gains on Vesta tasks rather than importing headline savings. | Alpha policy; P1 calibration in #288 |
| Model selection | [OpenAI agent guide, model selection](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/) | Establish a capable-model quality baseline before substituting smaller models, and keep substitutions only when eval targets still hold. | Trust and completion outrank raw savings. Maintain per-task baselines and recovery rules; a free/local route may escalate only through the user's configured and approved boundary. | Alpha — routing guardrail; P1 eval expansion |
| Agent memory | [Claude Platform, memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) | Durable memory can support just-in-time retrieval across sessions while storage and file operations remain application-controlled. Memory needs path confinement, size bounds, expiry, and sensitive-data validation. | Keep memory project-local and inspectable. Store concise provenance-bearing facts, not entire prompts. Add view, correct, delete, expiry, and disable controls before production prompt injection. | Post-alpha |
| Memory user control | [OpenAI Agents SDK, sessions](https://openai.github.io/openai-agents-python/sessions/) | Session history can be bounded, inspected, corrected, and cleared; storage strategy is an application choice. | A user must be able to see what Vesta will recall, remove entries, clear a project/session, and cap retrieved history. Do not silently merge legacy memory with server-managed provider state. | Post-alpha |
| Human-in-the-loop | [OpenAI agent guide, tool safeguards and human intervention](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/) | Tool risk depends on write access, reversibility, permissions, and impact. High-risk actions and exhausted retry thresholds should hand control back to the user. | Preserve exact Git-aware capabilities. Read/search can run within policy; edits remain bounded; push, PR, merge, credential, destructive, and other irreversible actions require explicit current authority. | Alpha — immediate trust programme |
| Tool execution | [MCP specification, tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) | Tools have schemas and structured results; protocol errors and execution errors are distinct. Clients should validate results, use timeouts, log use, show inputs, and ask before sensitive operations. | Standardize Vesta tool calls around request ID, validated arguments, timeout, typed outcome, bounded/redacted result, and an audit event. Never infer success from prose. | Alpha — immediate trust programme |
| Cancellation | [MCP specification, cancellation](https://modelcontextprotocol.io/specification/2025-06-18/basic/utilities/cancellation) | Cancellation is best-effort and racy: receivers should stop work and free resources, senders should ignore late results, and both sides should expose and log cancellation state. | Propagate one cancellation token through model, MCP, subprocess, and background-run layers; terminate the process tree; ignore late output; write one terminal `cancelled` outcome. | Alpha — immediate trust programme |
| Receipts and observability | [OpenAI Agents SDK, tracing](https://openai.github.io/openai-agents-python/tracing/) | Run traces connect generations, tool calls, handoffs, guardrails, and custom events, but inputs and outputs can contain sensitive data. | Keep Vesta observability local and redacted by default. Join run, route, model-call, tool, cancellation, Git-diff, and terminal-outcome IDs; receipts summarize this evidence and do not substitute for it. | Alpha redaction/receipt; P1 join in #288 |
| Prompt injection | [OWASP GenAI, LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) | Direct and indirect instructions can arrive through users, files, websites, images, or retrieved content. Retrieval and fine-tuning do not remove the risk; least privilege, deterministic validation, segregation, and approval limit impact. | Treat repository text, web pages, tool output, and memory as untrusted data. Never let retrieved text expand capabilities. Validate structured outputs and keep privileged operations outside model discretion. | Alpha — immediate trust programme |
| MCP security | [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices) | MCP implementations must address confused-deputy, token-passthrough, session, local-server, proxy, and over-broad-scope risks with audience validation, sandboxing, and progressive least privilege. | Keep server allowlists, path scopes, remote opt-in, per-tool confirmation, isolated processes, redacted logs, and validated token audiences. A prompt contract is not an enforcement boundary. | Alpha — immediate trust programme |
| Coding-agent evaluation limits | [OpenAI, *Why SWE-bench Verified no longer measures frontier coding capabilities*](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/) | A benchmark can stop representing real capability because of contamination, underspecified tasks, environment sensitivity, and tests that reject correct solutions. | Keep VestaBench as a deterministic regression suite, not a launch-quality proxy. Add fresh project tasks, multi-platform environments, user/test-grounded outcomes, and recovery metrics. | P1 — #288 |
| Desktop packaging | [Qt for Python, `pyside6-deploy`](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html) | Qt's official deployment wrapper uses Nuitka and a generated, reviewable `pysidedeploy.spec` to produce platform-native executables. | Adopt one checked-in deployment spec and pin the packaging toolchain. Build on each target OS, inventory bundled web/Qt assets, and smoke the installed GUI rather than only a wheel helper. | Alpha blocker — #290 |
| CI matrices | [GitHub Docs, building and testing Python](https://docs.github.com/en/actions/tutorials/build-and-test-code/python) | `setup-python` makes versions explicit, and a job matrix exercises multiple Python/OS combinations consistently. | Keep unit/security gates broad but add target-native Windows/macOS artifact jobs. Each artifact must install, launch, exercise first-run/cancel/exit, upgrade, uninstall, and publish checksums/provenance. | Alpha blocker — #290 |
| Outcome measurement | [Vesta issue #288](https://github.com/MarcoLadeira/OPai/issues/288) | Cost evidence needs one privacy-preserving logical task ID and at most one terminal outcome joining calls, tokens, cache decisions, latency, recovery, receipt, and completion evidence. Unknown values must remain unknown. | Add a versioned task-outcome event and reconcile GUI/CLI views to the append-only ledger. Cost per completed task becomes meaningful only after this join exists. | P1 — before public release |

## 3. Architectural conclusions for the current repository

### Keep the existing control plane

The repository already has the right seams for a staged implementation:

- [`agent_policy.py`](../../vestahub/agent_policy.py), [`provider_tools.py`](../../vestahub/provider_tools.py), [`sandbox.py`](../../vestahub/sandbox.py), and [`mcp_runtime.py`](../../vestahub/mcp_runtime.py) separate intent, capability, command, repository, and MCP enforcement. The immediate trust patch should tighten these seams rather than add another policy layer.
- [`agent_runtime.py`](../../vestahub/agent_runtime.py), [`checkpoints.py`](../../vestahub/checkpoints.py), [`background_runs.py`](../../vestahub/background_runs.py), [`process_tree.py`](../../vestahub/process_tree.py), and [`cancellation.py`](../../vestahub/cancellation.py) already represent state, resume, interruption, and process termination. Add one logical request ID and one terminal outcome across them in #288.
- [`ledger.py`](../../vestahub/ledger.py), [`cost_telemetry.py`](../../vestahub/cost_telemetry.py), [`audit.py`](../../vestahub/audit.py), and [`receipt.py`](../../vestahub/receipt.py) already separate cost, governance, and proof artifacts. The route is a decision; the model call is spend; the terminal task outcome is the denominator. Preserve those distinctions.
- [`context_pack.py`](../../vestahub/context_pack.py), [`evidence_cache.py`](../../vestahub/evidence_cache.py), [`result_cache.py`](../../vestahub/result_cache.py), and [`semantic_index.py`](../../vestahub/semantic_index.py) should become three explicit layers: bounded context composition, correctness-sensitive reusable results, and retrieval/index state. They must not share an ambiguous `cache_hit` field.
- [`eval_harness.py`](../../vestahub/eval_harness.py) and [`vestabench.py`](../../vestahub/vestabench.py) are useful deterministic regressions. #288 should add outcome fixtures and sampled real tasks rather than replacing these tests.
- [`pyproject.toml`](../../pyproject.toml) already separates the `desktop-gui` extra, while [hosted CI](../../.github/workflows/ci.yml) has a manual three-OS wheel smoke. #290 should extend that path to the real PySide6 application and native artifacts instead of creating a second packaging system.

### Use a single resumable run envelope

Each user request should create one run envelope with:

1. immutable request ID, repository identity/fingerprint, requested mode, and exact capability policy;
2. ordered route, model-call, tool-call, approval, Git, cache, and recovery events;
3. cancellation state propagated to every child operation;
4. bounded progress/checkpoint state that a new session can inspect; and
5. at most one terminal outcome: completed, failed, blocked, cancelled, or interrupted.

This fits Vesta's current state machine and append-only files. It also prevents a retry, resumed process, or late tool result from being mistaken for another completed task.

### Separate the three cache contracts

1. **Provider prompt cache:** a billing/latency observation owned by each provider adapter. Keep stable reusable material first and variable task/repository material last. Record native cache-read/cache-write token fields and configuration; never reuse one provider's cache rules for another.
2. **Vesta result/evidence cache:** a correctness contract. #287 must version keys, hash relevant dirty/untracked bytes within explicit bounds, enforce expiry, and bypass on incomplete fingerprints.
3. **Repository index:** a retrieval accelerator. #289 should update changed files only, retain per-chunk provenance/content hashes, explain selection, and fall back safely. Tree-sitter can later enrich symbols/imports where its packaging and language coverage are justified.

The dependency order is **#287 correctness, then #288 outcomes, then #289 quality-gated retrieval**. Otherwise Vesta cannot tell whether a lower-token run was correct or merely omitted decisive context.

### Route on evidence, not price alone

Use the current taxonomy and scorecards to choose an initial tier, but make escalation a typed policy:

- deterministic/local work first when it can actually satisfy the request;
- a cheaper model only when its task-class baseline remains acceptable;
- recovery or escalation after bounded failure, invalid tool output, missing completion evidence, or low retrieval confidence; and
- no paid/cloud boundary crossing or high-risk action without the user's explicit current authority.

The free product launch does not mean every underlying inference has zero cost. Vesta must label provider-measured cost, estimated cost, zero-cost entitlements, and unknown cost separately.

### Make memory explicit and reversible

The legacy [`opcoding/memory.py`](../../opcoding/memory.py) provides project-local SQLite `add` and `list` operations, but it is not yet a production agent-memory contract. Do not auto-inject it. A future shared memory service should add:

- project/session namespaces and provenance;
- opt-in read and write controls;
- view, correction, deletion, clear, expiry, and size limits;
- sensitive-data and path validation; and
- retrieval reasons plus the context cost of every recalled item.

Until those controls exist, durable run checkpoints are safer than generalized learned memory.

### Package where the application will run

The current wheel smoke proves Python packaging, not the desktop product. #290 should pin `pyside6-deploy`/Nuitka through one reviewed spec, build independently on Windows and macOS, and run installed-artifact tests. Signing/notarization status must be explicit. If required credentials are unavailable, the artifact is an internal test build and must not be described as public-release-ready.

## 4. Ideas rejected or deferred

| Idea | Decision and reason |
|---|---|
| Multi-agent-first rewrite | **Deferred.** The existing single-run control plane is easier to permission, cancel, evaluate, and reconcile. Add specialist workers only for a measured failure mode that a clearer tool or prompt cannot solve. |
| Maximize the context window | **Rejected.** Long windows still incur attention, token, and quality costs; prompt caching changes price/latency, not context occupancy. Select the smallest evidence set that preserves outcome quality. |
| One universal prompt-cache abstraction | **Rejected.** Providers expose different eligibility, ordering, retention, pricing, and counters. Normalize event names only at the observation layer and retain native fields. |
| Reuse results based on Git HEAD and dirty path names | **Rejected.** Different bytes at the same dirty path can represent different tasks. #287 must land before result-cache evidence is trusted for public release. |
| Enable semantic retrieval by default immediately | **Deferred to #289.** The current deterministic index is promising but rebuilds selected content and is not yet the production prompt source. Shadow evaluation and conservative fallback come first. |
| Automatically write and inject agent memory | **Deferred.** Memory without inspect/correct/delete/expire controls can preserve stale, sensitive, or injected instructions. Checkpoints remain scoped recovery state. |
| Treat prompts as the tool security boundary | **Rejected.** Capability checks, path confinement, command policy, approval, timeouts, and output validation must be enforced in deterministic code. |
| Use a single benchmark score as the launch gate | **Rejected.** Benchmark contamination, test defects, environment drift, and task ambiguity can distort both failures and successes. Use several regression sets plus real outcome evidence. |
| Hosted raw-prompt tracing for alpha | **Deferred.** Local redacted event joins meet the trust goal with less privacy exposure. Hosted export can be separately opt-in after a data and threat review. |
| Paid feature or entitlement gates | **Rejected for alpha.** The launch is fully free. Safety, honest accounting, cache correctness, and release proof are not future premium features. |

## 5. Research-backed measurement plan

### Metric definitions and instrumentation status

| Metric | Definition | What is measurable in the repository now | Instrumentation needed |
|---|---|---|---|
| Model calls per completed task | Count authoritative `model_call` events joined to a logical task ID, divided by terminal outcomes classified as completed. Also report the distribution and calls for failed/cancelled tasks. | Route and model-call events exist; the immediate trust patch makes spend exact-once. | #288 must add the task ID/terminal-outcome join. Do not divide by messages, routes, or receipts. |
| Tokens per completed task | Sum provider-reported input, output, reasoning, cache-read, and cache-write tokens by task. Keep estimated tokens in a separate field and mark missing provider usage unknown. | Token estimates and normalized provider usage exist in cost modules. | #288 plus provider-adapter coverage; provider cache categories remain native. |
| EUR per completed task | Sum call-level `cost_eur` for completed tasks, while reporting failed/recovery spend separately. Each estimate records price version, source currency, conversion rate, and conversion timestamp. | The current ledger primarily reports estimated USD and cannot yet prove EUR per outcome. | Add call-level EUR provenance and the #288 outcome join. Record zero only when a measured entitlement/provider contract supports zero; otherwise use unknown. |
| Result-cache correctness | Count validated hit, miss, expired, corrupt, and safety-bypass outcomes. The public-alpha stale-hit target is zero; latency is secondary to complete invalidation. | Evidence caching has version/age metadata; result caching does not yet hash dirty bytes or enforce bounded expiry. | #287: content-aware fingerprints, schema/expiry, bounds, bypass reasons, concurrency tests, and avoided-call linkage. |
| Provider prompt-cache efficiency | For each provider/configuration: cache-read tokens, cache-write tokens, uncached input tokens, hit-eligible calls, and net measured cost/latency effect. | No trustworthy cross-provider aggregate should be inferred from generic input tokens. | Adapter-specific usage capture and tests; never aggregate unlike semantics into one undifferentiated hit rate. |
| Context-use ratio | `selected_context_tokens / eligible_context_tokens`, alongside selected files/chunks, reasons, truncation, and outcome quality. A lower ratio is not success by itself. | Context packs estimate selected tokens; the local index can return bounded, content-hash-checked chunks. | #288 joins the ratio to outcomes; #289 defines eligible context, incremental invalidation, reasons, and fallback. |
| Quality and recovery | Completed with user acceptance or task-specific test evidence; first-pass completion; retry count; recovery-after-failure rate; regression failures; blocked/cancelled outcomes; time to first useful result. | Runtime states, repair bounds, deterministic evals, and tests provide regression evidence. | #288 adds one terminal outcome and evidence class per request. Build baselines before setting percentage targets. |
| Cancellation reliability | Cancel acknowledgement latency, process-tree termination, late-result rejection, orphan process count, and exactly one terminal cancellation event. | Cancellation, background-run, process-tree, MCP, and provider-tool paths are directly testable now. | Extend the existing cross-platform tests to the packaged GUI in #290 and join terminal events in #288. |
| Receipt reconciliation | Every receipt references the run/outcome and reconciles calls, cost, cache decisions, changed files, tests, approvals, and final status without raw prompt/source content. | Hash/signature verification and aggregate savings receipts exist. | #288 adds per-task reconciliation; the immediate trust patch supplies authoritative spend semantics. |
| Install and crash metrics | Artifact build success; clean install/launch; first-run missing-provider flow; open fixture repository; cancel/exit without orphans; upgrade/uninstall; startup crash; asset inventory; checksum/provenance; signing status, all by OS/Python/artifact version. | A manual three-OS wheel smoke and extensive source-tree tests exist. | #290 must exercise the real installed Windows/macOS GUI artifacts and retain logs/artifacts even on failure. |
| Privacy and control | Count redaction failures, raw prompt/source/credential fields in telemetry, memory operations by user choice, and denied capability expansions. | Redaction, hash-chained audit, scoped MCP, and receipt primitives exist. | Add schema-level allowlists and privacy fixtures to #288; memory remains disabled from automatic prompt injection until reversible controls exist. |

### Practical alpha criteria

The public alpha is ready only when all of these statements are backed by automated or locally reproducible evidence:

1. **Immediate trust programme:** ordinary implementation cannot authorize push/PR/merge; providers that cannot enforce bounded edits fail closed; unsupported edits cannot become success; each real model call produces one spend event; cancelled/blocked/failed work cannot produce a completion receipt.
2. **Cache correctness (#287, P1):** changed relevant bytes invalidate reusable results, entries expire, incomplete fingerprints bypass safely, and cache metrics never claim avoided tokens when usage is unknown.
3. **Outcome truth (#288, P1):** every run has one logical task ID and at most one terminal outcome; GUI and CLI reconcile calls, tokens, EUR, cache decisions, quality evidence, recovery, and receipts to the same local ledger.
4. **Desktop proof (#290, P0):** Windows and macOS artifacts build from a tagged commit and pass clean install, real GUI launch, first-run, fixture-repository, cancellation/exit, upgrade, uninstall, asset, secret, checksum, and provenance checks. Signing/notarization is complete or the build remains clearly non-public.
5. **Quality before savings:** representative fresh tasks show no known quality regression from routing or context reduction. Unknown measurements remain labelled unknown; no savings claim relies only on a route estimate or benchmark headline.
6. **Security boundary:** repository/web/tool/memory content is treated as untrusted; MCP servers and tools are allowlisted and scoped; sensitive inputs are visible before invocation; high-risk actions require current approval; logs and receipts remain redacted.

#289 is not a public-alpha blocker. It may run in shadow mode after #287 and #288, but bounded semantic context should become the default only after it demonstrates non-inferior completion with lower input context and safe fallback. Generalized agent memory and multi-agent orchestration remain post-alpha.

## 6. Sources grouped by topic

All links were opened successfully on 2026-07-12.

### Agent architecture and long-running work

- [OpenAI, *A practical guide to building agents*](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/) — use for the loop, model-baseline, tool-risk, guardrail, and human-handoff decisions.
- [Anthropic, *Effective harnesses for long-running agents*](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) — use for resumable progress, Git recovery, incremental work, and end-to-end verification.

### Context, caching, retrieval, and memory

- [OpenAI API prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) — implement stable prefixes and capture OpenAI-native cache usage.
- [Claude Platform prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — implement Claude-native breakpoints, ordering, retention, and accounting without cross-provider assumptions.
- [Claude Platform context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows) — budget total working context and remember that cached tokens still consume the window.
- [Tree-sitter advanced parsing](https://tree-sitter.github.io/tree-sitter/using-parsers/3-advanced-parsing.html) — evaluate incremental syntax/symbol updates behind #289's index interface.
- [Claude Platform memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) — design local, just-in-time memory with application-owned storage and security bounds.
- [OpenAI Agents SDK sessions](https://openai.github.io/openai-agents-python/sessions/) — require bounded retrieval plus inspect, correct, and clear operations.
- [Vesta #287](https://github.com/MarcoLadeira/OPai/issues/287) — implement correctness and expiry before trusting saved answers.
- [Vesta #289](https://github.com/MarcoLadeira/OPai/issues/289) — stage bounded repository retrieval only after correctness and outcome measurement.

### Routing, cost, and outcomes

- [Chen, Zaharia, and Zou, *FrugalGPT*](https://arxiv.org/abs/2305.05176) — test cost-aware cascades on Vesta workloads; do not import the paper's experimental savings as a product claim.
- [Vesta #288](https://github.com/MarcoLadeira/OPai/issues/288) — join cost, calls, tokens, cache, latency, recovery, and completion under one privacy-preserving task outcome.

### Tool reliability, permissions, and security

- [MCP tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) — use schemas, typed errors, validation, timeouts, visible inputs, confirmations, and audit logs.
- [MCP cancellation specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/utilities/cancellation) — propagate cancellation, free resources, ignore late results, and handle races.
- [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices) — prevent token passthrough, confused-deputy, session, local-server, proxy, and scope-expansion failures.
- [OWASP GenAI LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) — treat direct and indirect content as untrusted and limit impact through deterministic least privilege and approval.
- [OpenAI Agents SDK tracing](https://openai.github.io/openai-agents-python/tracing/) — join run events while keeping sensitive generation/tool content out of default telemetry.

### Evaluation and release proof

- [OpenAI, *Why SWE-bench Verified no longer measures frontier coding capabilities*](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/) — avoid a single contaminated or test-fragile benchmark as the launch-quality proxy.
- [Qt for Python `pyside6-deploy`](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html) — create a pinned, reviewable native desktop build path.
- [GitHub Docs, building and testing Python](https://docs.github.com/en/actions/tutorials/build-and-test-code/python) — make Python and OS coverage explicit with `setup-python` and matrices.
- [Vesta #290](https://github.com/MarcoLadeira/OPai/issues/290) — turn the packaging guidance into clean Windows/macOS artifact evidence for the fully free alpha.

### Vesta decision record

- [Alpha Trust Programme design](../superpowers/specs/2026-07-12-alpha-trust-programme-design.md) — defines the fully free launch, immediate trust failures, selected architecture, and backlog ordering.
- [Alpha Trust Programme implementation plan](../superpowers/plans/2026-07-12-alpha-trust-programme.md) — defines the exact-capability, fail-closed outcome, exact-once spend, and verification work in the immediate programme.
