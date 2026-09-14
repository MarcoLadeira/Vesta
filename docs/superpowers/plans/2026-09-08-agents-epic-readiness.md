# Epic #821 implementation assessment

Scope: source review and local qualification against the complete issue, updated 2026-09-13. Extended provider/platform qualification remains deferred; the requested UI refinements receive focused checks. No provider calls were made.

Merge readiness: pending the provider-backed comparisons, platform qualification and required hosted checks listed below. The PR remains draft; implementation coverage alone does not close the epic.

## Local qualification

The following runs overlap; their counts must not be added as unique tests.

| Run | Result |
| --- | --- |
| Initial focused objective/store/execution/CLI/bootstrap suites | 97 passed; one outdated fixture lacked the required ownership fence. Fixture corrected and its integration suite rerun successfully. |
| Broader receipt, budget, journal, GUI-mode, ownership and integration regressions | 193 passed, 1 skipped. |
| Desktop/CLI/bootstrap and additional qualification | 105 passed; the real-worker case exposed the shutdown defect described below. It passed in the final runtime rerun after the fix. |
| Final objective execution, integration, qualification, capacity and orphan-process suites | 36 passed, 1 POSIX-only test skipped on Windows, 5 subtests passed. |
| Packaging, asset integrity, update packaging and release identity surfaces | 30 passed. These are contract tests, not a native executable rehearsal. |
| Agents/composer Chromium suite | 27 passed. |
| Expanded Agents Chromium suite | 8 passed, including two new live-revision/artifact-target regressions; 29 distinct browser tests across both runs. |
| Full JavaScript unit suite | 122 passed, 3 existing chat-components failures. The failing test and implementation files are unchanged from PR base `3d5c90a`. |
| Scoped Ruff lint/format, Bandit, design tokens and lifecycle generation | Passed; two design-token tests passed. |
| Six synthetic workloads, sequential and concurrent | All 12 integrated successfully; zero conflicts and no intervention, with complete synthetic zero-cost evidence. |

The real subprocess test found that the parent watcher blocked on Python's buffered stdin, causing an interpreter-shutdown abort after a successful response. The watcher now uses the pipe's raw file descriptor. The regression exercises canonical launch authorization, a real leased Git worktree, the worker entry point, response evidence and worktree lookup with an explicitly stubbed provider.

Additional regressions cover exact receipt reconciliation and tamper detection, interrupted-owner acknowledgement, sequential eligibility, invalid launch authority, bounded dependency reports, incremental ledger records and cross-objective scheduling.

Synthetic benchmark results are in `.opaihub/qualification/agents-benchmark-current.json` locally. These runs used deterministic workers and overlapped other local checks; timing is diagnostic, not a controlled provider-performance comparison. Hosted PR checks were not reported when inspected. The full Python repository suite and native packaged executable have not been qualified in this pass.

## Acceptance coverage

| Epic criterion | Implementation assessment |
| --- | --- |
| High-level objective creates bounded assignments | Implemented: deterministic plan validation and canonical objective/assignment records. Invalid planner contracts now finalize as needing attention. |
| Concurrent isolation; overlapping/unknown scopes serialize | Implemented: worktree leases, transactional admission, project/host caps and explicit sequential eligibility. Cross-objective capacity waits now keep scheduling. |
| Canonical owner, route, model, budget, activity, changes, verification | Implemented routing admission and observed quota projection; real-provider and platform qualification remains. |
| Separate bounded parent/child context | Implemented: isolated context plus bounded dependency findings, treated as untrusted reports. |
| Independent failures preserve sibling work | Implemented with isolated attempts and supervisor-loss regressions; unconfirmed termination remains explicitly unresolved. |
| Exact per-agent and objective costs | Implemented: canonical event accounting, unknown-cost coverage, receipts and incremental ledger reads. |
| Stop one/all without orphan processes | Implemented guardian custody and proven termination for worker and integration subprocesses; native/POSIX qualification remains. |
| File/content conflicts detected before integration | Implemented: observed Git evidence, sibling/user-change checks and isolated integration. Semantic conflicts rely on integrated verification/review. |
| Completion requires integrated verification | Implemented: policy from original repository, persisted manifest and matching integration commit. |
| GUI restart reconstructs canonical state | Implemented canonical reconstruction and proof-based expired-owner recovery without replay. |
| Later CLI inspection/control uses same runtime | Implemented, including objective creation, execution, controls and receipt export. |

“Implemented” records code presence and design coverage, not acceptance-test success.

## September 10 implementation and qualification

- Independent guardians now retain host capacity and process-tree custody through supervisor loss. Workers wait for recorded custody before dispatch. Windows job emptiness is queried before termination proof; unknown termination retains ownership. Integration checks use the same guardian. Restart/listing consumes proof without replaying a model operation and retains the worktree for inspection.
- Canonical blocked-command approval creates a fresh run and worktree, retains prior attempts and exact costs, and rejects stale approval IDs. Conflicting retained changes stop continuation before dispatch. Edit approval explicitly grants one continuation in the isolated worktree; listed files describe the request, rather than an enforced per-file provider permission.
- Additional review uses a revision fence, preserves prior integration evidence, and still requires integrated verification. Desktop and CLI share these controls.
- Diff/PR inspection uses canonical Git/lease identity and bounded inert output. External Git helpers are disabled. Cloud consent is explicit per objective; route selection filters capabilities, availability and observed quota. Concrete local dispatch pins its approved endpoint without automatic rediscovery.
- Updated from main and retained its independent bypass switch. Objectives capture bypass authority at submission; read-only roles never inherit it.

Validation runs overlap and must not be added as unique tests:

| Run | Result |
| --- | --- |
| Objective/store/CLI/routing controls | 68 passed. |
| Receipts, assignment scope, budget, GUI mode and packaging contracts | 93 passed, 14 subtests passed. |
| Guardian subprocess and internal packaged-entry contracts | 11 passed, including killing the fixture supervisor and checking surviving descendants and capacity. |
| Integration supervisor-loss regression | Passed after fixing recovery's objective status and preserving the integration worktree. |
| Runtime, integration, routing, bootstrap and qualification | 104 passed, one old provider-stub fixture failed after routing was added. The fixture was isolated correctly and passed with six continuation regressions in a subsequent 7-test run. |
| Full JavaScript unit suite after main integration | 127 passed. |
| Agents browser before main integration | 12 passed. |
| Merged-main backend controls, routing, artifacts and CLI | 112 passed, 13 subtests passed. |
| Merged-main Agents, composer, bypass and topbar browser suite | 46 passed after restoring the Agents entry removed by the new sidebar and updating checkbox selectors. |

A long-running combined test was interrupted for diagnosis. Its isolated integration cases passed; the subsequent combined run completed. No claim of a production hang fix is based solely on that timing observation.

GitHub Actions attempted validation of `01d78ff`, but every required job failed before starting any steps. GitHub reported failed account payments or an insufficient spending limit; hosted CI is therefore blocked by account billing, not a test result. Required hosted checks must run after that account issue is resolved.

## September 11 merge qualification

- Pre-dispatch routing/budget denials now expose model/budget adjustment and a run-fenced retry. Only known-zero, unchanged attempts with sufficient termination evidence qualify. A retry preserves history and creates a fresh run; pipeline output cannot promote itself to pre-dispatch authority. Desktop and CLI share the control. The UI can explicitly remove a dollar cap.
- Integration now distinguishes Git checkout line-ending normalization from actual conflicting edits. A CRLF checkout is accepted; an independent user edit still blocks integration.
- Provider qualification now has six behavioral fixture classes, paired identical assignments at concurrency one and two, counterbalanced order, retained worktrees and canonical verification/cost/conflict evidence. Static, whitespace and independent behavioral checks run on the integrated result. Baselines must fail and reference implementations must pass. The harness discovered the CRLF integration defect during local validation.
- Provider benchmarks remain unexecuted pending explicit account/cloud authorization. The prepared Claude Sonnet plan permits 12 objective runs, up to 30 assignment attempts, two concurrent agents and a five-minute timeout per objective. Account-quota mode has no enforceable dollar cap, preserves unknown costs and cannot support dollar-savings claims. Capped runs stop if cost coverage becomes unknown. Use a short retained workspace path on Windows; the report can live separately.
- Recovery/backend/routing/bridge/CLI plus initial benchmark validation: 77 passed. Agents JavaScript: 9 passed. Agents Chromium: 13 passed after updating the budget selector to distinguish Set budget from Remove cap. Dedicated real Git CRLF/conflict regressions: 2 passed. These runs overlap prior coverage.
- Hosted checks for `6b3b367` again failed before any job steps: GitHub reports failed account payments or an insufficient spending limit. This needs an account-side resolution before required CI can provide evidence.

## September 12 implementation priority

The user explicitly deferred extended testing and provider benchmarks again. Finish implementation and retain qualification artifacts for a later pass; do not interpret this deferral as passing release evidence.

- Linux guardian custody now uses a dedicated child subreaper. It drains adopted descendants across setsid/double-fork boundaries and requires kernel child exhaustion before releasing capacity. Historical process-group proofs remain visible but cannot authorize recovery. Windows continues using retained job objects. Other POSIX platforms, including macOS, cannot start managed workers until equivalent containment is implemented.
- A real Alpine/Linux guest reproduced the old defect: termination was reported while a setsid descendant remained alive. The replacement implementation's final Linux qualification is deferred.
- Guardians and their gated children now start from the trusted runtime directory with a pinned Python import path. Repository modules cannot replace startup imports before custody is established. Actual assignment commands retain their intended worktree.
- Guardian setup failures before any child is spawned now return execution-bound, known-zero pre-dispatch failure evidence. This permits the existing explicit retry flow without trapping capacity for a process that never existed. Failure to confirm custody after dispatch still retains ownership.
- The native build now uses explicit output handles and closed stdin, exposes compiler options, converts the Windows icon through pinned Qt, and checks expected executable outputs. Nuitka's actual packaged executable is used for internal child launches instead of its nonexistent Python path.
- A native GUI/CLI bundle was produced with the pinned toolchain, but child-launch smoke exposed the Nuitka executable bug. Source fixes are present; the rebuilt native smoke, GUI smoke and latest platform regression pass remain deferred. The produced bundle is an untagged dirty-tree rehearsal, not a release artifact or exact-current-commit qualification.
- Provider benchmark harness validation completed with 16 local checks before the testing pause; provider calls remain unexecuted. The final implementation batch receives static checks only under the latest instruction.

## Remaining before epic closure

1. Run the required six workload classes with real providers in sequential and concurrent modes, measuring verified outcomes, wall time, cost, conflicts and intervention. Existing synthetic results establish orchestration behavior only. Provider/cloud use needs explicit authorization under the repository's session rules.
2. Rehearse a native packaged executable and validate POSIX process-tree behavior on that platform. Windows subprocess tests and frozen-entry contract tests do not substitute for these runs. A pinned isolated PySide6/Nuitka toolchain is now installed for the native rehearsal; qualification results must be recorded before closing this gate.
3. Resolve any findings from those runs and obtain release evidence before marking the epic complete. Simultaneous guardian loss without durable proof intentionally retains ownership; it never fabricates termination. Direct paid API and tool-less local adapters are visibly excluded where the underlying dispatch adapter cannot support the assignment.

## Improvements in this pass

- Validate planner contracts while planning errors can still release ownership safely.
- Continue scheduling objectives waiting behind another objective.
- Preserve rationale, cost estimates and explicit sequential eligibility in the planning contract.
- Carry bounded predecessor findings into dependent assignments without granting them authority.
- Project objective and per-agent receipts from journal evidence; support UI copying and CLI export with optional local signing.
- Show queue reasons, an enabled-state indicator, queued-work execution and canonical worktree-opening controls.
- Surface executor failures after the initial objective acknowledgement and bound international-text worker packets by encoded size.

## September 13 UI refinement

- Give artifact previews an opaque, centered dialog with a dimmed backdrop, a fixed header, scrollable diff, accessible name and restored focus on close.
- Wrap both multi-agent picker descriptions, including the complete paid/account quota consent, and bound the picker to the available viewport.
- Keep status, cost, live activity and immediate actions visible. Move budget/routing controls and technical evidence into named disclosures; preserve their state and keyboard focus across journal refreshes.
- Keep new objectives in chat with a compact live card linking to the full workspace. Add responsive assignment navigation, quiet state indicators and reduced-motion-aware transitions. Refreshes do not replay entrance animations.
- Verification: 17 focused Chromium checks passed, followed by 5 targeted checks after the final layout/animation refinements; 9 JavaScript unit checks and syntax/design-token/diff checks passed. Browser checks cover opaque centered dialogs, hostile diff text, narrow layout, complete consent, disclosure/focus retention, current revision fences and live chat updates. Extended backend, packaging and provider qualification remains deferred.

## September 13 control improvements

- Team limits in the Mode picker configure 1–4 concurrent agents and an exact decimal objective budget before submission. Retries retain the original limits; workspace changes reset session settings. Invalid budgets retain the prompt and never dispatch.
- Canonical approvals, failures and retryable attempts appear in an attention summary with direct navigation. Ordinary dependency waits do not imply user intervention.
- Journal refreshes preserve unsaved budget/model drafts, caret position, focus and evidence scrolling while continuing to update status and cost. A value changed elsewhere requires the user to edit their stale draft before applying it.
- Expanded picker height respects the header; a collapsed inspector no longer leaves invisible padding/border that can shift the page sideways.
- Validation: 11 unit checks; 21 browser checks passed in the full Agents pass, with the newly exposed inspector regression then fixed and its attention flow plus Team limits rechecked successfully. Earlier focused new-flow pass: 5 passed. These counts overlap.

## September 13 chat-first AI Team

- Follow the supplied product reference: chat is the primary workspace, with a lightweight AI Team rail. The composer has a direct Team action; accepted objectives show their team automatically. The initial roster contains names, avatars, current work and status, with details shown only after selecting an agent.
- Persist friendly names and avatar identities in the objective journal. Rename changes presentation metadata without changing assignment, task, run, route or permission identity. Portraits render locally as small SVGs, with no image service or extra provider calls.
- Show recorded agent updates in chat, and task rationale, recent activity, approval details, findings, files and verification on inspection. These are journal projections, not invented messages or a fabricated historical timeline.
- Show actual predecessor/reviewer relationships as named links. Additional team review uses the existing canonical action and revision fence. General observer/assistant relationship editing is not introduced by this presentation change.
- Keep the detailed Agents workspace reachable from the rail, with a Back to chat action. Preserve focus, disclosure state and rename drafts during live updates. Reopened teams refresh from the local journal independently of the full workspace, reject older revisions and show connection failures. Polling stops when neither team surface is in use.
- Use existing OPai colors and typography, local character avatars, subtle fades/activity indicators and reduced-motion support. On narrow screens the rail collapses over chat. The first enable action leaves the composer usable before submission.
- Expose platform support before submission. Unsupported hosts cannot create a desktop objective by spoofing browser state; existing objectives remain inspectable. Windows/Linux support here describes implemented containment, not successful host qualification.

- Focused validation: 22 workspace browser checks and 8 AI Team browser checks passed in separate runs; 14 JavaScript unit checks and 8 bridge checks passed. The combined browser run exposed a transient mobile entrance-animation offset; removing the offset was verified by the final 8-check team run. Scoped Ruff/format, JavaScript syntax, design-token and diff checks passed. These runs overlap earlier coverage and do not replace the deferred release gates.

## September 13 baseline refinement

- Preserve the layout and visual style. Replace the duplicated centre roster with a journal-backed work timeline: chronological activity, task starts, completion findings and recorded verification summaries. Keep the latest 80 work events bounded, disclose truncation, and retain earlier events in the journal. Streaming/status-channel updates stay out of the work feed; structured file/command details remain readable.
- Reduce the large team card to a summary line with the actual working count, recorded cost and View team. Remove Open in Agents. Agent identity is secondary to the event in the centre; the roster remains people/status navigation.
- Collapse the right panel into a 48px avatar strip on desktop. Narrow-screen panels leave the composer available. Selecting an agent reveals its task, activity and controls in the existing drawer, with Back to team and in-place diff inspection. The full workspace remains optional.
- Explain dependency waits using agent names. Say OPai will continue only when the predecessors are actually running and do not need approval. Show collaboration links while the selected agent is working, rather than a persistent graph.
- Team ON opens one menu for automatic sizing, optional concurrency caps, cloud consent and configuration. The default permission-mode button is hidden while Team is enabled; permissions remain accessible in the menu, with non-default modes and bypass still visible. Auto model distinguishes routing from the header's Auto mode. Budget/authority validation and exact one-objective cloud consent are unchanged.

- Refinement validation: 23 team/mode/workspace-switch browser checks passed, followed by 2 targeted drawer/diff and collapsed-strip focus checks. The preceding combined run passed all 22 workspace checks; its one new width assertion was measuring the collapse transition and now waits for the settled layout. 15 JavaScript unit checks and 9 bridge checks passed, including ordered, durable, bounded, objective-scoped timeline projection and status-channel exclusion. Scoped syntax, Ruff/format, design-token and diff checks passed. Counts overlap prior runs.

## Activation

Use a build containing PR #842. Press **Team** beside the composer’s Mode picker, then send an objective. Chat stays open and the **AI Team** panel shows the assignments. Select an agent to inspect its work or rename it. Once enabled, **Team ON** opens team options, including **Show team**, sizing, consent, configuration and **Turn Team off**. **View team** or the collapsed avatar strip opens the panel; **Collapse** minimizes it. The existing Mode-menu toggle remains available through permission settings. **Team ON** indicates the enabled preference, which persists. Permission mode and model selection remain separate. The header’s **Agents** button opens the detailed workspace.

For cloud/account models, explicitly enable **Allow cloud providers for this objective** before sending. This permission resets for the next objective. Bypass is captured independently; reviewer/planner roles remain read-only.

## September 14 optional team customisation

Keep chat, activity and the compact roster as the baseline. Add centred local-time separators from recorded message/event timestamps; missing historical dates stay unknown. An optional Organise team map exposes draggable agent cards, named groups, automatic arrangement and directed result handoffs. Clicking a card uses the existing drawer. Default planning still chooses the tasks and dependencies.

Stable agent identity spans isolated follow-up assignments. The drawer can send an agent a message, queued after its current task; replies and work remain in the same agent thread. Add agent accepts a task and optional name/model/group. Models apply to unstarted and future work, preserving active execution. Connections change pending task dependencies transactionally, reject cycles and cannot rewrite running work. Group/layout changes are presentation only. Team revisions fence duplicate/stale user changes independently from live activity updates. Existing objective limits, exact costs, permissions, cloud consent and process custody still govern all work.

Acceptance: local persistence, canonical cross-objective rejection, real follow-up dependency context, no duplicate actor rows, editable graph with keyboard alternatives, responsive layout, and draft preservation during refresh. Use focused backend and browser checks; no provider calls or repeated full-suite qualification.


Implemented: Add agent in the roster; optional task/name/model/group/budget form with Start immediately off by default. Held agents can be grouped, connected and arranged before Start agent. The map supports directional task handoffs, dependency-based arrangement, zoom, dragging entire groups, keyboard movement and coalesced layout saves. Group selection changes presentation membership only. A blocked receiving task can remove a failed dependency only if it never started; executed inputs remain fixed.

Agent conversation history comes from persisted user messages and worker handoff results across a stable actor identity, even after activity leaves the bounded live feed. Draft messages survive switching agents and refreshing. Model changes apply to unstarted/future work, and messages create serial isolated follow-ups instead of silently changing an active worker's instructions. Team mutations use a separate revision so frequent worker activity does not make user edits stale. Duplicate actor rows and counts are collapsed in the normal UI; the detailed workspace retains every task.

Validation: 8 new backend checks; 3 existing canonical-identity/cloud/bypass checks; 18 JavaScript checks; all 5 customisation browser flows passed. The earlier combined run passed 12/14 browser checks: one existing rename flow exceeded the short local test budget and passed its focused rerun; the model selector needed an explicit accessible label, which was added and verified. Saved-conversation regression checks passed (21 tests plus 4 subtests, run alongside the first 6 team backend checks). Screenshots were inspected at desktop and narrow widths. No provider call or expanded platform qualification was performed.

## September 14 global agent access

Keep the avatar strip on every page, including beside the expanded drawer and team map. Each avatar opens its own agent in the existing drawer without changing the current page. Show known teams from the local journal, preserve shortcut focus and scrolling during refreshes, and keep active teams updated outside chat. Empty teams retain a compact entry point. Narrow drawers leave the strip accessible; non-chat pages use the available height.

The optional map opens in viewing mode. Edit team reveals Add agent, Connect, Group and Auto arrange, plus pointer/keyboard movement. Done editing returns to inspection; opening the map again also starts in viewing mode. Connection inspection remains available in both modes, with mutation controls only in editing mode. Label recorded reviewer dependencies Review and other result dependencies Hand off; show each agent's latest recorded activity. Runtime authority and dependency semantics remain unchanged.

Validation: 6 of 7 focused browser flows passed initially. The narrow-screen flow exposed a navigation bug when opening the map from Settings; after fixing that route, its targeted rerun passed. Coverage includes global access on Settings, Prompts, dashboards and the detailed Agents page, same-page drawer inspection, map viewing/editing, group movement, focus retention and narrow-screen geometry. Desktop and mobile screenshots were inspected. All 18 scoped JavaScript checks and syntax, design-token and diff checks passed. No provider calls or expanded release qualification were performed.


## September 14 map density refinement

Apply the supplied density review while retaining the global avatar strip. Viewing mode automatically summarizes inactive groups in teams of eight or more; group headers expand/collapse, and editing exposes every agent. Compression is a presentation projection and never rewrites saved positions. Attention states remain visible by default. Cards prioritize task, secondary identity and a short icon/status; details stay in the existing drawer and tooltip. Selecting an agent emphasizes its upstream/downstream work, and large cross-group graphs reveal links on selection or through All links. Fit sizes the current visible map. Grouped avatar shortcuts have labels and tooltips. The map narrows history, removes nested outlines and uses a compact composer until focused. Editing has a distinct Done editing action and quieter tools. Runtime behavior is unchanged.

Validation: the populated 12-agent preview passed collapse/expand, edit-mode restoration and selected-agent focus checks. Existing group editing and drag/save flows passed; narrow-screen access passed after correcting the new count label overflow. The 18 existing scoped JavaScript checks and 2 new compression checks passed across the focused runs. Syntax/design-token/diff checks passed. No provider or broader release qualification was run.
