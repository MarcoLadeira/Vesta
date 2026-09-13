# Epic #821 implementation assessment

Scope: source review and local qualification against the complete issue, updated 2026-09-10. Testing resumed at the user's request. No provider calls were made.

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

## Activation

Use a build containing PR #842. The header's **Agents** button opens existing objectives. Open the composer's **Mode** picker (the button showing Auto, Manual or the current permission mode), enable **Allow multiple agents mode**, then send an objective. The mode button includes **Agents** while enabled. The toggle persists; permission mode and model selection remain separate. Existing objectives are managed from the Agents workspace.

For cloud/account models, explicitly enable **Allow cloud providers for this objective** before sending. This permission resets for the next objective. Bypass is captured independently; reviewer/planner roles remain read-only.
